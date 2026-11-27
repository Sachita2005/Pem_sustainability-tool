// TBAL HVAC & live meters tab (Energy page).
// Three sub-views, kept in the hash query as view=live|schedule|history:
//   live      the ION meter sheet (snapshot workbook now, live server when configured)
//   schedule  HVAC ODU heat-load schedule with the overrides the endpoint accepts
//   history   HVAC energy history, per-unit detail, sheet reconciliation, coverage, SEU
// Every figure comes from /api/tbal/*. The SEU deck transcription is read by eye from a
// picture; it is shown only under a caution and never charted or totalled.

import { api } from '../../api.js';
import { badge, clear, empty, errorBox, fmt, fmtPct, h, load, loading, modal, monthLabel, notes, section, select, table, card, kpi, chips } from '../../ui.js';
import { destroyCharts, seriesChart, targetSeries } from '../../charts.js';

const VIEWS = [
  { id: 'live', label: 'Live meters' },
  { id: 'schedule', label: 'HVAC heat load' },
  { id: 'history', label: 'HVAC history & SEU' }
];

let pollTimer = null;
let debounceTimer = null;
let viewToken = 0;
let alive = false;

function ensureCss() {
  if (document.getElementById('energy-tbal-css')) return;
  document.head.appendChild(h('link', { id: 'energy-tbal-css', rel: 'stylesheet', href: '/static/css/energy-tbal.css' }));
}

export function destroy() {
  alive = false;
  clearInterval(pollTimer);
  clearTimeout(debounceTimer);
  pollTimer = null;
  debounceTimer = null;
  viewToken++;
}

// ------------------------------------------------------------------ helpers

const isNil = v => v === null || v === undefined;
const DASH = '—';
const num = (v, d) => (isNil(v) || Number.isNaN(Number(v)) ? DASH : fmt(v, d === undefined ? (Number.isInteger(Number(v)) ? 0 : 2) : d));
const sum = arr => (arr.some(isNil) ? null : arr.reduce((a, b) => a + b, 0));
const uniq = a => [...new Set(a)];

function fmtTime(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' });
}

function fmtInterval(seconds) {
  if (!seconds) return DASH;
  if (seconds < 120) return seconds + ' seconds';
  if (seconds < 7200) return fmt(seconds / 60, seconds % 60 ? 1 : 0) + ' minutes';
  return fmt(seconds / 3600, 1) + ' hours';
}

// Charts need a laid-out container, so they are drawn just after the block is attached.
function drawChart(host, build) {
  destroyCharts(host);
  clear(host);
  const go = tries => setTimeout(() => {
    if (!host.isConnected && tries < 5) { go(tries + 1); return; }
    if (!host.isConnected) return;
    try { build(host); } catch (err) { console.error(err); clear(host).appendChild(errorBox(err)); }
  }, tries ? 80 : 0);
  go(0);
}

function xTitle(chart, text) {
  if (!chart) return;
  const s = chart.options.scales.x;
  s.title = { display: true, text, color: getComputedStyle(document.documentElement).getPropertyValue('--text-2').trim(), font: { size: 11 } };
  chart.update('none');
}

function setHash(view) {
  const p = new URLSearchParams((location.hash.split('?')[1]) || '');
  p.set('tab', 'tbal');
  p.set('view', view);
  history.replaceState(null, '', '#/energy?' + p.toString());
}

function put(el, ...kids) {
  kids.flat(Infinity).forEach(k => { if (k === null || k === undefined || k === false) return; el.appendChild(k instanceof Node ? k : document.createTextNode(String(k))); });
  return el;
}

const kv = rows => h('dl', { class: 'tb-dl' }, rows.map(([k, v]) => [h('dt', null, k), h('dd', null, isNil(v) || v === '' ? DASH : v)]));

// ------------------------------------------------------------------ entry

export async function render(root, ctx) {
  ensureCss();
  destroy();
  alive = true;
  let view = VIEWS.some(v => v.id === ctx.params.view) ? ctx.params.view : 'live';
  const body = h('div');
  const picker = chips(VIEWS, view, id => show(id));
  picker.el.setAttribute('role', 'group');
  picker.el.setAttribute('aria-label', 'TBAL view');
  root.append(
    h('p', { class: 'tb-sub' }, 'TBAL (Tata Boeing Aerospace, Hyderabad): the ION energy-meter points and the HVAC model. Figures come from the TBAL source workbooks through the API.'),
    h('div', { class: 'tb-views' }, picker.el),
    body);

  function show(id) {
    view = id;
    viewToken++;
    clearInterval(pollTimer);
    clearTimeout(debounceTimer);
    pollTimer = null;
    destroyCharts(body);
    clear(body);
    picker.set(id);
    setHash(id);
    const token = viewToken;
    if (id === 'live') liveView(body, token);
    else if (id === 'schedule') scheduleView(body, token);
    else historyView(body, token);
  }
  show(view);
}

// =================================================================== LIVE

const KIND_LABEL = { energy: 'Energy meter', temperature: 'Temperature sensor', pressure: 'Pressure sensor', service: 'ION system service' };
const KIND_TONE = { energy: 'brand', temperature: 'info', pressure: 'info', service: '' };
const GROUPINGS = [
  { id: 'section', label: 'Section (as the sheet groups)' },
  { id: 'seu', label: 'SEU chart category' },
  { id: 'bay', label: 'Bay (HVAC rows only)' },
  { id: 'tagGroup', label: 'ION tag group' }
];

function groupOf(m, by) {
  if (by === 'section') return isNil(m.sectionNo) ? (m.section || '(none)') : m.sectionNo + '. ' + (m.section || '');
  return m[by] || '(none)';
}

