# Air API (`/api/air`)

Module: `backend/pem/domains/air.py`. Every response has a `notes` list. Missing files give a valid empty payload plus a note.

## Sources

* Workbook "Sustainability Calculation" (primary) and "Sustainability (Only to Enter)" (fills blanks, `"source": "entry"`), sheet **Air Monitoring data**.
  One row per plant and parameter (col B plant, C parameter label, D unit), then year blocks of 12 monthly columns (Apr..Mar). The month headers are dates whose *day* is the two-digit year (`2024-04-24` = Apr-24); months are assigned by position in each 12-column block.
  Calculation workbook: FY18-19 .. FY24-25; entry workbook: FY18-19 .. FY26-27 (to Oct-2026 for some plants). A repeated FY21-22 block at the far right of both sheets is ignored.
* **Consent order**: `air monitoring/10. Tata Boeing Aerospace Ltd (EXP) - CFO Order - July 2024.docx` — Telangana Pollution Control Board, order dated 19.07.2024, 5 pages, for Tata Boeing Aerospace Ltd = plant **TBAL**.
* The sheet states **no limits**. All limits come from the consent order and apply to **TBAL only**; no other plant's consent order is in the source folder, so other plants get `limit: null`, `aboveLimit: null`.

## Rules

* Entry-workbook values fill only cells that are blank in the calculation workbook. Entry zeros are **not** used (506 of them): the entry sheet is pre-filled with 0.0 for parameters not measured (e.g. ambient PM10 = 0.0), which is not a reading.
* "NA" cells are returned only with `include_na=true` (`value: null, notApplicable: true`).
* "Plant Total Consumption" rows are not air data and are skipped.
* Units are the sheet's: they differ by plant for the same parameter (NOx: mg/m3, mg/Nm3, ppm, µg/m3). Never compare values across differing units. Ambient PM rows have a blank unit for several plants; the consent order's unit is µg/m3.
* `aboveLimit` is `true`/`false` only when the plant has a limit and the reading's unit is blank or equal to the limit's unit; otherwise `null`.
* Months `YYYY-MM`, FY `FY24-25`. Plants are plant ids. The sheet has no day-level dates and no sampling-point names: `location` is derived from the parameter label (`Ambient`, `DG stack`, `HWG stack`, `Paint booth`, or `null` for gas rows such as NOx/SOx/CH4/CO2).

## `GET /api/air/readings`

Parameters (all optional): `plant`, `fy`, `month` (`2024-04`), `parameter` (substring of the clean or sheet label, e.g. `PM10`, `Nitrogen`), `location`, `include_na`.

```json
{"count": 20, "readings": [
  {"plant": "TBAL", "plantName": "TBAL-Hyd", "location": "Ambient", "parameter": "PM10",
   "sheetLabel": "Ambient air monitoring Particulate Matter (PM 10)", "month": "2024-04", "fy": "FY24-25",
   "value": 71.0, "unit": null, "notApplicable": false,
   "limit": 100.0, "limitUnit": "µg/m3", "limitSource": "10. Tata Boeing Aerospace Ltd (EXP) - CFO Order - July 2024.docx, page 4",
   "aboveLimit": false, "source": "calculation"},
  {"plant": "TLMAL", "plantName": "TLMAL-Hyd", "location": null, "parameter": "Nitrogen oxides (NOx)", "sheetLabel": "Nitrogen oxides (NOx)",
   "month": "2024-04", "fy": "FY24-25", "value": 2.3, "unit": "mg/m3", "notApplicable": false,
   "limit": null, "limitUnit": null, "limitSource": null, "aboveLimit": null, "source": "calculation"}],
 "notes": ["..."]}
```

