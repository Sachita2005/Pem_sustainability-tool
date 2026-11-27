// Metrics > Water, for the plant chosen in the Metrics frame.
// Every figure comes from /api/water/* (workbooks "Sustainability Calculation", "Only to Enter",
// "Water Flow Meter Status"); nothing is typed in here. Sums or differences shown (change vs last FY,
// reconciliation flags) are worked out from API values and say so.

import { api } from '../../api.js';
import { badge, change, clear, empty, errorBox, fmt, fmtPct, h, loading, monthLabel, notes, select, table } from '../../ui.js';
import { destroyCharts, seriesChart, targetSeries } from '../../charts.js';

const VIEWS = [
  { id: 'consumption', label: 'Consumption' },
  { id: 'meters', label: 'Flow meters' },
  { id: 'reuse', label: 'Reuse' },
  { id: 'rwh', label: 'RWH & ground water' }
];
const SRC = [
  { key: 'municipal', label: 'Municipal' },
  { key: 'borewell', label: 'Borewell' },
  { key: 'tanker', label: 'Tanker' },
  { key: 'drinking', label: 'Drinking-water cans' },
  { key: 'other', label: 'Other source' }
];
const isNil = v => v === null || v === undefined;
const sum = a => a.reduce((s, v) => s + (isNil(v) ? 0 : v), 0);
const anyVal = a => a.some(v => !isNil(v));
const uniq = a => [...new Set(a.filter(Boolean))];
const KL = 'KL';

let alive = false;

function ensureCss() {
  if (document.getElementById('metrics-water-css')) return;
  document.head.appendChild(h('link', { id: 'metrics-water-css', rel: 'stylesheet', href: '/static/css/metrics-water.css' }));
}

export function destroy() { alive = false; }

// ---------- small helpers ----------

const pad = n => String(n).padStart(2, '0');
const fyStart = fy => 2000 + Number(String(fy).slice(2, 4));
const prevFy = fy => { const y = fyStart(fy) - 1; return `FY${pad(y % 100)}-${pad((y + 1) % 100)}`; };
/** 12 "YYYY-MM" keys of a FY, April first. */
function fyMonths(fy) {
  const y = fyStart(fy);
  return Array.from({ length: 12 }, (_, i) => { const m = ((i + 3) % 12) + 1; return `${m >= 4 ? y : y + 1}-${pad(m)}`; });
}
const monthNum = ym => Number(ym.slice(5, 7));
const unitSpan = t => h('span', { class: 'mw-unit' }, t);

/** Fill an attached element with a spinner, then build(data) (which may draw charts into it). */
async function fill(el, promise, build) {
  clear(el).appendChild(loading());
  try {
    const data = await promise;
    if (!alive) return;
    clear(el);
    build(data, el);
  } catch (err) {
    console.error(err);
    if (alive) clear(el).appendChild(errorBox(err));
  }
}

function box(parent, cls) { const el = h('div', { class: cls || null }); parent.appendChild(el); return el; }

/** A card with a title/subtitle; returns its body (attached, so charts can mount). */
function cardIn(parent, title, subtitle) {
  const body = h('div');
  parent.appendChild(h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('div', null, h('h3', null, title), subtitle ? h('p', null, subtitle) : null)), body));
  return body;
}

function mcard(label, value, unit, sub, tone) {
  return h('div', { class: 'mcard' },
    h('div', { class: 'mcard-value' + (tone ? ' ' + tone : '') }, value, unit ? h('small', null, unit) : null),
    h('div', { class: 'mcard-label' }, label),
    sub ? h('div', { class: 'mcard-sub' }, sub) : null);
}

function dl(rows, cls) {
  const items = rows.filter(r => !isNil(r[1]) && r[1] !== '');
  return h('dl', { class: cls || 'mw-kv' }, items.map(([k, v]) => [h('dt', null, k), h('dd', null, v)]));
}

// ---------- entry markers ----------

const COL_OF = [
  [/^fresh\.(municip|madc)/i, ['municipal', 'total']],
  [/^fresh\.borewell/i, ['borewell', 'total']],
  [/^fresh\.tanker/i, ['tanker', 'total']],
  [/^fresh\.water for drinking|^fresh\.drinking/i, ['drinking', 'total']],
  [/^fresh\./i, ['other', 'total']],
  [/^domestic\./i, ['domestic']],
  [/^industrial\./i, ['industrial']],
  [/^(sewage|process_out)\./i, ['reused', 'pct']]
];
/** Which table columns hold a value that was filled from "Only to Enter", and the cell labels per column. */
function filledCols(rec) {
  const out = {};
  (rec.filledFromEntry || []).forEach(label => {
    const hit = COL_OF.find(([re]) => re.test(label));
    const cols = hit ? hit[1] : ['month'];
    cols.forEach(c => { (out[c] = out[c] || []).push(label); });
  });
  return out;
}

// ---------- data checks (worked out from API values, shown as flags) ----------

