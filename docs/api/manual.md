# Manual entry API (`/api/manual`)

Month-wise manual entry of four parameters for seven plants. Entries are stored in Excel files, so
they can be added, viewed and deleted on the website **and** edited in Excel.

Code: `backend/pem/domains/manual.py`. Tests: `backend/tests/test_manual.py`.

| Parameter id    | Label                                    | Unit        | Workbook values are |
|-----------------|------------------------------------------|-------------|---------------------|
| `diesel`        | Diesel consumption                       | `KL`        | monthly             |
| `manpower`      | Manpower                                 | `headcount` | **yearly only**     |
| `revenue`       | Revenue                                  | `₹ Cr`      | monthly (FY25-26 only) |
| `afforestation` | Afforestation (trees + shrubs planted)   | `No.`       | monthly (FY25-26 only) |

Plants allowed: `TASL-NGP, TASL-BLR, TSAL, TCOE, TLMAL, TASL-HYD, TASL-MCA`.
**TBAL is deliberately excluded** (400 with a clear message on add, and on `?plant=TBAL`).
Plant input may use any spelling `canonical_plant()` knows ("TASL-Nagpur", "ngp", "TCOE-Hyd") plus
"Banglore"; responses always use the plant ids above.

Months are `"YYYY-MM"`, financial years `"FY25-26"` (April to March). Filters accept `FY25-26`,
`FY 25-26`, `2025-26`, `25-26`.

## Storage (Excel)

The one place the app writes inside the source folder: the four existing sub-folders of `energy/`.

| Parameter       | File |
|-----------------|------|
| diesel          | `energy/diesel/Diesel - manual entries.xlsx` |
| manpower        | `energy/man power/Manpower - manual entries.xlsx` |
| revenue         | `energy/revenue/Revenue - manual entries.xlsx` (the three PNG screenshots next to it are untouched) |
| afforestation   | `energy/Afforestation/Afforestation - manual entries.xlsx` |

Created on the first write. One sheet, header row:
`Plant | Month (YYYY-MM) | Value | Unit | Note | Entered at | Entry ID`.
If the folder already holds some other `*manual*.xlsx`, that file is used instead of the default name.

* The file is read fresh on every request, so edits made in Excel show up immediately.
* Hand-typed rows are tolerated: plant in any known spelling; month as text `2026-04`, `Apr-26`,
  `April 2026`, `2026/04`, or as a real date (read as its calendar month; note Excel turns a typed
  `Apr-26` into 26 April of the *current* year, so typing `2026-04` as text is safest); numbers
  typed as text (`"1,250.5"`); a missing Entry ID gets a stable derived id (`h` + 10 hex digits from
  plant, month and occurrence) which can be used to delete the row.
* Unusable rows (unknown plant, TBAL, unreadable month, negative/non-numeric value) are skipped and
  reported in `notes` of the list response; they are never deleted.
* Hand-typed duplicate rows for one plant+month: the later row wins in `combined`; the next `POST` for
  that plant+month collapses them into one row.
* If the file is open in Excel (or otherwise locked) a save, delete or read answers **409** with
  "...the file is open in Excel (or locked by another program). Close it and try again." A save goes
  to a temp file first, so a failed save never damages the workbook.
* A file whose first rows contain no `Plant | Month...` header: reading returns a note, writing
  returns 409.

## GET `/api/manual/parameters`

The four parameters, for building the entry form.

```json
{"parameters": [
  {"id": "diesel", "label": "Diesel consumption", "unit": "KL",
   "plants": [{"id": "TASL-NGP", "name": "TASL-Nagpur"}, "... 7 plants, no TBAL ..."],
   "excludedPlants": ["TBAL"], "workbookGranularity": "monthly",
   "folder": "energy/diesel", "file": "Diesel - manual entries.xlsx"},
  {"id": "manpower", "label": "Manpower", "unit": "headcount", "workbookGranularity": "yearly", "...": "..."},
  {"id": "revenue", "label": "Revenue", "unit": "₹ Cr", "...": "..."},
  {"id": "afforestation", "label": "Afforestation (trees + shrubs planted)", "unit": "No.", "...": "..."}
]}
```

