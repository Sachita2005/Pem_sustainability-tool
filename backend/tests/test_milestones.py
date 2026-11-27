"""Milestones tests: values checked by reading slides 1-14 of the Sustainability Deck."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pem.domains import deck, milestones

pytestmark = pytest.mark.skipif(deck.source_path() is None, reason="source deck not available")


@pytest.fixture(scope="module")
def data():
    return milestones.milestone_slides()


def _slide(data, n):
    return next(s for s in data["slides"] if s["number"] == n)


def test_fourteen_slides_in_order_with_stages(data):
    assert [s["slide"] for s in data["sequence"]] == list(range(1, 15))
    assert {s["stage"] for s in data["sequence"]} == {"intro", "aalingana", "tasl"}
    assert _slide(data, 6)["stage"] == "aalingana" and _slide(data, 13)["stage"] == "tasl"
    assert all(s["imageUrl"] == f"/api/deck/slides/{s['number']}/image" for s in data["slides"])


def test_slide4_pillar_targets(data):
    # Slide 4: nine "year: target" boxes under three pillars (read from the slide).
    t = _slide(data, 4)["targets"]
    assert len(t) == 10  # the middle-column box 206 holds two targets
    nz = [x for x in t if x["pillar"] == "Driving Net-Zero"]
    assert [x["year"] for x in nz] == [2027, 2030, 2045]
    assert any(x["year"] == 2040 and x["label"] == "Achieving Water Positivity" for x in t)


def test_slide6_status_markers(data):
    st = _slide(data, 6)["statuses"]
    assert [s["marker"] for s in st] == ["tick", "tick", "WIP"]
    assert st[2]["pillar"] == "Preserving Nature & Biodiversity"
    assert "Planted 560" in st[2]["details"][0]


def test_slide7_goals_target_current(data):
    rows = _slide(data, 7)["targets"]
    assert len(rows) == 7
    solar = rows[0]
    assert (solar["year"], solar["label"]) == (2025, "Solar Units")
    assert solar["target"]["value"] == 2.25 and solar["target"]["unit"] == "Million Units"
    assert solar["current"]["value"] == 8.52 and solar["baselineType"] == "From 2020 Baseline"
    assert rows[2]["target"]["value"] == 19 and rows[2]["current"]["value"] == 59
    assert rows[4]["current"]["value"] is None and rows[4]["baselineType"] == "Future Year as Baseline"
    assert "absolute reduction" in _slide(data, 7)["footnotes"][0]


def test_slide5_challenges_by_column(data):
    b = {x["heading"]: [i["text"] for i in x["items"]] for x in _slide(data, 5)["bullets"]}
    assert len(b["Net Zero Emissions"]) == 4 and "Regulatory Barriers" in b["Net Zero Emissions"]
    assert "Waste Segregation at the source" in b["Zero Waste to Landfill"]


def test_picture_and_partial_slides_are_flagged(data):
    assert data["imageOnlySlides"] == [10]
    assert set(data["partialSlides"]) == {8, 13, 14}
    assert _slide(data, 10)["bullets"] == []


def test_ticks_on_slide9_and_11(data):
    for n in (9, 11):
        cols = _slide(data, n)["bullets"]
        ticked = [i["text"] for c in cols for i in c["items"] if i.get("marker") == "tick"]
        assert ticked == ["Proper Separation of Scrap (Hazardous, E-Waste, Battery)", "Effective Selling to Recycler"]


def test_slide14_chart_series(data):
    ch = _slide(data, 14)["charts"][0]
    assert ch["categories"][0] == "FY 18-19" and len(ch["categories"]) == 10
    total = next(s for s in ch["series"] if s["name"].startswith("Total Units"))
    assert total["values"][0] == pytest.approx(386.02314994, rel=1e-6)


def test_slide13_table_and_figures(data):
    s = _slide(data, 13)
    assert s["tables"][0]["rows"][0] == ["TLMAL", "Yes"] and len(s["tables"][0]["rows"]) == 8
    assert s["targets"][0]["points"][2]["electricityRequirement"] == "146.7M"


def test_routes():
    app = FastAPI()
    app.include_router(milestones.router)
    c = TestClient(app)
    assert len(c.get("/api/milestones").json()["slides"]) == 14
    assert c.get("/api/milestones/slides/7").json()["kind"] == "goals_table"
    assert c.get("/api/milestones/slides/20").status_code == 404
    assert len(c.get("/api/milestones/sequence").json()["sequence"]) == 14


def test_missing_deck_returns_empty_payload(monkeypatch):
    monkeypatch.setattr(deck, "_all_slide_models", lambda: [])
    d = milestones.milestone_slides()
    assert d["available"] is False and d["slides"] == [] and d["notes"]
