"""Water area: flow-meter status, monthly/yearly water by source, reuse, rainwater, ground water.

Sources (all read through `workbooks.read_sheet`, cached):

* Monthly water, per plant      -> "Sustainability Calculation" > "Water consumption data" (primary).
  A blank cell there is filled from "Sustainability (Only to Enter)" > "Water consumption data"
  and the month is marked `"source": "entry"`.  Why this sheet and not the others: it is the
  hand-kept master with one block per plant, FY18-19 to FY26-27, and it is what the workbook's own
  reuse % rows are computed on.  "Water Sheet Working" / "Water Data Sheet" / "Complete Water Data
  Sheet" / "Water Sheet Tableau" are downstream copies that stop in 2024-25 and still carry
  typing errors that were corrected in the master (e.g. TLMAL Mar-21 borewell 8620 vs 862).
* Flow meters                    -> water/"Water Flow Meter Status.xlsx" (plant-level only).
* Ground water                   -> "Ground Water" sheet; rainwater pits -> "Rainwater harvesting1";
  estimated rain collection      -> "Only to Enter" > "RWH"; reuse history -> "Water Summary",
  "GEM Water Reuse Con Per% Trends", "Percapita water consumption".

Layout notes that drive the code (details in docs/api/water.md):
* The master sheet is wide: one 12-month block per financial year, newest first, each closed by an
  "FY 26-27" style column.  Month header cells are unreliable (typed "Apr-26" but stored as the
  date 26-Apr; "0ct-25"), so a block is read as the 12 columns that precede its FY label, April first.
* Plant names sit in the first row of a block; the block ends with "Total Reuse water %age".
  An "Overall TADL" block follows, whose detail rows are shifted by one row against their labels,
  so TADL and cluster figures are summed from the plant blocks instead.
* "Total" rows are the sheet's own formulas and are used as they are, except where a month was
  filled from the entry workbook (then the total is the sum of the rows) or nothing was entered
  (the sheet shows 0; we return null).
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fastapi import APIRouter, Query

from .. import workbooks
from ..config import find_source_file
from ..plants import PLANT_IDS, PLANTS, canonical_plant, plant_name
from ..workbooks import to_number

router = APIRouter(prefix="/api/water", tags=["water"])

CLUSTERS = {
    "DP": ["TASL-NGP", "TASL-BLR", "TSAL", "TCOE"],
    "ASSY": ["TLMAL", "TASL-HYD", "TBAL", "TASL-MCA"],
}
UNIT = "KL"
TADL = "TADL"
ENTITIES = [TADL, "DP", "ASSY", *PLANT_IDS]

MASTER_SHEET = "Water consumption data"
_FY_LABEL = re.compile(r"^\s*FY\s*(\d{2})\s*-\s*(\d{2})\s*$", re.I)
_SECTIONS = (
    ("fresh", "fresh"), ("domestic", "domestic"), ("treated sewage", "sewage"),
    ("industrial", "industrial"), ("incoming process", "process_in"),
    ("treated process", "process_out"), ("total reuse", "reuse_pct"),
)

# Flat metric names kept per plant per month.
LEAF_METRICS = [
    "municipal", "borewell", "tanker", "drinking", "other_fresh",
    "sewage_garden", "sewage_toilets", "sewage_other",
    "process_etp", "process_zld", "process_ro", "process_dm", "process_other",
    "rwh_garden",
]
FRESH_METRICS = ("municipal", "borewell", "tanker", "drinking", "other_fresh")
TOTAL_METRICS = ["fresh_total", "domestic_total", "industrial_total", "sewage_total", "process_in_total", "process_out_total"]
ALL_METRICS = LEAF_METRICS + TOTAL_METRICS
_TOTAL_KEY = {"fresh": "fresh_total", "domestic": "domestic_total", "industrial": "industrial_total",
              "sewage": "sewage_total", "process_in": "process_in_total", "process_out": "process_out_total"}


# --------------------------------------------------------------------------- periods

def fy_name(start_year: int) -> str:
    return f"FY{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def fy_of_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return fy_name(year if mon >= 4 else year - 1)


def normalize_fy(text: str | None) -> str | None:
    """"FY 25-26", "FY25-26", "2025-26" -> "FY25-26"; None when it is not a year range."""
    m = re.match(r"^\s*(?:fy)?\s*(\d{2,4})\s*[-–]\s*(\d{2,4})\s*$", str(text or ""), re.I)
    if not m:
        return None
    a = int(m.group(1))
    return fy_name(a if a > 99 else 2000 + a)


def _fy_end_label(text: Any) -> str | None:
    """Water Summary labels a year by the calendar year it ends in: "FY 25" is FY24-25
    (checked against the monthly sheet: FY24-25 fresh water 300162 KL = the summary's "FY 25")."""
    m = re.match(r"^\s*FY\s*(\d{2})\s*$", str(text or ""), re.I)
    return fy_name(2000 + int(m.group(1)) - 1) if m else None


def _month_sort(m: str) -> tuple[int, int]:
    return int(m[:4]), int(m[5:7])


# --------------------------------------------------------------------------- numbers

def _num(v: float | None, digits: int = 2) -> float | None:
    return None if v is None else round(v, digits)


def _sum(values: list[float | None]) -> float | None:
    """Sum of the numbers present; None when nothing was reported (never 0 for "no data")."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _sum_int(values: list[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _pct(part: float | None, whole: float | None) -> float | None:
    if part is None or whole is None or whole <= 0:
        return None
    return part / whole * 100.0


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


# --------------------------------------------------------------------------- master sheet parsing

def _section_of(name: Any) -> str | None:
    text = str(name or "").strip().lower()
    return next((key for prefix, key in _SECTIONS if text.startswith(prefix)), None)


def _leaf_metric(section: str, label: str) -> str | None:
    """Map a sheet row to one of LEAF_METRICS.  Unlisted rows (canteen, HVAC ...) stay in the detail."""
    low = label.lower()
    if section == "fresh":
        if low.startswith("municipal"):
            return "municipal"
        if low.startswith("borewell"):
            return "borewell"
        if low.startswith("tanker"):
            return "tanker"
        if low.startswith("water for drinking"):
            return "drinking"
        return "other_fresh" if low.startswith("other") else None
    if section == "sewage":
        if "garden" in low:
            return "sewage_garden"
        return "sewage_toilets" if "toilet" in low else "sewage_other"
    if section == "process_in":
        return "process_etp" if low.startswith("etp") else "process_zld" if low.startswith("zld") else None
    if section == "process_out":
        return "process_ro" if low == "ro" else "process_dm" if low == "dm" else "process_other"
    if section == "domestic" and "rwh" in low:
        return "rwh_garden"
    return None


def _fy_blocks(header: list[Any]) -> list[tuple[int, int]]:
    """(FY start year, column of its "FY xx-yy" label); the 12 columns before it are Apr..Mar."""
    blocks = []
    for j, cell in enumerate(header):
        m = _FY_LABEL.match(str(cell or ""))
        if m and j >= 12:
            blocks.append((2000 + int(m.group(1)), j))
    return blocks


def _month_key(start: int, k: int) -> str:
    return f"{start if k < 9 else start + 1}-{((k + 3) % 12) + 1:02d}"


def _parse_master(path: Path) -> OrderedDict[str, list[dict]]:
    """Rows of the master sheet grouped by plant id ("Overall TADL" keeps that label)."""
    grid = workbooks.read_sheet(path, MASTER_SHEET)
    header_rows = [i for i, r in enumerate(grid) if any(str(c or "").strip().lower().startswith("type of water") for c in r)]
    if not header_rows:
        return OrderedDict()
    h = header_rows[0]
    end = header_rows[1] if len(header_rows) > 1 else len(grid)  # a second header starts the KPI tables
    col: dict[str, int] = {}
    for j, cell in enumerate(grid[h]):
        text = str(cell or "").strip().lower()
        for key, prefix in (("plant", "plant"), ("input", "input"), ("type", "type of water"), ("fac", "facility")):
            if text.startswith(prefix) and key not in col:
                col[key] = j
    if len(col) < 4:
        return OrderedDict()
    blocks = _fy_blocks(grid[h])
    out: OrderedDict[str, list[dict]] = OrderedDict()
    plant = section = None
    for i in range(h + 1, end):
        row = grid[i]
        label_plant = _clean(row[col["plant"]])
        if label_plant:
            plant, section = label_plant, None
        found = _section_of(row[col["input"]])
        if found:
            section = found
        label = _clean(row[col["type"]])
        if not plant or section is None:
            continue
        if section == "reuse_pct" or label:
            vals = {_month_key(start, k): to_number(row[j - 12 + k]) for start, j in blocks for k in range(12)}
            pid = canonical_plant(plant) or plant
            out.setdefault(pid, []).append({
                "section": section, "label": label, "row": i + 1, "vals": vals,
                "facility": row[col["fac"]] if isinstance(row[col["fac"]], bool) else None,
            })
        if section == "reuse_pct":  # last row of a plant block; whatever follows is not part of it
            plant = section = None
    return out


def _row_index(rows: list[dict]) -> dict[tuple[str, str, int], dict]:
    """Rows keyed by (section, label, n-th occurrence) so the two workbooks can be matched."""
    seen: dict[tuple[str, str], int] = {}
    keyed = {}
    for r in rows:
        base = (r["section"], r["label"])
        keyed[(*base, seen.get(base, 0))] = r
        seen[base] = seen.get(base, 0) + 1
    return keyed


class _PlantSeries:
    """All monthly metrics of one plant: metric -> month -> value, plus the cells taken from the entry workbook."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, float | None]] = {m: {} for m in ALL_METRICS}
        self.sheet_reuse_pct: dict[str, float | None] = {}
        self.domestic_detail: dict[str, dict[str, float | None]] = {}
        self.industrial_detail: dict[str, dict[str, float | None]] = {}
        self.entry_filled: dict[str, set[str]] = {}  # month -> "section.label" rows filled from the entry workbook
        self.labels: dict[str, str] = {}  # metric -> sheet label (e.g. municipal -> "Municipality Water/MADC")
        self.facilities: list[str] = []
        self.total_mismatches: dict[str, int] = {}
        self._reported: list[str] | None = None

    @property
    def reported(self) -> list[str]:
        """Months with at least one fresh-water figure entered, oldest first."""
        if self._reported is None:
            months = {m for k in FRESH_METRICS for m, v in self.data[k].items() if v is not None}
            self._reported = sorted(months, key=_month_sort)
        return self._reported


def _build_series(calc_rows: list[dict], entry_rows: list[dict] | None) -> _PlantSeries:
    series = _PlantSeries()
    entry_index = _row_index(entry_rows) if entry_rows else {}
    leaves: dict[str, list[dict]] = {}
    filled: set[tuple[str, str]] = set()  # (section, month) with a leaf taken from the entry workbook
    sheet_totals: dict[str, dict[str, float | None]] = {}

    for key, row in _row_index(calc_rows).items():
        section, label = row["section"], row["label"]
        if section == "reuse_pct":
            series.sheet_reuse_pct = row["vals"]
            continue
        if label.lower() == "total":
            sheet_totals[section] = row["vals"]
            continue
        vals = dict(row["vals"])
        match = entry_index.get(key)
        if match is not None:
            for month, value in vals.items():
                fill = match["vals"].get(month)
                if value is None and fill is not None:
                    vals[month] = fill
                    series.entry_filled.setdefault(month, set()).add(f"{section}.{label}")
                    filled.add((section, month))
        leaves.setdefault(section, []).append({"label": label, "vals": vals})
        if row["facility"]:
            series.facilities.append(label)
        metric = _leaf_metric(section, label)
        if metric:
            series.labels.setdefault(metric, label)
            target = series.data[metric]
            for month, value in vals.items():
                target[month] = value if target.get(month) is None else _sum([target[month], value])
        if section == "domestic":
            series.domestic_detail[label] = vals
        elif section == "industrial":
            series.industrial_detail[label] = vals

    months = sorted({m for rows in leaves.values() for r in rows for m in r["vals"]}, key=_month_sort)
    for section, total_metric in _TOTAL_KEY.items():
        sheet = sheet_totals.get(section, {})
        mismatches = 0
        for month in months:
            component_sum = _sum([r["vals"].get(month) for r in leaves.get(section, [])])
            value = sheet.get(month)
            if component_sum is None:
                value = None  # the sheet's formula shows 0 for a month nobody has entered yet
            elif value is None or (section, month) in filled:
                value = component_sum
            elif abs(value - component_sum) > 0.01:
                mismatches += 1
            series.data[total_metric][month] = value
        series.total_mismatches[section] = mismatches
    return series


_lock = threading.Lock()
_cache: dict[tuple, dict[str, _PlantSeries]] = {}


def _series() -> dict[str, _PlantSeries]:
    """plant id -> series, with the entry workbook filling blanks.  Empty when the workbook is missing."""
    calc, entry = workbooks.calculation_workbook(), workbooks.entry_workbook()
    if calc is None:
        return {}
    key = (str(calc), calc.stat().st_mtime, str(entry), entry.stat().st_mtime if entry else 0)
    with _lock:
        if key in _cache:
            return _cache[key]
        try:
            calc_rows = _parse_master(calc)
        except KeyError:
            return {}
        try:
            entry_rows = _parse_master(entry) if entry else OrderedDict()
        except KeyError:
            entry_rows = OrderedDict()
        built = {p: _build_series(rows, entry_rows.get(p)) for p, rows in calc_rows.items() if p in PLANT_IDS}
        _cache.clear()
        _cache[key] = built
        return built


def sheet_overall_tadl() -> dict[str, dict[str, float | None]]:
    """The workbook's own "Overall TADL" block as {section.label: {month: value}}, for cross-checks only."""
    calc = workbooks.calculation_workbook()
    if calc is None:
        return {}
    rows = _parse_master(calc).get("Overall TADL", [])
    return {f"{r['section']}.{r['label']}": r["vals"] for r in rows}


# --------------------------------------------------------------------------- aggregation

def _members(entity: str) -> list[str]:
    if entity == TADL:
        return list(PLANT_IDS)
    return list(CLUSTERS.get(entity, [entity] if entity in PLANT_IDS else []))


def _reported_set(plant: str) -> set[str]:
    s = _series().get(plant)
    return set(s.reported) if s else set()


def _entity_months(members: list[str]) -> list[str]:
    return sorted({m for p in members for m in _reported_set(p)}, key=_month_sort)


def _month_values(members: list[str], month: str) -> tuple[dict[str, float | None], list[str]]:
    """Metrics of a month summed over `members`; also the members that reported it."""
    all_series = _series()
    reporting = [p for p in members if month in _reported_set(p)]
    return {m: _sum([all_series[p].data[m].get(month) for p in reporting]) for m in ALL_METRICS}, reporting


def _shape(values: dict[str, float | None]) -> dict[str, Any]:
    """Nested payload fields from flat metrics (KL), plus the derived reuse figures."""
    def g(k: str) -> float | None:
        return _num(values.get(k))

    reused = _sum([values.get("sewage_total"), values.get("process_out_total")])
    return {
        "fresh": {"municipal": g("municipal"), "borewell": g("borewell"), "tanker": g("tanker"),
                  "drinking": g("drinking"), "other": g("other_fresh"), "total": g("fresh_total")},
        "domestic": g("domestic_total"),
        "industrial": g("industrial_total"),
        "sewageReused": {"garden": g("sewage_garden"), "toilets": g("sewage_toilets"),
                         "other": g("sewage_other"), "total": g("sewage_total")},
        "processIncoming": {"etp": g("process_etp"), "zld": g("process_zld"), "total": g("process_in_total")},
        "processReused": {"ro": g("process_ro"), "dm": g("process_dm"), "other": g("process_other"),
                          "total": g("process_out_total")},
        "totalReused": _num(reused),
        # Same formula as the workbook's "Total Reuse water %age" row: (STP + treated process reuse) / fresh water.
        "reusePct": _num(_pct(reused, values.get("fresh_total")), 1),
        "rwhGarden": g("rwh_garden"),
    }


def entity_monthly(entity: str, detail: bool = False) -> list[dict]:
    """Monthly records for a plant id, "DP", "ASSY" or "TADL", oldest first."""
    members = _members(entity)
    all_series = _series()
    records = []
    for month in _entity_months(members):
        values, reporting = _month_values(members, month)
        rec: dict[str, Any] = {"month": month, "fy": fy_of_month(month), **_shape(values)}
        if entity in PLANT_IDS:
            sheet = all_series[entity].sheet_reuse_pct.get(month)
            rec["reusePctSheet"] = _num(None if sheet is None else sheet * 100, 1)
        else:
            rec["plantsReporting"] = len(reporting)
        filled = sorted({f for p in reporting for f in all_series[p].entry_filled.get(month, ())})
        rec["source"] = "entry" if filled else "calc"
        if filled:
            rec["filledFromEntry"] = filled
        if detail and entity in PLANT_IDS:
            s = all_series[entity]
            rec["domesticDetail"] = {k: _num(v.get(month)) for k, v in s.domestic_detail.items()}
            rec["industrialDetail"] = {k: _num(v.get(month)) for k, v in s.industrial_detail.items()}
        records.append(rec)
    return records


def _period_values(members: list[str], months: list[str]) -> tuple[dict[str, float | None], int, bool]:
    """Metrics summed over months and members; months with data; whether every member covered every month."""
    totals: dict[str, list[float | None]] = {m: [] for m in ALL_METRICS}
    complete, reported = True, 0
    for month in months:
        values, reporting = _month_values(members, month)
        reported += bool(reporting)
        complete = complete and len(reporting) == len(members)
        for m in ALL_METRICS:
            totals[m].append(values[m])
    return {m: _sum(v) for m, v in totals.items()}, reported, complete


def entity_yearly(entity: str) -> list[dict]:
    """Financial-year records, oldest first.  `complete` is true only when every plant reported all 12 months."""
    members = _members(entity)
    by_fy: dict[str, list[str]] = {}
    for month in _entity_months(members):
        by_fy.setdefault(fy_of_month(month), []).append(month)
    out = []
    for fy, months in by_fy.items():
        values, reported, complete = _period_values(members, months)
        out.append({"fy": fy, "monthsReported": reported, "complete": complete and reported == 12, **_shape(values)})
    return out


# --------------------------------------------------------------------------- entity helpers and notes

def resolve_entity(name: str | None) -> tuple[str | None, str | None]:
    """(entity id, error text) for "TADL", "DP", "ASSY" or any plant spelling."""
    text = (name or TADL).strip()
    if text.upper() in (TADL, "DP", "ASSY"):
        return text.upper(), None
    pid = canonical_plant(text)
    return (pid, None) if pid else (None, f"Unknown plant {text!r}; use TADL, DP, ASSY or one of {', '.join(PLANT_IDS)}.")


def _entity_label(entity: str) -> str:
    return {"TADL": "TADL (all plants)", "DP": "DP cluster", "ASSY": "ASSY cluster"}.get(entity) or plant_name(entity)


def _base_notes() -> list[str]:
    if workbooks.calculation_workbook() is None:
        return ["The 'Sustainability Calculation' workbook was not found, so no water figures are available."]
    if not _series():
        return [f"Sheet {MASTER_SHEET!r} was not found in the calculation workbook."]
    return []


def _gap_notes(entity: str) -> list[str]:
    """Plain-language data caveats that apply to this entity's figures."""
    notes: list[str] = []
    all_series = _series()
    for p in _members(entity):
        s = all_series.get(p)
        if s is None:
            notes.append(f"{plant_name(p)}: no block in the water sheet.")
            continue
        if s.entry_filled:
            notes.append(f"{plant_name(p)}: {len(s.entry_filled)} month(s) have cells filled from the 'Only to Enter' workbook (source = entry).")
        bad = sum(s.total_mismatches.values())
        if bad:
            notes.append(f"{plant_name(p)}: in {bad} section-month(s) the sheet's own Total row differs from the sum of its rows; the sheet's Total is shown.")
    if entity in (TADL, "DP", "ASSY"):
        notes.append("Cluster and TADL figures are sums of the plant blocks; a plant that has not reported a month is left out of that month (see plantsReporting).")
    return notes


# --------------------------------------------------------------------------- reuse and the workbook's summary sheets

def _summary_sheet() -> dict[str, dict[str, dict[str, float | None]]]:
    """"Water Summary": entity -> row label -> FY -> value (the sheet's own yearly table, FY18-19 .. FY24-25)."""
    path = workbooks.calculation_workbook()
    name = workbooks.find_sheet(path, "water", "summary") if path else None
    if path is None or name is None:
        return {}
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    entity: str | None = None
    fys: dict[int, str] = {}
    for row in workbooks.read_sheet(path, name):
        label = _clean(row[1]) if len(row) > 1 else ""
        if not label:
            continue
        if label.lower() == "description":
            fys = {j: fy for j, c in enumerate(row) if (fy := _fy_end_label(c))}
        elif not any(c not in (None, "") for c in row[2:]):
            entity = TADL if label.lower().startswith("tasl & its") else (canonical_plant(label) or label)
        elif entity and fys:
            out.setdefault(entity, {})[label] = {fy: to_number(row[j]) for j, fy in fys.items()}
    return out


def _gem_reuse() -> dict[str, float | None]:
    """"GEM Water Reuse Con Per% Trends": TADL reuse % by year; a year N is read as the FY starting in N."""
    path = workbooks.calculation_workbook()
    name = workbooks.find_sheet(path, "gem", "water reuse") if path else None
    if path is None or not name:
        return {}
    out: dict[str, float | None] = {}
    for row in workbooks.read_sheet(path, name):
        year, pct = to_number(row[0]), (to_number(row[1]) if len(row) > 1 else None)
        if year and 2000 < year < 2100:
            out[fy_name(int(year))] = None if pct is None else round(pct * 100, 2)
    return out


def reuse_payload(entity: str) -> dict:
    """Reuse figures for one entity: yearly, monthly, and what the workbook's own summary sheets say."""
    notes = _base_notes() + _gap_notes(entity)
    yearly = entity_yearly(entity)
    monthly = []
    for r in entity_monthly(entity):
        item = {"month": r["month"], "fy": r["fy"], "freshTotal": r["fresh"]["total"], "sewageReused": r["sewageReused"],
                "processReused": r["processReused"], "totalReused": r["totalReused"], "reusePct": r["reusePct"],
                "source": r["source"]}
        if "reusePctSheet" in r:
            item["reusePctSheet"] = r["reusePctSheet"]
        monthly.append(item)
    summary_rows: dict[str, dict[str, float | None]] = {}
    for label, by_fy in _summary_sheet().get(entity, {}).items():
        low = label.lower()
        if low.startswith(("total reuse", "total water reused", "treated", "incoming", "fresh water")):
            # The sheet stores the % row as a fraction; shown here as a percentage like reusePct.
            summary_rows[label] = {fy: (_num(v * 100, 1) if low.startswith("total reuse") and v is not None else _num(v))
                                   for fy, v in by_fy.items()}
    if entity in (*PLANT_IDS, TADL) and not summary_rows:
        notes.append("The 'Water Summary' sheet has no yearly block for this entity.")
    if entity == TADL:
        notes.append("gemReusePct (GEM sheet) and workbookSummary (Water Summary sheet) are the workbook's own yearly figures and use different aggregation from the monthly plant data; "
                     "they differ by a few percentage points from `yearly` and are shown unchanged.")
    return {
        "entity": entity, "name": _entity_label(entity), "unit": UNIT, "reusePctUnit": "%",
        "formula": "reusePct = (treated sewage reused + treated process water reused) / fresh water total x 100",
        "yearly": [{"fy": r["fy"], "monthsReported": r["monthsReported"], "complete": r["complete"],
                    "freshTotal": r["fresh"]["total"], "sewageReused": r["sewageReused"],
                    "processIncoming": r["processIncoming"], "processReused": r["processReused"],
                    "totalReused": r["totalReused"], "reusePct": r["reusePct"]} for r in yearly],
        "monthly": monthly,
        "workbookSummary": summary_rows,
        "gemReusePct": _gem_reuse() if entity == TADL else None,
        "notes": notes,
    }


def percapita_payload() -> dict:
    """"Percapita water consumption": despite the name it holds yearly reuse and fresh-water totals for
    two plants; no per-capita figure is stored in the sheet."""
    notes = _base_notes()
    path = workbooks.calculation_workbook()
    name = workbooks.find_sheet(path, "percapita") if path else None
    plants: dict[str, dict[str, dict[str, float | None]]] = {}
    if path is None or not name:
        notes.append("Sheet 'Percapita water consumption' not found.")
        return {"unit": UNIT, "plants": plants, "notes": notes}
    grid = workbooks.read_sheet(path, name)
    years: dict[int, str] = {}
    for row in grid:
        heads = {j: _clean(c) for j, c in enumerate(row) if "percapita" in _clean(c).lower()}
        if heads:
            for j, text in heads.items():
                m = re.search(r"FY\s*-?\s*(\d{2})", text, re.I)
                if m:  # "FY 22" = FY21-22, the same convention as the Water Summary sheet
                    years[j] = fy_name(2000 + int(m.group(1)) - 1)
            break
    plant = None
    for row in grid:
        if _clean(row[0]):
            plant = canonical_plant(row[0]) or _clean(row[0])
        label = _clean(row[1])
        if plant and label and years and any(to_number(row[j]) is not None for j in years):
            plants.setdefault(plant, {})[label] = {fy: _num(to_number(row[j]), 4) for j, fy in years.items()}
    notes.append("Only plants with values are listed (the sheet is blank for the others). Despite its name the sheet holds reuse volumes and fresh water in KL "
                 "(and the reuse % as a fraction), exactly as stored; no per-capita figure exists in it.")
    return {"unit": UNIT, "plants": plants, "notes": notes}


# --------------------------------------------------------------------------- ground water, rainwater

def groundwater_payload() -> dict:
    """"Ground Water": yearly average borewell water level (feet) per plant, state rainfall and the ratio."""
    notes = _base_notes()
    result: dict[str, Any] = {"levelUnit": "feet", "rainfallUnit": "mm", "plants": [], "rainfallMm": {}, "tadl": {}, "notes": notes}
    path = workbooks.calculation_workbook()
    name = workbooks.find_sheet(path, "ground water") if path else None
    if path is None or not name:
        notes.append("Sheet 'Ground Water' not found.")
        return result
    grid = workbooks.read_sheet(path, name)
    head_i = next((i for i, r in enumerate(grid) if _clean(r[1]).lower() == "plant"), None)
    if head_i is None:
        notes.append("Header row of 'Ground Water' not found.")
        return result
    level_cols: dict[int, str] = {}
    ratio_cols: dict[int, str] = {}
    remark_col = None
    for j, cell in enumerate(grid[head_i]):
        text = _clean(cell)
        fy = normalize_fy(text)
        if fy and j > 1:  # the FY labels appear twice: first the levels, then the level/rainfall ratios
            (ratio_cols if fy in level_cols.values() else level_cols)[j] = fy
        if text.lower().startswith("remark"):
            remark_col = j
    for row in grid[head_i + 1:]:
        label = _clean(row[1])
        low = label.lower()
        pid = canonical_plant(label)
        if pid:
            result["plants"].append({
                "plant": pid,
                "level": {fy: _num(to_number(row[j])) for j, fy in level_cols.items()},
                "levelToRainfallRatio": {fy: _num(to_number(row[j]), 4) for j, fy in ratio_cols.items()},
                "remark": (_clean(row[remark_col]) or None) if remark_col is not None else None,
            })
        elif low.startswith("tadl avg"):
            result["tadl"]["level"] = {fy: _num(to_number(row[j])) for j, fy in level_cols.items() if to_number(row[j]) is not None}
        elif low.startswith("avg rainfal"):
            result["rainfallMm"] = {fy: _num(to_number(row[j]), 0) for j, fy in level_cols.items() if to_number(row[j]) is not None}
        elif low.startswith("ratio"):
            result["tadl"]["levelToRainfallRatio"] = {fy: _num(to_number(row[j]), 4) for j, fy in level_cols.items() if to_number(row[j]) is not None}
    notes.append("Levels are in feet as the sheet states. 'NA' means not applicable: TASL-BLR, TCOE and TASL-NGP have no borewell inside the plant. "
                 "Values exist for FY21-22 to FY23-24 only; the earlier years are blank in the sheet.")
    return result


def _rwh_pits(path: Path | None) -> list[dict]:
    name = workbooks.find_sheet(path, "rainwater harvesting") if path else None
    if path is None or not name:
        return []
    grid = workbooks.read_sheet(path, name)
    head_i = next((i for i, r in enumerate(grid) if any(_clean(c).lower().startswith("no. of rainwater pits") for c in r)), None)
    if head_i is None:
        return []
    cols = {j: _clean(c).lower() for j, c in enumerate(grid[head_i]) if _clean(c)}

    def find(prefix: str, nth: int = 0) -> int | None:
        hits = [j for j, t in cols.items() if t.startswith(prefix)]
        return hits[nth] if len(hits) > nth else None

    spec = {"pits": find("no. of rainwater pits"), "dimensions": find("dimensions"),
            "lastMaintenance": find("last maintenance"), "latestMaintenanceDate": find("latest maintenance"),
            "conditionJun2024": find("current condition (jun"), "conditionDec2024": find("current condition as per"),
            "yieldWaterTableHeight": find("yield"), "pitsConditionOk": find("rain water harvesting pits con"),
            "additionalPitsProposed": find("proposal for additional", 0), "additionalPitsProposed2": find("proposal for additional", 1),
            "flowMeterMeasurement": find("flow meter"), "remarks": find("remarks")}
    plant_col = find("plant")
    out = []
    for row in grid[head_i + 1:]:
        pid = canonical_plant(row[plant_col]) if plant_col is not None else None
        if not pid:
            continue
        item: dict[str, Any] = {"plant": pid}
        for key, j in spec.items():
            v = row[j] if j is not None else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                item[key] = int(v) if float(v).is_integer() else v
            else:
                item[key] = v.date().isoformat() if isinstance(v, dt.datetime) else (_clean(v) or None)
        out.append(item)
    return out


def _rwh_estimates(entry: Path | None) -> dict[str, dict[str, Any]]:
    """"Only to Enter" > "RWH": rain-based estimate of water reaching the pits, per plant and FY, as stored."""
    name = workbooks.find_sheet(entry, "rwh") if entry else None
    if entry is None or not name:
        return {}
    grid = workbooks.read_sheet(entry, name)
    head_i = next((i for i, r in enumerate(grid) if any(_clean(c).lower() == "area description" for c in r)), None)
    if head_i is None:
        return {}
    # The sheet continues to the right with power-consumption blocks for FY18-19 .. FY20-21; only the
    # rain-collection blocks (FY21-22 onward) belong here.  The titles in row 2 are shifted against the
    # columns, so a block is identified by the FY label in the header row.
    blocks = [(start, j) for start, j in _fy_blocks(grid[head_i]) if start >= 2021]
    out: dict[str, dict[str, Any]] = {}
    plant = None
    for row in grid[head_i + 1:]:
        if _clean(row[1]):
            plant = canonical_plant(row[1]) or _clean(row[1])
        area = _clean(row[2]).lower()
        target = TADL if area.startswith("total tadl") else plant if area.startswith("total - water reaching to rwh") else None
        if not target:
            continue
        per_fy = {}
        for start, j in blocks:
            months = {_month_key(start, k): to_number(row[j - 12 + k]) for k in range(12) if to_number(row[j - 12 + k]) is not None}
            if months:
                per_fy[fy_name(start)] = {"months": months, "sheetTotal": to_number(row[j])}
        if per_fy:
            out[target] = per_fy
    return out


def rwh_payload() -> dict:
    """Rainwater harvesting: pit inventory, rainwater used for gardens (water sheet), rain-based estimates."""
    notes = _base_notes()
    calc, entry = workbooks.calculation_workbook(), workbooks.entry_workbook()
    pits = _rwh_pits(calc) or _rwh_pits(entry)
    if not pits:
        notes.append("Sheet 'Rainwater harvesting1' (pit inventory) not found.")
    garden: dict[str, Any] = {}
    for plant, s in _series().items():
        monthly = {m: v for m, v in s.data["rwh_garden"].items() if v is not None}
        if monthly:
            by_fy: dict[str, float] = {}
            for m, v in monthly.items():
                by_fy[fy_of_month(m)] = by_fy.get(fy_of_month(m), 0.0) + v
            garden[plant] = {"label": s.labels.get("rwh_garden"),
                             "monthly": {m: _num(v) for m, v in sorted(monthly.items(), key=lambda kv: _month_sort(kv[0]))},
                             "yearly": {fy: _num(v) for fy, v in sorted(by_fy.items())}}
    if not garden:
        notes.append("No 'RWH for Garden' rows with values in the water sheet.")
    estimates = _rwh_estimates(entry)
    if not estimates:
        notes.append("Sheet 'RWH' (estimated collection) not found in the 'Only to Enter' workbook.")
    notes.append("rainwaterUsedForGarden is entered use (KL) and exists only for plants that have an 'RWH for Garden' row. "
                 "estimatedCollection is the sheet's rain-based estimate (rainfall x area x runoff coefficient). Its header says Liters, but the FY26-27 columns are orders of magnitude "
                 "different from FY25-26 and earlier, so the values are returned exactly as stored and should not be compared across years without checking the source.")
    return {"pits": pits, "rainwaterUsedForGarden": {"unit": UNIT, "plants": garden},
            "estimatedCollection": {"unit": "as labelled in the sheet (Liters); magnitudes inconsistent between years", "plants": estimates},
            "notes": notes}


# --------------------------------------------------------------------------- flow meters

def _cell_comments(path: Path) -> dict[str, str]:
    """Cell comments (e.g. "Kindly provide ECD") keyed by cell reference; empty if the file has none."""
    out: dict[str, str] = {}
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.startswith("xl/comments") and name.endswith(".xml"):
                    for c in ElementTree.fromstring(z.read(name)).iterfind(".//m:comment", ns):
                        text = "".join(t.text or "" for t in c.iterfind(".//m:t", ns))
                        # Google Sheets prefixes "====== / ID# ..."; keep the author line and the message.
                        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith(("=", "ID#"))]
                        out[c.get("ref", "")] = " - ".join(lines)
    except (zipfile.BadZipFile, OSError, ElementTree.ParseError):
        return {}
    return out


