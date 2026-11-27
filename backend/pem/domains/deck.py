"""The Sustainability Deck: view, edit, save, download and approve.

The source .pptx in `common/` is never modified. A working copy lives in
`<data_dir>/deck/working.pptx`; edits are written into it with python-pptx
(keeping each run's formatting) and slide pictures are rendered from it through
Microsoft PowerPoint automation when PowerPoint is installed.

Slide pictures are cached per slide and keyed by a hash of the slide's XML, so
only slides whose content changed are re-rendered after a save.
"""
from __future__ import annotations

import copy
import hashlib
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn
from pydantic import BaseModel, Field

from .. import store
from ..config import find_source_file, settings

router = APIRouter(prefix="/api/deck", tags=["deck"])

IMAGE_WIDTH = 1600
_STATE_KEY = "deck/state"
_EDITS_KEY = "deck/edit_log"
_APPROVAL_KEY = "deck/approval"
_RENDER_KEY = "deck/render_index"

_edit_lock = threading.RLock()     # guards the working copy and the JSON records
_render_lock = threading.Lock()    # one PowerPoint render at a time
_progress: dict[str, Any] = {"state": "idle", "total": 0, "done": 0, "error": None,
                             "startedAt": None, "finishedAt": None, "lastSeconds": None}
_model_cache: dict[str, Any] = {"key": None, "slides": None}
_renderer_cache: dict[str, str] = {}


# ----------------------------------------------------------------------------
# Paths and the working copy
# ----------------------------------------------------------------------------

def deck_dir() -> Path:
    d = settings.data_dir / "deck"
    d.mkdir(parents=True, exist_ok=True)
    return d


def working_path() -> Path:
    return deck_dir() / "working.pptx"


def slides_dir() -> Path:
    d = deck_dir() / "slides"
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_path() -> Path | None:
    return find_source_file("common", "sustainability deck", suffix=".pptx")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state() -> dict:
    return store.read_json(_STATE_KEY, {})


def ensure_working_copy() -> Path | None:
    """Copy the source deck on first use; refresh the copy if the source is newer
    and nothing has been edited. Returns None when there is no source deck."""
    with _edit_lock:
        src = source_path()
        work = working_path()
        state = _state()
        if src is None:
            return work if work.is_file() else None
        src_mtime = src.stat().st_mtime
        stale = state.get("sourceMtime") is not None and src_mtime > state["sourceMtime"]
        if not work.is_file() or (stale and not state.get("hasEdits")):
            shutil.copyfile(src, work)
            state = {"sourceName": src.name, "sourceMtime": src_mtime, "hasEdits": False,
                     "copiedAt": _now()}
            store.write_json(_STATE_KEY, state)
            _model_cache["key"] = None
        return work


def reset_to_original() -> dict:
    """Throw away all edits: recopy the source and clear the edit log. Approval returns to draft."""
    with _edit_lock:
        src = source_path()
        if src is None:
            return {"ok": False, "notes": ["Source deck not found; nothing to reset to."]}
        shutil.copyfile(src, working_path())
        store.write_json(_STATE_KEY, {"sourceName": src.name, "sourceMtime": src.stat().st_mtime,
                                      "hasEdits": False, "copiedAt": _now()})
        store.write_json(_EDITS_KEY, [])
        _model_cache["key"] = None
        _drop_approval_if_any("Deck reset to the original file")
    start_render_async()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Reading slides into an editable model
# ----------------------------------------------------------------------------

def _frac(v: float, total: float) -> float:
    return round(v / total, 5) if total else 0.0


def _run_items(p) -> list[dict]:
    """Children of a paragraph that carry text, in order. Line breaks are kept
    as their own item so a save can keep them where they were."""
    items = []
    for child in p._p:
        tag = etree.QName(child).localname
        if tag == "r":
            items.append({"text": child.findtext(qn("a:t")) or ""})
        elif tag == "fld":
            items.append({"text": child.findtext(qn("a:t")) or "", "field": True})
        elif tag == "br":
            items.append({"text": "\n", "break": True})
    return items


