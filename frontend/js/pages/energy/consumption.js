// Energy consumption tab: power, diesel and emissions for TADL, a cluster or one plant.
//
// Everything shown comes from /api/energy/* (Sustainability Calculation workbook,
// gaps filled from the entry workbook). Nothing here is typed in or estimated.
// Sums made in this file (diesel for a cluster, emissions / revenue / headcount
// for a cluster, the like-for-like comparison) add up rows the API returned and
// are labelled as such.

import { api } from '../../api.js';
import { badge, card, change, clear, empty, errorBox, fmt, fmtPct, h, kpi, loading, monthLabel, notes, section, select, table, chips } from '../../ui.js';
import { destroyCharts, donutChart, seriesChart } from '../../charts.js';

// ------------------------------------------------------------------ constants

const SCOPE_GROUPS = [
  { id: 'TADL', label: 'TADL (all plants)' },
  { id: 'DP', label: 'DP cluster' },
  { id: 'ASSY', label: 'ASSY cluster' }
];
const SOURCES = [
  { key: 'grid', label: 'Grid (EB)', colour: '--c1' },
  { key: 'solar', label: 'In-house solar', colour: '--c3' },
  { key: 'purchased', label: 'Purchased renewable', colour: '--c2' },
  { key: 'dg', label: 'DG (diesel generator)', colour: '--c5' }
];
const DIESEL_USES = [
  { key: 'dg', label: 'DG' },
  { key: 'hwg', label: 'HWG' },
  { key: 'forklift', label: 'Forklift' },
  { key: 'other', label: 'Other' }
];
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

let token = 0;          // bumps on every redraw / destroy so late answers are dropped
let basis = 'primary';  // emissions basis shown: 'primary' (detailed block) or 'alt' (0.85 block)

export function destroy() { token++; }

function ensureCss() {
  if (document.getElementById('energy-consumption-css')) return;
  document.head.appendChild(h('link', { id: 'energy-consumption-css', rel: 'stylesheet', href: '/static/css/energy-consumption.css' }));
}

// ------------------------------------------------------------------ small helpers

const num = v => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const add = (a, b) => (a === null && b === null ? null : (a || 0) + (b || 0));
const isNum = v => typeof v === 'number' && Number.isFinite(v);

function fyStart(fy) { const m = /^FY(\d{2})-(\d{2})$/.exec(fy || ''); return m ? 2000 + Number(m[1]) : null; }
function fyMonths(fy) {
  const y = fyStart(fy);
  if (!y) return [];
  return Array.from({ length: 12 }, (_, i) => {
    const m = ((i + 3) % 12) + 1;
    return (m >= 4 ? y : y + 1) + '-' + String(m).padStart(2, '0');
  });
}
function prevFy(fy) {
  const y = fyStart(fy);
  if (!y) return null;
  const p = y - 1;
  return 'FY' + String(p % 100).padStart(2, '0') + '-' + String(y % 100).padStart(2, '0');
}
function shiftYear(ym, by) { return (Number(ym.slice(0, 4)) + by) + ym.slice(4); }
function monthShort(ym) { return MONTH_NAMES[Number(ym.slice(5, 7)) - 1]; }
const kwhToMwh = v => (isNum(v) ? v / 1000 : null);

/** Chart.js tick labels from charts.js are whole numbers; small values (KL) need decimals. */
function tickDigits(chart, digits) {
  const sc = chart.options.scales || {};
  const value = chart.options.indexAxis === 'y' ? sc.x : sc.y;
  [value, sc.y1].forEach(a => { if (a && a.ticks) a.ticks.callback = v => fmt(v, Number.isInteger(v) ? 0 : digits); });
  chart.update('none');
}

/** Load a block in place: spinner (with a hint about the slow first load), then content, or an error box. */
async function slot(box, mine, promises, build) {
  clear(box).appendChild(loading('Reading the Sustainability workbooks… the first request after a server start can take a few minutes.'));
  try {
    const data = await Promise.all(promises);
    if (mine !== token) return;
    clear(box);
    await build(box, ...data);
  } catch (err) {
    if (mine !== token) return;
    console.error(err);
    clear(box).appendChild(errorBox(err));
  }
}

function uniqueNotes(...lists) {
  const seen = new Set();
  const out = [];
  lists.flat().forEach(n => { if (n && !seen.has(n)) { seen.add(n); out.push(n); } });
  return out;
}

function flagsBox(title, items) {
  if (!items.length) return null;
  return h('div', { class: 'ec-flags' }, h('strong', null, title), h('ul', null, items.map(i => h('li', null, i))));
}

// ------------------------------------------------------------------ data model

/** Everything the API gives for the whole company, fetched once (the server caches too). */
function startFetching() {
  const g = (path) => api.get('/energy/' + path);
  return {
    monthly: g('monthly'),
    yearly: g('yearly'),
    diesel: g('diesel'),
    emissions: g('emissions'),
    revenue: g('revenue'),
    manpower: g('manpower'),
    intensity: g('intensity'),
    targets: g('targets')
  };
}

function makeModel(monthly, yearly) {
  const plants = monthly.plants.map(p => ({ id: p.plant, name: p.name, cluster: p.cluster }));
  return { plants, monthly, yearly };
}

function scopePlantIds(model, scope) {
  if (scope === 'TADL') return model.plants.map(p => p.id);
  if (scope === 'DP' || scope === 'ASSY') return model.plants.filter(p => p.cluster === scope).map(p => p.id);
  return [scope];
}

function scopeLabel(model, scope) {
  const hit = SCOPE_GROUPS.find(s => s.id === scope);
  if (hit) return hit.label;
  const p = model.plants.find(x => x.id === scope);
  return p ? (p.name && p.name !== p.id ? `${p.id} (${p.name})` : p.id) : scope;
}

function monthlyRows(model, scope) {
  if (model.monthly.clusters && model.monthly.clusters[scope]) return model.monthly.clusters[scope];
  const p = model.monthly.plants.find(x => x.plant === scope);
  return p ? p.months : [];
}

function yearlyRows(model, scope) {
  if (model.yearly.clusters && model.yearly.clusters[scope]) return model.yearly.clusters[scope];
  const p = model.yearly.plants.find(x => x.plant === scope);
  return p ? p.years : [];
}

function fyOptions(model, scope) {
  return yearlyRows(model, scope).filter(y => y.total > 0 || y.monthsReported > 0).map(y => y.fy).sort();
}

