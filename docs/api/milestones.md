# Milestones API (`/api/milestones`)

Module: `backend/pem/domains/milestones.py`. Slides 1-14 of the Sustainability Deck as structured data, read from the deck's working copy (so approved edits appear). Text and numbers are quoted from the slides; nothing is written by hand except the stage labels. Callable: `milestone_slides()`, `milestone_slide(n)`.

## Stages (the grouping, worked out from content)
| stage id | slides | basis |
|---|---|---|
| `intro` | 1-3 | cover, agenda, TASL vision, mission, objectives |
| `aalingana` | 4-7 | Aalingana milestones, challenges "in achieving Project Aalingana Targets", "Milestones as defined by the Tata Group" with current status, goals vs target vs current |
| `tasl` | 8-14 | TASL's Scope 1/2/3 framing, TADL KPI blue-print (slides 9-11), graph list (12), solar plan (13-14) |

There is no separate "Tata" stage in these slides: Tata is the owner of Project Aalingana and the branding. The order Aalingana -> TASL is the order of the slides. The response lists the stages with `evidence`.

## Endpoints
- `GET /api/milestones` -> `{"available", "stages", "sequence", "slides", "imageOnlySlides": [10], "partialSlides": [8,13,14], "renderer", "notes"}`.
- `GET /api/milestones/sequence` -> `{"stages", "sequence": [{"slide","stage","title","kind","structure"}], "notes"}`, in presentation order 1..14.
- `GET /api/milestones/slides/{n}` -> one slide (404 outside 1-14).
If the deck is missing: `available:false`, empty `slides`, a `notes` entry.

## Slide object
`number, stage, stageLabel, title, kind, structure, headings[], bullets[], tables[], targets[], statuses[], charts[], footnotes[], notes[], imageUrl` (`/api/deck/slides/<n>/image`, show it for any slide, and instead of content when `structure` is `image`).
- `structure`: `full` (all text structured), `partial` (some content only in a picture/diagram: show the image too), `image` (picture only).
- `bullets`: `[{"heading": str|null, "items": [{"text", "marker"?: "tick"}]}]`. `marker:"tick"` = the deck gives that line a tick-mark bullet (slides 9, 11: "Proper Separation of Scrap (Hazardous, E-Waste, Battery)" and "Effective Selling to Recycler"). The slide does not say what a tick means.
- `targets`: `{"year", "label", "text", "pillar"?}`; on slide 7 each also has `baselineType`, `target`, `current` = `{"text","measure","value","unit"}` (value null when the cell has no number).
- `statuses` (slide 6): `{"pillar","target","marker": "tick"|"WIP"|null,"source","details":[...]}`.
- `charts`: `{"shapeId","type","categories","series":[{"name","values"}]}` straight from the chart's stored data.

## Slides (kind, source)
1 cover (title text "Sustainability Reporting"). 2 agenda (two text boxes; right box split into sections at blank lines). 3 vision text, "Objectives:" with four lines. 4 `pillars`: mission text; 10 year targets under 3 pillars (Driving Net-Zero 2027/2030/2045; Pioneering Circular Economies 2025/2030/2030/2040; Preserving Nature & Biodiversity 2024/2025/2030), assigned to a pillar by column position. 5 `challenges`: three target areas with 4/4/3 challenges. 6 `pillar_status`: one target per pillar with current status text; ticks (pictures) on Net-Zero and Circular, "WIP" on Biodiversity. 7 `goals_table`: 7 goals with Target and "Current (Upto Mar-2026)" values (e.g. Solar Units target 2.25 Million Units, current 8.52 Million Units; Water Recycled 39.5 KL vs 67 KL; Emissions 19 vs 59 Million KG CO2), baseline type "From 2020 Baseline" (first 3) / "Future Year as Baseline" (last 4), footnote about absolute reduction. 8 `diagram` (partial): scope lists; the SCOPE 1/2/3 labels are in a picture, so which list belongs to which scope was read from the rendered image. 9 and 11 `kpi_columns`: four KPI columns. 10 `picture` (image only). 12 `list`: Energy and Water graph lists. 13 `chart_infographic` (partial): table of solar panel availability by plant, energy requirement 59.6M/98.4M/146.7M (FY 24/26/29), deltas, investment text; stacked bars are drawn shapes. 14 `chart` (partial): FY 18-19..FY 27-28 series from the chart ("Total Units Generated x1,00,000 KWh", Renewable Energy 2 MW / 1 MW / No Solar fractions).

## Gaps and oddities
- Slide 10 is a picture of the KPI columns (no text in the file); it shows arrow bullets and green ticks next to the same two scrap items as slides 9/11.
- Slide 9 and 10 are titled "By 2030", slide 11 "By 2026"; the KPI text is the same.
- Slide 7: "Current" cells for waste to landfill, water positivity and net zero are descriptions with no number (value null). Current values exceed or differ from targets as written (e.g. 8.52 vs 2.25); they are quoted unchanged.
- Slide 6 tick marks are pictures; their meaning is not written on the slide.
- Slide 13's chart cache has a single unlabeled series; FY labels are separate text boxes. Slide 14 has an embedded Excel object that is not read.
- Extraction uses fixed shape ids per slide; if a user deletes such a shape, that slide falls back to its plain text with a note.
