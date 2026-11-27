"""Objectives and milestones: the first 14 slides of the Sustainability Deck as structured data.

Everything here is read from the deck's working copy (so approved edits show up);
no text or number is written by hand except the stage labels, which are the
grouping worked out from the slide content (see STAGES). Slides that are mainly
a picture or diagram say so (`structure`) and carry the rendered image URL.

Shapes are found by their id in the deck (stable across edits) and, where a slide
is laid out in columns or rows, assigned to their column/row by position.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pptx import Presentation

from . import deck

router = APIRouter(prefix="/api/milestones", tags=["milestones"])

SLIDE_COUNT = 14

# The grouping, worked out from the content of the slides (not stated in the deck):
#  - intro: cover, agenda, vision/mission/objectives (TASL's own statements)
#  - aalingana: slides about the Tata Group's Project Aalingana milestones and TASL's status against them
#  - tasl: TASL's own plan and graphs that implement the Aalingana targets (Scope 1/2/3, KPI blue-print, solar)
# "Tata" is not a separate stage in these slides: it appears as the owner of Aalingana
# ("as defined by the Tata Group") and in the branding.
STAGES = [
    {"id": "intro", "label": "Vision, mission and agenda", "slides": [1, 2, 3],
     "evidence": "Cover, agenda, and TASL's vision, mission and objectives (slide 3)."},
    {"id": "aalingana", "label": "Project Aalingana (Tata Group programme)", "slides": [4, 5, 6, 7],
     "evidence": "Slide 4 mission 'in conformity with Project Aalingana' and the Aalingana milestones; slide 5 "
                 "'Challenges in achieving Project Aalingana Targets'; slide 6 'Project Aalingana Milestones as "
                 "defined by the Tata Group' with current status; slide 7 goals vs target vs current."},
    {"id": "tasl", "label": "TASL plan and progress", "slides": [8, 9, 10, 11, 12, 13, 14],
     "evidence": "Slide 8 'TASL monitors Scope 1 and Scope 2 emissions...'; slides 9-12 'TADL: Sustainability "
                 "Blue-Print with KPIs' and graphs; slides 13-14 solar energy plan for TASL / TBAL & TASL H01."},
]
_STAGE_OF = {n: s["id"] for s in STAGES for n in s["slides"]}
_STAGE_LABEL = {s["id"]: s["label"] for s in STAGES}

MARKER_CHECK = "tick"

# ----------------------------------------------------------------------------
# Small helpers over the deck's slide model
# ----------------------------------------------------------------------------


class _Slide:
    """Text boxes of one slide keyed by shape id, with their positions."""

    def __init__(self, model: dict) -> None:
        self.model = model
        self.boxes: dict[str, dict] = {b["id"]: b for b in model["textBoxes"]}

    def text(self, sid: str) -> str:
        b = self.boxes.get(sid)
        return "\n".join(p["text"] for p in b["paragraphs"]).strip() if b else ""

    def lines(self, sid: str) -> list[str]:
        b = self.boxes.get(sid)
        return [_clean(p["text"]) for p in b["paragraphs"] if p["text"].strip()] if b else []

    def box(self, sid: str) -> dict | None:
        return self.boxes.get(sid)

    def cx(self, sid: str) -> float:
        b = self.boxes[sid]
        return b["left"] + b["width"] / 2

    def cy(self, sid: str) -> float:
        b = self.boxes[sid]
        return b["top"] + b["height"] / 2


def _clean(text: str) -> str:
    return " ".join(text.replace("\v", " ").replace("\xa0", " ").split())


def _nearest(value: float, anchors: dict[str, float]) -> str:
    return min(anchors, key=lambda k: abs(anchors[k] - value))


_TARGET_RE = re.compile(r"^\s*(\d{4})\s*:\s*(.+)$")
_VALUE_RE = re.compile(r"^(.*?):\s*([\d][\d,]*\.?\d*)\s*(.*)$")


def _target(text: str, **extra: Any) -> dict:
    """"2030: Achieving Zero Waste to Landfill" -> year 2030 + label; text kept as written."""
    m = _TARGET_RE.match(text)
    return {"year": int(m.group(1)) if m else None, "label": _clean(m.group(2)) if m else _clean(text),
            "text": _clean(text), **extra}


def _value(text: str) -> dict:
    """"Water Recycled: 39.5 KL" -> value 39.5 unit KL. Text with no number keeps value None."""
    t = _clean(text)
    m = _VALUE_RE.match(t)
    if not m:
        return {"text": t, "value": None, "unit": None}
    return {"text": t, "measure": _clean(m.group(1)), "value": float(m.group(2).replace(",", "")),
            "unit": _clean(m.group(3)) or None}


def _columns(sl: _Slide, header_ids: list[str]) -> dict[str, float]:
    return {h: sl.cx(h) for h in header_ids}


def _empty(number: int, title: str, structure: str, kind: str) -> dict:
    return {
        "number": number, "stage": _STAGE_OF[number], "stageLabel": _STAGE_LABEL[_STAGE_OF[number]],
        "title": title, "kind": kind,
        # structure: "full" (all text structured), "partial" (some content is only in the picture), "image" (picture only)
        "structure": structure,
        "headings": [], "bullets": [], "tables": [], "targets": [], "statuses": [], "charts": [],
        "footnotes": [], "notes": [],
        "imageUrl": f"/api/deck/slides/{number}/image",
    }


def _bullets_block(sl: _Slide, sid: str, heading_first: bool = True) -> dict:
    """A text box whose first paragraph is a heading and the rest are bullet lines.
    Items carry `marker: "tick"` when the deck gives that line a tick-mark bullet."""
    b = sl.box(sid)
    paras = [p for p in b["paragraphs"] if p["text"].strip()] if b else []
    heading = _clean(paras[0]["text"]) if (paras and heading_first) else None
    rest = paras[1:] if heading_first else paras
    items = []
    for p in rest:
        item: dict[str, Any] = {"text": _clean(p["text"])}
        if p.get("bullet") == "✔":
            item["marker"] = MARKER_CHECK
        items.append(item)
    return {"heading": heading, "items": items, "left": b["left"] if b else None}


# ----------------------------------------------------------------------------
# Per-slide builders
# ----------------------------------------------------------------------------

def _s1(sl: _Slide, out: dict) -> None:
    out["headings"] = [sl.text("170")]
    out["notes"].append("Cover slide; the footer text 'Confidential |' is omitted.")


def _s2(sl: _Slide, out: dict) -> None:
    out["kind"] = "agenda"
    out["bullets"].append({"heading": None, "items": [{"text": t} for t in sl.lines("177")]})
    # Blank paragraphs in the right-hand box separate the agenda sections; the first line of each is its name.
    group: list[str] = []
    groups: list[list[str]] = []
    for p in sl.box("178")["paragraphs"]:
        if p["text"].strip():
            group.append(_clean(p["text"]))
        elif group:
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    for g in groups:
        out["bullets"].append({"heading": g[0], "items": [{"text": t} for t in g[1:]]})
    out["notes"].append("Agenda items are listed as written; the left column has no section headings in the deck.")


def _s3(sl: _Slide, out: dict) -> None:
    out["headings"] = [sl.text("185")]
    vision = sl.text("184")
    out["bullets"].append({"heading": "Vision", "items": [{"text": _clean(vision.split(":", 1)[1] if ":" in vision else vision)}]})
    out["bullets"].append({"heading": _clean(sl.text("195")).rstrip(":"),
                           "items": [{"text": sl.text(i)} for i in ("191", "192", "193", "194")]})


def _s4(sl: _Slide, out: dict) -> None:
    out["kind"] = "pillars"
    out["headings"] = [_clean(sl.text("200"))]
    pillars = {"201/211": sl.text("201/211"), "201/212": sl.text("201/212"), "201/213": sl.text("201/213")}
    cols = _columns(sl, list(pillars))
    names = {k: _clean(v.replace("\n", " ")) for k, v in pillars.items()}
    for sid in ("201/202", "201/203", "201/204", "201/205", "201/206", "201/207", "201/208", "201/209", "201/210"):
        pillar = names[_nearest(sl.cx(sid), cols)]
        for line in sl.lines(sid):
            out["targets"].append(_target(line, pillar=pillar, y=round(sl.cy(sid), 3)))
    out["targets"].sort(key=lambda t: (list(names.values()).index(t["pillar"]), t.pop("y")))
    out["headings"] += list(names.values())
    out["notes"].append("Pillar headings are in the slide's left-to-right order; targets are assigned to a pillar by their column.")


def _s5(sl: _Slide, out: dict) -> None:
    out["kind"] = "challenges"
    heads = {"226": sl.text("226"), "227": sl.text("227"), "228": sl.text("228")}
    cols = _columns(sl, list(heads))
    groups: dict[str, list[tuple[float, str]]] = {h: [] for h in heads}
    for sid in map(str, range(230, 241)):
        if sid in sl.boxes and sl.text(sid):
            groups[_nearest(sl.cx(sid), cols)].append((sl.cy(sid), _clean(sl.text(sid))))
    for h, name in heads.items():
        out["headings"].append(_clean(name))
        out["bullets"].append({"heading": _clean(name), "items": [{"text": t} for _, t in sorted(groups[h])]})
    out["notes"].append("The three headings are the Aalingana target areas; each list is the challenges the slide places under it.")


def _s6(sl: _Slide, out: dict, pics: list[dict]) -> None:
    out["kind"] = "pillar_status"
    out["headings"] = [_clean(sl.text("248"))]
    heads = {"252": sl.text("252"), "253": sl.text("253"), "254": sl.text("254")}
    cols = _columns(sl, list(heads))
    name = {k: _clean(v.replace("\n", " ")) for k, v in heads.items()}
    target_by_col = {_nearest(sl.cx(i), cols): i for i in ("250", "251", "249")}
    # Column order on the slide: 252 (left), 253 (middle), 254 (right).
    status_texts: dict[str, list[tuple[float, str]]] = {h: [] for h in heads}
    for sid in ("262", "263", "265", "267"):
        status_texts[_nearest(sl.cx(sid), cols)].append((sl.cy(sid), _clean(sl.text(sid))))
    # Status markers: two tick pictures (shapes 264, 268) and the text "WIP" (shape 266).
    marks: dict[str, dict] = {}
    for p in pics:
        if p["id"] in ("264", "268"):
            marks[_nearest(p["cx"], {h: sl.cx(h) for h in heads})] = {
                "marker": MARKER_CHECK, "source": "picture",
                "note": "A tick-mark image sits next to this target on the slide; the slide gives no word for what it means."}
    marks[_nearest(sl.cx("266"), cols)] = {"marker": "WIP", "source": "text", "text": sl.text("266")}
    for h in heads:
        t = target_by_col[h]
        out["targets"].append({**_target(sl.text(t)), "pillar": name[h]})
        out["statuses"].append({"pillar": name[h], "target": _clean(sl.text(t)), **marks.get(h, {"marker": None}),
                                "details": [x for _, x in sorted(status_texts[h])]})
    out["headings"] += list(name.values())
    out["notes"].append("The tick marks are pictures; their column was read from the picture position. "
                        "The deck's 'Current Status' text (details) sits below each target.")


def _s7(sl: _Slide, out: dict) -> None:
    out["kind"] = "goals_table"
    out["headings"] = [sl.text("273")]
    goal_ids = ["274/276", "277/279", "280/282", "283/285", "286/288", "289/291", "292/294"]
    tgt_ids = ["295/" + str(i) for i in range(296, 303)]
    cur_ids = ["308/" + str(i) for i in range(309, 316)]
    rows = []
    # Baseline labels are rotated text boxes spanning several rows; use their visual extent.
    spans = []
    for sid in ("307", "317"):
        b = sl.box(sid)
        # Fractions are of slide width/height, so a rotated box's width must be rescaled to a height fraction.
        aspect = deck.deck_size()["aspect"] or 16 / 9
        half = (b["width"] * aspect if b["rotation"] in (90.0, 270.0) else b["height"]) / 2
        spans.append((sl.text(sid), sl.cy(sid) - half, sl.cy(sid) + half))
    for g in goal_ids:
        y = sl.cy(g)
        t = min(tgt_ids, key=lambda s: abs(sl.cy(s) - y))
        c = min(cur_ids, key=lambda s: abs(sl.cy(s) - y))
        base = next((label for label, lo, hi in spans if lo <= y <= hi), None)
        goal = _target(sl.text(g))
        rows.append([goal["text"], _clean(sl.text(t)), _clean(sl.text(c)), _clean(base) if base else None])
        out["targets"].append({
            "year": goal["year"], "label": goal["label"], "baselineType": _clean(base) if base else None,
            "target": _value(sl.text(t)), "current": _value(sl.text(c)),
        })
    out["tables"].append({"title": "Project milestones: goal, target, current (up to Mar-2026)",
                          "columns": ["Goal", sl.text("295/303"), sl.text("308/316"), "Baseline"], "rows": rows})
    out["footnotes"].append(_clean(sl.text("304")))
    out["notes"].append("Target and current cells are quoted as written; 'value' and 'unit' are filled only where the cell holds a number. "
                        "Where the current cell has no number (e.g. 'Waste Generated in 2030') value is null.")


def _s8(sl: _Slide, out: dict) -> None:
    out["kind"] = "diagram"
    out["structure"] = "partial"
    out["headings"] = [_clean(sl.text("322"))]
    # The SCOPE 1/2/3 labels are part of a picture, so which box belongs to which scope is read from the image.
    def items(sid: str) -> list[dict]:
        return [{"text": t} for t in sl.lines(sid)]
    out["bullets"] = [
        {"heading": "Scope 1", "items": items("326"), "scopeLabelSource": "picture"},
        {"heading": "Scope 2 (upstream)", "items": items("327"), "scopeLabelSource": "picture"},
        {"heading": "Scope 3 (upstream)", "items": items("328"), "scopeLabelSource": "picture"},
        {"heading": "Scope 3 (downstream)", "items": items("329"), "scopeLabelSource": "picture"},
        {"heading": "Offsets", "items": items("331") + items("333")},
    ]
    out["footnotes"].append(_clean(sl.text("323")))
    out["notes"].append("Mainly a diagram: the 'Scope 1/2/3' labels and the picture itself are images, so the grouping "
                        "of the lists under scopes was read from the rendered slide. Show the slide image alongside.")


def _four_columns(sl: _Slide, out: dict, ids: list[str], title_id: str) -> None:
    out["kind"] = "kpi_columns"
    out["headings"] = [_clean(sl.text(title_id))]
    for sid in sorted(ids, key=lambda s: sl.cx(s)):
        blk = _bullets_block(sl, sid)
        out["bullets"].append({"heading": blk["heading"], "items": blk["items"]})
    if any(i.get("marker") for b in out["bullets"] for i in b["items"]):
        out["notes"].append("Items with marker 'tick' have a tick-mark bullet on the slide; the slide gives no word for what it means.")


def _s9(sl: _Slide, out: dict) -> None:
    _four_columns(sl, out, ["342", "343", "344", "345"], "341")


def _s10(sl: _Slide, out: dict) -> None:
    out["kind"] = "picture"
    out["structure"] = "image"
    out["headings"] = [_clean(sl.text("351"))]
    out["notes"].append("The four KPI columns on this slide are one picture (no text in the file). It shows the same "
                        "headings as slides 9 and 11 with arrow bullets and two green tick icons next to 'Proper Separation of "
                        "Scrap (Hazardous, E-Waste, Battery)' and 'Effective Selling to Recycler'. Show the slide image.")
    out["notes"].append("The columns' text boxes in the file are placeholders without the picture's content; use slide 9 or 11 text for the wording.")


def _s11(sl: _Slide, out: dict) -> None:
    _four_columns(sl, out, ["363", "364", "365", "366"], "362")
    out["notes"].append("Title says 'By 2026' while slides 9-10 say 'By 2030'; the KPI text is otherwise the same.")


def _s12(sl: _Slide, out: dict) -> None:
    out["kind"] = "list"
    out["headings"] = [_clean(sl.text("372"))]
    for sid in ("373", "374"):
        blk = _bullets_block(sl, sid)
        out["bullets"].append({"heading": blk["heading"], "items": blk["items"]})
    out["notes"].append("The slide has two further empty boxes; they are omitted.")


def _s13(sl: _Slide, out: dict, chart: dict | None) -> None:
    out["kind"] = "chart_infographic"
    out["structure"] = "partial"
    out["headings"] = [_clean(sl.text("384"))]
    out["tables"].append({"title": sl.text("439/r0c0") or "Solar Panel Availability", "columns": ["Plant", "Solar panel availability"],
                          "rows": [[sl.text(f"439/r{r}c0"), sl.text(f"439/r{r}c1")] for r in range(1, 9)]})
    out["targets"] = [
        {"label": "Energy requirement and sources (Million kWh, labels on the chart)",
         "points": [{"fy": sl.text("444"), "electricityRequirement": sl.text("461"), "solarSharePercent": sl.text("456")},
                    {"fy": sl.text("445"), "electricityRequirement": sl.text("462"), "solarSharePercent": sl.text("457")},
                    {"fy": sl.text("446"), "electricityRequirement": sl.text("463"), "solarSharePercent": sl.text("458")}],
         "deltas": [sl.text("460"), sl.text("459"), sl.text("450")]},
        {"label": "Current solar generation", "text": f"{sl.text('427')} ({sl.text('425')}); {_clean(sl.text('443'))}"},
    ]
    out["bullets"] = [
        {"heading": sl.lines("434")[0].rstrip(":"), "items": [{"text": t} for t in sl.lines("434")[1:]]},
        {"heading": sl.lines("436")[0].rstrip(":"), "items": [{"text": t} for t in sl.lines("436")[1:]]},
    ]
    if chart:
        out["charts"].append(chart)
    out["notes"].append("A mixed infographic: the text, table and chart labels are structured here, but the stacked-bar parts "
                        "(current vs target solar units) are drawn as shapes and only their delta labels are text. Show the slide image.")
    out["notes"].append("The chart's data cache holds one series (59.6M, 98.4M, 146.8M units) without category labels; the FY labels are separate text boxes.")


def _s14(sl: _Slide, out: dict, chart: dict | None) -> None:
    out["kind"] = "chart"
    out["structure"] = "partial"
    out["headings"] = [_clean(sl.text("476"))]
    out["targets"] = [{"label": "Renewable energy share target", "text": sl.text("471")}]
    out["footnotes"].append(_clean(sl.text("469")))
    if chart:
        out["charts"].append(chart)
    out["notes"].append("Chart data is read from the chart's embedded values. Labels on the chart (30.7% ... 16.5%) are the same "
                        "values rounded. The slide also embeds an Excel object ('Click the Excel file for calculations') that is not read.")


_BUILDERS = {1: _s1, 2: _s2, 3: _s3, 4: _s4, 5: _s5, 7: _s7, 8: _s8, 9: _s9, 10: _s10, 11: _s11, 12: _s12}


# ----------------------------------------------------------------------------
# Charts and pictures straight from the pptx
# ----------------------------------------------------------------------------

def _walk(shapes):
    for s in shapes:
        if s.shape_type == 6:
            yield from _walk(s.shapes)
        else:
            yield s


def _charts_and_pictures() -> tuple[dict[int, dict], dict[int, list[dict]]]:
    work = deck.ensure_working_copy()
    charts: dict[int, dict] = {}
    pics: dict[int, list[dict]] = {}
    if work is None:
        return charts, pics
    prs = Presentation(str(work))
    sw, sh = prs.slide_width, prs.slide_height
    for n in range(1, SLIDE_COUNT + 1):
        slide = prs.slides[n - 1]
        for s in _walk(slide.shapes):
            if s.shape_type == 13:
                pics.setdefault(n, []).append({"id": str(s.shape_id), "cx": (s.left + s.width / 2) / sw,
                                               "cy": (s.top + s.height / 2) / sh})
            if getattr(s, "has_chart", False) and s.has_chart:
                c = s.chart
                series = []
                cats: list[str] = []
                for plot in c.plots:
                    cats = [str(x) for x in plot.categories] or cats
                    series += [{"name": se.name, "values": [None if v is None else float(v) for v in se.values]} for se in plot.series]
                charts[n] = {"shapeId": str(s.shape_id), "type": str(c.chart_type), "categories": cats, "series": series}
    return charts, pics


# ----------------------------------------------------------------------------
# Public functions
# ----------------------------------------------------------------------------

def milestone_slides() -> dict:
    models = deck._all_slide_models()
    notes: list[str] = []
    if len(models) < SLIDE_COUNT:
        return {"available": False, "stages": STAGES, "sequence": [], "slides": [],
                "notes": ["Sustainability Deck not found or has fewer than 14 slides."]}
    charts, pics = _charts_and_pictures()
    slides = []
    for n in range(1, SLIDE_COUNT + 1):
        model = models[n - 1]
        sl = _Slide(model)
        out = _empty(n, model["title"], "full", "text")
        try:
            if n == 6:
                _s6(sl, out, pics.get(6, []))
            elif n == 13:
                _s13(sl, out, charts.get(13))
            elif n == 14:
                _s14(sl, out, charts.get(14))
            else:
                _BUILDERS[n](sl, out)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            # A shape was deleted or renamed by an edit: fall back to the slide's plain text, never fail the page.
            out["structure"] = "partial"
            out["bullets"] = [{"heading": None, "items": [{"text": _clean(p["text"])} for b in model["textBoxes"]
                                                         for p in b["paragraphs"] if p["text"].strip()]}]
            out["notes"].append(f"Slide layout changed since the extraction was written ({type(exc).__name__}); plain text shown.")
        if not out["title"]:
            out["title"] = out["headings"][0] if out["headings"] else ""
        slides.append(out)
    sequence = [{"slide": s["number"], "stage": s["stage"], "title": s["title"], "kind": s["kind"],
                 "structure": s["structure"]} for s in slides]
    notes.append("Stages are the grouping worked out from slide content; 'Tata' is not a separate stage in slides 1-14 "
                 "(it is the owner of Project Aalingana and the branding).")
    return {"available": True, "stages": STAGES, "sequence": sequence, "slides": slides,
            "imageOnlySlides": [s["number"] for s in slides if s["structure"] == "image"],
            "partialSlides": [s["number"] for s in slides if s["structure"] == "partial"],
            "renderer": deck.renderer_kind(), "notes": notes}


def milestone_slide(n: int) -> dict | None:
    data = milestone_slides()
    return next((s for s in data["slides"] if s["number"] == n), None)


@router.get("")
def milestones_route() -> dict:
    return milestone_slides()


@router.get("/sequence")
def sequence_route() -> dict:
    d = milestone_slides()
    return {"stages": d["stages"], "sequence": d["sequence"], "notes": d["notes"]}


@router.get("/slides/{n}")
def milestone_slide_route(n: int) -> dict:
    s = milestone_slide(n)
    if s is None:
        raise HTTPException(404, "Milestone slides are numbered 1 to 14")
    return s
