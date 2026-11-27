"""Manual month-wise entry of diesel, manpower, revenue and afforestation.

Entries live in Excel files inside the source folder's energy sub-folders (the one place the
app may write there), so people can also edit them in Excel:

    energy/diesel/Diesel - manual entries.xlsx
    energy/man power/Manpower - manual entries.xlsx
    energy/revenue/Revenue - manual entries.xlsx
    energy/Afforestation/Afforestation - manual entries.xlsx

One sheet, header row: Plant | Month (YYYY-MM) | Value | Unit | Note | Entered at | Entry ID.
The file is read fresh on every request. TBAL is deliberately not part of this group.

This module is self-contained: it reads the few workbook sheets it needs itself (through the
cached workbooks.read_sheet) and does not import the energy module.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from openpyxl import Workbook, load_workbook

from .. import workbooks
from ..config import settings
from ..plants import PLANT_IDS, canonical_plant, plant_name

router = APIRouter(prefix="/api/manual", tags=["manual"])

EXCLUDED_PLANT = "TBAL"
MANUAL_PLANTS = [p for p in PLANT_IDS if p != EXCLUDED_PLANT]

# Tests point this at a temp folder; None means "<source folder>/energy".
STORAGE_ROOT: Path | None = None

HEADERS = ["Plant", "Month (YYYY-MM)", "Value", "Unit", "Note", "Entered at", "Entry ID"]

# Units follow the source sheets: 'Diesel consumption data' is in KL, 'TADL Revenue Summary' is
# "PLANT REVENUE IN CR" (Rs crore), 'Afforestation sheet' counts plants ("No."), manpower is the
# "Employees / Nos" headcount of the plant summary sheets.
PARAMETERS: dict[str, dict[str, Any]] = {
    "diesel": {"label": "Diesel consumption", "folder": "diesel", "file": "Diesel - manual entries.xlsx",
               "unit": "KL", "workbookGranularity": "monthly"},
    "manpower": {"label": "Manpower", "folder": "man power", "file": "Manpower - manual entries.xlsx",
                 "unit": "headcount", "workbookGranularity": "yearly"},
    "revenue": {"label": "Revenue", "folder": "revenue", "file": "Revenue - manual entries.xlsx",
                "unit": "₹ Cr", "workbookGranularity": "monthly"},
    "afforestation": {"label": "Afforestation (trees + shrubs planted)", "folder": "Afforestation",
                      "file": "Afforestation - manual entries.xlsx", "unit": "No.", "workbookGranularity": "monthly"},
}

_MONTH_STRICT = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])$")
_MONTH_NAMES = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_TEXT_MONTH = re.compile(r"^\s*([A-Za-z]{3,9})\.?[-\s'’/]*(\d{2}|\d{4})\s*$")
_NUM_MONTH = re.compile(r"^\s*(20\d{2})\s*[-/]\s*(\d{1,2})\s*$")
_FY_TWO = re.compile(r"fy\s*(\d{2,4})\s*[-–]\s*(\d{2,4})", re.I)
_lock = threading.RLock()


# --------------------------------------------------------------------------- small helpers

def fy_of_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    start = year if mon >= 4 else year - 1
    return f"FY{start % 100:02d}-{(start + 1) % 100:02d}"


def normalize_fy(text: str | None) -> str | None:
    """'FY25-26', 'FY 25-26', '2025-26', '25-26' -> 'FY25-26'; None if not understood."""
    match = re.fullmatch(r"\s*(?:fy)?\s*(\d{2}|\d{4})\s*[-–]\s*(\d{2}|\d{4})\s*", str(text or ""), re.I)
    if not match:
        return None
    a, b = int(match.group(1)) % 100, int(match.group(2)) % 100
    return f"FY{a:02d}-{b:02d}" if (a + 1) % 100 == b else None


def _plant_of(label: Any) -> str | None:
    """canonical_plant plus the one misspelling seen in the diesel sheet ('TASL- Banglore')."""
    pid = canonical_plant(label)
    if pid is None and re.search(r"bang?lore|bengaluru", str(label or ""), re.I):
        return "TASL-BLR"
    return pid


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _lenient_month(value: Any) -> str | None:
    """A month cell a person typed: a date, 'YYYY-MM', 'Apr-26', 'April 2026'. None if unreadable.

    A date cell is read as its calendar month. (Excel turns a typed 'Apr-26' into 26 April of the
    current year, so month text such as 2026-04 is the safe way to type it.)"""
    if isinstance(value, (dt.datetime, dt.date)):
        return f"{value.year:04d}-{value.month:02d}"
    text = str(value or "").strip()
    if _MONTH_STRICT.match(text):
        return text
    num = _NUM_MONTH.match(text)
    if num and 1 <= int(num.group(2)) <= 12:
        return f"{int(num.group(1)):04d}-{int(num.group(2)):02d}"
    named = _TEXT_MONTH.match(text)
    if named and named.group(1)[:3].lower() in _MONTH_NAMES:
        year = int(named.group(2))
        year = year + 2000 if year < 100 else year
        return f"{year:04d}-{_MONTH_NAMES[named.group(1)[:3].lower()]:02d}"
    return None


# --------------------------------------------------------------------------- storage

def _folder(parameter: str) -> Path:
    root = STORAGE_ROOT if STORAGE_ROOT is not None else settings.source_dir / "energy"
    return root / PARAMETERS[parameter]["folder"]


def storage_path(parameter: str) -> Path:
    """The existing manual-entries file (any '*manual*.xlsx' in the folder, so a renamed copy works), or the default name."""
    folder = _folder(parameter)
    if folder.is_dir():
        found = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".xlsx"
                 and "manual" in f.name.lower() and not f.name.startswith(("~$", ".~", "~tmp"))]
        if found:
            return max(found, key=lambda f: f.stat().st_mtime)
    return folder / PARAMETERS[parameter]["file"]


def _locked(path: Path, exc: Exception, verb: str) -> HTTPException:
    return HTTPException(409, f"Cannot {verb} {path.name}: the file is open in Excel (or locked by another program). "
                              "Close it and try again.")


def _header_row(ws) -> int | None:
    for r in range(1, min(ws.max_row, 20) + 1):
        first = str(ws.cell(r, 1).value or "").strip().lower()
        second = str(ws.cell(r, 2).value or "").strip().lower()
        if first == "plant" and second.startswith("month"):
            return r
    return None


def _columns(ws, hdr: int) -> dict[str, int]:
    names = {str(ws.cell(hdr, c).value or "").strip().lower(): c for c in range(1, max(ws.max_column, 7) + 1)}
    return {"plant": names.get("plant", 1), "month": next((c for n, c in names.items() if n.startswith("month")), 2),
            "value": names.get("value", 3), "unit": names.get("unit", 4), "note": names.get("note", 5),
            "at": names.get("entered at", 6), "id": names.get("entry id", 7)}


def _new_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Entries"
    ws.append(HEADERS)
    for col, width in zip("ABCDEFG", (14, 16, 12, 12, 40, 20, 16)):
        ws.column_dimensions[col].width = width
    return wb


def _scan(ws, hdr: int, col: dict[str, int]) -> list[dict[str, Any]]:
    """Every non-empty row below the header as a record; valid=False rows keep the reason they are unusable.

    A row without an Entry ID gets a stable derived one (plant, month, occurrence), so it can be
    listed and deleted even though nobody typed an ID."""
    records: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for r in range(hdr + 1, ws.max_row + 1):
        raw = {k: ws.cell(r, c).value for k, c in col.items()}
        if all(v in (None, "") for v in raw.values()):
            continue
        pid = _plant_of(raw["plant"])
        month = _lenient_month(raw["month"])
        value = _number(raw["value"])
        problems = []
        if pid is None:
            problems.append(f"plant {raw['plant']!r} not recognised")
        elif pid == EXCLUDED_PLANT:
            problems.append("TBAL is not part of manual entry")
        if month is None:
            problems.append(f"month {raw['month']!r} not readable (use YYYY-MM)")
        if value is None or value < 0:
            problems.append(f"value {raw['value']!r} is not a number >= 0")
        explicit = str(raw["id"]).strip() if raw["id"] not in (None, "") else ""
        if explicit:
            entry_id = explicit
        else:
            key = f"{pid}|{month}"
            seen[key] = seen.get(key, 0) + 1
            entry_id = "h" + hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()[:10]
        at = raw["at"]
        records.append({"row": r, "valid": not problems, "problems": problems, "id": entry_id, "plant": pid, "month": month,
                        "value": value, "unit": str(raw["unit"]).strip() if raw["unit"] not in (None, "") else None,
                        "note": str(raw["note"]).strip() if raw["note"] not in (None, "") else None,
                        "enteredAt": at.isoformat(timespec="seconds") if isinstance(at, dt.datetime) else (str(at) if at else None),
                        "raw": raw})
    return records


def _open_for_write(parameter: str):
    """(path, workbook, sheet, header_row, columns); creates a fresh workbook in memory if the file is not there yet."""
    path = storage_path(parameter)
    if path.is_file():
        try:
            wb = load_workbook(path)
        except PermissionError as exc:
            raise _locked(path, exc, "open") from exc
        ws = wb.worksheets[0]
        hdr = _header_row(ws)
        if hdr is None:
            raise HTTPException(409, f"{path.name} has no header row (Plant, Month (YYYY-MM), Value, ...). "
                                     "Fix the first row in Excel or move the file away to start again.")
    else:
        wb = _new_workbook()
        ws, hdr = wb.worksheets[0], 1
    col = _columns(ws, hdr)
    for key, title in zip(("plant", "month", "value", "unit", "note", "at", "id"), HEADERS):
        if ws.cell(hdr, col[key]).value in (None, ""):
            ws.cell(hdr, col[key], title)   # a hand-made file may lack e.g. the Entry ID heading
    return path, wb, ws, hdr, col


def _save(path: Path, wb: Workbook) -> None:
    """Write beside the file then replace it, so a failed save never leaves a half-written workbook."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"~tmp-{uuid.uuid4().hex[:8]}{path.suffix}")
    try:
        wb.save(tmp)
        tmp.replace(path)
    except PermissionError as exc:
        raise _locked(path, exc, "save") from exc
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- reading entries

