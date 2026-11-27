"""Scrap tests: values checked by reading the source sheets and documents.

Workbook "Sustainability (Only to Enter)", sheet "Scrap generation details" (columns: S = Apr-25 ... AD = Mar-26,
AE = 'FY 25-26' total; E.. = FY26-27 months, Q = its total).
"""
import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pem.domains import scrap
from pem.workbooks import entry_workbook

pytestmark = pytest.mark.skipif(entry_workbook() is None, reason="source workbooks not available")


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(scrap.router)
    return TestClient(app)


@pytest.fixture(scope="module")
def tot():
    return scrap.totals()


def test_month_header_day_is_the_year():
    assert scrap.parse_month_header(datetime.datetime(2025, 1, 26)) == (2026, 1)
    assert scrap.parse_month_header("Apr-22") == (2022, 4)
    assert scrap.parse_month_header("FY 25-26") is None


def test_tlmal_oil_soaked_cotton_fy25_26():
    # Row 5 (TLMAL-Hyd, Oil socked Cotton Waste, KG): S5 = 585 (Apr-25), AE5 = 8845 (FY 25-26 total)
    q = scrap.quantities(plant="TLMAL", fy="FY25-26", category="Hazardous")
    item = next(i for i in q["items"] if i["wasteType"].startswith("Oil socked"))
    f = item["fy"]["FY25-26"]
    assert item["unit"] == "KG" and item["source"] == "entry"
    assert f["months"]["2025-04"] == 585 and f["total"] == 8845 and f["statedTotal"] == 8845
    assert len(f["months"]) == 12 and "2026-03" in f["months"]


def test_plant_year_totals(tot):
    rows = {(r["plant"], r["fy"], r["category"]): r for r in tot["plantYear"]}
    # Row 10 "Plant Total Hazardous Waste disposal" TLMAL FY 25-26 = 12461 KG (equals the sum of its five items)
    assert rows[("TLMAL", "FY25-26", "Hazardous")]["weightKg"] == 12461
    # TASL-Nagpur FY 25-26 items are in T: 182.88+148.68+41.6+55.96 = 429.12 T -> kg
    assert rows[("TASL-NGP", "FY25-26", "Hazardous")]["weightKg"] == pytest.approx(429120)
    # TASL-MCA: 7305.73 in the sheet adds KG and litres; weights and litres stay apart here
    mca = rows[("TASL-MCA", "FY25-26", "Hazardous")]
    assert mca["weightKg"] == pytest.approx(7095.73) and mca["volumeL"] == 210
    # E-waste from TASL-Bangalore (row 77) FY 25-26 = 4710 KG
    assert rows[("TASL-BLR", "FY25-26", "E-Waste")]["weightKg"] == 4710


def test_all_zero_plant_years_are_null_not_zero(tot):
    rows = {(r["plant"], r["fy"], r["category"]): r for r in tot["plantYear"]}
    assert rows[("TSAL", "FY25-26", "Hazardous")]["weightKg"] is None


def test_clusters_and_company(tot):
    co = {(r["fy"], r["category"]): r for r in tot["companyYear"]}
    cl = {(r["cluster"], r["fy"], r["category"]): r for r in tot["clusterYear"]}
    assert tot["clusters"]["DP"] == ["TASL-NGP", "TASL-BLR", "TSAL", "TCOE"]
    # FY26-27 so far: DP = TASL-BLR 46000 kg only; ASSY = TLMAL 6030 + TASL-HYD 15906 + TASL-MCA 4520.48
    assert cl[("DP", "FY26-27", "Hazardous")]["weightKg"] == pytest.approx(46000)
    assert cl[("ASSY", "FY26-27", "Hazardous")]["weightKg"] == pytest.approx(6030 + 15906 + 4520.48)
    assert co[("FY26-27", "Hazardous")]["weightKg"] == pytest.approx(46000 + 6030 + 15906 + 4520.48)
    assert co[("FY26-27", "Hazardous")]["volumeL"] == 200


def test_sheet_totals_reported_for_comparison(tot):
    # Row 113 "Total TADLHazardous Waste disposal" FY 25-26 = 164411.88 (adds KG, T and L rows)
    st = [s for s in tot["sheetTotals"] if s["plant"] == "TADL" and s["fy"] == "FY25-26" and "Hazardous" in s["label"]]
    assert st and st[0]["value"] == pytest.approx(164411.883)
    assert any("never added" in n for n in tot["notes"])


