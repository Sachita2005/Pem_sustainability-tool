"""Tests for pem.domains.initiatives, run against the real source files."""
import pytest
from fastapi.testclient import TestClient

from pem.app import app
from pem.domains import initiatives as ini

client = TestClient(app)


@pytest.fixture(scope="module")
def data():
    return ini.load_dataset()


def test_status_normalisation_covers_the_source_wording():
    assert ini.normalise_status("WIP") == "in_progress"
    assert ini.normalise_status("Under Faesible Study") == "under_study"
    assert ini.normalise_status("Under Feasible") == "under_study"
    assert ini.normalise_status("Horizontally deployed") == "horizontally_deployed"
    assert ini.normalise_status("Not feasible") == "not_feasible"
    assert ini.normalise_status("NA") == "not_applicable"
    assert ini.normalise_status("On Hold") == "on_hold"
    assert ini.normalise_status(None) is None
    assert ini.normalise_status("something new") is None


def test_every_status_in_the_files_is_recognised(data):
    assert not [n for n in data["notes"] if "unrecognised status" in n]
    for rec in data["records"]:
        for s in rec["plantStatuses"]:
            assert s["status"] in ini.STATUSES or s["status"] is None


def test_record_shape_and_unique_ids(data):
    ids = [r["id"] for r in data["records"]]
    assert len(ids) == len(set(ids))
    for r in data["records"]:
        for k in ("id", "groupKey", "source", "phase", "plants", "domain", "description", "plantStatuses",
                  "achieved", "expected", "priority", "initiatedBy", "responsible"):
            assert k in r
        assert r["phase"] in ("present", "planned")
        assert r["source"]["row"] >= 1
        assert set(r["plants"]) <= set(ini.PLANT_IDS)


def test_cogged_belt_initiative_matches_the_sheet(data):
    # "Energy Saving Initiatives Data.xlsx", sheet row 18 (Sl. No. 16): TLMAL block, 36360 kWh and 0.03 Cr.
    rec = data["byId"]["esi-r18"]
    assert rec["description"].startswith("Cogged Belt")
    assert rec["initiatedBy"] == "Harsha" and rec["responsible"] == "TLMAL"
    assert rec["initiationDate"] == "2025-02-11" and rec["priority"] is None
    tl = next(s for s in rec["plantStatuses"] if s["plant"] == "TLMAL")
    assert (tl["status"], tl["energySavingKwh"], tl["energyCostSavingCr"]) == ("implemented", 36360.0, 0.03)
    assert rec["initiationPlant"]["plants"] == ["TLMAL"]
    assert rec["phase"] == "present"


def test_fy27_copy_is_planned_and_linked(data):
    # FY 2026-27 file, sheet row 18: initiator status "WIP", savings in the TLMAL block (36360 kWh).
    plan = data["byId"]["fy27-r18"]
    old = data["byId"]["esi-r18"]
    assert plan["phase"] == "planned"
    assert plan["groupKey"] == old["groupKey"] and plan["groupSize"] >= 2
    assert plan["initiatorStatus"]["statusOriginal"] == "WIP"
    assert {s["plant"] for s in plan["plantStatuses"]} <= set(ini.PLANT_IDS)
    assert plan["expected"]["perYear"] is None and plan["investmentRs"] is None  # blank in the source


def test_original_status_text_is_kept(data):
    # FY 2026-27 file Sl. No. 8 (sheet row 10): "Under Faesible Study" in the (TASL-NGP) block (column AQ).
    rec = data["byId"]["fy27-r10"]
    ngp = next(s for s in rec["plantStatuses"] if s["plant"] == "TASL-NGP")
    assert ngp["status"] == "under_study" and ngp["statusOriginal"] == "Under Faesible Study"


def test_calculation_sheet_rows(data):
    # "Energy(Power) Initiatives" row 45: TSAL lighting initiative, FY24-25 total (col Q) 12150 kWh,
    # CO2 (col R) 10327.5 kg; FY23-24 total (col AI) 14580.
    rec = data["byId"]["calc-power-r45"]
    assert rec["plants"] == ["TSAL"] and rec["domain"] == "lighting" and rec["phase"] == "present"
    fy = rec["achieved"]["byFinancialYear"]
    assert fy["FY24-25"] == {"value": 12150.0, "co2Kg": 10327.5}
    assert fy["FY23-24"]["value"] == 14580.0
    assert rec["achieved"]["unit"] == "kWh"
    # row 57: Hooters and fixed flop curtains, 40120 kWh in FY24-25
    assert data["byId"]["calc-power-r57"]["achieved"]["byFinancialYear"]["FY24-25"]["value"] == 40120.0


