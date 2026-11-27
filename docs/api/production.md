# Production API (`/api/production`)

Energy per unit of production by machine family, one entry per **site** (one sheet each).
Source: `common/Power Consumption wrt Production Units (1).xlsx`. Module: `backend/pem/domains/production.py`.
Months are `"YYYY-MM"`; arrays in a payload follow the payload's `months` order; `null` = blank/`-`/`#DIV/0!` in the sheet (never zero-filled).
All sheets cover Apr-25..Mar-26 (FY25-26); filled months differ per family (see `notes`).

## Site code to plant
| Sheet | Site code | Plant id | Confidence |
|---|---|---|---|
| TSAL | TSAL | TSAL | high |
| TASL-B01 | B01 | TASL-BLR | medium-high (code alias only) |
| TBAL | TBAL | TBAL | high (kWh equals the TBAL HVAC ECHO workbook) |
| TASL H07 | H07 | TASL-MCA | high (deck slide 46 plots H07 paint-booth kWh/1000 exactly) |
| TLMAL | TLMAL | TLMAL | high (deck slides 40-44 match) |
| TASL NGP | N01 | TASL-NGP | high (block titles say N01) |
| TASL-H01 | H01 | TASL-HYD | high (deck slides 32-39 match) |
| TASL-H06 | H06 | TCOE | medium (elimination + scale; no file names H06) |

No two sheets belong to one plant. Each payload carries `mappingConfidence` and `mappingEvidence`. A site accepts its code, sheet name or plant id in URLs.

## Endpoints
### `GET /api/production/sites`
All sites with families. Example (trimmed):
```json
{"source":"Power Consumption wrt Production Units.xlsx",
 "sites":[{"code":"H01","sheet":"TASL-H01","plant":"TASL-HYD","plantName":"TASL-Hyd","mappingConfidence":"high",
   "mappingEvidence":"...","familyCount":8,"firstMonth":"2025-04","lastMonth":"2026-03",
   "families":[{"id":"hvac-1","name":"HVAC #1","category":"hvac","categoryLabel":"HVAC","energyUnit":"kWh",
     "productionUnit":"Run hours","energyPerUnitUnit":"kWh/Run hours","lastEnergyMonth":"2026-03",
     "hasProduction":true,"hasTarget":true}]}],
 "notes":["Targets are not in the workbook; ..."]}
```
### `GET /api/production/sites/{site}`
Same site object, families only (summary rows). Unknown site: `families: []` with a note.
### `GET /api/production/sites/{site}/families/{family_id}`
Full detail; 404 if unknown. Example (H01 `hvac-1`, trimmed):
```json
{"id":"hvac-1","name":"HVAC #1","category":"hvac","site":"H01","plant":"TASL-HYD","months":["2025-04","..."],
 "energy":{"label":"KWH","unit":"kWh","values":[40133.0,40920.0],"sheetTotal":220695.91},
 "production":{"label":"Run hours","unit":"Run hours","values":[910.0,934.0],"sheetTotal":6336.0},
 "energyPerUnit":{"label":"KWH/Hour","unit":"kWh/Run hours","sheetValues":[44.1022,43.8116],"computedValues":[44.1022,43.8116]},
 "totals":{"energy":220695.91,"energyWhereProductionKnown":220695.91,"production":6336.0,"energyPerUnit":34.8321,"basis":"..."},
 "extraRows":[],
 "target":{"values":{"2025-04":50.0},"unit":"KWH/Hour","verifiedAgainstSheet":true,"deckAxisTitle":"MW / Hr",
   "source":"Sustainability deck, slide 32: ...","notes":["..."]},
 "notes":[]}
```
Field origins: `energy` = the block's `KWH`/`MWH` row; `production` = the first other row (sq.ft, sq.m, run hours, cubic metre, tons, parts, SS...); `energyPerUnit.sheetValues` = the block's `KWH/<unit>` row; `computedValues` = energy / production, null when production is null or 0 (the sheet shows `#DIV/0!`); `sheetTotal` = the sheet's `Total`/`Average` column (it is a sum for kWh and a ratio of sums for ratios, despite the label "Average" on the TSAL sheet). `totals.*` sum only months where both are filled. `extraRows` = additional unclassified rows.
`target`: only H01, TLMAL and H07 (MCA) families that have a deck "Target vs Actual" chart (slides 32-39, 40-44, 45-47); otherwise `null`. `verifiedAgainstSheet=true` means the deck's actual line reproduces the sheet ratio (x1000 for MWh decks, as-is for the H01 revenue chart) and target values are given in the sheet's unit. `false` (MCA) means the deck's unit is unclear and values are the deck's own numbers, unconverted.
### `GET /api/production/sites/{site}/compare?metric=energy|production|energyPerUnit`
All families of the site on one month axis: `{site, plant, metric, months, series:[{id,name,category,unit,values}], notes}`.
### `GET /api/production/categories`
`[{category,label,sites}]`. Categories: hvac, compressor, paint, chemical-line, furnace, autoclave, oven, lighting, revenue, machines, cca, assembly, ship-set, other (from keywords in the block title).
### `GET /api/production/categories/{category}/compare?metric=...`
One category across sites: `{category,categoryLabel,metric,months,series:[{site,plant,familyId,name,unit,values}],notes}`. A note warns when units differ (e.g. kWh/sq m vs kWh/run hour).
### `GET /api/production/sources`
Compares the entry workbook sheet "Power Cons wrt Production units" with the production workbook: `moreCompleteSource` (the production workbook), per-plant filled counts, and a cell-by-cell TSAL comparison (99 kWh cells compared, 0 mismatches).

## Known gaps and oddities
- Targets exist only in the deck and only for H01, TLMAL, MCA. TLMAL deck values are rounded to 3-4 decimals of MWh (about +/-0.05 kWh/unit) and both chart lines carry the same name, so the target line is inferred (the one not tracking the sheet).
- MCA (H07) deck charts: axis says MWh per m2 / load hour but the "actual" values are MWh totals (paint booth Apr-25 15.82 = 15,815.5 kWh / 1000); not comparable to the sheet's ratio. The deck also shows MCA Feb-26 paint-booth 19.35 that the sheet lacks.
- H07 Paint Booth: "Sq.Meter area painted" is `-` for every month, so no energy per unit. H07 Air compressor has run hours for Feb-26 (468) but no kWh.
- H06 Ipsen/LVT furnaces ("Parts produced") and Bay-1/Bay-2 machines ("Runnning hours") have no production values: ratios are all `#DIV/0!` in the sheet. ALD furnace has parts Apr-Dec only.
- TLMAL F16 SS / M2M SS / AH64 SS: SS = 0 in every kWh month (ratio undefined). "SS" is not defined in the workbook (likely ship sets).
- Fixed room sizes (HVAC `Cubic Meter`, B01 CCA `Sq Meter`, H07/H06 bay `Sq Meter`) are constant each month; kWh per unit is then just scaled kWh, not production-linked (flagged in `notes`).
- Date headers on most sheets are text such as "Apr-25" that Excel stored as 25-Apr-2026; the parser reads the year from the day number.
- N01 (Nagpur) has Apr-Dec-25 only. H01 `Revenue` block is MWh vs Crore (the only revenue-linked family).
- The "Only to Enter" sheet fills TSAL only (Apr-Dec-25); the other seven plants are empty. Values agree with the production workbook.
