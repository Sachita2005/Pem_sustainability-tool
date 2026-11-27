// Manual-entry views for one plant and one parameter (Diesel, Manpower, Revenue,
// Afforestation), plus the entry dialog. Entries live in Excel workbooks in the
// source folder (API: /api/manual). Every figure shown comes from the API.
//
//   render(container, { plant, param })   page body for that plant and parameter
//   openEntryDialog({ plant, param, month?, onSaved })   modal entry form
//
// TBAL is outside the manual-entry group: for TBAL the workbook values are shown
// read-only from /api/energy/* and the manual API is never called.

import { api } from '../../api.js';
import { badge, clear, empty, errorBox, fmt, fmtPct, h, kpi, loading, modal, monthLabel, notes, table, toast } from '../../ui.js';
import { destroyCharts, seriesChart } from '../../charts.js';

const PARAM_IDS = ['diesel', 'manpower', 'revenue', 'afforestation'];
const SHORT = { diesel: 'Diesel', manpower: 'Manpower', revenue: 'Revenue', afforestation: 'Afforestation' };
const MONTH_RE = /^(20\d{2})-(0[1-9]|1[0-2])$/;
const FY_RE = /^FY(\d{2})-(\d{2})$/;
const LOCK_TITLE = 'The workbook is open in Excel';
const LOCK_TEXT = 'Close the entries workbook in Excel (or whichever program has it open), then try again. Nothing was changed.';

function ensureCss() {
  if (document.getElementById('energy-entry-css')) return;
  document.head.appendChild(h('link', { id: 'energy-entry-css', rel: 'stylesheet', href: '/static/css/energy-entry.css' }));
}

// ---------- helpers ----------

const num = v => (v === null || v === undefined ? '—' : Number.isInteger(v) ? fmt(v, 0) : fmt(v, 2));
const addMonth = (ym, n) => {
  const [y, m] = ym.split('-').map(Number);
  const t = y * 12 + (m - 1) + n;
  return `${Math.floor(t / 12)}-${String(t % 12 + 1).padStart(2, '0')}`;
};
const fyOf = ym => {
  const [y, m] = ym.split('-').map(Number);
  const s = m >= 4 ? y : y - 1;
  return `FY${String(s % 100).padStart(2, '0')}-${String((s + 1) % 100).padStart(2, '0')}`;
};
const fyStartYear = fy => { const m = FY_RE.exec(fy); return m ? 2000 + Number(m[1]) : null; };
const fyMonths = fy => (fyStartYear(fy) ? Array.from({ length: 12 }, (_, i) => addMonth(`${fyStartYear(fy)}-04`, i)) : []);
const prevFy = fy => { const s = fyStartYear(fy); return s ? `FY${String((s - 1) % 100).padStart(2, '0')}-${String(s % 100).padStart(2, '0')}` : null; };
const when = s => (s ? String(s).replace('T', ' ').slice(0, 16) : null);
const isLock = err => err && err.status === 409;
const lockBox = () => h('div', { class: 'ee-msg lock', role: 'alert' }, h('strong', null, LOCK_TITLE), LOCK_TEXT);
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };

async function getMeta() {
  const meta = await api.get('/manual/parameters');
  return Object.fromEntries(meta.parameters.map(p => [p.id, p]));
}
const labelOf = pm => pm.label.split(' (')[0];

export function destroy() { /* charts are cleaned up by the page frame */ }

// ---------- delete confirmation ----------

function confirmDelete({ param, pm, plantName, entry, onDone }) {
  const err = h('div', { 'aria-live': 'assertive' });
  const yes = h('button', { type: 'button', class: 'btn danger' }, 'Delete entry');
  const no = h('button', { type: 'button', class: 'btn' }, 'Cancel');
  const close = modal('Delete this entry?', h('div', null,
    h('p', null, `${SHORT[param]}, ${plantName}, ${monthLabel(entry.month)}: ${num(entry.value)} ${pm.unit}${entry.note ? ' (' + entry.note + ')' : ''}`),
    h('p', { class: 'muted small', style: { marginTop: '6px' } }, 'The row is removed from the entries workbook. A value already in the company workbooks is not touched and shows again afterwards.'),
    err,
    h('div', { class: 'ee-confirm-actions' }, no, yes)));
  no.addEventListener('click', close);
  yes.addEventListener('click', async () => {
    yes.disabled = true; clear(err);
    try {
      await api.del(`/manual/${param}/${encodeURIComponent(entry.id)}`);
      api.clearCache('/manual');
      close();
      toast(`Deleted: ${plantName}, ${monthLabel(entry.month)}`);
      if (onDone) onDone();
    } catch (e) {
      yes.disabled = false;
      err.appendChild(isLock(e) ? lockBox() : h('div', { class: 'ee-msg bad' }, e.message));
    }
  });
  no.focus();
}

