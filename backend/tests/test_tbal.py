"""TBAL tests run against the real files in energy/TBAL; no network is used.

Values below were read from the sheets themselves:
  ION snapshot sheet: 92 data rows (rows 2-93): 45 'Energy Meter', 41 'Temperature', 1 'Pressure', 5 blank
    instrument (ION system services). Row 2: SourceID 27, Live_Value G2 = 795. Row 7: SourceID 92 (HVAC-3),
    J7 Power_kW_hr = 533607.136, L7 'AHU-5&6', M7 'Bay-2'.
  HVAC_ODU schedule: C5 = 460, C6 = 21, C7 = 0.3, C8 = '.', C9 = 10; F13 = 138; B14 = 21.9047619; F34 = 460.
  ECHO: 'Energy Consumption' G5 (Bay-1 Apr FY2024-25) = 37246, M5 hours = 1028.44, V17 (Bay-2 FY2025-26 total) = 374066;
    C17 (FY2020-21 total) is blank. 'FY 2025-26' C4 (HVAC-1 Apr-25) = 2779.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pem.domains import tbal

pytestmark = pytest.mark.skipif(tbal.snapshot_path() is None or tbal.schedule_path() is None,
                                reason="TBAL source files not available")


def client() -> TestClient:
    app = FastAPI()
    app.include_router(tbal.router)
    return TestClient(app)


def cfg(url="", poll=900, user="", password=""):
    return SimpleNamespace(tbal_ion_url=url, tbal_ion_user=user, tbal_ion_password=password,
                           tbal_ion_server_id="S1", tbal_ion_poll_seconds=poll)


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- meters

def test_snapshot_breakdown_is_92_rows_but_45_energy_meters():
    snap = tbal.load_snapshot()
    assert len(snap["meters"]) == 92
    b = tbal.meter_breakdown(snap["meters"])
    assert b["byKind"] == {"energy": 45, "temperature": 41, "pressure": 1, "service": 5}
    assert b["statedMeterCount"] == 92 and b["energyMeters"] == 45
    assert len({m["id"] for m in snap["meters"]}) == 92


def test_snapshot_known_meters():
    meters = {m["id"]: m for m in tbal.load_snapshot()["meters"]}
    assert meters[27]["reading"] == 795.0 and meters[27]["seu"] == "Admin Building & Server Room"
    assert meters[27]["section"].startswith("TOP SEU") and meters[27]["sectionNo"] == 1
    assert meters[92]["powerKwHr"] == pytest.approx(533607.136)
    assert meters[92]["ahu"] == "AHU-5&6" and meters[92]["bay"] == "Bay-2"
    assert meters[73]["unit"] == "°C" and meters[73]["reading"] == 21.9
    assert meters[1]["kind"] == "service" and meters[1]["deviceType"] is None
    assert all(m["timestamp"] is None for m in meters.values())


def test_seu_chart_map_read_from_sheet():
    m = {r["chartBar"]: r["feeders"] for r in tbal.load_snapshot()["seuChartMap"]}
    assert m["HVAC (Bay-1)"] == "HVAC1 + HVAC2 - Mezzanine office"
    assert len(m) == 26


def test_snapshot_missing_file(monkeypatch):
    monkeypatch.setattr(tbal, "find_source_file", lambda *a, **k: None)
    snap = tbal.load_snapshot()
    assert snap["meters"] == [] and snap["notes"]
    assert tbal.IonConnector(cfg()).get_meters()["source"] == "snapshot"
    assert tbal.hvac_history()["bays"] == [] and tbal.hvac_schedule()["rows"] == []
    assert tbal.coverage_payload()["notes"] and tbal.hvac_units()["notes"]


# ------------------------------------------------------------- connector

def fake_response():
    return {"readings": [
        {"sourceId": 27, "value": 1111.5, "unit": "kWh", "parameter": "Active Energy", "timestamp": "2026-01-01T10:15:00Z"},
        {"sourceId": 999, "tag": "New.Meter", "value": 3},
        {"sourceId": "x", "value": 1},
    ]}


def test_blank_url_uses_snapshot():
    calls = []
    p = tbal.IonConnector(cfg(""), fetch=lambda *a: calls.append(a)).get_meters()
    assert calls == [] and p["source"] == "snapshot" and p["liveTried"] is False and p["liveError"] is None
    assert p["readAt"] and p["breakdown"]["rowsInSheet"] == 92


def test_live_mapping_and_cache():
    clock, calls = Clock(), []

    def fetch(url, headers, timeout):
        calls.append((url, headers, timeout))
        return fake_response()

    con = tbal.IonConnector(cfg("http://ion.example/", poll=900, user="u", password="secret"), fetch=fetch, clock=clock)
    first = con.get_meters()
    assert first["source"] == "live" and first["cached"] is False and first["liveError"] is None
    assert calls[0][0] == "http://ion.example/api/readings?serverId=S1"
    assert calls[0][1]["Authorization"].startswith("Basic ")
    by_id = {m["id"]: m for m in first["meters"]}
    assert by_id[27]["reading"] == 1111.5 and by_id[27]["unit"] == "kWh"
    assert by_id[27]["timestamp"] == "2026-01-01T10:15:00Z"
    assert by_id[27]["seu"] == "Admin Building & Server Room"      # grouping kept from the snapshot
    assert by_id[999]["tag"] == "New.Meter" and len(first["meters"]) == 2
    clock.t += 899
    assert con.get_meters()["cached"] is True and len(calls) == 1
    clock.t += 2
    assert con.get_meters()["cached"] is False and len(calls) == 2


def test_live_failure_falls_back_and_backs_off():
    clock, calls = Clock(), []

    def fetch(url, headers, timeout):
        calls.append(1)
        raise TimeoutError("timed out")

    con = tbal.IonConnector(cfg("http://ion.example"), fetch=fetch, clock=clock)
    p = con.get_meters()
    assert p["source"] == "snapshot" and p["liveTried"] is True and "TimeoutError" in p["liveError"]
    assert p["breakdown"]["rowsInSheet"] == 92
    con.get_meters()
    assert len(calls) == 1                                       # backoff: no immediate retry
    clock.t += tbal.LIVE_FAILURE_BACKOFF_SECONDS + 1
    con.get_meters()
    assert len(calls) == 2


def test_bad_live_shape_falls_back():
    con = tbal.IonConnector(cfg("http://ion.example"), fetch=lambda *a: {"nope": 1})
    p = con.get_meters()
    assert p["source"] == "snapshot" and "readings" in p["liveError"]


def test_status_never_exposes_password():
    con = tbal.IonConnector(cfg("http://user:pw@ion.example:8080/x", user="u", password="secret"))
    s = con.status()
    assert s["configured"] and s["passwordSet"] and s["userSet"]
    assert "secret" not in str(s) and "pw" not in str(s["serverUrl"])
    assert s["serverUrl"] == "http://ion.example:8080" and s["pollSeconds"] == 900
    assert tbal.IonConnector(cfg("")).status()["configured"] is False


# --------------------------------------------------------- HVAC schedule

def test_schedule_inputs_and_documented_kw_per_tr():
    wb = tbal.hvac_workbook_inputs()
    assert (wb["installedTr"], wb["designDeltaT"], wb["baseLoadShare"], wb["hoursPerDay"]) == (460, 21, 0.3, 10)
    assert wb["kwPerTrWorkbookCell"] == "." and wb["kwPerTr"] == 1.15
    assert wb["designAmbientC"] == 45 and wb["designSetC"] == 24


def test_schedule_reproduces_workbook_computed_columns():
    ref = tbal.workbook_schedule_values()
    assert ref["kwKwhColumns"] == "error"                       # workbook's own kW/kWh show #VALUE!
    p = tbal.hvac_schedule()
    assert len(p["rows"]) == len(ref["rows"]) == 22
    for mine, theirs in zip(p["rows"], ref["rows"]):
        assert mine["deltaT"] == theirs["deltaT"]
        assert mine["proRata"]["tr"] == pytest.approx(theirs["proRataTr"], rel=1e-9, abs=1e-9)
        assert mine["proRata"]["loadPct"] == pytest.approx(theirs["proRataLoad"], abs=1e-9)
        assert mine["realWorld"]["tr"] == pytest.approx(theirs["realWorldTr"], rel=1e-9, abs=1e-9)
        assert mine["realWorld"]["loadPct"] == pytest.approx(theirs["realWorldLoad"], abs=1e-9)
    # F13 = 138, B14 = 21.9047619, F34 = 460 as cached in the workbook
    assert p["rows"][0]["realWorld"]["tr"] == pytest.approx(138)
    assert p["rows"][1]["proRata"]["tr"] == pytest.approx(21.9047619, abs=1e-6)
    assert p["rows"][-1]["realWorld"]["tr"] == pytest.approx(460)
    # Formulas sheet: kW = TR x kW/TR (1.15), kWh = kW x 10 h
    assert p["rows"][-1]["realWorld"]["kw"] == pytest.approx(529.0)
    assert p["rows"][-1]["realWorld"]["kwh"] == pytest.approx(5290.0)
    assert p["totals"]["idleBaseLoadTr"] == pytest.approx(138)


def test_schedule_overrides():
    p = tbal.hvac_schedule(kw_per_tr=1.0, hours_per_day=8, base_load_share=0.5)
    assert p["overridden"] == ["baseLoadShare", "kwPerTr", "hoursPerDay"]
    assert p["rows"][0]["realWorld"]["tr"] == pytest.approx(230)
    assert p["rows"][-1]["realWorld"]["kwh"] == pytest.approx(460 * 1.0 * 8)
    r = client().get("/api/tbal/hvac/schedule", params={"kw_per_tr": 1.2, "hours_per_day": 12}).json()
    assert r["inputs"]["kwPerTr"] == 1.2 and r["defaults"]["kwPerTr"] == 1.15
    assert client().get("/api/tbal/hvac/schedule", params={"base_load_share": 2}).status_code == 422


# ------------------------------------------------------- ECHO history

def test_history_values():
    h = tbal.hvac_history()
    bays = {b["bay"]: b for b in h["bays"]}
    assert h["years"] == ["FY20-21", "FY21-22", "FY22-23", "FY23-24", "FY24-25", "FY25-26"]
    assert bays["Bay-1"]["monthlyEnergyKwh"]["FY24-25"]["2024-04"] == 37246
    assert bays["Bay-1"]["monthlyRunningHours"]["FY24-25"]["2024-04"] == pytest.approx(1028.44)
    assert bays["Bay-2"]["reportedTotalEnergyKwh"]["FY25-26"] == 374066
    assert bays["Bay-2"]["reportedTotalEnergyKwh"]["FY20-21"] is None      # blank Total in the sheet
    assert bays["Bay-1"]["monthlyEnergyKwh"]["FY25-26"]["2026-03"] == 22134
    assert h["facility"][0]["Total Connected Load (TR)"] == 250 and h["facility"][0]["Volume (CUM)"] == 64638
    assert any(i["label"] == "Model Name" and i["value"] == "AM260KXVANH" for i in h["equipmentInputs"])


def test_units_and_reported_discrepancy():
    y = tbal.hvac_units("FY25-26")["years"]["FY25-26"]
    assert y["total"][0]["name"] == "HVAC-1" and y["total"][0]["monthly"]["2025-04"] == 2779
    assert len(y["total"]) == 8 and len(y["hours"]) == 8
    assert tbal.hvac_units("FY20-21")["years"]["FY20-21"]["total"][0]["monthly"]["2020-05"] == 9992
    rec = tbal.hvac_reconciliation()
    row = next(r for r in rec["rows"] if r["fy"] == "FY25-26" and r["month"] == "2025-04" and r["bay"] == "Bay-1")
    assert row["energyConsumptionSheetKwh"] == 28491 and row["sumOfUnitsKwh"] == 19095
    assert rec["mismatched"] > 0


# ------------------------------------------------------------- decks

def test_coverage_and_seu_decks():
    c = tbal.coverage_payload()["facts"]
    assert (c["bayLengthM"], c["bayWidthM"], c["hvacTotal"], c["outdoorUnitsPerHvac"]) == (171, 27, 8, 3)
    assert c["recommendedTempMinC"] == 23 and c["recommendedTempMaxC"] == 29 and c["returnDuctHeightM"] == 6.7
    s = tbal.seu_payload()
    assert s["deck"]["deckTextLayer"] == [] and s["deck"]["deckPictures"] == 1
    assert s["deckTranscription"]["bars"][0]["name"] == "HVAC (Bay-2)"


# ----------------------------------------------------------- endpoints

def test_endpoints(monkeypatch):
    monkeypatch.setattr(tbal, "_connector", tbal.IonConnector(cfg("")))
    c = client()
    m = c.get("/api/tbal/meters", params={"kind": "energy"}).json()
    assert m["source"] == "snapshot" and m["count"] == 45
    s = c.get("/api/tbal/meters/summary").json()
    assert s["breakdown"]["rowsInSheet"] == 92 and any(g["name"] == "Bay-2" for g in s["byBay"])
    one = c.get("/api/tbal/meters/92").json()
    assert one["meter"]["ahu"] == "AHU-5&6" and one["source"] == "snapshot"
    assert c.get("/api/tbal/meters/9999").status_code == 404
    st = c.get("/api/tbal/connector/status").json()
    assert st["configured"] is False and "password" not in str(st).lower().replace("passwordset", "")
    for path in ("/hvac/history", "/hvac/units", "/hvac/coverage", "/seu", "/hvac/reconciliation"):
        assert c.get("/api/tbal" + path).status_code == 200
