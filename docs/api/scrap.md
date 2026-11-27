# Scrap API (`/api/scrap`)

Module: `backend/pem/domains/scrap.py`. Every response carries a `notes` list (gaps, oddities). Missing files give a
valid empty payload plus a note, never an error.

## What the sources actually contain

| Source | Holds | Used for |
|---|---|---|
| "Sustainability (Only to Enter)" workbook, sheet **Scrap generation details** | Monthly **hazardous waste items and e-waste per plant**, FY21-22 to FY26-27 (FY26-27 partly filled). The only quantities in the source. | quantities, totals, overview. Every row is `"source": "entry"` (the calculation workbook has no scrap quantities). |
| "Sustainability Calculation" workbook, sheets **Waste Data Sheet - Alt (2)**, **Waste Data Sheet - Alt 1** (hidden), **Summary Sheet** | Empty templates / zeros (column headers for categories such as Plastic, E-Waste, Battery, Metal, Glass, Wood, Landfill, Incineration, Recycled ... but no values) | nothing (stated in notes) |
| same workbook, **Waste Sheet Disposal Method** | plant x waste name -> disposal method text (no quantities) | `/disposal` |
| same workbook, **Target Sheet** | `Landfill Waste - 2030`, target level 0, reduction 0, for each FY19-20 to FY25-26 (the sheet also holds carbon targets; those are not returned here) | `/targets` |
| `scrap generation/Digitalization of Scrap Management- Mendix App.docx` | responsibility-matrix tables, flow-chart text boxes, 6 embedded images | `/initiative` |
| `scrap generation/BRD EHS- Scrap Management & Water Balance Solution (2) (1).pdf` | 14 scanned pages: **no text layer, no OCR installed**. Pages 2-5 were read by eye and transcribed; pages 1 and 6-14 are not transcribed. | `/initiative`, `/documents/brd.pdf` |

Categories are the sheet's own: **Hazardous** and **E-Waste** (the e-waste rows follow each plant's hazardous total in the sheet).
There is **no non-hazardous (metal, plastic, wood ...) quantity anywhere in the source**; the page must not show one.

## Units and totals

* Each row keeps the sheet's unit: `KG`, `T`, `L`, `Liters`. TLMAL, TASL-BLR, TASL-MCA, TCOE rows are mostly KG; TASL-HYD, TBAL,
  TASL-NGP rows are in **T** (tonnes). Waste oil / coolant rows are litres.
* Totals put weights on **kg** (`T x 1000`, stated formula). Litres are summed separately (`volumeL`) and never added to kg.
* A plant-year whose every item is 0 or blank is returned as `null` (nothing entered), not 0.
* The sheet's own "Plant Total ..." rows are labelled `T` although their values add KG, T and litres rows, and "Total TADL
  Hazardous Waste disposal" (FY25-26 = 164411.88) mixes them too. They are not used; `/totals` returns them as `sheetTotals`
  for comparison only.
* FY label format `FY25-26`; months `YYYY-MM`. The sheet's month headers are dates whose *day* is the two-digit year
  (`2025-01-26` = January 2026); the module reads them that way and assigns months by position inside each 12-column year block.
* Plant ids from `plants.PLANT_IDS`; clusters: DP = TASL-NGP, TASL-BLR, TSAL, TCOE; ASSY = TLMAL, TASL-HYD, TBAL, TASL-MCA.

## `GET /api/scrap/overview`

Compact payload for the home page: latest financial year with data, company / cluster / plant totals, yearly series, landfill target.