def _public(rec: dict[str, Any], parameter: str) -> dict[str, Any]:
    return {"id": rec["id"], "plant": rec["plant"], "name": plant_name(rec["plant"]), "month": rec["month"],
            "fy": fy_of_month(rec["month"]), "value": rec["value"], "unit": rec["unit"] or PARAMETERS[parameter]["unit"],
            "note": rec["note"], "enteredAt": rec["enteredAt"]}


def read_entries(parameter: str) -> tuple[list[dict[str, Any]], list[str]]:
    """All valid entries of a parameter's file, fresh from disk, plus notes about rows that were skipped."""
    path = storage_path(parameter)
    if not path.is_file():
        return [], []
    try:
        wb = load_workbook(path, data_only=True, read_only=False)
    except PermissionError as exc:
        raise _locked(path, exc, "read") from exc
    except Exception as exc:  # noqa: BLE001 - a damaged file must not fail the page
        return [], [f"{path.name} could not be read: {exc}"]
    try:
        ws = wb.worksheets[0]
        hdr = _header_row(ws)
        if hdr is None:
            return [], [f"{path.name}: header row (Plant, Month (YYYY-MM), Value, ...) not found"]
        entries, notes = [], []
        for rec in _scan(ws, hdr, _columns(ws, hdr)):
            if rec["valid"]:
                entries.append(_public(rec, parameter))
            else:
                notes.append(f"{path.name} row {rec['row']} skipped: " + "; ".join(rec["problems"]))
        entries.sort(key=lambda e: (e["month"], e["plant"]))
        return entries, notes
    finally:
        wb.close()


