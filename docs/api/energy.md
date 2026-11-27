# Energy API (`/api/energy`)

Source: `Sustainability Calculation ... FY20 to FY27.xlsx` (primary). Empty cells are filled from
`Sustainability (Only to Enter) ...xlsx`; a filled month carries `"source": "entry"` (and `fromEntry`
for power). Months are `YYYY-MM`, years `FY25-26`. Every payload has `notes` (list of strings: gaps and
oddities, show them). `null` = no value in the source. Plant ids: `TASL-NGP TASL-BLR TSAL TCOE TLMAL TASL-HYD TBAL TASL-MCA`.
Clusters: DP = TASL-NGP, TASL-BLR, TSAL, TCOE; ASSY = TLMAL, TASL-HYD, TBAL, TASL-MCA; `TADL` = all eight.

The first request after a server start can take ~3 minutes (workbooks are large); the module warms itself
in a background thread at start-up. Common params: `plant` (id or any spelling), `fy` (`FY25-26`).

## GET /api/energy/monthly?plant=&fy=
Unit kWh. Source: sheet "Energy consumption data" (header row with Plant / Type of Energy; month blocks FY18-19..FY26-27).
```json
{"unit":"kWh","plants":[{"plant":"TASL-BLR","name":"TASL-Bangalore","cluster":"DP","months":[
 {"month":"2026-04","fy":"FY26-27","grid":752060.0,"solar":0.0,"dg":24666.0,"purchased":250000.0,
  "total":1026726.0,"gridGross":1213000.0}]}],
 "clusters":{"DP":[{"month":"2026-04","fy":"FY26-27","grid":..,"solar":..,"dg":..,"purchased":..,"total":..,"plantsReporting":4,"plantsExpected":4}],"ASSY":[],"TADL":[]},
 "notes":[]}
```
- `grid` = "Govt. EB Units Generated" (Bangalore: "Govt. EB after 38% discount towards DRES RE", the row that sums to its total; `gridGross` is its "EB units Generated"). `solar` = in-house solar, `dg` = DG units, `purchased` = open-access/third-party renewable.
- `total` = sum of the four (what the sheet's "Plant Total Consumption" SUMs). `workbookTotal` appears only when the sheet's total differs by more than 1 kWh (text-typed numbers that SUM ignores, hard-typed totals).
- Only months with a non-zero total are returned (blank or placeholder-zero months are omitted). `flag:"outlier"` marks a month >8x/<1/8 of its FY median (not changed).
- Clusters (only when `plant` omitted) are sums computed here of plant rows; `plantsReporting < plantsExpected` means a partial roll-up.

## GET /api/energy/yearly?plant=
Same fields per FY: `{fy, grid, solar, dg, purchased, total, monthsReported, partial, renewableShare, workbookTotal?}`; `renewableShare = (solar+purchased)/total`. Sums of reported months (not the sheet's FY columns, which ignore text-typed cells). Cluster blocks add `fewestPlantsReporting`.

## GET /api/energy/diesel?plant=&fy=
Unit KL. Source "Diesel consumption data": `{month, fy, dg, hwg, forklift, other, total}` per plant plus `years`. `vehicle` comes from "Vehicle- Diesel consumption dat" (company cars, FY24-25 and later, mostly empty): labelled KL but magnitudes look like litres; never added to `total`. Entry-workbook gap filling applies.

## GET /api/energy/emissions?plant=&fy=
`plants[].years[]` (plant ids plus `TADL`), from the "... Sustainability Summary" sheets (FY columns, matched by label):
`scope1, scope2Gross, scope1Plus2, scope2IfGridInsteadOfSolar, afforestationBenefit, scope2Savings, emissionsPerRevenue` (detailed block, rows 52-86) and `scope2Basis, renewableBenefitBasis, netCarbonBasis` (0.85 block, rows 12-19). Example TADL FY25-26: scope1 1568.80, scope2Gross 60,207,361.49 kg, scope2Savings 11,391,042.57.
`factors` = rows of the TADL summary with a Conversion Factor: 2.64 kg CO2/L diesel, 0.823 kg/kWh grid (CEA), 41 g/kWh solar life-cycle. `conversionMatrix` = sheet "Conversion matrix": 0.85 kg/kWh, 2.7 kg/L HSD, 0.005 solar (used by the 0.85 block). `monthly` = sheet "Emissions" (`scope1`, `scope2` kg CO2 per plant and month) which only runs to 2024-10 (pasted values; later months omitted).
Units: scope2 kg CO2; scope1 is diesel KL x 2.64 = tonnes although labelled Kg; scope1Plus2 adds them as if both kg. FY26-27 is year-to-date.

## GET /api/energy/revenue?plant=&fy=
Unit Rs Cr. `years` from the "Revenue" row of each Summary sheet (e.g. TADL FY25-26 3979, TASL-HYD 543); `months` from "TADL Revenue Summary" (FY25-26 only, e.g. TASL-NGP 2025-04 = 25.13). Gaps: Jan-26 equals Feb-26 for all plants (formula to the Feb side table); monthly NGP sum 595.87 vs yearly 599; zero yearly values (FY26-27) omitted; MCA's FY19-20..21-22 values were rupee figures (>100,000 Cr) and are dropped.

## GET /api/energy/manpower?plant=
Headcount. `years[]` from the Summary "Employees" row (FY19-20..; TADL FY26-27 just repeats 9901). `latestSnapshot` from "TADL Total Empl Count" (undated): `label, plant, onRoll, contract, apprentices, offRollTotal, total`; TASL H01 = TASL-HYD 1351, TASL PMO / TASL Aero have `plant:null`.

## GET /api/energy/intensity?plant=
From Summary rows 100-118: `megaUnitsPerRevenue` (MWh/Cr), `hsdDgPerRevenue`, `hsdHwgPerRevenue` (KL/Cr), `emissionsPerRevenue`, `scope2PerKwh`, `scope1PerKwh`, `renewableShare` (TADL only), plus `kwhPerEmployeeComputed` (computed here: Total Units / Employees, full years only; not in the workbook).

## GET /api/energy/afforestation?plant=&fy=
Monthly trees/shrubs per plant ("Afforestation sheet", unit No.) and `benefitTables` ("Afforestration Benefit", kg CO2; two tables disagree). FY24-25 TASL-HYD months (~7,000 each) look like cumulative stock; FY25-26 are monthly plantings.

## GET /api/energy/targets
"Target Sheet" rows `{category, fy, targetLevel, reductionFraction}`. Only carbon (2030: 88,082,944.932, 0.75; 2045: 0) and landfill (0) targets exist; no energy target.

## GET /api/energy/overview
`{unit, latestMonth, latestCompleteMonth, fy, plants:[{plant, latestReportedMonth, latest, fyToDate}], tadl:{latest, fyToDate, latestFullYear}}`. `latestMonth` is the latest month any plant reported (2026-10); `latestCompleteMonth` the latest with all eight. FY-to-date sums each plant's reported months (`monthsReported`).

## Functions for other modules
`power_monthly, power_yearly, diesel_monthly, emissions, revenue, manpower, intensity, targets, overview, afforestation_monthly, workbook_monthly_values(parameter), manpower_yearly(), fy_of_month, normalize_fy, plant_of, clear_cache`.

## Known data gaps and oddities
- Month headers are dates stored as day = year (Apr-26 = 26-Apr-2026); parsed by block.
- Text-typed numbers (TLMAL Jun/Oct-26 grid, TSAL 2020 grid, MCA 2025-03 purchased): read as numbers; sheet totals ignore them.
- MCA Jun-Sep 2023: DG row holds 149,028 / 226,225 / 198,365 / 56,254 kWh with EB blank, while the sheet total says 118,978 / 161,615 / 161,615 / 201,794: likely misplaced entries.
- Diesel TLMAL 2026-07 and 2026-10 (e.g. DG 95, HWG 1,401 KL against ~0.3 typical) look like litres typed in KL; they inflate FY26-27 Scope 1 (TADL diesel 3,420 KL).
- The workbook's DP / Assy summary tables reference wrong rows; TADL summary "Renewable unit purchased" FY24-25 (8,892,077) differs from the plant sum (9,032,053).
- Summary sheets: Scope 1/2 unit labels wrong (above); two conflicting afforestation benefit rows; row 105 "Overall HSD wrt revenue" divides car diesel only (not returned).
- TASL-NGP data stops at 2026-04, MCA starts 2023-03, TCOE 2021-04; FY26-27 is partial for every plant.
