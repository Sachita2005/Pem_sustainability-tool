// Metrics > AIR MONITORING (one plant, chosen by the Metrics frame).
// Every figure comes from /api/air/*. Limits exist only for TBAL (its consent order); every
// other plant says "no limit stated in the source for this plant". Units travel with each reading.

import { api } from '../../api.js';
import { badge, clear, empty, fmt, h, load, monthLabel, notes, section, select, table } from '../../ui.js';
import { destroyCharts, seriesChart } from '../../charts.js';

let host = null;
let token = 0;

function ensureCss() {
  if (document.getElementById('metrics-air-css')) return;
  document.head.appendChild(h('link', { id: 'metrics-air-css', rel: 'stylesheet', href: '/static/css/metrics-air.css' }));
}

export function destroy() {
  token++;
  if (host) destroyCharts(host);
  host = null;
}

const defer = fn => setTimeout(() => { try { fn(); } catch (err) { console.error(err); } }, 0);
const isNil = v => v === null || v === undefined;
// Node.append turns null into the text "null"; this skips empty children
const add = (el, ...kids) => { kids.flat(Infinity).forEach(k => { if (k) el.appendChild(k instanceof Node ? k : document.createTextNode(String(k))); }); return el; };
const DASH = '—';
const NO_LIMIT = 'No limit stated in the source for this plant';

function mcard(label, value, unit, sub, tone) {
  return h('div', { class: 'mcard' },
    h('div', { class: 'mcard-value' + (tone ? ' ' + tone : '') }, value, unit ? h('small', null, unit) : null),
    h('div', { class: 'mcard-label' }, label),
    sub ? h('div', { class: 'mcard-sub' }, sub) : null);
}

function fyMonths(fy) {
  const m = /^FY(\d{2})-(\d{2})$/.exec(fy || '');
  if (!m) return [];
  const y = 2000 + Number(m[1]);
  return Array.from({ length: 12 }, (_, i) => {
    const mo = ((3 + i) % 12) + 1;
    return `${mo >= 4 ? y : y + 1}-${String(mo).padStart(2, '0')}`;
  });
}

const num = v => (isNil(v) ? DASH : fmt(v, Number.isInteger(Number(v)) ? 0 : (Math.abs(v) < 10 ? 2 : 1)));
const srcLabel = s => (s === 'entry' ? 'Only to Enter workbook' : s === 'calculation' ? 'Calculation workbook' : s || DASH);
const unitText = r => (r.unit ? r.unit : (r.limit !== null && r.limitUnit ? `${r.limitUnit} (blank in sheet)` : 'unit not stated'));