# --------------------------------------------------------------------------- validation

def _parameter(parameter: str) -> str:
    key = re.sub(r"[\s_-]", "", parameter.strip().lower())
    if key not in PARAMETERS:
        raise HTTPException(404, f"Unknown parameter {parameter!r}. Use one of: {', '.join(PARAMETERS)}.")
    return key


def _plant(value: Any) -> str:
    pid = _plant_of(value)
    if pid == EXCLUDED_PLANT:
        raise HTTPException(400, "TBAL is not part of manual entry: its data comes from the common workbooks. "
                                 "Choose one of: " + ", ".join(MANUAL_PLANTS) + ".")
    if pid is None:
        raise HTTPException(400, f"Unknown plant {value!r}. Choose one of: {', '.join(MANUAL_PLANTS)}.")
    return pid


def _fy(value: str | None) -> str | None:
    if not value:
        return None
    fy = normalize_fy(value)
    if fy is None:
        raise HTTPException(400, f"Financial year {value!r} not understood; use a form like FY25-26.")
    return fy


def validate_entry(parameter: str, body: dict[str, Any]) -> dict[str, Any]:
    pid = _plant(body.get("plant"))
    month = str(body.get("month") or "").strip()
    if not _MONTH_STRICT.match(month):
        raise HTTPException(400, f"Month {body.get('month')!r} is not valid; use YYYY-MM, for example 2026-04.")
    value = _number(body.get("value"))
    if value is None:
        raise HTTPException(400, f"Value {body.get('value')!r} is not a number.")
    if value < 0:
        raise HTTPException(400, "Value cannot be negative.")
    if parameter == "manpower" and value != int(value):
        raise HTTPException(400, "Manpower is a headcount; enter a whole number.")
    note = body.get("note")
    return {"plant": pid, "month": month, "value": value, "note": str(note).strip() if note not in (None, "") else None}


