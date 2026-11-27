# Deck API (`/api/deck`)

Module: `backend/pem/domains/deck.py`. The Sustainability Deck (`common/Sustainability Deck*.pptx`, 126 slides, 16:9, 9144000 x 5143500 EMU) can be viewed, edited in place, saved, downloaded and approved.

The source file is never modified. First use copies it to `<data_dir>/deck/working.pptx`; all edits go to that copy. If the source file is newer than the working copy and nothing has been edited, the copy is refreshed. Plain-Python helpers other modules can call: `ensure_working_copy()`, `get_slide(n)`, `list_slides()`, `deck_info()`, `render_slides(numbers)`, `save_edits(edits, author)`, `approve(by, note)`, `withdraw(by, note)`, `reset_to_original()`, `renderer_kind()`.

## Slide images

Slides are rendered to PNG (1600 px wide) through PowerPoint automation (pywin32, one render at a time, PowerPoint is quit afterwards if this app started it). Images are cached in `<data_dir>/deck/slides/<n>.png` and keyed by a hash of each slide's XML, so a save re-renders only the changed slides. Measured here: about 16 s for the first call (PowerPoint start-up) and about 38 s for all 122 slides; one edited slide takes a few seconds. Rendering is started in a background thread by `GET /api/deck` (first load) and after every save; no GET waits for it. When PowerPoint is not installed `renderer` is `"none"`, `imageUrl` is `null` and the image route returns 404: show the text layout from `textBoxes`.

## Endpoints

### GET /api/deck
Deck info.
```json
{"available": true, "sourceFile": "Sustainability Deck- till Mar'26 R1 - Compressed (1).pptx",
 "downloadName": "Sustainability Deck- till Mar'26 R1 - draft.pptx", "slideCount": 126,
 "size": {"widthEmu": 9144000, "heightEmu": 5143500, "widthInches": 10.0, "heightInches": 5.625, "aspect": 1.7778},
 "renderer": "powerpoint",
 "render": {"state": "idle", "total": 122, "done": 122, "error": null, "startedAt": "...", "finishedAt": "...",
            "lastSeconds": 38.3, "slidesWithoutCurrentImage": 0, "imagesReady": 126},
 "approval": {"status": "draft", "approvedBy": null, "approvedAt": null, "note": null},
 "hasEditsFromOriginal": false, "editCount": 0, "notes": []}
```
`render.state` is `idle`, `rendering` or `error` (with `error` text). Poll this (or `/render/progress`) to show progress. If the source is missing: `available:false`, `slideCount:0`, and a `notes` entry.

### GET /api/deck/slides
`{"slides": [{"number": 1, "title": "...", "layout": "OBJECT", "textBoxCount": 3, "imageUrl": "/api/deck/slides/1/image?v=<hash>" | null}], "renderer": "powerpoint"}`. `imageUrl` is null until that slide has a current picture.

### GET /api/deck/slides/{n}
One slide's editable model (404 for a bad number).
```json
{"number": 4, "title": "SUSTAINABILITY VISION AND MISSION", "layout": "TITLE_ONLY", "hash": "ab12...",
 "size": {"aspect": 1.7778, "widthInches": 10.0, "heightInches": 5.625},
 "imageUrl": "/api/deck/slides/4/image?v=ab12...", "renderer": "powerpoint",
 "textBoxes": [{"id": "201/202", "kind": "text", "name": "Google Shape;202;p6", "inGroup": true, "rotation": 0.0,
   "left": 0.66377, "top": 0.32651, "width": 0.26679, "height": 0.17569,
   "paragraphs": [{"text": "2024: Action Plans for Net Positive Impact",
                   "runs": [{"text": "2024: Action Plans for Net Positive Impact"}], "level": 0, "bullet": null}]}]}
```
- `id`: stable. Plain shape: its shape id (`"200"`). Shape inside a group: `"<group id>/<shape id>"`. Table cell: `"<table id>/r<row>c<col>"` (merged-away cells omitted).
- `left/top/width/height`: fractions of the slide (group transforms applied; text inside groups is placed correctly). `rotation` (degrees) is reported but NOT applied to the box; for a 90/270 degree box the stored width/height are the unrotated frame.
- `runs`: text pieces in order; `{"text": "\n", "break": true}` is a line break, `"field": true` marks a slide-number/date field.
- `bullet`: explicit bullet character the paragraph sets itself (e.g. `"✔"` tick); null otherwise (inherited bullets are not reported).
- Only shapes with text appear (empty placeholders, pictures and charts do not). Charts, pictures and embedded Excel objects are not editable.
- `title`: title placeholder text, else the first text near the top (max 140 chars); can be empty.

