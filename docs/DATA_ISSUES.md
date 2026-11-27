# Things found in the source files

Found while building the app, 27 Nov 2026. The app shows the data as it is and marks these; none
has been corrected. Each is worth a look by whoever owns the file. More detail, with cell-level
examples, is in the `docs\api\*.md` file for each area.

## Probably typing or unit mistakes

| Where | What | Why it looks wrong |
|---|---|---|
| Diesel, TLMAL, Jul-26 | HWG = 1,780 KL in the calculation workbook; 1.78 in the "Only to Enter" workbook | Normal month is about 0.3–2 KL |
| Diesel, TLMAL, Oct-26 | DG 95, HWG 1,401, other 30 KL (both workbooks) | Looks like litres typed in a kilolitre column |
| Diesel, TASL-Bangalore, Jul-26 | "Other than DG" = 600 KL | Normal month is about 3 KL |
| Energy, TASL-MCA, May-26 | Grid units 3,195,697 kWh | About 14 times the neighbouring months |
| Energy, TASL-MCA, Jun–Sep 2023 | DG row holds 149k–226k kWh with the EB row blank | Looks like values entered on the wrong row |
| Energy, TLMAL Jun-26 and Oct-26, TSAL 2020, MCA Mar-25 | Numbers typed as text (e.g. "2,23,101") | Excel's own SUM skips them, so the sheet's totals are low; the app reads them as numbers |
| Vehicle diesel sheet | Labelled KL, values look like litres (404 vs 0.404 in the other workbook) | Kept separate, not added to diesel |
| Revenue, all plants | Jan-26 equals Feb-26 | Both cells point at the same side table |
| Revenue, TASL-MCA FY19-20 to FY21-22 | Values are rupee amounts, not ₹ Cr | Left out |

These three diesel cases and the MCA power case are also what the recommendation model reports
under "entries to check".

## Sheets that disagree with each other

- **The two common workbooks** differ on diesel in 32 plant-months and on water in about 120 cells
  (e.g. TLMAL borewell Apr-24: 1,085 vs 1,065). The calculation workbook is used; the other value is shown beside it.
- **TBAL HVAC ECHO workbook**: the Energy Consumption sheet and the per-unit FY sheets disagree in all
  144 bay-months (Bay-1 Apr-25: 28,491 vs 19,095 kWh). Both are shown.
- **TBAL installed HVAC capacity**: 460 TR (schedule workbook), 250 TR (ECHO Sheet1), about 497 TR (layout deck).
- **"Energy consumption data" summary tables** (DP / Assy / Overall TADL) refer to the wrong rows, as does
  the "Overall TADL" block of "Water consumption data" (shifted by one row). The app adds up the plant rows instead.
- **Water Summary, TASL-NGP**: sewage and process rows are mixed, so TADL reuse for FY24-25 reads 25.8% there against 29.0% from the plant rows.
- **Initiatives files**: one status column in the first file has no heading and the "TLMAL" heading sits over
  the TBAL block, so the TCOE status from that file is uncertain. Remarks for items 10, 16 and 17 say TLMAL
  will finish in Oct/Dec 2025 while its status says Implemented.
- **Scrap sheet totals** add kilograms, tonnes and litres together. The app totals weights in kg and keeps litres apart.
- **Emission workings**: two sets of factors are in use (0.823 kg/kWh grid, 41 g/kWh solar, 2.64 kg/L diesel;
  and an older 0.85 block). Scope 1 is computed as KL × 2.64, which is tonnes, but labelled kg; "Total Scope 1 & 2" then adds tonnes to kg.
- **Afforestation benefit** appears as 1,168,000 in one place and 182,468 in another.

## Broken or missing

- **TBAL HVAC schedule workbook**: the kW-per-TR cell (C8) contains ".", so its kW and kWh columns show `#VALUE!`.
  The app uses the 1.15 kW/TR stated on the Formulas sheet.
- **TBAL meter sheet**: 92 rows, of which 45 are energy meters, 41 temperature sensors, 1 pressure sensor and
  5 system rows. No timestamps and no unit column; three columns have no heading.
- **Machine-family targets** are not in the production workbook, only in the deck's charts, and only for H01
  (TASL-Hyd), TLMAL and MCA. The MCA ones do not match the sheet's units.
- **Production workbook gaps**: MCA paint booth has no "area painted"; H06 furnaces and bays have no production
  values (`#DIV/0!`); Nagpur stops at Dec-25; for HVAC and some bays the "production unit" is a fixed room size.
- **Site code H06** is taken to be TCOE by elimination and scale; no file says so. Please confirm.
- **Scrap**: quantities exist only for hazardous waste and e-waste, only in the "Only to Enter" workbook;
  nothing for TSAL; FY21-22 to FY24-25 have TLMAL only. The only target is "Landfill Waste – 2030 = 0".
  The waste sheets in the calculation workbook are empty templates.
- **Scrap BRD PDF** is a scan with no text layer; pages 2–5 were read from the page images and are marked as such.
- **Air monitoring**: limits exist only for TBAL (its consent order, valid to 30.06.2029). The calculation
  workbook stops at FY24-25; the entry workbook is pre-filled with zeros for parameters not measured.
- **Water**: the flow-meter file gives counts per plant only, no meter locations; no counts for TASL-NGP.
  Rainwater harvesting exists only for TASL-MCA and TASL-NGP, and its units change between years.
  The "per-capita" sheet holds no per-capita values. Reuse above 100% comes from the sheet's own formula.
- **Manpower** is one headcount per year, not monthly. **Revenue** is monthly for FY25-26 only.
- **Target Sheet** holds a carbon target for 2030 and a landfill target; there is no energy target.
- **Milestones**: the first 14 slides have an intro, an Aalingana stage and a TASL stage; there is no separate Tata stage.
  Slide 10 is a picture with no text.
- **Month headings** in several sheets were typed as "Apr-26" and stored by Excel as 26 April; the app
  works out the month from its position in the year block.