```json
{
  "latestFy": "FY26-27", "latestFyMonthsReported": 7, "unit": {"weight": "kg", "volume": "L"},
  "company": {"company": "TADL", "byCategory": [{"company": "TADL", "fy": "FY26-27", "category": "Hazardous",
              "weightKg": 72456.48, "volumeL": 200.0, "plantsWithData": ["TASL-BLR", "TASL-HYD", "TASL-MCA", "TLMAL"]}]},
  "clusters": [{"cluster": "DP", "fy": "FY26-27", "category": "Hazardous", "weightKg": 46000.0, "volumeL": null, "plantsWithData": ["TASL-BLR"]}],
  "plants": [{"plant": "TASL-BLR", "plantName": "TASL-Bangalore", "weightKg": 46000.0, "volumeL": 0.0}],
  "yearly": [{"fy": "FY21-22", "weightKg": 12041.0, "volumeL": 0.0}],
  "target": null,
  "notes": ["Hazardous and e-waste only: the sources hold no non-hazardous scrap quantities."]
}
```

* `latestFy` = latest FY with a non-zero value; that year may be partial (`latestFyMonthsReported` = most months any plant reported).
* `target` = the Target Sheet row for `latestFy` if the sheet has one (it stops at FY25-26, so it is `null` for FY26-27).
* `yearly` is the company total over all plants with data; years differ in how many plants reported (see `/totals` `plantsWithData`):
  FY21-22 to FY24-25 contain **TLMAL only**.

## `GET /api/scrap/quantities?plant=&fy=&category=`

Item-level data (plant x waste type), with monthly values.  Parameters: `plant` (id or any spelling), `fy` (`FY25-26`), `category` (`Hazardous` | `E-Waste`).

```json
{"unit": "as stated per item (see `unit`)",
 "financialYears": ["FY21-22", "FY22-23", "FY23-24", "FY24-25", "FY25-26", "FY26-27"],
 "items": [{"plant": "TLMAL", "plantName": "TLMAL-Hyd", "category": "Hazardous", "wasteType": "Oil socked Cotton Waste",
            "unit": "KG", "source": "entry",
            "fy": {"FY25-26": {"total": 8845.0, "basis": "months", "monthsReported": 12, "statedTotal": 8845.0, "totalKg": 8845.0,
                   "months": {"2025-04": 585.0, "2025-05": 563.0, "...": 0}}}}],
 "notes": ["..."]}
```

* Source: entry workbook -> *Scrap generation details* -> item rows under each plant block (column B plant, C waste type, D unit); month columns in each year block; the FY total column right of the 12 months.
* `total` = sum of the months present, else the sheet's stated total (`basis` = `months` | `stated-total`; some FY25-26 rows of TCOE have a total but no months). In this source the sum equals the stated total wherever both exist.
* `totalKg` is `null` for litre rows. `wasteType` is the sheet's wording (spelling errors kept, e.g. "Oil socked").
* Items whose every value is 0/blank (placeholder rows "Hazardous-1", "E Waste-1" ...) are omitted.

## `GET /api/scrap/totals?fy=`

```json
{"unit": {"weight": "kg", "volume": "L"},
 "plantYear": [{"plant": "TLMAL", "plantName": "TLMAL-Hyd", "fy": "FY25-26", "category": "Hazardous", "weightKg": 12461.0, "volumeL": 0.0, "monthsReported": 12, "items": 5}],
 "plantMonth": [{"plant": "TASL-BLR", "plantName": "TASL-Bangalore", "month": "2026-04", "fy": "FY26-27", "category": "Hazardous", "weightKg": 9890.0, "volumeL": null}],
 "clusterYear": [{"cluster": "ASSY", "fy": "FY26-27", "category": "Hazardous", "weightKg": 26456.48, "volumeL": 200.0, "plantsWithData": ["TASL-HYD", "TASL-MCA", "TLMAL"]}],
 "companyYear": [{"company": "TADL", "fy": "FY25-26", "category": "Hazardous", "weightKg": 588528.95, "volumeL": 36575.0, "plantsWithData": ["..."]}],
 "clusters": {"DP": ["TASL-NGP", "TASL-BLR", "TSAL", "TCOE"], "ASSY": ["TLMAL", "TASL-HYD", "TBAL", "TASL-MCA"]},
 "sheetTotals": [{"plant": "TADL", "label": "Total TADLHazardous Waste disposal", "unitLabel": "T", "fy": "FY25-26", "value": 164411.883}],
 "notes": ["..."]}
```