def _paragraphs(tf) -> list[dict]:
    out = []
    for p in tf.paragraphs:
        runs = _run_items(p)
        ppr = p._p.pPr
        bu = ppr.find(qn("a:buChar")) if ppr is not None else None
        # "bullet" is the explicit bullet character (e.g. a tick) when the paragraph sets one itself.
        out.append({"text": "".join(r["text"] for r in runs), "runs": runs, "level": p.level,
                    "bullet": bu.get("char") if bu is not None else None})
    return out


def _has_text(tf) -> bool:
    return any(p.text.strip() for p in tf.paragraphs)


def _shape_boxes(shapes, sw: float, sh: float, xf: tuple, prefix: str, out: list, in_group: bool) -> None:
    """Recursively collect text shapes. `xf` maps a child coordinate to slide EMU:
    (scale_x, off_x, scale_y, off_y). Rotation is not modelled (noted in docs)."""
    sx, ox, sy, oy = xf
    for shp in shapes:
        sid = f"{prefix}{shp.shape_id}"
        try:
            left, top, width, height = shp.left, shp.top, shp.width, shp.height
        except Exception:  # noqa: BLE001
            left = top = width = height = None
        if shp.shape_type == 6:  # group
            if None in (left, top, width, height):
                continue
            el = shp._element.grpSpPr.find(qn("a:xfrm"))
            ch_off, ch_ext = el.find(qn("a:chOff")), el.find(qn("a:chExt"))
            cw, chh = int(ch_ext.get("cx")) or 1, int(ch_ext.get("cy")) or 1
            gsx, gsy = width / cw, height / chh
            # Child coordinates -> group box on the slide -> through the parent transform.
            child_sx, child_sy = gsx * sx, gsy * sy
            child_ox = ox + (left - int(ch_off.get("x")) * gsx) * sx
            child_oy = oy + (top - int(ch_off.get("y")) * gsy) * sy
            _shape_boxes(shp.shapes, sw, sh, (child_sx, child_ox, child_sy, child_oy), sid + "/", out, True)
            continue
        if getattr(shp, "has_table", False) and shp.has_table and None not in (left, top):
            tbl = shp.table
            col_x = [0]
            for c in tbl.columns:
                col_x.append(col_x[-1] + c.width)
            row_y = [0]
            for r in tbl.rows:
                row_y.append(row_y[-1] + r.height)
            for ri, row in enumerate(tbl.rows):
                for ci, cell in enumerate(row.cells):
                    if cell.is_spanned or not _has_text(cell.text_frame):
                        continue
                    cs = max(cell.span_width, 1)
                    rs = max(cell.span_height, 1)
                    x0, y0 = left + col_x[ci], top + row_y[ri]
                    w = col_x[min(ci + cs, len(col_x) - 1)] - col_x[ci]
                    h = row_y[min(ri + rs, len(row_y) - 1)] - row_y[ri]
                    out.append(_box(f"{sid}/r{ri}c{ci}", "tablecell", shp.name, cell.text_frame,
                                    (x0, y0, w, h), xf, sw, sh, in_group))
            continue
        if shp.has_text_frame and _has_text(shp.text_frame) and None not in (left, top, width, height):
            out.append(_box(sid, "text", shp.name, shp.text_frame, (left, top, width, height), xf, sw, sh, in_group,
                            float(getattr(shp, "rotation", 0.0) or 0.0)))


def _box(sid, kind, name, tf, geom, xf, sw, sh, in_group, rotation: float = 0.0) -> dict:
    sx, ox, sy, oy = xf
    left, top, width, height = geom
    return {
        "id": sid, "kind": kind, "name": name, "inGroup": in_group, "rotation": rotation,
        "left": _frac(ox + left * sx, sw), "top": _frac(oy + top * sy, sh),
        "width": _frac(width * sx, sw), "height": _frac(height * sy, sh),
        "paragraphs": _paragraphs(tf),
    }


