"""Energy area: power by source, diesel, emissions, revenue, manpower, afforestation, targets.

Primary source is the "Sustainability Calculation" workbook; the "Only to Enter"
workbook is used only for a cell the calculation workbook leaves empty (and the
payload says so with `"source": "entry"`). Every number is read from a sheet; the only
numbers computed here are plain sums of other sheet values, and each such place says so.

Layout notes that drive the code (see docs/api/energy.md for the full list of oddities):
* Month headers are typed as "Apr-26" but Excel stored them as the *date* 26-Apr of the
  current year, so a header datetime means (month = dt.month, year = 2000 + dt.day).
  Only the first (April) header of a 12-month block is trusted for the year; the other
  eleven months are walked forward from it.
* Plant names are in merged cells (only the first row of a block has one), and each block
  ends with a "Total" row. Summary tables sit below the plants, so a block is closed at
  its total row and anything after it is ignored until the next plant name.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Query

from .. import workbooks
from ..plants import PLANT_IDS, PLANTS, canonical_plant, plant_name
from ..workbooks import to_number

router = APIRouter(prefix="/api/energy", tags=["energy"])

CLUSTERS = {
    "DP": ["TASL-NGP", "TASL-BLR", "TSAL", "TCOE"],
    "ASSY": ["TLMAL", "TASL-HYD", "TBAL", "TASL-MCA"],
}
_POWER_KINDS = ("grid", "solar", "dg", "purchased")


# --------------------------------------------------------------------------- periods

_MONTH_NUM = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_TEXT_MONTH = re.compile(r"^\s*([A-Za-z]{3,9})[-\s'’]*(\d{2}|\d{4})\s*$")
_FY_LABEL = re.compile(r"^\s*(?:total\s+)?fy\s*(\d{2,4})(?:\s*[-–]\s*(\d{2,4}))?", re.I)


def fy_name(start_year: int) -> str:
    return f"FY{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def fy_of_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return fy_name(year if mon >= 4 else year - 1)


def normalize_fy(text: str | None) -> str | None:
    """"FY25-26", "FY 25-26", "FY 25 - 26", "2025-26" -> "FY25-26"."""
    if not text:
        return None
    m = re.match(r"^\s*(?:fy)?\s*(\d{2,4})\s*[-–]\s*(\d{2,4})\s*$", str(text), re.I)
    if not m:
        return None
    start = int(m.group(1))
    start = start + 2000 if start < 100 else start
    return fy_name(start)


def _decode_header(value: Any) -> tuple[int, int] | None:
    """(year, month) of a month header cell, or None if it is not one."""
    if isinstance(value, dt.datetime):
        # "Apr-26" typed in Excel becomes 26-Apr-<this year>; day 1 means a real date.
        return (value.year, value.month) if value.day == 1 else (2000 + value.day, value.month)
    if isinstance(value, str):
        m = _TEXT_MONTH.match(value)
        if m and m.group(1)[:3].lower() in _MONTH_NUM:
            year = int(m.group(2))
            return (year + 2000 if year < 100 else year, _MONTH_NUM[m.group(1)[:3].lower()])
    return None


def month_columns(header: list[Any]) -> tuple[dict[int, str], dict[int, str], list[str]]:
    """Locate month and FY-total columns of a header row.

    Returns (month_cols {col: "YYYY-MM"}, fy_cols {col: "FY25-26"}, notes). The FY of a
    block comes from its April header; a conflicting FY label is reported, not trusted.
    """
    month_cols: dict[int, str] = {}
    fy_cols: dict[int, str] = {}
    notes: list[str] = []
    run: list[tuple[int, int, int]] = []  # (col, year, month)

    def close(label: tuple[int, int | None] | None, label_col: int | None) -> None:
        nonlocal run
        if not run:
            return
        if run[0][2] == 4:
            year0 = run[0][1]
            fixed = [(c, year0 if m >= 4 else year0 + 1, m) for c, _y, m in run]
        else:
            fixed = run
        for c, y, m in fixed:
            month_cols[c] = f"{y:04d}-{m:02d}"
        if label_col is not None and run[0][2] == 4:
            fy = fy_name(run[0][1])
            fy_cols[label_col] = fy
            if label and label[1] is not None:
                start = label[0] + 2000 if label[0] < 100 else label[0]
                if fy_name(start) != fy:
                    notes.append(f"column {label_col + 1}: header says FY{label[0]:02d}-{label[1]:02d} but its months run {fy}")
        run = []

    for j, v in enumerate(header):
        dec = _decode_header(v)
        if dec:
            if len(run) == 12:
                close(None, None)
            run.append((j, dec[0], dec[1]))
            continue
        if isinstance(v, str):
            m = _FY_LABEL.match(v)
            if m and run:
                close((int(m.group(1)), int(m.group(2)) if m.group(2) else None), j)
    close(None, None)
    return month_cols, fy_cols, notes


# --------------------------------------------------------------------------- plants

def plant_of(label: Any) -> str | None:
    """Plant id for a label in a sheet; handles spellings plants.canonical_plant does not."""
    text = str(label or "").lower().replace("banglore", "bangalore").replace("bengaluru", "bangalore")
    pid = canonical_plant(text)
    if pid:
        return pid
    key = re.sub(r"[^a-z0-9]", "", text)
    return "TASL-MCA" if "mca" in key else None


# --------------------------------------------------------------------------- block sheets

def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _find_header(grid: list[list[Any]], first: str, second: str) -> tuple[int, int, int] | None:
    """(row, plant_col, label_col): the row that has a 'Plant' cell and a 'Type of ...' cell."""
    for i, row in enumerate(grid[:30]):
        cells = [str(c).strip().lower() if isinstance(c, str) else "" for c in row]
        p = next((j for j, c in enumerate(cells) if c == first), None)
        t = next((j for j, c in enumerate(cells) if c.startswith(second)), None)
        if p is not None and t is not None:
            return i, p, t
    return None


class Block:
    """One plant's rows of a block sheet, by kind, for months and FY-total columns."""

    def __init__(self, plant: str | None, label: str) -> None:
        self.plant, self.label = plant, label
        self.month: dict[str, dict[str, float | None]] = {}   # kind -> month -> value
        self.fy: dict[str, dict[str, float | None]] = {}      # kind -> fy -> workbook FY column
        self.text_cells: list[str] = []                       # numbers typed as text, for notes
        self.na_cells: set[tuple[str, str]] = set()           # cells holding "NA"/"-" (an answer, not a blank)
        self.from_entry: set[tuple[str, str]] = set()         # cells filled from the entry workbook