// ---------- entry dialog ----------

export function openEntryDialog({ plant, param, month, onSaved } = {}) {
  ensureCss();
  const host = h('div', null, loading());
  modal('Enter monthly data', host);
  getMeta().then(metaById => {
    clear(host);
    host.appendChild(entryForm(metaById, { plant, param, month, onSaved }));
  }, err => { clear(host); host.appendChild(errorBox(err)); });
}

function entryForm(metaById, opts) {
  const ids = PARAM_IDS.filter(id => metaById[id]);
  let param = ids.includes(opts.param) ? opts.param : ids[0];
  const plants = metaById[param].plants;                     // never TBAL
  const wantedTbal = opts.plant === 'TBAL';
  let plant = plants.some(p => p.id === opts.plant) ? opts.plant : plants[0].id;
  let month = MONTH_RE.test(opts.month || '') ? opts.month : addMonth(today(), -1);
  let mode = 'month';
  const pm = () => metaById[param];
  const plantName = id => (plants.find(p => p.id === id) || { name: id }).name;

  const paramSel = h('select', { class: 'input', id: 'ee-d-param' }, ids.map(id => h('option', { value: id, selected: id === param }, `${labelOf(metaById[id])} (${metaById[id].unit})`)));
  const plantSel = h('select', { class: 'input', id: 'ee-d-plant' }, plants.map(p => h('option', { value: p.id, selected: p.id === plant }, p.name)));
  const monthIn = h('input', { class: 'input', id: 'ee-d-month', type: 'month', required: true, placeholder: 'YYYY-MM', value: month });
  const valueIn = h('input', { class: 'input num', id: 'ee-d-value', type: 'number', min: '0', step: 'any', required: true, autocomplete: 'off' });
  const unitEl = h('span', { class: 'ee-unit' });
  const noteIn = h('input', { class: 'input', id: 'ee-d-note', type: 'text', maxlength: '200', placeholder: 'optional, e.g. meter reading', autocomplete: 'off' });
  const hintBox = h('div', { 'aria-live': 'polite' });
  const msgBox = h('div', { 'aria-live': 'assertive' });
  const saveBtn = h('button', { type: 'submit', class: 'btn primary' }, 'Save entry');
  const radios = [['month', 'Next month, same plant'], ['plant', 'Next plant, same month'], ['stay', 'Stay on this plant and month']]
    .map(([v, label]) => h('label', null, h('input', { type: 'radio', name: 'ee-d-mode', value: v, checked: v === mode, onChange: () => { mode = v; } }), label));

  function paramChanged() {
    valueIn.step = param === 'manpower' ? '1' : 'any';
    valueIn.inputMode = param === 'manpower' ? 'numeric' : 'decimal';
    valueIn.placeholder = param === 'manpower' ? 'whole number' : '0.00';
    unitEl.textContent = pm().unit;
  }
  paramChanged();

  // existing-value hint
  let hintToken = 0;
  async function renderHint() {
    const mine = ++hintToken;
    saveBtn.textContent = 'Save entry';
    if (!MONTH_RE.test(monthIn.value)) { clear(hintBox); hintBox.className = ''; return; }
    let combined, list;
    try {
      [combined, list] = await Promise.all([
        api.get(`/manual/${param}/combined`, { plant: plantSel.value }),
        api.get(`/manual/${param}`, { plant: plantSel.value })]);
    } catch (err) {
      if (mine !== hintToken) return;
      clear(hintBox); hintBox.className = '';
      hintBox.appendChild(isLock(err) ? lockBox() : h('div', { class: 'ee-msg bad' }, err.message));
      return;
    }
    if (mine !== hintToken) return;
    clear(hintBox);
    const unit = pm().unit;
    const prow = (combined.plants || []).find(p => p.plant === plantSel.value) || { months: [] };
    const cell = prow.months.find(m => m.month === monthIn.value);
    const entry = (list.entries || []).find(e => e.month === monthIn.value);
    const lines = [];
    if (entry) {
      saveBtn.textContent = 'Replace entry';
      lines.push(h('div', null, h('strong', null, 'Already entered: '), `${num(entry.value)} ${unit}`,
        entry.note ? ` (note: ${entry.note})` : '', entry.enteredAt ? `, saved ${when(entry.enteredAt)}` : '', '. Saving will replace it.'));
    }
    const wb = cell && cell.workbookValue !== null && cell.workbookValue !== undefined ? cell.workbookValue : null;
    if (wb !== null) {
      lines.push(h('div', null, h('strong', null, 'In the workbook: '), `${num(wb)} ${unit}`,
        cell.workbookSource === 'entry' ? ' (Only to Enter workbook; the calculation workbook has no value)' : ' (Sustainability Calculation workbook)'));
      if (cell.entryWorkbookValue !== null && cell.entryWorkbookValue !== undefined) {
        lines.push(h('div', null, `The Only to Enter workbook says ${num(cell.entryWorkbookValue)} ${unit} for this month.`));
      }
    }
    if (param === 'manpower') {
      const y = (prow.yearly || []).find(r => r.fy === fyOf(monthIn.value));
      lines.push(h('div', null, h('strong', null, `Workbook headcount for ${fyOf(monthIn.value)}: `),
        y ? `${num(y.value)} (one figure for the whole year, not a month)` : 'none in the workbooks'));
    }
    if (!entry && wb === null) lines.unshift(h('div', null, `Nothing yet for ${plantName(plantSel.value)}, ${monthLabel(monthIn.value)} in the workbooks or manual entries.`));
    const v = Number(valueIn.value);
    if (valueIn.value.trim() !== '' && Number.isFinite(v) && wb && wb > 0 && v > 0 && (v / wb >= 100 || v / wb <= 0.01)) {
      lines.push(h('div', { class: 'ee-warnline' }, `Check the unit: ${num(v)} is ${fmt(v / wb, v / wb >= 1 ? 0 : 4)} times the workbook value of ${num(wb)} ${unit}. It is still saved if you press Save.`));
    }
    hintBox.className = 'ee-hint' + (entry ? ' manual' : '');
    lines.forEach(l => hintBox.appendChild(l));
  }

  const validate = () => {
    const errs = [];
    if (!plants.some(p => p.id === plantSel.value)) errs.push('Choose a plant.');
    if (!MONTH_RE.test(monthIn.value)) errs.push('Choose a month (format YYYY-MM, 2000 to 2099).');
    const raw = valueIn.value.trim(), v = Number(raw);
    if (raw === '' || !Number.isFinite(v)) errs.push('Enter the value as a number.');
    else if (v < 0) errs.push('The value cannot be negative (0 is allowed).');
    else if (param === 'manpower' && !Number.isInteger(v)) errs.push('Manpower must be a whole number.');
    return errs;
  };

  let saving = false;
  async function save() {
    if (saving) return;
    clear(msgBox);
    const errs = validate();
    if (errs.length) {
      msgBox.appendChild(h('div', { class: 'ee-msg bad', role: 'alert' }, errs.map(e => h('div', null, e))));
      (!MONTH_RE.test(monthIn.value) ? monthIn : valueIn).focus();
      return;
    }
    const body = { plant: plantSel.value, month: monthIn.value, value: Number(valueIn.value.trim()) };
    if (noteIn.value.trim()) body.note = noteIn.value.trim();
    saving = true; saveBtn.disabled = true;
    try {
      const res = await api.post(`/manual/${param}`, body);
      api.clearCache('/manual');
      const verb = res.replaced ? 'Replaced' : 'Saved';
      const text = `${verb}: ${SHORT[param]}, ${plantName(body.plant)}, ${monthLabel(body.month)}, ${num(res.entry.value)} ${pm().unit}`;
      toast(text);
      msgBox.appendChild(h('div', { class: 'ee-msg ok' }, text.endsWith('.') ? text : text + '.'));
      valueIn.value = ''; noteIn.value = '';
      if (mode === 'month') monthIn.value = addMonth(body.month, 1);
      else if (mode === 'plant') {
        const i = plants.findIndex(p => p.id === body.plant);
        if (i + 1 < plants.length) plantSel.value = plants[i + 1].id;
        else { plantSel.value = plants[0].id; monthIn.value = addMonth(body.month, 1); }
      }
      if (opts.onSaved) { try { opts.onSaved(); } catch (e) { console.error(e); } }
      await renderHint();
      valueIn.focus();
    } catch (err) {
      if (isLock(err)) { msgBox.appendChild(lockBox()); toast(LOCK_TITLE + '. Close it and try again.', 'bad'); }
      else { msgBox.appendChild(h('div', { class: 'ee-msg bad', role: 'alert' }, err.message)); toast(err.message, 'bad'); }
    } finally { saving = false; saveBtn.disabled = false; }
  }

  paramSel.addEventListener('change', () => { param = paramSel.value; paramChanged(); clear(msgBox); renderHint(); });
  plantSel.addEventListener('change', () => { clear(msgBox); renderHint(); });
  monthIn.addEventListener('change', renderHint);
  monthIn.addEventListener('input', renderHint);
  valueIn.addEventListener('input', renderHint);

  const fileLine = h('div', { class: 'ee-store' });
  const showFile = () => { fileLine.textContent = `Saved in: ${pm().folder}/${pm().file}`; };
  showFile();
  paramSel.addEventListener('change', showFile);

  const form = h('form', { class: 'ee-form', novalidate: true, onSubmit: e => { e.preventDefault(); save(); } },
    wantedTbal ? h('div', { class: 'ee-hint' }, 'TBAL is not offered: it is outside the manual-entry group, and the API rejects TBAL entries.') : null,
    h('label', { class: 'field', for: 'ee-d-param' }, 'Parameter', paramSel),
    h('div', { class: 'ee-two' },
      h('label', { class: 'field', for: 'ee-d-plant' }, 'Plant', plantSel),
      h('label', { class: 'field', for: 'ee-d-month' }, 'Month', monthIn)),
    h('label', { class: 'field', for: 'ee-d-value' }, 'Value', h('span', { class: 'ee-valrow' }, valueIn, unitEl)),
    h('label', { class: 'field', for: 'ee-d-note' }, 'Note (optional)', noteIn),
    hintBox,
    h('div', { class: 'ee-replace' }, 'A second entry for the same plant and month replaces the first.'),
    h('fieldset', { class: 'ee-after' }, h('legend', null, 'After saving'), radios),
    msgBox,
    h('div', null, saveBtn),
    fileLine);
  renderHint();
  setTimeout(() => valueIn.focus(), 30);
  return form;
}