## GET `/api/manual/{parameter}?plant=&fy=`

Manual entries from the Excel file, sorted by month then plant. Both filters optional.
Unknown parameter: 404. Unknown/TBAL plant or unreadable `fy`: 400. File locked: 409.

```json
{"parameter": "diesel", "unit": "KL",
 "file": "C:\\...\\energy\\diesel\\Diesel - manual entries.xlsx", "fileExists": true,
 "excludedPlants": ["TBAL"],
 "entries": [{"id": "3a9a7736eb71", "plant": "TLMAL", "name": "TLMAL-Hyd", "month": "2025-04", "fy": "FY25-26",
              "value": 1.5, "unit": "KL", "note": "meter reading", "enteredAt": "2026-11-27T13:38:48"}],
 "notes": []}
```

`enteredAt` is null for hand-typed rows without a timestamp. `unit` is what the row says, or the
parameter's unit if blank.

## POST `/api/manual/{parameter}`

Body: `{"plant": "TLMAL", "month": "2025-04", "value": 1.5, "note": "optional"}`.

Validation (400 with a message in `detail`): known non-TBAL plant; `month` strictly `YYYY-MM`
(2000-2099, month 01-12); `value` a finite number >= 0 (0 is allowed; numeric strings accepted);
manpower must be a whole number. A second entry for the same plant+month **replaces** the first and
keeps its id (`"replaced": true`). The unit written to the file is the parameter's unit; any `unit`
in the body is ignored.

```json
{"ok": true, "replaced": false, "file": "Diesel - manual entries.xlsx",
 "entry": {"id": "3a9a7736eb71", "plant": "TLMAL", "name": "TLMAL-Hyd", "month": "2025-04", "fy": "FY25-26",
           "value": 1.5, "unit": "KL", "note": "meter reading", "enteredAt": "2026-11-27T13:38:48"}}
```

## DELETE `/api/manual/{parameter}/{entry_id}`

Removes the row with that id (explicit or derived). `{"ok": true, "deleted": "<id>", "file": "..."}`.
404 if no such id or no file yet; 409 if the file is locked.

## GET `/api/manual/{parameter}/combined?plant=&fy=`

Per plant (all seven, or the one requested) and month, the value already in the common workbooks next
to any manual entry. **Manual takes precedence**, and every cell says where its `value` came from.
A plant lists only months that have a workbook value or a manual entry; no month is invented.

```json
{"parameter": "diesel", "unit": "KL", "workbookGranularity": "monthly",
 "precedence": "manual entry over workbook",
 "plants": [{"plant": "TLMAL", "name": "TLMAL-Hyd", "months": [
   {"month": "2025-04", "fy": "FY25-26", "value": 1.5, "source": "manual", "entryId": "3a9a7736eb71",
    "note": "meter reading", "workbookValue": 1.32, "workbookSource": "calculation", "manualValue": 1.5},
   {"month": "2025-05", "fy": "FY25-26", "value": 3.46, "source": "workbook", "entryId": null, "note": null,
    "workbookValue": 3.46, "workbookSource": "calculation", "manualValue": null}]}],
 "notes": ["..."]}
```

Cell fields: `source` is `"workbook"` or `"manual"`; `workbookSource` is `"calculation"` (primary
workbook) or `"entry"` (the "Only to Enter" workbook, used only where the calculation workbook has no
value) or null when the workbook has nothing for that month; `entryWorkbookValue` appears only where both
workbooks have a value and they differ (the calculation value is the one shown).

`manpower` differs: months hold manual entries only, and each plant carries
`"yearly": [{"fy": "FY25-26", "value": 1033.0, "source": "workbook"}, ...]` (filtered by `fy` if given).
Yearly figures are never spread over months.

### Where workbook values come from

