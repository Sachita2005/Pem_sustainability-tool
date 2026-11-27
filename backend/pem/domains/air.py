"""Air monitoring: readings from "Air Monitoring data" and the conditions of the TBAL consent order.

Sources
* Workbook "Sustainability Calculation" (primary) and "Sustainability (Only to Enter)" (fills cells the
  primary leaves blank, marked "source": "entry"), sheet "Air Monitoring data": one value per plant,
  parameter and month (the sheet has no day-level dates and no location names beyond the stack /
  ambient wording in the parameter label).
* The consent order "10. Tata Boeing Aerospace Ltd (EXP) - CFO Order - July 2024.docx" (Telangana
  Pollution Control Board, order dated 19.07.2024). It is for Tata Boeing Aerospace Ltd, i.e. plant
  TBAL, so its limits are applied to TBAL only; the sheet itself states no limits.

Entry-workbook zeros are not used to fill blanks: the entry sheet is pre-filled with 0.0 for parameters
that are not measured (for example ambient PM10 = 0.0), and a zero concentration is not a reading.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from fastapi import APIRouter

from ..config import find_source_file
from ..plants import PLANT_IDS, canonical_plant, plant_name
from ..workbooks import calculation_workbook, entry_workbook, find_sheet, read_sheet, to_number

router = APIRouter(prefix="/api/air", tags=["air"])

CONSENT_PLANT = "TBAL"
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _month_header(value: Any) -> tuple[int, int] | None:
    """(year, month); headers typed as dates carry the two-digit year in the day ("2025-01-26" = Jan 2026)."""
    if isinstance(value, (dt.datetime, dt.date)):
        return 2000 + value.day, value.month
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]*-(\d\d)", str(value or "").strip())
    if m and m[1].lower() in _MONTHS:
        return 2000 + int(m[2]), _MONTHS[m[1].lower()]
    return None


def _fy(start_year: int) -> str:
    return f"FY{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def _blocks(header: list[Any]) -> list[dict]:
    """Year blocks of 12 consecutive month columns. A block that repeats an earlier FY is flagged duplicate."""
    cols = [(j, _month_header(v)) for j, v in enumerate(header)]
    out, run, seen = [], [], set()
    for j, p in cols + [(len(header), None)]:
        if p is not None and (not run or j == run[-1][0] + 1):
            run.append((j, p))
            continue
        if len(run) >= 12:
            first = run[0][1]
            sy = first[0] if first[1] == 4 else first[0] - 1
            months = [(c, f"{sy + (1 if k >= 9 else 0):04d}-{(k + 3) % 12 + 1:02d}") for k, (c, _p) in enumerate(run[:12])]
            out.append({"fy": _fy(sy), "months": months, "duplicate": _fy(sy) in seen})
            seen.add(_fy(sy))
        run = [(j, p)] if p is not None else []
    return out


# Parameter label -> (location, parameter, kind). The sheet puts location into the label.
def classify(label: str) -> dict:
    low = label.lower()
    if "ambient" in low:
        loc, kind = "Ambient", "ambient"
    elif "dg stack" in low:
        loc, kind = "DG stack", "stack"
    elif "hwg" in low:
        loc, kind = "HWG stack", "stack"
    elif "paint booth" in low:
        loc, kind = "Paint booth", "stack"
    else:
        loc, kind = None, "emission"
    if "pm 10" in low or "pm10" in low:
        param = "PM10"
    elif "pm 2.5" in low or "pm2.5" in low:
        param = "PM2.5"
    elif "particulate" in low:
        param = "Particulate matter (SPM)" if "suspended" in low else "Particulate matter (total)"
    else:
        param = re.sub(r"\s+", " ", label).strip()
    return {"location": loc, "parameter": param, "kind": kind}


def _parse(grid: list[list[Any]], source: str) -> tuple[list[dict], list[str]]:
    notes: list[str] = []
    hi = next((i for i, r in enumerate(grid[:15]) if str(r[1] or "").strip().lower() == "plant" and str(r[0] or "").lower().startswith("sl")), None)
    if hi is None:
        return [], [f"Header row not found in 'Air Monitoring data' ({source} workbook)."]
    blocks = _blocks(grid[hi])
    for b in blocks:
        if b["duplicate"]:
            notes.append(f"{source} workbook: a second block for {b['fy']} repeats an earlier one and is ignored.")
    rows, plant, plant_label = [], None, None
    for r in grid[hi + 1:]:
        if r[1] not in (None, ""):
            plant_label = str(r[1]).strip()
            plant = canonical_plant(plant_label) or plant_label
        label = str(r[2] or "").strip()
        if not label or plant is None or label.lower().startswith("plant total"):
            continue
        unit = str(r[3]).replace("�", "µ").strip() if r[3] else None
        for b in blocks:
            if b["duplicate"]:
                continue
            for c, mk in b["months"]:
                raw = r[c]
                value = to_number(raw)
                na = isinstance(raw, str) and raw.strip().upper() == "NA"
                if value is None and not na:
                    continue
                rows.append({"plant": plant, "plantLabel": plant_label, "sheetLabel": label, **classify(label), "unit": unit,
                             "month": mk, "fy": b["fy"], "value": value, "notApplicable": na, "source": source})
    return rows, notes


def _merged() -> tuple[list[dict], list[str]]:
    """Primary workbook readings, plus entry-workbook readings only where the primary cell is blank."""
    notes: list[str] = []
    prim: list[dict] = []
    ent: list[dict] = []
    for tag, path in (("calculation", calculation_workbook()), ("entry", entry_workbook())):
        if path is None:
            notes.append(f"The {tag} workbook was not found.")
            continue
        name = find_sheet(path, "air monitoring")
        if not name:
            notes.append(f"Sheet 'Air Monitoring data' not found in the {tag} workbook.")
            continue
        rows, n = _parse(read_sheet(path, name), tag)
        notes += n
        (prim if tag == "calculation" else ent).extend(rows)
    have = {(r["plant"], r["sheetLabel"], r["month"]) for r in prim}
    filled = skipped_zero = 0
    for r in ent:
        key = (r["plant"], r["sheetLabel"], r["month"])
        if key in have or r["notApplicable"]:
            continue
        if r["value"] == 0:
            skipped_zero += 1
            continue
        prim.append({**r, "source": "entry"})
        filled += 1
    if skipped_zero:
        notes.append(f"{skipped_zero} zero values in the entry workbook were not used to fill blanks (zero is the sheet's placeholder for 'not measured').")
    return prim, notes


# --------------------------------------------------------------------------------------
# Consent order (docx)
# --------------------------------------------------------------------------------------

def _consent_path():
    return find_source_file("air monitoring", "cfo", suffix=".docx")


def _paragraphs(path) -> list[tuple[str, int]]:
    """Non-empty paragraph texts with the page they sit on (the order prints 'Page n of 5' at each page end)."""
    import docx
    out, page = [], 1
    for p in docx.Document(str(path)).paragraphs:
        t = p.text.strip()
        if not t:
            continue
        out.append((t, page))
        if re.fullmatch(r"Page \d+ of \d+", t):
            page += 1
    return out


def _find(paras: list[tuple[str, int]], pattern: str) -> tuple[re.Match, int] | None:
    for t, pg in paras:
        m = re.search(pattern, t, flags=re.I)
        if m:
            return m, pg
    return None


def consent_conditions() -> dict:
    """Key conditions of the consent order, each with the page it was read from and the order's wording."""
    path = _consent_path()
    if path is None:
        return {"order": None, "conditions": [], "wasteAuthorisation": [],
                "notes": ["Consent order document (CFO order, Tata Boeing Aerospace Ltd) not found."]}
    paras = _paragraphs(path)
    notes: list[str] = []
    src = {"document": path.name}

    def fact(group: str, name: str, pattern: str, value_fn=None, unit: str | None = None, extra: dict | None = None):
        hit = _find(paras, pattern)
        if hit is None:
            notes.append(f"Not found in the order: {name}.")
            return None
        m, pg = hit
        val = value_fn(m) if value_fn else m.group(0)
        return {"group": group, "name": name, "value": val, "unit": unit, "wording": m.group(0).strip(), "page": pg, **(extra or {}), **src}

    conds = [
        fact("validity", "Order date", r"Date:\s*(\d\d\.\d\d\.\d{4})", lambda m: m.group(1)),
        fact("validity", "Consent and HW authorization valid until", r"valid for a period ending with the (\d\d\.\d\d\.\d{4})", lambda m: m.group(1)),
        fact("validity", "Consent fee paid up to", r"paid consent fee upto (\d\d\.\d\d\.\d{4})", lambda m: m.group(1)),
        fact("validity", "Previous order cancelled (was valid up to)", r"valid upto (\d\d\.\d\d\.\d{4}) stands cancelled", lambda m: m.group(1)),
        fact("validity", "Renewal application at least before expiry", r"at least (\d+) days before the date of expiry", lambda m: int(m.group(1)), "days"),
        fact("validity", "Auto-renewal application at least before expiry", r"Auto Renewal of the CFO atleast (\d+) days", lambda m: int(m.group(1)), "days"),
        fact("capacity", "Permitted production capacity", r"(\d+ Nos/Anuum)", lambda m: m.group(1)),
        fact("effluent", "Effluent standards (pH)", r"pH-([\d.]+ to [\d.]+)", lambda m: m.group(1)),
        fact("effluent", "Effluent standard TSS", r"TSS\s*[–-]\s*(\d+)\s*mg/L", lambda m: float(m.group(1)), "mg/L"),
        fact("effluent", "Effluent standard BOD", r"BOD\s*[–-]\s*(\d+)\s*mg/L", lambda m: float(m.group(1)), "mg/L"),
        fact("effluent", "Effluent standard Oil & Grease", r"Oil & Grease\s*-\s*(\d+)", lambda m: float(m.group(1)), "mg/L"),
        fact("emission", "Emission standard SPM (chimneys)", r"SPM\s*-\s*(\d+)\s*mg/Nm3", lambda m: float(m.group(1)), "mg/Nm3"),
        fact("noise", "Noise day time (6 AM to 10 PM)", r"Day time \(6 AM to 10 PM\) - (\d+) dB", lambda m: float(m.group(1)), "dB(A)"),
        fact("noise", "Noise night time (10 PM to 6 AM)", r"Night time \(10 PM to 6 AM\) - (\d+) dB", lambda m: float(m.group(1)), "dB(A)"),
        fact("other", "Hazardous waste storage not more than", r"not store hazardous waste for more than (\d+) days", lambda m: int(m.group(1)), "days"),
        fact("other", "Green belt share of total area", r"develop (\d+)% of the total area", lambda m: float(m.group(1)), "%"),
        fact("other", "Environment statement (Form V) due before", r"Form V before (30th September)", lambda m: m.group(1)),
    ]
    conds = [c for c in conds if c]
    _ambient(paras, conds, src, notes)
    _effluent_outlets(paras, conds, src, notes)
    _water(paras, conds, src, notes)
    waste = _waste_authorisation(paras, src, notes)
    order = {"issuer": "Telangana Pollution Control Board, Zonal Office Hyderabad",
             "orderNo": (_find(paras, r"Order No\.\s*([^(]+)\(") or [None])[0].group(1).strip() if _find(paras, r"Order No\.\s*([^(]+)\(") else None,
             "holder": "M/s. Tata Boeing Aerospace Ltd.", "plant": CONSENT_PLANT, "plantName": plant_name(CONSENT_PLANT),
             "site": "D.No.1/D, Survey No.656, Aditya Nagar, Adibatla (V), Ibrahimpatnam (M), Rangareddy District",
             "pages": 5, "document": path.name}
    notes.append("Chimney table: the extracted text lists one SPM standard (115 mg/Nm3) for the chimney table without tying it to a "
                 "single chimney; it is applied to TBAL stack SPM readings (DG stack, paint booth) and stated as such.")
    notes.append("The order is for TBAL only; the sheets give no limits and no other plant's consent order is in the source folder, "
                 "so other plants have no limit and are never flagged.")
    return {"order": order, "conditions": conds, "wasteAuthorisation": waste, "notes": notes}