def parse_blocks(grid: list[list[Any]], classify: Callable[[str], str | None],
                 plant_header: str = "plant", label_header: str = "type of") -> tuple[dict[str, Block], list[str]]:
    """Read a plant-block sheet. `classify` maps a row label to a kind; "total" kinds end a block."""
    found = _find_header(grid, plant_header, label_header)
    if not found:
        return {}, ["header row (Plant / Type of ...) not found"]
    hrow, pcol, tcol = found
    month_cols, fy_cols, notes = month_columns(grid[hrow])
    blocks: dict[str, Block] = {}
    current: Block | None = None
    for row in grid[hrow + 1:]:
        name = row[pcol]
        if not _is_blank(name):
            label = str(name).strip()
            pid = plant_of(label)
            key = pid or f"other:{label}"
            current = blocks.setdefault(key, Block(pid, label))
        if current is None:
            continue
        label = row[tcol]
        kind = classify(str(label).strip().lower()) if not _is_blank(label) else None
        if kind is None:
            continue
        for col, month in month_cols.items():
            raw = row[col]
            num = to_number(raw)
            if isinstance(raw, str) and num is not None:
                if kind != "total":
                    current.text_cells.append(f"{kind} {month}")
            elif isinstance(raw, str) and raw.strip():
                current.na_cells.add((kind, month))
            current.month.setdefault(kind, {}).setdefault(month, num)
        for col, fy in fy_cols.items():
            current.fy.setdefault(kind, {}).setdefault(fy, to_number(row[col]))
        if kind == "total":
            current = None   # summary tables below must not be read as plant rows
    return blocks, notes


def _classify_power(label: str) -> str | None:
    if "total" in label and "consumption" in label:
        return "total"
    if "discount" in label or label.startswith("govt"):
        return "grid"          # Bangalore: the after-38%-discount row is the grid figure that sums to the total
    if label.startswith("eb units"):
        return "ebGross"
    # "Units Purchased through open access / Third party (Solar, Wind, ...)" mentions solar: test it first.
    if "purchased" in label or "open access" in label:
        return "purchased"
    if "solar" in label:
        return "solar"
    if label.startswith("dg"):
        return "dg"
    return None


def _classify_diesel(label: str) -> str | None:
    if label.startswith("total"):
        return "total"
    if label == "dg":
        return "dg"
    if label == "hwg":
        return "hwg"
    if label.startswith("forl") or label.startswith("fork"):
        return "forklift"
    if label.startswith("other"):
        return "other"
    return None


def _classify_vehicle(label: str) -> str | None:
    if label.startswith("total"):
        return "total"
    return "cars" if "car" in label else None


def _classify_afforestation(label: str) -> str | None:
    if "total" in label:
        return "total"
    if "tree" in label:
        return "trees"
    if "shrub" in label:
        return "shrubs"
    return None


# --------------------------------------------------------------------------- loading

_lock = threading.Lock()
_build_lock = threading.RLock()   # one builder at a time: the workbooks are slow to open, do not open them twice
_cache: dict[str, Any] = {}


def _stamp() -> tuple:
    out = []
    for path in (workbooks.calculation_workbook(), workbooks.entry_workbook()):
        out.append((str(path), path.stat().st_mtime) if path else None)
    return tuple(out)


def _cached(name: str, build: Callable[[], Any]) -> Any:
    """Build once per version of the two workbooks; the grids themselves are cached by workbooks."""
    stamp = _stamp()
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == stamp:
            return hit[1]
    with _build_lock:
        with _lock:
            hit = _cache.get(name)
            if hit and hit[0] == stamp:
                return hit[1]
        value = build()
        with _lock:
            _cache[name] = (stamp, value)
        return value


def warm() -> None:
    """Load everything once so the first page view is not a multi-minute wait. Safe to call from a thread."""
    for loader in (load_power, load_diesel, load_afforestation, load_summaries, load_revenue, load_manpower,
                   _build_emissions_monthly_cached):
        try:
            loader()
        except Exception:  # noqa: BLE001 - a broken sheet is reported in the payload notes, never fatal
            pass


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _grid(path: Path | None, *name_parts: str, exact: str | None = None) -> tuple[list[list[Any]] | None, str | None]:
    """A sheet's grid, or (None, reason) when the file or sheet is missing."""
    if path is None:
        return None, "workbook not found"
    try:
        name = exact if exact and exact in workbooks.sheet_names(path) else workbooks.find_sheet(path, *name_parts)
        if not name:
            return None, f"sheet matching {' '.join(name_parts)!r} not found in {path.name}"
        return workbooks.read_sheet(path, name), None
    except Exception as exc:  # noqa: BLE001 - a broken file must not fail the page
        return None, f"could not read {path.name}: {exc}"


def _fill_from_entry(cb: Block, eb: Block) -> None:
    """Copy entry-workbook numbers into cells the calculation block left empty.

    The entry workbook pre-fills future months with literal zeros, so a lone 0 is not a report.
    A non-zero entry number fills its cell; once a month has been filled that way, the entry's
    remaining numbers for that month (zeros included) fill the other empty cells of the month.
    """
    kinds = [k for k in eb.month if k != "total"]
    months = sorted({m for k in kinds for m in eb.month[k]})
    for month in months:
        empty = [k for k in kinds if cb.month.get(k, {}).get(month) is None and (k, month) not in cb.na_cells
                 and eb.month[k].get(month) is not None]
        filled = [k for k in empty if eb.month[k][month] != 0]
        if not filled:
            continue
        for k in empty:
            cb.month.setdefault(k, {})[month] = eb.month[k][month]
            cb.from_entry.add((k, month))


def _blocks_with_fallback(sheet: str, classify: Callable[[str], str | None], notes: list[str]
                          ) -> dict[str, Block]:
    """Calculation-workbook blocks, with truly empty cells filled from the entry workbook.

    Only a cell that is empty in the calculation sheet is filled; "NA" or "-" there is an answer
    (to_number turns both into None, but the entry value is used only when the calc cell had no value
    at all *and* the entry workbook has a number).
    """
    calc, why = _grid(workbooks.calculation_workbook(), sheet, exact=sheet)
    if calc is None:
        notes.append(f"{sheet}: {why}")
        calc_blocks: dict[str, Block] = {}
    else:
        calc_blocks, n = parse_blocks(calc, classify)
        notes += [f"{sheet}: {x}" for x in n]
    entry, _why = _grid(workbooks.entry_workbook(), sheet, exact=sheet)
    if entry is None:
        return calc_blocks
    entry_blocks, _n = parse_blocks(entry, classify)
    for key, eb in entry_blocks.items():
        cb = calc_blocks.get(key)
        if cb is None:
            continue
        _fill_from_entry(cb, eb)
    return calc_blocks


# --------------------------------------------------------------------------- power

