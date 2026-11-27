// Initiatives page: (1) initiatives already present and their status, (2) upcoming:
// planned initiatives, "implemented elsewhere, not yet here" candidates and the
// AI-assisted recommendations. Everything shown comes from /api/initiatives and
// /api/ai; the only text typed here is interface wording and the explanation of
// each uncertainty marker.

import { api } from '../api.js';
import { badge, clear, empty, fmt, h, load, modal, monthLabel, notes, pageHead, section } from '../ui.js';
import { destroyCharts, donutChart, seriesChart, tone } from '../charts.js';

const PLANTS = ['TASL-NGP', 'TASL-BLR', 'TSAL', 'TCOE', 'TLMAL', 'TASL-HYD', 'TBAL', 'TASL-MCA'];
const VIEWS = [{ id: 'present', label: 'Already present initiatives' }, { id: 'upcoming', label: 'Upcoming initiatives' }];

const STATUS = {
  implemented: { label: 'Implemented', icon: '✓', tone: 'good' },
  in_progress: { label: 'In progress', icon: '◐', tone: 'brand' },
  open: { label: 'Open', icon: '○', tone: 'warn' },
  under_study: { label: 'Under study', icon: '?', tone: 'info' },
  horizontally_deployed: { label: 'Horizontally deployed', icon: '⇄', tone: '--c6' },
  not_feasible: { label: 'Not feasible', icon: '✕', tone: 'bad' },
  not_applicable: { label: 'Not applicable', icon: '–', tone: 'muted' },
  on_hold: { label: 'On hold', icon: '⏸', tone: '--c8' },
  not_recorded: { label: 'Not recorded', icon: '·', tone: 'muted' }
};
const DOMAIN = {
  lighting: 'Lighting', hvac: 'HVAC', compressed_air: 'Compressed air', water: 'Water', renewable: 'Renewable',
  drives_motors: 'Drives and motors', heating_fuel: 'Heating and fuel', energy_management: 'Energy management', other: 'Other'
};
const METRIC_NAME = { power: 'Electricity', diesel: 'Diesel', water: 'Water' };
const METHOD_LABEL = {
  expected: 'Expected value', band: 'Normal variation', amber: 'Amber', red: 'Red zone', check: 'Check this entry',
  window: 'Months judged', projection: 'After-action line'
};
const DASH = '—';

let timers = [];
let observer = null;
let token = 0;
const bandCharts = [];

const isNil = v => v === null || v === undefined;
const statusLabel = s => (STATUS[s] ? STATUS[s].label : (s ? String(s) : 'No status'));
const domainLabel = d => DOMAIN[d] || (d ? String(d) : DASH);
// The API text for the normal range carries a mis-encoded plus-minus sign ("Â±"); show the sign it meant.
const clean = s => (typeof s === 'string' ? s.replace(/Â±/g, '±').replace(/Â(?=\s)/g, '') : s);
const val = v => (isNil(v) ? DASH : fmt(v, Math.abs(v) >= 100 ? 0 : 2));
const defer = fn => { const t = setTimeout(() => { try { fn(); } catch (err) { console.error(err); } }, 0); timers.push(t); };
const dateText = (iso, original) => {
  if (!iso && !original) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  const nice = m ? `${Number(m[3])} ${monthLabel(m[1] + '-' + m[2]).split('-')[0]} ${m[1]}` : iso;
  return original && nice && original !== nice ? `${nice} (written "${original}" in the source)` : (nice || original);
};

function ensureCss() {
  if (document.getElementById('initiatives-css')) return;
  document.head.appendChild(h('link', { id: 'initiatives-css', rel: 'stylesheet', href: '/static/css/initiatives.css' }));
}

export function destroy() {
  token++;
  timers.forEach(clearTimeout);
  timers = [];
  if (observer) { observer.disconnect(); observer = null; }
  bandCharts.length = 0;
}

// ---------------------------------------------------------------- small pieces

/** load() plus a hint when the first request after a server start is slow. */
function loadSlow(container, promise, build) {
  const t = setTimeout(() => {
    const l = container.querySelector('.loading');
    if (l) l.appendChild(h('span', { class: 'ini-slow' }, 'Still reading the workbooks. The first request after the server starts can take a minute or two.'));
  }, 4000);
  timers.push(t);
  return load(container, promise, build);
}

function stBadge(ps, opts = {}) {
  const s = ps.status;
  const key = s || 'none';
  const orig = ps.statusOriginal;
  let title = orig ? `Original wording in the source: "${orig}"` : (s ? 'The source has no wording for this status' : 'The source has no status for this plant');
  if (ps.statusBasis) title += `. Basis: ${ps.statusBasis}`;
  if (opts.doubt) title += `. ${opts.doubt}`;
  return h('span', { class: `ini-st st-${key}${opts.selected ? ' sel' : ''}`, title },
    opts.plant ? h('span', { class: 'pl' }, opts.plant + ' ·') : null,
    s && STATUS[s] ? h('span', { 'aria-hidden': 'true' }, STATUS[s].icon) : null,
    statusLabel(s), opts.doubt ? h('span', { class: 'ini-doubt', 'aria-label': 'uncertain' }, ' ⚠ uncertain') : null);
}

function plantStatusCell(item, plant) {
  const list = (item.plantStatuses || []).filter(p => !plant || p.plant === plant);
  if (!list.length) return h('span', { class: 'ini-dash' }, plant ? `No status for ${plant} in the source` : DASH);
  const doubt = p => (/^esi-/.test(item.id) && p.plant === 'TCOE' ? 'Uncertain: the status headers of this file are one plant out of step, so the TCOE status is doubtful' : null);
  return h('div', { class: 'ini-sts' }, list.map(p => stBadge(p, { plant: plant ? null : p.plant, doubt: doubt(p) })));
}

const sourceName = i => {
  const s = i.source || {};
  if (s.kind === 'initiatives-file') return /2026-27/.test(s.workbook || '') ? 'FY 2026-27 plan' : 'Initiatives file';
  const sheet = (s.sheet || '').replace(/\s*Initiatives$/i, '').replace(/^Energy\((\w+)\)$/, '$1');
  return 'Calculation workbook' + (sheet ? ` · ${sheet}` : '');
};

/** Remarks that talk about a plan while the same plant is recorded as Implemented. */
function remarkConflict(i) {
  if (!i.remarks || !/planned|will be|to be implemented/i.test(i.remarks)) return false;
  return (i.plantStatuses || []).some(p => p.status === 'implemented' && i.remarks.includes(p.plant));
}