function flagsFor(months) {
  const ml = r => monthLabel(r.month);
  const off = (total, parts) => !isNil(total) && anyVal(parts) && Math.abs(sum(parts) - total) > 0.5;
  const f = {
    entry: months.filter(r => r.source === 'entry'),
    over: months.filter(r => !isNil(r.reusePct) && r.reusePct > 100),
    sheet: months.filter(r => !isNil(r.reusePct) && !isNil(r.reusePctSheet) && Math.abs(r.reusePct - r.reusePctSheet) > 0.5),
    fresh: months.filter(r => off(r.fresh.total, SRC.map(s => r.fresh[s.key]))),
    sewage: months.filter(r => off(r.sewageReused.total, [r.sewageReused.garden, r.sewageReused.toilets, r.sewageReused.other])),
    process: months.filter(r => off(r.processReused.total, [r.processReused.ro, r.processReused.dm, r.processReused.other]))
  };
  const list = (rows, show) => rows.map(r => show ? `${ml(r)} (${show(r)})` : ml(r)).join(', ');
  const items = [];
  const add = (rows, text, show) => rows.length && items.push({ n: rows.length, text, months: list(rows, show) });
  add(f.entry, 'month(s) with at least one cell filled from the "Only to Enter" workbook (marked † in the table)');
  add(f.over, 'month(s) where treated water reused is above 100% of fresh water (the sheet\'s own formula, shown uncapped)', r => fmtPct(r.reusePct));
  add(f.sheet, 'month(s) where the reuse % recomputed by the API differs from the sheet\'s own cell by more than 0.5 points', r => `API ${fmtPct(r.reusePct)} vs sheet ${fmtPct(r.reusePctSheet)}`);
  add(f.fresh, 'month(s) where the sheet\'s fresh-water total does not equal the sum of its source rows', r => `total ${fmt(r.fresh.total)}, sources ${fmt(sum(SRC.map(s => r.fresh[s.key])))}`);
  add(f.sewage, 'month(s) where the treated-sewage-reused total does not equal garden + toilets + other', r => `total ${fmt(r.sewageReused.total)}, parts ${fmt(sum([r.sewageReused.garden, r.sewageReused.toilets, r.sewageReused.other]))}`);
  add(f.process, 'month(s) where the treated-process-reused total does not equal RO + DM + other', r => `total ${fmt(r.processReused.total)}, parts ${fmt(sum([r.processReused.ro, r.processReused.dm, r.processReused.other]))}`);
  return { f, items };
}

function flagsBox(months, extraNotes) {
  const { items } = flagsFor(months);
  const apiNotes = uniq(extraNotes);
  return h('details', { class: 'mw-flags', open: items.length > 0 || apiNotes.length > 0 },
    h('summary', null, `Flagged in this view (${items.length} data check${items.length === 1 ? '' : 's'}${apiNotes.length ? ', ' + apiNotes.length + ' API note' + (apiNotes.length === 1 ? '' : 's') : ''})`),
    h('ul', null,
      items.length ? items.map(i => h('li', null, h('strong', null, String(i.n)), ' ', i.text, '. ',
        h('details', null, h('summary', null, 'Show months'), h('span', { class: 'mw-months' }, i.months))))
        : h('li', null, 'No total/component mismatches or reuse above 100% found in this plant\'s monthly records.'),
      apiNotes.map(n => h('li', null, n)),
      h('li', null, 'Months where the calculation and entry workbooks disagree are not itemised by the API: the calculation workbook value is shown, the entry value is used only to fill blanks.')));
}

// ---------- render ----------