def _sum(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    return round(sum(nums), 6) if nums else None


def _flag_outliers(recs: dict[str, dict[str, Any]], factor: float, label: str, notes: list[str]) -> None:
    """Mark months whose total is more than `factor` times (or less than 1/factor of) the median of the
    same plant's other months in that FY. Nothing is changed or dropped; the flag lets the UI show a warning."""
    by_fy: dict[str, list[dict[str, Any]]] = {}
    for r in recs.values():
        by_fy.setdefault(r["fy"], []).append(r)
    hits = []
    for rows in by_fy.values():
        vals = sorted(r["total"] for r in rows if r.get("total"))
        if len(vals) < 6:
            continue
        median = vals[len(vals) // 2]
        for r in rows:
            t = r.get("total")
            if t and median and (t > factor * median or t < median / factor):
                r["flag"] = "outlier"
                hits.append(r["month"])
    if hits:
        notes.append(f"{label}: suspicious month(s) {', '.join(sorted(hits))} differ from the same plant's FY median by more than "
                     f"{factor:g}x (flag='outlier'); values are shown as in the sheet, possibly a unit slip.")


def load_power() -> dict[str, Any]:
    """{"plants": {id: {month: record}}, "notes": [...], "labels": {...}} for every plant and month."""
    return _cached("power", _build_power)


def _build_power() -> dict[str, Any]:
    notes: list[str] = []
    blocks = _blocks_with_fallback("Energy consumption data", _classify_power, notes)
    plants: dict[str, dict[str, dict[str, Any]]] = {}
    fy_workbook: dict[str, dict[str, float | None]] = {}
    other: list[str] = []
    for key, blk in blocks.items():
        if blk.plant is None:
            other.append(blk.label)
            continue
        months = sorted({m for kind in _POWER_KINDS for m in blk.month.get(kind, {})})
        recs: dict[str, dict[str, Any]] = {}
        entry_cells = blk.from_entry
        for month in months:
            vals = {k: blk.month.get(k, {}).get(month) for k in _POWER_KINDS}
            total = _sum(list(vals.values()))
            if not total:
                continue   # nothing reported yet: blank, or placeholder zeros (a plant never draws 0 kWh in a month)
            rec: dict[str, Any] = {"month": month, "fy": fy_of_month(month), **vals, "total": total}
            wb_total = blk.month.get("total", {}).get(month)
            if wb_total is not None and total is not None and abs(wb_total - total) > 1:
                rec["workbookTotal"] = wb_total
            gross = blk.month.get("ebGross", {}).get(month)
            if gross is not None:
                rec["gridGross"] = gross
            filled = sorted(k for k in _POWER_KINDS if (k, month) in entry_cells)
            if filled:
                rec["source"] = "entry"
                rec["fromEntry"] = filled
            recs[month] = rec
        _flag_outliers(recs, 8, plant_name(blk.plant) + " power", notes)
        plants[blk.plant] = recs
        fy_workbook[blk.plant] = blk.fy.get("total", {})
        if blk.text_cells:
            notes.append(f"{plant_name(blk.plant)}: {len(blk.text_cells)} cell(s) typed as text were read as numbers "
                         f"(e.g. {', '.join(blk.text_cells[:3])}); the workbook's own SUM ignores text.")
        diff = [m for m, r in sorted(recs.items()) if "workbookTotal" in r]
        if diff:
            notes.append(f"{plant_name(blk.plant)}: in {len(diff)} month(s) the workbook's 'Plant Total Consumption' differs "
                         f"from the sum of its source rows ({', '.join(diff[:6])}{'...' if len(diff) > 6 else ''}): text-typed "
                         "numbers that SUM ignores, or hard-typed totals. `total` is the sum of the rows; `workbookTotal` is the sheet's figure.")
    if other:
        notes.append("Plant blocks not among the eight were ignored: " + ", ".join(other))
    missing = [p for p in PLANT_IDS if p not in plants]
    if missing:
        notes.append("No power rows found for: " + ", ".join(missing))
    return {"plants": plants, "notes": notes, "fyWorkbook": fy_workbook}


def _fy_ok(month: str, fy: str | None) -> bool:
    return fy is None or fy_of_month(month) == fy


def power_monthly(plant: str | None = None, fy: str | None = None) -> dict[str, Any]:
    data = load_power()
    wanted = [plant] if plant else PLANT_IDS
    series = []
    for pid in wanted:
        recs = [r for m, r in sorted(data["plants"].get(pid, {}).items()) if _fy_ok(m, fy)]
        series.append({"plant": pid, "name": plant_name(pid), "cluster": _cluster_of(pid), "months": recs})
    out = {"unit": "kWh", "plants": series, "notes": list(data["notes"])}
    if plant is None:
        out["clusters"] = _cluster_monthly(data["plants"], fy)
    return out


def _cluster_of(pid: str) -> str | None:
    return next((c for c, members in CLUSTERS.items() if pid in members), None)


def _roll_up(plants: dict[str, dict[str, dict[str, Any]]], members: list[str], fy: str | None) -> list[dict[str, Any]]:
    months = sorted({m for p in members for m in plants.get(p, {}) if _fy_ok(m, fy)})
    out = []
    for month in months:
        recs = [plants[p][month] for p in members if month in plants.get(p, {})]
        row: dict[str, Any] = {"month": month, "fy": fy_of_month(month)}
        for kind in (*_POWER_KINDS, "total"):
            row[kind] = _sum([r.get(kind) for r in recs])
        row["plantsReporting"] = len(recs)
        row["plantsExpected"] = len(members)
        out.append(row)
    return out


def _cluster_monthly(plants: dict[str, dict[str, dict[str, Any]]], fy: str | None) -> dict[str, list[dict[str, Any]]]:
    out = {name: _roll_up(plants, members, fy) for name, members in CLUSTERS.items()}
    out["TADL"] = _roll_up(plants, PLANT_IDS, fy)
    return out


def _yearly_from_months(recs: list[dict[str, Any]], fy_workbook: dict[str, float | None] | None = None) -> list[dict[str, Any]]:
    by_fy: dict[str, list[dict[str, Any]]] = {}
    for r in recs:
        by_fy.setdefault(r["fy"], []).append(r)
    out = []
    for fy in sorted(by_fy):
        rows = by_fy[fy]
        row: dict[str, Any] = {"fy": fy}
        for kind in (*_POWER_KINDS, "total"):
            row[kind] = _sum([r.get(kind) for r in rows])
        row["monthsReported"] = len(rows)
        row["partial"] = len(rows) < 12
        renew = _sum([row["solar"], row["purchased"]])
        row["renewableShare"] = round(renew / row["total"], 6) if renew is not None and row["total"] else None
        wb = (fy_workbook or {}).get(fy)
        if wb is not None and row["total"] is not None and abs(wb - row["total"]) > 1:
            row["workbookTotal"] = wb
        out.append(row)
    return out


def power_yearly(plant: str | None = None) -> dict[str, Any]:
    data = load_power()
    wanted = [plant] if plant else PLANT_IDS
    series = []
    for pid in wanted:
        recs = [r for _m, r in sorted(data["plants"].get(pid, {}).items())]
        series.append({"plant": pid, "name": plant_name(pid), "cluster": _cluster_of(pid),
                       "years": _yearly_from_months(recs, data["fyWorkbook"].get(pid))})
    out: dict[str, Any] = {"unit": "kWh", "plants": series, "notes": list(data["notes"])}
    if plant is None:
        clusters = _cluster_monthly(data["plants"], None)
        out["clusters"] = {name: _yearly_from_months(rows) for name, rows in clusters.items()}
        for name, rows in out["clusters"].items():
            expected = len(PLANT_IDS) if name == "TADL" else len(CLUSTERS[name])
            by_fy: dict[str, int] = {}
            for r in clusters[name]:
                by_fy[r["fy"]] = min(by_fy.get(r["fy"], expected), r["plantsReporting"])
            for row in rows:
                row["fewestPlantsReporting"] = by_fy.get(row["fy"])
        out["notes"].append("Cluster and TADL figures are sums of the plant rows computed here; the workbook's own "
                            "DP/Assy summary tables reference the wrong rows in several columns and are not used.")
    return out


# --------------------------------------------------------------------------- diesel

_DIESEL_PARTS = ("dg", "hwg", "forklift", "other")


def load_diesel() -> dict[str, Any]:
    return _cached("diesel", _build_diesel)


def _build_diesel() -> dict[str, Any]:
    notes: list[str] = []
    blocks = _blocks_with_fallback("Diesel consumption data", _classify_diesel, notes)
    plants: dict[str, dict[str, dict[str, Any]]] = {}
    for blk in blocks.values():
        if blk.plant is None:
            continue
        months = sorted({m for k in _DIESEL_PARTS for m in blk.month.get(k, {})})
        recs = {}
        entry_cells = blk.from_entry
        for month in months:
            vals = {k: blk.month.get(k, {}).get(month) for k in _DIESEL_PARTS}
            if all(v is None for v in vals.values()):
                continue
            rec: dict[str, Any] = {"month": month, "fy": fy_of_month(month), **vals, "total": _sum(list(vals.values()))}
            if any((k, month) in entry_cells for k in _DIESEL_PARTS):
                rec["source"] = "entry"
            recs[month] = rec
        _flag_outliers(recs, 50, plant_name(blk.plant) + " diesel", notes)
        plants[blk.plant] = recs
    vehicle: dict[str, dict[str, float]] = {}
    vblocks = _blocks_with_fallback("Vehicle- Diesel consumption dat", _classify_vehicle, notes)
    for blk in vblocks.values():
        if blk.plant is None:
            continue
        series = blk.month.get("total", {})
        cars = blk.month.get("cars", {})
        vehicle[blk.plant] = {m: (cars.get(m) if cars.get(m) is not None else series.get(m))
                              for m in sorted(series) if (cars.get(m) is not None or series.get(m) is not None)}
    notes.append("Generator diesel (DG, HWG, forklift, other) is from 'Diesel consumption data' in KL. "
                 "'Vehicle- Diesel consumption dat' is labelled KL but its magnitudes (hundreds per plant-month, "
                 "12,145 for FY25-26 against ~614 KL total diesel in the TADL summary) look like litres; "
                 "it is returned as labelled, in `vehicle`, and never added to the generator total.")
    return {"plants": plants, "vehicle": vehicle, "notes": notes}


def diesel_monthly(plant: str | None = None, fy: str | None = None) -> dict[str, Any]:
    data = load_diesel()
    series = []
    for pid in ([plant] if plant else PLANT_IDS):
        recs = [r for m, r in sorted(data["plants"].get(pid, {}).items()) if _fy_ok(m, fy)]
        veh = [{"month": m, "fy": fy_of_month(m), "value": v} for m, v in sorted(data["vehicle"].get(pid, {}).items())
               if _fy_ok(m, fy)]
        series.append({"plant": pid, "name": plant_name(pid), "months": recs, "vehicle": veh,
                       "years": _yearly_diesel(recs)})
    return {"unit": "KL", "vehicleUnit": "KL as labelled in the sheet (values look like litres)",
            "plants": series, "notes": list(data["notes"])}


def _yearly_diesel(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_fy: dict[str, list[dict[str, Any]]] = {}
    for r in recs:
        by_fy.setdefault(r["fy"], []).append(r)
    return [{"fy": fy, **{k: _sum([r.get(k) for r in rows]) for k in (*_DIESEL_PARTS, "total")},
             "monthsReported": len(rows)} for fy, rows in sorted(by_fy.items())]


# --------------------------------------------------------------------------- afforestation

def load_afforestation() -> dict[str, Any]:
    return _cached("afforestation", _build_afforestation)


def _build_afforestation() -> dict[str, Any]:
    notes: list[str] = []
    blocks = _blocks_with_fallback("Afforestation sheet", _classify_afforestation, notes)
    plants: dict[str, dict[str, dict[str, Any]]] = {}
    for blk in blocks.values():
        if blk.plant is None:
            continue
        months = sorted({m for k in ("trees", "shrubs", "total") for m in blk.month.get(k, {})})
        recs = {}
        for month in months:
            trees, shrubs = blk.month.get("trees", {}).get(month), blk.month.get("shrubs", {}).get(month)
            total = blk.month.get("total", {}).get(month)
            if trees is None and shrubs is None:
                continue   # the sheet's total cell is a SUM and reads 0 when nothing was entered
            rec = {"month": month, "fy": fy_of_month(month), "trees": trees, "shrubs": shrubs,
                   "total": _sum([trees, shrubs]) if total is None else total}
            if any((k, month) in blk.from_entry for k in ("trees", "shrubs")):
                rec["source"] = "entry"
            recs[month] = rec
        plants[blk.plant] = recs
    benefit, bnotes = _afforestation_benefit()
    notes += bnotes
    return {"plants": plants, "benefit": benefit, "notes": notes}


def _afforestation_benefit() -> tuple[list[dict[str, Any]], list[str]]:
    grid, why = _grid(workbooks.calculation_workbook(), "afforestration benefit", exact="Afforestration Benefit")
    if grid is None:
        return [], [f"Afforestration Benefit: {why}"]
    tables: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in grid:
        head = str(row[0] or "").strip().lower()
        if head == "financial year":
            current = {"table": len(tables) + 1, "rows": []}
            tables.append(current)
        elif current is not None and (fy := normalize_fy(row[0])):
            current["rows"].append({"fy": fy, "trees": to_number(row[1]), "plants": to_number(row[2]),
                                    "carbonFootprintKg": to_number(row[3])})
    notes = []
    if len(tables) > 1:
        notes.append("'Afforestration Benefit' holds two tables for the same years with different figures "
                     "(e.g. FY23-24 benefit 1,168,286.5 kg in table 1, 446,268 kg in table 2); both are returned. "
                     "The Summary sheets use table 2 in their first block and a flat 1,168,000 kg in the Scope 2 block.")
    return tables, notes


def afforestation_monthly(plant: str | None = None, fy: str | None = None) -> dict[str, Any]:
    data = load_afforestation()
    series = []
    for pid in ([plant] if plant else PLANT_IDS):
        recs = [r for m, r in sorted(data["plants"].get(pid, {}).items()) if _fy_ok(m, fy)]
        series.append({"plant": pid, "name": plant_name(pid), "months": recs})
    return {"unit": "No. (trees and shrubs planted)", "plants": series, "benefitTables": data["benefit"],
            "benefitUnit": "kg CO2", "notes": list(data["notes"])}


# --------------------------------------------------------------------------- summary sheets (per FY)

# metric key -> (label regex on the first column, which match, unit)
_SUMMARY_ROWS: dict[str, tuple[str, int, str]] = {
    "ebUnits": (r"^total eb units", 0, "kWh"),
    "solarGenerated": (r"^solar units generated", 0, "kWh"),
    "renewablePurchased": (r"^renewable (\(solar\) )?unit", 0, "kWh"),
    "dgUnits": (r"^dg units", 0, "kWh"),
    "totalUnits": (r"^total units for scope 2", 0, "kWh"),
    "scope2Basis": (r"^scope 2 carbon emissions", 0, "kg CO2"),
    "renewableBenefitBasis": (r"^renewable benefit", 0, "kg CO2"),
    "netCarbonBasis": (r"^net carbon emission", 0, "kg CO2"),
    "scope1": (r"^total scope 1 emissions", 0, "see notes: label says Kg, values are tonnes"),
    "scope2Gross": (r"^total (gross )?scope 2 emissions", 0, "kg CO2"),
    "scope2IfGridInsteadOfSolar": (r"^emissions if eb instead of solar", 1, "kg CO2"),
    "afforestationBenefit": (r"^afforestation benefit", 1, "kg CO2"),
    "scope2Savings": (r"^scope 2 savings$", 1, "kg CO2"),
    "scope1Plus2": (r"^total scope 1 & scope 2", 0, "kg CO2 (adds tonnes to kg, see notes)"),
    "emissionsPerRevenue": (r"^total emissions per rs", 0, "kg per Cr"),
    "dieselDg": (r"^diesel used for dg", 0, "KL"),
    "dieselHwg": (r"^diesel used for hwg", 0, "KL"),
    "dieselCars": (r"^diesel used for company", 0, "KL"),
    "dieselTotal": (r"^total diesel used", 0, "KL"),
    "revenue": (r"^revenue$", 1, "Rs Cr"),
    "employees": (r"^employees", 0, "headcount"),
    "megaUnitsPerRevenue": (r"^mega units", 0, "MWh per Cr"),
    "hsdDgPerRevenue": (r"^hsd consumption for dg", 0, "KL per Cr"),
    "hsdHwgPerRevenue": (r"^hsd consumption for hwg", 0, "KL per Cr"),
    "scope1PerKwh": (r"^scope1 emm", 0, "per kWh"),
    "scope2PerKwh": (r"^scope2 emm", 0, "kg per kWh"),
    "renewableShare": (r"^overall renewable %", 0, "fraction"),
}


def _summary_sheet_plant(name: str) -> str | None:
    if name.upper().startswith("TADL"):
        return "TADL"
    return canonical_plant(name.split(" Sustainability")[0]) or canonical_plant(name)


def load_summaries() -> dict[str, Any]:
    return _cached("summaries", _build_summaries)


def _build_summaries() -> dict[str, Any]:
    path = workbooks.calculation_workbook()
    notes: list[str] = []
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    factors: list[dict[str, Any]] = []
    if path is None:
        return {"summaries": out, "factors": factors, "notes": ["calculation workbook not found"]}
    try:
        names = [n for n in workbooks.sheet_names(path) if "sustainability su" in n.lower()]   # one sheet name is cut at 31 characters
    except Exception as exc:  # noqa: BLE001
        return {"summaries": out, "factors": factors, "notes": [f"could not open {path.name}: {exc}"]}
    for name in names:
        pid = _summary_sheet_plant(name)
        if pid is None:
            notes.append(f"summary sheet {name!r} is not one of the eight plants or TADL; ignored")
            continue
        try:
            grid = workbooks.read_sheet(path, name)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"could not read {name!r}: {exc}")
            continue
        hdr = next((i for i, r in enumerate(grid[:5]) if str(r[0] or "").strip().lower() == "particulars"), None)
        if hdr is None:
            notes.append(f"{name!r}: header row not found")
            continue
        fy_cols: dict[int, str] = {}
        for j, v in enumerate(grid[hdr]):
            if isinstance(v, str) and v.strip().lower() == "assumption":
                break
            m = re.match(r"^\s*fy\s*(\d{2})\s*-\s*(\d{2})\b", v, re.I) if isinstance(v, str) else None
            if m:
                fy_cols.setdefault(j, fy_name(2000 + int(m.group(1))))
        data: dict[str, dict[str, float | None]] = {}
        for key, (pattern, nth, _unit) in _SUMMARY_ROWS.items():
            rx = re.compile(pattern, re.I)
            hits = [r for r in grid if isinstance(r[0], str) and rx.match(r[0].strip())]
            if len(hits) <= nth:
                continue
            row = hits[nth]
            data[key] = {fy: to_number(row[c]) for c, fy in fy_cols.items()}
        out[pid] = data
        if pid == "TADL":
            factors = _read_factors(grid, hdr)
    return {"summaries": out, "factors": factors, "notes": notes}


def _read_factors(grid: list[list[Any]], hdr: int) -> list[dict[str, Any]]:
    """Rows of the TADL summary sheet that carry a 'Conversion Factor' next to the calculation."""
    head = grid[hdr]
    try:
        fcol = next(j for j, v in enumerate(head) if str(v).strip().lower() == "conversion factor")
    except StopIteration:
        return []
    out = []
    for i, row in enumerate(grid):
        factor = to_number(row[fcol])
        unit = str(row[fcol + 1] or "").strip()
        if i <= hdr or factor is None or not unit.lower().startswith("co2"):
            continue   # other cells in that column are not emission factors
        out.append({"appliesTo": str(row[0]).strip(), "factor": factor, "unit": str(row[fcol + 1]).strip(),
                    "source": str(row[fcol + 2]).strip() if row[fcol + 2] else None, "row": i + 1})
    return out


def _conversion_matrix() -> list[dict[str, Any]]:
    grid, _why = _grid(workbooks.calculation_workbook(), "conversion matrix", exact="Conversion matrix")
    out = []
    for row in grid or []:
        factor = to_number(row[2]) if len(row) > 2 else None
        if factor is not None and row[1]:
            out.append({"energyType": str(row[1]).strip(), "factor": factor, "unit": str(row[3]).strip() if row[3] else None})
    return out


_EMISSION_FACTOR_NOTES = [
    "The workbook uses more than one factor set. The 'Scope 2 Carbon emissions' block (rows 12-19 of the Summary sheets) "
    "uses 0.85 kg CO2/kWh for all units and (0.85 - 0.05) for renewable benefit, matching the 'Conversion matrix' sheet "
    "(0.85 kg CO2 per kWh, 2.7 kg per L HSD, 0.005 solar). The detailed block (rows 52-86) uses 0.823 kg CO2/kWh grid "
    "(CEA), 41 g CO2/kWh solar life-cycle and 2.64 kg CO2 per L diesel. `scope1`, `scope2Gross`, `scope2Savings`, "
    "`afforestationBenefit` and `emissionsPerRevenue` come from the detailed block; `*Basis` fields from the 0.85 block.",
    "Unit oddity: Scope 1 is diesel KL x 2.64 = tonnes CO2, yet the Summary sheets label it Kg; Scope 2 is kWh x 0.823 = kg. "
    "The workbook's 'Total Scope 1 & Scope 2' (scope1Plus2) adds the two as if both were kg, so Scope 1 is negligible in it.",
]


def _fy_filter(rows: dict[str, float | None], fy: str | None) -> dict[str, float | None]:
    return {k: v for k, v in rows.items() if fy is None or k == fy}


def _plant_rows(summaries: dict[str, Any], pid: str, keys: list[str], fy: str | None = None) -> list[dict[str, Any]]:
    data = summaries.get(pid, {})
    fys = sorted({f for k in keys for f in data.get(k, {})})
    rows = []
    for f in fys:
        if fy and f != fy:
            continue
        row: dict[str, Any] = {"fy": f}
        for k in keys:
            row[k] = data.get(k, {}).get(f)
        if any(row[k] is not None for k in keys):
            rows.append(row)
    return rows


def _series_ids(plant: str | None) -> list[str]:
    return [plant] if plant else [*PLANT_IDS, "TADL"]


def _label(pid: str) -> str:
    return "TADL (company total)" if pid == "TADL" else plant_name(pid)


# --------------------------------------------------------------------------- emissions

def emissions(plant: str | None = None, fy: str | None = None) -> dict[str, Any]:
    s = load_summaries()
    keys = ["scope1", "scope2Gross", "scope1Plus2", "scope2IfGridInsteadOfSolar", "afforestationBenefit",
            "scope2Savings", "emissionsPerRevenue", "scope2Basis", "renewableBenefitBasis", "netCarbonBasis"]
    series = [{"plant": pid, "name": _label(pid), "years": _plant_rows(s["summaries"], pid, keys, fy)}
              for pid in _series_ids(plant)]
    notes = list(s["notes"]) + _EMISSION_FACTOR_NOTES + [
        "FY26-27 columns are year-to-date (see energy/yearly monthsReported).",
        "`afforestationBenefit` here is the Scope 2 block figure (a flat 1,168,000 kg for FY23-24 onward in most plant sheets); "
        "the 'Afforestration Benefit' sheet gives different numbers, see /api/energy/afforestation.",
    ]
    return {"units": {"scope1": "tonnes CO2 (sheet label says Kg)", "scope2Gross": "kg CO2", "scope1Plus2": "mixed, see notes",
                      "scope2IfGridInsteadOfSolar": "kg CO2 avoided by renewables", "afforestationBenefit": "kg CO2",
                      "scope2Savings": "kg CO2 (renewable saving + afforestation)", "emissionsPerRevenue": "kg per Cr revenue"},
            "factors": s["factors"], "conversionMatrix": _conversion_matrix(), "plants": series,
            "monthly": emissions_monthly(plant, fy), "notes": notes}


def emissions_monthly(plant: str | None, fy: str | None) -> dict[str, Any]:
    return _filter_monthly_emissions(plant, fy)


def _build_emissions_monthly() -> dict[str, Any]:
    grid, why = _grid(workbooks.calculation_workbook(), "emissions", exact="Emissions")
    if grid is None:
        return {"plants": {}, "notes": [f"Emissions: {why}"], "last": None}
    head = [str(c).strip().lower() if isinstance(c, str) else "" for c in grid[0]]
    try:
        cp, cy, cm, cs, ca = (head.index("plant"), head.index("year"), head.index("month"),
                              head.index("emissions"), head.index("emissions amount"))
    except ValueError:
        return {"plants": {}, "notes": ["Emissions: expected columns Year/Month/Plant/Emissions/Emissions Amount not found"], "last": None}
    plants: dict[str, dict[str, dict[str, float | None]]] = {}
    for row in grid[1:]:
        pid = plant_of(row[cp])
        mon = _MONTH_NUM.get(str(row[cm] or "").strip()[:3].lower())
        year = to_number(row[cy])
        amount = to_number(row[ca])
        scope = str(row[cs] or "").strip().lower()
        if not (pid and mon and year and scope in ("scope 1", "scope 2")):
            continue
        key = "scope1" if scope == "scope 1" else "scope2"
        plants.setdefault(pid, {}).setdefault(f"{int(year):04d}-{mon:02d}", {})[key] = amount
    populated = [m for months in plants.values() for m, v in months.items() if any(x for x in v.values())]
    last = max(populated) if populated else None
    return {"plants": plants, "last": last, "notes": []}


def _build_emissions_monthly_cached() -> dict[str, Any]:
    return _cached("emissions_monthly", _build_emissions_monthly)


def _filter_monthly_emissions(plant: str | None, fy: str | None) -> dict[str, Any]:
    data = _build_emissions_monthly_cached()
    last = data["last"]
    series = []
    for pid in ([plant] if plant else PLANT_IDS):
        months = [{"month": m, "fy": fy_of_month(m), **v} for m, v in sorted(data["plants"].get(pid, {}).items())
                  if last and m <= last and _fy_ok(m, fy)]
        series.append({"plant": pid, "name": plant_name(pid), "months": months})
    return {"unit": "kg CO2", "lastPopulatedMonth": last, "plants": series,
            "notes": data["notes"] + [
                "Source: 'Emissions' sheet (Tableau feed). Its amounts are pasted values from 'Energy Sheet Working' "
                "(kWh x 0.823 for grid, DG units as Scope 1) and 'Diesel Sheet Working' (KL x 2640 kg) and stop at "
                f"{last}; later months are 0 in the sheet only because nothing was pasted, so they are omitted here.",
                "Scope 1 in this sheet is in kg (KL x 2640) while the Summary sheets show the same quantity in tonnes."]}


# --------------------------------------------------------------------------- revenue

def load_revenue() -> dict[str, Any]:
    return _cached("revenue", _build_revenue)


def _build_revenue() -> dict[str, Any]:
    notes: list[str] = []
    grid, why = _grid(workbooks.calculation_workbook(), "revenue summary", exact="TADL Revenue Summary")
    monthly: dict[str, dict[str, float | None]] = {}
    fy_total: dict[str, dict[str, float | None]] = {}
    if grid is None:
        notes.append(f"TADL Revenue Summary: {why}")
    else:
        hdr = next((i for i, r in enumerate(grid[:10]) if str(r[0] or "").strip().lower() == "plant"
                    and str(r[1] or "").strip().lower().startswith("sub")), None)
        if hdr is None:
            notes.append("TADL Revenue Summary: header row (Plant / Sub-Plant) not found")
        else:
            month_cols, fy_cols, n = month_columns(grid[hdr])
            notes += [f"TADL Revenue Summary: {x}" for x in n]
            for row in grid[hdr + 1:]:
                label = str(row[1] or "").strip()
                pid = "TADL" if label.upper() == "TADL" else plant_of(label)
                if pid is None or pid in monthly:
                    continue
                monthly[pid] = {m: to_number(row[c]) for c, m in month_cols.items() if to_number(row[c]) is not None}
                fy_total[pid] = {fy: to_number(row[c]) for c, fy in fy_cols.items()}
            jan_eq_feb = [p for p, m in monthly.items() if p != "TADL" and m.get("2026-01") is not None
                          and m.get("2026-01") == m.get("2026-02")]
            if jan_eq_feb:
                notes.append("TADL Revenue Summary: Jan-26 equals Feb-26 for " + ", ".join(jan_eq_feb) +
                             " (the Jan cells are formulas pointing at the 'FTM Feb 26' side table); "
                             "treat Jan-26 revenue as unreliable.")
    s = load_summaries()
    yearly: dict[str, dict[str, float | None]] = {}
    dropped: list[str] = []
    for pid in [*PLANT_IDS, "TADL"]:
        rows = {}
        for fy, v in s["summaries"].get(pid, {}).get("revenue", {}).items():
            if v is None or v == 0:
                continue   # 0 in the sheet means "not entered yet" (e.g. FY26-27), not zero revenue
            if v > 100_000:   # TADL's whole turnover is ~4,000 Cr; this is a rupee figure pasted into a Cr row
                dropped.append(f"{plant_name(pid) if pid != 'TADL' else 'TADL'} {fy} ({v:g})")
                continue
            rows[fy] = v
        yearly[pid] = rows
    if dropped:
        notes.append("Implausible yearly revenue left out (more than 100,000 Cr): " + ", ".join(dropped))
    notes += s["notes"]
    notes.append("Yearly revenue is the 'Revenue' row (CR) of each '... Sustainability Summary' sheet. The first "
                 "'Revenue' row of several plant sheets (rows 20/31) is in rupees despite its 'CR' label; not used.")
    return {"monthly": monthly, "fyTotals": fy_total, "yearly": yearly, "notes": notes}


def revenue(plant: str | None = None, fy: str | None = None) -> dict[str, Any]:
    data = load_revenue()
    series = []
    for pid in _series_ids(plant):
        months = [{"month": m, "fy": fy_of_month(m), "value": v} for m, v in sorted(data["monthly"].get(pid, {}).items())
                  if _fy_ok(m, fy)]
        years = [{"fy": f, "value": v} for f, v in sorted(data["yearly"].get(pid, {}).items())
                 if v is not None and (fy is None or f == fy)]
        series.append({"plant": pid, "name": _label(pid), "years": years, "months": months})
    return {"unit": "Rs Cr", "plants": series, "notes": list(data["notes"])}


# --------------------------------------------------------------------------- manpower

def load_manpower() -> dict[str, Any]:
    return _cached("manpower", _build_manpower)


def _build_manpower() -> dict[str, Any]:
    s = load_summaries()
    notes = list(s["notes"])
    yearly = {pid: s["summaries"].get(pid, {}).get("employees", {}) for pid in [*PLANT_IDS, "TADL"]}
    snapshot: list[dict[str, Any]] = []
    grid, why = _grid(workbooks.calculation_workbook(), "total empl", exact="TADL Total Empl Count")
    if grid is None:
        notes.append(f"TADL Total Empl Count: {why}")
    else:
        for i, row in enumerate(grid):
            cols = [str(c).strip().lower() if isinstance(c, str) else "" for c in row]
            if "division/plant" in cols:
                c0 = cols.index("division/plant")
                for r in grid[i + 1:]:
                    label = str(r[c0] or "").strip()
                    total = to_number(r[c0 + 5]) if len(r) > c0 + 5 else None
                    if not label or total is None:
                        continue
                    pid = plant_of(label)
                    snapshot.append({"label": label, "plant": pid,
                                     "onRoll": to_number(r[c0 + 1]), "contract": to_number(r[c0 + 2]),
                                     "apprentices": to_number(r[c0 + 3]), "offRollTotal": to_number(r[c0 + 4]), "total": total})
                break
        else:
            notes.append("TADL Total Empl Count: 'Division/Plant' table not found")
    notes.append("'TADL Total Empl Count' has no date; it is the latest headcount snapshot. Site codes: "
                 "H01 = TASL-Hyd, B01 = TASL-Bangalore, N01 = TASL-Nagpur (matched by revenue: FTM Feb 26 of 59/62/61 Cr). "
                 "'TASL PMO' and 'TASL Aero' (a roll-up) are not plants and keep plant=null.")
    notes.append("The 'Employees' sheet (on-roll counts by legal entity, total 6,578) uses entity names that cannot be tied "
                 "to a site with certainty and is not used.")
    return {"yearly": yearly, "snapshot": snapshot, "notes": notes}


def manpower(plant: str | None = None) -> dict[str, Any]:
    data = load_manpower()
    series = [{"plant": pid, "name": _label(pid),
               "years": [{"fy": f, "headcount": v} for f, v in sorted(data["yearly"].get(pid, {}).items()) if v is not None]}
              for pid in _series_ids(plant)]
    snap = [r for r in data["snapshot"] if plant is None or r["plant"] == plant]
    return {"unit": "headcount", "plants": series, "latestSnapshot": snap, "notes": list(data["notes"])}


# --------------------------------------------------------------------------- intensity

def intensity(plant: str | None = None) -> dict[str, Any]:
    s = load_summaries()
    power = load_power()["plants"]
    keys = ["megaUnitsPerRevenue", "hsdDgPerRevenue", "hsdHwgPerRevenue", "emissionsPerRevenue",
            "scope2PerKwh", "scope1PerKwh", "renewableShare"]
    series = []
    for pid in _series_ids(plant):
        rows = {r["fy"]: r for r in _plant_rows(s["summaries"], pid, keys)}
        data = s["summaries"].get(pid, {})
        for fy, row in rows.items():
            units, heads = data.get("totalUnits", {}).get(fy), data.get("employees", {}).get(fy)
            months = sum(1 for r in power.get(pid, {}).values() if r["fy"] == fy) if pid != "TADL" else 12
            # not in the workbook: stated formula, only for a full year where both inputs are real numbers
            row["kwhPerEmployeeComputed"] = round(units / heads, 2) if units and heads and months >= 12 else None
        series.append({"plant": pid, "name": _label(pid), "years": [rows[f] for f in sorted(rows)]})
    return {"units": {"megaUnitsPerRevenue": "MWh per Cr revenue", "hsdDgPerRevenue": "KL per Cr", "hsdHwgPerRevenue": "KL per Cr",
                      "emissionsPerRevenue": "kg per Cr", "scope2PerKwh": "kg per kWh", "scope1PerKwh": "Scope1 (as sheet) x1000 / kWh",
                      "renewableShare": "fraction (TADL sheet only)",
                      "kwhPerEmployeeComputed": "kWh per head = 'Total Units for Scope 2' / 'Employees' (computed here, not in the workbook)"},
            "plants": series,
            "notes": list(s["notes"]) + [
                "No per-employee energy intensity exists in the workbook; `kwhPerEmployeeComputed` is the quotient stated above.",
                "'Overall HSD consumption wrt Revenue' (row 105) is not returned: its formula divides only the company-car diesel by revenue.",
                "FY26-27 columns are year-to-date; their revenue is blank/0 so ratios using it are #DIV/0! in the sheet and null here."]}


# --------------------------------------------------------------------------- targets

def targets() -> dict[str, Any]:
    grid, why = _grid(workbooks.calculation_workbook(), "target sheet", exact="Target Sheet")
    if grid is None:
        return {"targets": [], "notes": [f"Target Sheet: {why}"]}
    rows = []
    for row in grid[1:]:
        cat, fy_text = row[1], row[2]
        if not isinstance(cat, str):
            continue
        fy = normalize_fy(fy_text)
        rows.append({"category": cat.strip(), "fy": fy, "targetLevel": to_number(row[3]), "reductionFraction": to_number(row[4])})
    return {"targets": rows, "unit": "kg CO2 for carbon categories (as in the sheet; no unit column)",
            "notes": ["'Target Sheet' holds carbon-emission and landfill-waste targets, not energy targets. Carbon Emission - 2030 is "
                      "88,082,944.932 with a 0.75 reduction for every FY listed; Carbon Emission - 2045 and Landfill Waste - 2030 are 0 "
                      "(not set). No energy (kWh) target exists in this workbook."]}


# --------------------------------------------------------------------------- overview

def overview() -> dict[str, Any]:
    power = load_power()
    plants = power["plants"]
    tadl = _roll_up(plants, PLANT_IDS, None)
    notes = list(power["notes"])
    if not tadl:
        return {"unit": "kWh", "latestMonth": None, "latestCompleteMonth": None, "fy": None, "plants": [], "tadl": None, "notes": notes}
    latest = tadl[-1]["month"]
    complete = [r["month"] for r in tadl if r["plantsReporting"] == len(PLANT_IDS)]
    latest_complete = complete[-1] if complete else None
    fy = fy_of_month(latest)
    rows = []
    for pid in PLANT_IDS:
        recs = plants.get(pid, {})
        mine = [r for m, r in sorted(recs.items()) if r["fy"] == fy]
        last = max(recs) if recs else None
        ytd = _yearly_from_months(mine)
        rows.append({"plant": pid, "name": plant_name(pid), "cluster": _cluster_of(pid),
                     "latestReportedMonth": last, "latest": recs.get(last) if last else None,
                     "fyToDate": ytd[0] if ytd else None})
    ytd_all = _yearly_from_months([r for r in tadl if r["fy"] == fy])
    summaries = load_summaries()["summaries"].get("TADL", {})
    return {"unit": "kWh", "latestMonth": latest, "latestCompleteMonth": latest_complete, "fy": fy,
            "plants": rows, "tadl": {"latest": tadl[-1], "fyToDate": ytd_all[0] if ytd_all else None,
                                     "latestFullYear": _latest_full_year(summaries)},
            "notes": notes + [f"{latest} is the latest month any plant reported; plantsReporting says how many did. "
                              "FY-to-date sums only the months each plant has reported (monthsReported)."]}


def _latest_full_year(summaries: dict[str, Any]) -> dict[str, Any] | None:
    revenue_by_fy = summaries.get("revenue", {})
    fy = max((f for f, v in revenue_by_fy.items() if v), default=None)
    if not fy:
        return None
    return {"fy": fy, **{k: summaries.get(k, {}).get(fy) for k in
                         ("totalUnits", "scope1", "scope2Gross", "scope2Savings", "revenue", "employees", "dieselTotal")}}


# --------------------------------------------------------------------------- routes

def _norm_plant(plant: str | None) -> str | None:
    return plant_of(plant) if plant else None


def _check_fy(fy: str | None) -> str | None:
    return normalize_fy(fy) if fy else None


@router.get("/overview")
def overview_route() -> dict:
    return overview()


@router.get("/monthly")
def monthly_route(plant: str | None = Query(None), fy: str | None = Query(None)) -> dict:
    return power_monthly(_norm_plant(plant), _check_fy(fy))


@router.get("/yearly")
def yearly_route(plant: str | None = Query(None)) -> dict:
    return power_yearly(_norm_plant(plant))


@router.get("/diesel")
def diesel_route(plant: str | None = Query(None), fy: str | None = Query(None)) -> dict:
    return diesel_monthly(_norm_plant(plant), _check_fy(fy))


@router.get("/emissions")
def emissions_route(plant: str | None = Query(None), fy: str | None = Query(None)) -> dict:
    return emissions(_norm_plant(plant), _check_fy(fy))


@router.get("/revenue")
def revenue_route(plant: str | None = Query(None), fy: str | None = Query(None)) -> dict:
    return revenue(_norm_plant(plant), _check_fy(fy))


@router.get("/manpower")
def manpower_route(plant: str | None = Query(None)) -> dict:
    return manpower(_norm_plant(plant))


@router.get("/intensity")
def intensity_route(plant: str | None = Query(None)) -> dict:
    return intensity(_norm_plant(plant))


@router.get("/afforestation")
def afforestation_route(plant: str | None = Query(None), fy: str | None = Query(None)) -> dict:
    return afforestation_monthly(_norm_plant(plant), _check_fy(fy))


@router.get("/targets")
def targets_route() -> dict:
    return targets()


# Functions for other modules ------------------------------------------------------------------

def workbook_monthly_values(parameter: str) -> dict[str, dict[str, float | None]]:
    """{plant: {month: value}} of what the common workbooks hold, for manual.combined().

    diesel -> total generator diesel in KL; revenue -> Rs Cr; afforestation -> trees+shrubs (No.);
    manpower -> {} (the workbook only has yearly headcount, see manpower_yearly()).
    """
    if parameter == "diesel":
        return {p: {m: r["total"] for m, r in recs.items() if r["total"] is not None}
                for p, recs in load_diesel()["plants"].items()}
    if parameter == "revenue":
        return {p: dict(m) for p, m in load_revenue()["monthly"].items() if p in PLANT_IDS}
    if parameter == "afforestation":
        return {p: {m: r["total"] for m, r in recs.items() if r["total"] is not None}
                for p, recs in load_afforestation()["plants"].items()}
    return {}


def manpower_yearly() -> dict[str, dict[str, float | None]]:
    return {p: v for p, v in load_manpower()["yearly"].items() if p in PLANT_IDS}


__all__ = ["router", "PLANTS", "power_monthly", "power_yearly", "diesel_monthly", "emissions", "revenue", "manpower",
           "intensity", "targets", "overview", "afforestation_monthly", "workbook_monthly_values", "manpower_yearly",
           "fy_of_month", "normalize_fy", "plant_of"]


def _start_warm_up() -> None:
    """Open the big workbooks in the background when the server starts (not under pytest or PEM_NO_WARM)."""
    import os
    import sys
    if os.environ.get("PEM_NO_WARM") or "pytest" in sys.modules:
        return
    threading.Thread(target=warm, name="energy-warm-up", daemon=True).start()


_start_warm_up()
