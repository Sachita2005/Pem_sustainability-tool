# PEM Sustainability Tool

A web app that reads the plant sustainability workbooks, the Sustainability Deck and the supporting
files straight from a source folder and presents them as dashboards. Nothing is typed into the app
itself: change a workbook, reload the page, and the figures follow.

## Start it

```
.\run.ps1
```

This starts the server and opens http://127.0.0.1:8800/ in the browser. Stop it with Ctrl+C.
First-time setup on a new machine (Python 3.12):

```
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The first page load after a workbook has changed takes one to three minutes while the two large
workbooks are read; after that the parsed sheets are cached (in `data\cache`) and pages open quickly.

## Settings (`.env`)

| Setting | What it is |
|---|---|
| `PEM_SOURCE_DIR` | The folder that holds `common`, `energy`, `water`, `initiatives`, `scrap generation`, `air monitoring` |
| `PEM_DATA_DIR` | Where the app keeps its own files (default `data\`): the working copy of the deck, slide pictures, approvals, caches |
| `PEM_HOST`, `PEM_PORT` | Where the server listens. Use `PEM_HOST=0.0.0.0` to let other people on the network open it at `http://<this-computer>:8800/` |
| `TBAL_ION_URL`, `TBAL_ION_SERVER_ID`, `TBAL_ION_USER`, `TBAL_ION_PASSWORD`, `TBAL_ION_POLL_SECONDS` | The TBAL energy-meter server. Blank URL = use the snapshot workbook |
| `PEM_AI_PROVIDER`, `PEM_AI_URL`, `PEM_AI_API_KEY`, `PEM_AI_MODEL` | `local` = the model built into the app; `internal` = the company AI server |

Restart the server after changing `.env`.

## What reads what

| Page | Source files |
|---|---|
| Home | Everything below, summarised |
| Metrics › Energy › Energy Consumption | `common\Sustainability Calculation …xlsx` (primary), `common\Sustainability (Only to Enter) …xlsx` (fills blanks, marked), `common\Power Consumption wrt Production Units.xlsx` (machine families) |
| Metrics › Energy › Diesel, Revenue, Manpower, Afforestation | The same two workbooks, plus manual entries (below) |
| Metrics › Energy › HVAC & Live Meters (TBAL only) | `energy\TBAL\ION Server Data ….xlsx`, `energy\TBAL\HVAC\*` |
| Metrics › Water | The two workbooks, `water\Water Flow Meter Status.xlsx` |
| Metrics › Air Monitoring | The two workbooks, `air monitoring\… CFO Order ….docx` |
| Metrics › Scrap Generation | `Only to Enter` workbook (the only place scrap quantities exist), `scrap generation\*` |
| Initiatives | `initiatives\*.xlsx`, the initiatives sheets of the calculation workbook, and the recommendation model |
| Report | `common\Sustainability Deck ….pptx` |
| Milestones | The first 14 slides of the same deck |

Files are found by words in their names, so a renamed copy ("… (1).xlsx") still works; if two
copies match, the most recently modified one is used.

### Manual entries

"+ Enter Monthly Data" (Metrics › Energy) records Diesel, Manpower, Revenue and Afforestation month
by month for every plant except TBAL. Entries are saved as Excel files in the source folder —
`energy\diesel`, `energy\man power`, `energy\revenue`, `energy\Afforestation` — so they can also be
edited in Excel. A manual entry takes precedence over the workbook value for that plant and month.
Close the file in Excel before saving from the website; the page says so if it is open.

### Report: edit, save, download, approve

The original deck is never changed. The app keeps a working copy in `data\deck`; **Edit** lets you
change the text of any text box on a slide, **Save** writes it into the working copy (fonts and
colours are kept) and re-renders the slide, **Download** gives the working .pptx, **Approve** records
who approved and when — and any later edit returns the deck to draft. History shows every edit and
approval, and can restore the original deck. Charts and pictures on slides are not editable here.

Slide pictures are made by Microsoft PowerPoint on the machine running the server. On a machine
without PowerPoint the Report tab still works but shows the slide text without pictures.

### TBAL live meters

Until `TBAL_ION_URL` is set, the meters tab shows the snapshot workbook and says so. Once it is
set, readings are fetched every `TBAL_ION_POLL_SECONDS` (900 = 15 minutes); if the server cannot be
reached the snapshot is shown with the reason. The real server's API was not known when this was
written: the one place to adapt is `IonConnector.fetch_live_meters()` in
`backend\pem\domains\tbal.py`, whose comment states the request and response it assumes.

### Recommendations

The Initiatives › Upcoming tab lists abnormal months found in each plant's electricity, diesel and
fresh-water series, with what to do about them. Today this is a statistical model inside the app
(`backend\pem\ai_model.py`): no data leaves the server. To use the company AI server, set
`PEM_AI_PROVIDER=internal` and `PEM_AI_URL`; the request and response it expects are described at
`InternalProvider` in the same file, and the built-in model remains the fallback.

## For developers

```
backend\pem\            config, workbook cache, plants, the recommendation model, the web server
backend\pem\domains\    one module per area (energy, production, manual, tbal, water, scrap, air, initiatives, ai, deck, milestones)
backend\tests\          pytest, run against the real source folder
docs\api\               what each endpoint returns and which sheet each field comes from
frontend\               the single-page site (plain ES modules, Chart.js vendored, no build step)
tools\smoke.py          calls every endpoint and reports failures
```

```
.\.venv\Scripts\python.exe -m pytest backend\tests -q      # all tests (several minutes: they read the real workbooks)
.\.venv\Scripts\python.exe tools\smoke.py                  # quick check that every data area answers
```

The rule the code follows throughout: every number on screen comes from a source file or is
computed from one by a stated formula; where the source has nothing, the page shows a dash and a
note. Oddities found in the source files are listed in `docs\DATA_ISSUES.md`.

# Pem_sustainability-tool