def _ambient(paras, conds, src, notes) -> None:
    """NAAQS table: parameter names then the 'Standards in μg/m3' column in the same order."""
    texts = [t for t, _ in paras]
    try:
        i = next(k for k, t in enumerate(texts) if t.startswith("Parameters"))
        j = next(k for k, t in enumerate(texts) if t.startswith("Standards in"))
    except StopIteration:
        notes.append("Ambient air standards table not found in the order.")
        return
    names = [t for t in texts[i + 1:j] if not re.fullmatch(r"\d+|S\.No", t)]
    vals = [t for t in texts[j + 1:j + 1 + len(names)]]
    pg = paras[j][1]
    if len(names) != 4 or not all(re.fullmatch(r"\d+", v) for v in vals):
        notes.append("Ambient air standards table could not be read reliably; no ambient limits returned.")
        return
    for n, v in zip(names, vals):
        conds.append({"group": "ambient", "name": f"Ambient {n}", "value": float(v), "unit": "µg/m3",
                      "wording": f"{n} - {v} (Standards in μg/m3)", "page": pg, **src})


def _effluent_outlets(paras, conds, src, notes) -> None:
    texts = [t for t, _ in paras]
    hit = _find(paras, r"Max Daily Discharge")
    kld = [(t, p) for t, p in paras if re.fullmatch(r"(?:\(After Expansion\) )?\d+(?:\.\d+)? KLD", t)]
    names = [t for t in texts if t in ("Canteen", "Domestic")]
    if hit and len(kld) >= 2 and names[:2] == ["Canteen", "Domestic"]:
        for n, (t, p) in zip(names[:2], kld[:2]):
            v = float(re.search(r"\d+(?:\.\d+)?", t).group(0))
            conds.append({"group": "effluent", "name": f"Max daily discharge - {n} outlet", "value": v, "unit": "KLD",
                          "wording": t, "page": p, **src})
    else:
        notes.append("Effluent outlet table not read (layout differs from expected).")