# --------------------------------------------------------------------------- operations

def add_entry(parameter: str, body: dict[str, Any]) -> dict[str, Any]:
    clean = validate_entry(parameter, body)
    unit = PARAMETERS[parameter]["unit"]
    now = dt.datetime.now().replace(microsecond=0)
    with _lock:
        path, wb, ws, hdr, col = _open_for_write(parameter)
        same = [rec["row"] for rec in _scan(ws, hdr, col) if rec["plant"] == clean["plant"] and rec["month"] == clean["month"]]
        replaced = bool(same)
        if same:
            target = same[-1]   # a second entry for the same plant and month replaces the first
            for extra in reversed(same[:-1]):   # hand-typed duplicates collapse into the one row
                ws.delete_rows(extra)
                target -= 1
        else:
            target = max(ws.max_row, hdr) + 1
        if not ws.cell(target, col["id"]).value:
            ws.cell(target, col["id"], uuid.uuid4().hex[:12])
        ws.cell(target, col["plant"], clean["plant"])
        ws.cell(target, col["month"], clean["month"]).number_format = "@"
        ws.cell(target, col["value"], clean["value"])
        ws.cell(target, col["unit"], unit)
        ws.cell(target, col["note"], clean["note"])
        ws.cell(target, col["at"], now).number_format = "yyyy-mm-dd hh:mm:ss"
        entry_id = str(ws.cell(target, col["id"]).value)
        _save(path, wb)
    return {"ok": True, "replaced": replaced, "file": path.name,
            "entry": {"id": entry_id, "plant": clean["plant"], "name": plant_name(clean["plant"]), "month": clean["month"],
                      "fy": fy_of_month(clean["month"]), "value": clean["value"], "unit": unit,
                      "note": clean["note"], "enteredAt": now.isoformat()}}


def delete_entry(parameter: str, entry_id: str) -> dict[str, Any]:
    with _lock:
        path = storage_path(parameter)
        if not path.is_file():
            raise HTTPException(404, f"No manual {parameter} entries exist yet.")
        path, wb, ws, hdr, col = _open_for_write(parameter)
        for rec in _scan(ws, hdr, col):
            if rec["id"] == entry_id:
                ws.delete_rows(rec["row"])
                _save(path, wb)
                return {"ok": True, "deleted": entry_id, "file": path.name}
    raise HTTPException(404, f"No {parameter} entry with id {entry_id!r}.")