/** Uncertainties that apply to one record (and plant filter). Each: { short, text, kind }. */
function cautions(i, plant) {
  const out = [];
  const list = (i.plantStatuses || []).filter(p => !plant || p.plant === plant);
  if (list.some(p => p.statusBasis === 'savings recorded in a yearly total')) {
    out.push({ short: 'status inferred', text: 'The calculation workbook has no status column. "Implemented" is inferred because a yearly saving above zero is recorded for this row.' });
  }
  if (list.some(p => p.statusBasis === 'section')) {
    out.push({ short: 'status from section', text: 'The row sits under "Future Plan for Improvements" in the calculation workbook; "Open" comes from that section, not from a status cell.' });
  }
  if (i.source && i.source.kind === 'calculation-workbook' && (i.statusKnown === false || list.some(p => !p.status))) {
    out.push({ short: 'status not recorded', text: 'Listed in the calculation workbook without any savings, so the sheet does not say whether it was ever started.' });
  }
  if (/^esi-/.test(i.id) && list.some(p => p.plant === 'TCOE')) {
    out.push({ short: 'TCOE status uncertain', text: 'In the existing-initiatives file the status headers are one plant out of step with the data. Reading the column as TCOE or as TSAL gives different answers only for this plant, so its status here is doubtful. The FY 2026-27 file has proper labels.' });
  }
  if (remarkConflict(i)) {
    out.push({ short: 'remark conflicts with status', text: `The remark says "${i.remarks}" while the status for that plant is Implemented. Both are shown as the source gives them.` });
  }
  const ip = i.initiationPlant || {};
  if ((ip.plants || []).length && !ip.plants.some(p => (i.plants || []).includes(p))) {
    out.push({ short: 'plant columns look shifted', text: `This initiative was started at ${ip.label}, which has no status column in the file; the statuses shown for other plants may be shifted by one block. Treat them with caution.` });
  }
  if (i.otherPlantColumns && Object.keys(i.otherPlantColumns).length) {
    out.push({ short: 'stray cells in source', text: 'Cells beside the plant blocks contradict them; they are kept raw in the detail view and not used.' });
  }
  return out;
}

function flags(i, plant) {
  const c = cautions(i, plant).filter(x => x.short !== 'TCOE status uncertain');   // shown on the TCOE badge itself
  const dup = (i.groupSize || 1) > 1;
  if (!c.length && !dup) return null;
  return h('div', { class: 'ini-flags' },
    c.map(x => h('span', { class: 'ini-flag', title: x.text }, '⚠ ' + x.short)),
    dup ? h('span', { class: 'ini-flag info', title: `${i.groupSize} records describe the same initiative (shared groupKey). Open the detail to see them; do not count them twice.` }, `⛓ linked ×${i.groupSize}`) : null);
}

function savingText(i, plant) {
  const a = i.achieved || {};
  if (i.source && i.source.kind === 'initiatives-file') {
    const ps = plant ? (i.plantStatuses || []).find(p => p.plant === plant) : null;
    const kwh = ps ? ps.energySavingKwh : a.energyKwh;
    const cr = ps ? ps.energyCostSavingCr : a.energyCostCr;
    const parts = [];
    if (!isNil(kwh)) parts.push(`${fmt(kwh)} kWh`);
    if (!isNil(cr)) parts.push(`${fmt(cr, 3)} Cr`);
    return parts.length ? parts.join(' · ') : null;
  }
  if (isNil(a.total)) return null;
  return `${fmt(a.total)} ${a.totalUnit || '(unit not given)'}`;
}

function mcard(label, value, unit, sub, cls, src) {
  return h('div', { class: 'mcard' },
    h('div', { class: 'mcard-value ' + (cls || '') }, value, unit ? h('small', null, unit) : null),
    h('div', { class: 'mcard-label' }, label),
    sub ? h('div', { class: 'mcard-sub' }, sub) : null,
    src ? h('div', { class: 'ini-kpi-src' }, src) : null);
}

function dl(rows) {
  return h('dl', { class: 'ini-dl' }, rows.filter(Boolean).map(([k, v]) => [
    h('dt', null, k),
    h('dd', null, isNil(v) || v === '' ? h('span', { class: 'ini-dash' }, DASH + ' (not recorded)') : v)]));
}

// ---------------------------------------------------------------- detail dialog

function openDetail(item, all, plantNames) {
  let close = null;
  const reopen = other => { if (close) close(); openDetail(other, all, plantNames); };
  const pn = p => plantNames[p] || p;
  const a = item.achieved || {};
  const ps = item.plantStatuses || [];
  const linked = all.filter(x => x.groupKey === item.groupKey && x.id !== item.id);
  const cs = cautions(item, null);

  const psTable = ps.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
    h('thead', null, h('tr', null, ['Plant', 'Status', 'Original wording', 'Basis', 'Energy saving (kWh)', 'Cost saving (Cr)', 'Phase'].map((t, k) => h('th', { class: k === 4 || k === 5 ? 'num' : null }, t)))),
    h('tbody', null, ps.map(p => h('tr', null,
      h('td', null, `${pn(p.plant)}`, p.plantLabel && p.plantLabel !== pn(p.plant) ? h('span', { class: 'ini-sub' }, `source label: ${p.plantLabel}`) : null),
      h('td', null, stBadge(p)),
      h('td', null, p.statusOriginal || DASH),
      h('td', null, p.statusBasis || DASH),
      h('td', { class: 'num' }, !isNil(p.energySavingKwh) ? fmt(p.energySavingKwh) : (p.energySavingKwhRaw ? `${DASH} (source: ${p.energySavingKwhRaw})` : DASH)),
      h('td', { class: 'num' }, !isNil(p.energyCostSavingCr) ? fmt(p.energyCostSavingCr, 3) : (p.energyCostSavingCrRaw ? `${DASH} (source: ${p.energyCostSavingCrRaw})` : DASH)),
      h('td', null, p.phase || DASH)))))) : h('p', { class: 'muted' }, 'The source gives no per-plant status for this record.');

  let achieved;
  if (a.byFinancialYear) {
    const fys = Object.entries(a.byFinancialYear);
    achieved = h('div', null,
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
        h('thead', null, h('tr', null, h('th', null, 'Financial year'), h('th', { class: 'num' }, `Saving (${a.unit || 'unit not given'})`), h('th', { class: 'num' }, `CO2 (${a.co2Unit || 'Kg CO2 / yr'})`))),
        h('tbody', null, fys.map(([fy, v]) => h('tr', null, h('td', null, fy), h('td', { class: 'num' }, fmt(v.value, Math.abs(v.value || 0) < 100 && !Number.isInteger(v.value) ? 2 : 0)), h('td', { class: 'num' }, fmt(v.co2Kg, 1))))))),
      h('p', { class: 'small muted' }, `Sum over the years listed: ${fmt(a.total)} ${a.totalUnit || '(unit not given)'}. Shown for convenience; read a single year from the table.`));
  } else {
    achieved = dl([
      ['Energy saving, sum over plants', isNil(a.energyKwh) ? null : `${fmt(a.energyKwh)} ${a.energyKwhUnit || 'kWh'}`],
      ['Cost saving, sum over plants', isNil(a.energyCostCr) ? null : `${fmt(a.energyCostCr, 3)} ${a.energyCostUnit || 'Cr'}`],
      ['CO2', isNil(a.co2Kg) ? null : `${fmt(a.co2Kg)} kg`]]);
  }
  const ex = item.expected || {};
  const src = item.source || {};
  const ip = item.initiationPlant || {};
  const body = h('div', null,
    h('div', { class: 'ini-sts' }, badge(item.id, 'brand'), badge(sourceName(item)), badge('Phase: ' + (item.phase || DASH), item.phase === 'planned' ? 'warn' : 'good'),
      item.statusKnown === false ? badge('Status not recorded', 'warn') : null),
    cs.map(c => h('div', { class: 'ini-caution' }, h('strong', null, c.short + '. '), c.text)),
    h('div', { class: 'ini-dsec' }, h('h4', null, 'About the initiative'), dl([
      ['Description', item.description],
      ['Domain', `${domainLabel(item.domain)}${item.domainSource ? ` (from ${item.domainSource})` : ''}`],
      ['Equipment / area', item.equipmentArea], ['Category', item.category], ['Type', item.type], ['Energy type', item.energyType],
      ['Initiation plant', ip.label ? `${ip.label}${(ip.plants || []).length ? ` → ${ip.plants.map(pn).join(', ')}` : ' (not a plant)'}` : null],
      ['Plants with a status', (item.plants || []).length ? item.plants.map(pn).join(', ') : null],
      ['Initiated by', item.initiatedBy], ['Responsible', item.responsible], ['Priority (1 highest)', isNil(item.priority) ? null : String(item.priority)],
      ['Initiation date', dateText(item.initiationDate, item.initiationDateOriginal)], ['Year of activity', isNil(item.yearOfActivity) ? null : String(item.yearOfActivity)],
      ['Planned completion', dateText(item.plannedCompletion)], ['Actual completion', dateText(item.actualCompletion)],
      ['Initiating plant status (FY 2026-27 file)', item.initiatorStatus ? `${statusLabel(item.initiatorStatus.status)} (written "${item.initiatorStatus.statusOriginal}")` : null]])),
    h('div', { class: 'ini-dsec' }, h('h4', null, 'Status by plant'), psTable),
    h('div', { class: 'ini-dsec' }, h('h4', null, 'Savings recorded'), achieved),
    h('div', { class: 'ini-dsec' }, h('h4', null, 'Investment and expected saving'), dl([
      ['Investment (Rs)', isNil(item.investmentRs) ? null : fmt(item.investmentRs)],
      ['Expected saving per year', isNil(item.expectedSavingPerYear) ? null : fmt(item.expectedSavingPerYear)],
      ['ROI (years)', isNil(item.roiYears) ? null : fmt(item.roiYears, 1)],
      ['Expected energy saving (kWh)', isNil(ex.energyKwh) ? null : fmt(ex.energyKwh)],
      ['Expected cost saving (Cr)', isNil(ex.energyCostCr) ? null : fmt(ex.energyCostCr, 3)],
      ['Expected CO2 (kg)', isNil(ex.co2Kg) ? null : fmt(ex.co2Kg)]])),
    h('div', { class: 'ini-dsec' }, h('h4', null, 'Remarks'), item.remarks ? h('p', null, item.remarks) : h('p', { class: 'muted' }, 'No remark in the source.')),
    item.otherPlantColumns ? h('div', { class: 'ini-dsec' }, h('h4', null, 'Stray cells beside the plant blocks (raw, not used)'),
      dl(Object.entries(item.otherPlantColumns).map(([k, v]) => [k, isNil(v) ? null : String(v)]))) : null,
    h('div', { class: 'ini-dsec' }, h('h4', null, 'Source'), dl([
      ['Workbook', src.workbook], ['Sheet', src.sheet], ['Row', isNil(src.row) ? null : `row ${src.row}`], ['Sl. No.', isNil(src.slNo) ? null : String(src.slNo)],
      ['Entry-sheet fallback used', isNil(src.usedEntryFallback) ? null : (src.usedEntryFallback ? 'yes' : 'no')]])),
    h('div', { class: 'ini-dsec ini-linked' }, h('h4', null, `Linked records (groupKey ${item.groupKey || DASH}, ${item.groupSize || 1} record${(item.groupSize || 1) === 1 ? '' : 's'})`),
      linked.length ? [h('p', { class: 'small muted' }, 'These records describe the same initiative. They are separate rows in the source; do not add their savings twice.'),
        ...linked.map(x => h('button', { type: 'button', onClick: () => reopen(x) }, `${x.id} · ${sourceName(x)} · ${x.description.slice(0, 90)}`))]
        : h('p', { class: 'muted' }, 'No other record shares this initiative.')));
  close = modal(item.description.length > 80 ? item.description.slice(0, 80) + '…' : item.description, body);
}

