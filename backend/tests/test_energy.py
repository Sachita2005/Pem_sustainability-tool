"""Energy module tests against the real source workbooks (first run opens them: about three minutes)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pem.domains import energy

app = FastAPI()
app.include_router(energy.router)
client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def warmed():
    energy.clear_cache()
    energy.warm()
    yield


def _month(plant, month):
    return energy.load_power()["plants"][plant][month]


def test_tlmal_apr26_matches_sheet():
    # 'Energy consumption data' E5:E9 (TLMAL Apr-26)
    r = _month("TLMAL", "2026-04")
    assert r["grid"] == 198337 and r["solar"] == 145887 and r["purchased"] == 0
    assert r["dg"] == pytest.approx(32.16) and r["total"] == pytest.approx(344256.16)


def test_text_typed_number_is_read():
    # G5 holds the text '2,23,101'; the sheet's SUM ignores it (G9 = 133420)
    r = _month("TLMAL", "2026-06")
    assert r["grid"] == 223101 and r["workbookTotal"] == 133420


def test_bangalore_uses_discounted_grid_row():
    # E30 = 1,213,000 gross; E31 = E30 - 38% = 752,060; E34 = 250,000 RE; E35 total 1,026,726
    r = _month("TASL-BLR", "2026-04")
    assert r["grid"] == 752060 and r["gridGross"] == 1213000 and r["purchased"] == 250000
    assert r["total"] == 1026726


def test_summary_tables_not_read_as_plants():
    assert set(energy.load_power()["plants"]) == set(energy.PLANT_IDS)


def test_tadl_fy2526_matches_summary_sheet():
    # 'TADL Sustainability Summary (M)' FY 25-26: rows 2,3,5,6
    years = {r["fy"]: r for r in energy.power_yearly()["clusters"]["TADL"]}
    y = years["FY25-26"]
    assert y["grid"] == pytest.approx(72651365) and y["solar"] == pytest.approx(1418626.7)
    assert y["dg"] == pytest.approx(325065.503) and y["purchased"] == 10128978
    assert y["monthsReported"] == 12 and "FY18-19" in years


def test_clusters():
    data = energy.power_monthly()
    assert set(data["clusters"]) == {"DP", "ASSY", "TADL"}
    m = data["clusters"]["DP"][-1]
    assert m["plantsExpected"] == 4


def test_diesel_values():
    # 'Diesel consumption data' F4 (TLMAL DG Apr-26) = 0.29 KL; NGP Apr-26 DG 2.0, HWG 25.55
    d = energy.load_diesel()["plants"]
    assert d["TLMAL"]["2026-04"]["dg"] == pytest.approx(0.29)
    assert d["TASL-NGP"]["2026-04"]["hwg"] == pytest.approx(25.55)


def test_emissions_summary_values_and_factors():
    # 'TADL Sustainability Summary (M)' J57 / J64 (FY 25-26)
    em = energy.emissions("TADL")
    y = {r["fy"]: r for r in em["plants"][0]["years"]}["FY25-26"]
    assert y["scope1"] == pytest.approx(1568.804865) and y["scope2Gross"] == pytest.approx(60207361.49)
    assert any(f["factor"] == 0.823 for f in em["factors"])
    assert em["monthly"]["lastPopulatedMonth"] == "2024-10"


def test_revenue_manpower_targets():
    rev = {s["plant"]: s for s in energy.revenue()["plants"]}
    # 'TADL Revenue Summary' C4 (NGP Apr-25) = 25.13; Summary sheet FY25-26 revenue 3979 Cr
    assert rev["TASL-NGP"]["months"][0] == {"month": "2025-04", "fy": "FY25-26", "value": 25.13}
    assert {r["fy"]: r["value"] for r in rev["TADL"]["years"]}["FY25-26"] == 3979
    snap = {r["label"]: r for r in energy.manpower()["latestSnapshot"]}
    assert snap["TASL H01"]["total"] == 1351 and snap["TASL H01"]["plant"] == "TASL-HYD"
    t = [x for x in energy.targets()["targets"] if x["category"] == "Carbon Emission - 2030"]
    assert t and t[0]["reductionFraction"] == 0.75


def test_overview_shape():
    o = energy.overview()
    assert o["latestMonth"] == "2026-10" and o["fy"] == "FY26-27"
    assert len(o["plants"]) == 8 and o["tadl"]["fyToDate"]["monthsReported"] >= 7


def test_routes_shapes():
    for url in ("/api/energy/overview", "/api/energy/monthly?plant=TLMAL&fy=FY25-26", "/api/energy/yearly",
                "/api/energy/diesel", "/api/energy/emissions", "/api/energy/revenue", "/api/energy/manpower",
                "/api/energy/intensity", "/api/energy/afforestation", "/api/energy/targets"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert "notes" in r.json()
    assert len(client.get("/api/energy/monthly?plant=TLMAL&fy=FY25-26").json()["plants"][0]["months"]) == 12


def test_missing_files_give_empty_payloads(monkeypatch):
    monkeypatch.setattr(energy.workbooks, "calculation_workbook", lambda: None)
    monkeypatch.setattr(energy.workbooks, "entry_workbook", lambda: None)
    energy.clear_cache()
    try:
        o = energy.overview()
        assert o["latestMonth"] is None and o["notes"]
        assert energy.revenue()["plants"][0]["months"] == []
        assert energy.targets()["targets"] == []
    finally:
        monkeypatch.undo()
        energy.clear_cache()