def test_monthly_totals_have_month_keys(tot):
    m = [r for r in tot["plantMonth"] if r["plant"] == "TASL-BLR" and r["month"] == "2026-04" and r["category"] == "Hazardous"]
    # Rows 69-75 column E (Apr-26): 350+110+730+810+6380+900+610 = 9890 (E76 states the same plant total)
    assert m and m[0]["weightKg"] == 9890
    assert m[0]["fy"] == "FY26-27"


def test_disposal_methods():
    d = scrap.disposal_methods()
    # 'Waste Sheet Disposal Method' row 3: Batteries / MCA / Hazardous Waste / Recycled
    r = next(x for x in d["rows"] if x["plant"] == "TASL-MCA" and x["waste"] == "Batteries")
    assert r["method"] == "Recycled" and r["wasteType"] == "Hazardous Waste"
    assert {x["plant"] for x in d["rows"]} >= set(scrap.PLANT_IDS)
    assert any("no quantities" in n for n in d["notes"])
    only = scrap.disposal_methods(plant="TBAL")["rows"]
    assert only and all(x["plant"] == "TBAL" for x in only)


def test_targets_only_real():
    t = scrap.targets()
    # 'Target Sheet' row 20: Landfill Waste - 2030 / FY 25 - 26 / level 0 / reduction 0
    x = next(x for x in t["targets"] if x["financialYear"] == "FY25-26")
    assert x["category"] == "Landfill Waste - 2030" and x["level"] == 0 and x["targetYear"] == 2030 and x["unit"] is None
    assert all("Carbon" not in y["category"] for y in t["targets"])
    assert t["baseline"] is None and any("zeros" in n for n in t["notes"])


def test_initiative_structure():
    i = scrap.initiative()
    assert i["brd"]["pages"] == 14 and i["brd"]["textLayer"] is False
    assert any("no OCR" in n for n in i["notes"])
    obj = i["brd"]["transcription"]["objectives"][0]
    assert obj["text"].startswith("The objective of this application is to establish a digitized system for Scrap Management")
    assert obj["page"] == 2
    pcb = next(s for s in i["mendix"]["sections"] if s["title"].startswith("PCB Details/ PCB Master"))
    assert pcb["tables"][0]["rows"][1][:2] == ["EHS", "EHS"]
    flows = [b for s in i["mendix"]["sections"] for fc in s["flowCharts"] for b in fc["boxes"]]
    assert "Input Waste Category(Hazardous/ Non- Hazardous/ E- Waste/ Bio Medical Waste)" in flows
    assert len(i["mendix"]["images"]) == 6 and i["targetsOrBenefits"] == []


def test_routes(client):
    for url in ["/api/scrap/overview", "/api/scrap/quantities?plant=TLMAL", "/api/scrap/totals?fy=FY25-26",
                "/api/scrap/disposal", "/api/scrap/targets", "/api/scrap/initiative"]:
        r = client.get(url)
        assert r.status_code == 200 and "notes" in r.json(), url
    assert client.get("/api/scrap/documents/brd.pdf").headers["content-type"] == "application/pdf"
    img = client.get("/api/scrap/documents/mendix/images/1")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert client.get("/api/scrap/documents/mendix/images/99").status_code == 404


def test_overview_shape():
    o = scrap.overview()
    assert o["latestFy"] == "FY26-27" and o["unit"]["weight"] == "kg"
    assert {"company", "clusters", "plants", "yearly", "target", "notes"} <= set(o)


def test_missing_files_give_valid_empty_payloads(monkeypatch):
    monkeypatch.setattr(scrap, "entry_workbook", lambda: None)
    monkeypatch.setattr(scrap, "calculation_workbook", lambda: None)
    monkeypatch.setattr(scrap, "_docx_path", lambda: None)
    monkeypatch.setattr(scrap, "_pdf_path", lambda: None)
    assert scrap.quantities()["items"] == [] and scrap.quantities()["notes"]
    assert scrap.totals()["companyYear"] == []
    assert scrap.disposal_methods()["rows"] == [] and scrap.targets()["targets"] == []
    i = scrap.initiative()
    assert i["brd"]["pages"] is None and i["mendix"]["sections"] == [] and i["notes"]
    assert scrap.overview()["latestFy"] is None