// ---------------------------------------------------------------- view 1: present

function kpiBlock(sum, items, plant) {
  const bs = sum.byStatus || {};
  const sav = (sum.savings || {}).achievedFromInitiativesFiles || {};
  const esi = sav.esi || {};
  const calc = (sum.savings || {}).achievedFromCalculationWorkbook || {};
  const present = items.filter(i => i.phase === 'present');
  const nFiles = present.filter(i => i.source.kind === 'initiatives-file').length;
  const nCalc = present.length - nFiles;
  const scope = plant ? `at ${plant}` : 'across all plants';
  const latest = k => {
    const fys = Object.keys(calc[k] || {}).sort();
    return fys.length ? { fy: fys[fys.length - 1], ...calc[k][fys[fys.length - 1]] } : null;
  };
  const calcCard = (k, name, unitFallback) => {
    const l = latest(k);
    if (!l) return mcard(`${name} saving recorded`, DASH, '', `No calculation-workbook ${name.toLowerCase()} rows ${scope}`, '', 'Source: Sustainability Calculation workbook');
    const unit = l.unit || null;
    return mcard(`${name} saving recorded, ${l.fy}`, fmt(l.achieved, Math.abs(l.achieved) < 100 && !Number.isInteger(l.achieved) ? 2 : 0), unit || '',
      unit ? `${l.rows} present rows${!isNil(l.co2Kg) && l.co2Kg ? ` · ${fmt(l.co2Kg)} kg CO2` : ''}` : `${l.rows} present rows · the source gives no unit for these rows`,
      '', 'Source: Sustainability Calculation workbook, one financial year only');
  };
  // identical rows carrying the same saving (a repeated saving in the source)
  const dupes = {};
  present.filter(i => i.source.kind === 'initiatives-file').forEach(i => {
    const kwh = plant ? ((i.plantStatuses || []).find(p => p.plant === plant) || {}).energySavingKwh : (i.achieved || {}).energyKwh;
    if (isNil(kwh)) return;
    const k = i.description.trim().toLowerCase() + '|' + kwh;
    (dupes[k] = dupes[k] || []).push(kwh);
  });
  const rep = Object.values(dupes).filter(v => v.length > 1);
  const impl = bs.implemented, hd = bs.horizontally_deployed;
  return h('div', null,
    h('div', { class: 'ini-block-title' }, `Status of initiatives ${scope}`),
    h('div', { class: 'grid kpi5 ini-kpis' },
      mcard('Initiatives on record', fmt((sum.byPhase || {}).present), '', `${fmt(nFiles)} from the initiatives file, ${fmt(nCalc)} from the calculation workbook`, '', 'Present-phase records; the two sources are listed separately'),
      mcard('Implemented', fmt(impl), 'plant entries', hd ? `plus ${fmt(hd)} horizontally deployed` : 'one entry per plant and initiative', 'green', 'Source: initiatives files (FY 2026-27 copy). Calculation-workbook rows have no status column'),
      mcard('In progress', fmt(bs.in_progress), 'plant entries', 'Status "WIP" in the source', '', 'Source: initiatives files'),
      mcard('Open or under study', fmt((bs.open || 0) + (bs.under_study || 0)), 'plant entries', `${fmt(bs.open)} open · ${fmt(bs.under_study)} under study`, 'amber', 'Source: initiatives files'),
      mcard('Not feasible / not applicable', fmt((bs.not_feasible || 0) + (bs.not_applicable || 0)), 'plant entries', `${fmt(bs.not_feasible)} not feasible · ${fmt(bs.not_applicable)} not applicable`, '', 'Source: initiatives files')),
    h('div', { class: 'ini-block-title' }, `Savings recorded ${scope} (the two sources are never added together)`),
    h('div', { class: 'grid kpi5 ini-kpis' },
      mcard('Energy saving recorded', fmt(esi.energyKwh), isNil(esi.energyKwh) ? '' : 'kWh', isNil(esi.energyKwh) ? `No saving figure in the initiatives file ${scope}` : `${fmt(esi.entries)} plant entries${rep.length ? ` · ${rep.length} set of rows with identical text repeat the same figure (${rep.map(v => fmt(v[0]) + ' kWh × ' + v.length).join(', ')}), so it may be counted more than once in the source` : ''}`, '', 'Source: initiatives files. The FY 2026-27 file repeats the same figures, not added'),
      mcard('Cost saving recorded', isNil(esi.energyCostCr) ? DASH : fmt(esi.energyCostCr, 3), isNil(esi.energyCostCr) ? '' : 'Cr', 'Crore rupees, as headed in the source', '', 'Source: initiatives files'),
      calcCard('power', 'Electricity'),
      calcCard('fuel', 'Fuel'),
      calcCard('water', 'Water')),
    h('p', { class: 'small muted', style: { margin: '8px 2px 0' } }, 'Expected saving, investment and CO2 for initiatives are blank in the source files, so none is shown.'),
    calcYearTable(calc));
}

