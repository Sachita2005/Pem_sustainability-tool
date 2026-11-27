"""Production-linked energy: machine families per site, kWh per unit of production.

Source: "Power Consumption wrt Production Units.xlsx" - one sheet per site (TSAL, TASL-B01,
TBAL, TASL H07, TLMAL, TASL NGP, TASL-H01, TASL-H06). Every sheet is laid out for people:
stacked blocks (one per machine family) with months across, or - on the TLMAL sheet - blocks
side by side with months down. The parser finds blocks by their month header, never by cell
address, so a new block or month keeps working.

Targets: the workbook has none. They exist only as "Target vs Actual" line charts in the
Sustainability deck (slides for TASL-H01, TLMAL and TASL-MCA); we read those chart series.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .. import workbooks as wb
from ..config import find_source_file
from ..plants import PLANT_IDS, plant_name

router = APIRouter(prefix="/api/production", tags=["production"])

MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
ENTRY_SHEET = "Power Cons wrt Production units"

# --------------------------------------------------------------------------- site -> plant
# Evidence is stated here because the mapping is a judgement call; confidence is per site.
SITE_DEFS: dict[str, dict[str, Any]] = {
    "tsal": {
        "code": "TSAL", "plant": "TSAL", "confidence": "high",
        "evidence": "Sheet is named after the plant; the 'Only to Enter' sheet carries the same 11 TSAL families "
                    "with identical values.",
    },
    "taslb01": {
        "code": "B01", "plant": "TASL-BLR", "confidence": "medium-high",
        "evidence": "B01 is the Bangalore site code in the source (Employees sheet 'Tata Advanced Systems Limited - B01', "
                    "'TASL B01' in TADL Total Empl Count; plants.py alias). No chart or total ties the sheet to Bangalore numerically; "
                    "the sheet's Apr-25 kWh (compressors + CCA lines) is below TASL-Bangalore's plant total of 1,457,204 kWh.",
    },
    "tbal": {
        "code": "TBAL", "plant": "TBAL", "confidence": "high",
        "evidence": "Named after the plant. HVAC Bay-1/Bay-2 kWh equal the TBAL HVAC ECHO workbook "
                    "(Apr-25: 28,491 and 49,709 kWh).",
    },
    "taslh07": {
        "code": "H07", "plant": "TASL-MCA", "confidence": "high",
        "evidence": "Sustainability deck slide 46 (TASL-MCA Paint Booth) plots exactly the H07 'Paint Booth' kWh/1000 "
                    "for Apr-25..Jan-26 (15.82, 21.24, 28.20 ... 18.17 MWh). Slides 45-47 show MCA has HVAC, paint booth "
                    "and compressor - the three families on this sheet. The remaining unclaimed plant by elimination.",
    },
    "tlmal": {
        "code": "TLMAL", "plant": "TLMAL", "confidence": "high",
        "evidence": "Named after the plant; deck slides 40-44 (TLMAL HVAC, chemical lines, autoclave, compressor, oven) "
                    "reproduce this sheet's kWh/unit ratios.",
    },
    "taslngp": {
        "code": "N01", "plant": "TASL-NGP", "confidence": "high",
        "evidence": "Block titles carry 'N01' (Special Process Line N01, CCA N01, Paint Shop N01, ACFB Final Assembly N01); "
                    "N01 is the Nagpur code (Employees sheet 'Tata Advanced Systems Limited - N01'; plants.py alias).",
    },
    "taslh01": {
        "code": "H01", "plant": "TASL-HYD", "confidence": "high",
        "evidence": "Deck slides 32-39 are titled 'TASL- H01' and plot this sheet's kWh/run-hour as MWh/hr "
                    "(e.g. HVAC #1 Apr-25: 44.10 kWh/h = 0.0441 in the chart). Deck slide 6 ties 'TASL H01' to the "
                    "Hyderabad solar proposal; plants.py alias h01 -> TASL-HYD.",
    },
    "taslh06": {
        "code": "H06", "plant": "TCOE", "confidence": "medium",
        "evidence": "By elimination and scale: the other seven sheets map to seven plants, leaving TCOE and TASL-MCA; H07 is "
                    "proven MCA. H06's Apr-25 sheet total (about 987,000 kWh: HVAC bays, compressors, ALD/Ipsen/LVT "
                    "furnaces, Bay-1/Bay-2 machines) fits under TCOE's plant total of 1,171,827 kWh but would exceed "
                    "TASL-MCA's 241,412 kWh. Furnaces fit TCOE (Center of Excellence for Aero Engines). No document names H06 directly.",
    },
}


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _site_def(sheet: str) -> dict[str, Any]:
    d = SITE_DEFS.get(_norm(sheet))
    if d:
        return dict(d)
    return {"code": sheet, "plant": None, "confidence": "none",
            "evidence": "Sheet not recognised; kept under its own label."}


# --------------------------------------------------------------------------- months
def _month(value: Any) -> str | None:
    """A month header as "YYYY-MM", or None if the cell is not a month.

    The sheets were typed as "Apr-25"; Excel read that as 25 April of the *current* year, so a
    date whose day is not 1 stores the real year in its day number (26-Apr-2026 means Apr 2025).
    Text such as "Apr'25" or "Sept'25" is also accepted.
    """
    if isinstance(value, (dt.datetime, dt.date)):
        year = value.year if value.day == 1 else 2000 + value.day
        return f"{year:04d}-{value.month:02d}"
    if isinstance(value, str):
        m = re.fullmatch(r"\s*([A-Za-z]{3,9})[\s'\-]*(\d{2})\s*", value)
        if m and m.group(1)[:3].lower() in MONTH_NAMES:
            return f"20{m.group(2)}-{MONTH_NAMES.index(m.group(1)[:3].lower()) + 1:02d}"
    return None


def fy_of(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    start = y if m >= 4 else y - 1
    return f"FY{start % 100:02d}-{(start + 1) % 100:02d}"


# --------------------------------------------------------------------------- categories
_CATEGORY_RULES = [
    ("hvac", "hvac"), ("compres", "compressor"), ("paint", "paint"), ("booth", "paint"),
    ("chemical", "chemical-line"), ("anodiz", "chemical-line"), ("paa", "chemical-line"),
    ("special process", "chemical-line"), ("furnace", "furnace"), ("autoclave", "autoclave"),
    ("oven", "oven"), ("lighting", "lighting"), ("revenue", "revenue"), ("machines", "machines"),
    ("cca", "cca"), ("assembly", "assembly"),
]
CATEGORY_LABELS = {
    "hvac": "HVAC", "compressor": "Compressors", "paint": "Paint booths / shops",
    "chemical-line": "Chemical / anodizing / special-process lines", "furnace": "Furnaces",
    "autoclave": "Autoclave", "oven": "Oven", "lighting": "Lighting", "revenue": "Energy vs revenue",
    "machines": "Machines", "cca": "CCA lines", "assembly": "Assembly", "ship-set": "Per ship-set (SS)",
    "other": "Other",
}


def _category(title: str) -> str:
    low = title.lower()
    if re.search(r"\bss$", low.strip()):
        return "ship-set"
    for key, cat in _CATEGORY_RULES:
        if key in low:
            return cat
    return "other"


# --------------------------------------------------------------------------- parsing
@dataclass
class Series:
    label: str
    values: dict[str, float | None]
    sheet_total: float | None = None


@dataclass
class Block:
    title: str
    months: list[str]
    energy: Series | None = None
    production: Series | None = None
    ratio: Series | None = None
    extra: list[Series] = field(default_factory=list)
    orientation: str = "horizontal"


def _r(x: float | None) -> float | None:
    """Round away float noise from the sheet (24739.899999999998)."""
    return None if x is None else round(x, 6)


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _title_above(rows: list[list[Any]], r: int, c: int) -> str:
    """Block title: the nearest text cell above the header in the label column."""
    for rr in range(r - 1, max(r - 4, -1), -1):
        v = rows[rr][c]
        if isinstance(v, str) and v.strip() and _month(v) is None:
            return _clean(v)
    return ""


def _classify(rows_in_block: list[Series]) -> tuple[Series | None, Series | None, Series | None, list[Series]]:
    energy = production = ratio = None
    extra: list[Series] = []
    for s in rows_in_block:
        low = s.label.lower()
        if "/" in low:
            ratio = ratio or s
        elif energy is None and (low.startswith("kwh") or low.startswith("mwh")):
            energy = s
        elif production is None:
            production = s
        else:
            extra.append(s)
    return energy, production, ratio, extra


def _horizontal_blocks(rows: list[list[Any]]) -> list[Block]:
    blocks: list[Block] = []
    for r, row in enumerate(rows):
        month_cols = [(c, _month(v)) for c, v in enumerate(row) if _month(v)]
        if len(month_cols) < 3:
            continue
        first = month_cols[0][0]
        # months must be a contiguous run, otherwise it is a data row that happens to hold dates
        if [c for c, _ in month_cols] != list(range(first, first + len(month_cols))) or first == 0:
            continue
        label_col = first - 1
        header_label = row[label_col]
        title = _clean(header_label) if isinstance(header_label, str) and _clean(header_label).lower() != "month" else ""
        title = title or _title_above(rows, r, label_col)
        last = month_cols[-1][0]
        total_col = next((c for c in range(last + 1, min(last + 3, len(row)))
                          if isinstance(row[c], str) and row[c].strip().lower() in ("total", "average")), None)
        months = [m for _, m in month_cols]
        series: list[Series] = []
        rr = r + 1
        while rr < len(rows) and isinstance(rows[rr][label_col], str) and rows[rr][label_col].strip():
            if len([1 for v in rows[rr][first:last + 1] if _month(v)]) >= 3:
                break  # next header row without a blank line in between
            vals = {m: _r(wb.to_number(rows[rr][c])) for c, m in month_cols}
            total = _r(wb.to_number(rows[rr][total_col])) if total_col is not None else None
            series.append(Series(_clean(rows[rr][label_col]), vals, total))
            rr += 1
        if series:
            e, p, ra, ex = _classify(series)
            blocks.append(Block(title, months, e, p, ra, ex))
    return blocks


def _vertical_blocks(rows: list[list[Any]]) -> list[Block]:
    blocks: list[Block] = []
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if not (isinstance(v, str) and v.strip().lower() == "month"):
                continue
            if _month(row[c + 1] if c + 1 < len(row) else None):
                continue  # months run across: handled as a horizontal block
            month_rows = []
            rr = r + 1
            while rr < len(rows) and _month(rows[rr][c]):
                month_rows.append((rr, _month(rows[rr][c])))
                rr += 1
            if len(month_rows) < 3:
                continue
            title = _title_above(rows, r, c)
            series: list[Series] = []
            cc = c + 1
            while cc < len(row) and isinstance(row[cc], str) and row[cc].strip() and _month(row[cc]) is None:
                series.append(Series(_clean(row[cc]), {m: _r(wb.to_number(rows[i][cc])) for i, m in month_rows}))
                cc += 1
            if series:
                e, p, ra, ex = _classify(series)
                blocks.append(Block(title, [m for _, m in month_rows], e, p, ra, ex, "vertical"))
    return blocks


def parse_blocks(rows: list[list[Any]]) -> list[Block]:
    return _horizontal_blocks(rows) + _vertical_blocks(rows)


# --------------------------------------------------------------------------- deck targets
def _excel_serial_month(serial: Any) -> str | None:
    try:
        d = dt.date(1899, 12, 30) + dt.timedelta(days=int(float(serial)))
    except (TypeError, ValueError):
        return None
    return f"{d.year:04d}-{d.month:02d}"


# deck slide-title keyword -> sheet block title, per deck tag
_DECK_TAGS = {"TASL- H01": "taslh01", "TASL-H01": "taslh01", "TLMAL": "tlmal", "TASL-MCA": "taslh07"}
_DECK_FAMILY_RULES: dict[str, list[tuple[str, str]]] = {
    "tlmal": [("hvac", "HVAC"), ("chemical", "PAA Line"), ("autoclave", "Autoclave"),
              ("compressor", "Air Compresor (VSD type)"), ("oven", "Oven")],
    "taslh07": [("paint", "Paint Booth"), ("compressor", "Air Compresor")],
}


def _deck_path() -> Path | None:
    return find_source_file("common", "sustainability deck", suffix=".pptx")


@lru_cache(maxsize=4)
def _deck_charts_cached(path: str, mtime: float) -> list[dict[str, Any]]:
    from pptx import Presentation  # imported lazily: only needed for the deck

    out: list[dict[str, Any]] = []
    prs = Presentation(path)
    for no, slide in enumerate(prs.slides, 1):
        texts = [sh.text_frame.text.strip() for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
        title = next((t for t in texts if re.search(r"(Target|Plan)\s*Vs\s*Actual", t, re.I)), None)
        if not title:
            continue
        m = re.match(r"\s*(TASL-\s*H01|TASL-MCA|TLMAL)\s*:\s*(.+?)\s*\(", title.replace("\n", " "))
        if not m:
            continue  # "Plant Name: ..." sample slides carry placeholder numbers, not data
        chart = next((sh.chart for sh in slide.shapes if getattr(sh, "has_chart", False) and sh.has_chart), None)
        if chart is None:
            continue
        months = [_excel_serial_month(c) for c in chart.plots[0].categories]
        series = [{"name": s.name, "index": i, "values": [None if v is None else float(v) for v in s.values]}
                  for i, s in enumerate(chart.plots[0].series)]
        axis = None
        try:
            axis = chart.value_axis.axis_title.text_frame.text if chart.value_axis.has_title else None
        except Exception:  # noqa: BLE001 - axis title is optional decoration
            axis = None
        out.append({"slide": no, "title": _clean(title), "tag": _clean(m.group(1)).replace(" ", ""),
                    "family_text": _clean(m.group(2)), "months": months, "series": series, "axis": axis})
    return out


def deck_charts() -> list[dict[str, Any]]:
    p = _deck_path()
    if not p:
        return []
    try:
        return _deck_charts_cached(str(p), p.stat().st_mtime)
    except Exception:  # noqa: BLE001 - a broken deck must not break the page
        return []


def _deck_target(site_key: str, family_title: str, ratio: Series | None) -> dict[str, Any] | None:
    """The deck's target series for one family, converted to kWh per unit when it is verifiable."""
    for ch in deck_charts():
        tag = _DECK_TAGS.get(ch["tag"].replace("TASL-H01", "TASL- H01")) or _DECK_TAGS.get(ch["tag"])
        if tag != site_key:
            continue
        match = False
        low = ch["family_text"].lower()
        if site_key == "taslh01":
            if "revenue" in low:
                match = _norm(family_title) == "revenue"
            else:
                match = _norm(re.sub(r"energy", "", low, flags=re.I)) == _norm(family_title)
        else:
            for key, sheet_title in _DECK_FAMILY_RULES.get(site_key, []):
                if key in low:
                    match = _norm(family_title) == _norm(sheet_title)
                    break
        if not match:
            continue
        named = {s["name"].lower(): s for s in ch["series"]}
        target = named.get("target") or named.get("plan")
        actual = named.get("actual")
        inferred = False
        if target is None and len(ch["series"]) == 2:
            # TLMAL charts name both lines "MWH/<unit>": the line that tracks the sheet's ratio is the actual
            a, b = ch["series"]
            sheet = [v for v in (ratio.values.values() if ratio else []) if v is not None]
            if sheet:
                def err(s: dict[str, Any]) -> float:
                    vals = [v for v in s["values"] if v is not None][: len(sheet)]
                    return sum(abs(v * 1000 - x) for v, x in zip(vals, sheet))
                actual, target = (a, b) if err(a) < err(b) else (b, a)
                inferred = True
        if target is None:
            continue
        # does the deck's "actual" reproduce the sheet's ratio, either as-is or x1000 (deck MWh vs sheet kWh)?
        factor: int | None = None
        if actual and ratio:
            pairs = [(av, ratio.values.get(m)) for av, m in zip(actual["values"], ch["months"])]
            pairs = [(a_, s_) for a_, s_ in pairs if a_ is not None and s_ not in (None, 0) and a_ != 0]
            for f_ in (1000, 1):
                if pairs and all(abs(a_ * f_ - s_) <= max(0.06 * abs(s_), 0.6) for a_, s_ in pairs[:6]):
                    factor = f_
                    break
        verified = factor is not None
        values = {m: (None if v is None else (v * factor if verified else v))
                  for m, v in zip(ch["months"], target["values"]) if m}
        notes = []
        if factor == 1000:
            notes.append("Deck values are MWh per unit; multiplied by 1000 to compare with the sheet's kWh per unit.")
        elif factor == 1:
            notes.append("Deck 'actual' equals the sheet's ratio as-is, so the target is in the sheet's unit.")
        else:
            notes.append("The deck's 'actual' line does not reproduce the sheet's ratio, so the unit of this target is "
                         "unclear; values are the deck's own numbers, not converted. Axis title in the deck: "
                         f"{ch['axis']!r}.")
        if inferred:
            notes.append("Both chart lines carry the same name; the line that does not track the sheet was taken as the "
                         "target/plan (title says 'Plan Vs Actual' / 'Target Vs Actual').")
        if site_key == "tlmal":
            notes.append("Chart values are rounded in the deck to 3-4 decimals of MWh, i.e. about +/-0.05 kWh per unit.")
        return {"values": values, "unit": (ratio.label if (ratio and verified) else None),
                "deckAxisTitle": ch["axis"], "verifiedAgainstSheet": verified,
                "source": f"Sustainability deck, slide {ch['slide']}: {ch['title']}", "notes": notes}
    return None