function liveView(box, token) {
  const S = { payload: null, status: null, statusError: null, fetchedAt: null, error: null, kind: 'all', by: 'section', group: 'all', q: '' };
  const bannerBox = h('div');
  const tilesBox = h('div');
  const toolbarBox = h('div');
  const listBox = h('div');
  const notesBox = h('div');

  async function fetchAll(manual) {
    const [m, s] = await Promise.allSettled([
      api.get('/tbal/meters', null, { fresh: true }),
      api.get('/tbal/connector/status', null, { fresh: true })
    ]);
    if (token !== viewToken) return false;
    if (m.status === 'rejected') {
      S.error = m.reason && m.reason.message ? m.reason.message : String(m.reason);
      return false;
    }
    S.error = null;
    S.payload = m.value;
    S.status = s.status === 'fulfilled' ? s.value : null;
    S.statusError = s.status === 'rejected' ? (s.reason && s.reason.message) : null;
    S.fetchedAt = new Date();
    return true;
  }

  function pollMs() {
    const sec = (S.status && S.status.pollSeconds) || (S.payload && S.payload.pollSeconds) || null;
    return sec ? Math.max(5, sec) * 1000 : null;
  }

  function startTimer() {
    clearInterval(pollTimer);
    pollTimer = null;
    const ms = pollMs();
    if (!ms) return;
    pollTimer = setInterval(() => refresh(false), ms);
  }

  async function refresh(manual) {
    if (token !== viewToken) return;
    const btn = toolbarBox.querySelector('[data-refresh]');
    if (btn) btn.disabled = true;
    const ok = await fetchAll(manual);
    if (token !== viewToken) return;
    if (btn) btn.disabled = false;
    if (ok) startTimer();
    drawAll();
  }

  // ---- banner
  function drawBanner() {
    const p = S.payload, st = S.status;
    const isLive = p.source === 'live';
    const failed = !isLive && p.liveTried && p.liveError;
    const configured = st ? st.configured : null;
    let title;
    if (isLive) title = 'Live from ION server';
    else if (failed) title = 'Snapshot workbook (live read from the ION server failed)';
    else if (configured === false || (configured === null && !p.liveTried)) title = 'Snapshot workbook (ION server not configured)';
    else title = 'Snapshot workbook';
    const meta = [
      ['Read time', fmtTime(p.readAt)],
      ['Time is', p.readAtKind || null],
      ['Snapshot file', p.snapshotFile || (st && st.snapshotFile)],
      ['Live read tried', p.liveTried ? 'yes' : 'no'],
      ['Auto-refresh', pollMs() ? 'every ' + fmtInterval(pollMs() / 1000) + ' (connector poll interval)' : 'off (no interval reported)'],
      ['This page fetched', S.fetchedAt ? S.fetchedAt.toLocaleTimeString('en-IN') : null]
    ].filter(([, v]) => v);
    if (st) {
      meta.push(['Connector', st.configured ? 'configured' + (st.serverUrl ? ' (' + st.serverUrl + ')' : '') : 'not configured']);
      if (st.lastAttempt) meta.push(['Last live attempt', fmtTime(st.lastAttempt)]);
      if (st.lastSuccess) meta.push(['Last live success', fmtTime(st.lastSuccess)]);
    }
    clear(bannerBox).appendChild(h('div', { class: 'card tb-banner ' + (isLive ? 'live' : failed ? 'failed' : 'snapshot'), role: 'status' },
      h('div', { class: 'tb-banner-title' },
        badge(isLive ? 'LIVE' : 'SNAPSHOT', isLive ? 'good' : 'warn'), title),
      failed ? h('div', { class: 'tb-reason' }, 'Reason: ' + p.liveError) : null,
      (!failed && st && st.lastError) ? h('div', { class: 'tb-reason' }, 'Last connector error: ' + st.lastError) : null,
      S.statusError ? h('div', { class: 'tb-reason' }, 'Connector status could not be read: ' + S.statusError) : null,
      S.error ? h('div', { class: 'tb-reason' }, 'The last refresh failed (' + S.error + '); the figures below are from the previous read.') : null,
      h('div', { class: 'tb-banner-meta' }, meta.map(([k, v]) => h('span', null, k + ': ', h('b', null, v))))));
  }

  // ---- tiles
  function drawTiles() {
    const b = S.payload.breakdown || {};
    const by = b.byKind || {};
    clear(tilesBox).appendChild(h('div', { class: 'grid kpis' },
      kpi({ label: 'Energy meters', value: num(by.energy), sub: 'instrument "Energy Meter"', tone: 'good' }),
      kpi({ label: 'Temperature sensors', value: num(by.temperature), sub: 'instrument "Temperature" (readings in °C where the unit is known)', tone: 'info' }),
      kpi({ label: 'Pressure sensors', value: num(by.pressure), sub: 'instrument "Pressure"', tone: 'info' }),
      kpi({ label: 'ION system-service rows', value: num(by.service), sub: 'section 5 rows with a blank instrument; not meters' }),
      kpi({ label: 'Rows in the sheet', value: num(b.rowsInSheet !== undefined ? b.rowsInSheet : S.payload.count), sub: 'the sheet calls them "' + num(b.statedMeterCount) + ' meters"; only ' + num(b.energyMeters) + ' are energy meters', tone: 'warn' })));
  }

  // ---- toolbar (search and selects persist; chips and group list are rebuilt)
  const search = h('input', {
    class: 'input', type: 'search', placeholder: 'Name, tag, measures, SEU, bay, id…', 'aria-label': 'Search points',
    onInput: e => { S.q = e.target.value; drawList(); }
  });
  const kindBox = h('div');
  const groupBox = h('div');
  const bySel = select(GROUPINGS, S.by, v => { S.by = v; S.group = 'all'; drawToolbar(); drawList(); }, { 'aria-label': 'Group points by' });
  const refreshBtn = h('button', { type: 'button', class: 'btn', 'data-refresh': '', onClick: () => refresh(true) }, '↻ Refresh now');

  function drawToolbar() {
    const ms = S.payload.meters || [];
    const kinds = ['energy', 'temperature', 'pressure', 'service'].filter(k => ms.some(m => m.kind === k));
    const opts = [{ id: 'all', label: 'All (' + ms.length + ')' }].concat(kinds.map(k => ({ id: k, label: KIND_LABEL[k] + ' (' + ms.filter(m => m.kind === k).length + ')' })));
    const kc = chips(opts, S.kind, id => { S.kind = id; drawList(); });
    kc.el.setAttribute('aria-label', 'Filter by kind');
    clear(kindBox).appendChild(kc.el);
    const groups = orderedGroups(ms, S.by);
    clear(groupBox).appendChild(select([{ id: 'all', label: 'All groups' }].concat(groups.map(g => ({ id: g, label: g }))), S.group, v => { S.group = v; drawList(); }, { 'aria-label': 'Filter by group' }));
  }

  function orderedGroups(ms, by) {
    const names = uniq(ms.map(m => groupOf(m, by)));
    if (by === 'section') return names.sort((a, b) => (parseInt(a, 10) || 99) - (parseInt(b, 10) || 99));
    return names.sort((a, b) => (a === '(none)') - (b === '(none)') || a.localeCompare(b));
  }

  function matches(m) {
    if (S.kind !== 'all' && m.kind !== S.kind) return false;
    if (S.group !== 'all' && groupOf(m, S.by) !== S.group) return false;
    const q = S.q.trim().toLowerCase();
    if (!q) return true;
    return [m.id, m.name, m.tag, m.quantity, m.seu, m.section, m.bay, m.ahu, m.tagGroup, m.deviceType, m.unit]
      .some(v => !isNil(v) && String(v).toLowerCase().includes(q));
  }

  // ---- list
  function drawList() {
    const p = S.payload;
    const ms = p.meters || [];
    const shown = ms.filter(matches);
    const inferred = p.source === 'snapshot';
    clear(listBox);
    listBox.appendChild(h('div', { class: 'tb-count', 'aria-live': 'polite' }, 'Showing ' + shown.length + ' of ' + ms.length + ' points, grouped by ' + GROUPINGS.find(g => g.id === S.by).label.toLowerCase() + '.'));
    if (!shown.length) { listBox.appendChild(empty('No point matches these filters.')); return; }
    const cols = [
      { key: 'id', label: 'ID', num: true },
      { label: 'Name', render: m => h('button', { type: 'button', class: 'tb-pick', title: m.tag, onClick: () => openMeter(m) }, m.name || m.tag || String(m.id)) },
      { label: 'Kind', render: m => badge(KIND_LABEL[m.kind] || m.kind || DASH, KIND_TONE[m.kind]) },
      { label: 'Measures', render: m => m.quantity },
      { label: 'Reading', num: true, render: m => (isNil(m.reading) ? null : fmt(m.reading, Number.isInteger(m.reading) ? 0 : 3)) },
      { label: inferred ? 'Unit (inferred)' : 'Unit', render: m => (m.unit ? [m.unit, inferred ? h('span', { class: 'tb-inf', title: 'The sheet has no unit column; inferred from the parameter name' }, 'inferred') : null] : null) },
      { label: 'Reading time', render: m => (m.timestamp ? fmtTime(m.timestamp) : null) }
    ];
    const byGroup = new Map();
    shown.forEach(m => { const g = groupOf(m, S.by); if (!byGroup.has(g)) byGroup.set(g, []); byGroup.get(g).push(m); });
    orderedGroups(shown, S.by).forEach(g => {
      const rows = byGroup.get(g);
      const kinds = uniq(rows.map(m => KIND_LABEL[m.kind] || m.kind));
      listBox.appendChild(h('details', { class: 'tb-group card', open: true },
        h('summary', null, g, h('small', null, rows.length + ' point' + (rows.length === 1 ? '' : 's') + ' · ' + kinds.join(', '))),
        table(cols, rows)));
    });
    listBox.appendChild(h('p', { class: 'tb-count' }, 'A dash means the value is not in the source: the sheet has no timestamps, and units are only known for some parameters. “Reading” is the sheet’s Live_Value column; it does not always agree with the parameter name, so open a point to see all of its columns.'));
  }

  // ---- detail
  function openMeter(m) {
    const bodyEl = h('div', null, loading());
    modal((m.name || m.tag || 'Point') + ' (ID ' + m.id + ')', bodyEl);
    api.get('/tbal/meters/' + encodeURIComponent(m.id), null, { fresh: true }).then(res => {
      const r = res.meter || m;
      const feeders = ((S.payload.seuChartMap) || []).filter(x => x.chartBar && x.chartBar === r.seu).map(x => x.feeders);
      const warn = [];
      if (/frequency/i.test(r.quantity || '') && r.reading === 0 && r.powerKwHr > 0) warn.push('Live_Value is 0 while Power_kW_hr is ' + fmt(r.powerKwHr, 3) + ': the two columns disagree for this row.');
      if (/interruption/i.test(r.quantity || '') && !isNil(r.reading) && r.reading === r.powerKwHr) warn.push('Live_Value and Power_kW_hr hold the same number (' + fmt(r.reading, 0) + '): the sheet repeats the count in both columns.');
      const raw = v => (isNil(v) ? null : String(v));
      put(clear(bodyEl),
        ...warn.map(w => h('div', { class: 'tb-warnline' }, w)),
        kv([
          ['ID (SourceID)', raw(r.id)], ['Full ION tag', r.tag], ['Name', r.name], ['Tag group', r.tagGroup],
          ['Kind', KIND_LABEL[r.kind] || r.kind], ['Device type (Instrument)', r.deviceType], ['Parameter (Latest_Parameter)', r.quantity],
          ['Reading (Live_Value)', raw(r.reading)], ['Unit', r.unit ? r.unit + (res.source === 'snapshot' ? ' (inferred from the parameter name)' : '') : null],
          ['Current_A', raw(r.currentA)], ['Voltage_V', raw(r.voltageV)], ['Power_kW_hr', raw(r.powerKwHr)],
          ['Timestamp', r.timestamp ? fmtTime(r.timestamp) : null],
          ['Section', isNil(r.sectionNo) ? r.section : r.sectionNo + '. ' + r.section], ['SEU chart category', r.seu],
          ['Flag (unlabelled column)', r.individualFlag], ['AHU label (unlabelled column)', r.ahu], ['Bay label (unlabelled column)', r.bay],
          ['Feeders behind this SEU bar (sheet table)', feeders.length ? feeders.join(' | ') : null]
        ]),
        h('p', { class: 'tb-count' }, 'Read ' + fmtTime(res.readAt) + (res.readAtKind ? ' (' + res.readAtKind + ')' : '') + '. A dash means the sheet has no value.'),
        notes(res.notes));
    }).catch(err => clear(bodyEl).appendChild(errorBox(err)));
  }

  function drawAll() {
    if (token !== viewToken || !S.payload) return;
    drawBanner();
    drawTiles();
    drawToolbar();
    drawList();
    clear(notesBox).appendChild(notes(S.payload.notes) || h('span'));
  }

  // ---- first load
  clear(box).appendChild(loading('Reading the meter sheet…'));
  fetchAll(false).then(ok => {
    if (token !== viewToken) return;
    clear(box);
    if (!ok) {
      box.appendChild(errorBox(S.error));
      box.appendChild(h('p', null, h('button', { type: 'button', class: 'btn', onClick: () => liveView(clear(box), token) }, 'Try again')));
      return;
    }
    put(box,
      bannerBox, h('div', { style: { marginTop: '14px' } }, tilesBox),
      h('div', { class: 'tb-toolbar' },
        kindBox,
        h('label', { class: 'field' }, 'Group by', bySel),
        h('label', { class: 'field' }, 'Group', groupBox),
        h('label', { class: 'field grow' }, 'Search', search),
        refreshBtn),
      listBox, notesBox);
    drawAll();
    startTimer();
  });
}