// ---------- shared view pieces ----------

/** Monthly values {month: value}, yearly [{fy, value}] -> KPI tiles for the chosen FY. */
function kpiTiles({ param, unit, fy, monthly, yearly }) {
  const months = fyMonths(fy);
  const have = months.filter(m => monthly[m] !== null && monthly[m] !== undefined);
  const pfy = prevFy(fy);
  const prevMonths = pfy ? fyMonths(pfy) : [];
  const tiles = [];
  const latest = have[have.length - 1];
  tiles.push(kpi({ label: `Latest month in ${fy}`, value: latest ? num(monthly[latest]) : '—', unit: latest ? unit : null,
    sub: latest ? monthLabel(latest) : (param === 'manpower' ? 'no manual monthly entry' : 'no value this year') }));
  if (param === 'manpower') {
    const y = yearly.find(r => r.fy === fy), py = yearly.find(r => r.fy === pfy);
    tiles.push(kpi({ label: `Yearly headcount ${fy}`, value: y ? num(y.value) : '—', unit: y ? unit : null, sub: 'one figure per year in the workbook' }));
    const pct = y && py && py.value ? ((y.value - py.value) / Math.abs(py.value)) * 100 : null;
    tiles.push(kpi({ label: `Change vs ${pfy || 'previous year'}`, value: pct === null ? '—' : (pct >= 0 ? '+' : '') + fmtPct(pct), sub: pct === null ? 'previous year not available' : `${num(py.value)} to ${num(y.value)} (yearly)` }));
  } else {
    const sum = have.reduce((s, m) => s + monthly[m], 0);
    tiles.push(kpi({ label: `${fy} to date`, value: have.length ? num(Math.round(sum * 100) / 100) : '—', unit: have.length ? unit : null,
      sub: `total of ${have.length} month${have.length === 1 ? '' : 's'} with a value` }));
    const common = have.filter((m, i) => { const pm_ = prevMonths[months.indexOf(m)]; return monthly[pm_] !== null && monthly[pm_] !== undefined; });
    const cur = common.reduce((s, m) => s + monthly[m], 0);
    const prv = common.reduce((s, m) => s + monthly[prevMonths[months.indexOf(m)]], 0);
    const pct = common.length && prv ? ((cur - prv) / Math.abs(prv)) * 100 : null;
    tiles.push(kpi({ label: `Change vs ${pfy || 'previous year'}`, value: pct === null ? '—' : (pct >= 0 ? '+' : '') + fmtPct(pct),
      sub: pct === null ? 'no month has a value in both years' : `same ${common.length} month${common.length === 1 ? '' : 's'}: ${num(Math.round(prv * 100) / 100)} to ${num(Math.round(cur * 100) / 100)} ${unit}` }));
  }
  return h('div', { class: 'grid kpis' }, tiles);
}

