"""Cached access to the source workbooks.

The two "common" workbooks are about 5 MB each and take several seconds to
open, so each sheet is read once into plain Python lists and kept until the
file changes on disk.
"""
from __future__ import annotations

import hashlib
import pickle
import threading
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .config import find_source_file, settings

Grid = list[list[Any]]

_lock = threading.Lock()
_cache: dict[tuple[str, float], dict[str, Grid]] = {}
_names: dict[tuple[str, float], list[str]] = {}


def _key(path: Path) -> tuple[str, float]:
    return (str(path), path.stat().st_mtime)


def sheet_names(path: Path) -> list[str]:
    key = _key(path)
    with _lock:
        if key not in _names:
            wb = load_workbook(path, read_only=True, data_only=True)
            _names[key] = list(wb.sheetnames)
            wb.close()
        return _names[key]


def read_sheet(path: Path, sheet: str) -> Grid:
    """Cached cell values of one sheet as rows of values (formulas already evaluated).

    Rows are padded to the same length. Treat the result as read-only.
    Raises KeyError when the sheet does not exist.
    """
    key = _key(path)
    with _lock:
        sheets = _cache.setdefault(key, {})
        if sheet in sheets:
            return sheets[sheet]
        # Drop grids cached for an older version of the same file.
        for old in [k for k in _cache if k[0] == key[0] and k != key]:
            _cache.pop(old, None)
            _names.pop(old, None)
        rows = _disk_get(path, key[1], sheet)
        if rows is None:
            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                if sheet not in wb.sheetnames:
                    raise KeyError(f"Sheet {sheet!r} not found in {path.name}")
                rows = [list(r) for r in wb[sheet].iter_rows(values_only=True)]
            finally:
                wb.close()
            width = max((len(r) for r in rows), default=0)
            for r in rows:
                r.extend([None] * (width - len(r)))
            _disk_put(path, key[1], sheet, rows)
        sheets[sheet] = rows
        return rows


# Parsed sheets are also kept on disk, so a server restart does not have to
# re-open the 5 MB workbooks (about a minute each). A cached sheet is used only
# while the workbook's modified time is unchanged; the cache holds nothing but
# values this app read from the user's own files.

def _disk_file(path: Path, mtime: float, sheet: str) -> Path:
    tag = hashlib.sha1(f"{path}|{mtime}|{sheet}".encode("utf-8")).hexdigest()[:20]
    return settings.data_dir / "cache" / "sheets" / f"{tag}.pkl"


def _disk_get(path: Path, mtime: float, sheet: str) -> Grid | None:
    file = _disk_file(path, mtime, sheet)
    if not file.is_file():
        return None
    try:
        with file.open("rb") as fh:
            return pickle.load(fh)  # noqa: S301 - written by this app into its own data folder
    except Exception:  # noqa: BLE001 - a damaged cache file is simply ignored and rebuilt
        return None


def _disk_put(path: Path, mtime: float, sheet: str, rows: Grid) -> None:
    file = _disk_file(path, mtime, sheet)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(rows, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(file)
    except OSError:
        pass  # caching is an optimisation; reading still worked


def find_sheet(path: Path, *name_parts: str) -> str | None:
    """First sheet whose name contains every part (case-insensitive)."""
    wanted = [p.lower() for p in name_parts]
    for name in sheet_names(path):
        if all(w in name.lower() for w in wanted):
            return name
    return None


# The two workbooks every module leans on -------------------------------------

def calculation_workbook() -> Path | None:
    """Primary source: "Sustainability Calculation - ... FY20 to FY27.xlsx"."""
    return find_source_file("common", "sustainability calculation")


def entry_workbook() -> Path | None:
    """Fallback source: "Sustainability (Only to Enter)- ... FY20 to FY27.xlsx"."""
    return find_source_file("common", "only to enter")


def production_workbook() -> Path | None:
    """"Power Consumption wrt Production Units.xlsx": machine families per plant."""
    return find_source_file("common", "power consumption", "production units")


def to_number(value: Any) -> float | None:
    """A cell as a number, or None for blanks, "NA", dashes and other text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