`parameter` values: `PM10`, `PM2.5`, `Particulate matter (SPM)` (stack, "Suspended"), `Particulate matter (total)` (TASL-NGP's rows, labelled "Total Particulate Matter"), or the sheet label (gases: Carbon dioxide (CO2), Nitrogen oxides (NOx), Sulfur oxides (SOx), Methane (CH4), N2O, HFCs, PFCs, SF6, NF3).
Checked: TLMAL NOx Apr-24 = 2.3 mg/m3 (row 6, F); TBAL ambient PM10 Apr-24 = 71.0 (row 62, F); TBAL PM10 Dec-24 = 63.0 comes from the entry workbook.
**No reading in the source exceeds a stated limit** (2188 readings checked; only TBAL's can be).

## `GET /api/air/limits`

The limits that are compared against readings, plus the whole set of key consent conditions and the hazardous waste authorisation, each with `page` and `document`.

```json
{"limits": [{"plant": "TBAL", "parameter": "PM10", "location": "Ambient", "kind": "ambient", "limit": 100.0, "unit": "µg/m3",
             "comparison": "above", "page": 4, "document": "10. Tata Boeing ... .docx", "condition": "Ambient Particulate Matter(PM10)"}],
 "order": {"issuer": "Telangana Pollution Control Board, Zonal Office Hyderabad", "orderNo": "868 -RR-I/TGPCB/ZOH/TG-iPASS/CFO/2024- 617",
           "holder": "M/s. Tata Boeing Aerospace Ltd.", "plant": "TBAL", "pages": 5, "document": "..."},
 "conditions": [{"group": "validity", "name": "Consent and HW authorization valid until", "value": "30.06.2029", "unit": null,
                 "wording": "valid for a period ending with the 30.06.2029", "page": 2, "document": "..."}],
 "wasteAuthorisation": [{"waste": "Waste/ Used Oil", "quantity": 360.0, "unit": "LPM", "scheduleCode": "5.1 of Sch-I", "page": 1, "document": "..."}],
 "notes": ["..."]}
```

`conditions[].group`: `validity` (order date 19.07.2024; valid until 30.06.2029; consent fee paid up to 31.03.2025; previous order valid up to 31.08.2027 cancelled; renewal application 120 days / auto-renewal 30 days before expiry), `capacity` (300 Nos/Anuum — the order's spelling), `effluent` (pH 5.5 to 9.0, TSS 200, BOD 30, Oil & Grease 10 mg/L; max daily discharge canteen 10.0 and domestic 30.0 KLD), `emission` (SPM 115 mg/Nm3), `ambient` (PM10 100, PM2.5 60, SO2 80, NOX 80 µg/m3), `noise` (day 75, night 70 dB(A)), `water` (consumption limits in KLD: floor mopping 2.0, process 15.0, canteen 10.0, domestic 40.0, green belt 23.0, total 90.0), `other` (hazardous waste storage ≤ 180 days; green belt 33 %; Form V before 30th September).
Condition values are matched in the document text by pattern; anything not found is omitted and listed in `notes`.

Caveats stated in `notes`:
* The chimney table gives one SPM standard (115 mg/Nm3) without tying it to a single chimney in the extracted text; it is applied to TBAL stack SPM (DG stack, Paint booth). HWG stack is not compared.
* The NOx/SO2 limits (80 µg/m3) are *ambient* standards; the sheet's NOx/SOx rows are gas readings (mg/m3), so they are not compared.
* Hazardous-waste quantities (Used Oil 360 LPM, STP Sludge 200 kg/month, Cotton Waste 1500, Residues of paints 500, Containers 500, Expired perishables 300, Packing material 7000, Used water-based coolant 200 LPM, Paint and oil choked filters 500 kg/month) were matched to their names from text that the extraction scrambles; check one line against pages 1-2 of the order before quoting it alone.

## `GET /api/air/overview`

```json
{"readings": 2188, "latestMonth": "2026-10", "readingsAboveLimit": 0, "flagged": [],
 "plants": [{"plant": "TBAL", "plantName": "TBAL-Hyd", "readings": 230, "lastMonth": "2025-01", "aboveLimit": 0}],
 "consent": {"plant": "TBAL", "validUntil": "30.06.2029", "document": "10. Tata Boeing ... .docx"},
 "notes": ["Limits exist only for TBAL (consent order); other plants are not checked."]}
```

`flagged` lists up to 20 readings above a stated limit (`plant, parameter, location, month, value, unit, limit, limitUnit`).

## Functions for other modules

`air.readings(plant, fy, parameter, location, month, include_na)`, `air.consent_conditions()`, `air.limits()`, `air.overview()`.

## Known gaps and oddities

* Last readings per plant: TBAL Jan-2025, TSAL Jun-2024, TCOE Nov-2024, TASL-BLR Jun-2026, TASL-NGP / TASL-MCA Sep-2026, TLMAL / TASL-HYD Oct-2026. The calculation workbook ends at FY24-25.
* Unit disagreements: TASL-BLR NOx/SOx is mg/m3 in the calculation workbook and ppm in the entry workbook; TASL-MCA NOx is mg/Nm3 and µg/m3; TASL-NGP gas rows are µg/m3. The unit travels with each reading.
* TASL-Nagpur's calculation-workbook air rows are all "NA" (its readings come from the entry workbook); TASL-BLR's gas rows are blank in the first calculation block.
* A few values look doubtful but are shown as stated: TLMAL Sulfur oxides 18.0 in Feb-23 and Mar-23 vs 0.6-1.4 mg/m3 in the months before; TASL-MCA NOx 54 vs 20 mg/Nm3 in consecutive months; TSAL "SF6" 19.0 where all other plants have NA.
* The workbook's year-block header cells are partly mis-typed (e.g. header text "Total FY 24 (Kwh)" in an air sheet); the module ignores them.