function fySelect(fys, value, onChange) {
  return h('label', { class: 'field' }, 'Financial year',
    h('select', { class: 'input', onChange: e => onChange(e.target.value) }, fys.map(f => h('option', { value: f, selected: f === value }, f))));
}

function defaultFy(fys, wanted) {
  if (wanted && fys.includes(wanted)) return wanted;
  const cur = fyOf(today());
  return fys.includes(cur) ? cur : (fys[fys.length - 1] || cur);
}

function legendFor(items) {
  return h('div', { class: 'ee-legend' }, items.map(([cls, text]) => h('span', null, h('i', { class: cls }), text)));
}

// ---------- render ----------

export async function render(container, ctx = {}) {
  ensureCss();
  let plant = ctx.plant;
  const param = PARAM_IDS.includes(ctx.param) ? ctx.param : 'diesel';
  if (plant === 'TBAL') return renderTbal(container, param);

  let metaById;
  try { metaById = await getMeta(); } catch (err) { container.appendChild(errorBox(err)); return; }
  const pm = metaById[param];
  if (!plant || !pm.plants.some(p => p.id === plant)) plant = pm.plants[0].id;
  const plantName = pm.plants.find(p => p.id === plant).name;
  const unit = pm.unit;
  const isYearly = param === 'manpower';

  const S = { fy: null, data: null, error: null };
  const kpiSlot = h('div');
  const toolSlot = h('div', { class: 'ee-tools' });
  const chartSlot = h('div', { class: 'card' });
  const tableSlot = h('div', { class: 'card' });
  const listSlot = h('div', { class: 'card' });
  const infoSlot = h('div');
  container.append(
    h('div', { class: 'ee-intro' }, h('p', null, `${labelOf(pm)} for ${plantName}, in ${unit}. A manual entry takes precedence over the company workbooks; entries are kept in an Excel workbook and can also be edited there.`)),
    toolSlot, kpiSlot,
    h('div', { class: 'ee-block' }, chartSlot), h('div', { class: 'ee-block' }, tableSlot), h('div', { class: 'ee-block' }, listSlot), infoSlot);
  kpiSlot.appendChild(loading());

  const row = () => (S.data.combined.plants || []).find(p => p.plant === plant) || { months: [], yearly: [] };
  const cells = () => Object.fromEntries(row().months.map(m => [m.month, m]));
  const yearly = () => row().yearly || [];
  const fyOptions = () => {
    const set = new Set();
    row().months.forEach(m => set.add(m.fy));
    yearly().forEach(y => set.add(y.fy));
    (S.data.list.entries || []).forEach(e => set.add(e.fy));
    return [...set].filter(Boolean).sort();
  };

  async function load(fresh) {
    if (fresh) api.clearCache('/manual');
    const o = fresh ? { fresh: true } : undefined;
    try {
      const [combined, list] = await Promise.all([
        api.get(`/manual/${param}/combined`, { plant }, o),
        api.get(`/manual/${param}`, { plant }, o)]);
      S.data = { combined, list }; S.error = null;
    } catch (err) { S.error = err; }
    draw();
  }

  function openDialog(month) {
    openEntryDialog({ plant, param, month, onSaved: () => load(true) });
  }
  const del = e => confirmDelete({ param, pm, plantName, entry: e, onDone: () => load(true) });

  function draw() {
    destroyCharts(chartSlot);
    [kpiSlot, toolSlot, chartSlot, tableSlot, listSlot, infoSlot].forEach(clear);
    if (S.error) {
      kpiSlot.appendChild(isLock(S.error) ? lockBox() : errorBox(S.error));
      kpiSlot.appendChild(h('button', { type: 'button', class: 'btn', style: { marginTop: '10px' }, onClick: () => load(true) }, 'Try again'));
      return;
    }
    const fys = fyOptions();
    S.fy = defaultFy(fys, S.fy || (FY_RE.test(ctx.fy || '') ? ctx.fy : null));
    const idx = cells();
    const months = fyMonths(S.fy);
    const monthly = Object.fromEntries(months.map(m => [m, idx[m] ? idx[m].value : null]));
    const prevMonthly = {};
    const pf = prevFy(S.fy);
    fyMonths(pf).forEach(m => { prevMonthly[m] = idx[m] ? idx[m].value : null; });

    toolSlot.append(fySelect(fys.length ? fys : [S.fy], S.fy, f => { S.fy = f; draw(); }),
      h('button', { type: 'button', class: 'btn primary', onClick: () => openDialog(null) }, 'Add or replace an entry'));
    kpiSlot.appendChild(kpiTiles({ param, unit, fy: S.fy, monthly: { ...prevMonthly, ...monthly }, yearly: yearly() }));

    // chart
    chartSlot.append(h('div', { class: 'card-head' }, h('div', null,
      h('h3', null, `${SHORT[param]}, ${plantName}, ${S.fy}`),
      h('p', null, `Monthly, ${unit}${isYearly ? ' (manual entries only; the workbooks have no monthly headcount)' : ''}. Manual points are a different colour from workbook points.`))));
    const part = f => months.map(m => { const c = idx[m]; return c && f(c) ? c.value : null; });
    const sets = [
      { label: 'Manual entry', data: part(c => c.source === 'manual'), tone: 'warn' },
      { label: 'Workbook', data: part(c => c.source !== 'manual' && c.workbookSource !== 'entry'), tone: 'brand' },
      { label: 'Entry workbook', data: part(c => c.source !== 'manual' && c.workbookSource === 'entry'), tone: 'info' }
    ].filter(s => s.data.some(v => v !== null)).map(s => ({ ...s, type: 'bar', stack: 'v', unit }));
    if (!sets.length) chartSlot.append(empty(`No ${SHORT[param].toLowerCase()} values for ${plantName} in ${S.fy}.`));
    else seriesChart(chartSlot, months.map(monthLabel), sets, { type: 'bar', stacked: true, yTitle: unit, legend: true, height: 'short' });

    // month table
    tableSlot.append(h('div', { class: 'card-head' }, h('div', null, h('h3', null, `Month by month, ${S.fy}`),
      h('p', null, 'Where each value comes from is stated in the Source column.'))));
    if (isYearly) {
      tableSlot.append(h('p', { class: 'small muted', style: { marginBottom: '8px' } },
        'The workbooks hold manpower only as one headcount per financial year, so it is shown yearly (below) and is not spread over months. The months here are filled only by manual entries.'));
    }
    tableSlot.append(h('div', { class: 'table-wrap' }, h('table', { class: 'table ee-mtable' },
      h('thead', null, h('tr', null, ['Month', `Value (${unit})`, 'Source', 'Note', ''].map((t, i) => h('th', { class: i === 1 ? 'num' : null }, t)))),
      h('tbody', null, (isYearly ? months.filter(m => idx[m]) : months).map(m => monthRow(m, idx[m]))))));
    if (isYearly && !months.some(m => idx[m])) tableSlot.append(empty(`No manual monthly entries for ${S.fy}. Use "Add or replace an entry".`));
    tableSlot.append(legendFor(isYearly
      ? [['manual-gap', 'Manual entry']]
      : [['', 'Workbook (Sustainability Calculation)'], ['entry', 'Entry workbook ("Only to Enter"), where the calculation workbook is empty'],
        ['manual-over', 'Manual entry replacing a workbook value'], ['manual-gap', 'Manual entry filling a gap']]));
    if (isYearly) {
      const ys = yearly();
      tableSlot.append(h('h4', { style: { margin: '14px 0 6px' } }, 'Yearly headcount in the workbooks'));
      tableSlot.append(ys.length ? table([{ key: 'fy', label: 'Financial year' }, { key: 'value', label: 'Headcount', num: true, render: r => num(r.value) }, { key: 'source', label: 'Source', render: () => 'Workbook (Sustainability Summary)' }], ys)
        : empty('The workbooks have no yearly headcount for this plant.'));
    }

    // list
    const entries = S.data.list.entries || [];
    listSlot.append(h('div', { class: 'card-head' }, h('div', null, h('h3', null, `Manual ${SHORT[param].toLowerCase()} entries, ${plantName}`),
      h('p', null, `${entries.length} entr${entries.length === 1 ? 'y' : 'ies'} in the entries workbook, all years`))));
    listSlot.append(entries.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
      h('thead', null, h('tr', null, ['Month', 'Value', 'Unit', 'Note', 'Entered at', ''].map((t, i) => h('th', { class: i === 1 ? 'num' : null }, t)))),
      h('tbody', null, entries.map(e => h('tr', null, h('td', null, monthLabel(e.month)), h('td', { class: 'num' }, num(e.value)), h('td', null, e.unit || unit),
        h('td', null, e.note || '—'), h('td', null, when(e.enteredAt) || '—'),
        h('td', null, h('button', { type: 'button', class: 'btn small danger', 'aria-label': `Delete entry ${monthLabel(e.month)}`, onClick: () => del(e) }, 'Delete')))))))
      : empty('No manual entries for this plant yet.'));

    const l = S.data.list;
    infoSlot.append(h('div', { class: 'ee-store', style: { marginTop: '12px' } }, h('strong', null, 'Entries are stored in: '), h('code', null, l.file),
      l.fileExists ? '' : ' (not created yet; it is created when the first entry is saved)',
      h('div', null, 'Open this file in Excel to edit entries by hand, and close it again before saving here.')),
      notes([...(S.data.combined.notes || []), ...(l.notes || [])]));
  }

  function monthRow(m, c) {
    if (!c) {
      return h('tr', null, h('td', null, monthLabel(m)), h('td', { class: 'num' }, '—'), h('td', { class: 'muted' }, 'No value'), h('td', null, '—'),
        h('td', null, h('button', { type: 'button', class: 'btn small', onClick: () => openDialog(m) }, 'Enter')));
    }
    const manual = c.source === 'manual';
    const hasWb = c.workbookValue !== null && c.workbookValue !== undefined;
    const src = [];
    let cls = '';
    if (manual) {
      cls = hasWb ? 'manual-over' : 'manual-gap';
      src.push(badge('Manual entry', hasWb ? 'warn' : 'good'),
        ' ', hasWb ? `replaces ${num(c.workbookValue)} (${c.workbookSource === 'entry' ? 'entry workbook' : 'workbook'})` : 'no workbook value');
    } else if (c.workbookSource === 'entry') {
      cls = 'entry';
      src.push(badge('Entry workbook', 'info'), ' calculation workbook is empty');
    } else src.push(badge('Workbook'));
    const ew = c.entryWorkbookValue;
    if (ew !== null && ew !== undefined) {
      const ref = c.workbookValue;
      const r = ref && ew ? Math.max(ref, ew) / Math.min(ref, ew) : null;
      src.push(h('div', { class: 'ee-flag', title: 'The two company workbooks hold different values for this month; the calculation workbook value is the one used.' }, `≠ the Only to Enter workbook says ${num(ew)}`));
      if (r !== null && r >= 500 && r <= 2000) src.push(h('div', { class: 'ee-flag' }, '⚠ about 1000 times apart: possible unit slip (litres typed as KL)'));
    }
    return h('tr', { class: 'ee-row ' + cls },
      h('td', null, monthLabel(m)), h('td', { class: 'num' }, h('strong', null, num(c.value))), h('td', null, src), h('td', null, c.note || '—'),
      h('td', null, h('div', { class: 'ee-actions' },
        h('button', { type: 'button', class: 'btn small', onClick: () => openDialog(m) }, manual ? 'Replace' : 'Enter'),
        manual && c.entryId ? h('button', { type: 'button', class: 'btn small danger', 'aria-label': `Delete manual entry ${monthLabel(m)}`, onClick: () => del({ id: c.entryId, month: m, value: c.value, note: c.note }) }, 'Delete') : null)));
  }

  await load(false);
}