// =================================================================== SCHEDULE

const FIELDS = [
  { key: 'kwPerTr', param: 'kw_per_tr', label: 'kW per TR', unit: 'kW per TR', step: '0.01', digits: 2 },
  { key: 'hoursPerDay', param: 'hours_per_day', label: 'Operating hours per day', unit: 'hours per day', step: '0.5', digits: 1 },
  { key: 'baseLoadShare', param: 'base_load_share', label: 'Base-load share (scenario B)', unit: 'fraction of installed TR', step: '0.05', digits: 2 },
  { key: 'installedTr', param: 'installed_tr', label: 'Installed capacity', unit: 'TR', step: '1', digits: 1 },
  { key: 'designDeltaT', param: 'design_delta_t', label: 'Design temperature difference ΔT', unit: '°C', step: '0.5', digits: 1 }
];

const METRICS = [
  { id: 'tr', label: 'Cooling load (TR)', unit: 'TR', field: 'tr', ref: 'fullLoadTr', refLabel: 'Installed capacity (full load)' },
  { id: 'pct', label: 'Load (% of installed)', unit: '% of installed TR', field: 'loadPct', scale: 100 },
  { id: 'kw', label: 'Electrical demand (kW)', unit: 'kW', field: 'kw', ref: 'fullLoadKw', refLabel: 'Full-load demand' },
  { id: 'kwh', label: 'Energy (kWh per day)', unit: 'kWh per day', field: 'kwh', ref: 'fullLoadKwhPerDay', refLabel: 'Full-load energy per day' }
];

function plainMessage(msg) {
  let m = /less than or equal to ([\d.]+)/i.exec(msg);
  if (m) return 'Too high: it must be ' + m[1] + ' or less.';
  m = /greater than or equal to ([\d.-]+)/i.exec(msg);
  if (m) return 'Too low: it must be ' + m[1] + ' or more.';
  m = /less than ([\d.]+)/i.exec(msg);
  if (m) return 'Too high: it must be below ' + m[1] + '.';
  m = /greater than ([\d.-]+)/i.exec(msg);
  if (m) return 'Too low: it must be above ' + m[1] + '.';
  if (/valid number|parsing/i.test(msg)) return 'This is not a valid number.';
  return msg;
}

async function getSchedule(params) {
  let res;
  try { res = await fetch(api.url('/tbal/hvac/schedule', params)); }
  catch (e) { throw Object.assign(new Error('The server could not be reached. Check that the app is running.'), { status: 0 }); }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (!res.ok) throw Object.assign(new Error((data && typeof data.detail === 'string' && data.detail) || res.statusText || 'Request failed'), { status: res.status, detail: data && data.detail });
  return data;
}

