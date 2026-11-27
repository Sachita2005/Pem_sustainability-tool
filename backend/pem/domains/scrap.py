"""Scrap / waste: quantities, disposal methods, targets and the scrap-digitalisation initiative.

Real data only. What the source files actually hold:

* "Scrap generation details" (workbook "Sustainability (Only to Enter)") is the only sheet with
  quantities: hazardous waste items and e-waste per plant, monthly, FY21-22 to FY26-27.
  (The "Sustainability Calculation" workbook has no scrap quantities: its "Waste Data Sheet - Alt 1",
  "Waste Data Sheet - Alt (2)" and "Summary Sheet" are empty templates / zeros.)
* "Waste Sheet Disposal Method" (calculation workbook) is a plant x waste-name -> disposal-method table
  with no quantities.
* "Target Sheet" (calculation workbook) states "Landfill Waste - 2030" with target level 0.
* The Mendix document (docx) is read with python-docx (tables, flow-chart text boxes, images).
* The BRD (pdf) is a scan: it has no text layer and no OCR is available. The few pages that were
  read by eye are kept in BRD_TRANSCRIPTION, clearly marked as such; the PDF itself is served too.

Units are kept as the source states them. Weights (KG, T) are put on a common kg scale by x1000 for T;
litres are never mixed with weights.
"""
from __future__ import annotations

import datetime as dt
import re
import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from ..config import find_source_file
from ..plants import PLANT_IDS, PLANTS, canonical_plant, plant_name
from ..workbooks import (calculation_workbook, entry_workbook, find_sheet, read_sheet,
                         to_number)

router = APIRouter(prefix="/api/scrap", tags=["scrap"])

CLUSTERS = {
    "DP": ["TASL-NGP", "TASL-BLR", "TSAL", "TCOE"],
    "ASSY": ["TLMAL", "TASL-HYD", "TBAL", "TASL-MCA"],
}
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------

def parse_month_header(value: Any) -> tuple[int, int] | None:
    """(year, month) of a month header cell.

    The sheets hold "Apr-26" typed as a date, so Excel stored 26 April of the current year:
    the month is the month and the DAY is the two-digit year ("2025-01-26" is January 2026).
    Text headers such as "Apr-22" / "March-19" are read as written.
    """
    if isinstance(value, (dt.datetime, dt.date)):
        return 2000 + value.day, value.month
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]*-(\d\d)", str(value or "").strip())
    if m and m[1].lower() in _MONTHS:
        return 2000 + int(m[2]), _MONTHS[m[1].lower()]
    return None


def fy_label(start_year: int) -> str:
    return f"FY{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def fy_of_month(year: int, month: int) -> str:
    return fy_label(year if month >= 4 else year - 1)


def clean_unit(unit: Any) -> str | None:
    text = str(unit).strip() if unit is not None else ""
    return text.replace("�", "µ") or None


def to_kg(value: float | None, unit: str | None) -> float | None:
    """Weight in kg when the unit is a weight (KG, T); None for litres and unknown units."""
    if value is None or not unit:
        return None
    u = unit.lower()
    if u in ("kg", "kgs"):
        return value
    if u in ("t", "ton", "tons", "tonne", "tonnes"):
        return value * 1000.0
    return None


def is_volume(unit: str | None) -> bool:
    return (unit or "").lower() in ("l", "liter", "liters", "litre", "litres", "lpm")


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


# --------------------------------------------------------------------------------------
# Quantities: "Scrap generation details"
# --------------------------------------------------------------------------------------

def _find_header_row(grid: list[list[Any]]) -> int | None:
    for i, row in enumerate(grid[:15]):
        if str(row[0] or "").strip().lower().startswith("sl") and str(row[1] or "").strip().lower() == "plant":
            return i
    return None


def _scrap_blocks(header: list[Any]) -> list[dict]:
    """Year blocks: 12 consecutive month headers followed by a 'FY xx-yy' total column.

    The same sheet carries power-consumption blocks further right; their total column is followed
    by more headers ("Total CO2 ..."), a scrap block's is followed by a blank spacer column.
    """
    cols = [(j, parse_month_header(v)) for j, v in enumerate(header)]
    blocks: list[dict] = []
    run: list[tuple[int, tuple[int, int]]] = []
    for j, p in cols + [(len(header), None)]:
        if p is not None and (not run or j == run[-1][0] + 1):
            run.append((j, p))
            continue
        if len(run) >= 12:
            first, total = run[0], run[-1][0] + 1
            label = str(header[total] or "") if total < len(header) else ""
            nxt = header[total + 1] if total + 1 < len(header) else None
            if re.match(r"FY\s*\d\d-\d\d", label) and nxt in (None, ""):
                start_year = first[1][0] if first[1][1] == 4 else first[1][0] - 1
                blocks.append({
                    "fy": fy_label(start_year),
                    "months": [(c, month_key(start_year + (1 if k >= 9 else 0), (k + 3) % 12 + 1))
                               for k, (c, _p) in enumerate(run[:12])],
                    "totalCol": total,
                })
        run = [(j, p)] if p is not None else []
    return blocks