function calcYearTable(calc) {
  const kinds = Object.keys(calc);
  if (!kinds.length) return null;
  const fys = [...new Set(kinds.flatMap(k => Object.keys(calc[k])))].sort();
  return h('details', { class: 'ini-how' },
    h('summary', null, 'Calculation-workbook savings for every financial year'),
    h('div', { class: 'table-wrap', style: { marginTop: '8px' } }, h('table', { class: 'table' },
      h('thead', null, h('tr', null, h('th', null, 'Energy type (unit)'), fys.map(f => h('th', { class: 'num' }, f)))),
      h('tbody', null, kinds.map(k => {
        const unit = ((Object.values(calc[k])[0] || {}).unit) || 'unit not given in the source';
        return h('tr', null, h('td', null, `${k.charAt(0).toUpperCase() + k.slice(1)} (${unit})`),
          fys.map(f => h('td', { class: 'num' }, calc[k][f] ? fmt(calc[k][f].achieved, Math.abs(calc[k][f].achieved) < 100 && !Number.isInteger(calc[k][f].achieved) ? 2 : 0) : DASH)));
      })))),
    h('p', { class: 'small muted' }, 'Each cell is the sum of the present rows of that sheet for the financial year. Units are never added across energy types.'));
}

function statusChart(sum) {
  const order = Object.keys(STATUS);
  const slices = order.map(k => ({ k, v: (sum.byStatus || {})[k] || 0 })).filter(s => s.v > 0)
    .map(s => ({ label: statusLabel(s.k), value: s.v, tone: STATUS[s.k].tone }));
  const box = h('div');
  if (!slices.length) { box.appendChild(empty('No status entries in the initiatives files for this selection.')); return box; }
  defer(() => donutChart(box, slices, { unit: 'plant entries', height: 'short', legend: window.innerWidth < 720 ? 'bottom' : 'right' }));
  return box;
}

function domainChart(sum) {
  const entries = Object.entries(sum.byDomain || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const box = h('div');
  if (!entries.length) { box.appendChild(empty('No initiatives for this selection.')); return box; }
  defer(() => seriesChart(box, entries.map(([k]) => domainLabel(k)), [{ label: 'Initiatives', data: entries.map(([, v]) => v), type: 'bar', unit: 'initiatives' }],
    { type: 'bar', horizontal: true, height: 'short', xTitle: null }));
  return box;
}

function registerBlock(items, all, state, plantNames) {
  const rows0 = items.filter(i => i.phase === 'present' && (!state.plant || (i.plants || []).includes(state.plant)));
  const f = { status: '', domain: '', source: '', q: '' };
  const host = h('div');
  const tbl = h('div');
  const count = h('p', { class: 'ini-count', 'aria-live': 'polite' });

  const domains = [...new Set(rows0.map(i => i.domain).filter(Boolean))].sort();
  const sel = (id, label, options, key) => h('label', { class: 'field', for: id }, label,
    h('select', { id, class: 'input', onChange: e => { f[key] = e.target.value; draw(); } },
      options.map(o => h('option', { value: o.id }, o.label))));

  const filters = h('div', { class: 'ini-filters' },
    sel('ini-f-status', 'Status', [{ id: '', label: 'All statuses' }, ...Object.keys(STATUS).filter(k => k !== 'not_recorded').map(k => ({ id: k, label: STATUS[k].label }))], 'status'),
    sel('ini-f-domain', 'Domain', [{ id: '', label: 'All domains' }, ...domains.map(d => ({ id: d, label: domainLabel(d) }))], 'domain'),
    sel('ini-f-source', 'Source', [{ id: '', label: 'All sources' }, { id: 'initiatives-file', label: 'Initiatives file' }, { id: 'calculation-workbook', label: 'Calculation workbook' }], 'source'),
    h('label', { class: 'field grow', for: 'ini-f-q' }, 'Search the description',
      h('input', { id: 'ini-f-q', class: 'input', type: 'search', placeholder: 'e.g. VFD, HVAC, lighting', onInput: e => { f.q = e.target.value.trim().toLowerCase(); draw(); } })));

  function draw() {
    const rows = rows0.filter(i => {
      if (f.domain && i.domain !== f.domain) return false;
      if (f.source && i.source.kind !== f.source) return false;
      if (f.q && !String(i.description).toLowerCase().includes(f.q)) return false;
      if (f.status) {
        const list = (i.plantStatuses || []).filter(p => !state.plant || p.plant === state.plant);
        if (!list.some(p => p.status === f.status)) return false;
      }
      return true;
    });
    clear(tbl);
    count.textContent = `Showing ${fmt(rows.length)} of ${fmt(rows0.length)} present initiatives${state.plant ? ` at ${state.plant}` : ''}. Select an initiative for every field the source has.`;
    if (!rows.length) { tbl.appendChild(empty('No initiative matches these filters.')); return; }
    tbl.appendChild(h('div', { class: 'table-wrap', style: { maxHeight: '640px' } }, h('table', { class: 'table ini-table' },
      h('thead', null, h('tr', null,
        h('th', null, 'Initiative'), h('th', null, 'Source'), h('th', null, 'Domain'),
        h('th', null, state.plant ? `Status at ${state.plant}` : 'Status by plant'),
        h('th', { class: 'num' }, 'Saving recorded'))),
      h('tbody', null, rows.map(i => {
        const sv = savingText(i, state.plant);
        return h('tr', null,
          h('td', { class: 'd' }, h('button', { type: 'button', class: 'ini-desc', onClick: () => openDetail(i, all, plantNames) }, i.description), flags(i, state.plant)),
          h('td', null, sourceName(i), h('span', { class: 'ini-sub' }, `${i.id}${i.source.row ? ` · row ${i.source.row}` : ''}`)),
          h('td', null, domainLabel(i.domain), i.domainSource === 'description keywords' ? h('span', { class: 'ini-sub', title: 'Classified by keywords in the description, not a field in the source' }, 'by keyword') : null),
          h('td', { class: 's' }, plantStatusCell(i, state.plant)),
          h('td', { class: 'num', title: i.source.kind === 'calculation-workbook' ? 'Sum of the yearly totals FY18-19 to FY24-25 listed in the sheet' : 'From the initiatives file' }, sv || h('span', { class: 'ini-dash' }, DASH)));
      })))));
  }
  host.append(filters, count, tbl);
  draw();
  return host;
}

function reconBlock(sum) {
  const rows = sum.reconciliation || [];
  if (!rows.length) return h('p', { class: 'small muted' }, 'The calculation workbook plant totals match the sum of their listed initiatives for this selection.');
  return h('div', null,
    h('p', { class: 'ini-ai-note' }, 'In these cases the "Plant Total" row of a calculation sheet does not equal the sum of that plant\'s listed initiatives. Nothing is corrected here; both numbers are shown as the sheet gives them.'),
    h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
      h('thead', null, h('tr', null, ['Sheet', 'Plant', 'Financial year', 'Row', 'Plant total in sheet', 'Sum of listed initiatives'].map((t, k) => h('th', { class: k > 2 ? 'num' : null }, t)))),
      h('tbody', null, rows.map(r => h('tr', null, h('td', null, r.sheet), h('td', null, r.plant), h('td', null, r.financialYear), h('td', { class: 'num' }, fmt(r.row)),
        h('td', { class: 'num' }, fmt(r.plantTotalInSheet, 2)), h('td', { class: 'num' }, fmt(r.sumOfListedInitiatives, 2))))))));
}