### GET /api/deck/slides/{n}/image
PNG, immutable cache headers (use the `?v=` from `imageUrl`). 404 `{"detail": "Slide image not available", "renderer": ...}` when not rendered or renderer is `none`.

### POST /api/deck/save
```json
{"author": "Name (optional)", "edits": [{"slide": 4, "shapeId": "201/202", "paragraphs": ["new text"]}]}
```
Each paragraph is a string, `{"text": "..."}` or `{"runs": [{"text": "..."}]}`. Rules: unchanged shapes are ignored; if the number of runs sent equals the paragraph's current number of runs (breaks included) each run keeps its own formatting; otherwise all text goes into the paragraph's first run (its font, size, colour kept) and `\n` becomes a line break; extra paragraphs are clones of the last paragraph; fewer paragraphs delete the trailing ones. Response: `{"ok": true, "changedSlides": [4], "changed": [{"slide":4,"shapeId":"201/202","before":[...],"after":[...]}], "errors": [], "rendering": true}`. Unknown slides/shapes are listed in `errors` (`ok:false`), never raised. A save of an approved deck returns it to draft (recorded in history). The affected slides are re-rendered in the background; poll `/api/deck` and reload `imageUrl` (its `?v=` changes when the picture is current).

### POST /api/deck/reset
Recopies the source deck, clears the edit log, approval returns to draft (history entry "Deck reset to the original file"), re-renders. `{"ok": true}`.

### GET /api/deck/edits
`{"edits": [{"at": "2026-11-27T07:40:00+00:00", "by": "Name|null", "slide": 4, "shapeId": "...", "before": [...], "after": [...]}]}` (last 2000, stored in `<data_dir>/deck/edit_log.json`).

### GET /api/deck/download?version=working|approved
The .pptx. File name: `Sustainability Deck- till Mar'26 R1 - draft.pptx` (or `- approved.pptx` when the deck is approved). `version=approved` returns the snapshot taken at approval time (404 if none). The name is URL-encoded in `Content-Disposition`.

### POST /api/deck/render, GET /api/deck/render/progress
Start a render of out-of-date slides; progress is `{state,total,done,error,startedAt,finishedAt,lastSeconds,renderer,slidesWithoutCurrentImage}`.

### Approval
- `GET /api/deck/approval` -> `{"status": "draft|approved", "approvedBy", "approvedAt", "note", "history": [{"at","event","by","note"}]}`. Events: `approved`, `returned_to_draft` (after a save/reset; includes `was`), `approval_withdrawn`.
- `POST /api/deck/approval/approve` `{"by": "name", "note": "optional"}`; copies the working deck to `approved.pptx`.
- `POST /api/deck/approval/withdraw` `{"by": "...", "note": "..."}`.

## Where data is stored
`<data_dir>/deck/working.pptx`, `slides/*.png`, `approved.pptx`; JSON via `store`: `deck/state`, `deck/edit_log`, `deck/approval`, `deck/render_index`.

## Known limits
- Rotated text boxes are overlaid unrotated. Text in WordArt/charts/OLE objects is not editable. Slide 10 is a picture (its text is not in the file).
- The deck contains the character U+FFFD (a broken dash) in a few titles ("Material Efficiency and Circularity � Waste Reduction"); it is returned as is.
- Saving through python-pptx re-serialises the file; rendering via PowerPoint requires PowerPoint on the same machine.
