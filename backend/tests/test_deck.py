"""Deck tests run against the real Sustainability Deck in the source folder.

Verified by reading the deck: slide 4 (TITLE_ONLY, 'Milestone') has group shape 201 holding
shape 202 "2024: Action Plans for Net Positive Impact"; the deck has 126 slides at 9144000 x 5143500 EMU.
"""
import hashlib
import importlib.util
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lxml import etree
from pptx import Presentation

from pem import store
from pem.domains import deck

pytestmark = pytest.mark.skipif(deck.source_path() is None, reason="source deck not available")

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(deck.router)
    return TestClient(app)


def _sha(path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def fresh_copy(monkeypatch):
    # No PowerPoint in most tests; test_render_one_slide switches it back on.
    monkeypatch.setitem(deck._renderer_cache, "kind", "none")
    deck.reset_to_original()
    store.write_json("deck/approval", {})
    yield


def test_info_and_slide_list():
    c = _app()
    info = c.get("/api/deck").json()
    assert info["available"] and info["slideCount"] == 126
    assert info["renderer"] == "none" and info["size"]["widthEmu"] == 9144000
    assert info["approval"]["status"] == "draft" and info["hasEditsFromOriginal"] is False
    slides = c.get("/api/deck/slides").json()["slides"]
    assert len(slides) == 126 and slides[3]["imageUrl"] is None
    assert c.get("/api/deck/slides/4/image").status_code == 404  # renderer none: text layout instead


def test_slide_model_includes_group_text_with_fractions():
    s = _app().get("/api/deck/slides/4").json()
    box = next(b for b in s["textBoxes"] if b["id"] == "201/202")
    assert box["paragraphs"][0]["text"] == "2024: Action Plans for Net Positive Impact"
    assert 0 < box["left"] < 1 and 0 < box["width"] < 1 and 0 < box["top"] < 1
    assert s["size"]["aspect"] == pytest.approx(16 / 9, rel=1e-3)


def test_table_cells_are_editable_boxes():
    s = _app().get("/api/deck/slides/13").json()  # slide 13 has the "Solar Panel Availability" table
    cell = next(b for b in s["textBoxes"] if b["id"] == "439/r1c0")
    assert cell["paragraphs"][0]["text"] == "TLMAL" and cell["kind"] == "tablecell"


def _rpr(path, slide_no, shape_path):
    prs = Presentation(str(path))
    shapes = prs.slides[slide_no - 1].shapes
    shp = None
    for part in shape_path.split("/"):
        shp = next(s for s in shapes if str(s.shape_id) == part)
        if shp.shape_type == 6:
            shapes = shp.shapes
    run = shp.text_frame.paragraphs[0].runs[0]
    return shp.text_frame.text, etree.tostring(run._r.find(A + "rPr"))


def test_save_round_trip_keeps_formatting_and_other_slides():
    src = deck.source_path()
    src_sha = _sha(src)
    work = deck.working_path()
    before_text, before_rpr = _rpr(work, 4, "201/202")
    other_before = etree.tostring(Presentation(str(work)).slides[4]._element)  # slide 5
    res = _app().post("/api/deck/save", json={"author": "tester", "edits": [
        {"slide": 4, "shapeId": "201/202", "paragraphs": ["2024: Action Plans for Net Positive Impact (edited)"]}]}).json()
    assert res["ok"] and res["changedSlides"] == [4]
    after_text, after_rpr = _rpr(work, 4, "201/202")
    assert after_text == "2024: Action Plans for Net Positive Impact (edited)" and before_text != after_text
    assert after_rpr == before_rpr                       # font, size, colour untouched
    assert etree.tostring(Presentation(str(work)).slides[4]._element) == other_before
    assert _sha(src) == src_sha                          # the source deck is never modified
    log = store.read_json("deck/edit_log", [])
    assert log[-1]["slide"] == 4 and log[-1]["by"] == "tester" and log[-1]["at"]
    assert _app().get("/api/deck").json()["hasEditsFromOriginal"] is True


def test_unchanged_text_is_not_a_save():
    res = _app().post("/api/deck/save", json={"edits": [
        {"slide": 4, "shapeId": "201/202", "paragraphs": ["2024: Action Plans for Net Positive Impact"]}]}).json()
    assert res["changed"] == [] and res["changedSlides"] == []


def test_paragraph_count_can_change():
    c = _app()
    box = next(b for b in c.get("/api/deck/slides/12").json()["textBoxes"] if b["id"] == "373")
    texts = [p["text"] for p in box["paragraphs"]]
    assert len(texts) >= 3
    c.post("/api/deck/save", json={"edits": [{"slide": 12, "shapeId": "373", "paragraphs": texts[:2] + ["Extra line", "Another"]}]})
    again = next(b for b in c.get("/api/deck/slides/12").json()["textBoxes"] if b["id"] == "373")
    assert [p["text"] for p in again["paragraphs"]] == texts[:2] + ["Extra line", "Another"]


def test_multi_run_paragraph_keeps_run_formatting():
    c = _app()
    box = next(b for b in c.get("/api/deck/slides/4").json()["textBoxes"] if b["id"] == "200")
    runs = box["paragraphs"][0]["runs"]
    assert len(runs) == 2 and runs[0]["text"] == "Mission: "          # label run + body run
    work = deck.working_path()
    before = [etree.tostring(r._r.find(A + "rPr")) for r in
              next(s for s in Presentation(str(work)).slides[3].shapes if s.shape_id == 200).text_frame.paragraphs[0].runs]
    new_runs = [{"text": "Mission: "}, {"text": "Edited body."}]
    c.post("/api/deck/save", json={"edits": [{"slide": 4, "shapeId": "200", "paragraphs": [{"runs": new_runs}]}]})
    shp = next(s for s in Presentation(str(work)).slides[3].shapes if s.shape_id == 200)
    p_runs = shp.text_frame.paragraphs[0].runs
    assert [r.text for r in p_runs] == ["Mission: ", "Edited body."]
    assert [etree.tostring(r._r.find(A + "rPr")) for r in p_runs] == before   # each run keeps its own formatting


def test_unknown_shape_is_reported_not_raised():
    res = _app().post("/api/deck/save", json={"edits": [{"slide": 4, "shapeId": "99999", "paragraphs": ["x"]}]}).json()
    assert res["ok"] is False and "not found" in res["errors"][0]


def test_approval_workflow_and_edit_returns_to_draft():
    c = _app()
    a = c.post("/api/deck/approval/approve", json={"by": "Reviewer", "note": "ok"}).json()
    assert a["status"] == "approved" and a["approvedBy"] == "Reviewer"
    assert c.get("/api/deck").json()["approval"]["status"] == "approved"
    c.post("/api/deck/save", json={"edits": [{"slide": 4, "shapeId": "201/202", "paragraphs": ["Changed after approval"]}]})
    st = c.get("/api/deck/approval").json()
    assert st["status"] == "draft"
    assert [h["event"] for h in st["history"]] == ["approved", "returned_to_draft"]
    c.post("/api/deck/approval/approve", json={"by": "R2"})
    w = c.post("/api/deck/approval/withdraw", json={"by": "R2", "note": "mistake"}).json()
    assert w["status"] == "draft" and w["history"][-1]["event"] == "approval_withdrawn"


def test_download_and_reset():
    c = _app()
    r = c.get("/api/deck/download")
    assert r.status_code == 200 and "Sustainability Deck" in __import__("urllib.parse").parse.unquote(r.headers["content-disposition"])
    assert r.content[:2] == b"PK"
    c.post("/api/deck/save", json={"edits": [{"slide": 4, "shapeId": "201/202", "paragraphs": ["Temp"]}]})
    assert c.post("/api/deck/reset").json()["ok"]
    boxes = c.get("/api/deck/slides/4").json()["textBoxes"]
    assert next(b for b in boxes if b["id"] == "201/202")["paragraphs"][0]["text"].startswith("2024")
    assert c.get("/api/deck").json()["hasEditsFromOriginal"] is False


def test_no_source_file_never_raises(monkeypatch):
    monkeypatch.setattr(deck, "source_path", lambda: None)
    shutil.rmtree(deck.deck_dir(), ignore_errors=True)
    deck._model_cache["key"] = None
    info = _app().get("/api/deck").json()
    assert info["available"] is False and info["notes"]
    assert _app().get("/api/deck/slides").json()["slides"] == []


@pytest.mark.skipif(importlib.util.find_spec("win32com") is None, reason="pywin32 missing")
def test_render_one_slide():
    deck._renderer_cache.pop("kind", None)
    if deck.renderer_kind() != "powerpoint":
        pytest.skip("PowerPoint not installed")
    res = deck.render_slides([3])
    assert res["rendered"] == [3]
    png = deck.slides_dir() / "3.png"
    assert png.is_file() and png.stat().st_size > 10_000
    from PIL import Image
    assert Image.open(png).size[0] == deck.IMAGE_WIDTH
    s = deck.get_slide(3)
    assert s["imageUrl"] and s["imageUrl"].startswith("/api/deck/slides/3/image?v=")