def test_future_plan_rows_are_planned(data):
    rec = data["byId"]["calc-power-r67"]  # TSAL row under "Future Plan for Improvements"
    assert rec["phase"] == "planned" and rec["plants"] == ["TSAL"]
    assert rec["plantStatuses"][0]["status"] == "open"


def test_summary_rows_are_not_initiatives(data):
    for r in data["records"]:
        d = r["description"].lower()
        assert not d.startswith(("plant total", "future plan", "total", "eb", "solar (in house"))


def test_plant_total_mismatches_are_reported(data):
    # "Energy(Power) Initiatives" row 152 says TASL-BLR FY24-25 total 48664 but its rows carry no FY24-25 total.
    hit = [x for x in data["reconciliation"] if x["row"] == 152 and x["financialYear"] == "FY24-25"]
    assert hit and hit[0]["plantTotalInSheet"] == 48664.0


def test_filters():
    out = ini.list_initiatives(plant="TLMAL", status="in_progress", phase="present")
    assert out["count"] >= 1
    assert all("TLMAL" in r["plants"] and r["phase"] == "present" for r in out["items"])
    water = ini.list_initiatives(domain="water")
    assert water["count"] > 0 and all(r["domain"] == "water" for r in water["items"])
    bad = ini.list_initiatives(plant="not a plant")
    assert bad["count"] == 0 and any("Unknown plant" in n for n in bad["notes"])


def test_summary_shape():
    s = ini.summary()
    assert set(ini.STATUSES) <= set(s["byStatus"])
    assert set(s["byPlant"]) == set(ini.PLANT_IDS)
    assert s["byPhase"]["present"] + s["byPhase"]["planned"] == s["totalInitiatives"]
    assert s["savings"]["expected"]["energyKwh"] is None
    # 36360 + 5 x 4501 kWh as given in the existing-initiatives file
    assert s["savings"]["achievedFromInitiativesFiles"]["esi"]["energyKwh"] == 58865.0
    assert s["savings"]["achievedFromCalculationWorkbook"]["power"]["FY24-25"]["unit"] == "kWh"


def test_horizontal_deployment_candidates():
    # TBAL is "Open" for Sl. No. 1 (switches, Office area), which TLMAL, MCA, TCOE and NGP have
    # implemented (FY 2026-27 file, sheet row 3).
    cands = ini.horizontal_deployment_candidates("TBAL")
    hit = next(c for c in cands if c["id"] == "fy27-r3")
    assert hit["statusAtPlant"] == "open"
    assert {x["plant"] for x in hit["implementedAt"]} >= {"TLMAL", "TASL-MCA", "TCOE", "TASL-NGP"}
    assert all(c["plant"] == "TBAL" for c in cands)
    assert all(c["statusAtPlant"] in ("open", "under_study") for c in cands)


def test_http_endpoints():
    r = client.get("/api/initiatives", params={"phase": "planned", "plant": "TSAL"})
    assert r.status_code == 200 and r.json()["count"] > 0
    assert client.get("/api/initiatives", params={"phase": "bogus"}).status_code == 422
    one = client.get("/api/initiatives/esi-r18")
    assert one.status_code == 200 and one.json()["id"] == "esi-r18"
    assert client.get("/api/initiatives/nope").status_code == 404
    assert client.get("/api/initiatives/summary").status_code == 200
    h = client.get("/api/initiatives/horizontal-deployment/TBAL").json()
    assert h["plant"] == "TBAL" and h["count"] == len(h["items"])
    assert client.get("/api/initiatives/horizontal-deployment/xx").status_code == 404


def test_missing_sources_give_valid_empty_payload(monkeypatch):
    monkeypatch.setattr(ini, "_find_files", lambda: (None, None))
    monkeypatch.setattr(ini, "calculation_workbook", lambda: None)
    ini._cache.clear()
    try:
        out = ini.list_initiatives()
        assert out["count"] == 0 and len(out["notes"]) >= 3
        assert ini.summary()["totalInitiatives"] == 0
    finally:
        ini._cache.clear()
