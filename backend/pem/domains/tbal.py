"""TBAL: live ION energy meters and the HVAC heat-load / energy model.

Sources (all under `energy/TBAL`, found by words in the file name):
  * "ION Server Data - Live Meters & SEU Snapshot" - the meter snapshot sheet
  * HVAC/"HVAC_ODU_Load_and_Energy_Schedule" - the heat-load and energy formulas
  * HVAC/"TBAL HVAC ECHO" - monthly HVAC energy and running hours since FY 2020-21
  * HVAC/"TBAL Shop floor layout ... HVAC Coverage" - deck with facility facts (text)
  * HVAC/"TBAL SEU details" - deck that is a single picture (no text layer)

Nothing here is estimated: a value the sources do not hold is returned as null
and explained in `notes`.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

from ..config import find_source_file, settings
from ..workbooks import find_sheet, read_sheet, sheet_names, to_number

router = APIRouter(prefix="/api/tbal", tags=["tbal"])

METER_DIR = "energy/TBAL"
HVAC_DIR = "energy/TBAL/HVAC"
STATED_METER_COUNT = 92          # the business figure ("92 energy meters")
LIVE_TIMEOUT_SECONDS = 5.0
LIVE_FAILURE_BACKOFF_SECONDS = 30.0   # do not hammer a dead server on every page load


def _now_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, tz=timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _missing(label: str, where: str) -> str:
    return f"{label} not found in {where}."


# ============================================================================
# 1. Meter snapshot (ION Server Data workbook)
# ============================================================================

def snapshot_path() -> Path | None:
    return find_source_file(METER_DIR, "ion server", "snapshot")


_SECTION_RE = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")

# Unit is inferred only from the sheet's own "Latest_Parameter" label; the sheet has no unit column.
_UNIT_BY_PARAMETER = (
    ("temperature", "°C"),
    ("pressure (bar)", "bar"),
    ("frequency", "Hz"),
    ("current", "A"),
    ("interruptions", "count"),
)


def _unit_for(parameter: str | None) -> str | None:
    low = (parameter or "").lower()
    for key, unit in _UNIT_BY_PARAMETER:
        if key in low:
            return unit
    return None


def _kind(instrument: str | None, section_no: int | None) -> str:
    if instrument:
        return instrument.lower().replace(" meter", "")   # energy / temperature / pressure
    return "service" if section_no == 5 else "unknown"


def load_snapshot() -> dict[str, Any]:
    """Parse the snapshot sheet into meters, found by header text (not cell address).

    Returns {"meters": [...], "seuChartMap": [...], "file": name|None, "fileModified": iso|None, "notes": [...]}.
    """
    notes: list[str] = []
    path = snapshot_path()
    out: dict[str, Any] = {"meters": [], "seuChartMap": [], "file": None, "fileModified": None, "notes": notes}
    if path is None:
        notes.append(_missing("ION server snapshot workbook", METER_DIR))
        return out
    out["file"] = path.name
    out["fileModified"] = _now_iso(path.stat().st_mtime)
    try:
        grid = read_sheet(path, sheet_names(path)[0])
    except (KeyError, IndexError, OSError) as exc:
        notes.append(f"Could not read the snapshot sheet: {exc}")
        return out

    header_row = next((i for i, r in enumerate(grid) if "Section" in [_text(c) for c in r] and "SourceID" in [_text(c) for c in r]), None)
    if header_row is None:
        notes.append("Header row (Section / SourceID ...) not found in the snapshot sheet.")
        return out
    head = [_text(c) for c in grid[header_row]]
    col = {name: head.index(name) for name in head if name}
    need = ("Section", "SourceID", "Full_ION_Meter_Tag")
    if any(n not in col for n in need):
        notes.append("Snapshot sheet is missing one of: " + ", ".join(need))
        return out

    def get(row: list[Any], name: str) -> Any:
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else None

    # The sheet has four unlabelled columns right after Power_kW_hr: a flag ("Individual"), an
    # AHU label, a bay label. They are read by position and flagged as such in the notes.
    after = col.get("Power_kW_hr")
    for grid_row in grid[header_row + 1:]:
        section = _text(get(grid_row, "Section"))
        sid = to_number(get(grid_row, "SourceID"))
        if not section or sid is None:
            continue
        m = _SECTION_RE.match(section)
        section_no = int(m.group(1)) if m else None
        tag = _text(get(grid_row, "Full_ION_Meter_Tag"))
        instrument = _text(get(grid_row, "Instrument")) or None
        parameter = _text(get(grid_row, "Latest_Parameter")) or None

        def extra(offset: int) -> str | None:
            if after is None or after + offset >= len(grid_row):
                return None
            return _text(grid_row[after + offset]) or None

        reading = to_number(get(grid_row, "Live_Value"))
        out["meters"].append({
            "id": int(sid),
            "tag": tag,
            "name": tag.split(".", 1)[-1] if tag else None,
            "tagGroup": tag.split(".", 1)[0] if "." in tag else None,
            "kind": _kind(instrument, section_no),
            "deviceType": instrument,
            "quantity": parameter,
            "reading": reading,
            "unit": _unit_for(parameter),
            "currentA": to_number(get(grid_row, "Current_A")),
            "voltageV": to_number(get(grid_row, "Voltage_V")),
            "powerKwHr": to_number(get(grid_row, "Power_kW_hr")),
            "timestamp": None,
            "sectionNo": section_no,
            "section": m.group(2) if m else section,
            "seu": _text(get(grid_row, "SEU_Chart_Category")) or None,
            "individualFlag": extra(1),
            "ahu": extra(2),
            "bay": extra(3),
        })

    out["seuChartMap"] = _read_seu_chart_map(grid)
    notes.append("The sheet has no timestamp column: per-meter `timestamp` is null and `readAt` is the file's modified time.")
    notes.append("Columns after Power_kW_hr have no header; they were read by position as flag / AHU / bay.")
    notes.append("Units are inferred from the Latest_Parameter label; the sheet has no unit column. "
                 "For 'Power Interruptions Count' rows Live_Value and Power_kW_hr hold the same number, "
                 "and for 'Frequency' rows Live_Value is 0 while Power_kW_hr is large, so what the sheet "
                 "calls Live_Value does not always match its Latest_Parameter label.")
    return out


def _read_seu_chart_map(grid: list[list[Any]]) -> list[dict[str, Any]]:
    """The two-column table at the right of the sheet: SEU chart bar -> meters/feeders behind it."""
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if _text(cell).lower().startswith("significant energy graph"):
                rows = []
                for below in grid[r + 1:]:
                    label = _text(below[c]) if c < len(below) else ""
                    if not label:
                        break
                    feeders = _text(below[c + 1]) if c + 1 < len(below) else ""
                    rows.append({"chartBar": label, "feeders": feeders or None})
                return rows
    return []


def meter_breakdown(meters: list[dict[str, Any]]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for m in meters:
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
    return {
        "rowsInSheet": len(meters),
        "statedMeterCount": STATED_METER_COUNT,
        "byKind": kinds,
        "energyMeters": kinds.get("energy", 0),
    }


# ============================================================================
# 2. Live connector
# ============================================================================

Fetch = Callable[[str, dict[str, str], float], Any]


def _urllib_fetch(url: str, headers: dict[str, str], timeout: float) -> Any:
    """GET `url` and return the decoded JSON. Standard library only."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 - URL comes from settings
        return json.loads(resp.read().decode("utf-8"))