function scheduleView(box, token) {
  const S = { defaults: null, last: null, metric: 'tr', seq: 0, errors: {} };
  const inputs = {};
  const hints = {};
  const errs = {};
  const badges = {};
  const staleBox = h('div');
  const resultsBox = h('div');
  const caveatBox = h('div');

  function buildForm(p) {
    const form = h('div', { class: 'tb-inputs' });
    FIELDS.forEach(f => {
      const def = p.defaults[f.key];
      inputs[f.key] = h('input', {
        class: 'input', type: 'number', step: f.step, inputmode: 'decimal', id: 'tb-in-' + f.key, value: def,
        'aria-describedby': 'tb-hint-' + f.key + ' tb-err-' + f.key,
        onInput: () => { clearTimeout(debounceTimer); debounceTimer = setTimeout(recalc, 400); },
        onChange: () => { clearTimeout(debounceTimer); recalc(); }
      });
      badges[f.key] = h('span');
      errs[f.key] = h('div', { class: 'tb-err', id: 'tb-err-' + f.key, role: 'alert' });
      const src = f.key === 'kwPerTr'
        ? (p.kwPerTrSource || 'documented benchmark') + '; the workbook cell C8 holds “' + (p.kwPerTrWorkbookCell === null ? DASH : p.kwPerTrWorkbookCell) + '”, not a number'
        : (p.workbook || 'workbook') + ', “Load & Energy Schedule” sheet input';
      hints[f.key] = h('div', { class: 'tb-hint', id: 'tb-hint-' + f.key }, 'Default ' + fmt(def, f.digits) + ' ' + f.unit + ' · from ' + src);
      form.appendChild(h('label', { class: 'tb-field', for: 'tb-in-' + f.key }, h('span', null, f.label + ' (' + f.unit + ') ', badges[f.key]), inputs[f.key], hints[f.key], errs[f.key]));
    });
    return form;
  }

  function currentParams() {
    const params = {};
    FIELDS.forEach(f => {
      const v = inputs[f.key].value.trim();
      if (v !== '' && Number(v) !== Number(S.defaults[f.key])) params[f.param] = v;
    });
    return params;
  }

  function markEdited() {
    FIELDS.forEach(f => {
      const v = inputs[f.key].value.trim();
      const edited = v !== '' && Number(v) !== Number(S.defaults[f.key]);
      clear(badges[f.key]);
      if (edited) badges[f.key].appendChild(badge('changed', 'info'));
    });
  }

  function showErrors(detail, fallbackMessage) {
    const general = [];
    FIELDS.forEach(f => { errs[f.key].textContent = ''; inputs[f.key].removeAttribute('aria-invalid'); });
    (Array.isArray(detail) ? detail : []).forEach(d => {
      const name = d.loc && d.loc[d.loc.length - 1];
      const f = FIELDS.find(x => x.param === name);
      const msg = plainMessage(d.msg || 'Not accepted');
      if (f) { errs[f.key].textContent = msg; inputs[f.key].setAttribute('aria-invalid', 'true'); }
      else general.push(msg);
    });
    if (!Array.isArray(detail) && fallbackMessage) general.push(fallbackMessage);
    clear(staleBox);
    staleBox.appendChild(h('div', { class: 'tb-warnline', role: 'status' },
      'These values were not accepted by the calculation' + (general.length ? ' (' + general.join('; ') + ')' : '') + '. The table and chart below still show the last valid result.'));
  }

  function clearErrors() {
    FIELDS.forEach(f => { errs[f.key].textContent = ''; inputs[f.key].removeAttribute('aria-invalid'); });
    clear(staleBox);
  }

  async function recalc() {
    if (token !== viewToken) return;
    markEdited();
    const seq = ++S.seq;
    resultsBox.classList.add('tb-busy');
    try {
      const p = await getSchedule(currentParams());
      if (seq !== S.seq || token !== viewToken) return;
      S.last = p;
      clearErrors();
      drawResults();
    } catch (err) {
      if (seq !== S.seq || token !== viewToken) return;
      if (err.status === 422) showErrors(err.detail, err.message);
      else showErrors(null, err.message);
    } finally {
      if (seq === S.seq) resultsBox.classList.remove('tb-busy');
    }
  }

  function reset() {
    FIELDS.forEach(f => { inputs[f.key].value = S.defaults[f.key]; });
    clearErrors();
    recalc();
  }

  function drawResults() {
    const p = S.last;
    if (!p) return;
    const i = p.inputs;
    const metric = METRICS.find(m => m.id === S.metric);
    const rows = p.rows || [];
    const chartHost = h('div');
    const picker = chips(METRICS.map(m => ({ id: m.id, label: m.label })), S.metric, id => { S.metric = id; drawResults(); });
    picker.el.setAttribute('aria-label', 'Chart quantity');
    const t = p.totals || {};
    const val = (r, sc, f) => {
      const v = r[sc] && r[sc][f];
      return isNil(v) ? null : v;
    };
    const cols = [
      { key: 'deltaT', label: 'ΔT (°C)', num: true, render: r => num(r.deltaT, 1) },
      { label: 'A · TR', num: true, render: r => num(val(r, 'proRata', 'tr'), 1) },
      { label: 'A · load %', num: true, render: r => (isNil(val(r, 'proRata', 'loadPct')) ? null : fmtPct(val(r, 'proRata', 'loadPct') * 100, 1)) },
      { label: 'A · kW', num: true, render: r => num(val(r, 'proRata', 'kw'), 1) },
      { label: 'A · kWh/day', num: true, render: r => num(val(r, 'proRata', 'kwh'), 0) },
      { label: 'B · TR', num: true, render: r => num(val(r, 'realWorld', 'tr'), 1) },
      { label: 'B · load %', num: true, render: r => (isNil(val(r, 'realWorld', 'loadPct')) ? null : fmtPct(val(r, 'realWorld', 'loadPct') * 100, 1)) },
      { label: 'B · kW', num: true, render: r => num(val(r, 'realWorld', 'kw'), 1) },
      { label: 'B · kWh/day', num: true, render: r => num(val(r, 'realWorld', 'kwh'), 0) }
    ];
    const over = (p.overridden || []).length
      ? badge('overridden: ' + p.overridden.join(', '), 'info')
      : badge('workbook values', 'good');

    put(clear(resultsBox),
      h('div', { class: 'grid kpis' },
        kpi({ label: 'Full load', value: num(t.fullLoadTr), unit: 'TR', sub: 'installed capacity at ΔT = design ΔT', tone: 'info' }),
        kpi({ label: 'Full-load demand', value: num(t.fullLoadKw), unit: 'kW', sub: 'at ' + fmt(i.kwPerTr, 2) + ' kW per TR' }),
        kpi({ label: 'Full-load energy', value: num(t.fullLoadKwhPerDay, 0), unit: 'kWh per day', sub: 'over ' + fmt(i.hoursPerDay, 1) + ' operating hours per day' }),
        kpi({ label: 'Idle base load (scenario B)', value: num(t.idleBaseLoadTr), unit: 'TR', sub: 'at ΔT = 0, base-load share ' + fmt(i.baseLoadShare, 2) })),
      h('div', { class: 'tb-sub', style: { marginTop: '10px' } }, 'Calculated with: ', over, ' installed ' + fmt(i.installedTr, 1) + ' TR · design ΔT ' + fmt(i.designDeltaT, 1) + ' °C · base-load share ' + fmt(i.baseLoadShare, 2) + ' · ' + fmt(i.kwPerTr, 2) + ' kW per TR · ' + fmt(i.hoursPerDay, 1) + ' h per day' + (p.design ? ' · design conditions: ambient ' + fmt(p.design.ambientC, 1) + ' °C, set point ' + fmt(p.design.setC, 1) + ' °C' : '')),
      h('div', { class: 'card', style: { marginTop: '14px' } },
        h('div', { class: 'card-head' }, h('div', null,
          h('h3', null, metric.label + ' against temperature difference'),
          h('p', null, 'Unit: ' + metric.unit + ' · horizontal axis: ΔT, the temperature drop in °C (0 to design ΔT) · scenario A scales load pro-rata with ΔT; scenario B keeps a base load that is always on'))),
        picker.el, chartHost),
      h('div', { class: 'section' },
        h('div', { class: 'section-head' }, h('h2', null, 'Schedule table'), h('p', { class: 'small' }, 'A = pro-rata, B = real-world (base load + pro-rata). TR = tons of refrigeration; kWh per day = kW × operating hours per day.')),
        table(cols, rows, { emptyText: 'The schedule returned no rows.' })),
      notes(p.notes));

    drawChart(chartHost, host => {
      const labels = rows.map(r => fmt(r.deltaT, Number.isInteger(r.deltaT) ? 0 : 1));
      const pick = (sc) => rows.map(r => {
        const v = val(r, sc, metric.field);
        return isNil(v) ? null : v * (metric.scale || 1);
      });
      const series = [
        { label: 'Scenario A: pro-rata', data: pick('proRata'), unit: metric.unit, tone: 'brand' },
        { label: 'Scenario B: real-world (base load + pro-rata)', data: pick('realWorld'), unit: metric.unit, tone: 'warn' }
      ];
      if (metric.ref && !isNil(t[metric.ref])) series.push({ ...targetSeries(metric.refLabel, t[metric.ref], rows.length, 'muted'), unit: metric.unit });
      const c = seriesChart(host, labels, series, { type: 'line', yTitle: metric.label, height: 'tall', legend: true, digits: metric.id === 'pct' ? 1 : 1 });
      xTitle(c, 'Temperature difference ΔT (°C)');
    });
  }

  function drawCaveats(p) {
    const box2 = h('div', { class: 'tb-callout' },
      h('strong', null, 'Read this before using the kW and kWh figures'),
      h('ul', null,
        h('li', null, 'The workbook’s own kW and kWh columns show #VALUE!: its efficiency cell (C8, “ODU Compressor Efficiency Ratio”) holds “' + (p.kwPerTrWorkbookCell === null ? DASH : p.kwPerTrWorkbookCell) + '”, not a number. TR and load-% columns are not affected.'),
        h('li', null, 'The kW and kWh here therefore use ' + fmt(p.defaults.kwPerTr, 2) + ' kW per TR, the benchmark documented on the workbook’s “Formulas & Derivations” sheet. It is a documented assumption, not a measurement; kW per TR is held constant across part load.'),
        h('li', null, 'The sources disagree on installed capacity: this schedule uses ' + fmt(p.defaults.installedTr, 1) + ' TR (schedule workbook). ', h('span', { id: 'tb-cap-other' }, 'Loading the other sources…'))));
    clear(caveatBox).appendChild(box2);
    Promise.allSettled([api.get('/tbal/hvac/history'), api.get('/tbal/hvac/coverage')]).then(([hs, cv]) => {
      if (token !== viewToken) return;
      const slot = box2.querySelector('#tb-cap-other');
      if (!slot) return;
      const parts = [];
      if (hs.status === 'fulfilled') {
        const f = (hs.value.facility || [])[0];
        if (f && !isNil(f['Total Connected Load (TR)'])) parts.push('ECHO workbook Sheet1: ' + fmt(f['Total Connected Load (TR)'], 1) + ' TR' + (isNil(f['Total Connected load (Kw)']) ? '' : ' (' + fmt(f['Total Connected load (Kw)'], 1) + ' kW)') + '.');
      }
      if (cv.status === 'fulfilled') {
        (cv.value.notes || []).filter(n => /outdoor units/i.test(n)).forEach(n => parts.push('Coverage deck, API note: ' + n));
      }
      slot.textContent = parts.length ? parts.join(' ') : 'The other sources could not be loaded here.';
    });
    return box2;
  }

  clear(box).appendChild(loading('Loading the HVAC schedule…'));
  getSchedule({}).then(p => {
    if (token !== viewToken) return;
    S.defaults = p.defaults;
    S.last = p;
    clear(box);
    put(box,
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Inputs'),
          h('p', null, 'Change a value to recalculate. Source: ' + (p.workbook || 'HVAC ODU load and energy schedule workbook')))),
        buildForm(p),
        staleBox,
        h('div', { class: 'tb-actions', style: { marginTop: '12px' } },
          h('button', { type: 'button', class: 'btn', onClick: reset }, 'Reset to workbook values'),
          h('span', { class: 'tb-hint' }, 'Blank fields use the workbook value.'))),
      h('div', { style: { marginTop: '14px' } }, drawCaveats(p)),
      h('div', { style: { marginTop: '14px' } }, resultsBox));
    markEdited();
    drawResults();
  }).catch(err => { if (token === viewToken) { clear(box).appendChild(errorBox(err)); } });
}

