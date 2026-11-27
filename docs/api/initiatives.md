# Initiatives API (`/api/initiatives`)

Module: `backend/pem/domains/initiatives.py`. Real data only: a blank in the source is `null`, and every
status keeps its original wording next to the normalised one. The first call parses the workbooks (the
calculation workbook is slow to open, about a minute the first time); later calls use a cache that is rebuilt
when a source file changes.

## Sources

| Key in `id` | File / sheet | What it holds |
|---|---|---|
| `esi-r<row>` | `initiatives/Energy Saving Initiatives Data.xlsx`, sheet "Energy Saving Initiatives Data" | 21 initiatives (rows 3-23), one status per plant, kWh and Cr savings per plant |
| `fy27-r<row>` | `initiatives/FY 2026-27 Energy Saving Initiatives Data.xlsx`, same sheet name | The same 21 initiatives re-issued as the FY 2026-27 plan, now with 8 plant blocks plus investment / expected saving / ROI / planned and actual completion columns (all blank in the file) |
| `calc-power-r<row>` | "Sustainability Calculation" workbook, sheet "Energy(Power) Initiatives" | Per plant, one row per initiative, yearly totals (kWh) and CO2 per financial year FY18-19 to FY24-25 |
| `calc-fuel-r<row>` | same workbook, "Energy(Fuel) Initiatives" | same layout, fuel |
| `calc-gas-r<row>` | same workbook, "Energy(Gas) Initiatives" | same layout, gas (no initiative is listed in the file) |
| `calc-water-r<row>` | same workbook, "Water conservation Initiatives" | same layout, water (no CO2 column) |

`<row>` is the 1-based Excel row, so `esi-r18` is row 18 of the first file.

Files are found by words in the name (`energy saving initiatives`, `2026-27`), so renamed copies still resolve.

### Calculation workbook vs "Only to Enter" workbook

The four sheets also exist (hidden) in the "Sustainability (Only to Enter)" workbook. They were compared cell by cell.
The calculation workbook is the more complete one and is the primary source:

* Fuel, Gas and Water sheets: no numeric difference.
* Power sheet: calc has 32 monthly values the entry sheet lacks (Bangalore rows 138-141, 8 months each, for example
  row 138 = 1500 kWh/month) and entry shows 0 where calc has 6083 on the Bangalore "Plant Total" row 152; nowhere does
  entry hold a number that calc lacks.
* Where calc says "NA" the entry sheet stores 0.0; where it prints a name, "na" is replaced by "0"
  ("Elimi0ting", "Ma0gement"), so entry text is never used.

The entry sheet is only used to fill a blank cell with a non-zero number; no cell needed it
(`source.usedEntryFallback` is `false` everywhere).

## Fixed vocabularies

* `status` (per plant): `implemented`, `in_progress`, `open`, `under_study`, `horizontally_deployed`,
  `not_feasible`, `not_applicable`, `on_hold`; `null` when the source has no status or the wording is unknown.
* Mapping (case-insensitive): Implemented, Closed -> `implemented`; WIP -> `in_progress`; Open -> `open`;
  "Under Faesible Study" / "Under Feasible" / "Under Feasible Study" -> `under_study`; "Horizontally deployed" ->
  `horizontally_deployed`; "Not Feasible" -> `not_feasible`; NA / Not Applicable -> `not_applicable`; On Hold -> `on_hold`.
  "Closed" occurs only in the FY 2026-27 file's "Initiated plant status" column and is read as finished.
  An unrecognised wording gives `status: null` and a `notes` entry; none occur today.
* `domain`: `lighting`, `hvac`, `compressed_air`, `water`, `renewable`, `drives_motors`, `heating_fuel`,
  `energy_management`, `other`. From the "Equipment / Area of initiative" column where it is filled
  (Lighting, HVAC, Water Management, Energy Management); otherwise by keyword on the description
  (`domainSource` says which). Keyword domains are a convenience classification, not a source field.
* `phase`: `present` or `planned`, see the rule below.

