# TBAL API (`/api/tbal`)

Module: `backend/pem/domains/tbal.py`. TBAL is plant id `TBAL`. Sources are in `energy/TBAL/` of the source folder,
found by words in the file name (so "... (1).xlsx" copies still resolve). Missing files never raise: payloads stay
valid, lists are empty and `notes` says what was not found.

Every payload that can come from a live server carries `source` (`"live"` or `"snapshot"`), `readAt`, `liveTried`, `liveError`.

## Quick facts about the data (read before building the UI)

- The meter sheet has **92 rows, not 92 energy meters**: 45 energy meters, 41 temperature sensors, 1 pressure sensor and
  5 ION system-service rows (blank instrument). Show `breakdown`, do not label 92 as "energy meters".
- The meter sheet has **no timestamp column**. Per-meter `timestamp` is `null`; for the snapshot `readAt` is the workbook's
  file-modified time and `readAtKind` says so.
- The HVAC schedule workbook's efficiency cell (`C8`, "ODU Compressor Efficiency Ratio") holds `"."`, so the workbook's own
  kW and kWh columns show `#VALUE!`. The workbook's "Formulas & Derivations" sheet documents **1.15 kW per TR**; that is the
  default used here. TR and %-load columns are computed by the workbook and are reproduced exactly (tested).
- "TBAL SEU details.pptx" is one picture (a bar chart) with no text layer; its values are given only as a **by-eye transcription**, flagged as such.

---

## GET `/api/tbal/meters`

All meters. Optional case-insensitive substring filters: `kind` (`energy|temperature|pressure|service`), `section`, `seu`, `bay`.

Source: `ION Server Data - Live Meters & SEU Snapshot (1).xlsx` -> the one sheet; header row found by the text
`Section` + `SourceID`.

```json
{
  "source": "snapshot",
  "readAt": "2026-11-27T07:18:11+00:00",
  "readAtKind": "snapshot file modified time (the sheet has no timestamps)",
  "cached": false, "liveTried": false, "liveError": null, "pollSeconds": 900,
  "snapshotFile": "ION Server Data - Live Meters & SEU Snapshot (1).xlsx",
  "count": 92,
  "breakdown": {"rowsInSheet": 92, "statedMeterCount": 92, "byKind": {"energy": 45, "temperature": 41, "pressure": 1, "service": 5}, "energyMeters": 45},
  "meters": [{
    "id": 27, "tag": "Utility_Building.Main_PCC_Admin_Building", "name": "Main_PCC_Admin_Building",
    "tagGroup": "Utility_Building", "kind": "energy", "deviceType": "Energy Meter",
    "quantity": "Power Interruptions Count", "reading": 795.0, "unit": "count",
    "currentA": 72.4155, "voltageV": 243.3576, "powerKwHr": 795.0, "timestamp": null,
    "sectionNo": 1, "section": "TOP SEU (72% Priority Load)", "seu": "Admin Building & Server Room",
    "individualFlag": "Individual", "ahu": null, "bay": null
  }],
  "seuChartMap": [{"chartBar": "HVAC (Bay-1)", "feeders": "HVAC1 + HVAC2 - Mezzanine office"}],
  "notes": ["..."]
}
```

| field | from (sheet column) |
|---|---|
| `id` | `SourceID` |
| `tag`, `name`, `tagGroup` | `Full_ION_Meter_Tag`; `tagGroup` is the part before the first `.` (e.g. `Utility_Building`, `Shop_Floor_Bay-1`, `Mezzanine`), `name` the rest |
| `kind`, `deviceType` | `Instrument` ("Energy Meter", "Temperature", "Pressure"); blank in section 5 gives `kind: "service"`, `deviceType: null` |
| `quantity` | `Latest_Parameter` (null when blank) |
| `reading` | `Live_Value` |
| `unit` | **inferred from `quantity`** (Temperature -> °C, Frequency -> Hz, "Steam Pressure (bar)" -> bar, Current -> A, Interruptions -> count); null otherwise. The sheet has no unit column |
| `currentA`, `voltageV`, `powerKwHr` | `Current_A`, `Voltage_V`, `Power_kW_hr` |
| `section`, `sectionNo` | `Section`, e.g. "1. TOP SEU (72% Priority Load)", "2. Plant Main Supply (EB / DG)", "3. Secondary Equipment & Utilities (28%)", "4. Predictive Sensors ...", "5. ION System Services" |
| `seu` | `SEU_Chart_Category` |
| `individualFlag`, `ahu`, `bay` | three **unlabelled** columns right after `Power_kW_hr` (flag "Individual", AHU label, bay label; the last two are filled only for some HVAC rows) |
| `seuChartMap` | the two-column table at the right of the sheet titled "Significant Energy Graph Capaturing Data": SEU bar -> feeders behind it |

Known oddities in the data: for "Power Interruptions Count" rows `Live_Value` and `Power_kW_hr` hold the same number; for
"Frequency" rows `Live_Value` is 0 while `Power_kW_hr` is large (e.g. meter 92: 533,607.136). The columns do not always
match their `Latest_Parameter` label, so treat `reading` with care. Many sensor rows have all zeros.