# --------------------------------------------------------------------------- model
def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-") or "family"


def _num(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def _family(site_code: str, site_key: str, block: Block, fid: str) -> dict[str, Any]:
    months = block.months
    e, p, ra = block.energy, block.production, block.ratio
    energy_unit = "MWh" if (e and e.label.lower().startswith("mwh")) else "kWh"
    notes: list[str] = []
    energy = [e.values.get(m) if e else None for m in months]
    prod = [p.values.get(m) if p else None for m in months]
    sheet_ratio = [ra.values.get(m) if ra else None for m in months]
    computed: list[float | None] = []
    for ev, pv in zip(energy, prod):
        computed.append(ev / pv if ev is not None and pv not in (None, 0) else None)
    if p is None or all(v is None for v in prod):
        notes.append("No production units are filled in for this family; energy per unit cannot be shown.")
    elif any(v == 0 for v in prod):
        notes.append("Some months have 0 production units; the ratio is null for them (the sheet shows #DIV/0!).")
    if p and len({v for v in prod if v}) == 1 and p.label.lower() in ("cubic meter", "sq meter"):
        notes.append(f"'{p.label}' is the same every month: it is a fixed room size, not a production quantity.")
    both = [(ev, pv) for ev, pv in zip(energy, prod) if ev is not None and pv not in (None, 0)]
    total_e = sum(ev for ev, _ in both) if both else None
    total_p = sum(pv for _, pv in both) if both else None
    series_e = [v for v in energy if v is not None]
    filled_idx = [i for i, v in enumerate(energy) if v is not None]
    if filled_idx and any(energy[i] is None for i in range(filled_idx[0], filled_idx[-1])):
        notes.append("Some months between the first and last reported month have no kWh in the sheet (left null, not zero).")
    return {
        "id": fid,
        "name": block.title,
        "category": _category(block.title),
        "site": site_code,
        "months": months,
        "energy": {"label": e.label if e else None, "unit": energy_unit, "values": energy,
                   "sheetTotal": e.sheet_total if e else None},
        "production": {"label": p.label if p else None, "unit": p.label if p else None, "values": prod,
                       "sheetTotal": p.sheet_total if p else None},
        "energyPerUnit": {"label": ra.label if ra else (f"{energy_unit}/{p.label}" if p else None),
                          "unit": f"{energy_unit}/{p.label}" if p else None,
                          "sheetValues": sheet_ratio,
                          "computedValues": [_num(v) for v in computed]},
        "totals": {"energy": _num(sum(series_e), 2) if series_e else None,
                   "energyWhereProductionKnown": _num(total_e, 2),
                   "production": _num(total_p, 2),
                   "energyPerUnit": _num(total_e / total_p) if total_e is not None and total_p else None,
                   "basis": "Sum over months where both energy and production are filled and production is above 0."},
        "extraRows": [{"label": s.label, "values": [s.values.get(m) for m in months]} for s in block.extra],
        "target": _deck_target(site_key, block.title, ra),
        "orientation": block.orientation,
        "notes": notes,
    }


@dataclass
class _Site:
    key: str
    sheet: str
    code: str
    meta: dict[str, Any]
    families: list[dict[str, Any]]


@lru_cache(maxsize=4)
def _load_sites_cached(path: str, mtime: float) -> tuple[list[_Site], list[str]]:
    p = Path(path)
    sites: list[_Site] = []
    notes: list[str] = []
    for sheet in wb.sheet_names(p):
        meta = _site_def(sheet)
        try:
            blocks = parse_blocks(wb.read_sheet(p, sheet))
        except Exception as exc:  # noqa: BLE001 - one bad sheet must not hide the others
            notes.append(f"Sheet {sheet!r} could not be read: {exc}")
            continue
        used: dict[str, int] = {}
        fams = []
        for b in blocks:
            base = _slug(b.title)
            used[base] = used.get(base, 0) + 1
            fid = base if used[base] == 1 else f"{base}-{used[base]}"
            fams.append(_family(meta["code"], _norm(sheet), b, fid))
        sites.append(_Site(_norm(sheet), sheet, meta["code"], meta, fams))
    return sites, notes


def load_sites() -> tuple[list[_Site], list[str]]:
    p = wb.production_workbook()
    if not p:
        return [], ["The 'Power Consumption wrt Production Units' workbook was not found in the common folder."]
    return _load_sites_cached(str(p), p.stat().st_mtime)


def _site_summary(s: _Site, with_families: bool = True) -> dict[str, Any]:
    months = sorted({m for f in s.families for m in f["months"]})
    out = {
        "code": s.code, "sheet": s.sheet, "plant": s.meta["plant"],
        "plantName": plant_name(s.meta["plant"]) if s.meta["plant"] else None,
        "mappingConfidence": s.meta["confidence"], "mappingEvidence": s.meta["evidence"],
        "familyCount": len(s.families),
        "firstMonth": months[0] if months else None, "lastMonth": months[-1] if months else None,
    }
    if with_families:
        out["families"] = [family_summary(f) for f in s.families]
    return out


def family_summary(f: dict[str, Any]) -> dict[str, Any]:
    last = None
    for m, v in zip(f["months"], f["energy"]["values"]):
        if v is not None:
            last = m
    return {"id": f["id"], "name": f["name"], "category": f["category"], "categoryLabel": CATEGORY_LABELS[f["category"]],
            "energyUnit": f["energy"]["unit"], "productionUnit": f["production"]["unit"],
            "energyPerUnitUnit": f["energyPerUnit"]["unit"], "lastEnergyMonth": last,
            "hasProduction": any(v is not None for v in f["production"]["values"]),
            "hasTarget": f["target"] is not None}


def find_site(code: str) -> _Site | None:
    sites, _ = load_sites()
    want = code.lower()
    for s in sites:
        if want in (s.code.lower(), s.sheet.lower(), s.key, (s.meta["plant"] or "").lower()):
            return s
    return None


# --------------------------------------------------------------------------- public functions
def list_sites() -> dict[str, Any]:
    sites, notes = load_sites()
    notes = list(notes) + [
        "Targets are not in the workbook; they appear only as deck chart series for H01, TLMAL and MCA (H07).",
        "Each site stays a separate entry; no two sheets were found to belong to one plant.",
    ]
    mapped = {s.meta["plant"] for s in sites if s.meta["plant"]}
    missing = [p for p in PLANT_IDS if p not in mapped]
    if missing:
        notes.append(f"No sheet for plant(s): {', '.join(missing)}.")
    return {"source": "Power Consumption wrt Production Units.xlsx", "sites": [_site_summary(s) for s in sites], "notes": notes}


def site_families(code: str) -> dict[str, Any]:
    s = find_site(code)
    if not s:
        return {"site": code, "families": [], "notes": [f"No site {code!r}."]}
    return {**_site_summary(s, with_families=False), "families": [family_summary(f) for f in s.families], "notes": []}


def family_detail(code: str, family_id: str) -> dict[str, Any] | None:
    s = find_site(code)
    if not s:
        return None
    f = next((f for f in s.families if f["id"] == family_id), None)
    if f is None:
        return None
    return {**f, "plant": s.meta["plant"], "sheet": s.sheet,
            "source": f"Power Consumption wrt Production Units.xlsx > {s.sheet}"}


def compare_within_site(code: str, metric: str = "energyPerUnit") -> dict[str, Any]:
    """All families of one site on a shared month axis. metric: energy | production | energyPerUnit."""
    s = find_site(code)
    if not s:
        return {"site": code, "months": [], "series": [], "notes": [f"No site {code!r}."]}
    months = sorted({m for f in s.families for m in f["months"]})
    series = []
    for f in s.families:
        by_m = dict(zip(f["months"], _metric_values(f, metric)))
        series.append({"id": f["id"], "name": f["name"], "category": f["category"],
                       "unit": _metric_unit(f, metric), "values": [by_m.get(m) for m in months]})
    notes = ["Units differ between families (kWh per sq.ft, per run hour, per m3 ...); compare shapes, not magnitudes, "
             "unless the unit is the same."] if metric == "energyPerUnit" else []
    return {"site": s.code, "plant": s.meta["plant"], "metric": metric, "months": months, "series": series, "notes": notes}


def _metric_values(f: dict[str, Any], metric: str) -> list[float | None]:
    if metric == "energy":
        return f["energy"]["values"]
    if metric == "production":
        return f["production"]["values"]
    return f["energyPerUnit"]["computedValues"]


def _metric_unit(f: dict[str, Any], metric: str) -> str | None:
    if metric == "energy":
        return f["energy"]["unit"]
    if metric == "production":
        return f["production"]["unit"]
    return f["energyPerUnit"]["unit"]


def compare_category(category: str, metric: str = "energyPerUnit") -> dict[str, Any]:
    """One family category (hvac, compressor, paint ...) across every site that has it."""
    sites, notes = load_sites()
    notes = list(notes)
    if category not in CATEGORY_LABELS:
        return {"category": category, "months": [], "series": [],
                "notes": notes + [f"Unknown category {category!r}; known: {', '.join(CATEGORY_LABELS)}."]}
    rows = [(s, f) for s in sites for f in s.families if f["category"] == category]
    months = sorted({m for _, f in rows for m in f["months"]})
    series = []
    for s, f in rows:
        by_m = dict(zip(f["months"], _metric_values(f, metric)))
        series.append({"site": s.code, "plant": s.meta["plant"], "familyId": f["id"], "name": f["name"],
                       "unit": _metric_unit(f, metric), "values": [by_m.get(m) for m in months]})
    units = {x["unit"] for x in series}
    if metric == "energyPerUnit" and len(units) > 1:
        notes.append("Series use different production units (" + "; ".join(sorted(u or "none" for u in units))
                     + "), so their magnitudes are not directly comparable.")
    return {"category": category, "categoryLabel": CATEGORY_LABELS[category], "metric": metric,
            "months": months, "series": series, "notes": notes}


def categories() -> list[dict[str, Any]]:
    sites, _ = load_sites()
    counts: dict[str, set[str]] = {}
    for s in sites:
        for f in s.families:
            counts.setdefault(f["category"], set()).add(s.code)
    return [{"category": c, "label": CATEGORY_LABELS[c], "sites": sorted(v)} for c, v in sorted(counts.items())]


# --------------------------------------------------------------------------- entry workbook comparison
def _entry_blocks() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Parse the 'Only to Enter' sheet: families across (3 columns each), plant blocks down, months as text."""
    notes: list[str] = []
    path = wb.entry_workbook()
    if not path:
        return {}, ["The 'Only to Enter' workbook was not found."]
    name = wb.find_sheet(path, "power cons", "production")
    if not name:
        return {}, [f"Sheet {ENTRY_SHEET!r} not found in the 'Only to Enter' workbook."]
    rows = wb.read_sheet(path, name)
    title_row = next((r for r in rows if any(isinstance(v, str) and "paint" in v.lower() for v in r)), None)
    if title_row is None:
        return {}, ["No family titles found in the entry sheet."]
    titles = [(c, _clean(v)) for c, v in enumerate(title_row) if isinstance(v, str) and v.strip()]
    result: dict[str, dict[str, Any]] = {}
    plant = None
    for row in rows:
        if isinstance(row[1], str) and row[1].strip() and row[1].strip().lower() != "plants":
            plant = _clean(row[1])
        m = _month(row[2]) if plant else None
        if not m:
            continue
        for c, title in titles:
            vals = [wb.to_number(row[c + k]) if c + k < len(row) else None for k in range(3)]
            slot = result.setdefault(plant, {}).setdefault(title, {})
            slot[m] = vals
    return result, notes


def compare_sources() -> dict[str, Any]:
    """Which of the two workbooks is more complete, cell by cell for the plant the entry sheet fills."""
    entry, notes = _entry_blocks()
    sites, snotes = load_sites()
    notes = notes + snotes
    prod_cells = sum(1 for s in sites for f in s.families for v in f["energy"]["values"] if v is not None)
    entry_by_plant = {}
    for plant, fams in entry.items():
        filled = sum(1 for months in fams.values() for vals in months.values() for v in vals[:2] if v is not None)
        entry_by_plant[plant] = {"familiesListed": len(fams), "filledValueCells": filled}
    tsal = next((s for s in sites if s.meta["plant"] == "TSAL"), None)
    cmp_rows = []
    mismatches = 0
    compared = 0
    if tsal and "TSAL" in entry:
        e_t = entry["TSAL"]
        for f in tsal.families:
            ef = e_t.get(f["name"])
            if not ef:
                cmp_rows.append({"family": f["name"], "inEntrySheet": False})
                continue
            fam_cmp = {"family": f["name"], "inEntrySheet": True, "monthsCompared": 0, "kwhDifferences": [], "productionDifferences": []}
            for i, m in enumerate(f["months"]):
                if m not in ef:
                    continue
                ek, ep = ef[m][0], ef[m][1]
                pk, pp = f["energy"]["values"][i], f["production"]["values"][i]
                if ek is None and pk is None:
                    continue
                fam_cmp["monthsCompared"] += 1
                compared += 1
                if (ek is None) != (pk is None) or (ek is not None and abs(ek - pk) > 0.5):
                    fam_cmp["kwhDifferences"].append({"month": m, "entry": ek, "production": pk})
                    mismatches += 1
                if ep is not None and pp is not None and abs(ep - pp) > max(0.6, 0.001 * abs(pp)):
                    fam_cmp["productionDifferences"].append({"month": m, "entry": ep, "production": pp})
            cmp_rows.append(fam_cmp)
    notes.append("The 'Only to Enter' sheet lists 8 plants but only TSAL has numbers (Apr-25..Dec-25); "
                 "the other seven plant blocks are empty. It shows the same 11 TSAL families as the production workbook.")
    notes.append("Where both have a value they agree on kWh; production units agree within rounding (the entry sheet rounds sq.ft and hours to whole numbers).")
    return {
        "moreCompleteSource": "Power Consumption wrt Production Units.xlsx",
        "reason": f"It covers {len(sites)} sites and {sum(len(s.families) for s in sites)} families "
                  f"({prod_cells} monthly kWh values); the entry sheet fills only TSAL.",
        "entrySheet": {"name": ENTRY_SHEET, "plants": entry_by_plant},
        "tsalComparison": {"kwhCellsCompared": compared, "kwhMismatches": mismatches, "families": cmp_rows},
        "notes": notes,
    }


# --------------------------------------------------------------------------- routes
@router.get("/sites")
def api_sites() -> dict:
    """All sites (one per sheet) with their machine families and the plant each belongs to."""
    return list_sites()


@router.get("/categories")
def api_categories() -> list[dict]:
    return categories()


@router.get("/sources")
def api_sources() -> dict:
    return compare_sources()


@router.get("/sites/{site}")
def api_site(site: str) -> dict:
    return site_families(site)


@router.get("/sites/{site}/compare")
def api_site_compare(site: str, metric: str = Query("energyPerUnit", pattern="^(energy|production|energyPerUnit)$")) -> dict:
    return compare_within_site(site, metric)


@router.get("/sites/{site}/families/{family_id}")
def api_family(site: str, family_id: str) -> dict:
    d = family_detail(site, family_id)
    if d is None:
        raise HTTPException(status_code=404, detail=f"No family {family_id!r} at site {site!r}")
    return d


@router.get("/categories/{category}/compare")
def api_category_compare(category: str, metric: str = Query("energyPerUnit", pattern="^(energy|production|energyPerUnit)$")) -> dict:
    return compare_category(category, metric)