def list_entries(parameter: str, plant: str | None = None, fy: str | None = None) -> dict[str, Any]:
    pid = _plant(plant) if plant else None
    fy_name = _fy(fy)
    entries, notes = read_entries(parameter)
    entries = [e for e in entries if (pid is None or e["plant"] == pid) and (fy_name is None or e["fy"] == fy_name)]
    path = storage_path(parameter)
    return {"parameter": parameter, "unit": PARAMETERS[parameter]["unit"], "file": str(path), "fileExists": path.is_file(),
            "excludedPlants": [EXCLUDED_PLANT], "entries": entries, "notes": notes}


def describe_parameters() -> dict[str, Any]:
    plants = [{"id": p, "name": plant_name(p)} for p in MANUAL_PLANTS]
    return {"parameters": [{"id": pid, "label": spec["label"], "unit": spec["unit"], "plants": plants,
                            "excludedPlants": [EXCLUDED_PLANT], "workbookGranularity": spec["workbookGranularity"],
                            "folder": f"energy/{spec['folder']}", "file": spec["file"]}
                           for pid, spec in PARAMETERS.items()]}


# --------------------------------------------------------------------------- workbook values (for 'combined')

def _fy_start(label: str) -> int | None:
    match = _FY_TWO.search(label)
    if not match:
        return None
    a = int(match.group(1))
    return a + 2000 if a < 100 else a


def _month_cell(value: Any) -> tuple[int, int | None] | None:
    """(month number, own year or None) of a header cell that names a month."""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.month, None   # the year of a typed 'Mar-23' is junk (Excel fills the current year)
    named = _TEXT_MONTH.match(str(value or ""))
    if named and named.group(1)[:3].lower() in _MONTH_NAMES:
        year = int(named.group(2))
        return _MONTH_NAMES[named.group(1)[:3].lower()], year + 2000 if year < 100 else year
    return None


def month_columns(header: list[Any], titles: list[Any] | None = None) -> list[tuple[int, str, str | None]]:
    """(column, 'YYYY-MM', title above it) for each month column of a header row.

    The sheets hold one run of twelve month headers per financial year, followed by an 'FY 25-26'
    style total column. Header dates are unreliable (Excel read 'Apr-26' as 26 April of some year),
    so the month number comes from the header and the year from the run's FY label. A run with no
    FY label is used only if it starts at April, taking the year of that April header."""
    out: list[tuple[int, str, str | None]] = []
    run: list[tuple[int, tuple[int, int | None]]] = []

    def close(label: Any) -> None:
        nonlocal run
        if run:
            start = _fy_start(str(label)) if label else None
            first_month, first_year = run[0][1]
            if start is None and first_month == 4:
                start = first_year
                if start is None:
                    cell = header[run[0][0]]
                    start = cell.year if isinstance(cell, (dt.datetime, dt.date)) else None
            if start is not None:
                title = None
                if titles:
                    for c in range(run[0][0], -1, -1):
                        if titles[c] not in (None, ""):
                            title = str(titles[c])
                            break
                for col, (month, _own) in run:
                    year = start if month >= 4 else start + 1
                    out.append((col, f"{year:04d}-{month:02d}", title))
        run = []

    for i, cell in enumerate(header):
        parsed = _month_cell(cell)
        if parsed:
            run.append((i, parsed))
        elif isinstance(cell, str) and _FY_TWO.search(cell):
            close(cell)
        elif cell is None:
            close(None)
    close(None)
    return out


def _find_header(grid: list[list[Any]], first: str) -> int | None:
    for i, row in enumerate(grid[:40]):
        if str(row[0] or "").strip().lower().startswith(first):
            return i
    return None