## GET `/api/tbal/meters/summary`

Counts and meter ids grouped by `bySection`, `bySeu`, `byKind`, `byBay`, `byTagGroup` (each `[{name, count, meterIds}]`,
`"(none)"` for blanks), plus `source`, `readAt`, `liveError`, `breakdown`, `notes`. No readings are summed (the
sheet mixes counts, volts and kWh).

## GET `/api/tbal/meters/{id}`

`{source, readAt, readAtKind, liveError, notes, meter: {...same fields as above}}`. 404 when the id is not present.

## GET `/api/tbal/connector/status`

Configuration state of the live connector; never returns the password or credentials in the URL.

```json
{"configured": false, "serverUrl": null, "userSet": false, "passwordSet": false, "serverId": null,
 "pollSeconds": 900, "activeSource": "snapshot", "lastAttempt": null, "lastSuccess": null,
 "lastError": null, "cacheAgeSeconds": null,
 "snapshotFile": "ION Server Data - Live Meters & SEU Snapshot (1).xlsx"}
```

Settings come from `pem.config.settings` (`TBAL_ION_URL`, `_USER`, `_PASSWORD`, `_SERVER_ID`, `_POLL_SECONDS`, default 900).
Behaviour of `IonConnector` (importable: `tbal.IonConnector`, `tbal.get_connector()`, `tbal.meters_payload()`):

- URL blank -> snapshot, `liveTried: false`.
- URL set -> live read (5 s timeout), cached for `pollSeconds`. On timeout, HTTP/URL error or an unusable response -> snapshot,
  `source: "snapshot"`, `liveTried: true`, `liveError` = reason. A failed attempt is not retried for 30 s.
- **Only `IonConnector.fetch_live_meters()` knows the server's API.** The assumed shape (unverified, the real API was not
  available): `GET {url}/api/readings?serverId={id}` with Basic auth, answering
  `{"readings": [{"sourceId": 27, "value": 12.3, "unit": "kWh", "parameter": "...", "timestamp": "ISO-8601"}]}`. Readings are merged
  onto the snapshot meter with the same `sourceId` so SEU/bay/section grouping is kept. Change that one method when the real API is known.

## GET `/api/tbal/hvac/schedule`

The HVAC ODU part-load and electrical demand schedule, calculated as the workbook defines it.

Query overrides (all optional; out-of-range values give HTTP 422): `kw_per_tr` (0-10), `hours_per_day` (0-24),
`base_load_share` (0-1), `installed_tr`, `design_delta_t`.

Source: `HVAC/HVAC_ODU_Load_and_Energy_Schedule.xlsx` -> "Load & Energy Schedule" (inputs found by label in column A, values
in column C; formulas read from row 13 onward) and "Formulas & Derivations" (text; the 1.15 kW/TR).

Formulas (per row, `dT` = temperature drop 0..design dT):

- Scenario A pro-rata: `TR = dT * installed / designDT`
- Scenario B real-world: `TR = installed*base + dT * installed*(1-base)/designDT`
- `loadPct = TR / installed`, `kW = TR * kWperTR`, `kWh = kW * hoursPerDay` (daily).

```json
{
  "units": {"tr": "TR", "kw": "kW", "kwh": "kWh per day", "deltaT": "°C", "loadPct": "fraction of installed TR"},
  "workbook": "HVAC_ODU_Load_and_Energy_Schedule.xlsx",
  "defaults":  {"installedTr": 460.0, "designDeltaT": 21.0, "baseLoadShare": 0.3, "kwPerTr": 1.15, "hoursPerDay": 10.0},
  "inputs":    {"installedTr": 460.0, "designDeltaT": 21.0, "baseLoadShare": 0.3, "kwPerTr": 1.15, "hoursPerDay": 10.0},
  "overridden": [],
  "design": {"ambientC": 45.0, "setC": 24.0},
  "kwPerTrWorkbookCell": ".", "kwPerTrSource": "Formulas & Derivations sheet text (documented benchmark)",
  "formulas": ["..."],
  "rows": [{"deltaT": 1.0,
            "proRata":   {"tr": 21.9048, "loadPct": 0.0476, "kw": 25.19, "kwh": 251.90},
            "realWorld": {"tr": 153.333, "loadPct": 0.3333, "kw": 176.33, "kwh": 1763.33}}],
  "totals": {"fullLoadTr": 460.0, "fullLoadKw": 529.0, "fullLoadKwhPerDay": 5290.0, "idleBaseLoadTr": 138.0},
  "notes": ["The workbook's efficiency cell holds '.', ...", "kW per TR is held constant across part load ..."]
}
```

Verified: all 22 rows' TR and %-load (both scenarios) equal the workbook's cached values (e.g. real-world TR at dT 0 = 138,
pro-rata TR at dT 1 = 21.9047619, both 460 at dT 21). kW/kWh could not be compared because the workbook shows `#VALUE!`.
`installedTr` 460 differs from ECHO Sheet1 (250 TR) and from the deck's 24 outdoor units x 72.8 kW (about 497 TR) - see `/hvac/coverage` notes.

## GET `/api/tbal/hvac/history`