def _slide_hash(slide) -> str:
    return hashlib.sha1(etree.tostring(slide._element)).hexdigest()[:12]


def _slide_title(slide, boxes: list[dict]) -> str:
    try:
        t = slide.shapes.title
        if t is not None and t.has_text_frame and t.text_frame.text.strip():
            return " ".join(t.text_frame.text.split())[:140]
    except Exception:  # noqa: BLE001
        pass
    # No title placeholder: use the first text near the top of the slide.
    top_boxes = sorted((b for b in boxes if b["paragraphs"]), key=lambda b: (round(b["top"], 2), b["left"]))
    for b in top_boxes:
        text = " ".join(b["paragraphs"][0]["text"].split())
        if text:
            return text[:140]
    return ""


def _all_slide_models() -> list[dict]:
    work = ensure_working_copy()
    if work is None:
        return []
    key = (str(work), work.stat().st_mtime_ns)
    if _model_cache["key"] == key:
        return _model_cache["slides"]
    prs = Presentation(str(work))
    sw, sh = prs.slide_width, prs.slide_height
    models = []
    for i, slide in enumerate(prs.slides, 1):
        boxes: list[dict] = []
        _shape_boxes(slide.shapes, sw, sh, (1.0, 0.0, 1.0, 0.0), "", boxes, False)
        models.append({"number": i, "title": _slide_title(slide, boxes), "layout": slide.slide_layout.name,
                       "hash": _slide_hash(slide), "textBoxes": boxes})
    _model_cache.update(key=key, slides=models)
    return models


def deck_size() -> dict:
    work = ensure_working_copy()
    if work is None:
        return {"widthEmu": None, "heightEmu": None, "aspect": None}
    prs = Presentation(str(work))
    return {"widthEmu": prs.slide_width, "heightEmu": prs.slide_height,
            "widthInches": round(prs.slide_width / 914400, 3), "heightInches": round(prs.slide_height / 914400, 3),
            "aspect": round(prs.slide_width / prs.slide_height, 4)}


def _image_url(n: int, h: str) -> str | None:
    idx = store.read_json(_RENDER_KEY, {})
    if idx.get(str(n)) == h and (slides_dir() / f"{n}.png").is_file():
        return f"/api/deck/slides/{n}/image?v={h}"
    return None


def get_slide(n: int) -> dict | None:
    slides = _all_slide_models()
    if not 1 <= n <= len(slides):
        return None
    m = dict(slides[n - 1])
    size = deck_size()
    return {**m, "size": {"aspect": size["aspect"], "widthInches": size.get("widthInches"),
                          "heightInches": size.get("heightInches")},
            "imageUrl": _image_url(n, m["hash"]), "renderer": renderer_kind()}


def list_slides() -> list[dict]:
    """Slide list with titles and image URLs, without the text boxes."""
    out = []
    for m in _all_slide_models():
        out.append({"number": m["number"], "title": m["title"], "layout": m["layout"],
                    "textBoxCount": len(m["textBoxes"]), "imageUrl": _image_url(m["number"], m["hash"])})
    return out


# ----------------------------------------------------------------------------
# Rendering through PowerPoint
# ----------------------------------------------------------------------------

def renderer_kind() -> str:
    """"powerpoint" when PowerPoint automation is available, else "none"."""
    if "kind" not in _renderer_cache:
        kind = "none"
        try:
            import winreg
            import win32com.client  # noqa: F401
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application\CLSID"))
            kind = "powerpoint"
        except Exception:  # noqa: BLE001 - not Windows, no pywin32 or no PowerPoint
            kind = "none"
        _renderer_cache["kind"] = kind
    return _renderer_cache["kind"]