function drawPresent(box, state, data) {
  const sumP = api.get('/initiatives/summary', { plant: state.plant || undefined, phase: 'present' });
  const kBox = h('div'), cBox = h('div'), rBox = h('div'), reBox = h('div');
  const statusCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Status breakdown'),
    h('p', null, 'Plant entries by normalised status, initiatives files only (FY 2026-27 copy preferred, so the two files are not double counted)'))));
  const domainCard = h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Initiatives by domain'),
    h('p', null, 'Present-phase records, initiatives files and calculation workbook together. Some domains are classified by keyword.'))));
  const sBody = h('div'), dBody = h('div');
  statusCard.appendChild(sBody); domainCard.appendChild(dBody);
  box.append(
    section('Present initiatives', state.plant ? `Selected plant: ${state.plant}` : 'All plants (TADL)', kBox),
    section('Breakdowns', null, h('div', { class: 'grid cols-2' }, statusCard, domainCard)),
    section('Register of present initiatives', 'Status per plant uses the normalised wording; hover a badge for the original wording in the source.', rBox),
    section('Plant totals that do not reconcile', 'Calculation workbook, financial year 2024-25 and others as listed', reBox));

  loadSlow(kBox, Promise.all([sumP, data.list]), ([sum, list]) => [kpiBlock(sum, list.items || [], state.plant), notes([...(sum.notes || []), ...(list.notes || [])])]);
  load(sBody, sumP, sum => statusChart(sum));
  load(dBody, sumP, sum => domainChart(sum));
  loadSlow(rBox, data.list, list => {
    const items = list.items || [];
    return [registerBlock(items, items, state, data.plantNames),
      h('details', { class: 'ini-how' }, h('summary', null, 'Where the data comes from'),
        h('ul', null, (list.sources || []).map(s => h('li', null, `${s.role}: ${s.file}`)))),
      notes(list.notes)];
  });
  load(reBox, sumP, sum => [reconBlock(sum), notes(sum.notes)]);
}

// ---------------------------------------------------------------- view 2a: planned

function plannedBlock(items, state, data) {
  const rows = items.filter(i => i.phase === 'planned' && (!state.plant || (i.plants || []).includes(state.plant)));
  if (!rows.length) return empty(`No planned initiatives${state.plant ? ' for ' + state.plant : ''} in the source files.`);
  const blank = k => rows.every(i => isNil(i[k]));
  const blanks = [['investmentRs', 'investment'], ['expectedSavingPerYear', 'expected saving per year'], ['roiYears', 'ROI'], ['plannedCompletion', 'planned completion date']]
    .filter(([k]) => blank(k)).map(([, n]) => n);
  return h('div', null,
    h('p', { class: 'ini-count' }, `${fmt(rows.length)} planned initiative${rows.length === 1 ? '' : 's'}${state.plant ? ` involving ${state.plant}` : ''}. The status at each plant shows what is still to be done: Open and Under study are the work ahead.`),
    h('div', { class: 'table-wrap', style: { maxHeight: '640px' } }, h('table', { class: 'table ini-table' },
      h('thead', null, h('tr', null, h('th', null, 'Initiative'), h('th', null, 'Source'), h('th', null, 'Domain'), h('th', { class: 'num' }, 'Priority'),
        h('th', null, state.plant ? `Status at ${state.plant}` : 'Status by plant'),
        h('th', { class: 'num' }, 'Investment (Rs)'), h('th', { class: 'num' }, 'Expected saving / year'), h('th', { class: 'num' }, 'ROI (years)'), h('th', null, 'Planned completion'))),
      h('tbody', null, rows.map(i => h('tr', null,
        h('td', { class: 'd' }, h('button', { type: 'button', class: 'ini-desc', onClick: () => openDetail(i, items, data.plantNames) }, i.description), flags(i, state.plant)),
        h('td', null, sourceName(i), h('span', { class: 'ini-sub' }, `${i.id}${i.source.row ? ` · row ${i.source.row}` : ''}`)),
        h('td', null, domainLabel(i.domain)),
        h('td', { class: 'num' }, isNil(i.priority) ? DASH : fmt(i.priority)),
        h('td', { class: 's' }, plantStatusCell(i, state.plant)),
        h('td', { class: 'num' }, isNil(i.investmentRs) ? DASH : fmt(i.investmentRs)),
        h('td', { class: 'num' }, isNil(i.expectedSavingPerYear) ? DASH : fmt(i.expectedSavingPerYear)),
        h('td', { class: 'num' }, isNil(i.roiYears) ? DASH : fmt(i.roiYears, 1)),
        h('td', null, dateText(i.plannedCompletion) || DASH)))))),
    blanks.length ? h('p', { class: 'ini-ai-note' }, `Not recorded in the source for any of these rows: ${blanks.join(', ')}. A dash means blank, not zero.`) : null);
}

function elsewhereBlock(state, data) {
  const fetchAll = () => api.get('/initiatives/summary').then(s => Object.entries(s.horizontalDeployment || {}).flatMap(([plant, v]) => (v.candidates || []).map(c => ({ ...c, plant: c.plant || plant }))));
  const fetchOne = () => api.get(`/initiatives/horizontal-deployment/${encodeURIComponent(state.plant)}`).then(r => (r.items || []).map(c => ({ ...c, plant: c.plant || state.plant })));
  const box = h('div');
  loadSlow(box, state.plant ? fetchOne() : fetchAll(), items => {
    if (!items.length) return empty(`No initiative is open or under study ${state.plant ? 'at ' + state.plant : 'at any plant'} while another plant has implemented it.`);
    const withSaving = items.filter(c => (c.implementedAt || []).some(x => !isNil(x.energySavingKwh) || !isNil(x.energyCostSavingCr)));
    return h('div', null,
      h('p', { class: 'ini-count' }, `${fmt(items.length)} proven action${items.length === 1 ? '' : 's'} that ${state.plant ? state.plant + ' has' : 'a plant has'} not yet done (status Open or Under study) although at least one other plant has implemented it. Sorted by priority, then by how many plants already did it.`),
      h('div', { class: 'table-wrap', style: { maxHeight: '560px' } }, h('table', { class: 'table ini-table' },
        h('thead', null, h('tr', null, state.plant ? null : h('th', null, 'Plant'), h('th', null, 'Initiative'), h('th', null, 'Domain'), h('th', { class: 'num' }, 'Priority'),
          h('th', null, 'Status here'), h('th', null, 'Already implemented at (saving recorded there)'))),
        h('tbody', null, items.map(c => h('tr', null,
          state.plant ? null : h('td', null, data.plantNames[c.plant] || c.plant),
          h('td', { class: 'd' }, c.description, h('span', { class: 'ini-sub' }, `${c.id}${c.source && c.source.row ? ` · row ${c.source.row}` : ''}`)),
          h('td', null, domainLabel(c.domain)),
          h('td', { class: 'num' }, isNil(c.priority) ? DASH : fmt(c.priority)),
          h('td', null, stBadge({ status: c.statusAtPlant, statusOriginal: c.statusAtPlantOriginal })),
          h('td', { class: 's' }, h('div', { class: 'ini-sts' }, (c.implementedAt || []).map(x => h('span', { class: `ini-st st-${x.status}`, title: x.statusOriginal ? `Original wording: "${x.statusOriginal}"` : '' },
            h('span', { class: 'pl' }, x.plant + ' ·'), statusLabel(x.status),
            ' · ', isNil(x.energySavingKwh) && isNil(x.energyCostSavingCr) ? 'saving not recorded'
              : [!isNil(x.energySavingKwh) ? `${fmt(x.energySavingKwh)} kWh` : null, !isNil(x.energyCostSavingCr) ? `${fmt(x.energyCostSavingCr, 3)} Cr` : null].filter(Boolean).join(' · ')))))))))),
      withSaving.length ? null : h('p', { class: 'ini-ai-note' }, 'None of the plants that implemented these actions recorded a saving figure in the source, so no saving is shown.'));
  });
  return box;
}