export async function render(container, ctx) {
  alive = true;
  ensureCss();
  const plant = ctx.plant;
  const params = ctx.params || {};
  const state = { view: VIEWS.some(v => v.id === params.wview) ? params.wview : 'consumption', fy: params.fy || null, fys: [] };
  const persist = () => { if (typeof ctx.setParams === 'function') ctx.setParams({ wview: state.view, fy: state.fy || undefined }); };
  const get = (path, p) => api.get(path, p);
  const monthlyP = get('/water/monthly', { plant });
  const yearlyP = get('/water/yearly', { plant });
  const reuseP = get('/water/reuse', { plant });
  const metersP = get('/water/meters');
  [monthlyP, yearlyP, reuseP, metersP].forEach(p => p.catch(() => {}));   // each block reports its own failure

  const root = h('div', { class: 'mw-root' });
  container.appendChild(root);
  const kpiBox = box(root);
  const bar = h('div', { class: 'mw-bar' });
  root.appendChild(bar);
  const sub = box(root, 'mw-stack');

  // ----- KPI cards -----
  fill(kpiBox, Promise.all([get('/water/overview'), monthlyP.catch(() => null), reuseP.catch(() => null), metersP.catch(() => null)]),
    ([ov, monthly, reuse, meters], el) => {
      const p = (ov.plants || []).find(x => x.entity === plant);
      if (!p) { el.appendChild(empty(`The overview has no entry for ${plant}.`)); return; }
      const lm = p.latestMonth, ytd = p.fyToDate || {}, latest = p.latest || {};
      const to = lm ? 'to ' + monthLabel(lm) : '';
      // same months of the previous FY, from the monthly records
      let cmp = null;
      if (monthly) {
        const by = Object.fromEntries(monthly.months.map(r => [r.month, r]));
        const cur = monthly.months.filter(r => r.fy === p.fy && !isNil(r.fresh.total));
        const pairs = cur.map(r => [r, by[`${Number(r.month.slice(0, 4)) - 1}-${r.month.slice(5)}`]]).filter(([, q]) => q && !isNil(q.fresh.total));
        if (pairs.length) {
          const a = sum(pairs.map(([r]) => r.fresh.total)), b = sum(pairs.map(([, q]) => q.fresh.total));
          cmp = { a, b, pct: change(a, b), n: pairs.length, from: pairs[0][0].month, to: pairs[pairs.length - 1][0].month, skipped: cur.length - pairs.length };
        }
      }
      const m = (meters && meters.plants || []).find(x => x.plant === plant);
      const pct = ytd.reusePct;
      const cards = [
        mcard(`Fresh water, ${monthLabel(lm)}`, fmt(latest.freshWater), KL, `Latest reported month for ${plant}`),
        mcard(`Fresh water, FY-to-date ${to}`, fmt(ytd.freshWater), KL, `${p.fy}, ${ytd.monthsReported} month${ytd.monthsReported === 1 ? '' : 's'} reported`),
        cmp ? mcard(`Change vs same months of ${prevFy(p.fy)}`, (cmp.pct >= 0 ? '+' : '') + fmt(cmp.pct, 1), '%',
          `${monthLabel(cmp.from)} to ${monthLabel(cmp.to)}: ${fmt(cmp.a)} vs ${fmt(cmp.b)} KL${cmp.skipped ? ` (${cmp.skipped} month(s) without last-year data left out)` : ''}`)
          : mcard('Change vs same months of last FY', '—', null, 'No matching months in the previous FY'),
        mcard(`Treated water reused, FY-to-date ${to}`, fmt(ytd.totalReused), KL,
          h('span', null, 'Reuse ', h('span', { class: 'mw-sub-strong' + (pct > 100 ? ' mw-over' : '') }, fmtPct(pct)), ` of fresh water; ${monthLabel(lm)} alone: ${fmtPct(latest.reusePct)} (${fmt(latest.totalReused)} KL)`)),
        m && !isNil(m.available)
          ? mcard('Water flow meters installed', fmt(m.available), 'installed', isNil(m.toBeInstalled) ? 'To be installed: not stated in the meter file' : `${fmt(m.toBeInstalled)} to be installed`)
          : mcard('Water flow meters', '—', null, m ? 'No counts in the meter file' : 'Meter file not available')
      ];
      el.appendChild(h('div', { class: 'grid kpi5' }, cards));
      if ([ytd.reusePct, latest.reusePct].some(v => v > 100)) {
        el.appendChild(h('div', { class: 'mw-note-line' }, h('strong', null, 'Reuse above 100%. '),
          `The sheet's own formula (${reuse && reuse.formula ? reuse.formula : 'treated water reused / fresh water x 100'}) can exceed 100 when more treated water is reused than fresh water supplied. It is shown as it is, not capped.`));
      }
    });

  // ----- sub-view chips + FY picker -----
  const chipEls = {};
  VIEWS.forEach(v => {
    chipEls[v.id] = h('button', { type: 'button', class: 'sub-tab', 'aria-pressed': 'false', onClick: () => { state.view = v.id; persist(); show(); } }, v.label);
    bar.appendChild(chipEls[v.id]);
  });
  const tools = h('div', { class: 'mw-bar-tools' });
  bar.appendChild(tools);

  function drawTools() {
    clear(tools);
    if ((state.view === 'consumption' || state.view === 'reuse') && state.fys.length) {
      const id = 'mw-fy-' + Math.random().toString(36).slice(2, 7);
      const opts = state.fys.map(f => ({ id: f.fy, label: `${f.fy}${f.n < 12 ? ` (${f.n} months)` : ''}` }));
      const sel = select(opts, state.fy, v => { state.fy = v; persist(); show(); }, { id, 'aria-label': 'Financial year' });
      tools.appendChild(h('label', { for: id }, 'Financial year'));
      tools.appendChild(sel);
    }
  }

  async function show() {
    VIEWS.forEach(v => { chipEls[v.id].classList.toggle('active', v.id === state.view); chipEls[v.id].setAttribute('aria-pressed', String(v.id === state.view)); });
    destroyCharts(sub);
    clear(sub);
    drawTools();
    const body = box(sub, 'mw-stack');
    try {
      if (state.view === 'consumption') await viewConsumption(body);
      else if (state.view === 'meters') viewMeters(body);
      else if (state.view === 'reuse') await viewReuse(body);
      else viewRwh(body);
    } catch (err) { console.error(err); if (alive) body.appendChild(errorBox(err)); }
  }

  // FY list from the monthly records (needed by the picker)
  try {
    const monthly = await monthlyP;
    const counts = {};
    monthly.months.forEach(r => { counts[r.fy] = (counts[r.fy] || 0) + 1; });
    state.fys = Object.keys(counts).sort().reverse().map(fy => ({ fy, n: counts[fy] }));
    if (!state.fys.some(f => f.fy === state.fy)) state.fy = state.fys.length ? state.fys[0].fy : null;
  } catch (err) { /* the sub-view that needs it shows the error */ }
  if (!alive) return;
  show();

  // ======================= Consumption =======================
  async function viewConsumption(body) {
    const top = box(body), mid = box(body), yr = box(body), tb = box(body), fl = box(body);
    await Promise.all([
      fill(top, monthlyP, (data, el) => {
        const fy = state.fy;
        if (!fy) { el.appendChild(empty('No monthly records for this plant.')); return; }
        const by = Object.fromEntries(data.months.map(r => [r.month, r]));
        const keys = fyMonths(fy), pkeys = fyMonths(prevFy(fy));
        const labels = keys.map(monthLabel);
        const series = SRC.map(s => ({ label: s.label, unit: KL, type: 'bar', stack: 'src', data: keys.map(k => by[k] ? by[k].fresh[s.key] : null) }))
          .filter(s => anyVal(s.data) && s.data.some(v => v));
        const prev = pkeys.map(k => by[k] ? by[k].fresh.total : null);
        if (anyVal(prev)) series.push({ label: `${prevFy(fy)} total fresh water`, unit: KL, type: 'line', data: prev, tone: 'muted', dashed: true, smooth: false });
        const b = cardIn(el, `Fresh water by source, ${fy}`, `${plant}, KL per month (Sustainability Calculation workbook); dashed line = same month of ${prevFy(fy)}, total of all sources`);
        if (!series.some(s => s.type === 'bar')) b.appendChild(empty(`No fresh-water volumes reported for ${plant} in ${fy}.`));
        else seriesChart(b, labels, series, { type: 'bar', stacked: true, yTitle: 'KL per month', height: 'tall' });
        el.appendChild(h('p', { class: 'mw-legend' }, 'Months not yet reported are left blank. Bars show the sheet\'s source rows; where they do not add up to its total, the month is listed under "Flagged in this view".'));
      }),
      fill(mid, monthlyP, (data, el) => {
        const fy = state.fy;
        if (!fy) return;
        const by = Object.fromEntries(data.months.map(r => [r.month, r]));
        const keys = fyMonths(fy);
        const dom = keys.map(k => by[k] ? by[k].domestic : null), ind = keys.map(k => by[k] ? by[k].industrial : null);
        const b = cardIn(el, `Domestic vs industrial use, ${fy}`, `${plant}, KL per month (group totals of the sheet; domestic includes canteen, toilets, garden)`);
        if (!anyVal(dom) && !anyVal(ind)) b.appendChild(empty(`No domestic or industrial use entered for ${plant} in ${fy}.`));
        else seriesChart(b, keys.map(monthLabel), [
          { label: 'Domestic use', type: 'bar', unit: KL, data: dom },
          { label: 'Industrial use', type: 'bar', unit: KL, data: ind }
        ], { type: 'bar', yTitle: 'KL per month' });
      }),
      fill(yr, yearlyP, (data, el) => {
        const years = ((data.entities || [])[0] || { years: [] }).years.filter(y => y.monthsReported > 0);
        const b = cardIn(el, 'Yearly fresh water since FY18-19', `${plant}, KL per financial year by source; years with fewer than 12 months reported are partial sums and say so`);
        if (!years.length) { b.appendChild(empty('No yearly totals for this plant.')); return; }
        const labels = years.map(y => y.monthsReported < 12 ? [y.fy, `${y.monthsReported} of 12 mo`] : y.fy);
        const series = SRC.map(s => ({ label: s.label, type: 'bar', stack: 'src', unit: KL, data: years.map(y => y.fresh[s.key]) })).filter(s => s.data.some(v => v));
        if (!series.length) { b.appendChild(empty('No source-wise volumes reported.')); return; }
        seriesChart(b, labels, series, { type: 'bar', stacked: true, yTitle: 'KL per FY', height: 'tall' });
        const partial = years.filter(y => y.monthsReported < 12);
        if (partial.length) el.appendChild(h('p', { class: 'mw-legend' }, 'Partial years: ' + partial.map(y => `${y.fy} (${y.monthsReported} months)`).join(', ') + '. Do not read their bars as full-year totals.'));
      }),
      fill(tb, monthlyP, (data, el) => {
        const fy = state.fy;
        if (!fy) return;
        const rows = data.months.filter(r => r.fy === fy);
        const b = cardIn(el, `Monthly table, ${fy}`, `${plant}, KL unless stated; reuse % = treated water reused / fresh water x 100`);
        const mark = (r, col, text, extra) => {
          const cols = filledCols(r);
          const t = cols[col];
          return [text, t ? h('span', { class: 'mw-mark', title: 'Filled from the "Only to Enter" workbook: ' + t.join('; ') }, '†') : null, extra];
        };
        const numCol = (key, label, get, col) => ({ key, label, num: true, render: r => { const v = get(r); return isNil(v) ? null : mark(r, col || key, fmt(v)); } });
        const cols = [
          { key: 'month', label: 'Month', render: r => mark(r, 'month', monthLabel(r.month)) },
          ...SRC.map(s => numCol(s.key, s.label, r => r.fresh[s.key])),
          {
            key: 'total', label: 'Fresh total', num: true,
            render: r => {
              if (isNil(r.fresh.total)) return null;
              const bad = anyVal(SRC.map(s => r.fresh[s.key])) && Math.abs(sum(SRC.map(s => r.fresh[s.key])) - r.fresh.total) > 0.5;
              return mark(r, 'total', fmt(r.fresh.total), bad ? h('span', { class: 'mw-mark', title: `The sheet's total differs from the sum of its source rows (${fmt(sum(SRC.map(s => r.fresh[s.key])))} KL)` }, '≠') : null);
            }
          },
          numCol('domestic', 'Domestic', r => r.domestic),
          numCol('industrial', 'Industrial', r => r.industrial),
          numCol('reused', 'Reused', r => r.totalReused),
          {
            key: 'pct', label: 'Reuse %', num: true,
            render: r => isNil(r.reusePct) ? null : mark(r, 'pct', h('span', { class: r.reusePct > 100 ? 'mw-over' : null }, fmtPct(r.reusePct)), r.reusePct > 100 ? h('span', { class: 'mw-mark over', title: 'Above 100%: more treated water reused than fresh water supplied (the sheet\'s formula)' }, '▲') : null)
          }
        ];
        b.appendChild(table(cols, rows, { emptyText: 'No months reported in this FY.' }));
        b.appendChild(h('p', { class: 'mw-legend' }, h('span', { class: 'mw-mark' }, '†'), ' value filled from the "Only to Enter" workbook   ', h('span', { class: 'mw-mark over' }, '▲'), ' reuse above 100%   ', h('span', { class: 'mw-mark' }, '≠'), ' sheet total differs from its rows. A dash = not entered.'));
      })
    ]);
    const [monthly, yearly] = await Promise.all([monthlyP.catch(() => null), yearlyP.catch(() => null)]);
    fl.appendChild(monthly ? flagsBox(monthly.months, [...(monthly.notes || []), ...(yearly ? yearly.notes || [] : [])])
      : h('div', { class: 'mw-note-line' }, 'Data checks unavailable: monthly records could not be loaded.'));
  }

  // ======================= Flow meters =======================
  function viewMeters(body) {
    const selBox = box(body), stripBox = box(body), nb = box(body);
    fill(selBox, metersP, (data, el) => {
      const m = (data.plants || []).find(x => x.plant === plant);
      const b = cardIn(el, `Water flow meters at ${plant}`, 'Counts by status and type from "Water Flow Meter Status.xlsx" (one tile = one meter)');
      b.appendChild(h('div', { class: 'mw-note-line' }, h('strong', null, 'Locations not in the source. '), 'The meter file is plant-level only: it gives counts by status and type, not where each meter sits, so this is a count layout, not a floor plan.'));
      if (!m) { b.appendChild(empty(`${plant} is not in the meter file.`)); return; }
      if (isNil(m.available) && isNil(m.toBeInstalled)) {
        b.appendChild(h('div', { class: 'empty', style: { marginTop: '12px' } }, 'No counts in the meter file for this plant.',
          m.circuitDiagramShared ? h('div', { class: 'small' }, `The file only records "water circuit diagram shared: ${m.circuitDiagramShared}".`) : null));
        return;
      }
      b.appendChild(h('div', { class: 'mw-legend-row' },
        h('span', null, h('i', { class: 'mw-tile avail' }, 'D'), 'solid = available (existing)'),
        h('span', null, h('i', { class: 'mw-tile tbi' }, 'D'), 'dashed outline = to be installed'),
        h('span', null, h('i', { class: 'mw-tile analog avail' }, 'A'), 'round "A" = analog'),
        h('span', null, h('i', { class: 'mw-tile avail' }, 'D'), 'square "D" = digital'),
        h('span', null, h('i', { class: 'mw-tile unspecified avail' }, '?'), 'dotted "?" = type not stated')));
      const typeName = { analog: 'Analog', digital: 'Digital', unspecified: 'Type not stated' };
      const glyph = { analog: 'A', digital: 'D', unspecified: '?' };
      const groups = m.meterGroups || [];
      ['available', 'to_be_installed'].forEach(status => {
        const gs = groups.filter(g => g.status === status && g.count > 0);
        const total = status === 'available' ? m.available : m.toBeInstalled;
        if (isNil(total) && !gs.length) {
          b.appendChild(h('div', { class: 'mw-group' }, h('h4', null, 'To be installed'), h('p', { class: 'muted small' }, 'Not stated in the meter file.')));
          return;
        }
        b.appendChild(h('div', { class: 'mw-group' },
          h('h4', null, h('span', { class: 'mw-count' }, fmt(total)), status === 'available' ? 'available (existing meters)' : 'to be installed'),
          gs.length ? gs.map(g => h('div', null,
            h('div', { class: 'small muted', style: { marginTop: '6px' } }, `${typeName[g.type] || g.type}: ${g.count}`),
            h('div', { class: 'mw-tiles' }, Array.from({ length: g.count }, () =>
              h('i', { class: `mw-tile ${g.type} ${status === 'available' ? 'avail' : 'tbi'}`, title: `${typeName[g.type] || g.type} meter, ${status === 'available' ? 'available' : 'to be installed'}` }, glyph[g.type] || '?')))))
            : h('p', { class: 'muted small' }, total === 0 ? 'None.' : 'No type breakdown.')));
      });
      const tone = { installed: 'good', to_be_installed: 'warn', not_applicable: null, unknown: null }[m.installationStatusNormalised];
      b.appendChild(dl([
        ['Installation status', m.installationStatus ? [m.installationStatus, ' ', badge(({ installed: 'Installed', to_be_installed: 'To be installed', not_applicable: 'Not applicable' })[m.installationStatusNormalised] || 'Not stated', tone || undefined)] : 'Not stated in the file'],
        ['Expected completion', m.expectedCompletion],
        ['Completion comment', m.expectedCompletionComment],
        ['Instrument types (as written)', m.instrumentType],
        ['Calibrated', m.calibrated ? m.calibrated + (m.calibrationDate ? ` (${m.calibrationDate})` : '') : null],
        ['Water circuit diagram shared', m.circuitDiagramShared],
        ['Budget proposed', m.budgetProposed],
        ['Budget allocated', m.budgetAllocated],
        ['Remarks', m.remarks],
        ['Working / not working', 'not recorded in the file']
      ], 'mw-dl'));
    });
    fill(stripBox, metersP, (data, el) => {
      const b = cardIn(el, 'All plants at a glance', `Water flow meters per plant${data.totals ? `: ${fmt(data.totals.available)} available, ${fmt(data.totals.toBeInstalled)} to be installed across the estate` : ''}`);
      const plants = data.plants || [];
      if (!plants.length) { b.appendChild(empty('No plants in the meter file.')); return; }
      b.appendChild(h('div', { class: 'mw-strip' }, plants.map(m => {
        const sel = m.plant === plant;
        const noCounts = isNil(m.available) && isNil(m.toBeInstalled);
        const head = h('div', { class: 'mw-phead' }, h('div', null, h('strong', null, m.plant), h('small', null, m.name || '')), sel ? badge('Selected', 'brand') : null);
        if (noCounts) return h('div', { class: 'mw-pcard none' + (sel ? ' sel' : '') }, head, h('div', { class: 'mw-pnum' }, 'No counts in the meter file'), m.circuitDiagramShared ? h('div', { class: 'mw-ptypes' }, `Circuit diagram shared: ${m.circuitDiagramShared}`) : null);
        const a = m.available || 0, t = m.toBeInstalled || 0, tot = a + t;
        const bt = m.byType || {};
        const types = [['Analog', bt.analog], ['Digital', bt.digital], ['Not stated', m.unspecifiedType]].filter(([, v]) => !isNil(v) && v > 0);
        return h('div', { class: 'mw-pcard' + (sel ? ' sel' : '') }, head,
          h('div', { class: 'mw-pnum' }, h('b', null, fmt(m.available)), ' available · ', h('b', null, isNil(m.toBeInstalled) ? '—' : fmt(m.toBeInstalled)), ' to install'),
          h('div', { class: 'mw-statbar', role: 'img', 'aria-label': `${m.plant}: ${a} available, ${isNil(m.toBeInstalled) ? 'to be installed not stated' : t + ' to be installed'}` },
            tot ? [h('i', { class: 'a', style: { width: (a / tot * 100) + '%' } }), h('i', { class: 't', style: { width: (t / tot * 100) + '%' } })] : null),
          h('div', { class: 'mw-ptypes' }, types.length ? types.map(([k, v]) => `${k} ${v}`).join(' · ') : 'Type split not stated'),
          m.installationStatus ? h('div', { class: 'mw-ptypes' }, 'Installation: ' + m.installationStatus + (m.expectedCompletion ? ` (expected ${m.expectedCompletion})` : '')) : null);
      })));
      b.appendChild(h('p', { class: 'mw-legend' }, 'Bar: green = available, hatched = to be installed (share of available + to be installed). No per-meter locations exist in the source file.'));
    });
    metersP.then(d => { if (alive) { const n = notes(d.notes); if (n) nb.appendChild(n); } }).catch(() => {});
  }

  // ======================= Reuse =======================
  async function viewReuse(body) {
    const sew = box(body), proc = box(body), pctBox = box(body), yrBox = box(body), nb = box(body);
    await Promise.all([
      fill(sew, monthlyP, (data, el) => {
        const fy = state.fy;
        if (!fy) { el.appendChild(empty('No monthly records for this plant.')); return; }
        const by = Object.fromEntries(data.months.map(r => [r.month, r]));
        const keys = fyMonths(fy);
        const parts = [['garden', 'Garden'], ['toilets', 'Toilet flushing'], ['other', 'Other uses']];
        const series = parts.map(([k, label]) => ({ label, type: 'bar', stack: 's', unit: KL, data: keys.map(m => by[m] ? by[m].sewageReused[k] : null) })).filter(s => s.data.some(v => v));
        const b = cardIn(el, `Treated sewage (STP) water reused, by use, ${fy}`, `${plant}, KL per month`);
        if (!series.length) b.appendChild(empty(`No treated-sewage reuse recorded for ${plant} in ${fy}.`));
        else seriesChart(b, keys.map(monthLabel), series, { type: 'bar', stacked: true, yTitle: 'KL per month' });
      }),
      fill(proc, monthlyP, (data, el) => {
        const fy = state.fy;
        if (!fy) return;
        const by = Object.fromEntries(data.months.map(r => [r.month, r]));
        const keys = fyMonths(fy);
        const parts = [['ro', 'RO'], ['dm', 'DM'], ['other', 'Other (direct saving)']];
        const series = parts.map(([k, label]) => ({ label, type: 'bar', stack: 'p', unit: KL, data: keys.map(m => by[m] ? by[m].processReused[k] : null) })).filter(s => s.data.some(v => v));
        const inc = keys.map(m => by[m] ? by[m].processIncoming.total : null);
        const b = cardIn(el, `Treated process (ETP/ZLD) water reused, by use, ${fy}`, `${plant}, KL per month; dashed line = process water sent for treatment`);
        if (!series.length) { b.appendChild(empty(`No treated process water reused for ${plant} in ${fy}.`)); return; }
        if (anyVal(inc)) series.push({ label: 'Incoming process water (ETP + ZLD)', type: 'line', unit: KL, data: inc, tone: 'muted', dashed: true, smooth: false });
        seriesChart(b, keys.map(monthLabel), series, { type: 'bar', stacked: true, yTitle: 'KL per month' });
      }),
      fill(pctBox, monthlyP, (data, el) => {
        const ms = data.months;
        const b = cardIn(el, 'Reuse % over time, every reported month', `${plant}: (treated sewage + treated process water reused) / fresh water x 100. Dashed line = 100% (reuse equals fresh water)`);
        if (!ms.some(r => !isNil(r.reusePct))) { b.appendChild(empty('No reuse % available.')); return; }
        seriesChart(b, ms.map(r => monthLabel(r.month)), [
          { label: 'Reuse %', unit: '%', data: ms.map(r => r.reusePct), points: false, smooth: false, tone: 'good', fill: true },
          targetSeries('100% = reused equals fresh water', 100, ms.length, 'muted')
        ], { yTitle: 'Reuse, % of fresh water', tickDigits: 0, height: 'tall', digits: 1 });
      }),
      fill(yrBox, reuseP, (data, el) => {
        const ys = (data.yearly || []).filter(y => y.monthsReported > 0);
        const wb = (data.workbookSummary || {})['Total Reuse water %age'] || {};
        const b = cardIn(el, 'Reuse by financial year', `${plant}: KL reused and reuse %; "workbook summary" = the Water Summary sheet's own yearly %, for comparison`);
        if (!ys.length) { b.appendChild(empty('No yearly reuse figures.')); return; }
        const labels = ys.map(y => y.monthsReported < 12 ? [y.fy, `${y.monthsReported} of 12 mo`] : y.fy);
        const chartBox = box(b);
        seriesChart(chartBox, labels, [
          { label: 'Reuse % (this view, from monthly data)', type: 'bar', unit: '%', data: ys.map(y => y.reusePct) },
          { label: 'Reuse % (workbook Water Summary sheet)', type: 'line', unit: '%', data: ys.map(y => (isNil(wb[y.fy]) ? null : wb[y.fy])), smooth: false, tone: 'warn' }
        ], { type: 'bar', yTitle: 'Reuse, % of fresh water', digits: 1 });
        b.appendChild(table([
          { key: 'fy', label: 'FY', render: y => y.monthsReported < 12 ? `${y.fy} (${y.monthsReported} mo)` : y.fy },
          { key: 'f', label: 'Fresh water (KL)', num: true, render: y => isNil(y.freshTotal) ? null : fmt(y.freshTotal) },
          { key: 's', label: 'STP reused (KL)', num: true, render: y => isNil(y.sewageReused.total) ? null : fmt(y.sewageReused.total) },
          { key: 'p', label: 'Process reused (KL)', num: true, render: y => isNil(y.processReused.total) ? null : fmt(y.processReused.total) },
          { key: 't', label: 'Total reused (KL)', num: true, render: y => isNil(y.totalReused) ? null : fmt(y.totalReused) },
          { key: 'r', label: 'Reuse %', num: true, render: y => isNil(y.reusePct) ? null : h('span', { class: y.reusePct > 100 ? 'mw-over' : null }, fmtPct(y.reusePct)) },
          { key: 'w', label: 'Workbook %', num: true, render: y => isNil(wb[y.fy]) ? null : fmtPct(wb[y.fy]) }
        ], ys.slice().reverse()));
        b.lastChild.style.marginTop = '12px';
        const diffNote = (data.notes || []);
        { const n = notes([...(diffNote), ...(plant === 'TASL-NGP' ? ['The Water Summary sheet\'s own TASL-NGP block mixes its sewage and process rows and shows 0 for FY19-20 to FY21-22; the workbook % can therefore differ from this view (API documentation).'] : [])]); if (n) nb.appendChild(n); }
      })
    ]);
    monthlyP.then(d => { if (alive) { const n = notes(d.notes); if (n) nb.appendChild(n); } }).catch(() => {});
  }

  // ======================= RWH, ground water, per-capita =======================
  function viewRwh(body) {
    const useBox = box(body), pitBox = box(body), estBox = box(body), gwBox = box(body), pcBox = box(body), nb = box(body);
    const rwhP = get('/water/rwh'), gwP = get('/water/groundwater'), pcP = get('/water/percapita');
    [rwhP, gwP, pcP].forEach(p => p.catch(() => {}));
    const allNotes = [];
    const collect = p => p.then(d => { (d.notes || []).forEach(n => allNotes.push(n)); }).catch(() => {});
    fill(useBox, rwhP, (data, el) => {
      const r = (data.rainwaterUsedForGarden || {}).plants || {};
      const own = r[plant];
      const b = cardIn(el, 'Rainwater used for garden', `${plant}, KL: the master sheet's "RWH for Garden" row (entered use; no rainwater volume is metered in the workbooks)`);
      if (!own) { b.appendChild(empty(`${plant} has no "RWH for Garden" row in the water workbook (only TASL-MCA and TASL-NGP have one).`)); return; }
      const keys = Object.keys(own.monthly || {}).sort();
      if (!keys.length) { b.appendChild(empty('Row exists but holds no values.')); return; }
      seriesChart(b, keys.map(monthLabel), [{ label: own.label || 'RWH for Garden', type: 'bar', unit: KL, data: keys.map(k => own.monthly[k]) }], { type: 'bar', yTitle: 'KL per month', legend: true });
      b.appendChild(table([
        { key: 'fy', label: 'FY' }, { key: 'v', label: 'Used for garden (KL)', num: true, render: x => fmt(x.v) }
      ], Object.entries(own.yearly || {}).sort().reverse().map(([fy, v]) => ({ fy, v }))));
      b.lastChild.style.marginTop = '12px';
      b.appendChild(h('p', { class: 'mw-legend' }, 'Yearly sums cover the months present in the sheet (TASL-MCA values start 2025-01).'));
    });
    fill(pitBox, rwhP, (data, el) => {
      const row = (data.pits || []).find(x => x.plant === plant);
      const b = cardIn(el, 'Rain-water harvesting pits', `${plant}: "Rainwater harvesting1" sheet, values as typed`);
      if (!row) { b.appendChild(empty(`${plant} has no row in the pits sheet.`)); return; }
      const flag = v => (isNil(v) ? null : `${v} (sheet flag, 0/1)`);
      const rows = [
        ['Pits', row.pits], ['Dimensions', row.dimensions], ['Last maintenance', row.lastMaintenance], ['Latest maintenance date', row.latestMaintenanceDate],
        ['Condition, Jun 2024', row.conditionJun2024], ['Condition, Dec 2024', row.conditionDec2024], ['Yield / water-table height', row.yieldWaterTableHeight],
        ['Pits in OK condition', flag(row.pitsConditionOk)], ['Additional pits proposed', flag(row.additionalPitsProposed)], ['Additional pits proposed (2)', flag(row.additionalPitsProposed2)],
        ['Flow meter measurement', row.flowMeterMeasurement], ['Remarks', row.remarks]
      ];
      if (!rows.some(r => !isNil(r[1]))) b.appendChild(empty('The row has no values.'));
      else b.appendChild(dl(rows));
    });
    fill(estBox, rwhP, (data, el) => {
      const ec = data.estimatedCollection || {};
      const e = (ec.plants || {})[plant];
      const b = cardIn(el, 'Estimated rainwater reaching the pits', `${plant}: "Only to Enter > RWH" (rainfall x area x runoff coefficient), values exactly as stored, no conversion`);
      b.appendChild(h('div', { class: 'mw-note-line' }, h('strong', null, 'Units are inconsistent. '), `${ec.unit || 'Units as labelled in the sheet'}. FY26-27 values are about 1000 times FY25-26 ones, so they are not charted across years or converted.`));
      if (!e) { b.appendChild(empty(`No estimate for ${plant}.`)); return; }
      const fysE = Object.keys(e).sort().reverse();
      const mk = Array.from({ length: 12 }, (_, i) => ((i + 3) % 12) + 1);
      const cols = [{ key: 'fy', label: 'FY' }, ...mk.map((m, i) => ({ key: 'm' + i, label: monthLabel(`2000-${pad(m)}`).slice(0, 3), num: true, render: r => { const v = r.months[i]; return isNil(v) ? null : fmt(v, 1); } })),
        { key: 'tot', label: 'Sheet total', num: true, render: r => (isNil(r.total) ? null : fmt(r.total, 1)) }];
      const rows = fysE.map(fy => ({ fy, total: e[fy].sheetTotal, months: fyMonths(fy).map(k => (e[fy].months || {})[k]) }));
      b.appendChild(h('div', { style: { marginTop: '12px' } }, table(cols, rows)));
    });
    fill(gwBox, gwP, (data, el) => {
      const row = (data.plants || []).find(x => x.plant === plant);
      const b = cardIn(el, 'Ground-water level', `${plant}: yearly average borewell water level in feet (Ground Water sheet); line = state rainfall in mm`);
      if (!row) { b.appendChild(empty(`${plant} is not listed in the Ground Water sheet.`)); return; }
      const fys = Object.keys(row.level || {}).filter(fy => !isNil(row.level[fy]));
      if (!fys.length) {
        b.appendChild(empty(`No ground-water levels for ${plant}: the sheet marks it "NA" (no borewell inside the plant).${row.remark ? ' Sheet remark: ' + row.remark : ''}`));
        return;
      }
      seriesChart(b, fys, [
        { label: 'Average water level (feet)', type: 'bar', unit: 'ft', data: fys.map(fy => row.level[fy]) },
        { label: 'State rainfall (mm)', type: 'line', axis: 'right', unit: 'mm', smooth: false, tone: 'info', data: fys.map(fy => (data.rainfallMm || {})[fy] ?? null) }
      ], { type: 'bar', yTitle: 'Water level (feet)', y1Title: 'Rainfall (mm)', height: 'short' });
      b.appendChild(h('div', { style: { marginTop: '12px' } }, table([
        { key: 'fy', label: 'FY' }, { key: 'l', label: 'Level (feet)', num: true, render: r => fmt(r.l, 1) },
        { key: 'r', label: 'Level / rainfall ratio', num: true, render: r => (isNil(r.r) ? null : fmt(r.r, 4)) }
      ], fys.map(fy => ({ fy, l: row.level[fy], r: (row.levelToRainfallRatio || {})[fy] })))));
      if (row.remark) b.appendChild(h('p', { class: 'mw-legend' }, 'Sheet remark: ' + row.remark));
    });
    fill(pcBox, pcP, (data, el) => {
      const p = (data.plants || {})[plant];
      const b = cardIn(el, '"Per-capita" water sheet', `${plant}: sheet "Percapita water consumption", as stored`);
      b.appendChild(h('div', { class: 'mw-note-line' }, h('strong', null, 'Not per-capita. '), 'This sheet holds yearly reuse and fresh-water volumes (KL), not per-capita values; no per-capita figure can be shown from the source files.'));
      if (!p) { b.appendChild(h('div', { style: { marginTop: '12px' } }, empty(`The sheet has no values for ${plant} (only TLMAL and TSAL are filled).`))); return; }
      const fys = uniq(Object.values(p).flatMap(o => Object.keys(o))).sort();
      const rows = Object.entries(p).map(([label, vals]) => ({ label, vals }));
      b.appendChild(h('div', { style: { marginTop: '12px' } }, table([
        { key: 'label', label: 'Row (sheet label)' },
        ...fys.map(fy => ({ key: fy, label: fy, num: true, render: r => { const v = r.vals[fy]; return isNil(v) ? null : /%/.test(r.label) ? fmt(v, 4) + ' (fraction)' : fmt(v, 1) + ' KL'; } }))
      ], rows)));
    });
    Promise.all([collect(rwhP), collect(gwP), collect(pcP)]).then(() => { if (alive) { const n = notes(uniq(allNotes)); if (n) nb.appendChild(n); } });
  }
}