def _stale_slides() -> list[int]:
    idx = store.read_json(_RENDER_KEY, {})
    stale = []
    for m in _all_slide_models():
        if idx.get(str(m["number"])) != m["hash"] or not (slides_dir() / f"{m['number']}.png").is_file():
            stale.append(m["number"])
    return stale


def _export_with_powerpoint(snapshot: Path, numbers: list[int], height: int, on_done) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = pres = None
    started_by_us = False
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        started_by_us = app.Presentations.Count == 0
        pres = app.Presentations.Open(str(snapshot), True, False, False)  # read-only, untitled, no window
        for n in numbers:
            pres.Slides(n).Export(str(slides_dir() / f"{n}.png"), "PNG", IMAGE_WIDTH, height)
            on_done(n)
    finally:
        try:
            if pres is not None:
                pres.Close()
        except Exception:  # noqa: BLE001
            pass
        try:
            # Only quit an instance we started; never close a PowerPoint the user is working in.
            if app is not None and started_by_us and app.Presentations.Count == 0:
                app.Quit()
        except Exception:  # noqa: BLE001
            pass
        app = pres = None
        pythoncom.CoUninitialize()


def render_slides(numbers: list[int] | None = None) -> dict:
    """Render the given slides (default: every slide whose picture is missing or out of date).
    Blocking; call from a background thread. Serialised by a lock."""
    if renderer_kind() == "none":
        return {"rendered": [], "renderer": "none"}
    with _render_lock:
        slides = _all_slide_models()
        by_n = {m["number"]: m["hash"] for m in slides}
        todo = [n for n in (numbers if numbers is not None else _stale_slides()) if n in by_n]
        if not todo:
            return {"rendered": [], "renderer": "powerpoint"}
        size = deck_size()
        height = round(IMAGE_WIDTH / (size["aspect"] or 16 / 9))
        with _edit_lock:  # snapshot so a concurrent save is not blocked by PowerPoint's file lock
            snapshot = deck_dir() / "render_snapshot.pptx"
            shutil.copyfile(working_path(), snapshot)
        _progress.update(state="rendering", total=len(todo), done=0, error=None, startedAt=_now(), finishedAt=None)
        t0 = time.time()
        idx = store.read_json(_RENDER_KEY, {})

        def done(n: int) -> None:
            idx[str(n)] = by_n[n]
            _progress["done"] += 1
            store.write_json(_RENDER_KEY, idx)  # persisted per slide so the page shows pictures as they appear

        try:
            _export_with_powerpoint(snapshot, todo, height, done)
            _progress.update(state="idle", lastSeconds=round(time.time() - t0, 1), finishedAt=_now())
        except Exception as exc:  # noqa: BLE001 - never crash a worker thread
            _progress.update(state="error", error=f"{type(exc).__name__}: {exc}", finishedAt=_now())
        finally:
            try:
                snapshot.unlink()
            except OSError:
                pass
        return {"rendered": todo, "renderer": "powerpoint", "seconds": round(time.time() - t0, 1)}


_rerun = threading.Event()


def _render_worker(numbers: list[int] | None) -> None:
    render_slides(numbers)
    # A save that arrived while we were rendering asked for another pass.
    for _ in range(3):
        if not _rerun.is_set():
            break
        _rerun.clear()
        render_slides(None)


def start_render_async(numbers: list[int] | None = None) -> bool:
    """Start rendering in a background thread; if one is running, ask it to do another pass."""
    if renderer_kind() == "none":
        return False
    if _render_lock.locked():
        _rerun.set()
        return False
    threading.Thread(target=_render_worker, args=(numbers,), daemon=True, name="deck-render").start()
    return True


# ----------------------------------------------------------------------------
# Saving edits
# ----------------------------------------------------------------------------

class ParagraphIn(BaseModel):
    text: str | None = None
    runs: list[dict] | None = None   # [{"text": "..."}], same shape the slide model returns