Checked values: TLMAL FY25-26 hazardous 12461 kg (sheet row 10); TASL-NGP FY25-26 429.12 T = 429120 kg; TASL-BLR Apr-26 9890 kg; TASL-BLR FY25-26 e-waste 4710 kg (2 months only).
`weightKg`/`volumeL` are `null` for plant-years with nothing entered. A cluster/company row only sums plants that have data; check `plantsWithData` before comparing years.

## `GET /api/scrap/disposal?plant=`

Plant x waste -> disposal method as written in *Waste Sheet Disposal Method* (calculation workbook).
`rows`: `{plant, plantLabel, waste, wasteType ("Hazardous Waste" | "Non Hazardous"), method|null}`; `byMethod`: count of waste lines per method
("Sold", "Re-Processing", "Recycled", "Compost Pit", "Not Sure", "Not Segregated", "Recycled/Reused", "Incinerated", "Manure", "Landfill with Treatment", "(not stated)").
**No quantities by disposal method exist in the source**; the note says so. Ten lines have no method (null); "Not Sure" / "Not Segregated" are the sheet's own words.

## `GET /api/scrap/targets`

```json
{"targets": [{"category": "Landfill Waste - 2030", "targetYear": 2030, "financialYear": "FY25-26", "level": 0.0,
              "reductionPercentage": 0.0, "unit": null, "source": {"workbook": "Sustainability Calculation - ....xlsx", "sheet": "Target Sheet"}}],
 "baseline": null, "notes": ["..."]}
```

Only the sheet's real waste target (landfill, level 0 by 2030; the same row repeats for each FY19-20..FY25-26). The sheet states no unit, so `unit` is `null`. `baseline` is `null`: the *Summary Sheet* ("KPIs Project Aalingana": hazardous solid/liquid, non-hazardous, landfill) holds only zeros. There are no stated targets for hazardous or e-waste, and the source has no landfill quantity to plot against the target.

## `GET /api/scrap/initiative`

```json
{"brd": {"file": "BRD EHS- ... .pdf", "pages": 14, "pdfUrl": "/api/scrap/documents/brd.pdf", "textLayer": false,
         "pagesTranscribed": [2, 3, 4, 5],
         "transcription": {"method": "Read by eye from the rendered scanned pages ...",
            "objectives": [{"text": "The objective of this application is to establish a digitized system for Scrap Management (Hazardous, Non-Hazardous, E-Waste, Battery, General Waste and Bio-Waste) within the organization to facilitate improved monitoring and tracking functions.", "page": 2, "section": "1. Objective"}],
            "currentProcess": {"statement": "Currently, the entire process is managed manually and is decentralized.", "page": 2, "wasteStreams": ["Metal Scrap", "..."], "steps": [{"process": "...", "responsible": {"Metal Scrap": "..."}}]},
            "toBeProcess": {"statement": "...", "page": 2, "flowSteps": ["Source", "Input Mode of Scrap", "..."]},
            "responsibilityMatrix": {"page": "3-4", "columns": ["Sr. No.", "Process", "..."], "rows": [{"no": 1, "process": "Generation and Movement of Scrap", "fromTo": "Source - Scrap Yard", "executingDept": {"Metal Scrap": "End User"}, "authorization": "Individual HOD/ Functional Head", "acknowledgement": "Admin/ Scrap Yard In-Charge", "page": 3}]},
            "masters": {"page": 5, "items": ["PCB Master, PCB certification, expiry details", "..."]},
            "notStated": ["Targets or quantified benefits: none are stated on the pages that were read (2-5)."]}},
 "mendix": {"file": "Digitalization of Scrap Management- Mendix App.docx",
            "sections": [{"title": "PCB Details/ PCB Master – To Input (in the Application)", "text": ["Responsibility Matrix:", "Template:", "..."],
                          "tables": [{"rows": [["Overall Responsibility", "Input CTE/CFO details", "Logics to be Built", "Flow/ Publication of Alert- PCB Input details"], ["EHS", "EHS", "IT- BA", "EHS, Legal, PEM, SCM, Admin"]], "firstRowIsHeader": false}],
                          "flowCharts": [{"boxes": ["Start Input PCB Certificate Details (Refer Temp : 1) ...", "..."], "note": "..."}], "imageIds": [1, 2]}],
            "images": [{"id": 1, "url": "/api/scrap/documents/mendix/images/1", "file": "media/image1.png"}]},
 "targetsOrBenefits": [], "notes": ["..."]}
```

