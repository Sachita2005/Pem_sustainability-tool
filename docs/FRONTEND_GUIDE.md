# Front-end guide — how to build a page

The site is a single page with no build step: plain ES modules, Chart.js (vendored), one stylesheet.
Open `frontend/index.html`, `frontend/js/app.js`, `ui.js`, `charts.js`, `api.js` and
`frontend/css/app.css` first — they are short, and every page is built from them.

## A page

`frontend/js/pages/<name>.js` (names: home, milestones, energy, water, scrap, initiatives, report):

```js
import { api } from '../api.js';
import { h, load, pageHead, section, card, kpi, table, tabs, chips, notes, empty, fmt, fmtShort, fmtPct, monthLabel } from '../ui.js';
import { seriesChart, donutChart, targetSeries } from '../charts.js';

export async function render(root, ctx) {      // ctx = { params, rest, navigate }
  root.appendChild(pageHead('Water', 'Consumption, reuse and metering across the eight plants'));
  const box = h('div');
  root.appendChild(box);
  load(box, api.get('/water/overview'), data => [ ...nodes ]);
}
export function destroy() { /* clear timers you started; charts are cleaned up for you */ }
```

- Build DOM with `h(tag, attrs, ...children)`. Strings passed as children are inserted as **text**.
  Never build HTML strings from data (no `innerHTML` with values from the API).
- Use `load(container, promise, build)` for every independent block so that a block shows a spinner,
  then its content, or its own error box. **One failing request must never blank the page** — give
  each card/section its own `load`.
- Sub-views inside a page (tabs, a selected plant) are local state; re-render the affected container.
  For a deep link use the hash query: `ctx.navigate('energy', { plant: 'TBAL', tab: 'families' })`
  and read `ctx.params`.

## Rules that matter

1. **Real data only.** Show what the API returns. Never hard-code a figure, never fill a gap with a
   typical value, never compute a "sample". `null` renders as a dash (`fmt` does this) and as a gap in
   a chart (pass `null` in the data array). If a payload has `notes`, render them with `notes(list)`.
   If a section has nothing, show `empty('…what is missing…')`.
2. **Say what a number is**: every KPI and chart has its unit and its period (month or FY) in the
   label or subtitle, and where useful the source ("Sustainability Calculation workbook").
3. **Numbers**: `fmt(n, digits)` (Indian grouping), `fmtShort(n)` (L / Cr), `fmtPct(n)`,
   `monthLabel('2026-04')` → `Apr-26`. Right-align numeric table columns (`num: true`).
4. **Charts** only through `charts.js`:
   - `seriesChart(container, labels, series, opts)` — line/bar/mixed, stacked, dual axis, horizontal;
     series items: `{ label, data, type, axis: 'right', stack, fill, dashed, tone, unit, points }`.
   - `donutChart(container, slices, opts)`; `targetSeries(label, value, length)` for a target/limit line.
   - Colours come from the theme: do not pass hex colours. Use `tone: 'good' | 'warn' | 'bad' | 'brand' | 'info' | 'muted'`
     when a series has a meaning (target = 'bad' dashed, renewable = 'good'), otherwise leave it to the palette.
   - Pick the chart for the question: trend over months → line; composition over time → stacked bar;
     share of a whole → doughnut; comparing plants → horizontal bar; actual vs target → line + target line.
5. **Styling**: use the classes in `app.css` (`card`, `grid cols-2/3/4`, `grid kpis`, `grid split`,
   `kpi`, `badge good/warn/bad/info/brand`, `tabs`, `chips`, `table`, `btn`, `input`, `form-row`,
   `notes`, `empty`). If a page needs something more, add `frontend/css/<page>.css` and load it from
   your page with a `<link>` appended to `document.head` once — do not edit `app.css` or other shared
   files; say in your report if a shared change is needed.
6. **Works in light and dark** (toggle in the top bar) and down to a phone width (the grid classes
   collapse on their own; test at 390 px wide — no sideways scrolling of the page; wide tables scroll
   inside `.table-wrap`).
7. **Accessible basics**: real `<button>`s, labels on inputs, status not conveyed by colour alone
   (badge text or an icon as well).
8. Plants: ids are `TASL-NGP, TASL-BLR, TSAL, TCOE, TLMAL, TASL-HYD, TBAL, TASL-MCA`
   (`GET /api/plants` gives names and clusters DP/ASSY). Offer "All plants (TADL)" where the API has a total.

## Checking your page

The server is usually already running at http://127.0.0.1:8800/ (start it with
`.venv\Scripts\python.exe -m uvicorn pem.app:app --port 8800` from `backend\` if not; static files
are served fresh, so just reload). Open your page in a browser, click through every tab and control,
and check the browser console is free of errors in both themes and at phone width. Compare several
on-screen numbers with the API response and with the source workbook cell they came from.
