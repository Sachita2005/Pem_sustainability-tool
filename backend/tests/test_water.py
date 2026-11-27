"""Water module tests: real source files, values read by hand from the sheets (cell refs in comments)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pem import workbooks
from pem.domains import water

pytestmark = pytest.mark.skipif(workbooks.calculation_workbook() is None, reason="source workbooks not available")

app = FastAPI()
app.include_router(water.router)
client = TestClient(app)


def _month(rows, month):
    return next(r for r in rows if r["month"] == month)


# --- monthly -------------------------------------------------------------------------------

def test_monthly_tlmal_matches_sheet():
    # Calculation > "Water consumption data": TLMAL block, Apr-26 column (H):
    # H4 Municipality 363, H5 Borewell 680, H9 Total 1043, H15 STP garden 758, H35 treated process 60, H36 reuse 0.7842761266
    data = client.get("/api/water/monthly", params={"plant": "TLMAL", "fy": "FY26-27"}).json()
    assert data["unit"] == "KL" and data["entity"] == "TLMAL"
    apr = _month(data["months"], "2026-04")
    assert apr["fresh"]["municipal"] == 363 and apr["fresh"]["borewell"] == 680 and apr["fresh"]["total"] == 1043
    assert apr["sewageReused"]["garden"] == 758 and apr["processReused"]["total"] == 60
    assert apr["reusePct"] == pytest.approx(78.4, abs=0.05) and apr["reusePctSheet"] == pytest.approx(78.4, abs=0.05)
    assert apr["fy"] == "FY26-27" and apr["source"] == "calc"
    # Later blank months are not invented: Nov-26 is not reported yet.
    assert all(r["month"] <= "2026-10" for r in data["months"])


def test_monthly_full_history():
    rows = water.entity_monthly("TLMAL")
    assert rows[0]["month"] == "2018-04" and rows[-1]["month"] == "2026-10"
    assert len({r["month"] for r in rows}) == len(rows)
    # Mar-21 borewell is 862 in the master sheet (the downstream copy "Water Data Sheet" has the typo 8620).
    assert _month(rows, "2021-03")["fresh"]["borewell"] == 862


def test_unreported_total_is_null_not_zero():
    # TBAL has no Oct-26 entries; the sheet's total formula shows 0 there.
    assert "2026-10" not in {r["month"] for r in water.entity_monthly("TBAL")}


def test_entry_workbook_fills_blank_and_is_marked():
    # TBAL domestic Canteen Jun-26 is blank in the calculation workbook and 135 in "Only to Enter".
    rows = water.entity_monthly("TBAL")
    jun = _month(rows, "2026-06")
    assert jun["source"] == "entry"
    assert "domestic.Canteen" in jun["filledFromEntry"]


def test_detail_and_rwh_garden():
    # TASL-MCA block, Oct-26: 'RWH for Garden' 28 KL.
    rows = water.entity_monthly("TASL-MCA", detail=True)
    last = _month(rows, "2026-10")
    assert last["rwhGarden"] == 28
    assert last["domesticDetail"]["RWH for Garden"] == 28
    assert "Canteen" in last["domesticDetail"]


def test_tadl_is_sum_of_plants():
    # Municipality Apr-26: TLMAL 363 + TSAL 922 + TASL-NGP 17170 (other plants 0) = 18455.
    tadl = _month(water.entity_monthly("TADL"), "2026-04")
    assert tadl["fresh"]["municipal"] == 18455
    assert tadl["plantsReporting"] == 8
    dp = _month(water.entity_monthly("DP"), "2026-04")
    assr = _month(water.entity_monthly("ASSY"), "2026-04")
    assert dp["fresh"]["total"] + assr["fresh"]["total"] == pytest.approx(tadl["fresh"]["total"], abs=0.02)


def test_unknown_plant_is_a_note_not_an_error():
    data = client.get("/api/water/monthly", params={"plant": "Atlantis"}).json()
    assert data["months"] == [] and "Unknown plant" in data["notes"][0]


# --- yearly / reuse -------------------------------------------------------------------------

def test_yearly_matches_fy_column_and_water_summary():
    # FY 26-27 column (S4) of the TLMAL block: municipality 2444 = sum of Apr..Oct-26.
    tl = {r["fy"]: r for r in water.entity_yearly("TLMAL")}
    assert tl["FY26-27"]["fresh"]["municipal"] == 2444 and tl["FY26-27"]["monthsReported"] == 7
    assert tl["FY26-27"]["complete"] is False
    # "Water Summary" TADL block: Fresh Water FY 25 (= FY24-25) 3.002e5 KL, FY 19 (= FY18-19) 1.99e5.
    tadl = {r["fy"]: r for r in water.entity_yearly("TADL")}
    assert tadl["FY24-25"]["fresh"]["total"] == pytest.approx(300161.92, abs=0.5)
    assert tadl["FY18-19"]["fresh"]["total"] == pytest.approx(199001.87, abs=0.5)
    assert "FY19-20" in tadl and "FY25-26" in tadl


def test_yearly_endpoint_lists_all_entities():
    data = client.get("/api/water/yearly").json()
    ids = [e["entity"] for e in data["entities"]]
    assert ids[:3] == ["TADL", "DP", "ASSY"] and len(ids) == 11
    one = client.get("/api/water/yearly", params={"plant": "tcoe"}).json()
    assert [e["entity"] for e in one["entities"]] == ["TCOE"]


def test_reuse_pct_formula_equals_sheet_row():
    # The sheet row is (sewage reused + process reused) / fresh; every plant-month agrees within rounding.
    off = 0
    for plant in water.PLANT_IDS:
        for r in water.entity_monthly(plant):
            if r["reusePct"] is not None and r["reusePctSheet"] is not None and r["source"] == "calc":
                off += abs(r["reusePct"] - r["reusePctSheet"]) > 0.2
    assert off <= 1  # one known disagreement: TLMAL Jun-26 (sheet 105.0%, components give 115.7%)


def test_reuse_payload_has_workbook_summaries():
    data = client.get("/api/water/reuse", params={"plant": "TADL"}).json()
    assert data["reusePctUnit"] == "%" and data["yearly"] and data["monthly"]
    # GEM sheet: year 2024 -> 0.2912 -> 29.12 %
    assert data["gemReusePct"]["FY24-25"] == pytest.approx(29.12, abs=0.01)
    # Water Summary TADL block, Total Reuse water %age FY 19 = 0.1274
    assert data["workbookSummary"]["Total Reuse water %age"]["FY18-19"] == pytest.approx(12.7, abs=0.1)


def test_percapita_sheet_is_reported_as_it_is():
    data = client.get("/api/water/percapita").json()
    # 'Percapita water consumption' B2/E2: TLMAL treated sewage reused, FY 22 column = 7022
    sewage = next(v for k, v in data["plants"]["TLMAL"].items() if k.startswith("Treated Sewage"))
    assert sewage["FY21-22"] == 7022
    assert "TASL-BLR" not in data["plants"]


# --- ground water and rainwater --------------------------------------------------------------

def test_groundwater():
    data = client.get("/api/water/groundwater").json()
    tl = next(p for p in data["plants"] if p["plant"] == "TLMAL")
    assert tl["level"]["FY21-22"] == 223 and tl["levelToRainfallRatio"]["FY21-22"] == pytest.approx(0.1889, abs=1e-4)
    assert data["rainfallMm"]["FY22-23"] == 1388
    blr = next(p for p in data["plants"] if p["plant"] == "TASL-BLR")
    assert all(v is None for v in blr["level"].values())


def test_rwh():
    data = client.get("/api/water/rwh").json()
    tl = next(p for p in data["pits"] if p["plant"] == "TLMAL")
    assert tl["pits"] == 4 and tl["dimensions"] == "4m dia x 4m Depth"
    assert len(data["pits"]) == 8
    assert data["rainwaterUsedForGarden"]["plants"]["TASL-MCA"]["monthly"]["2026-04"] == 43
    assert "FY25-26" in data["estimatedCollection"]["plants"]["TLMAL"]
    assert "FY20-21" not in data["estimatedCollection"]["plants"]["TLMAL"]  # power blocks, not rain


# --- meters -----------------------------------------------------------------------------------

def test_meters():
    data = client.get("/api/water/meters").json()
    by = {p["plant"]: p for p in data["plants"]}
    assert len(by) == 8
    # Water Flow Meter Status.xlsx row 11 (TASL-HYD): 25 meters, "Analog- 8 and Digital- 17", Installed.
    assert by["TASL-HYD"]["available"] == 25 and by["TASL-HYD"]["byType"] == {"analog": 8, "digital": 17}
    assert by["TASL-HYD"]["installationStatusNormalised"] == "installed"
    assert by["TCOE"]["byType"] == {"analog": 12, "digital": 2} and by["TCOE"]["unspecifiedType"] == 4
    assert by["TASL-BLR"]["toBeInstalled"] == 3 and by["TASL-BLR"]["expectedCompletionComment"].endswith("Kindly provide ECD")
    assert by["TASL-NGP"]["available"] is None and "TASL-NGP" in data["totals"]["plantsWithoutCounts"]
    assert data["totals"]["available"] == 6 + 24 + 18 + 14 + 12 + 25 + 13
    assert data["totals"]["toBeInstalled"] == 3 + 0 + 9 + 8 + 0 + 3
    assert all(p["areas"] == [] and p["statusCounts"]["working"] is None for p in data["plants"])
    assert any("plant-level only" in n for n in data["notes"])


# --- overview ---------------------------------------------------------------------------------

def test_overview_shape():
    data = client.get("/api/water/overview").json()
    assert data["tadl"]["latestMonth"] == "2026-10" and data["tadl"]["fy"] == "FY26-27"
    # TASL-NGP block, Oct-26: municipality 15070 (col N), FY-to-date = sum Apr..Oct-26 = 100760
    ngp = next(p for p in data["plants"] if p["entity"] == "TASL-NGP")
    assert ngp["latest"]["municipal"] == 15070 and ngp["fyToDate"]["municipal"] == 100760
    assert len(data["plants"]) == 8 and [c["entity"] for c in data["clusters"]] == ["DP", "ASSY"]
    assert data["meters"]["available"] == 112
    # TBAL and TSAL have nothing for Oct-26: their own latest month is earlier, and the note says so.
    tsal = next(p for p in data["plants"] if p["entity"] == "TSAL")
    assert tsal["latestMonth"] < "2026-10"
    assert any("not reported" in n for n in data["notes"])


# --- missing files -----------------------------------------------------------------------------

def test_missing_files_give_empty_valid_payloads(monkeypatch):
    monkeypatch.setattr(workbooks, "calculation_workbook", lambda: None)
    monkeypatch.setattr(workbooks, "entry_workbook", lambda: None)
    monkeypatch.setattr(water, "find_source_file", lambda *a, **k: None)
    for url in ("overview", "monthly", "yearly", "reuse", "rwh", "groundwater", "percapita", "meters"):
        res = client.get(f"/api/water/{url}")
        assert res.status_code == 200, url
        body = res.json()
        assert body["notes"], url
    assert client.get("/api/water/monthly").json()["months"] == []
    assert client.get("/api/water/meters").json()["plants"] == []
    assert client.get("/api/water/overview").json()["tadl"] is None