// =================================================================== HISTORY & SEU

const COVERAGE_LABELS = {
  bayLengthM: ['Length of each bay', 'm'], bayWidthM: ['Width of each bay', 'm'], bayMaxHeightM: ['Bay height, maximum', 'm'], bayMinHeightM: ['Bay height, minimum', 'm'],
  bays: ['Identical bays', ''], hvacPerBay: ['HVAC units per bay', ''], hvacTotal: ['HVAC units, both bays', ''],
  outdoorUnitsPerHvac: ['Outdoor units per HVAC', ''], indoorUnitsPerHvac: ['Indoor units (AHU) per HVAC', ''],
  supplyDuctHeightM: ['Supply duct height', 'm'], returnDuctHeightM: ['Return duct height', 'm'],
  recommendedTempMinC: ['Recommended temperature, low', '°C'], recommendedTempMaxC: ['Recommended temperature, high', '°C'],
  outdoorUnitCoolingKwThermal: ['Outdoor unit cooling capacity', 'kW (thermal)'], outdoorUnitCoolingInputKw: ['Outdoor unit power input, cooling', 'kW (electrical)'],
  outdoorUnitModel: ['Outdoor unit model', ''], partitionBetweenBays: ['Partition between the bays', '']
};

const UNIT_SECTIONS = [
  { id: 'total', label: 'Total energy', unit: 'kWh', note: 'fixed + variable' },
  { id: 'fixed', label: 'Fixed (indoor AHU blowers)', unit: 'kWh', note: 'running hours × 7 (workbook formula)' },
  { id: 'variable', label: 'Variable (outdoor units)', unit: 'kWh', note: 'total − fixed (workbook formula); small negative values occur in idle months' },
  { id: 'hours', label: 'Running hours', unit: 'hours', note: 'hours' }
];