// ---------------------------------------------------------------- view 2b: AI recommendations

const SEV = {
  red: { label: 'Red zone', icon: '●', cls: 'red' },
  amber: { label: 'Amber', icon: '▲', cls: 'amber' },
  check: { label: 'Check this entry', icon: '?', cls: 'check' }
};
const KIND = { spike: 'Months above the normal range', sustained_rise: 'Sustained rise year on year', suspect_entry: 'Probable typing or unit mistake' };

function hexAlpha(css, a) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec((css || '').trim());
  return m ? `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})` : css;
}

function paintBands() {
  bandCharts.forEach(({ chart, bands }) => {
    try {
      bands.forEach(b => { const ds = chart.data.datasets[b.index]; if (ds) { ds.backgroundColor = hexAlpha(tone(b.tone), 0.16); ds.borderColor = 'transparent'; } });
      chart.update();
    } catch (err) { /* chart already destroyed */ }
  });
  for (let k = bandCharts.length - 1; k >= 0; k--) if (!bandCharts[k].chart.canvas || !bandCharts[k].chart.canvas.isConnected) bandCharts.splice(k, 1);
}

/** Chart for one finding: actual, expected, upper edge of normal, flagged months, and the after-action line. */
function findingChart(box, f) {
  const c = f.chart || {};
  const months = c.months || [];
  const actual = c.actual || [];
  const labels = months.map(monthLabel);
  const unit = f.unit;
  const levelAt = i => (c.levels && c.levels[i]) || ((f.months || []).includes(months[i]) ? f.severity : null);
  const lv = months.map((_, i) => levelAt(i));
  const after = f.projection && f.projection.after;
  const showAfter = f.kind === 'spike' && Array.isArray(after) && after.some((v, i) => v !== actual[i]);
  // A month flagged "check" (a probable typing mistake) must not squash the scale of an operating finding.
  const finite = actual.filter((v, i) => !isNil(v) && (f.severity === 'check' || lv[i] !== 'check'));
  const clipped = f.severity !== 'check' ? months.filter((_, i) => lv[i] === 'check' && actual[i] > Math.max(...finite) * 1.18) : [];
  if (!finite.length) { box.appendChild(empty('The series has no values to draw.')); return; }
  const isCheck = f.severity === 'check';
  const yMax = Math.max(...finite) * (isCheck ? 3 : 1.18);
  const upperBeyond = (c.upper || []).some(v => !isNil(v) && v > yMax);
  const digits = yMax < 5 ? 2 : yMax < 50 ? 1 : 0;

  const series = [
    { label: 'Actual', data: actual, type: 'line', tone: 'brand', unit, smooth: false },
    { label: 'Expected', data: c.expected, type: 'line', tone: 'muted', dashed: true, points: false, unit, smooth: false },
    { label: 'Upper edge of normal range', data: c.upper, type: 'line', tone: 'warn', dashed: true, points: false, unit, smooth: false }
  ];
  const marks = [];
  if (showAfter) series.push({ label: 'After action (flagged months back at the upper edge)', data: after, type: 'line', tone: 'good', dashed: true, unit, smooth: false });
  const levelsPresent = ['red', 'amber', 'check'].filter(l => lv.includes(l));
  levelsPresent.forEach(l => {
    marks.push({ level: l, index: series.length });
    series.push({ label: `${SEV[l].label} month${l === 'check' ? ' (entry to verify)' : ''}`, data: months.map((_, i) => (lv[i] === l ? actual[i] : null)), type: 'line', tone: l === 'red' ? 'bad' : l === 'amber' ? 'warn' : 'info', unit, smooth: false });
  });
  const bands = [];
  levelsPresent.forEach(l => {
    bands.push({ index: series.length, tone: l === 'red' ? 'bad' : l === 'amber' ? 'warn' : 'info' });
    series.push({ label: `Shaded: ${SEV[l].label.toLowerCase()} period`, data: months.map((_, i) => (lv[i] === l ? yMax : null)), type: 'bar', tone: l === 'red' ? 'bad' : l === 'amber' ? 'warn' : 'info', unit });
  });

  const chart = seriesChart(box, labels, series, { type: 'line', yTitle: `${unit} per month`, height: 'tall', legend: true, max: isCheck ? undefined : yMax, tickDigits: digits, digits: undefined });
  marks.forEach(m => {
    const ds = chart.data.datasets[m.index];
    ds.pointRadius = ds.data.map(v => (isNil(v) ? 0 : 7));
    ds.pointHoverRadius = 9;
    ds.pointStyle = m.level === 'check' ? 'rectRot' : m.level === 'amber' ? 'triangle' : 'circle';
    ds.showLine = false;
  });
  if (showAfter) {
    const ds = chart.data.datasets[3];
    ds.pointRadius = months.map((_, i) => (lv[i] && after[i] !== actual[i] ? 4 : 0));
  }
  const bandIdx = new Set(bands.map(b => b.index));
  bands.forEach(b => { const ds = chart.data.datasets[b.index]; ds._tone = undefined; ds.stack = 'band'; ds.barPercentage = 1; ds.categoryPercentage = 1; ds.borderWidth = 0; ds.borderRadius = 0; ds.order = 3; });
  chart.options.plugins.legend.labels.filter = it => !bandIdx.has(it.datasetIndex);
  chart.options.plugins.tooltip.filter = it => !bandIdx.has(it.datasetIndex);
  if (isCheck) {
    chart.options.scales.y.type = 'logarithmic';
    chart.options.scales.y.beginAtZero = false;
    chart.options.scales.y.ticks.callback = v => (String(v).replace(/0/g, '') === '1' || [2, 5].includes(Number(String(v)[0])) ? fmt(v, v < 1 ? 2 : 0) : '');
  }
  bandCharts.push({ chart, bands });
  paintBands();
  return { upperBeyond, showAfter, isCheck, bandPct: c.bandPct, clipped };
}

