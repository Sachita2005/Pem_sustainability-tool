# Water API (`/api/water`)

Module: `backend/pem/domains/water.py`. All volumes are **KL** (kilolitres) unless a field says otherwise.
Every payload has a `notes` list: read it, it states what is missing or odd. A missing file or sheet returns
a valid payload with empty data plus a note, never an error.

Entities: a plant id (`TASL-NGP`, `TASL-BLR`, `TSAL`, `TCOE`, `TLMAL`, `TASL-HYD`, `TBAL`, `TASL-MCA`; any
spelling that `canonical_plant()` knows is accepted), a cluster (`DP` = TASL-NGP, TASL-BLR, TSAL, TCOE;
`ASSY` = TLMAL, TASL-HYD, TBAL, TASL-MCA) or `TADL` (all eight). Cluster and TADL numbers are **sums of the plant
blocks** (see "Why not the workbook's TADL block"). Months are `"YYYY-MM"`, years `"FY25-26"` (April-March).

## Where the data comes from (and why)

| Figure | Source | Why this source |
|---|---|---|
| Monthly water per plant | Sustainability Calculation > **Water consumption data** (primary); Only to Enter > **Water consumption data** fills blanks only | Master sheet: one block per plant, FY18-19 to FY26-27, same sheet the workbook's reuse % rows are computed on. Its yearly sums equal the "FY xx-yy" columns of the sheet (204 of 204 cells checked) and the **Water Summary** sheet for the 7 plants other than TASL-NGP. |
| Not used for numbers | Water Sheet Working, Water Data Sheet, Complete Water Data Sheet, Water Sheet Tableau | Downstream copies: they stop at Sep-24 / Mar-25 and still hold typing errors the master has fixed (e.g. TLMAL Mar-21 borewell 8620 vs 862 in the master). |
| Workbook yearly reuse | **Water Summary** (FY19..FY25), **GEM Water Reuse Con Per% Trends** | Returned unchanged under `workbookSummary` / `gemReusePct` for comparison. |
| Flow meters | `water/Water Flow Meter Status.xlsx`, sheet "Water Flow Meter Mapping" | Only file with meter data; plant-level. |
| Ground water | **Ground Water** sheet | |
| Rainwater | **Rainwater harvesting1** (pits), master sheet rows "RWH for Garden", Only to Enter > **RWH** (rain-based estimates) | |
| "Per-capita" | **Percapita water consumption** | Holds reuse volumes, not per-capita values. |

Sheet layout the code relies on: the master sheet has one 12-month block per FY (newest first), each closed by an
`FY 26-27`-style column. Month header cells are unreliable (typed "Apr-26" but stored as the date 26-Apr, one is
"0ct-25"), so a block is read as the 12 columns before its FY label (April first). Per plant, rows are grouped:
1 Fresh Water (Municipality, Borewell, Tanker, Drinking water cans, Other), 2 Domestic (canteen, toilets, garden, ...),
3 Treated Sewage water reused (garden, toilets, other), 4 Industrial (machinery, HVAC, RO, DM ...), 5 Incoming Process
Water (ETP, ZLD), 6 Treated Process water reused (RO, DM, other), 7 Total Reuse water %age. A "Total" row closes each group.

## Common rules

* `null` = not entered. Zero is a real zero. A group Total is the sheet's own Total, except: months nobody entered
  (sheet shows 0) are `null`; months where a cell was filled from the Only to Enter workbook use the sum of the rows.
* `source`: `"calc"`, or `"entry"` when at least one cell that month came from the Only to Enter workbook;
  `filledFromEntry` then lists them as `"section.row label"`.
* `reusePct` (percent) = (treated sewage reused + treated process water reused) / fresh water total x 100. This is
  the workbook's formula (the sheet stores a fraction; here it is x100). It can exceed 100 (TLMAL, TSAL): the sheet
  itself shows e.g. 132.3 % for TLMAL Oct-26. Plant rows also carry `reusePctSheet`, the sheet's own cell x 100.
* Source row labels differ by plant (e.g. TASL-NGP municipality row is "Municipality Water/MADC"; its "other" row is
  "Other source if any.. MADC Supply"). Payload keys are normalised (`municipal`, `borewell`, `tanker`, `drinking`,
  `other`); `detail=true` on `/monthly` returns the original labels for domestic and industrial uses.

---

## GET `/api/water/overview`

