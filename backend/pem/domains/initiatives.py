"""Energy / water saving initiatives: what is already running and what is still to do.

Sources (all read-only):
  * initiatives/"Energy Saving Initiatives Data.xlsx"          - existing initiatives, one row each,
                                                                  with a status block per plant
  * initiatives/"FY 2026-27 Energy Saving Initiatives Data.xlsx" - the same 21 initiatives re-issued
                                                                  as the FY 2026-27 plan (more columns)
  * "Sustainability Calculation" workbook, sheets "Energy(Power|Fuel|Gas) Initiatives" and
    "Water conservation Initiatives": per plant, one row per initiative with monthly and yearly
    savings. The hidden copies in the "Only to Enter" workbook are used only to fill a blank cell
    (they turned out to be less complete, see docs/api/initiatives.md).

Nothing here invents a value: a blank in the source is None, and every record keeps the original
status wording next to the normalised one.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from openpyxl import load_workbook

from ..config import find_source_file, settings
from ..plants import PLANT_IDS, canonical_plant
from ..workbooks import calculation_workbook, entry_workbook, read_sheet, sheet_names, to_number

router = APIRouter(prefix="/api/initiatives", tags=["initiatives"])

STATUSES = [
    "implemented", "in_progress", "open", "under_study",
    "horizontally_deployed", "not_feasible", "not_applicable", "on_hold",
]
# Statuses that mean work has been done or is under way at a plant.
PRESENT_STATUSES = {"implemented", "in_progress", "horizontally_deployed"}
DONE_STATUSES = {"implemented", "horizontally_deployed"}

# Source wording (lower-case, trimmed) -> fixed set. "Closed" appears only in the FY 2026-27 file's
# "Initiated plant Status" column and is read as finished work.
_STATUS_MAP = {
    "implemented": "implemented", "closed": "implemented",
    "wip": "in_progress", "in progress": "in_progress",
    "open": "open",
    "under faesible study": "under_study", "under feasible study": "under_study",
    "under feasible": "under_study", "under study": "under_study",
    "horizontally deployed": "horizontally_deployed",
    "not feasible": "not_feasible",
    "na": "not_applicable", "n/a": "not_applicable", "not applicable": "not_applicable",
    "on hold": "on_hold",
}

CALC_SHEETS = {
    "power": ("Energy(Power) Initiatives", "power", "kWh"),
    "fuel": ("Energy(Fuel) Initiatives", "fuel", None),
    "gas": ("Energy(Gas) Initiatives", "gas", None),
    "water": ("Water conservation Initiatives", "water", None),
}
ALL_PLANTS_LABELS = {"allplants"}

_lock = threading.Lock()
_cache: dict[tuple, dict] = {}


# --------------------------------------------------------------------------- small helpers

def normalise_status(text: Any) -> str | None:
    """The fixed status for a source wording, or None for blanks and unknown wording."""
    if text is None:
        return None
    return _STATUS_MAP.get(re.sub(r"\s+", " ", str(text)).strip().lower())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def _iso_date(value: Any) -> str | None:
    """"25.01.2025" and "16/12/2024" (day first) or a real date -> "YYYY-MM-DD"."""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.date().isoformat() if isinstance(value, dt.datetime) else value.isoformat()
    s = _text(value)
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        try:
            return dt.date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        except ValueError:
            return None
    return None


def _priority(value: Any) -> int | None:
    n = to_number(value)
    return int(n) if n is not None else None


_DOMAIN_RULES = [
    ("lighting", r"\b(led|lights?|lighting|lamps?|lux|fluorescent|cfl|metal hal+ide|tube ?lights?|high ?bay|low ?bay)\b"),
    ("compressed_air", r"compressor|compressed air|pneumatic|air leak|air dryer"),
    ("hvac", r"hvac|\bahu|chiller|\bvrf\b|\bac\b|air condition|cooling tower|cassette|hvls|cleanroom|clean room|"
             r"fresh air|autoclave cooling|cooling pad|precooling|pre cooling|sub.?cooling|thawing|blower|pulley"),
    ("water", r"water|tap|irrigation|stp\b|etp\b|dish ?wash|rwh|rain"),
    ("renewable", r"solar"),
    ("drives_motors", r"vfd|\bvsd\b|motor|pump|drive"),
    ("heating_fuel", r"heater|burner|fuel|boiler|furnace|hot gas|\bdg\b"),
]
_XLSX_DOMAIN = {
    "lighting": "lighting", "hvac": "hvac", "water management": "water",
    "energy management": "energy_management",
}


def classify_domain(description: str | None) -> str | None:
    """Keyword-based domain for rows whose source has no equipment/area column."""
    low = (description or "").lower()
    for domain, pattern in _DOMAIN_RULES:
        if re.search(pattern, low):
            return domain
    return "other" if low else None


def _plant_ref(label: Any) -> dict:
    """A plant label from the source -> {"plant": id|None, "label": original text}."""
    return {"plant": canonical_plant(label), "label": _text(label)}


def _fy_label(g1: str, g2: str | None) -> str:
    """"Total FY 25" is April 2024 - March 2025 (see the month headers), so FY24-25."""
    if g2:
        return f"FY{g1}-{g2}"
    return f"FY{int(g1) - 1:02d}-{g1}"


def _group_key(*parts: Any) -> str:
    raw = "|".join(re.sub(r"[^a-z0-9]", "", str(p).lower()) for p in parts)
    return "g-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def _find_files() -> tuple[Path | None, Path | None]:
    """(existing-initiatives file, FY 2026-27 plan file); renamed copies still match."""
    plan = find_source_file("initiatives", "2026-27", "energy saving initiatives")
    folder = settings.source_dir / "initiatives"
    base = None
    if folder.is_dir():
        cands = [f for f in folder.iterdir()
                 if f.suffix.lower() == ".xlsx" and not f.name.startswith(("~$", ".~"))
                 and "energy saving initiatives" in f.name.lower() and f != plan]
        base = max(cands, key=lambda f: f.stat().st_mtime) if cands else None
    return base, plan


# --------------------------------------------------------------------------- the two xlsx files

def _grid(path: Path) -> tuple[list[list[Any]], str]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        sheet = wb.worksheets[0].title
    finally:
        wb.close()
    width = max((len(r) for r in rows), default=0)
    for r in rows:
        r.extend([None] * (width - len(r)))
    return rows, sheet


def _hdr(header: list[Any], j: int) -> str:
    return (_text(header[j]) or "") if j < len(header) else ""


def _col(header: list[Any], *starts: str, nth: int = 0) -> int | None:
    """Index of the nth column whose header (lower-case) starts with one of `starts`."""
    hits = [j for j, h in enumerate(header)
            if any(str(h or "").strip().lower().startswith(s) for s in starts)]
    return hits[nth] if len(hits) > nth else None


def _plant_blocks_old(header: list[Any]) -> list[tuple[int, int, int, str]]:
    """File 1: (status col, kWh col, cost col, plant label).

    The status headers in this file are shifted by one plant (the column labelled
    "Status (TLMAL HYD)" sits in front of the TBAL block, and one status column has no label), so the
    plant is taken from the savings header that directly follows each status column.
    """
    blocks = []
    for j, h in enumerate(header):
        if str(h or "").strip().lower().startswith("status"):
            nxt = _hdr(header, j + 1)
            m = re.search(r"\(([^)]+)\)\s*$", nxt)
            if nxt.lower().startswith("energy saving") and m:
                blocks.append((j, j + 1, j + 2, m.group(1)))
    return blocks


def _plant_blocks_new(top: list[Any]) -> list[tuple[int, int, int, str]]:
    """File 2: row 1 carries "(TASL HYD)" etc. over each Status / kWh / cost triple."""
    return [(j, j + 1, j + 2, m.group(1)) for j, v in enumerate(top)
            if (m := re.fullmatch(r"\s*\(([^)]+)\)\s*", str(v or "")))]


def _xlsx_records(path: Path, key: str, phase_rule: str, notes: list[str]) -> list[dict]:
    rows, sheet = _grid(path)
    hi = next((i for i, r in enumerate(rows) if str(r[0] or "").strip().lower().startswith("sl")), None)
    if hi is None:
        notes.append(f"{path.name}: header row (Sl. No.) not found")
        return []
    header = rows[hi]
    is_plan = phase_rule == "plan"
    blocks = _plant_blocks_new(rows[hi - 1]) if is_plan and hi > 0 else _plant_blocks_old(header)
    if not blocks:
        blocks = _plant_blocks_old(header)
    c = {
        "slno": _col(header, "sl"), "initPlant": _col(header, "initiation plant"),
        "category": _col(header, "category"), "equipment": _col(header, "equipment"),
        "type": _col(header, "initiative category"), "date": _col(header, "initiation date"),
        "year": _col(header, "year of activity"), "by": _col(header, "initiated by"),
        "action": _col(header, "action planned"), "priority": _col(header, "priority"),
        "resp": _col(header, "resp"), "invest": _col(header, "investment"),
        "expected": _col(header, "expected saving"), "roi": _col(header, "roi"),
        "planned": _col(header, "planned date"), "actual": _col(header, "actual date"),
        "initStatus": _col(header, "initiated plant status"), "remarks": _col(header, "remarks"),
    }
    # Category is matched by prefix, so it can hit "Category" only (the exact header).
    c["category"] = next((j for j, h in enumerate(header) if str(h or "").strip().lower() == "category"), None)
    extra = {_hdr(header, j): j for j in range(len(header))
             if _hdr(header, j).upper() in {"TASL BLR", "TASL NGP", "TSAL HYD", "TCOE HYD"}}

    def cell(r: list[Any], k: str) -> Any:
        return r[c[k]] if c[k] is not None else None

    out: list[dict] = []
    for i in range(hi + 1, len(rows)):
        r = rows[i]
        action = _text(cell(r, "action"))
        if action is None and to_number(cell(r, "slno")) is None:
            continue
        excel_row = i + 1
        statuses = []
        for sc, kc, cc, label in blocks:
            original = _text(r[sc])
            if original is None:
                continue
            ref = _plant_ref(label)
            norm = normalise_status(original)
            if norm is None:
                notes.append(f"{path.name} row {excel_row}: unrecognised status {original!r} for {label}")
            statuses.append({
                "plant": ref["plant"], "plantLabel": _text(label),
                "status": norm, "statusOriginal": original,
                "energySavingKwh": to_number(r[kc]), "energySavingKwhRaw": _text(r[kc]),
                "energyCostSavingCr": to_number(r[cc]), "energyCostSavingCrRaw": _text(r[cc]),
                "phase": ("present" if norm in PRESENT_STATUSES else "planned") if norm else None,
            })
        equipment = _text(cell(r, "equipment"))
        domain = _XLSX_DOMAIN.get((equipment or "").lower())
        domain_source = "equipment column"
        if domain is None:
            domain, domain_source = classify_domain(action), "description keywords"
        init_label = _text(cell(r, "initPlant"))
        init_plants = list(PLANT_IDS) if re.sub(r"[^a-z]", "", (init_label or "").lower()) in ALL_PLANTS_LABELS \
            else [p for p in [canonical_plant(init_label)] if p]
        kwh = [s["energySavingKwh"] for s in statuses if s["energySavingKwh"] is not None]
        cost = [s["energyCostSavingCr"] for s in statuses if s["energyCostSavingCr"] is not None]
        if is_plan:
            phase = "planned"
        else:
            phase = "present" if any(s["status"] in PRESENT_STATUSES for s in statuses) else "planned"
        slno = to_number(cell(r, "slno"))
        init_status_orig = _text(cell(r, "initStatus"))
        out.append({
            "id": f"{key}-r{excel_row}",
            "groupKey": _group_key("xlsx", slno, action),
            "source": {"kind": "initiatives-file", "workbook": path.name, "sheet": sheet, "row": excel_row,
                       "slNo": int(slno) if slno is not None else None},
            "phase": phase,
            "plants": [s["plant"] for s in statuses if s["plant"]],
            "initiationPlant": {"label": init_label, "plants": init_plants},
            "domain": domain, "domainSource": domain_source,
            "equipmentArea": equipment,
            "category": _text(cell(r, "category")),
            "type": _text(cell(r, "type")),
            "energyType": _energy_type(_text(cell(r, "type"))),
            "description": action,
            "initiationDate": _iso_date(cell(r, "date")), "initiationDateOriginal": _text(cell(r, "date")),
            "yearOfActivity": int(to_number(cell(r, "year"))) if to_number(cell(r, "year")) else None,
            "plannedCompletion": _iso_date(cell(r, "planned")) or _text(cell(r, "planned")),
            "actualCompletion": _iso_date(cell(r, "actual")) or _text(cell(r, "actual")),
            "initiatedBy": _text(cell(r, "by")),
            "responsible": _text(cell(r, "resp")),
            "priority": _priority(cell(r, "priority")),
            "investmentRs": to_number(cell(r, "invest")),
            "expectedSavingPerYear": to_number(cell(r, "expected")),
            "roiYears": to_number(cell(r, "roi")),
            "initiatorStatus": {"status": normalise_status(init_status_orig), "statusOriginal": init_status_orig}
            if init_status_orig else None,
            "plantStatuses": statuses,
            "achieved": {
                "energyKwh": sum(kwh) if kwh else None, "energyKwhUnit": "kWh",
                "energyCostCr": sum(cost) if cost else None, "energyCostUnit": "Cr (as headed in the source)",
                "co2Kg": None,
            },
            "expected": {"energyKwh": None, "energyCostCr": None, "co2Kg": None,
                         "perYear": to_number(cell(r, "expected"))},
            "otherPlantColumns": {k: _text(r[j]) for k, j in extra.items() if _text(r[j])} or None,
            "remarks": _text(cell(r, "remarks")),
            "statusKnown": bool(statuses),
        })
    return out


def _energy_type(initiative_type: str | None) -> str | None:
    low = (initiative_type or "").lower()
    if "power" in low:
        return "power"
    if "water" in low or "rwh" in low:
        return "water"
    if "hsd" in low or "fuel" in low:
        return "fuel"
    return None


# --------------------------------------------------------------------------- the calculation workbook

def _calc_cell(calc: list[list[Any]], entry: list[list[Any]] | None, i: int, j: int) -> tuple[float | None, str]:
    """Calculation value, or the "Only to Enter" value when calc is blank and entry has a non-zero number
    (entry stores 0 where calc says NA, so a bare 0 is not treated as a fill)."""
    v = to_number(calc[i][j]) if j < len(calc[i]) else None
    if v is not None:
        return v, "calculation"
    if entry is not None and i < len(entry) and j < len(entry[i]):
        ev = to_number(entry[i][j])
        if ev not in (None, 0.0):
            return ev, "entry"
    return None, "calculation"


def _calc_records(kind: str, notes: list[str], recon: list[dict]) -> list[dict]:
    sheet_name, energy_type, default_unit = CALC_SHEETS[kind]
    cwb = calculation_workbook()
    if cwb is None or sheet_name not in sheet_names(cwb):
        notes.append(f"Calculation workbook sheet {sheet_name!r} not found")
        return []
    calc = read_sheet(cwb, sheet_name)
    entry = None
    ewb = entry_workbook()
    if ewb is not None and sheet_name in sheet_names(ewb):
        entry = read_sheet(ewb, sheet_name)
    hi = next((i for i, r in enumerate(calc) if str(r[0] or "").strip().lower().startswith("sl")), None)
    if hi is None:
        notes.append(f"{sheet_name}: header row (Sl. No.) not found")
        return []
    header = calc[hi]
    fy_cols: list[tuple[str, int, int | None]] = []
    for j, h in enumerate(header):
        m = re.match(r"\s*total fy\s*(\d{2})(?:\s*-\s*(\d{2}))?", str(h or ""), re.I)
        if m:
            co2 = j + 1 if str(header[j + 1] or "").lower().startswith("total co2") else None
            fy_cols.append((_fy_label(m[1], m[2]), j, co2))
    # A single unit per sheet is used only where the sheet itself shows exactly one UOM.
    uoms = {_text(r[3]) for r in calc[hi + 1:] if _text(r[3])}
    sheet_unit = default_unit or (next(iter(uoms)) if len(uoms) == 1 else None)

    records: list[dict] = []
    plant: dict | None = None
    section = "existing"
    totals: dict[str, dict] = {}
    for i in range(hi + 1, len(calc)):
        r = calc[i]
        label = _text(r[1])
        desc = _text(r[2])
        if label and canonical_plant(label):
            plant, section = _plant_ref(label), "existing"
        elif label and plant is None:
            continue  # "Examples" block
        if plant is None:
            continue
        low = (desc or "").lower()
        if low.startswith("plant total"):
            totals[plant["plant"] or plant["label"]] = {
                fy: _calc_cell(calc, entry, i, j)[0] for fy, j, _ in fy_cols}
            plant_total_row = i + 1
            totals[plant["plant"] or plant["label"]]["_row"] = plant_total_row  # type: ignore[assignment]
            plant = None  # EB / Solar / DG tables follow the last plant; ignore until the next plant label
            continue
        if low.startswith("future plan"):
            section = "future"
            continue
        if not desc:
            continue
        achieved_fy: dict[str, dict] = {}
        used_entry = False
        for fy, j, co2j in fy_cols:
            val, src = _calc_cell(calc, entry, i, j)
            co2, src2 = _calc_cell(calc, entry, i, co2j) if co2j is not None else (None, "calculation")
            if val is None and co2 is None:
                continue
            used_entry |= "entry" in (src, src2)
            achieved_fy[fy] = {"value": val, "co2Kg": co2}
        unit = _text(r[3]) or sheet_unit
        has_savings = any((v["value"] or 0) > 0 for v in achieved_fy.values())
        if section == "future":
            status, original, basis, phase = "open", "Listed under 'Future Plan for Improvements'", "section", "planned"
        elif has_savings:
            status, original, basis, phase = "implemented", None, "savings recorded in a yearly total", "present"
        else:
            status, original, basis, phase = None, None, "none (listed without savings)", "present"
        domain = "water" if kind == "water" else classify_domain(desc)
        total_value = sum(v["value"] for v in achieved_fy.values() if v["value"] is not None) if achieved_fy else None
        records.append({
            "id": f"calc-{kind}-r{i + 1}",
            "groupKey": _group_key("calc", plant["plant"] or plant["label"], desc),
            "source": {"kind": "calculation-workbook", "workbook": cwb.name, "sheet": sheet_name, "row": i + 1,
                       "usedEntryFallback": used_entry},
            "phase": phase,
            "plants": [plant["plant"]] if plant["plant"] else [],
            "initiationPlant": {"label": plant["label"], "plants": [plant["plant"]] if plant["plant"] else []},
            "domain": domain, "domainSource": "sheet" if kind == "water" else "description keywords",
            "equipmentArea": None, "category": None, "type": sheet_name, "energyType": energy_type,
            "description": desc,
            "initiationDate": None, "initiationDateOriginal": None, "yearOfActivity": None,
            "plannedCompletion": None, "actualCompletion": None,
            "initiatedBy": None, "responsible": None, "priority": None,
            "investmentRs": None, "expectedSavingPerYear": None, "roiYears": None, "initiatorStatus": None,
            "plantStatuses": [{
                "plant": plant["plant"], "plantLabel": plant["label"], "status": status,
                "statusOriginal": original, "statusBasis": basis,
                "energySavingKwh": None, "energySavingKwhRaw": None,
                "energyCostSavingCr": None, "energyCostSavingCrRaw": None,
                "phase": phase if status else None,
            }],
            "achieved": {
                "byFinancialYear": achieved_fy, "unit": unit, "co2Unit": "Kg CO2 / yr",
                "energyKwh": total_value if (energy_type == "power" and total_value is not None) else None,
                "total": total_value, "totalUnit": unit,
                "energyCostCr": None, "co2Kg": None,
            },
            "expected": {"energyKwh": None, "energyCostCr": None, "co2Kg": None, "perYear": None},
            "otherPlantColumns": None, "remarks": None, "statusKnown": status is not None,
        })
    # Per-plant check: does the sheet's own "Plant Total" row equal the sum of the plant's rows?
    sums: dict[str, dict[str, float]] = {}
    for rec in records:
        p = rec["plants"][0] if rec["plants"] else rec["initiationPlant"]["label"]
        for fy, v in rec["achieved"]["byFinancialYear"].items():
            if v["value"] is not None:
                sums.setdefault(p, {}).setdefault(fy, 0.0)
                sums[p][fy] += v["value"]
    for p, t in totals.items():
        for fy, reported in t.items():
            if fy == "_row" or reported is None:
                continue
            got = sums.get(p, {}).get(fy, 0.0)
            if abs(reported - got) > 0.5:
                recon.append({"sheet": sheet_name, "plant": p, "financialYear": fy, "row": t["_row"],
                              "plantTotalInSheet": reported, "sumOfListedInitiatives": round(got, 3)})
    _link_duplicates(records)
    return records


def _link_duplicates(records: list[dict]) -> None:
    """Records sharing a groupKey are the same initiative written more than once; they stay separate
    but carry `groupSize` so a page can show them as related."""
    sizes: dict[str, int] = {}
    for rec in records:
        sizes[rec["groupKey"]] = sizes.get(rec["groupKey"], 0) + 1
    for rec in records:
        rec["groupSize"] = sizes[rec["groupKey"]]


# --------------------------------------------------------------------------- assembly

def _stamp(*paths: Path | None) -> tuple:
    return tuple((str(p), p.stat().st_mtime) if p else None for p in paths)


def load_dataset() -> dict:
    """All records plus notes; rebuilt only when a source file changes on disk."""
    base, plan = _find_files()
    cwb = calculation_workbook()
    key = _stamp(base, plan, cwb)
    with _lock:
        if key in _cache:
            return _cache[key]
    notes: list[str] = []
    recon: list[dict] = []
    records: list[dict] = []
    if base:
        records += _xlsx_records(base, "esi", "existing", notes)
    else:
        notes.append("initiatives/'Energy Saving Initiatives Data.xlsx' not found")
    if plan:
        records += _xlsx_records(plan, "fy27", "plan", notes)
    else:
        notes.append("initiatives/'FY 2026-27 Energy Saving Initiatives Data.xlsx' not found")
    if cwb is None:
        notes.append("Sustainability Calculation workbook not found; sheet-based initiatives skipped")
    else:
        for kind in CALC_SHEETS:
            records += _calc_records(kind, notes, recon)
    # Same initiative in the two xlsx files shares a groupKey already (Sl. No. + text); count group sizes
    # across the whole list so linked records in different sources are visible.
    _link_duplicates(records)
    data = {
        "records": records, "byId": {r["id"]: r for r in records}, "notes": notes, "reconciliation": recon,
        "sources": [
            {"role": "existing initiatives", "file": base.name if base else None},
            {"role": "FY 2026-27 plan", "file": plan.name if plan else None},
            {"role": "calculation workbook sheets", "file": cwb.name if cwb else None},
        ],
    }
    with _lock:
        _cache.clear()
        _cache[key] = data
    return data


def _matches(rec: dict, plant: str | None, status: str | None, domain: str | None, phase: str | None) -> bool:
    if phase and rec["phase"] != phase:
        return False
    if domain and rec["domain"] != domain:
        return False
    sts = rec["plantStatuses"]
    if plant:
        if plant not in rec["plants"]:
            return False
        sts = [s for s in sts if s["plant"] == plant]
    if status and not any(s["status"] == status for s in sts):
        return False
    return True


def list_initiatives(plant: str | None = None, status: str | None = None,
                     domain: str | None = None, phase: str | None = None) -> dict:
    data = load_dataset()
    plant_id = canonical_plant(plant) if plant else None
    notes = list(data["notes"])
    if plant and not plant_id:
        notes.append(f"Unknown plant {plant!r}; no initiative can match it")
    items = [r for r in data["records"]
             if (not plant or plant_id) and _matches(r, plant_id, status, domain, phase)]
    return {"count": len(items), "items": items, "statuses": STATUSES, "sources": data["sources"], "notes": notes}


def get_initiative(initiative_id: str) -> dict | None:
    return load_dataset()["byId"].get(initiative_id)


def _preferred_xlsx_records() -> list[dict]:
    """One record per initiative from the two xlsx files, the newer FY 2026-27 file winning."""
    chosen: dict[str, dict] = {}
    for rec in load_dataset()["records"]:
        if rec["source"]["kind"] != "initiatives-file":
            continue
        if rec["groupKey"] not in chosen or rec["id"].startswith("fy27"):
            chosen[rec["groupKey"]] = rec
    return list(chosen.values())


def horizontal_deployment_candidates(plant_id: str, include_unrecorded: bool = False) -> list[dict]:
    """Initiatives done at another plant but still open / under study at `plant_id`.

    Only the initiatives files hold a status per plant, so only they can feed this. A plant with a
    blank status cell is "not recorded" and is left out unless `include_unrecorded` is true.
    """
    pid = canonical_plant(plant_id) or plant_id
    out = []
    for rec in _preferred_xlsx_records():
        done_at = [s for s in rec["plantStatuses"] if s["plant"] and s["plant"] != pid and s["status"] in DONE_STATUSES]
        if not done_at:
            continue
        here = next((s for s in rec["plantStatuses"] if s["plant"] == pid), None)
        if here is None:
            if not include_unrecorded:
                continue
        elif here["status"] not in {"open", "under_study"}:
            continue
        out.append({
            "id": rec["id"], "groupKey": rec["groupKey"], "description": rec["description"],
            "domain": rec["domain"], "type": rec["type"], "priority": rec["priority"],
            "plant": pid,
            "statusAtPlant": here["status"] if here else None,
            "statusAtPlantOriginal": here["statusOriginal"] if here else None,
            "implementedAt": [{"plant": s["plant"], "status": s["status"], "statusOriginal": s["statusOriginal"],
                               "energySavingKwh": s["energySavingKwh"], "energyCostSavingCr": s["energyCostSavingCr"]}
                              for s in done_at],
            "source": rec["source"],
        })
    out.sort(key=lambda c: (c["priority"] is None, c["priority"] or 0, -len(c["implementedAt"])))
    return out


def summary(plant: str | None = None, domain: str | None = None, phase: str | None = None) -> dict:
    data = load_dataset()
    plant_id = canonical_plant(plant) if plant else None
    recs = [r for r in data["records"] if _matches(r, plant_id, None, domain, phase)]
    # Status counts use only the initiatives files (the only source with a real per-plant status),
    # one record per initiative (FY 2026-27 file preferred, so the two copies are not counted twice).
    # The phase filter is not applied here: it would pick between two copies of the same initiative.
    by_status = {s: 0 for s in STATUSES}
    by_status["not_recorded"] = 0
    for r in _preferred_xlsx_records():
        if not _matches(r, plant_id, None, domain, None):
            continue
        for s in r["plantStatuses"]:
            if plant_id and s["plant"] != plant_id:
                continue
            by_status[s["status"] or "not_recorded"] += 1
    by_plant: dict[str, dict] = {pid: {"initiatives": 0, "byStatus": {s: 0 for s in STATUSES}} for pid in PLANT_IDS}
    by_domain: dict[str, int] = {}
    by_phase = {"present": 0, "planned": 0}
    by_source = {"initiatives-file": 0, "calculation-workbook": 0}
    sheet_rows = {"withSavings": 0, "listedWithoutSavings": 0, "futurePlan": 0}
    for r in recs:
        by_phase[r["phase"]] += 1
        by_source[r["source"]["kind"]] += 1
        by_domain[r["domain"] or "unspecified"] = by_domain.get(r["domain"] or "unspecified", 0) + 1
        for pid in set(r["plants"]):
            by_plant[pid]["initiatives"] += 1
        if r["source"]["kind"] == "calculation-workbook":
            st = r["plantStatuses"][0]
            sheet_rows["futurePlan" if st["statusBasis"] == "section" else
                       "withSavings" if st["status"] else "listedWithoutSavings"] += 1
    for r in _preferred_xlsx_records():
        if not _matches(r, plant_id, None, domain, None):
            continue
        for s in r["plantStatuses"]:
            if s["plant"] and s["status"] and (not plant_id or s["plant"] == plant_id):
                by_plant[s["plant"]]["byStatus"][s["status"]] += 1

    # Savings as given; kept per basis because kWh, kL, litres and kg are not additive.
    xlsx_totals: dict[str, dict] = {}
    for src_key in ("esi", "fy27"):
        kwh = cost = 0.0
        n = 0
        for r in recs:
            if r["id"].startswith(src_key):
                for s in r["plantStatuses"]:
                    if plant_id and s["plant"] != plant_id:
                        continue
                    for val, field in ((s["energySavingKwh"], "k"), (s["energyCostSavingCr"], "c")):
                        if val is not None:
                            n += field == "k"
                            kwh += val if field == "k" else 0
                            cost += val if field == "c" else 0
        xlsx_totals[src_key] = {"energyKwh": kwh if n else None, "energyCostCr": cost if n else None,
                                "unitsKwh": "kWh", "unitsCost": "Cr (as headed in the source)", "entries": n}
    calc_totals: dict[str, dict] = {}
    for r in recs:
        if r["source"]["kind"] != "calculation-workbook" or r["phase"] != "present":
            continue
        kind = r["id"].split("-")[1]
        for fy, v in r["achieved"]["byFinancialYear"].items():
            cell = calc_totals.setdefault(kind, {}).setdefault(
                fy, {"achieved": 0.0, "co2Kg": 0.0, "hasCo2": False, "unit": r["achieved"]["unit"], "rows": 0})
            if v["value"] is not None:
                cell["achieved"] += v["value"]
                cell["rows"] += 1
            if v["co2Kg"] is not None:
                cell["co2Kg"] += v["co2Kg"]
                cell["hasCo2"] = True
    calc_out = {k: {fy: {"achieved": round(c["achieved"], 3), "unit": c["unit"],
                         "co2Kg": round(c["co2Kg"], 3) if c["hasCo2"] else None, "rows": c["rows"],
                         **({} if c["unit"] else {"unitNote": "the source gives no unit for these rows"})}
                    for fy, c in sorted(v.items())} for k, v in calc_totals.items()}

    horizontal = {pid: horizontal_deployment_candidates(pid) for pid in ([plant_id] if plant_id else PLANT_IDS)}
    return {
        "filters": {"plant": plant_id, "domain": domain, "phase": phase},
        "totalInitiatives": len(recs),
        "byPhase": by_phase,
        "bySource": by_source,
        "byStatus": by_status,
        "byStatusNote": "Per-plant status entries from the initiatives files only, one record per initiative "
                        "(FY 2026-27 copy preferred), phase filter not applied.",
        "calculationSheetRows": sheet_rows,
        "byPlant": by_plant,
        "byDomain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])),
        "savings": {
            "expected": {"energyKwh": None, "energyCostCr": None, "co2Kg": None,
                         "note": "The FY 2026-27 file has 'Expected Saving/Year' and 'Investment Cost' columns but "
                                 "every cell is blank, so no expected saving exists in the source."},
            "achievedFromInitiativesFiles": xlsx_totals,
            "achievedFromCalculationWorkbook": calc_out,
            "note": "Files 'esi' and 'fy27' list the same 21 initiatives; do not add them together.",
        },
        "horizontalDeployment": {pid: {"count": len(v), "candidates": v} for pid, v in horizontal.items()},
        "reconciliation": data["reconciliation"],
        "sources": data["sources"],
        "notes": data["notes"],
    }


# --------------------------------------------------------------------------- routes

@router.get("")
def api_list(plant: str | None = None, status: str | None = None, domain: str | None = None,
             phase: str | None = Query(None, pattern="^(present|planned)$")) -> dict:
    return list_initiatives(plant, status, domain, phase)


@router.get("/summary")
def api_summary(plant: str | None = None, domain: str | None = None,
                phase: str | None = Query(None, pattern="^(present|planned)$")) -> dict:
    return summary(plant, domain, phase)


@router.get("/horizontal-deployment/{plant_id}")
def api_horizontal(plant_id: str, include_unrecorded: bool = False) -> dict:
    pid = canonical_plant(plant_id)
    if pid is None:
        raise HTTPException(404, f"Unknown plant {plant_id!r}")
    items = horizontal_deployment_candidates(pid, include_unrecorded)
    return {"plant": pid, "count": len(items), "items": items}


@router.get("/{initiative_id}")
def api_one(initiative_id: str) -> dict:
    rec = get_initiative(initiative_id)
    if rec is None:
        raise HTTPException(404, f"No initiative {initiative_id!r}")
    return rec