/** Per plant: which months of the FY it reported. Used to say "to Oct-26" and who stops early / starts late. */
function periodInfo(model, scope, fy) {
  const ids = scopePlantIds(model, scope);
  const wanted = fyMonths(fy);
  const per = ids.map(id => {
    const p = model.monthly.plants.find(x => x.plant === id);
    const months = p ? p.months.filter(r => r.fy === fy).map(r => r.month).sort() : [];
    return { id, months, first: months[0] || null, last: months[months.length - 1] || null, count: months.length };
  });
  const lastAll = per.map(p => p.last).filter(Boolean).sort().pop() || null;
  const firstAll = per.map(p => p.first).filter(Boolean).sort()[0] || null;
  const maxCount = Math.max(0, ...per.map(p => p.count));
  const partial = lastAll !== null && lastAll !== wanted[11];
  return { per, lastAll, firstAll, maxCount, partial };
}

function toText(info) {
  if (!info.lastAll) return 'no months reported';
  if (info.partial) return `year to date, to ${monthLabel(info.lastAll)}`;
  return info.maxCount < 12 ? `${info.maxCount} months reported` : 'full year';
}

/** Like-for-like comparison with the previous FY: only (plant, month) pairs present in both years. */
function likeForLike(model, scope, fy) {
  const ids = scopePlantIds(model, scope);
  const cur = { grid: 0, solar: 0, dg: 0, purchased: 0, total: 0 };
  const prev = { grid: 0, solar: 0, dg: 0, purchased: 0, total: 0 };
  const months = new Set();
  let pairs = 0;
  ids.forEach(id => {
    const p = model.monthly.plants.find(x => x.plant === id);
    if (!p) return;
    const idx = new Map(p.months.map(r => [r.month, r]));
    fyMonths(fy).forEach(m => {
      const a = idx.get(m), b = idx.get(shiftYear(m, -1));
      if (!a || !b) return;
      pairs++;
      months.add(m);
      Object.keys(cur).forEach(k => { cur[k] += a[k] || 0; prev[k] += b[k] || 0; });
    });
  });
  return { cur, prev, pairs, months: [...months].sort(), prevFy: prevFy(fy) };
}

/** Month rows of the power workbook grouped by month for the scope's plants, with their flags. */
function powerFlags(model, scope, fy) {
  const ids = scopePlantIds(model, scope);
  const byMonth = new Map();
  ids.forEach(id => {
    const p = model.monthly.plants.find(x => x.plant === id);
    if (!p) return;
    p.months.filter(r => r.fy === fy).forEach(r => {
      const e = byMonth.get(r.month) || { outlier: [], entry: [], sheet: [] };
      if (r.flag === 'outlier') e.outlier.push(id);
      if (r.source === 'entry') e.entry.push(id);
      if (r.workbookTotal !== undefined && r.workbookTotal !== null) e.sheet.push({ id, value: r.workbookTotal, total: r.total });
      byMonth.set(r.month, e);
    });
  });
  return byMonth;
}

function badgesFor(flags, single) {
  if (!flags) return null;
  const out = [];
  const suffix = ids => (single ? '' : ' · ' + ids.join(', '));
  if (flags.outlier.length) {
    const b = badge('outlier' + suffix(flags.outlier), 'warn');
    b.title = 'Flagged by the API: more than 8x or less than 1/8 of the plant\'s median for this FY. Shown as in the sheet.';
    out.push(b);
  }
  if (flags.entry.length) {
    const b = badge('entry workbook' + suffix(flags.entry), 'info');
    b.title = 'Empty in the Sustainability Calculation workbook; value taken from the entry workbook.';
    out.push(b);
  }
  if (flags.sheet.length) {
    const b = badge('sheet total differs' + suffix(flags.sheet.map(s => s.id)), 'bad');
    b.title = flags.sheet.map(s => `${s.id}: workbook total ${fmt(s.value)} kWh, sum of rows ${fmt(s.total)} kWh`).join('\n');
    out.push(b);
  }
  return out.length ? h('span', { class: 'ec-badges' }, out) : null;
}

// ----- diesel (per month, summed over the scope's plants when the scope is a cluster)

function dieselMonths(diesel, ids, fy) {
  const map = new Map();
  diesel.plants.filter(p => ids.includes(p.plant)).forEach(p => {
    p.months.filter(r => r.fy === fy).forEach(r => {
      const e = map.get(r.month) || { month: r.month, dg: null, hwg: null, forklift: null, other: null, total: null, n: 0, outlier: [], entry: [] };
      DIESEL_USES.forEach(u => { e[u.key] = add(e[u.key], num(r[u.key])); });
      e.total = add(e.total, num(r.total));
      e.n++;
      if (r.flag === 'outlier') e.outlier.push(p.plant);
      if (r.source === 'entry') e.entry.push(p.plant);
      map.set(r.month, e);
    });
  });
  return [...map.values()].sort((a, b) => a.month.localeCompare(b.month));
}

// ----- per-FY figures that the API gives per plant and for TADL, summed here for clusters

function fyValue(rowsByPlant, ids, fy, pick) {
  // rowsByPlant: Map plantId -> array of year rows. Returns { value, n } (n = plants that had a value).
  let value = null, n = 0;
  ids.forEach(id => {
    const row = (rowsByPlant.get(id) || []).find(r => r.fy === fy);
    const v = row ? num(pick(row)) : null;
    if (v !== null) { value = (value || 0) + v; n++; }
  });
  return { value, n };
}

function byPlant(list, key) {
  return new Map(list.map(p => [p.plant, p[key] || []]));
}

function emissionRow(model, emissions, scope, fy) {
  const map = byPlant(emissions.plants, 'years');
  if (map.has(scope)) return { row: (map.get(scope) || []).find(r => r.fy === fy) || null, n: 1, expected: 1, summed: false };
  const ids = scopePlantIds(model, scope);
  const keys = ['scope1', 'scope2Gross', 'scope2IfGridInsteadOfSolar', 'afforestationBenefit', 'scope2Savings', 'scope2Basis', 'renewableBenefitBasis', 'netCarbonBasis'];
  const row = { fy };
  let n = 0;
  keys.forEach(k => {
    const r = fyValue(map, ids, fy, x => x[k]);
    row[k] = r.value;
    if (k === 'scope2Gross') n = r.n;
  });
  return { row: n ? row : null, n, expected: ids.length, summed: true };
}

function emissionYears(model, emissions, scope) {
  const map = byPlant(emissions.plants, 'years');
  const ids = map.has(scope) ? [scope] : scopePlantIds(model, scope);
  return [...new Set(ids.flatMap(id => (map.get(id) || []).map(r => r.fy)))].sort();
}

// ------------------------------------------------------------------ render