def _water(paras, conds, src, notes) -> None:
    texts = [(t, p) for t, p in paras]
    purposes = ["Floor Mopping", "Process (Fire Storage Tank)", "Canteen", "Domestic", "Green Belt", "Total"]
    start = next((k for k, (t, _) in enumerate(texts) if t == "Quantity (After Expansion)"), None)
    if start is None:
        notes.append("Water consumption limits table not found.")
        return
    vals = [(t, p) for t, p in texts[start + 1:start + 7] if re.fullmatch(r"\d+(?:\.\d+)? KLD", t)]
    if len(vals) != 6:
        notes.append("Water consumption limits table could not be read reliably.")
        return
    nums = [float(v.split()[0]) for v, _ in vals]
    if abs(sum(nums[:5]) - nums[5]) > 1e-9:
        notes.append("Water consumption limits do not add up to the stated total; not returned.")
        return
    for n, (t, p), v in zip(purposes, vals, nums):
        conds.append({"group": "water", "name": f"Water consumption limit - {n}", "value": v, "unit": "KLD", "wording": t, "page": p, **src})


# Hazardous waste authorisation (Form 2). The order's text for this table is scattered in the extraction, so each
# line is defined by name and quantity pattern and is kept only when both are found in the order.
_WASTE_SPEC = [
    ("Waste/ Used Oil", r"360 LPM", "5.1 of Sch-I"),
    ("STP Sludge", r"200 kgs/month", None),
    ("Cotton Waste", r"1500 Kgs/month", "5.2 of Sch - I"),
    ("Residues of Paints, Sealants & Adhesives", r"500 Kgs/month", "21.1 of Sch - I"),
    ("Containers of paints, sealants & adhesives", r"500 Kgs/month", "33.3 of Sch - I"),
    ("Expired Perishable hazardous materials", r"300 Kgs/month", "33.1 of Sch - I"),
    ("Packing material", r"7000 Kgs/month", None),
    ("Used Water based Coolant", r"200 LPM", None),
    ("Paint and oil choked filters", r"500 Kg/month", "5.2 of Sch-I"),
]


