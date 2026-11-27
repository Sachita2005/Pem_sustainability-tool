"""Tests for the production module, against the real source files.

Cell references are to "Power Consumption wrt Production Units (1).xlsx".
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pem import workbooks as wb
from pem.domains import production as P

pytestmark = pytest.mark.skipif(wb.production_workbook() is None, reason="source workbook not available")


def _fam(site, fid):
    d = P.family_detail(site, fid)
    assert d is not None, (site, fid)
    return d


def _at(f, series, month):
    return series[f["months"].index(month)]


def test_eight_sites_mapped():
    sites = {s["code"]: s for s in P.list_sites()["sites"]}
    assert set(sites) == {"TSAL", "B01", "TBAL", "H07", "TLMAL", "N01", "H01", "H06"}
    assert {c: s["plant"] for c, s in sites.items()} == {
        "TSAL": "TSAL", "B01": "TASL-BLR", "TBAL": "TBAL", "H07": "TASL-MCA",
        "TLMAL": "TLMAL", "N01": "TASL-NGP", "H01": "TASL-HYD", "H06": "TCOE"}
    assert sites["H06"]["mappingConfidence"] == "medium"
    assert sites["TSAL"]["familyCount"] == 11


def test_tsal_values():
    f = _fam("TSAL", "paint-booth-leo")  # TSAL!D5 (kWh) and D6 (sq.ft), Apr-25
    assert _at(f, f["energy"]["values"], "2025-04") == 33530.0
    assert _at(f, f["production"]["values"], "2025-04") == pytest.approx(24739.9)
    assert _at(f, f["energyPerUnit"]["computedValues"], "2025-04") == pytest.approx(1.3553, abs=1e-4)
    assert f["energy"]["sheetTotal"] == pytest.approx(259603.1)  # TSAL!P5
    assert f["category"] == "paint"


def test_text_dates_resolve_to_real_months():
    # TASL-B01 CCA#1 header cells are "Apr-25" read as 25-Apr-2026 by Excel
    f = _fam("B01", "cca-1")
    assert f["months"][0] == "2025-04" and f["months"][-1] == "2026-03"
    assert _at(f, f["energy"]["values"], "2025-04") == 10352.0
    assert P._month("Sept'25") == "2025-09"
    import datetime as dt
    assert P._month(dt.datetime(2026, 1, 26)) == "2026-01"
    assert P._month(dt.datetime(2025, 4, 1)) == "2025-04"


def test_blank_months_stay_null_and_zero_production_gives_null_ratio():
    f = _fam("B01", "cca-8")  # TASL-B01 CCA#8 kWh blank Apr..Sep-25
    assert _at(f, f["energy"]["values"], "2025-04") is None
    assert _at(f, f["energy"]["values"], "2025-10") == 21400.0
    g = _fam("TLMAL", "f16-ss")  # SS = 0 -> sheet shows #DIV/0!
    assert _at(g, g["energyPerUnit"]["computedValues"], "2025-04") is None
    assert _at(g, g["energy"]["values"], "2025-04") == 130892.0


def test_vertical_tlmal_blocks():
    f = _fam("TLMAL", "hvac")  # TLMAL A3:D3
    assert _at(f, f["energy"]["values"], "2025-04") == 177513.0
    assert _at(f, f["production"]["values"], "2025-04") == 261954.0
    assert _at(f, f["energyPerUnit"]["sheetValues"], "2025-04") == pytest.approx(0.6776, abs=1e-3)
    c = _fam("TLMAL", "c130j-ss")
    assert _at(c, c["production"]["values"], "2025-04") == 2.0
    assert c["category"] == "ship-set"


def test_dash_production_is_null():
    f = _fam("H07", "paint-booth")  # TASL H07 area painted is '-' every month
    assert all(v is None for v in f["production"]["values"])
    assert _at(f, f["energy"]["values"], "2025-04") == 15815.5


def test_h01_targets_from_deck():
    f = _fam("H01", "hvac-1")  # deck slide 32: target 0.05 MWh/h flat
    assert f["target"]["verifiedAgainstSheet"] is True
    assert set(f["target"]["values"].values()) == {50.0}
    assert _at(f, f["energyPerUnit"]["computedValues"], "2025-04") == pytest.approx(44.1022, abs=1e-3)
    r = _fam("H01", "revenue")
    assert set(r["target"]["values"].values()) == {15.0}
    assert _fam("TSAL", "plant-hvac")["target"] is None


def test_mca_targets_flagged_unverified():
    t = _fam("H07", "paint-booth")["target"]
    assert t is not None and t["verifiedAgainstSheet"] is False
    assert t["values"]["2025-04"] == pytest.approx(52.8578688)


def test_comparisons():
    c = P.compare_within_site("H01", "energy")
    assert len(c["series"]) == 8 and c["months"][0] == "2025-04"
    cat = P.compare_category("compressor")
    assert {s["site"] for s in cat["series"]} >= {"TSAL", "B01", "TBAL", "H07", "TLMAL", "H01", "H06"}
    assert P.compare_category("nonsense")["series"] == []


def test_sources_comparison():
    c = P.compare_sources()
    assert c["moreCompleteSource"].startswith("Power Consumption")
    assert c["tsalComparison"]["kwhCellsCompared"] == 99  # 11 families x Apr..Dec-25
    assert c["tsalComparison"]["kwhMismatches"] == 0
    assert c["entrySheet"]["plants"]["TASL- NGP"]["filledValueCells"] == 0


def test_routes_and_missing_file(monkeypatch):
    app = FastAPI()
    app.include_router(P.router)
    cl = TestClient(app)
    assert cl.get("/api/production/sites").status_code == 200
    assert cl.get("/api/production/sites/H01/families/hvac-1").json()["name"] == "HVAC #1"
    assert cl.get("/api/production/sites/H01/families/nope").status_code == 404
    assert cl.get("/api/production/sites/ZZ").json()["families"] == []
    assert cl.get("/api/production/sites/H01/compare?metric=bad").status_code == 422
    monkeypatch.setattr(wb, "production_workbook", lambda: None)
    d = cl.get("/api/production/sites").json()
    assert d["sites"] == [] and d["notes"]