All in `common/Sustainability Calculation ... .xlsx` (via `workbooks.read_sheet`, cached), with the
"Only to Enter" workbook as fallback where noted. Columns are found by header, not address: the
month headers form runs of twelve followed by an `FY xx-yy` total column; the month number comes from
the header and the year from that FY label (the header *dates* are unreliable, Excel having read
"Apr-26" as 26 April of some year; e.g. the Mar-23 header is the date 2024-03-23).

| Parameter | Sheet | Rows / columns used |
|-----------|-------|---------------------|
| diesel | `Diesel consumption data` (fallback: same sheet in Only to Enter) | per plant block, the `Total Consumption` row (DG + HWG + forklift + other), unit KL; all FY blocks FY18-19 to FY26-27. A 0 over blank components is the sheet's SUM of nothing and is treated as "no data". Summary rows (`Overall HSD ...`) below the plants are ignored. Sheet plant labels: TLMAL, TSAL, TASL-Hyd, TBAL-Hyd (dropped), TASL-Nagpur, TASL- Banglore, TASL-MCA, TCOE-Hyd. |
| revenue | `TADL Revenue Summary` (no fallback) | rows by `Sub-Plant`, the 12 month columns of FY25-26, unit ₹ Cr (sheet title "PLANT REVENUE IN CR"); the `TADL` company total row and the side tables are ignored; TBAL row dropped. |
| afforestation | `Afforestation sheet` (fallback: same sheet in Only to Enter) | per plant block, the `Afforestation Total` row (trees + shrubs planted), unit No., only the FY25-26 run under the title "Afforestation Details". |
| manpower | each `... Sustainability Summary` sheet (TASL-NGP, TASL-BLR, TSAL, TCOE, TLMAL, TASL-HYD, TASL(MCA)-HYD) | row `Employees` (Nos) against the `FY ..` header row; `TADL Total Empl Count` / `Employees` sheets are single snapshots with no date and are not used. |

Verified examples: diesel TLMAL Apr-25 = 1.32 KL, TASL-Nagpur Apr-25 = 34.45; revenue TASL-NGP Apr-25 =
25.13 ₹ Cr; afforestation TASL-HYD Apr-25 = 450 (200 trees + 250 shrubs); TASL-NGP Jun-25 = 325
(entry workbook only); manpower TASL-NGP FY25-26 = 2116.

### Known gaps and oddities in the data (shown as found, not corrected)

* **Manpower has no month-wise data** in the workbooks, only one headcount per FY.
* **Revenue and afforestation are month-wise for FY25-26 only.** Revenue Jan-26 and Feb-26 hold exactly the same
  figure for every plant (a note says so), and the sheet title says "YTD Jan-26".
* **Afforestation**: the FY24-25 and older blocks of `Afforestation sheet` have a copy-pasted
  "Diesel Consumption Details" title and values that look like standing stock (about 7,000 every month for
  TASL-HYD), so they are left out. In the calculation workbook only TASL-HYD, and a few zeros, have
  FY25-26 figures; most plants' real monthly plantings are in the Only to Enter workbook, which is why the
  fallback matters. TASL-MCA Apr-25: calculation workbook 0, entry workbook 400 (shrubs not counted in the
  calculation workbook's total).
* **Diesel unit slips**: TLMAL Jul-26 `HWG` is 1780 in the calculation workbook but 1.78 in the entry workbook;
  TLMAL Oct-26 shows 95 / 1401 / 30 KL in both (almost certainly litres typed as KL); TASL-BLR Jul-26
  "Other than DG" is 600. These flow into `value` unchanged. About 32 plant-months differ between the two
  workbooks (flagged with `entryWorkbookValue`).
* The separate `Vehicle- Diesel consumption dat` sheet (company cars) is not added into diesel. Its values in
  the calculation workbook are in litres under a "KL" label (404 against 0.404 in the entry workbook).
* Months the workbooks leave blank are simply absent from `combined`.
* `canonical_plant()` does not know "TASL- Banglore" (the diesel sheet's spelling); this module maps it locally.