* **BRD**: scanned images. `pypdf` returns no text and no OCR is installed, so the structured content was transcribed by eye from pages 2-5 only (objective p.2; current process table p.2; to-be statement p.2; workflow chart and responsibility matrix rows 1-4 p.3, rows 5-17 p.4; masters and sample PCB master p.5). Wording is as printed (including its typos "Waster/Scrap type", "Barells"). Pages 6-14 are not transcribed: show the PDF (`pdfUrl`, `application/pdf`). Re-check against the scan before quoting in a report.
* **Mendix document**: the only text outside images is the responsibility-matrix tables and the text boxes inside the flow charts. `sections` follow the document headings (Heading 1); `tables` are its tables (cell text, `/` replaces a line break); `flowCharts[].boxes` are the text boxes of one drawing in document order — arrows are not text, so the order is not the flow order (`note`). The templates are images: `images[].url` serves each (PNG) and `imageIds` in a section say which images sit under it.
* **Stated targets / benefits**: none appear in either document (the Mendix text and the BRD pages read). `targetsOrBenefits` is an empty list; do not invent any.
* The quantified thresholds that do appear are workflow rules inside the flow charts: "Check for Validation (Date) before > 90 days", "Check for Validation (Qty.) forecast for <=75%", "PCB Authorized (Date) before >30 days", disposal "Within 180 days" (BRD).

## `GET /api/scrap/documents/brd.pdf`

The BRD PDF itself (`application/pdf`); 404 if the file is not in the source folder.

## `GET /api/scrap/documents/mendix/images/{n}`

Embedded image `n` (1-based, ids as in `/initiative`) of the Mendix document, as `image/png`; 404 for an unknown id.

## Functions for other modules

`scrap.overview()`, `scrap.totals(fy=None)`, `scrap.quantities(plant, fy, category)`, `scrap.disposal_methods(plant)`, `scrap.targets()`, `scrap.initiative()`; helpers `parse_month_header()`, `to_kg()`.

## Known gaps and oddities

* Quantities exist only for hazardous waste and e-waste; no non-hazardous categories, no disposal-method quantities, no baseline.
* FY21-22 to FY24-25 hold TLMAL only (other plants' cells are 0 placeholders, shown as `null` totals); FY25-26 holds TLMAL, TASL-HYD, TBAL, TASL-NGP, TASL-BLR, TASL-MCA, TCOE; TSAL has no scrap data; FY26-27 is Apr-Oct 2026 for some plants and nothing for TBAL, TASL-NGP, TCOE.
* Partial years: TASL-NGP FY25-26 has 10 months, TASL-BLR e-waste 2 months, TBAL and TCOE 11 months. Their FY totals are sums of the months present.
* Mixed units within a plant (KG, T, L) and plant-total rows labelled "T" with non-tonne values; the sheet's company total adds kg, T and litres.
* TCOE "Machine coolant oil" is 35,820 litres in FY25-26 (as entered); TASL-BLR plastic waste 68,570 kg (FY25-26) dominates that plant — values are as typed in the sheet.
* The same sheet also carries power-consumption blocks to the right (column CK onward); they are ignored here.