def _col_letter(index: int) -> str:
    letters, n = "", index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _meter_types(text: str, available: float | None) -> tuple[dict[str, int], int | None]:
    """"Analog-12&&Digital-2" -> ({"analog": 12, "digital": 2}, remainder); remainder = available minus named."""
    counts: dict[str, int] = {}
    for kind, n in re.findall(r"(analog|digital)\s*-?\s*(\d+)", text, re.I):
        counts[kind.lower()] = counts.get(kind.lower(), 0) + int(n)
    if available is None or not counts:
        return counts, None
    return counts, max(int(available) - sum(counts.values()), 0)


def _install_status(text: str) -> str:
    low = text.strip().lower()
    if low.startswith("installed"):
        return "installed"
    if "yet to" in low or "pending" in low or "to be" in low:
        return "to_be_installed"
    return "not_applicable" if low in ("na", "n/a") else "unknown"


def _lakh(text: str) -> float | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*lac", text, re.I)
    return float(m.group(1)) if m else None


def _count_by(items: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    return counts


def _empty_meters(notes: list[str]) -> dict:
    return {"unit": "meters", "plants": [], "statuses": {}, "notes": notes,
            "totals": {"available": None, "toBeInstalled": None, "byType": {}, "plantsWithoutCounts": [], "installationStatus": {}}}


def meters_payload() -> dict:
    """Flow-meter status per plant, shaped plant -> meter groups, plus counts by status per plant and overall."""
    notes: list[str] = []
    path = find_source_file("water", "flow meter")
    if path is None:
        notes.append("'Water Flow Meter Status.xlsx' not found in the water folder.")
        return _empty_meters(notes)
    names = workbooks.sheet_names(path)
    sheet = workbooks.find_sheet(path, "flow meter") or (names[0] if names else None)
    if sheet is None:
        notes.append("The flow-meter workbook has no sheets.")
        return _empty_meters(notes)
    grid = workbooks.read_sheet(path, sheet)
    head_i = next((i for i, r in enumerate(grid) if any("no. of water meters available" in _clean(c).lower() for c in r)), None)
    if head_i is None:
        notes.append("Header row ('No. of Water Meters Available') not found in the flow-meter sheet.")
        return _empty_meters(notes)
    cols = {_clean(c).lower(): j for j, c in enumerate(grid[head_i]) if _clean(c)}

    def idx(prefix: str) -> int | None:
        return next((j for t, j in cols.items() if t.startswith(prefix)), None)

    spec = {"plant": idx("plants"), "diagram": idx("water circuit"), "available": idx("no. of water meters available"),
            "type": idx("type of instrument"), "calibration": idx("calibration status"), "calibrationDate": idx("calibration date"),
            "toInstall": idx("no. of water meters to be"), "budgetProposed": idx("budget proposed"),
            "budgetAllocated": idx("budget allocated"), "installStatus": idx("installation status"),
            "ecd": idx("ecd"), "remarks": idx("remarks")}
    if spec["plant"] is None:
        notes.append("'Plants' column not found in the flow-meter sheet.")
        return _empty_meters(notes)
    comments = _cell_comments(path)
    clusters = {p["id"]: p["cluster"] for p in PLANTS}
    plants = []
    for i in range(head_i + 1, len(grid)):
        row = grid[i]
        pid = canonical_plant(row[spec["plant"]])
        if not pid:
            continue

        def get(key: str) -> Any:
            return row[spec[key]] if spec[key] is not None else None

        def as_text(value: Any) -> str | None:
            return value.date().isoformat() if isinstance(value, dt.datetime) else (_clean(value) or None)

        available, to_install = to_number(get("available")), to_number(get("toInstall"))
        type_text = _clean(get("type"))
        by_type, unspecified = _meter_types(type_text, available)
        status_text = _clean(get("installStatus"))
        ecd_cell = f"{_col_letter(spec['ecd'])}{i + 1}" if spec["ecd"] is not None else ""
        groups = [{"status": "available", "type": kind, "count": n} for kind, n in by_type.items()]
        if unspecified:
            groups.append({"status": "available", "type": "unspecified", "count": unspecified})
        elif available and not by_type:
            groups.append({"status": "available", "type": "unspecified", "count": int(available)})
        if to_install:
            groups.append({"status": "to_be_installed", "type": "unspecified", "count": int(to_install)})
        plants.append({
            "plant": pid, "name": plant_name(pid), "cluster": clusters[pid],
            "circuitDiagramShared": as_text(get("diagram")),
            "available": None if available is None else int(available),
            "toBeInstalled": None if to_install is None else int(to_install),
            "instrumentType": type_text or None, "byType": by_type, "unspecifiedType": unspecified,
            "calibrated": as_text(get("calibration")), "calibrationDate": as_text(get("calibrationDate")),
            "installationStatus": status_text or None, "installationStatusNormalised": _install_status(status_text),
            "expectedCompletion": as_text(get("ecd")), "expectedCompletionComment": comments.get(ecd_cell),
            "budgetProposed": as_text(get("budgetProposed")), "budgetProposedLakh": _lakh(_clean(get("budgetProposed"))),
            "budgetAllocated": as_text(get("budgetAllocated")), "budgetAllocatedLakh": _lakh(_clean(get("budgetAllocated"))),
            "remarks": as_text(get("remarks")),
            "statusCounts": {"available": None if available is None else int(available),
                             "toBeInstalled": None if to_install is None else int(to_install),
                             "working": None, "notWorking": None},
            "areas": [], "meterGroups": groups,
        })
    have = [p for p in plants if p["available"] is not None]
    by_type_total: dict[str, int] = {}
    for p in plants:
        for kind, n in p["byType"].items():
            by_type_total[kind] = by_type_total.get(kind, 0) + n
    unspecified_total = sum(p["unspecifiedType"] or 0 for p in plants)
    if unspecified_total:
        by_type_total["unspecified"] = unspecified_total
    without = [p["plant"] for p in plants if p["available"] is None]
    totals = {"available": sum(p["available"] for p in have) if have else None,
              "toBeInstalled": _sum_int([p["toBeInstalled"] for p in plants]),
              "byType": by_type_total, "plantsWithoutCounts": without,
              "installationStatus": _count_by([p["installationStatusNormalised"] for p in plants])}
    notes += [
        "The file is plant-level only: it gives how many meters each plant has and their type, not each meter's location, line or working state. "
        "`areas` is empty and `meterGroups` holds counts by status and type; no per-meter layout can be drawn from this source.",
        "Statuses in the file: 'available' (existing meters), 'to be installed', and a per-plant installation status (Installed / Yet to install / NA). "
        "'Working' and 'not working' are not recorded, so those counts are null.",
        "Types come from text such as 'Analog-12&&Digital-2'; where the text names fewer meters than 'No. of Water Meters Available' the remainder is 'unspecified'.",
    ]
    if without:
        notes.append(f"No meter counts in the sheet for: {', '.join(without)}.")
    absent = [pid for pid in PLANT_IDS if pid not in {p["plant"] for p in plants}]
    if absent:
        notes.append(f"Plants missing from the sheet: {', '.join(absent)}.")
    return {"unit": "meters", "plants": plants, "totals": totals,
            "statuses": {"available": "meters existing at the plant", "to_be_installed": "meters still to be installed"},
            "notes": notes}


# --------------------------------------------------------------------------- overview

def _headline(entity: str, month: str | None) -> dict | None:
    """Latest-month and FY-to-date figures of an entity for the overview."""
    if month is None:
        return None
    fy = fy_of_month(month)
    members = _members(entity)
    latest, reporting = _month_values(members, month)
    ytd_months = [m for m in _entity_months(members) if fy_of_month(m) == fy and _month_sort(m) <= _month_sort(month)]
    ytd, reported, _ = _period_values(members, ytd_months)

    def pick(values: dict[str, float | None]) -> dict:
        s = _shape(values)
        return {"freshWater": s["fresh"]["total"], "municipal": s["fresh"]["municipal"], "borewell": s["fresh"]["borewell"],
                "tanker": s["fresh"]["tanker"], "treatedSewageReused": s["sewageReused"]["total"],
                "treatedProcessReused": s["processReused"]["total"], "totalReused": s["totalReused"], "reusePct": s["reusePct"]}

    return {"entity": entity, "name": _entity_label(entity), "latestMonth": month, "fy": fy,
            "plantsReporting": len(reporting), "latest": pick(latest),
            "fyToDate": {"monthsReported": reported, **pick(ytd)}}


def overview_payload() -> dict:
    notes = _base_notes()
    plants = []
    for p in PLANT_IDS:
        months = sorted(_reported_set(p), key=_month_sort)
        head = _headline(p, months[-1] if months else None)
        if head is None:
            notes.append(f"{plant_name(p)}: no reported water months.")
            head = {"entity": p, "name": plant_name(p), "latestMonth": None, "latest": None, "fyToDate": None}
        plants.append(head)
    all_months = _entity_months(PLANT_IDS)
    tadl_month = all_months[-1] if all_months else None
    tadl = _headline(TADL, tadl_month)
    if tadl and tadl["plantsReporting"] < len(PLANT_IDS):
        missing = [p for p in PLANT_IDS if tadl_month not in _reported_set(p)]
        notes.append(f"TADL latest month {tadl_month} is reported by {tadl['plantsReporting']} of {len(PLANT_IDS)} plants "
                     f"(not reported: {', '.join(missing)}); its latest-month figures cover only those plants.")
    clusters = [h for c in CLUSTERS if (h := _headline(c, tadl_month))]
    meters = meters_payload()["totals"]
    if tadl_month:
        notes.append("FY-to-date adds only the months each entity has reported; compare entities using monthsReported.")
    return {"unit": UNIT, "reusePctUnit": "%", "tadl": tadl, "clusters": clusters, "plants": plants,
            "meters": {"available": meters["available"], "toBeInstalled": meters["toBeInstalled"]}, "notes": notes}


# --------------------------------------------------------------------------- routes

@router.get("/overview")
def overview() -> dict:
    """Latest reported month and FY-to-date headline water figures for TADL, clusters and each plant."""
    return overview_payload()


@router.get("/monthly")
def monthly(plant: str = Query("TADL", description="TADL, DP, ASSY or a plant id/spelling"),
            fy: str | None = Query(None, description='Only this financial year, e.g. "FY25-26"'),
            detail: bool = Query(False, description="Add domestic/industrial use breakdown (plants only)")) -> dict:
    entity, error = resolve_entity(plant)
    if entity is None:
        return {"entity": None, "unit": UNIT, "months": [], "notes": [error]}
    notes = _base_notes()
    wanted = normalize_fy(fy) if fy else None
    if fy and wanted is None:
        notes.append(f"Could not read fy={fy!r}; use e.g. FY25-26. Showing all years.")
    rows = [r for r in entity_monthly(entity, detail) if wanted is None or r["fy"] == wanted]
    if not rows:
        notes.append("No reported months for this selection.")
    return {"entity": entity, "name": _entity_label(entity), "unit": UNIT, "reusePctUnit": "%", "months": rows,
            "notes": notes + _gap_notes(entity)}


@router.get("/yearly")
def yearly(plant: str | None = Query(None, description="One entity; omit for TADL, both clusters and all plants")) -> dict:
    notes = _base_notes()
    entities = ENTITIES
    if plant:
        entity, error = resolve_entity(plant)
        if entity is None:
            notes.append(error or "")
        entities = [entity] if entity else []
    notes.append("A year is complete only when every plant in the entity reported all 12 months; an incomplete year is a partial sum, not a full-year total.")
    return {"unit": UNIT, "reusePctUnit": "%",
            "entities": [{"entity": e, "name": _entity_label(e), "years": entity_yearly(e)} for e in entities], "notes": notes}


@router.get("/reuse")
def reuse(plant: str = Query("TADL")) -> dict:
    entity, error = resolve_entity(plant)
    if entity is None:
        return {"entity": None, "unit": UNIT, "yearly": [], "monthly": [], "notes": [error]}
    return reuse_payload(entity)


@router.get("/rwh")
def rwh() -> dict:
    return rwh_payload()


@router.get("/groundwater")
def groundwater() -> dict:
    return groundwater_payload()


@router.get("/percapita")
def percapita() -> dict:
    return percapita_payload()


@router.get("/meters")
def meters() -> dict:
    return meters_payload()