def _waste_authorisation(paras, src, notes) -> list[dict]:
    out = []
    for name, qty_pat, code in _WASTE_SPEC:
        key = name.split()[0].lower()
        if not any(key in t.lower() for t, _ in paras):
            notes.append(f"Waste line not found in the order: {name}.")
            continue
        hit = _find(paras, qty_pat)
        if hit is None:
            notes.append(f"Quantity for '{name}' not found in the order.")
            continue
        m = hit[0]
        num = float(re.match(r"\d+", m.group(0)).group(0))
        unit = "LPM" if "LPM" in m.group(0) else "kg/month"
        out.append({"waste": name, "quantity": num, "unit": unit, "scheduleCode": code, "page": hit[1], **src})
    notes.append("Hazardous waste table: the quantity of each line was matched to its waste name from the order's text, which the "
                 "document extraction scrambles; check against page 1-2 of the order before relying on a single line.")
    return out


# --------------------------------------------------------------------------------------
# Limits applied to readings
# --------------------------------------------------------------------------------------

def limits() -> list[dict]:
    """Limits usable against sheet readings, all from the TBAL consent order (None when it could not be read)."""
    cond = {c["name"]: c for c in consent_conditions()["conditions"]}
    out = []

    def add(name, where_parameter, where_location, where_kind):
        c = cond.get(name)
        if c:
            out.append({"plant": CONSENT_PLANT, "parameter": where_parameter, "location": where_location, "kind": where_kind,
                        "limit": c["value"], "unit": c["unit"], "comparison": "above", "page": c["page"], "document": c["document"],
                        "condition": c["name"]})
    add("Ambient Particulate Matter(PM10)", "PM10", "Ambient", "ambient")
    add("Ambient Particulate Matter (PM2.5)", "PM2.5", "Ambient", "ambient")
    for loc in ("DG stack", "Paint booth"):
        add("Emission standard SPM (chimneys)", "Particulate matter (SPM)", loc, "stack")
    return out