export async function render(root, ctx) {
  ensureCss();
  const mine = ++token;
  const fetched = startFetching();
  const params = ctx.params || {};

  const embedded = !!(ctx.embedded && ctx.plant);
  const state = { scope: embedded ? ctx.plant : (params.scope || 'TADL'), fy: params.fy || null, embedded };
  const controls = h('div', { class: 'ec-controls' });
  const body = h('div');
  root.append(controls, body);

  clear(body).appendChild(loading('Reading the Sustainability workbooks… the first request after a server start can take a few minutes.'));

  let model;
  try {
    const [monthly, yearly] = await Promise.all([fetched.monthly, fetched.yearly]);
    if (mine !== token) return;
    model = makeModel(monthly, yearly);
  } catch (err) {
    if (mine !== token) return;
    clear(body).appendChild(errorBox(err));
    return;
  }

  const validScopes = [...SCOPE_GROUPS.map(s => s.id), ...model.plants.map(p => p.id)];
  if (!validScopes.includes(state.scope)) {
    if (embedded) { clear(body).appendChild(empty(`Unknown plant "${state.scope}".`)); return; }
    state.scope = 'TADL';
  }

  function settleFy() {
    const options = fyOptions(model, state.scope);
    if (!options.includes(state.fy)) state.fy = options[options.length - 1] || null;
    return options;
  }

  function writeUrl() {
    if (embedded) return;   // the hosting page owns the address
    const q = new URLSearchParams({ tab: 'consumption', scope: state.scope });
    if (state.fy) q.set('fy', state.fy);
    history.replaceState(null, '', '#/energy?' + q.toString());
  }

  function drawControls() {
    const options = settleFy();
    const scopeOptions = [...SCOPE_GROUPS, ...model.plants.map(p => ({ id: p.id, label: p.id }))];
    const picker = chips(scopeOptions, state.scope, id => { state.scope = id; drawControls(); drawBody(); });
    [...picker.el.children].forEach((b, i) => {
      const o = scopeOptions[i];
      const p = model.plants.find(x => x.id === o.id);
      b.title = p ? `${p.name} (${p.cluster} cluster)` : o.label;
      b.setAttribute('aria-label', p ? `${p.id}, ${p.name}` : o.label);
    });
    const fySelect = select(options.map(f => ({ id: f, label: f })), state.fy, v => { state.fy = v; writeUrl(); drawBody(); }, { 'aria-label': 'Financial year', id: 'ec-fy' });
    clear(controls).append(
      embedded ? null : h('div', { class: 'field' }, 'Scope', picker.el),
      h('label', { class: 'field' }, 'Financial year', fySelect));
  }

  function drawBody() {
    const run = ++token;
    writeUrl();
    destroyCharts(body);
    clear(body);
    if (!state.fy) { body.appendChild(empty('The API has no reported months for this scope.')); return; }
    drawSections(body, run, model, fetched, state);
  }

  drawControls();
  drawBody();
}

// ------------------------------------------------------------------ sections