class EditIn(BaseModel):
    slide: int
    shapeId: str
    paragraphs: list[ParagraphIn | str]


class SaveIn(BaseModel):
    edits: list[EditIn]
    author: str | None = Field(default=None, description="Who is saving (free text)")


def _find_text_frame(slide, shape_id: str):
    """Resolve ids like "168/170" (shape in group) or "439/r1c0" (table cell)."""
    parts = shape_id.split("/")
    shapes = slide.shapes
    shp = None
    for i, part in enumerate(parts):
        if part.startswith("r") and "c" in part and i == len(parts) - 1 and shp is not None and getattr(shp, "has_table", False):
            r, c = part[1:].split("c")
            return shp.table.cell(int(r), int(c)).text_frame
        shp = next((s for s in shapes if str(s.shape_id) == part), None)
        if shp is None:
            return None
        if shp.shape_type == 6:
            shapes = shp.shapes
    return shp.text_frame if shp is not None and shp.has_text_frame else None


def _para_text(item: ParagraphIn | str) -> tuple[str, list[str] | None]:
    if isinstance(item, str):
        return item, None
    if item.runs is not None:
        return "".join(r.get("text", "") for r in item.runs), [r.get("text", "") for r in item.runs]
    return item.text or "", None


def _set_paragraph(p, text: str, run_texts: list[str] | None) -> None:
    """Write text into paragraph `p` keeping formatting.

    If the caller sends the same number of runs as the paragraph has, each run keeps its own
    formatting. Otherwise the whole text goes into the first run (its font, size and colour
    are kept) and the other runs are removed."""
    items = [c for c in p._p if etree.QName(c).localname in ("r", "fld", "br")]
    if run_texts is not None and len(run_texts) == len(items):
        for el, t in zip(items, run_texts):
            if etree.QName(el).localname != "br":
                el.find(qn("a:t")).text = t
        return
    runs = [c for c in items if etree.QName(c).localname in ("r", "fld")]
    if runs:
        first = runs[0]
    else:  # empty paragraph: new run takes the paragraph's end-of-paragraph formatting
        first = etree.SubElement(p._p, qn("a:r"))
        end = p._p.find(qn("a:endParaRPr"))
        if end is not None:
            rpr = copy.deepcopy(end)
            rpr.tag = qn("a:rPr")
            first.append(rpr)
        etree.SubElement(first, qn("a:t"))
        if end is not None:
            p._p.remove(end)
            p._p.append(end)
        items = [first]
    for el in items:
        if el is not first:
            p._p.remove(el)
    if etree.QName(first).localname == "fld":  # a field would re-compute its text; make it plain text
        first.tag = qn("a:r")
        for child in list(first):
            if etree.QName(child).localname not in ("rPr", "t"):
                first.remove(child)
    lines = text.split("\n")
    first.find(qn("a:t")).text = lines[0]
    anchor = first
    for line in lines[1:]:
        br = etree.Element(qn("a:br"))
        rpr = first.find(qn("a:rPr"))
        if rpr is not None:
            br.append(copy.deepcopy(rpr))
        nr = copy.deepcopy(first)
        nr.find(qn("a:t")).text = line
        anchor.addnext(br)
        br.addnext(nr)
        anchor = nr


def _apply_edit(slide, edit: EditIn) -> dict | None:
    tf = _find_text_frame(slide, edit.shapeId)
    if tf is None:
        return {"error": f"slide {edit.slide}: shape {edit.shapeId} not found"}
    new = [_para_text(p) for p in edit.paragraphs]
    before = [p.text for p in tf.paragraphs]
    after_plain = [t for t, _ in new]
    if [b.replace("\v", "\n") for b in before] == after_plain:
        return None
    paras = list(tf.paragraphs)
    for i, (text, runs) in enumerate(new):
        if i < len(paras):
            if paras[i].text.replace("\v", "\n") != text:
                _set_paragraph(paras[i], text, runs)
        else:  # more paragraphs than before: clone the last one so it keeps the same look
            template = tf.paragraphs[-1]._p
            clone = copy.deepcopy(template)
            template.addnext(clone)
            from pptx.text.text import _Paragraph
            _set_paragraph(_Paragraph(clone, tf), text, None)
    for extra in list(tf.paragraphs)[len(new):]:  # fewer paragraphs: drop the trailing ones
        extra._p.getparent().remove(extra._p)
    return {"slide": edit.slide, "shapeId": edit.shapeId, "before": before,
            "after": [p.text for p in tf.paragraphs]}