## The present / planned rule

* **Existing-initiatives file (`esi-*`)**: `present` when at least one plant is `implemented`, `in_progress` or
  `horizontally_deployed`; otherwise `planned`. Today all 21 are `present`.
* **FY 2026-27 file (`fy27-*`)**: always `planned`, because the file is the FY 2026-27 plan. It repeats the same
  initiatives, so use `plantStatuses[].phase` (`present` for implemented / in-progress / horizontally-deployed at that
  plant, `planned` for open / under study) when the page needs to know what is still to be done at each plant.
* **Calculation workbook rows (`calc-*`)**: rows above a "Future Plan for Improvements" marker in a plant block are
  `present` (they sit in the section for initiatives already done); rows below the marker are `planned`
  (status `open`, `statusBasis: "section"`). A row above the marker with a yearly saving above zero gets status
  `implemented` (`statusBasis: "savings recorded in a yearly total"`); a row with no savings stays `present` with
  `status: null` (`statusKnown: false`), because the sheet does not say whether it was ever started.
  There are 127 `present` and 5 `planned` rows.

## Linking: `groupKey`

Records that describe the same initiative share a `groupKey` and stay separate records; `groupSize` is how many
records share it.

* `esi-r<n>` and `fy27-r<n>` always link (same Sl. No. and text). Items 3-7 have identical text but different
  Sl. No., so they are five groups.
* Calculation rows link when plant and normalised text match: TLMAL "Waste heat recovery from the air
  compressor ..." in the Power sheet (row 27) and the Fuel sheet (row 9), and Bangalore "AC instalation for the CCA-4
  Thawing room" twice in the Power sheet (rows 138 and 144).
* Records from the initiatives files are **not** linked to calculation-sheet rows: no description matches closely
  enough (best fuzzy matches are different initiatives), and linking them would be a guess.

## Plant mapping

Plant labels go through `canonical_plant()`: "TASL HYD" -> `TASL-HYD`, "TLMAL HYD" / "TLMAL-Hyd" -> `TLMAL`,
"TBAL HYD" -> `TBAL`, "TASL MCA" -> `TASL-MCA`, "TCOE HYD" -> `TCOE`, "TSAL HYD" / "TSAL-Hyd" -> `TSAL`,
"TASL NGP" / "TASL -Nagpur" -> `TASL-NGP`, "TASL BLR" / "TASL- Bangalore" -> `TASL-BLR`.
"Corp.PEM" is not a plant: `initiationPlant.plants` is `[]` and the label is kept. "All Plants" expands to all eight.
No site codes (N01, B01, H01 ...) appear in these files.

**File 1 column alignment.** In `Energy Saving Initiatives Data.xlsx` the "Status (...)" headers are one plant out of
step with the data: there is one unlabelled "Status" column (col O) and the label "Status (TLMAL HYD)" (col R) sits
in front of the TBAL savings block. The module takes the plant from the savings header that directly follows each
status column, i.e. the plants are TASL-HYD, TLMAL, TBAL, TASL-MCA, TCOE, TASL-NGP, TASL-BLR (columns L, O, R, U, X,
AA, AD). TSAL has no block in this file. Comparing with the FY 2026-27 file, this alignment agrees on 123 of 130
status cells; reading column X as TSAL instead of TCOE would agree on 129. The two readings differ only for TCOE; treat
the TCOE status in `esi-*` records as uncertain. The FY 2026-27 file has proper block labels in row 1 and is unambiguous.

## `GET /api/initiatives`

Parameters (all optional): `plant` (any spelling, e.g. `TLMAL` or `TLMAL-Hyd`), `status` (one of the fixed set;
matches if any plant status, or the given plant's status, equals it), `domain`, `phase` (`present` | `planned`;
anything else is a 422).