def _limit_for(row: dict, lims: list[dict]) -> dict | None:
    return next((l for l in lims if l["plant"] == row["plant"] and l["parameter"] == row["parameter"] and l["location"] == row["location"]), None)


def _fy_of(month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    return _fy(y if m >= 4 else y - 1)


def readings(plant: str | None = None, fy: str | None = None, parameter: str | None = None,
             location: str | None = None, month: str | None = None, include_na: bool = False) -> dict:
    rows, notes = _merged()
    lims = limits()
    pid = canonical_plant(plant) if plant else None
    out = []
    for r in rows:
        if plant and r["plant"] != (pid or plant):
            continue
        if fy and r["fy"] != fy:
            continue
        if month and r["month"] != month:
            continue
        if parameter and parameter.lower() not in (r["parameter"] + " " + r["sheetLabel"]).lower():
            continue
        if location and (r["location"] or "").lower() != location.lower():
            continue
        if r["notApplicable"] and not include_na:
            continue
        lim = _limit_for(r, lims)
        # The order states ambient limits in µg/m3 and the sheet leaves the ambient unit blank on some rows;
        # a unit that is stated and different (e.g. ppm) is not compared.
        comparable = lim is not None and r["value"] is not None and (r["unit"] in (None, lim["unit"], "µg/m3", "mg/Nm3"))
        out.append({
            "plant": r["plant"], "plantName": plant_name(r["plant"]) if r["plant"] in PLANT_IDS else r["plantLabel"],
            "location": r["location"], "parameter": r["parameter"], "sheetLabel": r["sheetLabel"],
            "month": r["month"], "fy": r["fy"], "value": r["value"], "unit": r["unit"], "notApplicable": r["notApplicable"],
            "limit": lim["limit"] if lim else None, "limitUnit": lim["unit"] if lim else None,
            "limitSource": f"{lim['document']}, page {lim['page']}" if lim else None,
            "aboveLimit": (r["value"] > lim["limit"]) if comparable else None,
            "source": r["source"],
        })
    out.sort(key=lambda x: (x["plant"], x["parameter"], x["location"] or "", x["month"]))
    if not out and not notes:
        notes.append("No readings for this selection.")
    notes += [
        "Readings are monthly values per plant and parameter; the sheet has no day-level dates or sampling-point names.",
        "'value' is null with notApplicable=true where the sheet says NA (only returned with include_na=true).",
        "Units are as the sheet states them and differ between plants for the same parameter (mg/m3, mg/Nm3, ppm, µg/m3).",
        "aboveLimit is null when no limit is stated for that plant/parameter (only TBAL has a consent order in the source folder).",
    ]
    return {"readings": out, "count": len(out), "notes": notes}


def overview() -> dict:
    data = readings()
    rows = data["readings"]
    flagged = [r for r in rows if r["aboveLimit"]]
    latest = max((r["month"] for r in rows), default=None)
    per_plant: dict[str, dict] = {}
    for r in rows:
        p = per_plant.setdefault(r["plant"], {"plant": r["plant"], "plantName": r["plantName"], "readings": 0, "lastMonth": None, "aboveLimit": 0})
        p["readings"] += 1
        p["lastMonth"] = max(p["lastMonth"] or "", r["month"])
        p["aboveLimit"] += 1 if r["aboveLimit"] else 0
    cond = consent_conditions()
    valid = next((c for c in cond["conditions"] if c["name"] == "Consent and HW authorization valid until"), None)
    return {
        "readings": len(rows), "latestMonth": latest, "readingsAboveLimit": len(flagged),
        "flagged": [{k: r[k] for k in ("plant", "parameter", "location", "month", "value", "unit", "limit", "limitUnit")} for r in flagged][:20],
        "plants": [per_plant[p] for p in PLANT_IDS if p in per_plant],
        "consent": {"plant": CONSENT_PLANT, "validUntil": valid["value"] if valid else None,
                    "document": cond["order"]["document"] if cond["order"] else None},
        "notes": data["notes"][:1] if not rows else ["Limits exist only for TBAL (consent order); other plants are not checked."],
    }


@router.get("/overview")
def route_overview() -> dict:
    return overview()


@router.get("/readings")
def route_readings(plant: str | None = None, fy: str | None = None, parameter: str | None = None,
                   location: str | None = None, month: str | None = None, include_na: bool = False) -> dict:
    return readings(plant, fy, parameter, location, month, include_na)


@router.get("/limits")
def route_limits() -> dict:
    c = consent_conditions()
    return {"limits": limits(), "order": c["order"], "conditions": c["conditions"],
            "wasteAuthorisation": c["wasteAuthorisation"], "notes": c["notes"]}