def save_edits(edits: list[EditIn], author: str | None = None) -> dict:
    """Apply edits to the working copy. Returns what changed and any errors."""
    with _edit_lock:
        work = ensure_working_copy()
        if work is None:
            return {"ok": False, "changed": [], "errors": ["Source deck not found."]}
        prs = Presentation(str(work))
        changed, errors = [], []
        for e in edits:
            if not 1 <= e.slide <= len(prs.slides):
                errors.append(f"slide {e.slide} does not exist")
                continue
            res = _apply_edit(prs.slides[e.slide - 1], e)
            if res is None:
                continue
            (errors if "error" in res else changed).append(res["error"] if "error" in res else res)
        if changed:
            tmp = work.with_suffix(".tmp")
            prs.save(str(tmp))
            tmp.replace(work)
            now = _now()
            log = store.read_json(_EDITS_KEY, [])
            log.extend({"at": now, "by": author, **c} for c in changed)
            store.write_json(_EDITS_KEY, log[-2000:])
            state = _state()
            state["hasEdits"] = True
            store.write_json(_STATE_KEY, state)
            _model_cache["key"] = None
            _drop_approval_if_any("Edited after approval", author)
        slides = sorted({c["slide"] for c in changed})
    if changed:
        start_render_async()  # the lock-protected renderer picks up exactly the changed slides
    return {"ok": not errors, "changedSlides": slides, "changed": changed, "errors": errors,
            "rendering": bool(changed) and renderer_kind() != "none"}


# ----------------------------------------------------------------------------
# Approval workflow
# ----------------------------------------------------------------------------

def approval_status() -> dict:
    a = store.read_json(_APPROVAL_KEY, None)
    if not a:
        return {"status": "draft", "approvedBy": None, "approvedAt": None, "note": None, "history": []}
    return a


def _drop_approval_if_any(reason: str, who: str | None = None) -> None:
    a = store.read_json(_APPROVAL_KEY, None)
    if not a:
        return
    if a.get("status") == "approved":
        a["history"].append({"at": _now(), "event": "returned_to_draft", "by": who, "note": reason,
                             "was": {"approvedBy": a.get("approvedBy"), "approvedAt": a.get("approvedAt")}})
        a.update(status="draft", approvedBy=None, approvedAt=None, note=None)
        store.write_json(_APPROVAL_KEY, a)


def approve(by: str, note: str | None = None) -> dict:
    with _edit_lock:
        a = approval_status()
        a.update(status="approved", approvedBy=by, approvedAt=_now(), note=note)
        a["history"].append({"at": a["approvedAt"], "event": "approved", "by": by, "note": note})
        work = ensure_working_copy()
        if work is not None:  # keep exactly what was approved, even if the working copy is edited later
            shutil.copyfile(work, deck_dir() / "approved.pptx")
        store.write_json(_APPROVAL_KEY, a)
        return a


def withdraw(by: str | None = None, note: str | None = None) -> dict:
    with _edit_lock:
        a = approval_status()
        if a["status"] == "approved":
            a["history"].append({"at": _now(), "event": "approval_withdrawn", "by": by, "note": note,
                                 "was": {"approvedBy": a.get("approvedBy"), "approvedAt": a.get("approvedAt")}})
            a.update(status="draft", approvedBy=None, approvedAt=None, note=None)
            store.write_json(_APPROVAL_KEY, a)
        return a