def _blocks(grid: list[list[Any]], hdr: int, total_pattern: str) -> list[tuple[str | None, str, list[Any], list[list[Any]]]]:
    """Plant blocks under the header: (plant id, plant label, total row, component rows).

    A block starts at a row with a plant name in column B; its total row is the first row whose
    label (column C) matches total_pattern. Reading stops at the 'Overall ...' summary rows so
    summary tables below the data are never mistaken for plants."""
    blocks = []
    current: dict[str, Any] | None = None
    for row in grid[hdr + 1:]:
        label = str(row[2] or "").strip()
        if label.lower().startswith("overall"):
            break
        if isinstance(row[1], str) and row[1].strip():
            current = {"pid": _plant_of(row[1]), "label": row[1].strip(), "total": None, "parts": []}
            blocks.append(current)
        if current is None or not label:
            continue
        if current["total"] is None and re.match(total_pattern, label, re.I):
            current["total"] = row
        elif current["total"] is None:
            current["parts"].append(row)
    return [(b["pid"], b["label"], b["total"], b["parts"]) for b in blocks if b["total"] is not None]


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == "" or str(value).strip().upper() == "NA"


def _total_value(total_row: list[Any], parts: list[list[Any]], col: int) -> float | None:
    """The total cell as a number. A 0 over components that are all blank is the sheet's SUM of
    nothing (no data entered yet), not a measured zero, so it is reported as missing."""
    value = workbooks.to_number(total_row[col])
    if value is None:
        return None
    if value == 0 and all(_blank(p[col]) for p in parts):
        return None
    return value


def _read_monthly_blocks(path: Path | None, sheet_parts: tuple[str, ...], total_pattern: str,
                         title_filter: str | None, notes: list[str], label: str) -> dict[str, dict[str, float]]:
    if path is None:
        return {}
    sheet = workbooks.find_sheet(path, *sheet_parts)
    if sheet is None:
        notes.append(f"{label}: sheet containing {' '.join(sheet_parts)!r} not found in {path.name}")
        return {}
    grid = workbooks.read_sheet(path, sheet)
    hdr = _find_header(grid, "sl")
    if hdr is None:
        notes.append(f"{label}: header row not found in sheet {sheet!r}")
        return {}
    titles = grid[hdr - 2] if hdr >= 2 else None
    cols = month_columns(grid[hdr], titles)
    if title_filter:   # keep only the runs under a block whose title says so (the static note explains why)
        cols = [(c, mo, t) for c, mo, t in cols if t and title_filter in t.lower()]
    result: dict[str, dict[str, float]] = {}
    for pid, _plant_label, total, parts in _blocks(grid, hdr, total_pattern):
        if pid is None or pid == EXCLUDED_PLANT:
            continue
        for col, month, _title in cols:
            value = _total_value(total, parts, col)
            if value is not None:
                result.setdefault(pid, {})[month] = value
    return result


def _diesel_workbook() -> tuple[dict[str, dict[str, dict[str, Any]]], list[str]]:
    notes: list[str] = []
    calc = _read_monthly_blocks(workbooks.calculation_workbook(), ("diesel consumption data",), r"total consumption", None, notes,
                                "diesel (calculation workbook)")
    entry = _read_monthly_blocks(workbooks.entry_workbook(), ("diesel consumption data",), r"total consumption", None, notes,
                                 "diesel (entry workbook)")
    notes.append("Diesel = the 'Total Consumption' row (DG + HWG + forklift + other) of sheet 'Diesel consumption data', in KL. "
                 "The separate sheet 'Vehicle- Diesel consumption dat' (company cars) is not added in.")
    return _merge_sources(calc, entry, notes), notes


def _afforestation_workbook() -> tuple[dict[str, dict[str, dict[str, Any]]], list[str]]:
    notes: list[str] = []
    pattern = r"(afforestation total|total consumption)"
    calc = _read_monthly_blocks(workbooks.calculation_workbook(), ("afforestation sheet",), pattern, "afforestation", notes,
                                "afforestation (calculation workbook)")
    entry = _read_monthly_blocks(workbooks.entry_workbook(), ("afforestation sheet",), pattern, "afforestation", notes,
                                 "afforestation (entry workbook)")
    notes.append("Afforestation = trees + shrubs planted in the month ('Afforestation Total' row of sheet 'Afforestation sheet', "
                 "unit No.). Only the block titled 'Afforestation Details' is used; the older FY blocks of that sheet carry a "
                 "copy-pasted 'Diesel Consumption' title and hold figures that look like standing stock, not monthly plantings.")
    return _merge_sources(calc, entry, notes), notes