def _parse_scrap(grid: list[list[Any]]) -> dict:
    """Rows -> items per plant, with monthly values and the sheet's stated plant totals."""
    notes: list[str] = []
    hi = _find_header_row(grid)
    if hi is None:
        return {"items": [], "statedTotals": [], "notes": ["Header row (Sl. No. / Plant) not found in 'Scrap generation details'."]}
    blocks = _scrap_blocks(grid[hi])
    items: list[dict] = []
    totals: list[dict] = []
    plant_raw, plant, section = None, None, "Hazardous"
    for r in grid[hi + 1:]:
        if r[1] not in (None, ""):
            plant_raw = str(r[1]).strip()
            plant = canonical_plant(plant_raw) or plant_raw
            section = "Hazardous"
        label = str(r[2] or "").strip()
        if not label or plant is None:
            continue
        low = label.lower()
        unit = clean_unit(r[3])
        per_fy: dict[str, dict] = {}
        for b in blocks:
            months = {mk: to_number(r[c]) for c, mk in b["months"]}
            stated = to_number(r[b["totalCol"]])
            per_fy[b["fy"]] = {"months": months, "statedTotal": stated}
        if low.startswith("plant total"):
            totals.append({"plant": plant, "section": "E-Waste" if "e-waste" in low or "e waste" in low else "Hazardous",
                           "label": label, "unit": unit, "perFy": per_fy})
            if "hazardous" in low:
                section = "E-Waste"   # e-waste rows follow the hazardous total inside a plant block
            continue
        if low.startswith("total tadl"):
            totals.append({"plant": "TADL", "section": "E-Waste" if "e-waste" in low else "Hazardous",
                           "label": label, "unit": unit, "perFy": per_fy})
            continue
        category = "E-Waste" if (section == "E-Waste" or "e-waste" in low or "e waste" in low) else "Hazardous"
        has_data = any(v["statedTotal"] not in (None, 0) or any(x not in (None, 0) for x in v["months"].values())
                       for v in per_fy.values())
        items.append({"plant": plant, "plantLabel": plant_raw, "category": category, "wasteType": label,
                      "unit": unit, "perFy": per_fy, "hasData": has_data, "row": None})
    return {"items": items, "statedTotals": totals, "notes": notes}


def _load_scrap() -> dict:
    path = entry_workbook()
    out = {"items": [], "statedTotals": [], "notes": [], "file": path.name if path else None}
    if path is None:
        out["notes"].append("The 'Sustainability (Only to Enter)' workbook was not found; no scrap quantities available.")
        return out
    name = find_sheet(path, "scrap generation")
    if not name:
        out["notes"].append("Sheet 'Scrap generation details' not found in the 'Only to Enter' workbook.")
        return out
    parsed = _parse_scrap(read_sheet(path, name))
    out.update(parsed, file=path.name, sheet=name)
    return out


def _item_fy(item: dict, fy: str) -> dict:
    """Value of one item for one FY: sum of the months present, else the sheet's stated total."""
    cell = item["perFy"].get(fy)
    if cell is None:
        return {"value": None, "basis": None, "monthsReported": 0, "statedTotal": None}
    nums = [v for v in cell["months"].values() if v is not None]
    stated = cell["statedTotal"]
    if nums:
        return {"value": sum(nums), "basis": "months", "monthsReported": len(nums), "statedTotal": stated}
    if stated is not None:
        return {"value": stated, "basis": "stated-total", "monthsReported": 0, "statedTotal": stated}
    return {"value": None, "basis": None, "monthsReported": 0, "statedTotal": None}


def _selected(plant: str | None, fy: str | None, category: str | None, data: dict) -> list[dict]:
    pid = canonical_plant(plant) if plant else None
    rows = []
    for it in data["items"]:
        if plant and it["plant"] != (pid or plant):
            continue
        if category and it["category"].lower() != category.lower():
            continue
        if fy and fy not in it["perFy"]:
            continue
        rows.append(it)
    return rows


def quantities(plant: str | None = None, fy: str | None = None, category: str | None = None) -> dict:
    """Monthly and yearly quantities per plant and waste item, in the sheet's own unit."""
    data = _load_scrap()
    notes = list(data["notes"])
    items = []
    for it in _selected(plant, fy, category, data):
        fys = {}
        for f, cell in it["perFy"].items():
            if fy and f != fy:
                continue
            calc = _item_fy(it, f)
            fys[f] = {
                "total": _round(calc["value"]), "basis": calc["basis"], "monthsReported": calc["monthsReported"],
                "statedTotal": _round(calc["statedTotal"]),
                "months": {k: _round(v) for k, v in cell["months"].items() if v is not None},
                "totalKg": _round(to_kg(calc["value"], it["unit"])),
            }
        if not any(v["total"] is not None for v in fys.values()):
            continue
        items.append({"plant": it["plant"], "plantName": plant_name(it["plant"]), "category": it["category"],
                      "wasteType": it["wasteType"], "unit": it["unit"], "source": "entry", "fy": fys})
    if not items and not data["notes"]:
        notes.append("No quantities for this selection.")
    notes += _data_notes(data)
    return {"unit": "as stated per item (see `unit`)", "items": items,
            "financialYears": _fys(data), "notes": notes}