class IonConnector:
    """Reads meters from the TBAL ION server, falling back to the snapshot workbook.

    Every payload says where it came from. Live readings are cached for the poll
    interval; a failed attempt is remembered briefly so a dead server does not
    slow every page load.
    """

    def __init__(self, cfg: Any = None, fetch: Fetch | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._cfg = cfg if cfg is not None else settings
        self._fetch = fetch or _urllib_fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._live: dict[str, Any] | None = None       # {"at": ts, "meters": [...]}
        self._last_error: str | None = None
        self._last_error_at: float | None = None
        self._last_attempt_at: float | None = None
        self._last_success_at: float | None = None

    # -- configuration ------------------------------------------------------
    @property
    def url(self) -> str:
        return (self._cfg.tbal_ion_url or "").strip()

    @property
    def poll_seconds(self) -> int:
        return max(1, int(self._cfg.tbal_ion_poll_seconds or 900))

    def status(self) -> dict[str, Any]:
        """Configuration state without the password."""
        from urllib.parse import urlsplit
        parts = urlsplit(self.url) if self.url else None
        now = self._clock()
        return {
            "configured": bool(self.url),
            "serverUrl": f"{parts.scheme}://{parts.hostname}{':' + str(parts.port) if parts.port else ''}" if parts and parts.hostname else None,
            "userSet": bool(self._cfg.tbal_ion_user),
            "passwordSet": bool(self._cfg.tbal_ion_password),
            "serverId": self._cfg.tbal_ion_server_id or None,
            "pollSeconds": self.poll_seconds,
            "activeSource": "live" if self._live_fresh(now) else "snapshot",
            "lastAttempt": _now_iso(self._last_attempt_at) if self._last_attempt_at else None,
            "lastSuccess": _now_iso(self._last_success_at) if self._last_success_at else None,
            "lastError": self._last_error,
            "cacheAgeSeconds": round(now - self._live["at"], 1) if self._live else None,
            "snapshotFile": (snapshot_path().name if snapshot_path() else None),
        }

    # -- the one method to change when the real server API is known -----------
    def fetch_live_meters(self) -> list[dict[str, Any]]:
        """REQUEST + RESPONSE MAPPING. Replace only this method when the real ION API is known.

        Assumed (not verified - the server's API was not available):
          GET {tbal_ion_url}/api/readings?serverId={tbal_ion_server_id}
          with HTTP Basic auth from tbal_ion_user / tbal_ion_password, answering JSON:
            {"readings": [
               {"sourceId": 27,                      # same number as SourceID in the snapshot sheet
                "tag": "Utility_Building.Main_PCC_Admin_Building",   # optional
                "parameter": "Active Energy Delivered",              # optional
                "value": 12345.6,
                "unit": "kWh",                                       # optional
                "timestamp": "2026-01-01T10:15:00Z"}                 # optional ISO-8601
            ]}
        A bare JSON list of such objects is accepted too. Each reading is merged
        onto the snapshot meter with the same sourceId so grouping (SEU, bay, section)
        is kept; unknown sourceIds are returned with the fields the server gave.
        """
        base = self.url.rstrip("/")
        query = f"?serverId={self._cfg.tbal_ion_server_id}" if self._cfg.tbal_ion_server_id else ""
        headers = {"Accept": "application/json"}
        if self._cfg.tbal_ion_user:
            raw = f"{self._cfg.tbal_ion_user}:{self._cfg.tbal_ion_password}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        data = self._fetch(f"{base}/api/readings{query}", headers, LIVE_TIMEOUT_SECONDS)

        readings = data.get("readings") if isinstance(data, dict) else data
        if not isinstance(readings, list):
            raise ValueError("response has no 'readings' list")
        by_id = {m["id"]: m for m in load_snapshot()["meters"]}
        meters: list[dict[str, Any]] = []
        for item in readings:
            if not isinstance(item, dict):
                continue
            sid = to_number(item.get("sourceId"))
            if sid is None:
                continue
            meter = dict(by_id.get(int(sid)) or {
                "id": int(sid), "tag": item.get("tag"), "name": item.get("tag"), "kind": "unknown",
                "deviceType": None, "quantity": None, "unit": None, "section": None, "seu": None,
                "bay": None, "ahu": None, "tagGroup": None,
            })
            meter["reading"] = to_number(item.get("value"))
            meter["timestamp"] = item.get("timestamp")
            if item.get("parameter"):
                meter["quantity"] = item["parameter"]
            if item.get("unit"):
                meter["unit"] = item["unit"]
            meters.append(meter)
        if not meters:
            raise ValueError("response held no usable readings")
        return meters

    # -- cache and fallback ---------------------------------------------------
    def _live_fresh(self, now: float) -> bool:
        return bool(self._live) and now - self._live["at"] < self.poll_seconds

    def get_meters(self) -> dict[str, Any]:
        """The meter payload: live when available, otherwise the snapshot, with the reason."""
        now = self._clock()
        live_error: str | None = None
        if self.url:
            with self._lock:
                if self._live_fresh(now):
                    return self._payload("live", self._live["meters"], self._live["at"], None, cached=True)
                recent_failure = (self._last_error_at is not None
                                  and now - self._last_error_at < LIVE_FAILURE_BACKOFF_SECONDS)
                if recent_failure:
                    live_error = self._last_error
                else:
                    self._last_attempt_at = now
                    try:
                        meters = self.fetch_live_meters()
                    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
                        live_error = f"{type(exc).__name__}: {exc}"
                        self._last_error, self._last_error_at = live_error, now
                    else:
                        self._live = {"at": now, "meters": meters}
                        self._last_success_at, self._last_error, self._last_error_at = now, None, None
                        return self._payload("live", meters, now, None, cached=False)
        return self._snapshot_payload(live_error)

    def _snapshot_payload(self, live_error: str | None) -> dict[str, Any]:
        snap = load_snapshot()
        modified = snap["fileModified"]
        read_at = modified or _now_iso(self._clock())
        payload = self._payload("snapshot", snap["meters"], None, live_error, cached=False)
        payload["readAt"] = read_at
        payload["readAtKind"] = "snapshot file modified time (the sheet has no timestamps)" if modified else "no snapshot file"
        payload["snapshotFile"] = snap["file"]
        payload["notes"] = snap["notes"] + payload["notes"]
        payload["seuChartMap"] = snap["seuChartMap"]
        return payload

    def _payload(self, source: str, meters: list[dict[str, Any]], at: float | None,
                 live_error: str | None, cached: bool) -> dict[str, Any]:
        notes: list[str] = []
        if not self.url:
            notes.append("tbal_ion_url is blank, so the snapshot workbook is used.")
        if live_error:
            notes.append("Live read was tried and failed; showing the snapshot workbook instead.")
        return {
            "source": source,
            "readAt": _now_iso(at) if at is not None else None,
            "readAtKind": "server read time" if source == "live" else None,
            "cached": cached,
            "liveTried": bool(self.url),
            "liveError": live_error,
            "pollSeconds": self.poll_seconds,
            "meters": meters,
            "breakdown": meter_breakdown(meters),
            "notes": notes,
        }


_connector: IonConnector | None = None


def get_connector() -> IonConnector:
    global _connector
    if _connector is None:
        _connector = IonConnector()
    return _connector


def meters_payload() -> dict[str, Any]:
    return get_connector().get_meters()


def meter_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Counts by section, SEU chart category, device kind, bay and tag group."""
    def tally(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[int]] = {}
        for m in payload["meters"]:
            groups.setdefault(str(m.get(key) or "(none)"), []).append(m["id"])
        return [{"name": k, "count": len(v), "meterIds": sorted(v)} for k, v in groups.items()]

    return {
        "source": payload["source"], "readAt": payload["readAt"], "liveError": payload["liveError"],
        "breakdown": payload["breakdown"],
        "bySection": tally("section"), "bySeu": tally("seu"), "byKind": tally("kind"),
        "byBay": tally("bay"), "byTagGroup": tally("tagGroup"),
        "notes": payload["notes"],
    }


# ============================================================================
# 3. HVAC heat load and energy schedule (HVAC_ODU_Load_and_Energy_Schedule)
# ============================================================================

def schedule_path() -> Path | None:
    return find_source_file(HVAC_DIR, "odu", "schedule")


def echo_path() -> Path | None:
    return find_source_file(HVAC_DIR, "echo")


def _label_value(grid: list[list[Any]], needle: str) -> Any:
    """Value in the first non-empty cell right of the label containing `needle`."""
    for row in grid:
        for c, cell in enumerate(row):
            if needle in _text(cell).lower():
                for v in row[c + 1:]:
                    if v is not None and _text(v) != "":
                        return v
                return None
    return None


def hvac_workbook_inputs() -> dict[str, Any]:
    """Inputs exactly as the workbook holds them, plus the documented kW/TR."""
    notes: list[str] = []
    out: dict[str, Any] = {"installedTr": None, "designDeltaT": None, "designAmbientC": None, "designSetC": None,
                           "baseLoadShare": None, "kwPerTr": None, "hoursPerDay": None,
                           "kwPerTrWorkbookCell": None, "kwPerTrSource": None, "notes": notes}
    path = schedule_path()
    if path is None:
        notes.append(_missing("HVAC ODU load schedule workbook", HVAC_DIR))
        return out
    sheet = find_sheet(path, "load", "schedule")
    if sheet is None:
        notes.append("Sheet 'Load & Energy Schedule' not found.")
        return out
    grid = read_sheet(path, sheet)
    out["installedTr"] = to_number(_label_value(grid, "total installed odu capacity"))
    out["baseLoadShare"] = to_number(_label_value(grid, "base load"))
    out["hoursPerDay"] = to_number(_label_value(grid, "operating window"))
    out["designDeltaT"] = to_number(_label_value(grid, "design peak"))
    for row in grid:
        for cell in row:
            m = re.search(r"design peak.*\((\d+(?:\.\d+)?)\s*°?C\s*to\s*(\d+(?:\.\d+)?)\s*°?C\)", _text(cell), re.I)
            if m:
                out["designAmbientC"], out["designSetC"] = float(m.group(1)), float(m.group(2))
    raw_eff = _label_value(grid, "efficiency ratio")
    out["kwPerTrWorkbookCell"] = None if raw_eff is None else _text(raw_eff)
    eff = to_number(raw_eff)
    if eff is not None:
        out["kwPerTr"], out["kwPerTrSource"] = eff, "Load & Energy Schedule: ODU Compressor Efficiency Ratio cell"
    else:
        doc = _documented_kw_per_tr(path)
        out["kwPerTr"] = doc
        out["kwPerTrSource"] = "Formulas & Derivations sheet text (documented benchmark)" if doc else None
        notes.append(f"The workbook's efficiency cell holds {raw_eff!r}, not a number, so its own kW and kWh columns "
                     "show #VALUE!. The documented benchmark of the Formulas & Derivations sheet is used as the default.")
    return out


def _documented_kw_per_tr(path: Path) -> float | None:
    sheet = find_sheet(path, "formulas")
    if sheet is None:
        return None
    for row in read_sheet(path, sheet):
        for cell in row:
            m = re.search(r"(\d+(?:\.\d+)?)\s*kW per TR", _text(cell), re.I)
            if m:
                return float(m.group(1))
    return None


def formula_notes() -> list[str]:
    path = schedule_path()
    sheet = find_sheet(path, "formulas") if path else None
    if not path or not sheet:
        return []
    return [_text(c) for row in read_sheet(path, sheet) for c in row if _text(c) and not re.fullmatch(r"\d\.", _text(c))]


def compute_schedule(installed_tr: float, design_delta_t: float, base_load_share: float,
                     kw_per_tr: float, hours_per_day: float, steps: list[float] | None = None) -> list[dict[str, Any]]:
    """The workbook's schedule, one row per degree of temperature drop.

    Scenario A (pro-rata):  TR = dT * (installed / design dT)
    Scenario B (real-world): TR = installed*base + dT * (installed*(1-base)/design dT)
    % load = TR / installed;  kW = TR * kW/TR;  kWh = kW * hours per day.
    """
    if steps is None:
        steps = [float(i) for i in range(int(design_delta_t) + 1)]
        if steps[-1] < design_delta_t:
            steps.append(float(design_delta_t))
    rows = []
    for dt in steps:
        a_tr = dt * (installed_tr / design_delta_t)
        b_tr = installed_tr * base_load_share + dt * (installed_tr * (1 - base_load_share) / design_delta_t)
        a_kw, b_kw = a_tr * kw_per_tr, b_tr * kw_per_tr
        rows.append({
            "deltaT": dt,
            "proRata": {"tr": a_tr, "loadPct": a_tr / installed_tr, "kw": a_kw, "kwh": a_kw * hours_per_day},
            "realWorld": {"tr": b_tr, "loadPct": b_tr / installed_tr, "kw": b_kw, "kwh": b_kw * hours_per_day},
        })
    return rows


def workbook_schedule_values() -> dict[str, Any]:
    """The cached computed values in the workbook (TR and % load columns; kW/kWh are #VALUE!)."""
    path = schedule_path()
    sheet = find_sheet(path, "load", "schedule") if path else None
    if not path or not sheet:
        return {"rows": [], "kwKwhColumns": None}
    grid = read_sheet(path, sheet)
    start = next((i for i, r in enumerate(grid) if _text(r[0]).startswith("ΔT")), None)
    if start is None:
        return {"rows": [], "kwKwhColumns": None}
    rows = []
    for r in grid[start + 1:]:
        dt = to_number(r[0])
        if dt is None:
            continue
        rows.append({"deltaT": dt, "proRataTr": to_number(r[1]), "proRataLoad": to_number(r[2]),
                     "realWorldTr": to_number(r[5]), "realWorldLoad": to_number(r[6]),
                     "proRataKw": r[3], "realWorldKwh": r[8]})
    sample = rows[0]["proRataKw"] if rows else None
    return {"rows": rows, "kwKwhColumns": "error" if isinstance(sample, str) and sample.startswith("#") else "values"}


def hvac_schedule(kw_per_tr: float | None = None, hours_per_day: float | None = None,
                  base_load_share: float | None = None, installed_tr: float | None = None,
                  design_delta_t: float | None = None) -> dict[str, Any]:
    wb = hvac_workbook_inputs()
    notes = list(wb["notes"])
    default = {k: wb[k] for k in ("installedTr", "designDeltaT", "baseLoadShare", "kwPerTr", "hoursPerDay")}
    used = {
        "installedTr": installed_tr if installed_tr is not None else default["installedTr"],
        "designDeltaT": design_delta_t if design_delta_t is not None else default["designDeltaT"],
        "baseLoadShare": base_load_share if base_load_share is not None else default["baseLoadShare"],
        "kwPerTr": kw_per_tr if kw_per_tr is not None else default["kwPerTr"],
        "hoursPerDay": hours_per_day if hours_per_day is not None else default["hoursPerDay"],
    }
    overridden = [k for k, v in (("installedTr", installed_tr), ("designDeltaT", design_delta_t),
                                 ("baseLoadShare", base_load_share), ("kwPerTr", kw_per_tr),
                                 ("hoursPerDay", hours_per_day)) if v is not None]
    payload: dict[str, Any] = {
        "units": {"tr": "TR", "kw": "kW", "kwh": "kWh per day", "deltaT": "°C", "loadPct": "fraction of installed TR"},
        "workbook": schedule_path().name if schedule_path() else None,
        "defaults": default, "inputs": used, "overridden": overridden,
        "design": {"ambientC": wb["designAmbientC"], "setC": wb["designSetC"]},
        "kwPerTrWorkbookCell": wb["kwPerTrWorkbookCell"], "kwPerTrSource": wb["kwPerTrSource"],
        "formulas": formula_notes(),
        "rows": [], "totals": None, "notes": notes,
    }
    if any(v is None for v in used.values()):
        missing = [k for k, v in used.items() if v is None]
        notes.append(f"Cannot calculate: no value for {', '.join(missing)}.")
        return payload
    rows = compute_schedule(used["installedTr"], used["designDeltaT"], used["baseLoadShare"],
                            used["kwPerTr"], used["hoursPerDay"])
    payload["rows"] = rows
    peak = rows[-1]
    payload["totals"] = {
        "fullLoadTr": peak["realWorld"]["tr"], "fullLoadKw": peak["realWorld"]["kw"],
        "fullLoadKwhPerDay": peak["realWorld"]["kwh"],
        "idleBaseLoadTr": rows[0]["realWorld"]["tr"],
    }
    notes.append("kW per TR is held constant across part load (the workbook's own simplification).")
    return payload


# ============================================================================
# 4. ECHO workbook: monthly / yearly HVAC energy
# ============================================================================

_MONTHS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]


def _fy_start(label: str) -> int | None:
    m = re.search(r"(\d{4})\s*-\s*(\d{2})", label)
    return int(m.group(1)) if m else None


def _fy_short(label: str) -> str | None:
    m = re.search(r"(\d{4})\s*-\s*(\d{2})", label)
    return f"FY{m.group(1)[2:]}-{m.group(2)}" if m else None


def _month_key(fy_start: int, month_index: int) -> str:
    month = (3 + month_index) % 12 + 1          # index 0 = April
    year = fy_start if month_index < 9 else fy_start + 1
    return f"{year}-{month:02d}"


def _month_label_index(label: Any) -> int | None:
    t = _text(label)[:3].title()
    return _MONTHS.index(t) if t in _MONTHS else None


def _parse_energy_block(grid: list[list[Any]], title_row: int, c0: int) -> dict[str, Any] | None:
    """One bay block of 'Energy Consumption': months down, FY columns across (energy, then hours)."""
    head = next((r for r in range(title_row, min(title_row + 4, len(grid)))
                 if _text(grid[r][c0]) == "Month" and _text(grid[r][c0 + 1]).startswith("FY")), None)
    if head is None:
        return None
    kinds: dict[int, str] = {}
    current = ""
    for c in range(c0 + 1, len(grid[head])):
        if _text(grid[head][c]).startswith("FY"):
            above = _text(grid[head - 1][c]) if head > 0 else ""
            if above:
                current = "hours" if "running" in above.lower() else "energy"
            kinds[c] = current
        elif c > c0 + 1 and _text(grid[head][c]) == "Month":
            break
        elif kinds and _text(grid[head][c]) == "":
            if c - max(kinds) > 1:
                break
    series: dict[str, dict[str, dict[str, float | None]]] = {"energy": {}, "hours": {}}
    reported: dict[str, dict[str, float | None]] = {"energy": {}, "hours": {}}
    for c, kind in kinds.items():
        fy = _text(grid[head][c])
        start = _fy_start(fy)
        if start is None:
            continue
        monthly: dict[str, float | None] = {}
        for r in range(head + 1, min(head + 14, len(grid))):
            idx = _month_label_index(grid[r][c0])
            if idx is not None and _text(grid[r][c0]).title() != "Total":
                monthly[_month_key(start, idx)] = to_number(grid[r][c])
            elif _text(grid[r][c0]).lower() == "total":
                reported[kind][_fy_short(fy) or fy] = to_number(grid[r][c])
        series[kind][_fy_short(fy) or fy] = monthly
    return {"series": series, "reportedTotals": reported}


def hvac_history() -> dict[str, Any]:
    """Monthly energy (kWh) and running hours per bay and FY, from the ECHO workbook."""
    notes: list[str] = []
    out: dict[str, Any] = {"workbook": None, "unit": {"energy": "kWh", "hours": "hours"}, "bays": [],
                           "years": [], "facility": [], "equipmentInputs": [], "notes": notes}
    path = echo_path()
    if path is None:
        notes.append(_missing("TBAL HVAC ECHO workbook", HVAC_DIR))
        return out
    out["workbook"] = path.name
    sheet = find_sheet(path, "energy", "consumption")
    if sheet:
        grid = read_sheet(path, sheet)
        for r, row in enumerate(grid[:6]):
            for c, cell in enumerate(row):
                if _text(cell).lower().startswith("hvac bay"):
                    block = _parse_energy_block(grid, r, c)
                    if block is None:
                        notes.append(f"Block {_text(cell)!r} could not be read.")
                        continue
                    bay = _text(cell).replace("HVAC ", "")
                    energy = block["series"]["energy"]
                    entry = {"bay": bay, "monthlyEnergyKwh": energy, "monthlyRunningHours": block["series"]["hours"],
                             "reportedTotalEnergyKwh": block["reportedTotals"]["energy"],
                             "reportedTotalHours": block["reportedTotals"]["hours"],
                             "sumOfMonthsKwh": {fy: round(sum(v for v in m.values() if v is not None), 2)
                                                for fy, m in energy.items()}}
                    out["bays"].append(entry)
        out["years"] = sorted({fy for b in out["bays"] for fy in b["monthlyEnergyKwh"]})
        for b in out["bays"]:
            for fy, total in b["reportedTotalEnergyKwh"].items():
                if total is not None and abs(total - b["sumOfMonthsKwh"].get(fy, 0)) > 1:
                    notes.append(f"{b['bay']} {fy}: reported total {total} differs from the sum of its months "
                                 f"{b['sumOfMonthsKwh'][fy]}.")
            for fy in b["monthlyEnergyKwh"]:
                if b["reportedTotalEnergyKwh"].get(fy) is None:
                    notes.append(f"{b['bay']} {fy}: the sheet's Total row is blank; see sumOfMonthsKwh (computed here).")
    else:
        notes.append("Sheet 'Energy Consumption' not found in the ECHO workbook.")

    s1 = find_sheet(path, "sheet1")
    if s1:
        g = read_sheet(path, s1)
        h = next((i for i, r in enumerate(g) if any(_text(c).startswith("Sl No") for c in r)), None)
        if h is not None:
            names = [_text(c) for c in g[h]]
            seen: list[dict[str, Any]] = []
            for r in g[h + 1:]:
                if not any(c is not None for c in r):
                    continue
                rec = {n: r[i] for i, n in enumerate(names) if n and r[i] is not None}
                if rec not in seen:
                    seen.append(rec)
            out["facility"] = seen
            notes.append("Sheet1 lists one location (the same row appears twice and is shown once); its 'Year (FY)' "
                         "column holds 'TBAL', and running hours / energy are blank.")
    inp = find_sheet(path, "inputs")
    if inp:
        out["equipmentInputs"] = [{"label": _text(r[1]).rstrip(": ").strip(), "value": r[2]}
                                  for r in read_sheet(path, inp) if _text(r[1]) and r[2] is not None]
    notes.append("The Energy Consumption sheet's FY 2020-21 Total row is blank and a few helper cells below the "
                 "Total rows (e.g. 19,200, 3.0, 48) are unlabelled scratch work; they are ignored.")
    return out


_UNIT_SECTIONS = (
    ("total", "total energy"), ("fixed", "fixed energy"), ("variable", "variable energy"),
    ("hours", "running hours"), ("conditions", "variables"),
)


def hvac_units(fy: str | None = None) -> dict[str, Any]:
    """Per-HVAC-unit monthly kWh (total = fixed AHU + variable ODU), hours and temperatures per FY sheet."""
    notes: list[str] = []
    out: dict[str, Any] = {"workbook": None, "unit": "kWh", "years": {}, "notes": notes}
    path = echo_path()
    if path is None:
        notes.append(_missing("TBAL HVAC ECHO workbook", HVAC_DIR))
        return out
    out["workbook"] = path.name
    for name in sheet_names(path):
        short = _fy_short(name) if name.startswith("FY") else None
        if short is None or (fy and fy != short):
            continue
        grid = read_sheet(path, name)
        sections: dict[str, Any] = {}
        r = 0
        while r < len(grid):
            title = _text(grid[r][0]).lower()
            key = next((k for k, needle in _UNIT_SECTIONS if title.startswith("hvac") and needle in title), None)
            if key is None:
                r += 1
                continue
            hdr = r + 1
            months = [(c, grid[hdr][c]) for c in range(2, len(grid[hdr])) if isinstance(grid[hdr][c], datetime)]
            rows: list[dict[str, Any]] = []
            bay = None
            rr = hdr + 1
            while rr < len(grid) and not _text(grid[rr][0]).lower().startswith("hvac") and rr < hdr + 12:
                a, b = _text(grid[rr][0]), _text(grid[rr][1])
                bay = a or bay
                if a.lower() == "total":
                    rr += 1
                    break
                if b:
                    rows.append({"bay": bay, "name": b,
                                 "monthly": {f"{d.year}-{d.month:02d}": to_number(grid[rr][c]) for c, d in months}})
                rr += 1
            sections[key] = rows
            r = rr
        out["years"][short] = sections
    if fy and not out["years"]:
        notes.append(f"No sheet for {fy}.")
    notes.append("'conditions' rows are set / ambient / room temperature (°C); the second block in each FY sheet is "
                 "labelled 'Bay -1' like the first (probably Bay-2) and its values are identical in the FY sheets read.")
    notes.append("In the FY sheets fixed energy = running hours x 7 and variable = total - fixed (formulas in the "
                 "workbook); this gives small negative variable values for some idle months.")
    return out


def hvac_reconciliation() -> dict[str, Any]:
    """Per FY and bay: 'Energy Consumption' sheet vs the sum of the per-unit FY sheet. Reported, not reconciled."""
    hist, units = hvac_history(), hvac_units()
    rows = []
    for bay in hist["bays"]:
        bay_no = re.search(r"\d", bay["bay"])
        for fy, months in bay["monthlyEnergyKwh"].items():
            tot = units["years"].get(fy, {}).get("total", [])
            members = [u for u in tot if bay_no and re.search(r"(\d)", u["bay"] or "") and
                       re.search(r"(\d)", u["bay"]).group(1) == bay_no.group()]
            for key, value in months.items():
                per_unit = [u["monthly"].get(key) for u in members]
                if value is None or not members or any(v is None for v in per_unit):
                    continue
                s = sum(per_unit)
                rows.append({"bay": bay["bay"], "fy": fy, "month": key, "energyConsumptionSheetKwh": value,
                             "sumOfUnitsKwh": s, "difference": round(value - s, 2)})
    mismatched = [r for r in rows if abs(r["difference"]) > 1]
    return {"compared": len(rows), "mismatched": len(mismatched), "rows": rows}


# ============================================================================
# 5. Decks
# ============================================================================

# Read by eye from the picture in "TBAL SEU details.pptx" (no text layer). Mwh labels as printed above
# each bar, Apr-26 to Sep-26. Bars whose label is unreadable are left out rather than guessed.
SEU_DECK_TRANSCRIPTION = {
    "title": "Significant Energy Use Review FY 2026-27 (April-26 to Sep-26)",
    "unit": "MWh",
    "rule": "Significant energy use: equipment consuming more than 5% of total plant energy consumption",
    "extraction": "transcribed by eye from the picture; not machine-extracted",
    "bars": [
        ("HVAC (Bay-2)", 311, 19, True), ("HVAC (Bay-1)", 207, 12, True), ("Plant Lighting", 184, 11, True),
        ("PAINTBOOTH", 163, 10, True), ("Admin Building", 148, 9, True), ("Paint Booth-3", 96, 6, True),
        ("Air Compressor", 79, 5, True), ("UPS", 68, 4, False), ("Mezzanine Office", 57, 3, False),
        ("200KVA UPS Incomer", 52, 3, False), ("Chiller-3", 50, 3, False), ("737 Sub Assembly HVAC", 47, 3, False),
        ("Raw Power - Utility equip", 31, 2, False), ("Raw Power for Pump house & Stores", 27, 2, False),
        ("Canteen", 27, 2, False), ("Bay-1 Jig lighting & Raw power", 26, 2, False),
        ("Transformer & other losses", 24, 1, False), ("Bay-2 Jig lighting & Raw power", 17, 1, False),
        ("Sealant Mixing Room", 15, 1, False), ("40 KLD STP", 9, 1, False), ("Tool room Machines", 6, 0, False),
        ("Sealant Mixing Room-2", 6, 0, False), ("Pump House", 4, 0, False), ("Battery Charger", 2, 0, False),
        ("6.3 Ton Eot Crane-1&2", 2, 0, False), ("3T EOT Cranes (3Nos)", 1, 0, False),
        ("Paintbooth-Chiller-2 BTT", 0, 0, False), ("Paintbooth-Chiller-1 BTO", 0, 0, False),
    ],
}


def seu_payload() -> dict[str, Any]:
    notes: list[str] = []
    deck = find_source_file(HVAC_DIR, "seu", suffix=".pptx")
    snap = load_snapshot()
    info: dict[str, Any] = {"deckFile": deck.name if deck else None, "deckTextLayer": None, "deckPictures": None}
    if deck is None:
        notes.append(_missing("TBAL SEU details deck", HVAC_DIR))
    else:
        from pptx import Presentation
        slides = list(Presentation(deck).slides)
        pics = sum(1 for s in slides for sh in s.shapes if sh.shape_type == 13)
        text = [sh.text_frame.text.strip() for s in slides for sh in s.shapes
                if sh.has_text_frame and sh.text_frame.text.strip()]
        info.update(slides=len(slides), deckTextLayer=text, deckPictures=pics)
        notes.append(f"The SEU deck has {len(slides)} slide(s), {pics} picture(s) and {len(text)} text boxes: the chart "
                     "is a picture, so its values cannot be extracted as data.")
    bars = [{"name": n, "mwh": v, "sharePct": p, "significant": s} for n, v, p, s in SEU_DECK_TRANSCRIPTION["bars"]]
    return {"deck": info, "deckTranscription": {**{k: v for k, v in SEU_DECK_TRANSCRIPTION.items() if k != "bars"},
                                                 "bars": bars if deck else []},
            "seuChartMap": snap["seuChartMap"], "seuChartMapSource": snap["file"],
            "notes": notes + snap["notes"][:0]}


def coverage_payload() -> dict[str, Any]:
    notes: list[str] = []
    path = find_source_file(HVAC_DIR, "coverage", suffix=".pptx")
    out: dict[str, Any] = {"deckFile": path.name if path else None, "facts": {}, "text": [], "labelsOnDrawing": [], "notes": notes}
    if path is None:
        notes.append(_missing("HVAC coverage layout deck", HVAC_DIR))
        return out
    from pptx import Presentation
    texts: list[str] = []
    for slide in Presentation(path).slides:
        for sh in slide.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                texts.append(sh.text_frame.text.strip())
    out["text"] = [t for t in texts if len(t) > 40]
    out["labelsOnDrawing"] = [t for t in texts if len(t) <= 40]
    blob = "\n".join(texts)

    def num(pattern: str) -> float | None:
        m = re.search(pattern, blob, re.I)
        return float(m.group(1)) if m else None

    facts = {
        "bayLengthM": num(r"Length of Each Bay\s*:\s*([\d.]+)"),
        "bayWidthM": num(r"Width of each Bay\s*:\s*([\d.]+)"),
        "bayMaxHeightM": num(r"Max Height of each Bay\s*:\s*([\d.]+)"),
        "bayMinHeightM": num(r"Minimum Height of each bay\s*:\s*([\d.]+)"),
        "bays": num(r"No of Identical Bays\s*:\s*(\d+)"),
        "hvacPerBay": num(r"No of HVACs in each Bay\s*:\s*(\d+)"),
        "hvacTotal": num(r"Total no of HVACs in Bay-1 & Bay-2\s*:\s*(\d+)"),
        "outdoorUnitsPerHvac": num(r"contains\s*(\d+)\s*Nos of Identical Outdoor"),
        "indoorUnitsPerHvac": num(r"Outdoor Units &\s*(\d+)\s*Nos of Indoor"),
        "supplyDuctHeightM": num(r"Fabric duct at height of\s*([\d.]+)"),
        "returnDuctHeightM": None,
        "recommendedTempMinC": num(r"recommended temperature is\s*(\d+)"),
        "recommendedTempMaxC": num(r"recommended temperature is\s*\d+\s*deg C to\s*(\d+)"),
        "outdoorUnitCoolingKwThermal": num(r"Cooling Capacity:\s*([\d.]+)\s*kW"),
        "outdoorUnitCoolingInputKw": num(r"Cooling \(Cool / Frio\) Mode Power Input\s*:-\s*([\d.]+)"),
        "outdoorUnitModel": None,
        "partitionBetweenBays": "no" if re.search(r"no\s+patrician|no partition", blob, re.I) else None,
    }
    m = re.search(r"Model Name:\s*(\S+)", blob)
    facts["outdoorUnitModel"] = m.group(1) if m else None
    m = re.search(r"Insulated GI duct at height of\s*([\d.]+)", blob)
    facts["returnDuctHeightM"] = float(m.group(1)) if m else None
    out["facts"] = facts
    notes.append("The plan/elevation drawing is made of lines and shapes: only its text labels (bay names, dimensions, "
                 "numbers 1-8) are returned; the geometry and which HVAC serves which area are not extracted.")
    if facts["outdoorUnitsPerHvac"] and facts["hvacTotal"] and facts["outdoorUnitCoolingKwThermal"]:
        n = facts["outdoorUnitsPerHvac"] * facts["hvacTotal"]
        tr = n * facts["outdoorUnitCoolingKwThermal"] / 3.517
        notes.append(f"For comparison (computed): {int(n)} outdoor units x {facts['outdoorUnitCoolingKwThermal']} kW is about "
                     f"{tr:.0f} TR, while the schedule workbook says 460 TR installed and ECHO Sheet1 says 250 TR.")
    return out


# ============================================================================
# Routes (thin)
# ============================================================================

def _filter_meters(payload: dict[str, Any], **criteria: str | None) -> dict[str, Any]:
    meters = payload["meters"]
    for key, wanted in criteria.items():
        if wanted:
            meters = [m for m in meters if wanted.lower() in str(m.get(key) or "").lower()]
    return {**payload, "meters": meters, "count": len(meters)}


@router.get("/meters")
def api_meters(kind: str | None = None, section: str | None = None, seu: str | None = None,
               bay: str | None = None) -> dict[str, Any]:
    return _filter_meters(meters_payload(), kind=kind, section=section, seu=seu, bay=bay)


@router.get("/meters/summary")
def api_meter_summary() -> dict[str, Any]:
    return meter_summary(meters_payload())


@router.get("/meters/{meter_id}")
def api_meter(meter_id: int) -> dict[str, Any]:
    payload = meters_payload()
    meter = next((m for m in payload["meters"] if m["id"] == meter_id), None)
    if meter is None:
        raise HTTPException(404, f"No meter with id {meter_id}")
    return {k: payload[k] for k in ("source", "readAt", "readAtKind", "liveError", "notes")} | {"meter": meter}


@router.get("/connector/status")
def api_connector_status() -> dict[str, Any]:
    return get_connector().status()


@router.get("/hvac/schedule")
def api_hvac_schedule(
    kw_per_tr: float | None = Query(None, gt=0, le=10),
    hours_per_day: float | None = Query(None, ge=0, le=24),
    base_load_share: float | None = Query(None, ge=0, le=1),
    installed_tr: float | None = Query(None, gt=0),
    design_delta_t: float | None = Query(None, gt=0, le=100),
) -> dict[str, Any]:
    return hvac_schedule(kw_per_tr, hours_per_day, base_load_share, installed_tr, design_delta_t)


@router.get("/hvac/history")
def api_hvac_history() -> dict[str, Any]:
    return hvac_history()


@router.get("/hvac/units")
def api_hvac_units(fy: str | None = Query(None, description="e.g. FY24-25")) -> dict[str, Any]:
    return hvac_units(fy)


@router.get("/hvac/reconciliation")
def api_hvac_reconciliation() -> dict[str, Any]:
    return hvac_reconciliation()


@router.get("/hvac/coverage")
def api_hvac_coverage() -> dict[str, Any]:
    return coverage_payload()


@router.get("/seu")
def api_seu() -> dict[str, Any]:
    return seu_payload()