Monthly and yearly HVAC energy per bay since FY20-21.

Source: `HVAC/TBAL HVAC ECHO (1).xlsx` -> "Energy Consumption" (blocks "HVAC Bay-1", "HVAC Bay-2": months down, FY columns across,
energy then running hours), "Sheet1" (`facility`), "Inputs" (`equipmentInputs`, name-plate data). Units: kWh and hours.

```json
{"workbook": "TBAL HVAC ECHO (1).xlsx", "unit": {"energy": "kWh", "hours": "hours"},
 "years": ["FY20-21", "...", "FY25-26"],
 "bays": [{"bay": "Bay-1",
   "monthlyEnergyKwh": {"FY24-25": {"2024-04": 37246.0, "2024-05": 49419.0}},
   "monthlyRunningHours": {"FY24-25": {"2024-04": 1028.44}},
   "reportedTotalEnergyKwh": {"FY20-21": null, "FY24-25": 343595.0},
   "reportedTotalHours": {"FY24-25": 11318.42},
   "sumOfMonthsKwh": {"FY24-25": 343595.0}}],
 "facility": [{"Sl No": 1.0, "Year (FY)": "TBAL", "Location": "Bay-1 & Bay 2", "Volume (CUM)": 64638,
               "Avg Set Temperature °C": 27.0, "Avg Actual Temperature °C": 27.0,
               "Total Connected Load (TR)": 250.0, "Total Connected load (Kw)": 260.0}],
 "equipmentInputs": [{"label": "Model Name", "value": "AM260KXVANH"}],
 "notes": ["..."]}
```

`sumOfMonthsKwh` is computed here from the months (for the blank FY20-21 total); `reportedTotal*` are the sheet's own Total rows.
Gaps: FY20-21 Total row blank; Bay-2 Apr 2020 = 0 (units not running); FY25-26 may cover a full year of months (Apr-25 to Mar-26) as the
sheet has them. `Inputs` has labels with no value (Area / Volume, AHU capacity) - those are skipped, not invented. Sheet1's running hours and energy are blank.

## GET `/api/tbal/hvac/units?fy=FY25-26`

Per-HVAC-unit monthly data from the "FY 2020-21" ... "FY 2025-26" sheets (all years when `fy` is omitted). Sections:
`total` (fixed + variable kWh), `fixed` (indoor AHU blowers = running hours x 7), `variable` (outdoor units = total - fixed),
`hours` (running hours), `conditions` (set / ambient / room temperature °C per bay block; no manpower or jig-load values are filled in the sheets).
Each row: `{"bay": "Bay-1", "name": "HVAC-1", "monthly": {"2025-04": 2779.0, ...}}`. Negative `variable` values (e.g. -0.49) exist in the source for idle months.
The second temperature block is labelled "Bay -1" like the first (likely Bay-2).

On Metrics > TBAL > HVAC history, the per-unit view includes the monthly source table and chart, plus a comparison summary
for each unit (latest reading, change from the previous reported month, FY total, average and peak). It also derives
`total energy / running hour` from the matching `total` and `hours` rows for the latest month both contain data. The
pairwise matrix compares the currently selected section at each pair's latest shared month; percentage differences are
relative to the column unit, with the month shown in each cell. These are consumption comparisons, not targets: different
unit capacities, runtime and operating conditions can explain different values.

## GET `/api/tbal/hvac/reconciliation`

Compares, per bay/FY/month, the "Energy Consumption" sheet with the sum of that bay's units in the FY sheet.
`{compared, mismatched, rows: [{bay, fy, month, energyConsumptionSheetKwh, sumOfUnitsKwh, difference}]}`.
**Reported, not reconciled**: all 144 bay-months differ (e.g. Bay-1 Apr-25: 28,491 vs 19,095). The two sheets disagree and the source does not say which is right.

## GET `/api/tbal/hvac/coverage`

Facts from `HVAC/TBAL Shop floor layout showing HVAC Coverage (1).pptx` (1 slide). `facts`: bay length 171 m, width 27 m, height 13.5-14.5 m,
2 bays, 4 HVACs per bay (8 total), 3 outdoor units + 1 AHU per HVAC, supply duct at 4 m, return duct at 6.7 m, recommended 23-29 °C, no partition between bays,
outdoor unit model AM260KXVANH (72.8 kW cooling, 18.91 kW input). Also `text` (long paragraphs, verbatim) and `labelsOnDrawing`.
Not extracted: the plan/elevation drawing itself (lines and shapes); which HVAC covers which area.

## GET `/api/tbal/seu`

`deck`: what the "TBAL SEU details.pptx" contains (1 slide, 1 picture, `deckTextLayer: []`).
`deckTranscription`: the chart "Significant Energy Use Review FY 2026-27 (April-26 to Sep-26)" read **by eye** from the picture
(`extraction` says so): bars in MWh with share % and a `significant` flag for the seven outlined bars (rule printed on the slide: more than 5% of plant energy).
Bars with unreadable labels were omitted rather than guessed; values are whole MWh as printed. Treat as a manual transcription, not machine data.
`seuChartMap`: the SEU bar -> feeder table from the meter workbook (machine-read).