```json
{
  "count": 1,
  "statuses": ["implemented", "in_progress", "open", "under_study", "horizontally_deployed", "not_feasible", "not_applicable", "on_hold"],
  "sources": [{"role": "existing initiatives", "file": "Energy Saving Initiatives Data.xlsx"}, "..."],
  "notes": [],
  "items": [ { "...one initiative, see below..." } ]
}
```

An unknown plant gives `count: 0` and a note. A missing file or sheet gives fewer items and a `notes` entry.

## `GET /api/initiatives/{id}`

404 when the id does not exist. Example (trimmed), `esi-r18`, Sl. No. 16, sheet row 18:

```json
{
  "id": "esi-r18",
  "groupKey": "g-...",
  "groupSize": 2,
  "source": {"kind": "initiatives-file", "workbook": "Energy Saving Initiatives Data.xlsx",
             "sheet": "Energy Saving Initiatives Data", "row": 18, "slNo": 16},
  "phase": "present",
  "plants": ["TASL-HYD", "TLMAL", "TBAL", "TASL-MCA", "TCOE", "TASL-NGP"],
  "initiationPlant": {"label": "TLMAL-HYD", "plants": ["TLMAL"]},
  "domain": "energy_management", "domainSource": "equipment column",
  "equipmentArea": "Energy Management",
  "category": "Mechanical Correction",
  "type": "Power Conservation", "energyType": "power",
  "description": "Cogged Belt for reducing the Blower RPM",
  "initiationDate": "2025-02-11", "initiationDateOriginal": "11.02.2025", "yearOfActivity": 2025,
  "plannedCompletion": null, "actualCompletion": null,
  "initiatedBy": "Harsha", "responsible": "TLMAL", "priority": null,
  "investmentRs": null, "expectedSavingPerYear": null, "roiYears": null,
  "initiatorStatus": null,
  "plantStatuses": [
    {"plant": "TLMAL", "plantLabel": "TLMAL HYD", "status": "implemented", "statusOriginal": "Implemented",
     "energySavingKwh": 36360.0, "energySavingKwhRaw": "36360.0",
     "energyCostSavingCr": 0.03, "energyCostSavingCrRaw": "0.03", "phase": "present"},
    {"plant": "TBAL", "plantLabel": "TBAL HYD", "status": "in_progress", "statusOriginal": "WIP",
     "energySavingKwh": null, "energySavingKwhRaw": null, "energyCostSavingCr": null,
     "energyCostSavingCrRaw": null, "phase": "present"}
  ],
  "achieved": {"energyKwh": 36360.0, "energyKwhUnit": "kWh", "energyCostCr": 0.03,
               "energyCostUnit": "Cr (as headed in the source)", "co2Kg": null},
  "expected": {"energyKwh": null, "energyCostCr": null, "co2Kg": null, "perYear": null},
  "otherPlantColumns": null,
  "remarks": "TLMAL planned to complete by Oct'2025",
  "statusKnown": true
}
```

Field sources (initiatives files; the header row is row 2, found by its "Sl. No." text):

| Field | Column header |
|---|---|
| `initiationPlant` | Initiation Plant |
| `category` / `equipmentArea` / `type` | Category / Equipment / Area of initiative / Initiative Category |
| `description` | Action Planned |
| `initiationDate` (ISO, day-first source dates "25.01.2025", "16/12/2024") | Initiation Date |
| `initiatedBy` / `responsible` | Initiated By / Resp |
| `priority` | Priority (1-3 as typed; first Priority column) |
| `investmentRs`, `expectedSavingPerYear`, `roiYears`, `plannedCompletion`, `actualCompletion` | FY 2026-27 file only: Investment Cost (in Rs), Expected Saving/Year, ROI (in Years), Planned Date of Completion, Actual date of completion. All blank in the file, so always `null` |
| `plantStatuses[].status*` | each plant's Status column; `energySavingKwh` = "Energy Saving in KwHr", `energyCostSavingCr` = "Energy Cost Saving in Cr" (as given, crore rupees, e.g. 0.03 Cr) |
| `initiatorStatus` | FY 2026-27 file only: "Initiated plant Status" (Closed / Open / WIP / On Hold) |
| `otherPlantColumns` | FY 2026-27 file only: the sparse TASL BLR / TASL NGP / TSAL HYD / TCOE HYD cells beside it, kept raw because they contradict the plant blocks (see gaps) |
| `achieved.energyKwh` / `energyCostCr` | sum of the plant values above (`null` if none); `co2Kg` is `null` (the files have no CO2) |