def _fys(data: dict) -> list[str]:
    seen = {f for it in data["items"] for f in it["perFy"]}
    return sorted(seen)


def _data_notes(data: dict) -> list[str]:
    """Oddities found in the source, stated rather than corrected."""
    notes = []
    if not data["items"]:
        return notes
    units = {it["unit"] for it in data["items"] if it["hasData"]}
    notes.append(f"Units differ between rows ({', '.join(sorted(u or '?' for u in units))}); weights are put on kg "
                 "(T x 1000), litres are kept separate and never added to weights.")
    notes.append("Plant-total rows in the sheet are labelled 'T' although their values add KG, T and litres rows; "
                 "they are not used here (see /api/scrap/totals `sheetTotals`).")
    return notes


# --------------------------------------------------------------------------------------
# Totals: plant, cluster, company
# --------------------------------------------------------------------------------------

def _zero() -> dict:
    return {"weightKg": 0.0, "volumeL": 0.0, "items": 0, "nonzero": False, "months": 0}


def totals(fy: str | None = None) -> dict:
    """Yearly and monthly totals per plant and category, then DP / ASSY cluster and TADL company."""
    data = _load_scrap()
    notes = list(data["notes"]) + _data_notes(data)
    by_year: dict[tuple[str, str, str], dict] = {}
    by_month: dict[tuple[str, str, str], dict] = {}
    for it in data["items"]:
        for f, cell in it["perFy"].items():
            if fy and f != fy:
                continue
            calc = _item_fy(it, f)
            if calc["value"] is None:
                continue
            kg = to_kg(calc["value"], it["unit"])
            acc = by_year.setdefault((it["plant"], f, it["category"]), _zero())
            acc["items"] += 1
            acc["nonzero"] = acc["nonzero"] or calc["value"] != 0
            acc["months"] = max(acc["months"], calc["monthsReported"])
            if kg is not None:
                acc["weightKg"] += kg
            elif is_volume(it["unit"]):
                acc["volumeL"] += calc["value"]
            else:
                notes.append(f"Unit {it['unit']!r} of {it['plant']} / {it['wasteType']} is neither weight nor volume; left out of totals.")
            for mk, v in cell["months"].items():
                if v is None:
                    continue
                m = by_month.setdefault((it["plant"], mk, it["category"]), _zero())
                m["nonzero"] = m["nonzero"] or v != 0
                mkg = to_kg(v, it["unit"])
                if mkg is not None:
                    m["weightKg"] += mkg
                elif is_volume(it["unit"]):
                    m["volumeL"] += v
    # A plant-year whose every item is 0 or blank is "nothing entered", not a measured zero: null.
    plants_year = [{"plant": p, "plantName": plant_name(p), "fy": f, "category": c,
                    "weightKg": _round(a["weightKg"]) if a["nonzero"] else None,
                    "volumeL": _round(a["volumeL"]) if a["nonzero"] else None,
                    "monthsReported": a["months"], "items": a["items"]}
                   for (p, f, c), a in sorted(by_year.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))]
    plants_month = [{"plant": p, "plantName": plant_name(p), "month": mk, "fy": fy_of_month(*map(int, mk.split("-"))),
                     "category": c, "weightKg": _round(a["weightKg"]) if a["nonzero"] else None,
                     "volumeL": _round(a["volumeL"]) if a["nonzero"] else None}
                    for (p, mk, c), a in sorted(by_month.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))]
    cluster_rows, company_rows = _roll_up(plants_year)
    return {
        "unit": {"weight": "kg", "volume": "L"},
        "plantYear": plants_year, "plantMonth": plants_month,
        "clusterYear": cluster_rows, "companyYear": company_rows,
        "clusters": CLUSTERS,
        "sheetTotals": _sheet_totals(data, fy),
        "notes": notes,
    }


def _roll_up(plant_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    cl: dict[tuple[str, str, str], dict] = {}
    co: dict[tuple[str, str], dict] = {}
    for r in plant_rows:
        if r["weightKg"] is None and r["volumeL"] is None:
            continue
        cluster = next((k for k, v in CLUSTERS.items() if r["plant"] in v), None)
        if cluster:
            a = cl.setdefault((cluster, r["fy"], r["category"]), {"weightKg": 0.0, "volumeL": 0.0, "plants": set()})
            a["weightKg"] += r["weightKg"] or 0
            a["volumeL"] += r["volumeL"] or 0
            a["plants"].add(r["plant"])
        b = co.setdefault((r["fy"], r["category"]), {"weightKg": 0.0, "volumeL": 0.0, "plants": set()})
        b["weightKg"] += r["weightKg"] or 0
        b["volumeL"] += r["volumeL"] or 0
        b["plants"].add(r["plant"])
    clusters = [{"cluster": c, "fy": f, "category": cat, "weightKg": _round(a["weightKg"]), "volumeL": _round(a["volumeL"]),
                 "plantsWithData": sorted(a["plants"])} for (c, f, cat), a in sorted(cl.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))]
    company = [{"company": "TADL", "fy": f, "category": cat, "weightKg": _round(a["weightKg"]), "volumeL": _round(a["volumeL"]),
                "plantsWithData": sorted(a["plants"])} for (f, cat), a in sorted(co.items())]
    return clusters, company


