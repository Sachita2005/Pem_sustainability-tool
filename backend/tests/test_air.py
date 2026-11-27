"""Air tests. Values read from workbook "Sustainability Calculation", sheet "Air Monitoring data"
(block 'FY25' = Apr-24..Mar-25 in columns F..Q) and from the TBAL consent order (docx)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pem.domains import air
from pem.workbooks import calculation_workbook

pytestmark = pytest.mark.skipif(calculation_workbook() is None, reason="source workbooks not available")


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(air.router)
    return TestClient(app)


def _one(rows, **kw):
    hit = [r for r in rows if all(r[k] == v for k, v in kw.items())]
    assert len(hit) == 1, (kw, len(hit))
    return hit[0]


def test_tlmal_nox_april_2024():
    # Row 6 (TLMAL-Hyd, Nitrogen oxides, mg/m3), F6 = 2.3 (Apr-24)
    r = _one(air.readings(plant="TLMAL")["readings"], parameter="Nitrogen oxides (NOx)", month="2024-04")
    assert r["value"] == 2.3 and r["unit"] == "mg/m3" and r["source"] == "calculation" and r["fy"] == "FY24-25"
    assert r["limit"] is None and r["aboveLimit"] is None


def test_tbal_ambient_pm10_with_limit():
    # Row 62 (TBAL-Hyd, Ambient PM10), F62 = 71.0 (Apr-24); the consent order's ambient PM10 standard is 100 ug/m3
    r = _one(air.readings(plant="TBAL")["readings"], parameter="PM10", month="2024-04")
    assert r["value"] == 71.0 and r["limit"] == 100 and r["aboveLimit"] is False
    assert r["location"] == "Ambient" and "page 4" in r["limitSource"]


def test_entry_workbook_fills_blank_only():
    rows = air.readings(plant="TBAL", parameter="PM10")["readings"]
    # the calculation workbook has no TBAL PM10 for Dec-24 (blank); the entry workbook has 63.0
    r = _one(rows, month="2024-12")
    assert r["value"] == 63.0 and r["source"] == "entry"
    assert _one(rows, month="2024-04")["source"] == "calculation"


def test_not_applicable_hidden_by_default():
    base = air.readings(plant="TASL-NGP", fy="FY24-25")["readings"]
    withna = air.readings(plant="TASL-NGP", fy="FY24-25", include_na=True)["readings"]
    assert all(not r["notApplicable"] for r in base) and len(withna) > len(base)
    assert all(r["value"] is None for r in withna if r["notApplicable"])


def _row(plant, month, value, unit):
    return {"plant": plant, "plantLabel": plant, "sheetLabel": "x", "location": "Ambient", "parameter": "PM10",
            "kind": "ambient", "unit": unit, "month": month, "fy": "FY24-25", "value": value,
            "notApplicable": False, "source": "calculation"}


def test_flagging_logic(monkeypatch):
    rows = [_row("TBAL", "2024-04", 120.0, None), _row("TBAL", "2024-05", 120.0, "ppm"), _row("TLMAL", "2024-04", 999.0, None)]
    monkeypatch.setattr(air, "_merged", lambda: (rows, []))
    by = {(r["plant"], r["month"]): r for r in air.readings()["readings"]}
    assert by[("TBAL", "2024-04")]["aboveLimit"] is True
    assert by[("TBAL", "2024-05")]["aboveLimit"] is None      # unit differs from the stated limit's: not compared
    assert by[("TLMAL", "2024-04")]["aboveLimit"] is None     # no consent order for this plant
    assert air.overview()["readingsAboveLimit"] == 1


def test_consent_conditions():
    c = air.consent_conditions()
    cond = {x["name"]: x for x in c["conditions"]}
    assert c["order"]["plant"] == "TBAL" and c["order"]["pages"] == 5
    assert cond["Consent and HW authorization valid until"]["value"] == "30.06.2029"
    assert cond["Order date"]["value"] == "19.07.2024"
    assert cond["Ambient Particulate Matter(PM10)"]["value"] == 100 and cond["Ambient Particulate Matter(PM10)"]["page"] == 4
    assert cond["Ambient Particulate Matter (PM2.5)"]["value"] == 60
    assert cond["Emission standard SPM (chimneys)"]["value"] == 115 and cond["Emission standard SPM (chimneys)"]["unit"] == "mg/Nm3"
    assert cond["Effluent standard TSS"]["value"] == 200 and cond["Effluent standard Oil & Grease"]["value"] == 10
    assert cond["Water consumption limit - Total"]["value"] == 90
    waste = {w["waste"]: w for w in c["wasteAuthorisation"]}
    assert waste["Waste/ Used Oil"]["quantity"] == 360 and waste["Waste/ Used Oil"]["unit"] == "LPM"
    assert waste["Cotton Waste"]["quantity"] == 1500 and waste["Packing material"]["quantity"] == 7000
    assert all(x["document"] and x["page"] for x in c["conditions"])


def test_limits_and_overview_and_routes(client):
    lims = air.limits()
    assert {(x["parameter"], x["location"]) for x in lims} == {
        ("PM10", "Ambient"), ("PM2.5", "Ambient"),
        ("Particulate matter (SPM)", "DG stack"), ("Particulate matter (SPM)", "Paint booth")}
    o = air.overview()
    assert o["readings"] > 1000 and o["consent"]["validUntil"] == "30.06.2029" and "plants" in o
    for url in ["/api/air/overview", "/api/air/readings?plant=TBAL&fy=FY24-25", "/api/air/limits"]:
        r = client.get(url)
        assert r.status_code == 200 and "notes" in r.json(), url
    assert client.get("/api/air/readings?plant=TBAL&parameter=PM10").json()["count"] > 0


def test_missing_files_give_valid_payloads(monkeypatch):
    monkeypatch.setattr(air, "calculation_workbook", lambda: None)
    monkeypatch.setattr(air, "entry_workbook", lambda: None)
    monkeypatch.setattr(air, "_consent_path", lambda: None)
    r = air.readings()
    assert r["readings"] == [] and r["notes"]
    c = air.consent_conditions()
    assert c["conditions"] == [] and c["notes"]
    assert air.limits() == []
    assert air.overview()["readings"] == 0