function findingCard(f, plantNames) {
  const sev = SEV[f.severity] || SEV.amber;
  const rec = f.recommendation;
  const isCheck = f.severity === 'check';
  const unit = f.unit;
  // Proven actions only for operating findings; a water action is not offered for an electricity or diesel finding.
  const related = isCheck || !rec ? [] : (rec.related_initiatives || []).filter(r => r.domain !== 'water' || f.metric === 'water');
  const avoid = rec && Number(rec.avoidable) > 0 ? rec.avoidable : null;
  const chartBox = h('div');
  const noteBox = h('div', { class: 'ini-chartnote' });
  const facts = [
    ['Flagged months', (f.months || []).map(monthLabel).join(', ')],
    [isCheck ? 'Figure as entered' : (f.kind === 'sustained_rise' ? 'Last three months' : 'Actual in flagged months'), h('span', null, val(f.observed), h('small', null, unit))],
    [isCheck ? 'Expected for those months' : (f.kind === 'sustained_rise' ? 'Same months a year earlier' : 'Expected for them'), h('span', null, val(f.expected), h('small', null, unit))]
  ];
  if (!isCheck) facts.push(['Above expected', h('span', null, `${f.excess_pct >= 0 ? '+' : ''}${fmt(f.excess_pct, 1)}%`, h('small', null, `${fmt(f.excess, Math.abs(f.excess) >= 100 ? 0 : 2)} ${unit}`))]);
  const card = h('article', { class: `card ini-find ${sev.cls}`, dataset: { severity: f.severity, plant: f.plant, metric: f.metric } },
    h('div', null,
      h('div', { class: 'ini-find-tags' },
        h('span', { class: `sev ${sev.cls}` }, h('span', { 'aria-hidden': 'true' }, sev.icon), sev.label),
        badge(KIND[f.kind] || f.kind),
        rec ? badge(rec.source === 'internal-ai' ? 'Written by the internal AI server' : 'Written by the built-in model', 'brand') : null),
      h('h3', null, `${plantNames[f.plant] || f.plantName || f.plant} · ${f.metricLabel || METRIC_NAME[f.metric] || f.metric}`),
      h('div', { class: 'small muted' }, `${f.label} · ${f.plant} · monthly, ${unit}`),
      h('p', { class: 'ini-evidence' }, clean(f.evidence)),
      h('div', { class: 'ini-facts' },
        facts.map(([k, v]) => h('div', { class: 'ini-fact' }, h('b', null, k), h('span', null, v))),
        avoid ? h('div', { class: 'ini-fact save' }, h('b', null, f.kind === 'sustained_rise' ? 'Avoidable amount (rise vs last year)' : 'Avoidable amount'), h('span', null, val(avoid), h('small', null, unit))) : null),
      isCheck ? h('p', { class: 'small muted' }, 'No saving is claimed: a figure this far outside the pattern is more likely a mistake in the sheet than consumption.') : null,
      rec ? h('div', { class: 'ini-actions' },
        h('h4', null, isCheck ? 'How to verify the figure' : 'Suggested checks'),
        isCheck ? null : h('div', { class: 'gen' }, 'Generic guidance from the model, not a measurement. The measured facts are the figures above and the chart.'),
        h('ol', null, (rec.actions || []).map(a => h('li', null, a)))) : h('p', { class: 'muted' }, 'No recommendation was returned for this finding.'),
      related.length ? h('div', { class: 'ini-related' },
        h('h4', null, 'Already proven at other plants'),
        h('ul', null, related.map(r => h('li', null, r.description,
          ' (', `${statusLabel(r.statusAtPlant)} here`, '; implemented at ',
          (r.implementedAt || []).map(x => x.plant + (!isNil(x.energySavingKwh) ? ` ${fmt(x.energySavingKwh)} kWh` : '')).join(', '),
          (r.implementedAt || []).some(x => !isNil(x.energySavingKwh)) ? '' : '; no saving recorded there', ')')))) : null),
    h('div', { class: 'ini-chartbox' },
      h('h4', null, isCheck ? 'Monthly series with the entry to check (log scale)' : f.kind === 'sustained_rise' ? 'Monthly series and the sustained rise' : f.severity === 'red' ? 'Red zone and the series after action' : 'Months above the normal range and the series after action'),
      h('div', { class: 'cap' }, `${f.label}, ${unit} per month, last ${(f.chart && f.chart.months ? f.chart.months.length : 18)} months with data. Gaps are months with no reported value.`),
      chartBox, noteBox));
  defer(() => {
    const info = findingChart(chartBox, f);
    if (!info) return;
    const bits = [];
    if (!isNil(info.bandPct)) bits.push(`Normal month-to-month variation: ±${fmt(info.bandPct, 1)}%.`);
    if (info.isCheck) bits.push('Log scale, because the flagged figures are many times the others. The shaded months are the entries to verify; there is no after-action line, since they are probably mistakes rather than consumption.');
    else if (info.showAfter) bits.push(`Dashed green line: the flagged months brought back to the upper edge of the normal range${avoid ? `, which removes ${val(avoid)} ${unit}` : ''}. Shaded months are the flagged ones.`);
    else bits.push('No after-action line: this finding is measured against the same months a year earlier, not against a normal range.');
    if (info.clipped && info.clipped.length) bits.push(`The ${info.clipped.map(monthLabel).join(', ')} figure is flagged separately as an entry to check and lies above the top of this chart.`);
    if (info.upperBeyond) bits.push('The scale follows the actual values; the upper edge continues above the top of the chart where it is higher.');
    noteBox.textContent = bits.join(' ');
  });
  return card;
}

function providerBlock(rec, status) {
  const p = rec.provider || {};
  const name = p.name === 'local-model' ? 'Built-in local model' : p.name === 'internal-ai' ? 'Company internal AI server' : (p.name || 'Unknown');
  const method = rec.method || {};
  return h('div', null,
    h('div', { class: 'ini-provider', role: 'note' },
      h('span', { class: 'pv-label' }, 'Recommendations produced by'),
      h('span', { class: 'pv-name' }, name),
      p.name ? badge(p.name, 'brand') : null,
      h('span', { class: 'pv-desc' }, [p.description,
        status ? ` Configured provider: ${status.configured}; active: ${status.active}${status.internalUrlSet ? '' : '; no internal server address is set'}.` : (p.configured ? ` Configured provider: ${p.configured}.` : '')].join(''))),
    h('details', { class: 'ini-how' },
      h('summary', null, 'How this works'),
      h('p', { class: 'ini-ai-note' }, 'Every number on the cards can be traced to these rules. The model only reads the monthly series already shown on the Energy and Water pages.'),
      dl(Object.entries(method).map(([k, v]) => [METHOD_LABEL[k] || k, clean(String(v))]).concat([['Series analysed', isNil(rec.seriesAnalysed) ? null : `${fmt(rec.seriesAnalysed)} plant series (electricity, diesel${(rec.notes || []).some(n => /water/i.test(n)) ? '' : ', water'})`]])),
      status && status.howToConnect ? h('p', { class: 'small muted', style: { marginTop: '10px' } }, 'Switching to the internal AI server: ' + status.howToConnect) : null));
}