def _merge_sources(calc: dict[str, dict[str, float]], entry: dict[str, dict[str, float]],
                   notes: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Calculation workbook first; the entry workbook only where the calculation workbook has no value."""
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    differing = 0
    for pid in set(calc) | set(entry):
        for month in set(calc.get(pid, {})) | set(entry.get(pid, {})):
            c, e = calc.get(pid, {}).get(month), entry.get(pid, {}).get(month)
            cell: dict[str, Any] = ({"value": c, "workbookSource": "calculation"} if c is not None
                                    else {"value": e, "workbookSource": "entry"})
            if c is not None and e is not None and not math.isclose(c, e, rel_tol=1e-6, abs_tol=1e-9):
                cell["entryWorkbookValue"] = e
                differing += 1
            merged.setdefault(pid, {})[month] = cell
    if differing:
        notes.append(f"{differing} plant-month values differ between the calculation workbook (shown) and the entry workbook "
                     "(see entryWorkbookValue on those cells); neither was altered.")
    return merged


def _revenue_workbook() -> tuple[dict[str, dict[str, dict[str, Any]]], list[str]]:
    notes: list[str] = []
    path = workbooks.calculation_workbook()
    sheet = workbooks.find_sheet(path, "revenue summary") if path else None
    if sheet is None:
        return {}, ["revenue: sheet 'TADL Revenue Summary' not found in the calculation workbook"]
    grid = workbooks.read_sheet(path, sheet)
    hdr = _find_header(grid, "plant")
    if hdr is None:
        return {}, ["revenue: header row not found in sheet 'TADL Revenue Summary'"]
    cols = month_columns(grid[hdr])
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for row in grid[hdr + 1:]:
        pid = _plant_of(row[1])
        if pid is None or pid == EXCLUDED_PLANT:
            continue   # the 'TADL' total row and blank separators
        for col, month, _ in cols:
            value = workbooks.to_number(row[col])
            if value is not None:
                result.setdefault(pid, {})[month] = {"value": value, "workbookSource": "calculation"}
    notes.append("Revenue = sheet 'TADL Revenue Summary' (PLANT REVENUE IN CR, ₹ Cr), one column per month of FY25-26; no other "
                 "financial year is month-wise in the workbooks.")
    jan_feb_same = [p for p, months in result.items()
                    if "2026-01" in months and "2026-02" in months and months["2026-01"]["value"] == months["2026-02"]["value"]]
    if jan_feb_same and len(jan_feb_same) == len(result):
        notes.append("In that sheet the Jan-26 column holds exactly the same figure as Feb-26 for every plant; "
                     "one of the two looks like a copy and is shown as found.")
    return result, notes


def _manpower_yearly() -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Employees per FY from the 'Employees / Nos' row of each plant's Sustainability Summary sheet."""
    path = workbooks.calculation_workbook()
    if path is None:
        return {}, ["manpower: calculation workbook not found"]
    out: dict[str, list[dict[str, Any]]] = {}
    for name in workbooks.sheet_names(path):
        if "sustainability su" not in name.lower() or name.lower().startswith("tadl"):
            continue
        pid = _plant_of(name)
        if pid is None or pid == EXCLUDED_PLANT:
            continue
        grid = workbooks.read_sheet(path, name)
        header = next((r for r in grid[:10] if sum(1 for c in r if isinstance(c, str) and _FY_TWO.search(c)) >= 3), None)
        emp = next((r for r in grid[:150] if str(r[0] or "").strip().lower() == "employees"), None)
        if header is None or emp is None:
            continue
        rows = []
        for i, cell in enumerate(header):
            fy = normalize_fy(re.sub(r"\s+", "", str(cell))) if isinstance(cell, str) and _FY_TWO.search(cell) else None
            value = workbooks.to_number(emp[i])
            if fy and value is not None:
                rows.append({"fy": fy, "value": value, "source": "workbook"})
        out[pid] = rows
    notes = ["Manpower in the workbooks is one headcount per financial year ('Employees', Nos, on each plant's Sustainability "
             "Summary sheet), so there are no workbook months; the yearly figures are returned as 'yearly' and are not spread "
             "across months. Monthly cells come only from manual entries."]
    return out, notes


# --------------------------------------------------------------------------- combined view

def combined(parameter: str, plant: str | None = None, fy: str | None = None) -> dict[str, Any]:
    """Per plant and month: the workbook's value next to the manual one; manual wins, each cell says which."""
    pid_filter = _plant(plant) if plant else None
    fy_name = _fy(fy)
    entries, notes = read_entries(parameter)
    manual: dict[str, dict[str, dict[str, Any]]] = {}
    for e in entries:
        manual.setdefault(e["plant"], {})[e["month"]] = e   # entries are month-sorted; a later duplicate row wins
    monthly: dict[str, dict[str, dict[str, Any]]] = {}
    yearly: dict[str, list[dict[str, Any]]] = {}
    try:
        if parameter == "manpower":
            yearly, extra = _manpower_yearly()
        else:
            monthly, extra = {"diesel": _diesel_workbook, "revenue": _revenue_workbook,
                              "afforestation": _afforestation_workbook}[parameter]()
    except Exception as exc:  # noqa: BLE001 - the page still shows the manual entries
        extra = [f"workbook values unavailable: {type(exc).__name__}: {exc}"]
    notes = notes + extra
    plants = []
    for pid in ([pid_filter] if pid_filter else MANUAL_PLANTS):
        cells = []
        for month in sorted(set(monthly.get(pid, {})) | set(manual.get(pid, {}))):
            if fy_name and fy_of_month(month) != fy_name:
                continue
            wb_cell = monthly.get(pid, {}).get(month)
            entry = manual.get(pid, {}).get(month)
            cell: dict[str, Any] = {"month": month, "fy": fy_of_month(month)}
            if entry is not None:
                cell.update(value=entry["value"], source="manual", entryId=entry["id"], note=entry["note"])
            else:
                cell.update(value=wb_cell["value"], source="workbook", entryId=None, note=None)
            cell["workbookValue"] = wb_cell["value"] if wb_cell else None
            cell["workbookSource"] = wb_cell["workbookSource"] if wb_cell else None
            cell["manualValue"] = entry["value"] if entry else None
            if wb_cell and "entryWorkbookValue" in wb_cell:
                cell["entryWorkbookValue"] = wb_cell["entryWorkbookValue"]
            cells.append(cell)
        row: dict[str, Any] = {"plant": pid, "name": plant_name(pid), "months": cells}
        if parameter == "manpower":
            row["yearly"] = [y for y in yearly.get(pid, []) if not fy_name or y["fy"] == fy_name]
        plants.append(row)
    notes.append(f"{EXCLUDED_PLANT} is excluded from manual entry and from this view.")
    return {"parameter": parameter, "unit": PARAMETERS[parameter]["unit"],
            "workbookGranularity": PARAMETERS[parameter]["workbookGranularity"],
            "precedence": "manual entry over workbook", "plants": plants, "notes": notes}


# --------------------------------------------------------------------------- routes

@router.get("/parameters")
def parameters_route() -> dict:
    return describe_parameters()


@router.get("/{parameter}/combined")
def combined_route(parameter: str, plant: str | None = Query(None), fy: str | None = Query(None)) -> dict:
    return combined(_parameter(parameter), plant, fy)


@router.get("/{parameter}")
def list_route(parameter: str, plant: str | None = Query(None), fy: str | None = Query(None)) -> dict:
    return list_entries(_parameter(parameter), plant, fy)


@router.post("/{parameter}")
def add_route(parameter: str, body: dict[str, Any] = Body(...)) -> dict:
    return add_entry(_parameter(parameter), body)


@router.delete("/{parameter}/{entry_id}")
def delete_route(parameter: str, entry_id: str) -> dict:
    return delete_entry(_parameter(parameter), entry_id)