# ----------------------------------------------------------------------------
# Info
# ----------------------------------------------------------------------------

def deck_info() -> dict:
    work = ensure_working_copy()
    notes: list[str] = []
    if work is None:
        return {"available": False, "slideCount": 0, "renderer": renderer_kind(),
                "notes": ["Sustainability Deck .pptx not found in the source folder."]}
    slides = _all_slide_models()
    state = _state()
    stale = _stale_slides() if renderer_kind() != "none" else []
    if renderer_kind() == "none":
        notes.append("No slide renderer on this machine: slides are served without images.")
    return {
        "available": True,
        "sourceFile": state.get("sourceName"),
        "downloadName": download_name(),
        "slideCount": len(slides),
        "size": deck_size(),
        "renderer": renderer_kind(),
        "render": {**_progress, "slidesWithoutCurrentImage": len(stale),
                   "imagesReady": len(slides) - len(stale) if renderer_kind() != "none" else 0},
        "approval": {k: v for k, v in approval_status().items() if k != "history"},
        "hasEditsFromOriginal": bool(state.get("hasEdits")),
        "editCount": len(store.read_json(_EDITS_KEY, [])),
        "notes": notes,
    }


def download_name() -> str:
    state = _state()
    base = Path(state.get("sourceName") or "Sustainability Deck.pptx").stem
    base = base.replace(" - Compressed (1)", "").replace(" - Compressed", "").strip()
    suffix = "approved" if approval_status()["status"] == "approved" else "draft"
    return f"{base} - {suffix}.pptx"


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------

@router.get("")
def info_route() -> dict:
    info = deck_info()
    if info.get("available") and info["renderer"] != "none" and info["render"]["slidesWithoutCurrentImage"] \
            and info["render"]["state"] != "rendering":
        start_render_async()  # first load: render in the background, never inside the request
    return info


@router.get("/slides")
def slides_route() -> dict:
    return {"slides": list_slides(), "renderer": renderer_kind()}


@router.get("/slides/{n}")
def slide_route(n: int) -> dict:
    s = get_slide(n)
    if s is None:
        raise HTTPException(404, "No such slide")
    return s


@router.get("/slides/{n}/image")
def image_route(n: int):
    path = slides_dir() / f"{n}.png"
    if renderer_kind() == "none" or not path.is_file():
        return JSONResponse({"detail": "Slide image not available", "renderer": renderer_kind()}, status_code=404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/render")
def render_route() -> dict:
    return {"started": start_render_async(), "progress": _progress}


@router.get("/render/progress")
def progress_route() -> dict:
    return {**_progress, "renderer": renderer_kind(), "slidesWithoutCurrentImage": len(_stale_slides()) if renderer_kind() != "none" else 0}


@router.post("/save")
def save_route(body: SaveIn) -> dict:
    return save_edits(body.edits, body.author)


@router.post("/reset")
def reset_route() -> dict:
    return reset_to_original()


@router.get("/edits")
def edits_route() -> dict:
    return {"edits": store.read_json(_EDITS_KEY, [])}


@router.get("/download")
def download_route(version: str = "working"):
    ensure_working_copy()
    path = deck_dir() / "approved.pptx" if version == "approved" else working_path()
    if not path.is_file():
        raise HTTPException(404, "Deck file not available")
    return FileResponse(path, filename=download_name(),
                        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


class ApproveIn(BaseModel):
    by: str
    note: str | None = None


class WithdrawIn(BaseModel):
    by: str | None = None
    note: str | None = None


@router.get("/approval")
def approval_route() -> dict:
    return approval_status()


@router.post("/approval/approve")
def approve_route(body: ApproveIn) -> dict:
    return approve(body.by, body.note)


@router.post("/approval/withdraw")
def withdraw_route(body: WithdrawIn) -> dict:
    return withdraw(body.by, body.note)