`energySavingKwhRaw` shows the cell text, so `"NA"` can be told from a blank (`null`).

A calculation-sheet record (`calc-power-r45`, TSAL, "Reduction of lightning power consumption by replac...", trimmed):

```json
{
  "id": "calc-power-r45", "phase": "present", "plants": ["TSAL"], "domain": "lighting",
  "domainSource": "description keywords",
  "source": {"kind": "calculation-workbook", "workbook": "Sustainability Calculation - ... FY20 to FY27.xlsx",
             "sheet": "Energy(Power) Initiatives", "row": 45, "usedEntryFallback": false},
  "plantStatuses": [{"plant": "TSAL", "status": "implemented", "statusOriginal": null,
                     "statusBasis": "savings recorded in a yearly total", "phase": "present"}],
  "achieved": {
    "byFinancialYear": {"FY24-25": {"value": 12150.0, "co2Kg": 10327.5},
                        "FY23-24": {"value": 14580.0, "co2Kg": 12393.0},
                        "FY18-19": {"value": 0.0, "co2Kg": 0.0}},
    "unit": "kWh", "co2Unit": "Kg CO2 / yr", "total": 26730.0, "totalUnit": "kWh", "energyKwh": 26730.0
  }
}
```

Calculation-sheet sources: plant block = the text in column B ("Plant"); description = column C; yearly values =
the "Total FY ..." columns found by header text (Q = FY24-25, AI = FY23-24, BA = FY22-23, BS = FY21-22, CK = FY20-21,
DC = FY19-20, DU = FY18-19 on the Power sheet), CO2 = the "Total CO2 emmision (Kg CO2 / yr)" column next to each.
The month columns are not exposed. "Total FY 25" is April 2024 to March 2025 (the sheet's own month headers), so it is
`FY24-25`. `achieved.total` is the sum over the financial years listed, shown for convenience; for a single year read
`byFinancialYear`. Calc rows have no dates, people, priority, investment or expected savings: those are `null`.
Units: Power = kWh (the FY24-25 header says "(Kw)", an evident typo); Water = KL; Gas = Kgs; Fuel rows carry no unit in
the workbook (the sheet mixes "L" and "KL" on a few example rows), so `unit` is `null` for them and must not be
shown as a unit.

## `GET /api/initiatives/summary`

Parameters: `plant`, `domain`, `phase` (all optional; `phase` filters the counts by phase / domain / source and
the savings, not the status table).

```json
{
  "filters": {"plant": null, "domain": null, "phase": null},
  "totalInitiatives": 174,
  "byPhase": {"present": 148, "planned": 26},
  "bySource": {"initiatives-file": 42, "calculation-workbook": 132},
  "byStatus": {"implemented": 87, "in_progress": 13, "open": 22, "under_study": 6, "horizontally_deployed": 2,
               "not_feasible": 9, "not_applicable": 8, "on_hold": 0, "not_recorded": 0},
  "byStatusNote": "Per-plant status entries from the initiatives files only, one record per initiative (FY 2026-27 copy preferred), phase filter not applied.",
  "calculationSheetRows": {"withSavings": 91, "listedWithoutSavings": 36, "futurePlan": 5},
  "byPlant": {"TLMAL": {"initiatives": 66, "byStatus": {"implemented": 15, "...": 0}}},
  "byDomain": {"hvac": 53, "lighting": 43, "other": 28, "water": 19},
  "savings": {
    "expected": {"energyKwh": null, "energyCostCr": null, "co2Kg": null, "note": "..."},
    "achievedFromInitiativesFiles": {"esi": {"energyKwh": 58865.0, "energyCostCr": 0.045, "entries": 6},
                                     "fy27": {"energyKwh": 58865.0, "energyCostCr": 0.045, "entries": 6}},
    "achievedFromCalculationWorkbook": {"power": {"FY24-25": {"achieved": 516537.7, "unit": "kWh", "co2Kg": 181014.7, "rows": 76}}}
  },
  "horizontalDeployment": {"TBAL": {"count": 7, "candidates": ["..."]}},
  "reconciliation": [{"sheet": "Energy(Power) Initiatives", "plant": "TASL-MCA", "financialYear": "FY24-25",
                      "row": 157, "plantTotalInSheet": 133730.0, "sumOfListedInitiatives": 0.0}],
  "sources": ["..."], "notes": []
}
```

(Numbers are the real values at the time of writing; lists are abbreviated.)

* `byStatus` and `byPlant[].byStatus` count plant-status entries from the initiatives files, one record per
  initiative (the FY 2026-27 copy wins), so the two copies are not double counted. The calculation sheets have no
  status column, so they are summarised separately in `calculationSheetRows`.
* `byPlant[].initiatives` counts records (both files and calc sheets) that mention the plant.
* `savings.achievedFromInitiativesFiles`: `esi` and `fy27` are the same 21 initiatives; do not add them. `energyCostCr`
  is the sum of the "Energy Cost Saving in Cr" cells as given. The 58865 kWh is 36360 (item 16) plus 4501 on each of
  items 3-7: five rows with identical text, so one saving may be repeated five times in the source.
* `savings.achievedFromCalculationWorkbook[kind][fy]`: per energy type (`power`, `fuel`, `gas`, `water`) and financial
  year, the sum of the rows of that sheet that are `present`, with the sheet's own unit. kWh, litres, KL and kg are
  never added across types. `expected` is always `null`: no expected saving exists in the source.
* `reconciliation` lists every case where a sheet's "Plant Total" row does not equal the sum of that plant's rows
  (tolerance 0.5). Nothing is corrected.
* `horizontalDeployment` repeats `horizontal_deployment_candidates` for each plant (or the one filtered plant).

## `GET /api/initiatives/horizontal-deployment/{plant_id}`

`?include_unrecorded=true` also lists initiatives that have no status cell at all for the plant (default `false`).
404 for an unknown plant. Same data as the Python function below.

```json
{
  "plant": "TBAL", "count": 7,
  "items": [{
    "id": "fy27-r3", "groupKey": "g-...", "description": "Area wise specific switches for better control Office area",
    "domain": "lighting", "type": "Power Conservation", "priority": 3, "plant": "TBAL",
    "statusAtPlant": "open", "statusAtPlantOriginal": "Open",
    "implementedAt": [{"plant": "TLMAL", "status": "implemented", "statusOriginal": "Implemented",
                       "energySavingKwh": null, "energyCostSavingCr": null}],
    "source": {"workbook": "FY 2026-27 Energy Saving Initiatives Data.xlsx", "sheet": "Energy Saving Initiatives Data", "row": 3}
  }]
}
```

Rule: an initiative from the initiatives files (FY 2026-27 copy preferred) qualifies for plant P when its status at P
is `open` or `under_study`, and at least one other plant is `implemented` or `horizontally_deployed`. Sorted by
priority (1 first, blanks last), then by number of plants that already did it. Calculation-sheet rows are excluded
because they have no per-plant status.

## Functions for other modules

```python
from pem.domains import initiatives
initiatives.horizontal_deployment_candidates(plant_id, include_unrecorded=False) -> list[dict]
initiatives.list_initiatives(plant=None, status=None, domain=None, phase=None) -> dict   # same as the endpoint
initiatives.get_initiative(initiative_id) -> dict | None
initiatives.summary(plant=None, domain=None, phase=None) -> dict
initiatives.normalise_status(text) -> str | None
```

## Known gaps and oddities in the data

* **No expected savings, investment, ROI or completion dates anywhere**: the FY 2026-27 file has the columns but
  every cell is empty. All of these are `null`; the page must show "not recorded".
* **No CO2 in the initiatives files.** CO2 exists only on the calculation-sheet rows (Power, Fuel).
* **Savings are sparse in the initiatives files**: only items 3-7 (4501 kWh, 0.003 Cr, under TLMAL) and item 16
  (36360 kWh, 0.03 Cr, TLMAL) carry a number; "NA" elsewhere or blank. Items 3-7 repeat identical text and
  numbers. The 4501 kWh appears under the TLMAL block for all five, while the status of those rows
  at TLMAL is "Implemented".
* **The two initiatives files list the same 21 initiatives.** The FY 2026-27 file is not a list of new ideas; the
  statuses largely agree, with a few changed cells (for example item 1 at TCOE: Open in file 1, Implemented in
  file 2, depending on the column alignment above).
* **File 1 headers are misaligned** (see Plant mapping). File 2 also has stray cells beside the "Initiated plant
  Status" column (TASL BLR / NGP / TSAL HYD / TCOE HYD) and a second "Initiation Plant" column (X) plus plant names typed
  under the "TCOE HYD" header (W) whose values ("TASL-HYD", "TASL-NGP", "TLMAL-HYD", "All Plants" ...) differ from the
  real "Initiation Plant" column B ("Corp.PEM"); neither is used. For item 18 (RWH measurement) the stray TASL BLR /
  NGP / TSAL cells say Open / Open / Implemented while the plant blocks say Not Feasible / Under Faesible Study /
  Not Feasible. Both are kept; the stray ones appear only
  in `otherPlantColumns`.
* **Blank Initiation Plant, date or priority** on several rows (items 15, 19-21 have no date; items 8-19 have no
  priority). Item 20 and 21 (TSAL) have no equipment area; their domain comes from keywords. Item 15 has no Initiation Plant.
* **Item 20 (TSAL, "Optimization of energy consumption of paint booth ...") shows "Implemented" under TASL-HYD and
  TCOE in the FY 2026-27 file**, though it is a TSAL initiative with no TSAL status. Item 21 is "WIP" at TASL-HYD
  and "Not Applicable" at TLMAL, again no TSAL cell. File 1 gives the same for item 20 (Implemented at TASL-HYD only). Because the plant blocks look shifted for these rows,
  treat their per-plant status with caution.
* **Remarks contradict statuses**: items 10, 16, 17 say TLMAL "planned to implement / complete by Dec'2025 / Oct'2025"
  while the TLMAL block says Implemented. Remarks are passed through as `remarks`.
* **Spelling**: "Under Faesible Study", "Dishwaters", "utilizaton" are in the source and kept as is.
* **Calculation sheets, plant totals that do not add up** (see `reconciliation`): the Power sheet "Plant Total"
  rows for TLMAL FY23-24 (73834.58 vs 190905.475 summed), TASL-BLR FY24-25 (48664 vs rows with no FY24-25 total) and
  TASL-MCA (133730, no rows); the same 133730 also appears as the TASL-MCA "Plant Total" on the Fuel and Water sheets,
  which looks like a copy of one number.
* **Calculation sheets list initiatives with no figures at all** (TASL-NGP "Air Consumption monitoring", most TASL-BLR
  rows, TCOE "Air Scan test ..."). They are kept (`status: null`, `statusKnown: false`).
* **Sheet blocks without initiatives**: TLMAL (Fuel, Gas, Water), TBAL, MCA and TCOE (several), Gas for all plants.
  No records are created for them.
* The calculation sheets cover one plant per block; TSAL, TCOE, etc. are all identified through the label in column B.
  TASL-BLR's block label is on row 134 but its "6.0" serial number is on row 137; serial numbers are ignored.
* Month headers on the calculation sheets are typed as odd dates ("2024-01-25" for January 2025); month columns are not
  used by this module, only the yearly totals.