Home-page headline. For TADL, each cluster and each plant: the latest reported month and FY-to-date (sum of the
months reported so far in that FY).

```json
{
  "unit": "KL", "reusePctUnit": "%",
  "tadl": {"entity": "TADL", "name": "TADL (all plants)", "latestMonth": "2026-10", "fy": "FY26-27", "plantsReporting": 6,
    "latest": {"freshWater": 27558.19, "municipal": 15393.0, "borewell": 3922.84, "tanker": 8242.35,
               "treatedSewageReused": 6758.89, "treatedProcessReused": 1025.0, "totalReused": 7783.89, "reusePct": 28.2},
    "fyToDate": {"monthsReported": 7, "freshWater": 216919.54, "municipal": 106988.0, "borewell": 42793.19, "tanker": 67138.35,
                 "treatedSewageReused": 48930.22, "treatedProcessReused": 12786.4, "totalReused": 61716.62, "reusePct": 28.5}},
  "clusters": [{"entity": "DP", "...": "same shape as tadl"}, {"entity": "ASSY", "...": "..."}],
  "plants": [{"entity": "TASL-NGP", "name": "TASL-Nagpur", "latestMonth": "2026-10", "fy": "FY26-27", "plantsReporting": 1,
              "latest": {"freshWater": 15070.0, "municipal": 15070.0, "borewell": null, "tanker": null, "...": "..."},
              "fyToDate": {"monthsReported": 7, "freshWater": 100760.0, "...": "..."}}],
  "meters": {"available": 112, "toBeInstalled": 23},
  "notes": ["TADL latest month 2026-10 is reported by 6 of 8 plants (not reported: TSAL, TBAL); its latest-month figures cover only those plants."]
}
```

Notes: each plant has its own `latestMonth` (TSAL and TBAL end at 2026-09 at the time of writing). Plant FY-to-date can
cover different numbers of months: compare using `monthsReported`. Source: master sheet as above; `meters` from the flow-meter file.

## GET `/api/water/monthly?plant=TADL&fy=FY25-26&detail=false`