def _sheet_totals(data: dict, fy: str | None) -> list[dict]:
    """The totals the sheet itself states (plant total rows, 'Total TADL ...'), shown for comparison only."""
    out = []
    for t in data["statedTotals"]:
        for f, cell in t["perFy"].items():
            if fy and f != fy:
                continue
            if cell["statedTotal"] is None:
                continue
            out.append({"plant": t["plant"], "label": t["label"], "unitLabel": t["unit"], "fy": f,
                        "value": _round(cell["statedTotal"])})
    return out


# --------------------------------------------------------------------------------------
# Disposal methods: "Waste Sheet Disposal Method"
# --------------------------------------------------------------------------------------

def disposal_methods(plant: str | None = None) -> dict:
    path = calculation_workbook()
    notes = ["The sheet lists the disposal method per plant and waste; it holds no quantities, so quantity by "
             "disposal method cannot be computed from the source."]
    if path is None:
        return {"rows": [], "byMethod": [], "notes": notes + ["Calculation workbook not found."]}
    name = find_sheet(path, "disposal method")
    if not name:
        return {"rows": [], "byMethod": [], "notes": notes + ["Sheet 'Waste Sheet Disposal Method' not found."]}
    grid = read_sheet(path, name)
    hi = next((i for i, r in enumerate(grid[:10]) if str(r[1] or "").strip().lower() == "waste name"), None)
    if hi is None:
        return {"rows": [], "byMethod": [], "notes": notes + ["Header row (Waste Name / Plant) not found."]}
    pid = canonical_plant(plant) if plant else None
    rows = []
    for r in grid[hi + 1:]:
        if not r[1] or not r[2]:
            continue
        p = canonical_plant(r[2]) or str(r[2]).strip()
        if plant and p != (pid or plant):
            continue
        method = str(r[4]).strip() if r[4] not in (None, "") else None
        rows.append({"plant": p, "plantLabel": str(r[2]).strip(), "waste": str(r[1]).strip(), "wasteType": str(r[3] or "").strip() or None,
                     "method": method})
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["method"] or "(not stated)"] = counts.get(r["method"] or "(not stated)", 0) + 1
    blanks = sum(1 for r in rows if r["method"] is None)
    if blanks:
        notes.append(f"{blanks} rows have no disposal method in the sheet (left null).")
    return {"rows": rows, "byMethod": [{"method": k, "wastes": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
            "file": path.name, "sheet": name, "notes": notes}


# --------------------------------------------------------------------------------------
# Targets and baseline: "Target Sheet", "Summary Sheet"
# --------------------------------------------------------------------------------------

def targets() -> dict:
    path = calculation_workbook()
    notes: list[str] = []
    out: list[dict] = []
    if path is None:
        return {"targets": [], "baseline": None, "notes": ["Calculation workbook not found."]}
    name = find_sheet(path, "target")
    if name:
        for r in read_sheet(path, name)[1:]:
            cat = str(r[1] or "")
            if "landfill" in cat.lower():
                out.append({"category": cat.strip(), "targetYear": int(m[0]) if (m := re.search(r"\d{4}", cat)) else None,
                            "financialYear": str(r[2] or "").replace(" ", "") or None, "level": to_number(r[3]),
                            "reductionPercentage": to_number(r[4]), "unit": None,
                            "source": {"workbook": path.name, "sheet": name}})
        notes.append("Only the landfill target is a waste target; the sheet's carbon-emission targets belong to another area.")
        notes.append("The sheet does not state a unit for the landfill target level.")
    else:
        notes.append("Sheet 'Target Sheet' not found.")
    summary = find_sheet(path, "summary sheet")
    baseline = None
    if summary:
        vals = [to_number(c) for r in read_sheet(path, summary) for c in r[2:8] if to_number(c) is not None]
        baseline = None
        if vals and any(v != 0 for v in vals):
            notes.append("'Summary Sheet' holds non-zero KPI values; baseline not interpreted.")
        else:
            notes.append("'Summary Sheet' (KPIs Project Aalingana: hazardous / non-hazardous waste, landfill) holds only zeros "
                         "for FY19-20 to FY24-25, so it states no baseline; baseline is null.")
    return {"targets": out, "baseline": baseline, "notes": notes}


# --------------------------------------------------------------------------------------
# Initiative: the two documents
# --------------------------------------------------------------------------------------

BRD_PAGES_READ = [2, 3, 4, 5]

# Pages 2-5 of the BRD were read by eye from the rendered scan (no text layer, no OCR). Wording is
# reproduced as printed on the page; table cells are kept as printed. Pages 1 and 6-14 are not transcribed.
BRD_TRANSCRIPTION = {
    "method": "Read by eye from the rendered scanned pages (the PDF has no text layer; no OCR is installed).",
    "title": "Business Requirement Document - EHS - Scrap Management & Water Balance Solution (Tata Advanced Systems)",
    "objectives": [{
        "text": "The objective of this application is to establish a digitized system for Scrap Management (Hazardous, Non-Hazardous, "
                "E-Waste, Battery, General Waste and Bio-Waste) within the organization to facilitate improved monitoring and tracking functions.",
        "page": 2, "section": "1. Objective"}],
    "currentProcess": {
        "statement": "Currently, the entire process is managed manually and is decentralized.",
        "page": 2, "section": "2. Current Process",
        "wasteStreams": ["Metal Scrap", "Product/ Process", "Used Packing Scrap", "Garbage", "Hazardous Waste", "e-waste",
                         "Wooden Scrap", "Used Oil"],
        "steps": [
            {"process": "Generation and Movement of Scrap", "responsible": {"Metal Scrap": "End User (PRD/ Tooling/ PEM/ IT/ Stores)", "Product/ Process": "MRB/ End user", "Used Packing Scrap": "Stores / Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "IT/ PEM", "Wooden Scrap": "Stores /Admin", "Used Oil": "PEM / Admin"}},
            {"process": "Preparation and attachment of FORM-3 (Hazardous Waste) along with the Scrap Movement", "responsible": {"all streams": "End User (PRD/ Tooling/ PEM/ IT/ Stores)"}},
            {"process": "Weighment of Hazardous Scrap (Before Storage)", "responsible": {"all streams": "End User (PRD/ Tooling/ PEM/ IT/ Stores)"}},
            {"process": "Storage & Monitoring of Scrap", "responsible": {"Metal Scrap": "Stores/ Admin", "Product/ Process": "Stores", "Used Packing Scrap": "Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "PEM/ Stores/ IT", "Wooden Scrap": "Admin", "Used Oil": "PEM/ Admin"}},
            {"process": "Request for disposal", "responsible": {"Metal Scrap": "Stores", "Product/ Process": "Stores", "Used Packing Scrap": "Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "PEM/ Stores/ IT", "Wooden Scrap": "Admin", "Used Oil": "PEM"}},
            {"process": "Preparation of DC / Gate Pass", "responsible": {"all streams": "Stores"}},
            {"process": "Preparation of Invoice", "responsible": {"all streams": "Finance"}},
            {"process": "Weighment of Hazardous Scrap (Before Disposal)", "responsible": {"all streams": "Security"}},
            {"process": "Disposal of Scrap to PCB Authorized Vendor (Within 180 days)", "responsible": {"Metal Scrap": "Stores", "Product/ Process": "Stores", "Used Packing Scrap": "Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "PEM/ IT", "Wooden Scrap": "Admin", "Used Oil": "PEM"}},
        ]},
    "toBeProcess": {
        "statement": "The system must capture the entire scrap management process from the point of generation to final disposal with data entry, "
                     "approvals and acknowledgements responsibility assigned to the concerned operating departments. The proposed high-level workflow is as below:",
        "page": 2, "section": "3. To Be Process",
        "workflowImage": "page 3 (flow chart; its boxes are read below under flowSteps)",
        "flowSteps": [
            "Source", "Input Mode of Scrap", "Input Waste Category (Hazardous/ Non- Hazardous/ E-Waste/ Bio Medical Waste)", "Input Type of Waste",
            "Input Sub Type of Waste", "Input Measured Qty.", "PCB Authorized (Qty.) forecast for <=75% (TRUE -> continue; FALSE -> Auto-Mail & Reject Submission, Inform to EHS for Qty. Renewal)",
            "Input Measured Weight", "Preparation of Form 3", "Acknowledgement by POC to move scrap from Source to Yard",
            "Monitoring and Weighment of Haz Waste", "Move to Scrap Yard",
            "Input Vendor Details (Refer Temp: 2)", "Is Vendor authorized for PCB (forecast for 90 days)", "Check for Validation (Date) before >90 days",
            "(on failure: Alert auto-mail -> Inform Vendor for Registration/ Renewal)", "Finalize of Vendor after Bidding by Finance", "PO to PCB Authorized Vendor",
            "Disposal Sticker", "Loading of Scrap and Weighment of Haz Waste", "Acknowledgement by POC", "Disposal Date, DC & Gate pass details",
            "Movement to Material Gate", "Acknowledgement by Security", "Movement to PCB Authorized Vendor", "Scrap Disposal Report Generation",
            "Form 4,5,8,9,10 Submission and Show to PCB"],
        "page_of_steps": 3},
    "responsibilityMatrix": {
        "page": "3-4", "section": "Following is the Responsibility Matrix for each process involved",
        "columns": ["Sr. No.", "Process", "From - To", "Type of Waste", "Executing Dept.", "Authorization", "Acknowledgement"],
        "rows": [
            {"no": 1, "process": "Generation and Movement of Scrap", "fromTo": "Source - Scrap Yard", "executingDept": {"Metal Scrap": "End User", "Product/ Process": "MRB/ End user", "Used Packing Scrap": "Stores / Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "IT/ PEM", "Wooden Scrap": "Stores /Admin", "Used Oil": "PEM / Admin"}, "authorization": "Individual HOD/ Functional Head", "acknowledgement": "Admin/ Scrap Yard In-Charge", "page": 3},
            {"no": 2, "process": "Preparation and attachment of FORM-3 (Hazardous Waste) along with Scrap Movement", "fromTo": "Source - Scrap Yard", "executingDept": {"Hazardous Waste": "End User"}, "authorization": "Individual HOD/ Functional Head; PEM", "acknowledgement": "Admin / Scrap Yard In-Charge/ EHS; PEM (Hazardous & Chemical Waste)", "page": 3},
            {"no": 3, "process": "Weighment of Hazardous Scrap (Before Storage)", "fromTo": "Scrap Yard", "executingDept": {"Hazardous Waste": "End User"}, "authorization": "Individual HOD/ Functional Head", "acknowledgement": "Stores In-Charge", "page": 3},
            {"no": 4, "process": "Storage & Monitoring of Scrap", "fromTo": "Scrap Yard", "executingDept": {"Metal Scrap": "Stores/ Admin", "Product/ Process": "Stores", "Used Packing Scrap": "Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "PEM/ Stores/ IT", "Wooden Scrap": "Admin", "Used Oil": "PEM/ Admin"}, "authorization": "Individual HOD/ Functional Head", "acknowledgement": "EHS", "page": 3},
            {"no": 5, "process": "Cross Verification of Scrap disposal vs PCB limit", "fromTo": None, "executingDept": {"All Wastes": "EHS"}, "authorization": "EHS", "acknowledgement": "Stores", "page": 4},
            {"no": 6, "process": "Request for disposal", "fromTo": "Scrap Yard", "executingDept": {"Metal Scrap": "Stores", "Product/ Process": "Stores", "Used Packing Scrap": "Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "PEM/ Stores/ IT", "Wooden Scrap": "Admin", "Used Oil": "PEM"}, "authorization": "Individual HOD/ Functional Head", "acknowledgement": "EHS", "page": 4},
            {"no": 7, "process": "Preparation of DC / Gate Pass", "fromTo": None, "executingDept": {"All Wastes": "Stores"}, "authorization": "Stores HOD", "acknowledgement": "Security", "page": 4},
            {"no": 8, "process": "Preparation of Invoice", "fromTo": None, "executingDept": {"All Wastes": "Finance"}, "authorization": "Plant Finance Head", "acknowledgement": "Plant Stores", "page": 4},
            {"no": 9, "process": "Weighment of Hazardous Scrap (Before Disposal)", "fromTo": "Scrap Yard", "executingDept": {"Hazardous Waste": "Security"}, "authorization": "Individual HOD/ Functional Head", "acknowledgement": "Stores In-Charge", "page": 4},
            {"no": 10, "process": "Disposal of Scrap to PCB Authorized Vendor (Within 180 days)", "fromTo": "Scrap Yard to Vendor", "executingDept": {"Metal Scrap": "Stores", "Product/ Process": "Stores", "Used Packing Scrap": "Admin", "Garbage": "Admin", "Hazardous Waste": "PEM/ EHS", "e-waste": "PEM/ IT", "Wooden Scrap": "Admin", "Used Oil": "PEM"}, "authorization": "Plant Finance Head/ Security", "acknowledgement": "EHS", "page": 4},
            {"no": 11, "process": "FILING ANNUAL RETURNS (FORM-4) See rules 6(5),13(8),16(6) and 20(2) of Hazardous and other wastes 2016", "fromTo": None, "executingDept": {"": "DP Plants- EHS, Assembly - PEM"}, "authorization": "EHS/ PEM Head", "acknowledgement": "PCB", "page": 4},
            {"no": 12, "process": "Bio Medical Waste Annual Return (FORM - 4) (See rule 13)", "fromTo": None, "executingDept": {"": "Admin"}, "authorization": "Admin Head", "acknowledgement": "PCB", "page": 4},
            {"no": 13, "process": "FORM 5 PCB- ENVIRONMENT STATEMENT", "fromTo": None, "executingDept": {"": "DP Plants- EHS, Assembly - PEM"}, "authorization": "EHS/ PEM Head", "acknowledgement": "PCB", "page": 4},
            {"no": 14, "process": "FORM - 8 FORM FOR FILING RETURNS FOR BULK CONSUMER OF BATTERIES", "fromTo": None, "executingDept": {"": "PEM"}, "authorization": "PEM- Head", "acknowledgement": "PCB", "page": 4},
            {"no": 15, "process": "FORM 9- TREM card used for transportation", "fromTo": None, "executingDept": {"": "PEM"}, "authorization": "PEM- Head", "acknowledgement": "PCB", "page": 4},
            {"no": 16, "process": "Manifest for Hazardous and Other Waste (FORM- 10)", "fromTo": None, "executingDept": {"": "PEM"}, "authorization": "PEM- Head", "acknowledgement": "PCB", "page": 4},
            {"no": 17, "process": "Centralized Review of the Scrap Management", "fromTo": "Provision in dashboard for reviewing", "executingDept": {"": "Provision in dashboard for reviewing"}, "authorization": "Provision in dashboard for reviewing", "acknowledgement": "Corporate Function - PEM, EHS, Stores, Admin, IT, Operations, Sustainability Team", "page": 4},
        ]},
    "masters": {
        "page": 5, "section": "Masters required for the system",
        "items": ["PCB Master, PCB certification, expiry details", "Vendor details, Vendor Master and PCB certification, expiry details", "Waster/Scrap type"],
        "sampleMasterColumns": ["Type of Waste", "PCB Consent taken for disposal", "UOM", "Validity From (As per PCB Consent)", "Validity To (As per PCB Consent)", "As per PCB Consent"],
        "sampleMasterRows": [["E-Waste", "Battery", "Ton Kgs."], ["E-Waste", "E-Waste", "Ton Kgs."], ["Hazardous", "Hazardous", "Ton Kgs."], ["Non-Hazardous", "Empty Cans/Barells", "Ton Kgs."], ["Hazardous", "Treated Effluent Water", "Kilo litres"], ["Non-Hazardous", "Treated Sewage Water", "Kilo litres"], ["Hazardous", "Used Oil", "Kilo litres"]],
        "sampleHeader": "PCB Details - To Input (Plant: TASL-HYD; CTO/CFO Reference Number; Industry Category; Date)"},
    "notStated": ["Targets or quantified benefits: none are stated on the pages that were read (2-5)."],
}


def _docx_path():
    return find_source_file("scrap generation", "digitalization", "scrap", suffix=".docx")


def _pdf_path():
    return find_source_file("scrap generation", "brd", suffix=".pdf")


_NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NS_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _in_fallback(el) -> bool:
    return any(a.tag == _NS_MC + "Fallback" for a in el.iterancestors())


def _mendix_sections() -> dict:
    """Mendix document as sections: headings, list text, responsibility-matrix tables, flow-chart
    boxes (text boxes in document order, the duplicate 'fallback' copies skipped) and image ids."""
    path = _docx_path()
    if path is None:
        return {"sections": [], "images": [], "notes": ["'Digitalization of Scrap Management- Mendix App.docx' not found."]}
    import docx  # local import: only needed here
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(str(path))
    rel_target = {rid: rel.target_ref for rid, rel in doc.part.rels.items() if "image" in rel.reltype}
    images: list[str] = []
    sections: list[dict] = []

    def section(title: str) -> dict:
        s = {"title": title, "text": [], "tables": [], "flowCharts": [], "imageIds": []}
        sections.append(s)
        return s

    cur = section("(start)")
    for el in doc.element.body.iterchildren():
        if el.tag == _NS_W + "p":
            p = Paragraph(el, doc)
            style = p.style.name if p.style is not None else ""
            if style.startswith("Heading") and p.text.strip():
                cur = section(p.text.strip())
                continue
            if p.text.strip():
                cur["text"].append(p.text.strip())
            boxes = []
            for tb in el.iter(_NS_W + "txbxContent"):
                if _in_fallback(tb):
                    continue
                # Join w:t runs only: itertext() on python-docx's element classes repeats run text.
                t = " ".join(x.strip() for x in ("".join(r.text or "" for r in q.iter(_NS_W + "t")) for q in tb.iter(_NS_W + "p")) if x.strip())
                if t:
                    boxes.append(t)
            if boxes:
                cur["flowCharts"].append({"boxes": boxes, "note": "Text boxes of one drawing in document order; connectors/arrows are not text, so order is not the flow order."})
            for blip in el.iter(_NS_A + "blip"):
                if _in_fallback(blip):
                    continue
                target = rel_target.get(blip.get(_NS_R + "embed"))
                if target:
                    if target not in images:
                        images.append(target)
                    cur["imageIds"].append(images.index(target) + 1)
        elif el.tag == _NS_W + "tbl":
            t = Table(el, doc)
            rows = [[c.text.replace("\n", " / ").strip() for c in row.cells] for row in t.rows]
            cur["tables"].append({"rows": rows, "firstRowIsHeader": False})
    sections = [s for s in sections if s["text"] or s["tables"] or s["flowCharts"] or s["imageIds"] or s["title"] != "(start)"]
    return {"sections": sections, "images": images, "file": path.name, "notes": []}


def mendix_image(n: int) -> tuple[bytes, str] | None:
    path = _docx_path()
    data = _mendix_sections()
    if path is None or not (1 <= n <= len(data["images"])):
        return None
    target = data["images"][n - 1]
    with zipfile.ZipFile(path) as z:
        name = "word/" + target if not target.startswith("word/") else target
        ext = name.rsplit(".", 1)[-1].lower()
        return z.read(name), {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}.get(ext, "application/octet-stream")


def initiative() -> dict:
    """The scrap digitalisation initiative from the BRD (pdf) and the Mendix document (docx)."""
    notes: list[str] = []
    pdf = _pdf_path()
    pages = None
    if pdf is not None:
        try:
            from pypdf import PdfReader
            r = PdfReader(str(pdf))
            pages = len(r.pages)
            text = "".join((p.extract_text() or "") for p in r.pages).strip()
            if text:
                notes.append("The BRD PDF now has a text layer; the transcription below was read from the scan and has not been re-checked against it.")
        except Exception as exc:  # noqa: BLE001 - never fail the page
            notes.append(f"BRD PDF could not be opened ({exc}).")
    else:
        notes.append("'BRD EHS- Scrap Management & Water Balance Solution' PDF not found.")
    notes.append("The BRD is scanned images: pypdf returns no text and no OCR is installed. Pages 2-5 were read by eye and "
                 "transcribed (see `brd.transcription`); the remaining pages are available only as the PDF.")
    mx = _mendix_sections()
    notes += mx["notes"]
    notes.append("The Mendix document states no quantified targets or benefits; none are returned.")
    return {
        "brd": {"file": pdf.name if pdf else None, "pages": pages, "pdfUrl": "/api/scrap/documents/brd.pdf" if pdf else None,
                "textLayer": False, "pagesTranscribed": BRD_PAGES_READ, "transcription": BRD_TRANSCRIPTION if pdf else None},
        "mendix": {"file": mx.get("file"), "sections": mx["sections"],
                   "images": [{"id": i + 1, "url": f"/api/scrap/documents/mendix/images/{i + 1}", "file": t}
                              for i, t in enumerate(mx["images"])]},
        "targetsOrBenefits": [],
        "notes": notes,
    }


# --------------------------------------------------------------------------------------
# Overview (home page)
# --------------------------------------------------------------------------------------

def overview() -> dict:
    t = totals()
    company = t["companyYear"]
    fys = sorted({r["fy"] for r in company})
    latest = None
    for f in reversed(fys):
        if any(r["fy"] == f and (r["weightKg"] or r["volumeL"]) for r in company):
            latest = f
            break
    latest_rows = [r for r in company if r["fy"] == latest]
    cluster = [r for r in t["clusterYear"] if r["fy"] == latest]
    plants = {}
    for r in t["plantYear"]:
        if r["fy"] == latest:
            if r["weightKg"] is None and r["volumeL"] is None:
                continue
            p = plants.setdefault(r["plant"], {"plant": r["plant"], "plantName": r["plantName"], "weightKg": 0.0, "volumeL": 0.0})
            p["weightKg"] += r["weightKg"] or 0
            p["volumeL"] += r["volumeL"] or 0
    for p in plants.values():
        p["weightKg"], p["volumeL"] = _round(p["weightKg"]), _round(p["volumeL"])
    tg = targets()
    landfill = next((x for x in tg["targets"] if x["financialYear"] == latest), None)
    notes = list(t["notes"][:1]) if not company else []
    notes.append("Hazardous and e-waste only: the sources hold no non-hazardous scrap quantities.")
    return {
        "latestFy": latest, "unit": {"weight": "kg", "volume": "L"},
        "company": {"company": "TADL", "byCategory": latest_rows},
        "clusters": cluster,
        "plants": [plants[p] for p in PLANT_IDS if p in plants],
        "yearly": [{"fy": f, "weightKg": _round(sum(r["weightKg"] or 0 for r in company if r["fy"] == f)),
                    "volumeL": _round(sum(r["volumeL"] or 0 for r in company if r["fy"] == f))} for f in fys],
        "latestFyMonthsReported": max((r["monthsReported"] for r in t["plantYear"] if r["fy"] == latest), default=0),
        "target": landfill,
        "notes": notes,
    }


# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------

@router.get("/overview")
def route_overview() -> dict:
    return overview()


@router.get("/quantities")
def route_quantities(plant: str | None = None, fy: str | None = None, category: str | None = None) -> dict:
    return quantities(plant, fy, category)


@router.get("/totals")
def route_totals(fy: str | None = None) -> dict:
    return totals(fy)


@router.get("/disposal")
def route_disposal(plant: str | None = None) -> dict:
    return disposal_methods(plant)


@router.get("/targets")
def route_targets() -> dict:
    return targets()


@router.get("/initiative")
def route_initiative() -> dict:
    return initiative()


@router.get("/documents/brd.pdf")
def route_brd_pdf():
    p = _pdf_path()
    if p is None:
        raise HTTPException(404, "BRD PDF not found in the source folder")
    return FileResponse(p, media_type="application/pdf", filename=p.name)


@router.get("/documents/mendix/images/{n}")
def route_mendix_image(n: int):
    got = mendix_image(n)
    if got is None:
        raise HTTPException(404, "No such image")
    return Response(content=got[0], media_type=got[1])