function summaryTiles(findings, rec) {
  const c = s => findings.filter(f => f.severity === s).length;
  const metrics = Object.keys(((rec.counts || {}).byMetric) || METRIC_NAME);
  const metricRows = metrics.map(m => {
    const l = findings.filter(f => f.metric === m);
    return h('tr', null, h('td', null, METRIC_NAME[m] || m),
      h('td', { class: 'num' }, fmt(l.filter(f => f.severity === 'red').length)), h('td', { class: 'num' }, fmt(l.filter(f => f.severity === 'amber').length)),
      h('td', { class: 'num' }, fmt(l.filter(f => f.severity === 'check').length)), h('td', { class: 'num' }, fmt(l.length)));
  });
  return h('div', null,
    h('div', { class: 'grid cols-4 ini-tiles' },
      h('div', { class: 'mcard ini-tile red' }, h('div', { class: 'mcard-value' }, fmt(c('red'))), h('div', { class: 'mcard-label' }, '● Red zone'), h('div', { class: 'mcard-sub' }, 'Far above the normal range')),
      h('div', { class: 'mcard ini-tile amber' }, h('div', { class: 'mcard-value' }, fmt(c('amber'))), h('div', { class: 'mcard-label' }, '▲ Amber'), h('div', { class: 'mcard-sub' }, 'Above the normal range, or a sustained rise')),
      h('div', { class: 'mcard ini-tile check' }, h('div', { class: 'mcard-value' }, fmt(c('check'))), h('div', { class: 'mcard-label' }, '? Check this entry'), h('div', { class: 'mcard-sub' }, 'Probable typing or unit mistake, no saving claimed')),
      h('div', { class: 'mcard ini-tile' }, h('div', { class: 'mcard-value' }, fmt(findings.length)), h('div', { class: 'mcard-label' }, 'Findings shown'), h('div', { class: 'mcard-sub' }, 'Last 12 reported months of each series'))),
    h('div', { class: 'ini-bymetric table-wrap' }, h('table', { class: 'table' },
      h('thead', null, h('tr', null, h('th', null, 'Metric'), h('th', { class: 'num' }, 'Red zone'), h('th', { class: 'num' }, 'Amber'), h('th', { class: 'num' }, 'Check this entry'), h('th', { class: 'num' }, 'Total'))),
      h('tbody', null, metricRows))));
}

function aiBlock(state, data) {
  const box = h('div');
  loadSlow(box, Promise.all([data.rec, data.aiStatus]), ([rec, status]) => {
    const all = rec.findings || [];
    const findings = all.filter(f => !state.plant || f.plant === state.plant);
    const out = [providerBlock(rec, status), summaryTiles(findings, rec)];
    if (!findings.length) {
      out.push(h('div', { style: { marginTop: '14px' } }, empty(`No abnormal months in the last 12 reported months${state.plant ? ' for ' + state.plant : ''}.`)));
    } else {
      out.push(h('div', { class: 'ini-finds' }, findings.map(f => findingCard(f, data.plantNames))));
    }
    out.push(notes(rec.notes));
    return out;
  });
  return box;
}

function drawUpcoming(box, state, data) {
  const pBox = h('div'), eBox = elsewhereBlock(state, data);
  box.append(
    section('Planned initiatives', 'From the FY 2026-27 plan and the "Future Plan for Improvements" rows of the calculation workbook (phase: planned)', pBox),
    section('Implemented elsewhere, not yet here', 'Proven actions: another plant has implemented them and the selected plant has them open or under study', eBox),
    section('AI-assisted recommendations', 'Abnormal months found in the monthly electricity, diesel and water series, with suggested checks', aiBlock(state, data)));
  loadSlow(pBox, data.list, list => [plannedBlock(list.items || [], state, data), notes(list.notes)]);
}

// ---------------------------------------------------------------- page

export async function render(root, ctx) {
  ensureCss();
  token++;
  const my = token;
  const p = (ctx && ctx.params) || {};
  const state = { view: VIEWS.some(v => v.id === p.view) ? p.view : 'present', plant: PLANTS.includes(p.plant) ? p.plant : '' };
  const plantNames = Object.fromEntries(PLANTS.map(x => [x, x]));
  const data = {
    plantNames,
    list: api.get('/initiatives'),
    rec: api.get('/ai/recommendations'),
    aiStatus: api.get('/ai/status').catch(() => null)
  };
  // Failed requests must not leave an unhandled rejection; each block shows its own error.
  [data.list, data.rec].forEach(x => x.catch(() => {}));
  api.get('/plants').then(list => { (list || []).forEach(pl => { if (plantNames[pl.id]) plantNames[pl.id] = pl.name || pl.id; }); drawPlantBar(); }).catch(() => {});

  const plantBar = h('div', { class: 'plant-bar bleed', role: 'group', 'aria-label': 'Plant' });
  const viewBar = h('div', { class: 'sub-bar bleed ini-view-bar', role: 'tablist', 'aria-label': 'Initiatives view' });
  const body = h('div');
  root.append(plantBar, viewBar, pageHead('Initiatives', 'Energy and water saving initiatives across the eight plants: what is already in place and what comes next'), body);

  const counts = { present: {}, upcoming: {} };

  function syncUrl() {
    const q = new URLSearchParams({ view: state.view });
    if (state.plant) q.set('plant', state.plant);
    history.replaceState(null, '', '#/initiatives?' + q.toString());
  }

  function drawPlantBar() {
    clear(plantBar);
    const cnt = counts[state.view];
    [{ id: '', label: 'All plants (TADL)' }, ...PLANTS.map(x => ({ id: x, label: plantNames[x] }))].forEach(o => {
      const n = cnt[o.id];
      plantBar.appendChild(h('button', { type: 'button', class: 'plant-tab' + (state.plant === o.id ? ' active' : ''), 'aria-pressed': String(state.plant === o.id),
        title: n === undefined ? null : (state.view === 'present' ? `${n} present initiative records mention this plant (initiatives file and calculation workbook)` : `${n} AI finding${n === 1 ? '' : 's'}`),
        onClick: () => { if (state.plant !== o.id) { state.plant = o.id; drawAll(); } } },
      o.label, n === undefined ? null : h('span', { class: 'count' }, fmt(n))));
    });
  }

  function drawViewBar() {
    clear(viewBar);
    VIEWS.forEach(v => viewBar.appendChild(h('button', { type: 'button', role: 'tab', class: 'sub-tab' + (state.view === v.id ? ' active' : ''), 'aria-selected': String(state.view === v.id),
      onClick: () => { if (state.view !== v.id) { state.view = v.id; drawAll(); } } }, v.label)));
    viewBar.appendChild(h('span', { class: 'ini-view-hint' }, state.view === 'present' ? 'Count on each plant: initiative records that mention it' : 'Count on each plant: AI findings'));
  }

  function drawAll() {
    destroyCharts(body);
    bandCharts.length = 0;
    timers.forEach(clearTimeout); timers = [];
    syncUrl();
    drawViewBar();
    drawPlantBar();
    clear(body);
    if (state.view === 'present') drawPresent(body, state, data); else drawUpcoming(body, state, data);
  }

  if (!observer) {
    observer = new MutationObserver(() => paintBands());
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }

  drawAll();

  // Counts for the plant tabs, filled in when the data arrives.
  data.list.then(l => {
    if (my !== token) return;
    const present = (l.items || []).filter(i => i.phase === 'present');
    counts.present[''] = present.length;
    PLANTS.forEach(x => { counts.present[x] = present.filter(i => (i.plants || []).includes(x)).length; });
    drawPlantBar();
  }).catch(() => {});
  data.rec.then(r => {
    if (my !== token) return;
    counts.upcoming[''] = (r.findings || []).length;
    PLANTS.forEach(x => { counts.upcoming[x] = (r.findings || []).filter(f => f.plant === x).length; });
    drawPlantBar();
  }).catch(() => {});
}