function historyView(box, token) {
  const S = { fy: 'all', unitFy: null, unitSection: 'total', recBay: 'all', recFy: 'all' };
  const ok = () => token === viewToken;
  clear(box);

  const histBox = h('div');
  const unitsBox = h('div');
  const recBox = h('div');
  const covBox = h('div');
  const seuBox = h('div');
  box.append(histBox, unitsBox, recBox, covBox, seuBox);

  // -------- history: yearly and monthly
  const histPromise = api.get('/tbal/hvac/history');
  load(histBox, histPromise, hist => {
    const bays = hist.bays || [];
    const years = hist.years || [];
    if (!bays.length) return [empty('No HVAC history was found in the ECHO workbook.'), notes(hist.notes)];
    const yearly = h('div');
    const monthlyBox = h('div');
    const chartYear = h('div');

    // yearly chart + table
    const sumHours = (b, fy) => sum(Object.values((b.monthlyRunningHours || {})[fy] || {}).map(v => (isNil(v) ? null : v))) ;
    const yearlyCols = [{ key: 'fy', label: 'Financial year' }];
    bays.forEach(b => {
      yearlyCols.push({ label: b.bay + ' energy (kWh)', num: true, render: r => num(r[b.bay].e, 0) });
      yearlyCols.push({ label: b.bay + ' sheet total (kWh)', num: true, render: r => num(r[b.bay].rep, 0) });
      yearlyCols.push({ label: b.bay + ' running hours (sum of months)', num: true, render: r => num(r[b.bay].hrs, 0) });
    });
    const yearlyRows = years.map(fy => {
      const row = { fy };
      bays.forEach(b => { row[b.bay] = { e: (b.sumOfMonthsKwh || {})[fy], rep: (b.reportedTotalEnergyKwh || {})[fy], hrs: sumHours(b, fy) }; });
      return row;
    });
    put(yearly,
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Yearly HVAC energy per bay'), h('p', null, 'Unit: kWh per financial year (April to March), sum of the monthly figures · source: ' + (hist.workbook || 'ECHO workbook') + ', “Energy Consumption” sheet'))),
        h('div', { class: 'chart-host' })),
      h('div', { style: { marginTop: '10px' } }, table(yearlyCols, yearlyRows)),
      h('p', { class: 'tb-count' }, '“Sheet total” is the workbook’s own Total row; where it is blank (FY20-21) it shows a dash and the energy column is the sum of the months, computed here. Running hours are the sum of the monthly figures, computed here.'));
    const yHost = yearly.querySelector('.chart-host');
    drawChart(yHost, host => seriesChart(host, years, bays.map(b => ({ label: b.bay, unit: 'kWh', data: years.map(fy => { const v = (b.sumOfMonthsKwh || {})[fy]; return isNil(v) ? null : v; }) })), { type: 'bar', yTitle: 'HVAC energy (kWh per FY)' }));

    // monthly
    const fyOptions = [{ id: 'all', label: 'All years' }].concat(years.map(y => ({ id: y, label: y })));
    function drawMonthly() {
      const fys = S.fy === 'all' ? years : [S.fy];
      const months = [];
      fys.forEach(fy => bays.forEach(b => {
        Object.keys((b.monthlyEnergyKwh || {})[fy] || {}).forEach(m => months.push(m));
        Object.keys((b.monthlyRunningHours || {})[fy] || {}).forEach(m => months.push(m));
      }));
      const ms = uniq(months).sort();
      const get = (b, key, m) => {
        for (const fy of fys) { const o = (b[key] || {})[fy]; if (o && m in o) return isNil(o[m]) ? null : o[m]; }
        return null;
      };
      const labels = ms.map(monthLabel);
      const eHost = h('div'), hHost = h('div');
      const single = S.fy !== 'all';
      const cols = [{ label: 'Month', render: r => monthLabel(r.m) }];
      bays.forEach(b => {
        cols.push({ label: b.bay + ' energy (kWh)', num: true, render: r => num(get(b, 'monthlyEnergyKwh', r.m), 0) });
        cols.push({ label: b.bay + ' running hours', num: true, render: r => num(get(b, 'monthlyRunningHours', r.m), 0) });
      });
      put(clear(monthlyBox),
        h('div', { class: 'grid cols-2' },
          h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Monthly HVAC energy'), h('p', null, 'Unit: kWh per month · ' + (single ? S.fy : 'FY20-21 onwards')))), eHost),
          h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Monthly HVAC running hours'), h('p', null, 'Unit: hours per month, summed over the bay’s units · ' + (single ? S.fy : 'FY20-21 onwards')))), hHost)),
        h('div', { style: { marginTop: '10px' } }, table(cols, ms.map(m => ({ m })))));
      drawChart(eHost, host => seriesChart(host, labels, bays.map(b => ({ label: b.bay, unit: 'kWh', points: single, data: ms.map(m => get(b, 'monthlyEnergyKwh', m)) })), { type: single ? 'bar' : 'line', yTitle: 'kWh per month' }));
      drawChart(hHost, host => seriesChart(host, labels, bays.map(b => ({ label: b.bay, unit: 'hours', points: single, data: ms.map(m => get(b, 'monthlyRunningHours', m)) })), { type: single ? 'bar' : 'line', yTitle: 'hours per month' }));
    }
    const fySel = select(fyOptions, S.fy, v => { S.fy = v; drawMonthly(); }, { 'aria-label': 'Financial year for monthly view' });
    drawMonthly();

    const eq = (hist.equipmentInputs || []).filter(e => !isNil(e.value));
    return [
      section('HVAC energy by financial year', 'Bay-1 and Bay-2, FY20-21 to the latest year in the workbook', yearly),
      section('HVAC energy by month', 'Choose a financial year or see all of them', h('div', { class: 'tb-toolbar' }, h('label', { class: 'field' }, 'Financial year', fySel)), monthlyBox),
      eq.length ? section('Name-plate inputs', 'From the ECHO workbook “Inputs” sheet; entries without a value are skipped', table([{ key: 'label', label: 'Item' }, { label: 'Value', render: e => String(e.value) }], eq)) : null,
      notes(hist.notes)
    ];
  });

  // -------- per-unit detail
  histPromise.then(hist => {
    if (!ok()) return;
    const years = hist.years || [];
    if (!years.length) { clear(unitsBox).appendChild(section('HVAC units', null, empty('No financial years found.'))); return; }
    S.unitFy = S.unitFy || years[years.length - 1];
    const body = h('div');
    const fySel = select(years.map(y => ({ id: y, label: y })), S.unitFy, v => { S.unitFy = v; drawUnits(); }, { 'aria-label': 'Financial year for unit detail' });
    const secChips = chips(UNIT_SECTIONS.map(s => ({ id: s.id, label: s.label })), S.unitSection, id => { S.unitSection = id; drawUnits(); });
    secChips.el.setAttribute('aria-label', 'Energy component');
    function drawUnits() {
      load(body, api.get('/tbal/hvac/units', { fy: S.unitFy }), d => {
        const y = (d.years || {})[S.unitFy];
        if (!y) return [empty('No unit sheet for ' + S.unitFy + '.'), notes(d.notes)];
        const sec = UNIT_SECTIONS.find(s => s.id === S.unitSection);
        const rows = y[sec.id] || [];
        const months = uniq(rows.flatMap(r => Object.keys(r.monthly || {}))).sort();
        const cond = (y.conditions || []);
        const chartHost = h('div');
        const cols = [{ label: 'Bay', key: 'bay' }, { label: 'Unit', key: 'name' }]
          .concat(months.map(m => ({ label: monthLabel(m), num: true, render: r => num((r.monthly || {})[m], sec.id === 'hours' ? 0 : 0) })))
          .concat([{ label: 'FY (' + sec.unit + ')', num: true, render: r => num(sum(months.map(m => (isNil((r.monthly || {})[m]) ? null : r.monthly[m]))), 0) }]);
        const condMonths = uniq(cond.flatMap(r => Object.keys(r.monthly || {}))).sort();
        const condCols = [{ label: 'Block (as labelled in the sheet)', key: 'bay' }, { label: 'Quantity', render: r => String(r.name || '').replace(/Â/g, '') }]
          .concat(condMonths.map(m => ({ label: monthLabel(m), num: true, render: r => num((r.monthly || {})[m], 1) })));
        const unitLabel = row => `${row.bay} · ${row.name}`;
        const unitKey = row => `${row.bay}|${row.name}`;
        const valueAt = (row, month) => {
          const value = row && row.monthly ? row.monthly[month] : null;
          return isNil(value) ? null : Number(value);
        };
        const totalByUnit = new Map((y.total || []).map(row => [unitKey(row), row]));
        const hoursByUnit = new Map((y.hours || []).map(row => [unitKey(row), row]));
        const summary = rows.map(row => {
          const reported = months.map(month => ({ month, value: valueAt(row, month) })).filter(point => point.value !== null);
          const latest = reported[reported.length - 1] || null;
          const previous = reported.length > 1 ? reported[reported.length - 2] : null;
          const peak = reported.reduce((best, point) => !best || point.value > best.value ? point : best, null);
          const totalRow = totalByUnit.get(unitKey(row));
          const hoursRow = hoursByUnit.get(unitKey(row));
          const rateMonth = months.slice().reverse().find(month => {
            const energy = valueAt(totalRow, month);
            const hours = valueAt(hoursRow, month);
            return energy !== null && hours !== null && hours > 0;
          });
          const rate = rateMonth ? valueAt(totalRow, rateMonth) / valueAt(hoursRow, rateMonth) : null;
          return {
            bay: row.bay,
            name: row.name,
            latestMonth: latest ? monthLabel(latest.month) : DASH,
            latestValue: latest ? `${num(latest.value, sec.id === 'hours' ? 1 : 0)} ${sec.unit}` : DASH,
            change: latest && previous && previous.value !== 0
              ? `${latest.value > previous.value ? '+' : ''}${fmtPct(((latest.value - previous.value) / Math.abs(previous.value)) * 100, 1)}`
              : DASH,
            fyTotal: reported.length ? `${num(reported.reduce((sum, point) => sum + point.value, 0), 0)} ${sec.unit}` : DASH,
            average: reported.length ? `${num(reported.reduce((sum, point) => sum + point.value, 0) / reported.length, 1)} ${sec.unit}/reported month` : DASH,
            peak: peak ? `${monthLabel(peak.month)} · ${num(peak.value, 1)} ${sec.unit}` : DASH,
            kwhPerHour: rate === null ? DASH : `${num(rate, 2)} kWh/hour`
          };
        });
        const pairCells = rows.map((row, rowIndex) => h('tr', null,
          h('th', { scope: 'row' }, unitLabel(row)),
          rows.map((other, colIndex) => {
            if (rowIndex === colIndex) return h('td', { class: 'num' }, '—');
            const commonMonth = months.slice().reverse().find(month =>
              valueAt(row, month) !== null && valueAt(other, month) !== null);
            if (!commonMonth) return h('td', { class: 'num muted' }, 'No shared data');
            const own = valueAt(row, commonMonth);
            const baseline = valueAt(other, commonMonth);
            const difference = own - baseline;
            const same = Math.abs(difference) < 0.000001;
            const comparison = baseline > 0 && own >= 0
              ? `${difference > 0 ? '+' : ''}${fmtPct((difference / baseline) * 100, 1)}`
              : `${difference > 0 ? '+' : ''}${num(difference, 1)} ${sec.unit}`;
            return h('td', {
              class: `num ${same ? '' : difference > 0 ? 'tb-compare-higher' : 'tb-compare-lower'}`,
              title: `${unitLabel(row)}: ${num(own, 2)} ${sec.unit}; ${unitLabel(other)}: ${num(baseline, 2)} ${sec.unit}; ${monthLabel(commonMonth)}.`
            }, `${same ? 'Same' : comparison} · ${monthLabel(commonMonth)}`);
          })));
        const analysisBlock = h('div', { class: 'tb-unit-analysis' },
          h('h3', null, `HVAC item comparison · ${sec.label}, ${S.unitFy}`),
          h('p', { class: 'tb-sub' }, 'Latest reported reading, change from the previous reported month, sum and average of reported months, and peak. Energy per running hour uses the source total-energy and running-hours sections for the latest month both contain data. These per-unit comparisons use the selected FY sheet; the separate reconciliation below shows where it differs from the Energy Consumption sheet.'),
          table([
            { key: 'bay', label: 'Bay' }, { key: 'name', label: 'HVAC item' }, { key: 'latestMonth', label: 'Latest month' },
            { key: 'latestValue', label: `Latest (${sec.unit})`, num: true }, { key: 'change', label: 'Change vs previous month', num: true },
            { key: 'fyTotal', label: `Sum of reported months (${sec.unit})`, num: true }, { key: 'average', label: 'Average per reported month', num: true },
            { key: 'peak', label: 'Peak month', num: true }, { key: 'kwhPerHour', label: 'Total energy / running hour', num: true }
          ], summary),
          h('h4', { class: 'tb-unit-matrix-title' }, `Pairwise comparison matrix · ${sec.unit}`),
          h('p', { class: 'tb-sub' }, 'Each cell compares the row HVAC with the column HVAC for their latest shared reported month. Positive means the row value is higher. This is a consumption comparison, not a claim that different HVAC units should use the same energy.'),
          h('div', { class: 'table-wrap tb-unit-matrix-wrap' }, h('table', { class: 'table tb-unit-matrix' },
            h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'HVAC item'), rows.map(row => h('th', { scope: 'col', title: unitLabel(row) }, unitLabel(row))))),
            h('tbody', null, pairCells))));
        const stacked = sec.id !== 'hours';
        const out = [
          h('div', { class: 'card' },
            h('div', { class: 'card-head' }, h('div', null, h('h3', null, sec.label + ' per HVAC unit, ' + S.unitFy), h('p', null, 'Unit: ' + sec.unit + ' per month · ' + sec.note + ' · source: ' + (d.workbook || 'ECHO workbook') + ', “FY” sheets'))),
            chartHost),
          h('div', { style: { marginTop: '10px' } }, table(cols, rows, { emptyText: 'No unit rows for this component.' })),
          analysisBlock,
          cond.length ? h('div', { class: 'tb-block' },
            h('h3', null, 'Operating conditions, ' + S.unitFy + ' (°C)'),
            h('p', { class: 'tb-sub' }, 'Set, ambient and room temperatures as recorded in the sheet. Both blocks are labelled “Bay -1” in the source, so they are shown with that label unchanged.'),
            table(condCols, cond)) : null,
          notes(d.notes)
        ];
        drawChart(chartHost, host => seriesChart(host, months.map(monthLabel), rows.map(r => ({ label: r.name, unit: sec.unit, points: !stacked, data: months.map(m => (isNil((r.monthly || {})[m]) ? null : r.monthly[m])) })), { type: stacked ? 'bar' : 'line', stacked, yTitle: sec.unit + ' per month', height: 'tall', legend: true }));
        return out;
      });
    }
    clear(unitsBox).appendChild(section('HVAC units: monthly detail', 'Each outdoor/indoor unit of both bays for one financial year',
      h('div', { class: 'tb-toolbar' }, h('label', { class: 'field' }, 'Financial year', fySel), secChips.el), body));
    drawUnits();
  }).catch(err => { if (ok()) clear(unitsBox).appendChild(section('HVAC units: monthly detail', null, errorBox(err))); });

  // -------- reconciliation
  load(recBox, api.get('/tbal/hvac/reconciliation'), rec => {
    const rows = rec.rows || [];
    if (!rows.length) return section('Do the two sheets agree?', null, empty('No reconciliation rows were returned.'));
    const bays = uniq(rows.map(r => r.bay));
    const fys = uniq(rows.map(r => r.fy));
    const out = h('div');
    const bSel = select([{ id: 'all', label: 'Both bays' }].concat(bays.map(b => ({ id: b, label: b }))), S.recBay, v => { S.recBay = v; draw(); }, { 'aria-label': 'Bay' });
    const fSel = select([{ id: 'all', label: 'All years' }].concat(fys.map(f => ({ id: f, label: f }))), S.recFy, v => { S.recFy = v; draw(); }, { 'aria-label': 'Financial year for reconciliation' });
    function draw() {
      const sel = rows.filter(r => (S.recBay === 'all' || r.bay === S.recBay) && (S.recFy === 'all' || r.fy === S.recFy));
      const months = uniq(sel.map(r => r.month)).sort();
      const chartHost = h('div');
      const pct = r => (isNil(r.difference) || !r.energyConsumptionSheetKwh ? null : fmtPct((r.difference / r.energyConsumptionSheetKwh) * 100, 1));
      const sumRows = [];
      uniq(sel.map(r => r.bay + '|' + r.fy)).forEach(k => {
        const g = sel.filter(r => r.bay + '|' + r.fy === k);
        const a = sum(g.map(r => r.energyConsumptionSheetKwh)), b = sum(g.map(r => r.sumOfUnitsKwh));
        sumRows.push({ bay: g[0].bay, fy: g[0].fy, n: g.length, a, b, d: isNil(a) || isNil(b) ? null : a - b });
      });
      put(clear(out),
        h('div', { class: 'card' },
          h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Difference between the two sheets, by month'), h('p', null, 'Unit: kWh per month · “Energy Consumption” sheet minus the sum of that bay’s units in the FY sheet · above zero means the Energy Consumption sheet is higher'))),
          chartHost),
        h('h3', { style: { margin: '16px 0 6px' } }, 'Totals of the selected months'),
        table([
          { key: 'bay', label: 'Bay' }, { key: 'fy', label: 'FY' }, { label: 'Months', num: true, render: r => num(r.n, 0) },
          { label: 'Energy Consumption sheet (kWh)', num: true, render: r => num(r.a, 0) }, { label: 'Sum of units (kWh)', num: true, render: r => num(r.b, 0) },
          { label: 'Difference (kWh)', num: true, render: r => num(r.d, 0) }
        ], sumRows),
        h('h3', { style: { margin: '16px 0 6px' } }, 'Every bay-month'),
        table([
          { key: 'bay', label: 'Bay' }, { key: 'fy', label: 'FY' }, { label: 'Month', render: r => monthLabel(r.month) },
          { label: 'Energy Consumption sheet (kWh)', num: true, render: r => num(r.energyConsumptionSheetKwh, 0) },
          { label: 'Sum of units (kWh)', num: true, render: r => num(r.sumOfUnitsKwh, 0) },
          { label: 'Difference (kWh)', num: true, render: r => num(r.difference, 0) },
          { label: 'Difference (% of sheet)', num: true, render: pct }
        ], sel, { emptyText: 'No rows for this selection.' }));
      drawChart(chartHost, host => seriesChart(host, months.map(monthLabel), uniq(sel.map(r => r.bay)).map(b => ({
        label: b, unit: 'kWh', data: months.map(m => { const r = sel.find(x => x.bay === b && x.month === m); return r && !isNil(r.difference) ? r.difference : null; })
      })), { type: 'bar', yTitle: 'kWh per month (sheet minus units)', height: 'tall', legend: true }));
    }
    draw();
    return section('Do the two ECHO sheets agree? No.',
      'The “Energy Consumption” sheet and the per-unit FY sheets both record HVAC energy; they are reported side by side, not reconciled',
      h('div', { class: 'tb-callout' }, h('strong', null, num(rec.mismatched, 0) + ' of ' + num(rec.compared, 0) + ' bay-months differ.'),
        h('p', { style: { margin: '4px 0 0' } }, 'The source does not say which sheet is right, so this page does not pick one: the history above uses the “Energy Consumption” sheet and the unit detail uses the FY sheets.')),
      h('div', { class: 'tb-toolbar' }, h('label', { class: 'field' }, 'Bay', bSel), h('label', { class: 'field' }, 'Financial year', fSel)),
      out, notes(rec.notes));
  });

  // -------- coverage
  load(covBox, api.get('/tbal/hvac/coverage'), cov => {
    const facts = cov.facts || {};
    const rows = Object.entries(facts).map(([k, v]) => {
      const [label, unit] = COVERAGE_LABELS[k] || [k, ''];
      return { label, value: typeof v === 'number' ? fmt(v, Number.isInteger(v) ? 0 : 2) : String(v), unit };
    });
    return section('HVAC coverage of the shop floor', 'Facts from ' + (cov.deckFile || 'the layout deck') + ' (1 slide)',
      table([{ key: 'label', label: 'Fact' }, { key: 'value', label: 'Value', num: true }, { key: 'unit', label: 'Unit' }], rows, { emptyText: 'No facts were extracted.' }),
      (cov.text || []).length ? h('details', { style: { marginTop: '10px' } }, h('summary', { class: 'muted small', style: { cursor: 'pointer' } }, 'Deck text, verbatim'),
        cov.text.map(t => h('p', { class: 'tb-pre' }, t))) : null,
      notes(cov.notes));
  });

  // -------- SEU
  load(seuBox, api.get('/tbal/seu'), seu => {
    const tr = seu.deckTranscription;
    const deck = seu.deck || {};
    const map = seu.seuChartMap || [];
    const out = [];
    out.push(section('Significant energy uses (SEU)', 'The SEU chart categories and the feeders behind each, read from the meter workbook (machine-read)',
      table([{ label: 'SEU chart bar', key: 'chartBar' }, { label: 'Feeders behind it', key: 'feeders' }], map, { emptyText: 'No SEU feeder table found.' }),
      h('p', { class: 'tb-count' }, 'Source: ' + (seu.seuChartMapSource || 'meter workbook') + '.')));
    if (tr && (tr.bars || []).length) {
      out.push(section('SEU chart values: transcribed by eye', null,
        h('div', { class: 'tb-caution', role: 'note' },
          h('strong', null, 'Read from the chart image, not from data: verify against the original deck.'),
          h('p', null, '“' + (deck.deckFile || 'TBAL SEU details.pptx') + '” is a single picture with no text layer (' + num((deck.deckTextLayer || []).length, 0) + ' text boxes), so these values were read from the picture by eye (' + (tr.extraction || 'transcribed by eye') + '). Bars whose labels could not be read were left out. They are shown as a table only: they are not charted, not totalled and not combined with the meter or HVAC figures on this page.')),
        h('div', { class: 'tb-hatch', style: { marginTop: '12px' } },
          h('p', { class: 'tb-sub' }, (tr.title || 'SEU chart') + ' · unit: ' + (tr.unit || 'MWh') + ' (as printed on the slide) · rule on the slide: ' + (tr.rule || DASH)),
          table([
            { label: 'Bar (as read)', key: 'name' },
            { label: 'Energy (' + (tr.unit || 'MWh') + ', as read)', num: true, render: b => num(b.mwh, 0) },
            { label: 'Share of plant (%, as read)', num: true, render: b => num(b.sharePct, 0) },
            { label: 'Outlined as significant (as read)', render: b => (isNil(b.significant) ? null : badge(b.significant ? 'Yes (outlined)' : 'No', b.significant ? 'warn' : '')) }
          ], tr.bars))));
    }
    out.push(notes(seu.notes));
    return out;
  });
}