// ---------- TBAL: workbook values, read only ----------

async function renderTbal(container, param) {
  const unitOf = { diesel: 'KL', revenue: '₹ Cr', manpower: 'headcount', afforestation: 'No.' }[param];
  container.append(h('div', { class: 'ee-intro' },
    h('p', null, 'TBAL is outside the manual-entry group, so there is no entry form here. These are the values already in the company workbooks, read only.')));
  const body = h('div', null, loading());
  container.appendChild(body);
  let res;
  try {
    res = await api.get(`/energy/${param}`, { plant: 'TBAL' });
  } catch (err) { clear(body).appendChild(errorBox(err)); return; }
  const p = (res.plants || []).find(x => x.plant === 'TBAL');
  if (!p) { clear(body).appendChild(empty('The workbooks have no TBAL values for this figure.')); return; }

  const monthlyRows = (p.months || []).map(m => ({ month: m.month, fy: m.fy, value: param === 'diesel' ? m.total : (param === 'afforestation' ? m.total : m.value), raw: m }));
  const yearlyRows = param === 'manpower' ? (p.years || []).map(y => ({ fy: y.fy, value: y.headcount })) : (param === 'revenue' ? (p.years || []).map(y => ({ fy: y.fy, value: y.value })) : []);
  const fys = [...new Set([...monthlyRows.map(r => r.fy), ...yearlyRows.map(r => r.fy)])].filter(Boolean).sort();
  let fy = defaultFy(fys, null);

  function draw() {
    destroyCharts(body);
    clear(body);
    const months = fyMonths(fy);
    const byMonth = Object.fromEntries(monthlyRows.map(r => [r.month, r]));
    const monthly = {};
    [...fyMonths(prevFy(fy)), ...months].forEach(m => { monthly[m] = byMonth[m] && byMonth[m].value !== undefined ? byMonth[m].value : null; });
    body.append(h('div', { class: 'ee-tools' }, fySelect(fys, fy, f => { fy = f; draw(); })),
      kpiTiles({ param, unit: unitOf, fy, monthly, yearly: yearlyRows }));
    const chartCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, `${SHORT[param]}, TBAL, ${fy}`),
      h('p', null, `Monthly, ${unitOf}, from the company workbook (read only)`))));
    const data = months.map(m => (monthly[m] === undefined ? null : monthly[m]));
    if (data.some(v => v !== null)) seriesChart(chartCard, months.map(monthLabel), [{ label: 'Workbook', data, type: 'bar', tone: 'brand', unit: unitOf }], { type: 'bar', yTitle: unitOf, legend: true, height: 'short' });
    else chartCard.append(empty(param === 'manpower' ? 'The workbooks hold manpower only as one figure per year, so there is no monthly chart.' : `No monthly ${SHORT[param].toLowerCase()} values for TBAL in ${fy}.`));
    body.append(h('div', { class: 'ee-block' }, chartCard));

    const cols = [{ key: 'month', label: 'Month', render: r => monthLabel(r.month) }, { key: 'value', label: `Value (${unitOf})`, num: true, render: r => num(r.value) }];
    if (param === 'diesel') ['dg', 'hwg', 'forklift', 'other'].forEach(k => cols.push({ key: k, label: k === 'dg' ? 'DG' : k === 'hwg' ? 'HWG' : k[0].toUpperCase() + k.slice(1), num: true, render: r => num(r.raw[k]) }));
    const rows = months.filter(m => byMonth[m]).map(m => byMonth[m]);
    const tcard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, `Month by month, ${fy}`), h('p', null, 'Source: company workbook. Read only.'))));
    if (param === 'manpower') tcard.append(h('p', { class: 'small muted' }, 'The workbooks hold manpower only as one headcount per financial year.'));
    else tcard.append(table(cols, rows, { emptyText: `No monthly values in ${fy}.` }));
    if (yearlyRows.length) {
      tcard.append(h('h4', { style: { margin: '14px 0 6px' } }, 'Yearly figures in the workbook'));
      tcard.append(table([{ key: 'fy', label: 'Financial year' }, { key: 'value', label: unitOf, num: true, render: r => num(r.value) }], yearlyRows));
    }
    body.append(h('div', { class: 'ee-block' }, tcard));
    if (param === 'manpower' && res.latestSnapshot) {
      const snap = res.latestSnapshot.filter(s => s.plant === 'TBAL');
      if (snap.length) body.append(h('div', { class: 'ee-block' }, h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Latest headcount snapshot (undated)'))),
        table([{ key: 'onRoll', label: 'On roll', num: true, render: r => num(r.onRoll) }, { key: 'contract', label: 'Contract', num: true, render: r => num(r.contract) },
          { key: 'apprentices', label: 'Apprentices', num: true, render: r => num(r.apprentices) }, { key: 'total', label: 'Total', num: true, render: r => num(r.total) }], snap))));
    }
    body.append(notes(res.notes));
  }
  draw();
}