Parameters: `plant` (default `TADL`; plant id/spelling, `DP`, `ASSY`, `TADL`), `fy` optional (`FY25-26`, `FY 25-26`, `2025-26`),
`detail` optional (plants only: adds `domesticDetail` and `industrialDetail` keyed by the sheet's row labels).
Returns every reported month of the entity, oldest first (TLMAL: 2018-04 to 2026-10, 103 months).

```json
{"entity": "TLMAL", "name": "TLMAL-Hyd", "unit": "KL", "reusePctUnit": "%",
 "months": [{
   "month": "2026-04", "fy": "FY26-27",
   "fresh": {"municipal": 363.0, "borewell": 680.0, "tanker": 0.0, "drinking": 0.0, "other": 0.0, "total": 1043.0},
   "domestic": 1115.0, "industrial": 113.0,
   "sewageReused": {"garden": 758.0, "toilets": 0.0, "other": 0.0, "total": 758.0},
   "processIncoming": {"etp": 60.0, "zld": 0.0, "total": 60.0},
   "processReused": {"ro": 0.0, "dm": 0.0, "other": 60.0, "total": 60.0},
   "totalReused": 818.0, "reusePct": 78.4, "rwhGarden": null, "reusePctSheet": 78.4, "source": "calc"}],
 "notes": []}
```

Field origins (master sheet, plant block): `fresh.*` group 1; `domestic`/`industrial` the group "Total" rows; `sewageReused.*` group 3
(garden/toilets/other by row label; `total` its Total row); `processIncoming` group 5; `processReused` group 6 (`ro`, `dm`, rest is `other`);
`rwhGarden` the domestic row "RWH for Garden" (only TASL-MCA and TASL-NGP have one; already counted inside `domestic`);
`reusePctSheet` group 7. For `TADL`/`DP`/`ASSY` records carry `plantsReporting` (plants that reported that month) instead of `reusePctSheet`.

## GET `/api/water/yearly?plant=`

Financial-year sums of the monthly records. Without `plant`: TADL, DP, ASSY and the eight plants.

```json
{"unit": "KL", "reusePctUnit": "%",
 "entities": [{"entity": "TADL", "name": "TADL (all plants)", "years": [
   {"fy": "FY24-25", "monthsReported": 12, "complete": false,
    "fresh": {"municipal": "...", "borewell": "...", "tanker": "...", "drinking": "...", "other": "...", "total": 300161.92},
    "domestic": "...", "industrial": "...", "sewageReused": {"...": "..."}, "processIncoming": {"...": "..."},
    "processReused": {"...": "..."}, "totalReused": 87084.32, "reusePct": 29.0, "rwhGarden": "..."}]}],
 "notes": []}
```

Years returned for every entity: FY18-19 to the current FY (FY26-27 is partial: `monthsReported` 7). `complete` is true only if every plant in
the entity reported all 12 months; otherwise the figure is a partial sum. Verified: TADL FY24-25 fresh water 300161.92 KL = Water Summary "FY 25"
(3.002e5); TADL FY18-19 199001.87 KL = Water Summary "FY 19".

## GET `/api/water/reuse?plant=TADL`

Reuse for one entity: where treated water goes and how much, yearly and monthly, plus the workbook's own summary figures.

```json
{"entity": "TADL", "unit": "KL", "reusePctUnit": "%",
 "formula": "reusePct = (treated sewage reused + treated process water reused) / fresh water total x 100",
 "yearly": [{"fy": "FY24-25", "monthsReported": 12, "complete": false, "freshTotal": 300161.92,
             "sewageReused": {"garden": "...", "toilets": "...", "other": "...", "total": 70661.0},
             "processIncoming": {"etp": "...", "zld": "...", "total": "..."},
             "processReused": {"ro": "...", "dm": "...", "other": "...", "total": 16423.0},
             "totalReused": 87084.32, "reusePct": 29.0}],
 "monthly": [{"month": "2026-04", "fy": "FY26-27", "freshTotal": 36...., "sewageReused": {}, "processReused": {}, "totalReused": 0, "reusePct": 0, "source": "calc"}],
 "workbookSummary": {"Total Reuse water %age": {"FY18-19": 12.7, "FY24-25": 25.8}, "Fresh Water": {"FY18-19": 199001.87}, "...": "..."},
 "gemReusePct": {"FY24-25": 29.12, "FY23-24": 26.41, "FY20-21": 0.0},
 "notes": []}
```

Where reused: `sewageReused.garden` = STP water to garden, `.toilets` = STP to toilets, `.other`; `processReused.ro`/`.dm`/`.other` = ETP/ZLD output
reused via RO, DM or other (the sheet's label "Treated Process water reused in (Direct saving)"). `workbookSummary` = "Water Summary" rows for this entity
(plants and TADL only; FY19..FY25 labelled by end year, converted here, so "FY 25" is FY24-25; the % row x100). `gemReusePct` = GEM sheet (TADL only; year N read as the FY starting in N).
The workbook's own yearly figures differ from `yearly` for TASL-NGP (see gaps) and `gemReusePct` uses a different aggregation (about 0-2 points off).

## GET `/api/water/rwh`

```json
{"pits": [{"plant": "TLMAL", "pits": 4, "dimensions": "4m dia x 4m Depth", "lastMaintenance": "30-03-2024", "latestMaintenanceDate": null,
           "conditionJun2024": "Pit covered by Sludge", "conditionDec2024": null, "yieldWaterTableHeight": "5.0 m", "pitsConditionOk": 0,
           "additionalPitsProposed": 0, "additionalPitsProposed2": 0, "flowMeterMeasurement": null,
           "remarks": "PR_50012552 , Dt: 30.05.2024 Waiting for PO to release to lift water and sludge"}],
 "rainwaterUsedForGarden": {"unit": "KL", "plants": {"TASL-MCA": {"label": "RWH for Garden",
     "monthly": {"2026-04": 43.0, "2026-05": 1.0}, "yearly": {"FY25-26": 101.0, "FY26-27": 349.0}}}},
 "estimatedCollection": {"unit": "as labelled in the sheet (Liters); magnitudes inconsistent between years",
     "plants": {"TLMAL": {"FY25-26": {"months": {"2025-04": 807.9}, "sheetTotal": 24651.0}}}},
 "notes": ["..."]}
```

* `pits`: "Rainwater harvesting1" sheet, one row per plant, columns by header text; values as typed (strings keep the source wording, e.g. "5.0 m"). `pitsConditionOk` and `additionalPitsProposed*` are the sheet's 0/1 flags.
* `rainwaterUsedForGarden`: master sheet row "RWH for Garden" (KL). Only TASL-MCA (values from 2025-01) and TASL-NGP (all 0 for FY25-26) have such a row. There is no metered rainwater harvest volume in the workbooks.
* `estimatedCollection`: Only to Enter > "RWH", row "Total - Water reaching to RWH" (rainfall x area x runoff coefficient), per plant, FY21-22 to FY26-27 only (the sheet's columns for earlier years are power-consumption data), plus `TADL` for FY25-26. **Units are inconsistent**: the header says Liters, but FY26-27 values (e.g. TLMAL Jul-26 656,640) are about 1000 times the FY25-26 values (TLMAL Jul-25 4,812), so FY26-27 looks like litres and earlier years like KL. Values are returned exactly as stored; do not chart them across years without confirming with the data owner.

## GET `/api/water/groundwater`

"Ground Water" sheet: yearly average borewell water level, in **feet**, per plant, plus state rainfall (mm).

```json
{"levelUnit": "feet", "rainfallUnit": "mm",
 "plants": [{"plant": "TLMAL", "level": {"FY18-19": null, "FY21-22": 223.0, "FY22-23": 230.0, "FY23-24": 221.0},
             "levelToRainfallRatio": {"FY21-22": 0.1889, "FY22-23": 0.1657, "FY23-24": 0.2214}, "remark": null}],
 "rainfallMm": {"FY21-22": 1180.0, "FY22-23": 1388.0, "FY23-24": 998.0},
 "tadl": {"level": {"FY21-22": 128.58}, "levelToRainfallRatio": {"FY21-22": 0.1089}},
 "notes": ["..."]}
```
Only TLMAL, TSAL, TASL-HYD and TBAL have values; TASL-BLR, TCOE and TASL-NGP are "NA" (no borewell inside the plant; the sheet's remark text is returned in `remark` where present). Data stops at FY23-24; FY18-19 to FY20-21 are blank in the sheet.

## GET `/api/water/percapita`

"Percapita water consumption" sheet, as stored. It is **not** per-capita: it holds yearly treated-sewage reuse, treated-process reuse and fresh water (KL) and the reuse % (as a fraction, e.g. 0.6699) for TLMAL and TSAL only (FY21-22 to FY23-24); every other plant block is empty.

```json
{"unit": "KL", "plants": {"TLMAL": {"Treated Sewage water reused for": {"FY21-22": 7022.0, "FY22-23": 4048.0, "FY23-24": 3574.0},
                                    "Total Reuse water %age": {"FY21-22": 0.6699}}}, "notes": ["..."]}
```
No per-capita figure can be produced from the source files in this area (headcount lives in the Employees sheet, which this module does not use).

## GET `/api/water/meters`

Plant-level flow-meter status from `Water Flow Meter Status.xlsx`. **The file has no per-meter rows**: no locations, lines or working state. `areas` is therefore always `[]`, and `meterGroups` carries counts by status and type so the layout can show plant -> groups with counts.

```json
{"unit": "meters",
 "plants": [{"plant": "TCOE", "name": "TCOE-Hyd", "cluster": "DP", "circuitDiagramShared": "Yes",
   "available": 18, "toBeInstalled": 9, "instrumentType": "Analog-12&&Digital-2", "byType": {"analog": 12, "digital": 2}, "unspecifiedType": 4,
   "calibrated": "Yes", "calibrationDate": "2026-03-29",
   "installationStatus": "Yet to install", "installationStatusNormalised": "to_be_installed",
   "expectedCompletion": "FY 26-27", "expectedCompletionComment": "Aradhana Dash    (2026-03-16 05:54:51) - Kindly provide ECD",
   "budgetProposed": null, "budgetProposedLakh": null, "budgetAllocated": null, "budgetAllocatedLakh": null,
   "remarks": "It will be done after April on next year's budget",
   "statusCounts": {"available": 18, "toBeInstalled": 9, "working": null, "notWorking": null},
   "areas": [],
   "meterGroups": [{"status": "available", "type": "analog", "count": 12}, {"status": "available", "type": "digital", "count": 2},
                   {"status": "available", "type": "unspecified", "count": 4}, {"status": "to_be_installed", "type": "unspecified", "count": 9}]}],
 "totals": {"available": 112, "toBeInstalled": 23, "byType": {"analog": 46, "digital": 57, "unspecified": 9},
            "plantsWithoutCounts": ["TASL-NGP"], "installationStatus": {"to_be_installed": 3, "not_applicable": 2, "installed": 1, "unknown": 2}},
 "statuses": {"available": "meters existing at the plant", "to_be_installed": "meters still to be installed"},
 "notes": ["..."]}
```

Columns: Plants, Water Circuit Diagram Shared (`circuitDiagramShared`), No. of Water Meters Available (`available`), Type of Instrument (`instrumentType`; `byType` parsed from text like "Analog- 8 and Digital- 17"; `unspecifiedType` = available minus the typed count, e.g. TLMAL "Analog - 09" of 14), Calibration Status/Date, No. of Water Meters to be Installed (`toBeInstalled`), Budget Proposed/Allocated (original text such as "7 Lac"; `...Lakh` only when the text is "<n> Lac"), Installation Status (original text in `installationStatus`; normalised to `installed` / `to_be_installed` / `not_applicable` / `unknown`), ECD (`expectedCompletion`, original text; cell comments, if any, in `expectedCompletionComment`), Remarks.
The statuses present in the file are "available" and "to be installed" only; "working"/"not working" are not recorded, so `statusCounts.working/notWorking` are `null`. Unknown fields are never guessed (TASL-NGP has a row with only "Circuit Diagram Shared = Yes"; TBAL has no installation status).

---

## Known gaps and oddities (also in the `notes` of each payload where relevant)

* **Reporting lag**: FY26-27 data ends at 2026-10 for most plants; TSAL and TBAL end at 2026-09. TADL "latest" and FY-to-date therefore cover fewer plants in the last month (`plantsReporting`).
* **Reuse above 100 %**: TLMAL and TSAL (and TBAL in earlier years) report treated water reused greater than fresh water supplied, so `reusePct` > 100 (sheet formula, unchanged). TLMAL Jun-26: sheet cell 105.0 % but its own components give 115.7 %.
* **Entry workbook disagrees with the calculation workbook** on ~120 historic cells (e.g. TLMAL borewell Apr-24 1085 in calc vs 1065 in entry). The calculation value is used; the entry value only fills blanks (about 140 cells: TBAL 88, TCOE 25, TSAL 15, TASL-MCA 13).
* **Sheet's own Total rows**: TASL-NGP industrial / process-reused / incoming-process totals disagree with the sum of their rows in ~100 early months (FY18-19 onward); the sheet's Total is shown.
* **"Overall TADL" block of the master sheet** is not used: its detail rows are shifted by one row against their labels (e.g. the row labelled "HWG" holds the "Special Process" value) and its Domestic/Industrial/Sewage/Process totals disagree with the sum of the plants in 67-103 months. Fresh totals agree except May-Jul 2026, where our figure includes entry-workbook fills.
* **Water Summary sheet**: matches our sums for the 7 other plants, but its TASL-NGP block has the treated-sewage and treated-process rows mixed (FY22-23 "process reused" 3,632 equals our sewage figure) and shows 0 for FY19-20 to FY21-22 where the master sheet has 7,221 / 7,717 / 8,790 KL of process reuse; hence its TADL reuse % (e.g. FY24-25 25.8 %) is below ours (29.0 %). The GEM sheet gives 29.12 % for FY24-25 and 0 for FY19-20/FY20-21 (no data).
* **Typing errors in downstream copies** (not used): TLMAL Mar-21 borewell 8620 / municipality 5066 in "Water Data Sheet" vs 862 / 506.6 in the master.
* **Blank cells**: e.g. TSAL Toilets (domestic) is blank in 42 of the 96 months since Apr-19, so its domestic total is partial in those months; TASL-NGP borewell/tanker blank for FY26-27; TBAL has no Oct-26 data; TSAL Nov-26 onward blank (future).
* **Year labels**: Water Summary "FY 25" means FY24-25 (verified numerically); GEM year 2024 means FY24-25; Percapita "FY 22" means FY21-22.
* **First request is slow** (about 5-15 s per sheet) because the 5 MB workbooks are opened once per sheet; results are then cached until the file changes.
* **Site codes** (N01, B01, H01 ...) do not occur in any water source file. The plant-name mapping used is the one in `plants.py` (the water sheets spell plants as "TLMAL- Hyderabad", "TASL- Hyderabad", "TBAL- Hyderabad", "TSAL-Hyderabad", "TASL-Bangalore", "TASL-MCA", "TASL- Nagpur", "TCOE- Hyderabad", and the meter file as "TASL-NGP ... TASL- MCA"); all resolve with `canonical_plant()`.