export async function render(container, ctx) {
  ensureCss();
  if (host) destroyCharts(host);
  const my = ++token;
  host = container;
  const plant = ctx.plant;
  const alive = () => my === token;
  const wrap = h('div', { class: 'ma-wrap' });
  clear(container).appendChild(wrap);

  const top = h('div');
  wrap.appendChild(top);
  let rd, ov, lim;
  await load(top, Promise.all([api.get('/air/readings', { plant }), api.get('/air/overview'), api.get('/air/limits')]),
    ([r, o, l]) => { rd = r; ov = o; lim = l; return null; });
  if (!alive() || !rd) return;

  const all = rd.readings || [];
  const plantOv = (ov.plants || []).find(p => p.plant === plant) || null;
  const hasConsent = !!(lim.order && lim.order.plant === plant);
  const consentLimits = (lim.limits || []).filter(l => l.plant === plant);

  if (!all.length) {
    add(wrap, 
      h('div', { class: 'ms-source ma-source' }, h('strong', null, 'Source: '), 'Sustainability Calculation and Sustainability (Only to Enter) workbooks, sheet "Air Monitoring data".'),
      empty(`No air-monitoring reading for ${plant} in the source (no value other than blank, NA or the entry sheet's 0 placeholder). ${NO_LIMIT}.`),
      notes(rd.notes));
    return;
  }

  const fys = [...new Set(all.map(r => r.fy))].sort();
  const state = { fy: ctx.params && fys.includes(ctx.params.fy) ? ctx.params.fy : fys[fys.length - 1], param: '' };
  const kpiBox = h('div');
  const chartBox = h('div', { class: 'ma-block' });
  const tableBox = h('div', { class: 'ma-block' });
  const consentBox = h('div', { class: 'ma-block' });

  const fySel = select(fys.map(f => ({ id: f, label: f })), state.fy, v => {
    state.fy = v; state.param = '';
    if (ctx.setParams) ctx.setParams({ fy: v });
    drawFy();
  }, { id: 'ma-fy', 'aria-label': 'Financial year' });
  const picker = h('div', { class: 'ma-toolbar' }, h('label', { for: 'ma-fy' }, 'Financial year'), fySel,
    h('span', { class: 'ma-hint' }, `Years with a reading for ${plant}: ${fys.join(', ')}`));

  add(wrap, 
    h('div', { class: 'ma-source' }, h('strong', null, 'Source: '),
      'monthly readings from the Sustainability Calculation workbook (sheet "Air Monitoring data"), with gaps filled from the Sustainability (Only to Enter) workbook; each reading below is marked with its workbook. The sheet has no day-level dates or sampling-point names.'),
    picker, kpiBox, chartBox, tableBox, consentBox, notes(rd.notes), notes(ov.notes), hasConsent ? notes(lim.notes) : null);

  const inFy = () => all.filter(r => r.fy === state.fy);

  function drawKpis() {
    clear(kpiBox);
    const rows = inFy();
    const params = [...new Set(rows.map(r => r.parameter))];
    const series = new Set(rows.map(r => `${r.parameter}|${r.location || ''}`));
    const compared = all.filter(r => !isNil(r.aboveLimit));
    const above = compared.filter(r => r.aboveLimit === true).length;
    const fromEntry = rows.filter(r => r.source === 'entry').length;
    const consentCond = (lim.conditions || []).find(c => /valid until/i.test(c.name));
    const validUntil = hasConsent ? ((ov.consent && ov.consent.plant === plant && ov.consent.validUntil) || (consentCond && consentCond.value)) : null;
    kpiBox.appendChild(h('div', { class: 'grid kpi5' },
      mcard('Parameters monitored', String(params.length), null, `${state.fy}: ${series.size} parameter-location series · ${params.slice(0, 4).join(', ')}${params.length > 4 ? ', …' : ''}`),
      mcard('Latest monitoring month', plantOv && plantOv.lastMonth ? monthLabel(plantOv.lastMonth) : DASH, null, 'Latest month with a reading, all years'),
      hasConsent
        ? mcard('Readings above a stated limit', String(above), null, `${compared.length} of ${all.length} readings (all years) could be compared with the consent-order limits${above === 0 ? '; none exceeds' : ''}`, above > 0 ? 'red' : 'green')
        : mcard('Readings above a stated limit', DASH, null, `${NO_LIMIT}: no reading was compared, so nothing is implied about compliance.`),
      hasConsent
        ? mcard('Consent valid until', validUntil || DASH, null, `${lim.order.issuer}, order ${lim.order.orderNo}`)
        : mcard('Consent validity', DASH, null, `No consent order for ${plant} in the source folder.`),
      mcard(`Readings in ${state.fy}`, String(rows.length), null, `${rows.length - fromEntry} from the Calculation workbook, ${fromEntry} from the Only to Enter workbook`)));
  }

  // ------------------------------------------------------------ one chart per parameter
  function drawCharts() {
    destroyCharts(chartBox);
    clear(chartBox);
    const rows = inFy();
    const months = fyMonths(state.fy);
    const labels = months.map(monthLabel);
    chartBox.appendChild(h('div', { class: 'section-head' }, h('h2', null, `Readings over time, ${state.fy}`),
      h('p', { class: 'small' }, `One chart per parameter, each in its own unit, ${monthLabel(months[0])} to ${monthLabel(months[11])}. Gaps are months with no reading. A dashed limit line is drawn only where the consent order gives a limit in the same unit.`)));
    if (!rows.length) { chartBox.appendChild(empty(`No reading in ${state.fy}.`)); return; }

    const groups = new Map();
    rows.forEach(r => {
      const key = `${r.parameter}|${r.unit || ''}`;
      if (!groups.has(key)) groups.set(key, { parameter: r.parameter, unit: r.unit, rows: [] });
      groups.get(key).rows.push(r);
    });
    const grid = h('div', { class: 'grid cols-3 ma-charts' });
    [...groups.values()].sort((a, b) => a.parameter.localeCompare(b.parameter) || String(a.unit).localeCompare(String(b.unit))).forEach(g => {
      const locs = [...new Set(g.rows.map(r => r.location || ''))];
      const series = locs.map(loc => {
        const rr = g.rows.filter(r => (r.location || '') === loc);
        const byM = new Map(rr.map(r => [r.month, r.value]));
        return { label: loc || g.parameter, type: 'line', smooth: false, unit: g.unit || '', data: months.map(m => (byM.has(m) && !isNil(byM.get(m)) ? byM.get(m) : null)) };
      });
      // limit lines: only where every reading of the location has a limit and it is comparable (same or blank unit)
      const limitLines = [];
      locs.forEach(loc => {
        const rr = g.rows.filter(r => (r.location || '') === loc);
        if (rr.length && rr.every(r => !isNil(r.limit) && !isNil(r.aboveLimit))) {
          const l = rr[0];
          limitLines.push({ loc, limit: l.limit, unit: l.limitUnit });
        }
      });
      const uniqLimits = [];
      limitLines.forEach(l => { if (!uniqLimits.some(u => u.limit === l.limit && u.unit === l.unit)) uniqLimits.push(l); });
      uniqLimits.forEach(l => series.push({ label: `Limit ${fmt(l.limit, 0)} ${l.unit || ''} (consent order)`, type: 'line', dashed: true, points: false, tone: 'bad', unit: l.unit || '', data: months.map(() => l.limit) }));

      const limitNote = uniqLimits.length
        ? `Limit from the consent order: ${uniqLimits.map(l => `${fmt(l.limit, 0)} ${l.unit}`).join(', ')}.`
        : (hasConsent ? 'No limit applied to this parameter in the consent-order comparison.' : NO_LIMIT + '.');
      const unitTitle = g.unit || (uniqLimits.length ? `${uniqLimits[0].unit} (unit blank in the sheet; limit's unit)` : 'unit not stated in the sheet');
      const box = h('div');
      grid.appendChild(h('div', { class: 'card ma-chart' },
        h('div', { class: 'card-head' }, h('div', null, h('h3', null, g.parameter), h('p', null, `${unitTitle} · ${locs.filter(Boolean).join(', ') || 'location not stated'} · ${g.rows.filter(r => !isNil(r.value)).length} readings`))),
        box, h('p', { class: 'ma-hint' }, limitNote)));
      defer(() => { if (alive()) seriesChart(box, labels, series, { type: 'line', yTitle: g.unit || (uniqLimits.length ? uniqLimits[0].unit : 'unit not stated'), height: 'short', legend: series.length > 1, digits: 2, beginAtZero: true }); });
    });
    chartBox.appendChild(grid);
  }

  // ------------------------------------------------------------ readings table
  function drawTable() {
    clear(tableBox);
    const rows = inFy();
    const params = [...new Set(rows.map(r => r.parameter))];
    const shown = (state.param ? rows.filter(r => r.parameter === state.param) : rows)
      .slice().sort((a, b) => a.parameter.localeCompare(b.parameter) || String(a.location).localeCompare(String(b.location)) || a.month.localeCompare(b.month));
    const sel = select([{ id: '', label: 'All parameters' }, ...params.map(p => ({ id: p, label: p }))], state.param, v => { state.param = v; drawTable(); }, { id: 'ma-param', 'aria-label': 'Parameter' });
    add(tableBox, 
      h('div', { class: 'section-head' }, h('h2', null, `Readings, ${state.fy}`), h('p', { class: 'small' }, `${shown.length} reading${shown.length === 1 ? '' : 's'}. Units are the sheet's own and differ between plants.`)),
      h('div', { class: 'ma-toolbar' }, h('label', { for: 'ma-param' }, 'Parameter'), sel),
      table([
        { key: 'month', label: 'Month', render: r => monthLabel(r.month) },
        { key: 'parameter', label: 'Parameter' },
        { key: 'location', label: 'Location' },
        { key: 'value', label: 'Reading', num: true, render: r => num(r.value) },
        { key: 'unit', label: 'Unit', render: r => (r.unit ? r.unit : h('span', { class: 'ma-muted' }, r.limit !== null && r.limitUnit ? `blank (limit in ${r.limitUnit})` : 'not stated')) },
        { key: 'limit', label: 'Limit', num: true, render: r => (isNil(r.limit) ? h('span', { class: 'ma-muted' }, 'none stated') : [num(r.limit), h('span', { class: 'ma-unit' }, r.limitUnit || '')]) },
        { key: 'flag', label: 'Against limit', render: r => (r.aboveLimit === true ? badge('Above limit', 'bad') : r.aboveLimit === false ? badge('Within limit', 'good') : badge(hasConsent ? 'not compared' : 'no limit stated', '')) },
        { key: 'source', label: 'Source workbook', render: r => srcLabel(r.source) }
      ], shown, { emptyText: 'No readings.' }));
  }

  // ------------------------------------------------------------ consent order (TBAL)
  function drawConsent() {
    clear(consentBox);
    if (!hasConsent) {
      add(consentBox, h('div', { class: 'section-head' }, h('h2', null, 'Consent order and limits')),
        empty(`${NO_LIMIT}. The only consent order in the source folder is TBAL's, so readings for ${plant} are not checked against any limit.`));
      return;
    }
    const o = lim.order;
    const doc = o.document;
    const pgCell = r => `p. ${r.page}`;
    const byGroup = g => (lim.conditions || []).filter(c => c.group === g);
    const condTable = (g, title) => {
      const rows = byGroup(g);
      if (!rows.length) return null;
      return h('div', { class: 'ma-sub' }, h('h3', null, title), table([
        { key: 'name', label: 'Condition' },
        { key: 'value', label: 'Value', num: true, render: r => [r.value, r.unit ? h('span', { class: 'ma-unit' }, r.unit) : null] },
        { key: 'wording', label: "Order's wording" },
        { key: 'page', label: 'Page', render: pgCell }
      ], rows));
    };
    const limRows = consentLimits;
    add(consentBox, 
      h('div', { class: 'section-head' }, h('h2', null, `Consent order, ${plant}`),
        h('p', { class: 'small' }, `${o.issuer} · order no. ${o.orderNo} · holder ${o.holder} · ${o.pages} pages · ${doc}`)),
      h('div', { class: 'ma-caution', role: 'note' }, h('strong', null, 'Read with care. '),
        'The order was extracted from a Word document whose text is partly scrambled. Values were matched by pattern; check a single line against the page shown before quoting it alone.'),
      h('div', { class: 'ma-sub' }, h('h3', null, 'Limits compared with the readings above'), table([
        { key: 'parameter', label: 'Parameter' },
        { key: 'location', label: 'Location' },
        { key: 'kind', label: 'Kind' },
        { key: 'limit', label: 'Limit', num: true, render: r => [num(r.limit), h('span', { class: 'ma-unit' }, r.unit || '')] },
        { key: 'condition', label: 'Condition in the order' },
        { key: 'page', label: 'Page', render: pgCell }
      ], limRows)),
      condTable('validity', 'Validity'),
      condTable('ambient', 'Ambient air limits'),
      condTable('emission', 'Stack emission limit'),
      condTable('effluent', 'Effluent limits'),
      condTable('noise', 'Noise limits'),
      condTable('water', 'Water consumption limits'),
      condTable('capacity', 'Capacity'),
      condTable('other', 'Other conditions'),
      h('div', { class: 'ma-sub' }, h('h3', null, 'Hazardous waste, authorised quantities'),
        table([
          { key: 'waste', label: 'Waste' },
          { key: 'quantity', label: 'Authorised quantity', num: true, render: r => [num(r.quantity), h('span', { class: 'ma-unit' }, r.unit || '')] },
          { key: 'scheduleCode', label: 'Schedule' },
          { key: 'page', label: 'Page', render: pgCell }
        ], lim.wasteAuthorisation || [], { emptyText: 'No authorised quantities extracted.' })));
  }

  function drawFy() { drawKpis(); drawCharts(); drawTable(); }
  drawFy();
  drawConsent();
}