function drawSections(body, mine, model, fetched, state) {
  const { scope, fy, embedded } = state;
  const ids = scopePlantIds(model, scope);
  const single = ids.length === 1;
  const info = periodInfo(model, scope, fy);
  const pf = prevFy(fy);
  const label = scopeLabel(model, scope);
  const yr = yearlyRows(model, scope).find(y => y.fy === fy) || null;

  // ---- period statement
  const lines = [];
  info.per.forEach(p => {
    if (!p.count) lines.push(`${p.id}: no months reported in ${fy}.`);
    else if (p.count < info.maxCount || (p.last !== info.lastAll && !single)) {
      lines.push(`${p.id}: ${monthLabel(p.first)} to ${monthLabel(p.last)} (${p.count} month${p.count > 1 ? 's' : ''}).`);
    } else if (p.first !== fyMonths(fy)[0] && p.count < 12 && !info.partial) {
      lines.push(`${p.id}: starts ${monthLabel(p.first)} (${p.count} months).`);
    }
  });
  body.appendChild(h('div', { class: 'ec-period' },
    h('strong', null, label), ` · ${fy} · `, h('strong', null, toText(info)),
    info.lastAll ? ` (${monthLabel(info.firstAll)} to ${monthLabel(info.lastAll)})` : '',
    lines.length ? h('ul', null, lines.map(l => h('li', null, l))) : null));

  // ---- headline KPIs: energy
  const kpiBox = h('div');
  if (!embedded) body.appendChild(section('Energy consumption', `${label}, ${fy} (${toText(info)}). Power from the "Energy consumption data" sheet, in kWh.`, kpiBox));
  else body.appendChild(kpiBox);
  slot(kpiBox, mine, [fetched.monthly, fetched.yearly], (box, monthly, yearly) => {
    if (!yr) { box.appendChild(empty(`No power data for ${label} in ${fy}.`)); return; }
    const cmp = likeForLike(model, scope, fy);
    if (embedded) { kpiTail(box, monthly, yearly); return; }
    const d = (k, good) => {
      const v = cmp.pairs ? change(cmp.cur[k], cmp.prev[k]) : null;
      return v === null ? null : { value: v, goodWhen: good, label: `vs ${cmp.prevFy}, same months` };
    };
    const share = share2 => (share2 === null ? null : share2 * 100);
    const tiles = [
      kpi({ label: 'Total energy', value: fmt(yr.total / 1e6, 2), unit: 'GWh', sub: `${fmt(yr.total)} kWh, ${fy} ${toText(info)}`, delta: d('total', 'down') }),
      ...SOURCES.map(s => kpi({
        label: s.label, value: fmt(yr[s.key] / 1e6, 2), unit: 'GWh',
        sub: `${fmtPct(yr.total ? (yr[s.key] / yr.total) * 100 : null)} of total · ${fmt(yr[s.key])} kWh`,
        delta: d(s.key, s.key === 'solar' || s.key === 'purchased' ? 'up' : 'down'),
        tone: s.key === 'dg' ? 'warn' : (s.key === 'grid' ? null : 'good')
      })),
      kpi({
        label: 'Renewable share', value: fmtPct(share(num(yr.renewableShare))), tone: 'good',
        sub: '(solar + purchased) ÷ total' + (cmp.pairs && cmp.prev.total ? ` · ${cmp.prevFy} same months: ${fmtPct(((cmp.prev.solar + cmp.prev.purchased) / cmp.prev.total) * 100)}` : '')
      })
    ];
    box.appendChild(h('div', { class: 'grid kpis' }, tiles));
    box.appendChild(h('p', { class: 'ec-line' }, cmpLineOf(cmp)));
    kpiTail(box, monthly, yearly);
  });
  function cmpLineOf(cmp) {
    return cmp.pairs
      ? `Change is like-for-like against ${cmp.prevFy}: only plant-months reported in both years (${cmp.pairs} plant-months, ${cmp.months.length ? monthLabel(cmp.months[0]) + ' to ' + monthLabel(cmp.months[cmp.months.length - 1]) : ''}); base ${fmt(cmp.cur.total / 1e6, 2)} GWh now against ${fmt(cmp.prev.total / 1e6, 2)} GWh then.`
      : `No month is reported in both ${fy} and ${pf}, so no like-for-like change can be shown.`;
  }
  function kpiTail(box, monthly, yearly) {
    if (!single && yr.fewestPlantsReporting !== undefined) {
      box.appendChild(h('p', { class: 'ec-line' }, `Roll-up of plant rows computed by the API; ${yr.monthsReported} month(s) reported, with as few as ${yr.fewestPlantsReporting} of ${ids.length} plants reporting in some months.`));
    }
    const flags = [];
    const pf2 = powerFlags(model, scope, fy);
    [...pf2.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([m, e]) => {
      e.outlier.forEach(id => flags.push(`${id} ${monthLabel(m)}: flagged outlier (more than 8x or less than 1/8 of the plant's median for the FY, possibly a unit slip). Shown as in the sheet and included in the totals.`));
      if (e.entry.length) flags.push(`${e.entry.join(', ')} ${monthLabel(m)}: value filled from the entry workbook (empty in the Sustainability Calculation workbook).`);
      e.sheet.forEach(s => flags.push(`${s.id} ${monthLabel(m)}: the sheet's own total is ${fmt(s.value)} kWh but its rows add up to ${fmt(s.total)} kWh (the rows are used here).`));
    });
    const fb = flagsBox(`Flagged in this view (${fy})`, flags);
    if (fb) box.appendChild(fb);
    const n = notes(uniqueNotes(monthly.notes, yearly.notes));
    if (n) box.appendChild(n);
  }

  // ---- headline KPIs: emissions, revenue, people
  if (!embedded) {
  const kpi2Box = h('div');
  body.appendChild(section('Emissions, revenue and headcount', `${label}, ${fy}. Emissions are the workbook's own figures from the Sustainability Summary sheets.`, kpi2Box));
  slot(kpi2Box, mine, [fetched.emissions, fetched.revenue, fetched.manpower, fetched.intensity], (box, emissions, revenue, manpower, intensity) => {
    const em = emissionRow(model, emissions, scope, fy);
    const r = em.row;
    const tonnes = kg => (isNum(kg) ? kg / 1000 : null);
    const partialNote = em.summed && em.row && em.n < em.expected ? ` · sum of ${em.n} of ${em.expected} plants` : (em.summed ? ' · sum of plant rows' : '');
    const when = `${fy} ${toText(info)}`;
    const tiles = [];
    tiles.push(kpi({
      label: 'Scope 1 emissions', value: fmt(r ? r.scope1 : null, 1), unit: 't CO2',
      sub: r ? `diesel × factor · ${when}${partialNote}` : `not in the workbook for ${fy}`, tone: 'warn'
    }));
    tiles.push(kpi({
      label: 'Scope 2 emissions (gross)', value: fmt(r ? tonnes(r.scope2Gross) : null, 0), unit: 't CO2',
      sub: r && isNum(r.scope2Gross) ? `${fmt(r.scope2Gross)} kg CO2 · ${when}${partialNote}` : `not in the workbook for ${fy}`, tone: 'warn'
    }));
    tiles.push(kpi({
      label: 'Scope 2 savings', value: fmt(r ? tonnes(r.scope2Savings) : null, 0), unit: 't CO2',
      sub: r && isNum(r.scope2Savings) ? `renewables ${fmt(tonnes(r.scope2IfGridInsteadOfSolar), 0)} t + afforestation ${fmt(tonnes(r.afforestationBenefit), 0)} t · ${when}` : `not in the workbook for ${fy}`, tone: 'good'
    }));

    const revMap = byPlant(revenue.plants, 'years');
    const rev = revMap.has(scope)
      ? { value: num(((revMap.get(scope) || []).find(x => x.fy === fy) || {}).value ?? null), n: 1 }
      : fyValue(revMap, ids, fy, x => x.value);
    tiles.push(kpi({
      label: 'Revenue', value: fmt(rev.value, 0), unit: '₹ Cr',
      sub: rev.value === null ? `not in the workbook for ${fy}${fy === 'FY26-27' ? ' (year to date, not yet entered)' : ''}`
        : `${fy} (Summary sheet "Revenue" row)${!revMap.has(scope) ? ` · sum of ${rev.n} of ${ids.length} plants` : ''}`
    }));

    const inten = (intensity.plants.find(p => p.plant === scope) || {}).years;
    const iv = inten ? (inten.find(x => x.fy === fy) || {}).megaUnitsPerRevenue : null;
    tiles.push(kpi({
      label: 'Energy per ₹ Cr revenue', value: fmt(num(iv ?? null), 2), unit: 'MWh / ₹ Cr',
      sub: inten ? (isNum(iv) ? `${fy}, from the workbook's intensity rows` : `no ratio in the workbook for ${fy} (revenue missing)`) : 'the workbook has no cluster ratio'
    }));

    const mpMap = byPlant(manpower.plants, 'years');
    const head = mpMap.has(scope)
      ? { value: num(((mpMap.get(scope) || []).find(x => x.fy === fy) || {}).headcount ?? null), n: 1 }
      : fyValue(mpMap, ids, fy, x => x.headcount);
    let headSub = head.value === null ? `not in the workbook for ${fy}` : `${fy}, "Employees" row of the Summary sheet${!mpMap.has(scope) ? ` · sum of ${head.n} of ${ids.length} plants` : ''}`;
    if (mpMap.has(scope) && head.value !== null) {
      const before = ((mpMap.get(scope) || []).find(x => x.fy === pf) || {}).headcount;
      if (before === head.value) headSub += ` · same figure as ${pf}`;
    }
    tiles.push(kpi({ label: 'Headcount', value: fmt(head.value, 0), unit: 'people', sub: headSub }));

    box.appendChild(h('div', { class: 'grid kpis' }, tiles));

    // Emission factors as returned by the API.
    const f = emissions.factors || [];
    const cm = emissions.conversionMatrix || [];
    const factorTable = table(
      [{ key: 'appliesTo', label: 'Applies to' }, { key: 'factor', label: 'Factor', num: true, render: x => fmt(x.factor, 3) }, { key: 'unit', label: 'Unit as written in the sheet' }, { key: 'row', label: 'Summary row', num: true }],
      f, { emptyText: 'The API returned no emission factors.' });
    const cmTable = table(
      [{ key: 'energyType', label: 'Energy type' }, { key: 'factor', label: 'Factor', num: true, render: x => fmt(x.factor, 3) }, { key: 'unit', label: 'Unit' }],
      cm, { emptyText: 'The API returned no conversion matrix.' });
    box.appendChild(h('details', { class: 'ec-details' },
      h('summary', null, 'Emission factors used by the workbook (as returned by the API)'),
      h('p', { class: 'ec-table-title' }, 'Primary basis: detailed block of the Summary sheets (Scope 1, Scope 2 gross, savings)'),
      factorTable,
      h('p', { class: 'ec-table-title' }, 'Other basis: 0.85 block, "Conversion matrix" sheet'),
      cmTable));
    const n = notes(uniqueNotes(emissions.notes, revenue.notes, manpower.notes, intensity.notes));
    if (n) box.appendChild(n);
  });
  }

  // ---- monthly trend and mix
  const trendBox = h('div');
  body.appendChild(section('Monthly trend', `${label}, ${fy} against ${pf || 'the previous FY'}, total energy in MWh (kWh ÷ 1,000), Sustainability Calculation workbook.`, trendBox));
  slot(trendBox, mine, [fetched.monthly], (box) => {
    const rows = monthlyRows(model, scope);
    const idx = new Map(rows.map(r => [r.month, r]));
    const months = fyMonths(fy);
    const cur = months.map(m => kwhToMwh(num((idx.get(m) || {}).total ?? null)));
    const prv = months.map(m => kwhToMwh(num((idx.get(shiftYear(m, -1)) || {}).total ?? null)));
    const labels = months.map(monthShort);
    const holder = h('div');
    box.appendChild(card(`Total energy by month, ${fy} and ${pf}`, `MWh per month. A gap means the month is not reported${info.partial ? `; ${fy} runs to ${monthLabel(info.lastAll)}` : ''}.`, holder));
    if (cur.every(v => v === null) && prv.every(v => v === null)) {
      clear(holder).appendChild(empty('No monthly values for this scope in either year.'));
    } else {
      const chart = seriesChart(holder, labels, [
        { label: fy, data: cur, tone: 'brand', unit: 'MWh', fill: true },
        { label: pf, data: prv, tone: 'muted', dashed: true, unit: 'MWh' }
      ], { yTitle: 'MWh per month', legend: true, digits: 0 });
      tickDigits(chart, 1);
    }
    const partials = months.map(m => idx.get(m)).filter(r => r && r.plantsExpected && r.plantsReporting < r.plantsExpected);
    const partialPrev = months.map(m => idx.get(shiftYear(m, -1))).filter(r => r && r.plantsExpected && r.plantsReporting < r.plantsExpected);
    if (partials.length || partialPrev.length) {
      const txt = [...partials, ...partialPrev].map(r => `${monthLabel(r.month)} ${r.plantsReporting} of ${r.plantsExpected}`).join(', ');
      box.appendChild(h('p', { class: 'ec-line' }, `Months where fewer plants reported than expected (the roll-up is partial, so the line dips or lags): ${txt}.`));
    }
  });

  const mixBox = h('div', { class: 'grid split', style: { marginTop: '14px' } });
  body.appendChild(section('Source mix', `${label}, ${fy}: grid EB, in-house solar, purchased renewable and DG, in MWh.`, mixBox));
  slot(mixBox, mine, [fetched.monthly], (box) => {
    const rows = monthlyRows(model, scope);
    const idx = new Map(rows.map(r => [r.month, r]));
    const months = fyMonths(fy);
    const left = h('div'), right = h('div');
    box.append(card(`Energy by source and month, ${fy}`, 'Stacked, MWh per month', left), card(`Split for ${fy}`, yr ? `Share of ${fmt(yr.total)} kWh, ${toText(info)}` : '', right));
    const series = SOURCES.map(s => ({
      label: s.label, type: 'bar', stack: 'mix', tone: s.colour, unit: 'MWh',
      data: months.map(m => kwhToMwh(num((idx.get(m) || {})[s.key] ?? null)))
    }));
    if (series.every(s => s.data.every(v => v === null))) clear(left).appendChild(empty('No monthly values for this scope in this FY.'));
    else tickDigits(seriesChart(left, months.map(monthLabel), series, { type: 'bar', stacked: true, yTitle: 'MWh per month', legend: true }), 1);
    const slices = yr ? SOURCES.map(s => ({ label: s.label, value: yr[s.key], tone: s.colour })).filter(s => isNum(s.value) && s.value > 0) : [];
    if (!slices.length) clear(right).appendChild(empty('No source split for this scope in this FY.'));
    else donutChart(right, slices, { unit: 'kWh' });
  });

  // ---- long run
  const longBox = h('div');
  body.appendChild(section('Year by year', `${label}, every FY the API has, by source in MWh, with the renewable share (solar + purchased) on the right axis.`, longBox));
  slot(longBox, mine, [fetched.yearly], (box) => {
    const rows = yearlyRows(model, scope).slice().sort((a, b) => a.fy.localeCompare(b.fy));
    if (!rows.length) { box.appendChild(empty('No yearly values for this scope.')); return; }
    const holder = h('div');
    const first = rows[0].fy;
    box.appendChild(card('Energy by FY', `MWh per FY from ${first}. FYs marked "part" are year to date (months reported shown); FYs before a plant started are simply absent.`, holder));
    const labels = rows.map(r => r.fy + (r.partial ? ` (${r.monthsReported} mo, part)` : (r.monthsReported < 12 ? ` (${r.monthsReported} mo)` : '')));
    const series = SOURCES.map(s => ({ label: s.label, type: 'bar', stack: 'yr', tone: s.colour, unit: 'MWh', data: rows.map(r => kwhToMwh(num(r[s.key] ?? null))) }));
    series.push({ label: 'Renewable share', type: 'line', axis: 'right', tone: 'good', unit: '%', data: rows.map(r => (isNum(r.renewableShare) ? r.renewableShare * 100 : null)) });
    const chart = seriesChart(holder, labels, series, { type: 'bar', stacked: true, yTitle: 'MWh per FY', y1Title: '% renewable', legend: true, digits: 1 });
    tickDigits(chart, 1);
  });

  // ---- plant comparison
  if (!embedded) {
  const cmpBox = h('div');
  body.appendChild(section('Plants compared', `All plants, ${fy}, total energy by source in MWh. Plants report different numbers of months, so read the month counts before comparing.`, cmpBox));
  slot(cmpBox, mine, [fetched.yearly, fetched.revenue, fetched.intensity], (box, yearly, revenue, intensity) => {
    const rowsOf = id => (yearly.plants.find(p => p.plant === id) || {}).years || [];
    const list = model.plants.map(p => ({ p, y: rowsOf(p.id).find(r => r.fy === fy) || null }));
    const withData = list.filter(x => x.y);
    const holder = h('div');
    box.appendChild(card(`Energy by plant, ${fy}`, 'Horizontal stacked bars, MWh. Months reported in brackets.', holder));
    if (!withData.length) clear(holder).appendChild(empty(`No plant reported power in ${fy}.`));
    else {
      const labels = withData.map(x => `${x.p.id} (${x.y.monthsReported} mo)`);
      const series = SOURCES.map(s => ({ label: s.label, type: 'bar', stack: 'p', tone: s.colour, unit: 'MWh', data: withData.map(x => kwhToMwh(num(x.y[s.key] ?? null))) }));
      tickDigits(seriesChart(holder, labels, series, { type: 'bar', stacked: true, horizontal: true, legend: true, yTitle: 'MWh', height: 'tall' }), 1);
    }
    const revMap = byPlant(revenue.plants, 'years');
    const intMap = byPlant(intensity.plants, 'years');
    const tadl = (yearly.clusters.TADL || []).find(r => r.fy === fy) || null;
    const tableRows = [...list.map(x => ({ id: x.p.id, cluster: x.p.cluster, y: x.y })), { id: 'TADL', cluster: 'all', y: tadl }];
    const rows = tableRows.map(x => ({
      ...x,
      rev: ((revMap.get(x.id) || []).find(r => r.fy === fy) || {}).value ?? null,
      mwhCr: ((intMap.get(x.id) || []).find(r => r.fy === fy) || {}).megaUnitsPerRevenue ?? null
    }));
    box.appendChild(h('p', { class: 'ec-table-title' }, `Plant table, ${fy}`));
    box.appendChild(table([
      { key: 'id', label: 'Plant', render: r => h('span', null, r.id, r.id === scope ? h('span', { class: 'ec-badges' }, badge('selected', 'brand')) : null) },
      { key: 'cluster', label: 'Cluster' },
      { key: 'months', label: 'Months reported', num: true, render: r => (r.y ? r.y.monthsReported + (r.y.partial ? ' (part)' : '') : null) },
      { key: 'total', label: 'Total (kWh)', num: true, render: r => (r.y ? fmt(r.y.total) : null) },
      { key: 'share', label: 'Renewable share', num: true, render: r => (r.y ? fmtPct(num(r.y.renewableShare) !== null ? r.y.renewableShare * 100 : null) : null) },
      { key: 'rev', label: 'Revenue (₹ Cr)', num: true, render: r => (isNum(r.rev) ? fmt(r.rev, 0) : null) },
      { key: 'mwhCr', label: 'Energy per ₹ Cr (MWh)', num: true, render: r => (isNum(r.mwhCr) ? fmt(r.mwhCr, 2) : null) }
    ], rows, { emptyText: 'No plants.' }));
  });
  }

  // ---- diesel
  const dieselBox = h('div');
  body.appendChild(section('Diesel', `${label}, ${fy}: kilolitres (KL) as in the sheet "Diesel consumption data", by use: DG, HWG, forklift, other.`, dieselBox));
  slot(dieselBox, mine, [fetched.diesel], (box, diesel) => {
    const rows = dieselMonths(diesel, ids, fy);
    if (!rows.length) { box.appendChild(empty(`The diesel sheet has no months for ${label} in ${fy}.`)); return; }
    const sums = {};
    DIESEL_USES.forEach(u => { sums[u.key] = rows.reduce((a, r) => add(a, r[u.key]), null); });
    sums.total = rows.reduce((a, r) => add(a, r.total), null);
    box.appendChild(h('div', { class: 'grid kpis' }, [
      ...DIESEL_USES.map(u => kpi({ label: `Diesel, ${u.label}`, value: fmt(sums[u.key], 2), unit: 'KL', sub: `${fy}, ${rows.length} month${rows.length > 1 ? 's' : ''} in the sheet${single ? '' : ' · sum of plant rows'}` })),
      kpi({ label: 'Diesel, total', value: fmt(sums.total, 2), unit: 'KL', sub: `${fy}, sum of the months below`, tone: 'warn' })
    ]));
    const holder = h('div', { style: { marginTop: '14px' } });
    box.appendChild(card(`Diesel by month and use, ${fy}`, 'Stacked, KL per month. Months flagged as outliers are drawn as they are in the sheet.', holder));
    const months = fyMonths(fy);
    const idx = new Map(rows.map(r => [r.month, r]));
    const series = DIESEL_USES.map((u, i) => ({ label: u.label, type: 'bar', stack: 'd', colorIndex: i, unit: 'KL', data: months.map(m => (idx.get(m) ? num(idx.get(m)[u.key]) : null)) }));
    tickDigits(seriesChart(holder, months.map(monthLabel), series, { type: 'bar', stacked: true, yTitle: 'KL per month', legend: true, digits: 2 }), 1);
    const dFlags = [];
    rows.forEach(r => {
      if (r.outlier.length) dFlags.push(`${r.outlier.join(', ')} ${monthLabel(r.month)}: diesel flagged outlier (more than 50x the plant's FY median, possibly litres typed as KL). Included as in the sheet.`);
      if (r.entry.length) dFlags.push(`${r.entry.join(', ')} ${monthLabel(r.month)}: diesel value filled from the entry workbook.`);
    });
    const fb = flagsBox(`Flagged diesel months (${fy})`, dFlags);
    if (fb) box.appendChild(fb);
    const n = notes(diesel.notes);
    if (n) box.appendChild(n);
  });

  // ---- emissions over the years, targets
  const emBox = h('div');
  body.appendChild(section('Emissions over the years', `${label}: workbook figures from the Sustainability Summary sheets. Scope 1 is diesel × factor in tonnes CO2; Scope 2 is kWh × factor in kg CO2 (shown here as tonnes).`, emBox));
  const drawEmissions = (emissions) => {
    clear(emBox);
    const years = emissionYears(model, emissions, scope);
    if (!years.length) { emBox.appendChild(empty(`The Summary sheets have no emission figures for ${label}.`)); return; }
    const partialSet = new Set(yearlyRows(model, scope).filter(r => r.partial).map(r => r.fy));
    const rowsE = years.map(y => { const e = emissionRow(model, emissions, scope, y); return { fy: y, ...(e.row || {}), n: e.n, expected: e.expected }; });
    const labels = years.map(y => y + (partialSet.has(y) ? ' (part)' : ''));
    const t = v => (isNum(v) ? v / 1000 : null);

    const toggle = chips([
      { id: 'primary', label: 'Primary: detailed block (grid 0.823 kg/kWh, solar 41 g/kWh, diesel 2.64 kg/L)' },
      { id: 'alt', label: 'Other basis: 0.85 block (0.85 kg/kWh, solar 0.005, diesel 2.7 kg/L)' }
    ], basis, id => { basis = id; drawEmissions(emissions); });
    emBox.appendChild(h('div', { class: 'field', style: { marginBottom: '10px' } }, 'Basis shown', toggle.el));

    const holder = h('div');
    if (basis === 'primary') {
      emBox.appendChild(card('Emissions by FY, primary basis', 'Bars: Scope 2 gross and Scope 2 savings in t CO2 (kg ÷ 1,000). Line, right axis: Scope 1 in t CO2.', holder));
      tickDigits(seriesChart(holder, labels, [
        { label: 'Scope 2 gross', type: 'bar', tone: 'warn', unit: 't CO2', data: rowsE.map(r => t(r.scope2Gross)) },
        { label: 'Scope 2 savings', type: 'bar', tone: 'good', unit: 't CO2', data: rowsE.map(r => t(r.scope2Savings)) },
        { label: 'Scope 1', type: 'line', axis: 'right', tone: 'bad', unit: 't CO2', data: rowsE.map(r => num(r.scope1 ?? null)) }
      ], { type: 'bar', yTitle: 't CO2 (Scope 2)', y1Title: 't CO2 (Scope 1)', legend: true, digits: 0 }), 0);
    } else {
      emBox.appendChild(card('Emissions by FY, other basis (0.85 block)', 'Bars in t CO2 (kg ÷ 1,000): Scope 2 at 0.85 kg/kWh, renewable benefit, net carbon.', holder));
      tickDigits(seriesChart(holder, labels, [
        { label: 'Scope 2 (0.85 basis)', type: 'bar', tone: 'warn', unit: 't CO2', data: rowsE.map(r => t(r.scope2Basis)) },
        { label: 'Renewable benefit', type: 'bar', tone: 'good', unit: 't CO2', data: rowsE.map(r => t(r.renewableBenefitBasis)) },
        { label: 'Net carbon', type: 'bar', tone: 'info', unit: 't CO2', data: rowsE.map(r => t(r.netCarbonBasis)) }
      ], { type: 'bar', yTitle: 't CO2', legend: true, digits: 0 }), 0);
    }
    const partialPlants = rowsE.some(r => r.expected > 1 && r.n < r.expected);
    if (!single && emissions.plants.every(p => p.plant !== scope)) {
      emBox.appendChild(h('p', { class: 'ec-line' }, `The workbook's cluster tables are not used (the API notes they reference wrong rows); these are sums of the plant rows computed here${partialPlants ? ', and some FYs cover fewer plants than the cluster has (see "Plants" column)' : ''}.`));
    }
    emBox.appendChild(h('p', { class: 'ec-table-title' }, `Emissions table (${basis === 'primary' ? 'primary basis' : 'other basis'})`));
    const plantsCol = emissions.plants.every(p => p.plant !== scope) ? [{ key: 'n', label: 'Plants', num: true, render: r => `${r.n} of ${r.expected}` }] : [];
    const cols = basis === 'primary'
      ? [{ key: 'fy', label: 'FY', render: r => r.fy + (partialSet.has(r.fy) ? ' (part)' : '') }, ...plantsCol,
        { key: 'scope1', label: 'Scope 1 (t CO2)', num: true, render: r => fmt(r.scope1, 1) },
        { key: 'scope2Gross', label: 'Scope 2 gross (kg CO2)', num: true, render: r => fmt(r.scope2Gross) },
        { key: 'scope2IfGridInsteadOfSolar', label: 'Renewable saving (kg CO2)', num: true, render: r => fmt(r.scope2IfGridInsteadOfSolar) },
        { key: 'afforestationBenefit', label: 'Afforestation (kg CO2)', num: true, render: r => fmt(r.afforestationBenefit) },
        { key: 'scope2Savings', label: 'Scope 2 savings (kg CO2)', num: true, render: r => fmt(r.scope2Savings) }]
      : [{ key: 'fy', label: 'FY', render: r => r.fy + (partialSet.has(r.fy) ? ' (part)' : '') }, ...plantsCol,
        { key: 'scope2Basis', label: 'Scope 2 at 0.85 (kg CO2)', num: true, render: r => fmt(r.scope2Basis) },
        { key: 'renewableBenefitBasis', label: 'Renewable benefit (kg CO2)', num: true, render: r => fmt(r.renewableBenefitBasis) },
        { key: 'netCarbonBasis', label: 'Net carbon (kg CO2)', num: true, render: r => fmt(r.netCarbonBasis) }];
    emBox.appendChild(table(cols, rowsE, { emptyText: 'No emission rows.' }));
    const n = notes(emissions.notes);
    if (n) emBox.appendChild(n);
  };
  slot(emBox, mine, [fetched.emissions], (box, emissions) => { drawEmissions(emissions); });

  const tgtBox = h('div');
  body.appendChild(section('Targets', 'What the workbook\'s "Target Sheet" holds. It applies to the company, not to the plant or scope chosen.', tgtBox));
  slot(tgtBox, mine, [fetched.targets], (box, targets) => {
    const list = targets.targets || [];
    if (!list.length) { box.appendChild(empty('The Target Sheet returned no rows.')); return; }
    const groups = new Map();
    list.forEach(t => {
      const key = [t.category, t.targetLevel, t.reductionFraction].join('|');
      const g = groups.get(key) || { category: t.category, level: t.targetLevel, fraction: t.reductionFraction, fys: [] };
      g.fys.push(t.fy);
      groups.set(key, g);
    });
    const rows = [...groups.values()].map(g => ({ ...g, span: g.fys.length > 1 ? `${g.fys.slice().sort()[0]} to ${g.fys.slice().sort().pop()}` : g.fys[0] }));
    box.appendChild(table([
      { key: 'category', label: 'Target' },
      { key: 'span', label: 'Listed for' },
      { key: 'level', label: `Target level (${targets.unit || 'as in the sheet'})`, num: true, render: r => fmt(r.level, 3) },
      { key: 'fraction', label: 'Reduction fraction', num: true, render: r => fmt(r.fraction, 2) },
      { key: 'set', label: 'Status', render: r => (r.level ? badge('set', 'good') : badge('not set (0 in the sheet)', 'warn')) }
    ], rows));
    const n = notes(targets.notes);
    if (n) box.appendChild(n);
  });

  // ---- data tables
  const tableBox = h('div');
  body.appendChild(section('Monthly data', `${label}, ${fy}: the numbers behind the charts, kWh per month as the API returns them.`, tableBox));
  slot(tableBox, mine, [fetched.monthly, fetched.diesel], (box, monthly, diesel) => {
    const rows = monthlyRows(model, scope);
    const idx = new Map(rows.map(r => [r.month, r]));
    const flags = powerFlags(model, scope, fy);
    const list = fyMonths(fy).map(m => idx.get(m)).filter(Boolean);
    box.appendChild(h('p', { class: 'ec-table-title' }, `Power, kWh (${fy}${pf ? `, with ${pf} total for the same month` : ''})`));
    if (!list.length) box.appendChild(empty('No months reported for this scope in this FY.'));
    else {
      const total = k => list.reduce((a, r) => add(a, num(r[k] ?? null)), null);
      const data = list.map(r => ({ ...r, prev: (idx.get(shiftYear(r.month, -1)) || {}).total ?? null }));
      data.push({ _total: true, month: 'Sum of months shown', grid: total('grid'), solar: total('solar'), purchased: total('purchased'), dg: total('dg'), total: total('total'), prev: null });
      const cell = (k) => r => fmt(r[k]);
      const t = table([
        { key: 'month', label: 'Month', render: r => (r._total ? r.month : h('span', null, monthLabel(r.month), badgesFor(flags.get(r.month), single))) },
        { key: 'grid', label: 'Grid EB', num: true, render: cell('grid') },
        { key: 'solar', label: 'Solar', num: true, render: cell('solar') },
        { key: 'purchased', label: 'Purchased renewable', num: true, render: cell('purchased') },
        { key: 'dg', label: 'DG', num: true, render: cell('dg') },
        { key: 'total', label: 'Total', num: true, render: cell('total') },
        { key: 'share', label: 'Renewable share', num: true, render: r => (r.total ? fmtPct(((r.solar + r.purchased) / r.total) * 100) : null) },
        { key: 'prev', label: `${pf || 'Prev FY'} total`, num: true, render: r => (r._total ? null : fmt(r.prev)) },
        { key: 'chg', label: 'Change', num: true, render: r => { const c = r._total ? null : change(r.total, r.prev); return c === null ? null : (c >= 0 ? '+' : '') + fmt(c, 1) + '%'; } },
        ...(single ? [] : [{ key: 'pl', label: 'Plants reporting', num: true, render: r => (r._total ? null : `${r.plantsReporting} of ${r.plantsExpected}`) }])
      ], data);
      box.appendChild(t);
      const trs = t.querySelectorAll ? t.querySelectorAll('tbody tr') : [];
      if (trs.length) trs[trs.length - 1].classList.add('ec-total');
      box.appendChild(h('p', { class: 'ec-line' }, 'Renewable share and change are worked out from the row shown; "Sum of months shown" adds the rows above and, for a part year, is not a full-year figure.'));
    }

    box.appendChild(h('p', { class: 'ec-table-title' }, `Diesel, KL (${fy})`));
    const drows = dieselMonths(diesel, ids, fy);
    if (!drows.length) box.appendChild(empty('No diesel months for this scope in this FY.'));
    else {
      const data = drows.map(r => ({ ...r }));
      const tot = k => drows.reduce((a, r) => add(a, r[k]), null);
      data.push({ _total: true, month: 'Sum of months shown', dg: tot('dg'), hwg: tot('hwg'), forklift: tot('forklift'), other: tot('other'), total: tot('total') });
      const badgesD = r => {
        const out = [];
        if (r.outlier && r.outlier.length) { const b = badge('outlier' + (single ? '' : ' · ' + r.outlier.join(', ')), 'warn'); b.title = 'Flagged by the API: more than 50x the plant\'s FY median (possibly litres typed as KL). Shown as in the sheet.'; out.push(b); }
        if (r.entry && r.entry.length) { const b = badge('entry workbook' + (single ? '' : ' · ' + r.entry.join(', ')), 'info'); b.title = 'Empty in the Sustainability Calculation workbook; value from the entry workbook.'; out.push(b); }
        return out.length ? h('span', { class: 'ec-badges' }, out) : null;
      };
      const t = table([
        { key: 'month', label: 'Month', render: r => (r._total ? r.month : h('span', null, monthLabel(r.month), badgesD(r))) },
        ...DIESEL_USES.map(u => ({ key: u.key, label: u.label, num: true, render: r => fmt(r[u.key], 3) })),
        { key: 'total', label: 'Total', num: true, render: r => fmt(r.total, 3) },
        ...(single ? [] : [{ key: 'n', label: 'Plants in sheet', num: true, render: r => (r._total ? null : `${r.n} of ${ids.length}`) }])
      ], data);
      box.appendChild(t);
      const trs = t.querySelectorAll ? t.querySelectorAll('tbody tr') : [];
      if (trs.length) trs[trs.length - 1].classList.add('ec-total');
    }
  });
}

// ------------------------------------------------------------------ headline helper for the hosting page

/**
 * Real figures for one plant (or TADL / DP / ASSY) and FY, from the API only.
 * Every field is null when the data does not have it. fy defaults to the latest FY with data.
 */
export async function plantHeadline(plant, fy) {
  const [monthly, yearly, emissions, revenue] = await Promise.all([
    api.get('/energy/monthly'), api.get('/energy/yearly'), api.get('/energy/emissions'), api.get('/energy/revenue')
  ]);
  const model = makeModel(monthly, yearly);
  const options = fyOptions(model, plant);
  const useFy = options.includes(fy) ? fy : options[options.length - 1] || null;
  const out = {
    plant, fy: useFy, monthsReported: null, toMonth: null, partial: null,
    totalKwh: null, avgMonthlyKwh: null, changePctVsPrevSamePeriod: null, comparedWith: null, comparedMonths: null,
    scope1: null, scope1Unit: (emissions.units || {}).scope1 || null,
    scope2Gross: null, scope2GrossUnit: (emissions.units || {}).scope2Gross || null,
    revenueCr: null, revenuePerMonthCr: null,
    notes: uniqueNotes(monthly.notes, yearly.notes, emissions.notes, revenue.notes)
  };
  if (!useFy) return out;
  const yr = yearlyRows(model, plant).find(y => y.fy === useFy) || null;
  const info = periodInfo(model, plant, useFy);
  out.toMonth = info.lastAll;
  out.partial = info.partial;
  if (yr) {
    out.monthsReported = num(yr.monthsReported);
    out.totalKwh = num(yr.total);
    out.avgMonthlyKwh = out.totalKwh !== null && out.monthsReported ? out.totalKwh / out.monthsReported : null;
  }
  const cmp = likeForLike(model, plant, useFy);
  if (cmp.pairs && cmp.prev.total) {
    out.changePctVsPrevSamePeriod = change(cmp.cur.total, cmp.prev.total);
    out.comparedWith = cmp.prevFy;
    out.comparedMonths = cmp.months.length;
  }
  const em = emissionRow(model, emissions, plant, useFy).row;
  if (em) { out.scope1 = num(em.scope1 ?? null); out.scope2Gross = num(em.scope2Gross ?? null); }
  const rv = (revenue.plants.find(p => p.plant === plant) || {}).years;
  const revValue = rv ? num((rv.find(r => r.fy === useFy) || {}).value ?? null) : null;
  out.revenueCr = revValue;
  // The sheet's yearly revenue is a full-FY figure; a monthly average is only meaningful for a full year.
  if (revValue !== null && out.monthsReported === 12) out.revenuePerMonthCr = revValue / 12;
  return out;
}
