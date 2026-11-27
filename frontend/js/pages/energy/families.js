// Machine families & production tab: energy mapped to production for each
// machine family at each site (source: "Power Consumption wrt Production Units"
// workbook, via /api/production). Every figure shown comes from the API.

import { api } from '../../api.js';
import { badge, clear, empty, fmt, h, load, monthLabel, notes, section, select } from '../../ui.js';
import { seriesChart, destroyCharts } from '../../charts.js';

const METRICS = [
  { id: 'energy', label: 'Energy' },
  { id: 'production', label: 'Production' },
  { id: 'energyPerUnit', label: 'Energy per unit' }
];
const METRIC_IDS = METRICS.map(m => m.id);

const CATEGORY_DOMAINS = {
  hvac: ['hvac'], compressor: ['compressed_air'], lighting: ['lighting'],
  furnace: ['heating_fuel'], oven: ['heating_fuel'], autoclave: ['hvac', 'heating_fuel'],
  'chemical-line': ['drives_motors'], paint: [], machines: ['drives_motors', 'energy_management'],
  cca: [], assembly: [], 'ship-set': [], revenue: [], other: []
};
const CATEGORY_TERMS = {
  hvac: ['hvac', 'ahu', 'chiller', 'cooling'], compressor: ['compressor', 'compressed air', 'air leak'],
  lighting: ['lighting', 'light', 'lux'], furnace: ['furnace'], oven: ['oven'], autoclave: ['autoclave'],
  paint: ['paint', 'booth'], 'chemical-line': ['chemical', 'anodizing', 'paa', 'process line'],
  cca: ['cca'], assembly: ['assembly'], machines: [], 'ship-set': [], revenue: [], other: []
};
const CATEGORY_ACTIONS = {
  hvac: 'Check operating schedules, temperature set-points and idle-hour AHU load; then verify whether timer, occupancy control or VFD action is applicable.',
  compressor: 'Run an air-leak survey, review discharge pressure and check unloaded running hours before changing controls.',
  lighting: 'Check lux levels and operating hours, then review zoning, timers and occupancy controls.',
  paint: 'Compare booth and exhaust operating hours with production; investigate idle ventilation, make-up air and batch scheduling.',
  'chemical-line': 'Compare energy with batches processed; review idle heating, pumps, ventilation and loading sequence.',
  furnace: 'Review batch loading, warm-up, idle holding and insulation losses before changing the process recipe.',
  oven: 'Review batch loading, warm-up, idle holding and temperature scheduling before changing the process recipe.',
  autoclave: 'Review loading, cycle scheduling, cooling and idle auxiliary loads; compare energy per completed cycle.',
  cca: 'Compare energy per production unit and isolate base load during non-production hours.',
  assembly: 'Compare energy with production and check idle utilities, shutdown discipline and shift scheduling.',
  machines: 'Sub-meter loaded and idle periods, then investigate the machines carrying the highest base load.',
  'ship-set': 'Compare energy per ship-set with the recent baseline and investigate months with energy but little or no output.',
  revenue: 'Use the energy-to-revenue ratio as a screening signal, then trace the increase to sub-metered equipment before acting.',
  other: 'Compare the latest month with production and operating hours, then inspect idle and base-load consumption.'
};
const STATUS_LABEL = {
  implemented: 'Implemented', in_progress: 'In progress', open: 'Open', under_study: 'Under study',
  horizontally_deployed: 'Horizontally deployed', not_feasible: 'Not feasible', not_applicable: 'Not applicable',
  on_hold: 'On hold'
};

function ensureCss() {
  if (document.getElementById('energy-families-css')) return;
  document.head.appendChild(h('link', { id: 'energy-families-css', rel: 'stylesheet', href: '/static/css/energy-families.css' }));
}

export function destroy() { /* nothing runs in the background; charts are cleaned up by the frame */ }

// Charts are drawn after the block is attached to the page (a canvas needs layout).
const defer = fn => setTimeout(() => { try { fn(); } catch (err) { console.error(err); } }, 0);
const allNull = arr => !arr || arr.every(v => v === null || v === undefined);
const uniq = a => [...new Set(a.filter(Boolean))];
const isNil = v => v === null || v === undefined;
const num = (v, unit, digits) => (isNil(v)
  ? '—'
  : [fmt(v, digits || 0), unit ? h('span', { class: 'fam-unit' }, unit) : null]);

function btnGroup(options, active, onChange, label) {
  const el = h('div', { class: 'chips', role: 'group', 'aria-label': label });
  const set = id => [...el.children].forEach(b => {
    b.classList.toggle('active', b.dataset.id === id);
    b.setAttribute('aria-pressed', String(b.dataset.id === id));
  });
  options.forEach(o => el.appendChild(h('button', { type: 'button', class: 'chip', dataset: { id: o.id }, onClick: () => { set(o.id); onChange(o.id); } }, o.label)));
  set(active);
  return el;
}

const daysIn = ym => { const m = /^(\d{4})-(\d{2})$/.exec(ym || ''); return m ? new Date(Number(m[1]), Number(m[2]), 0).getDate() : null; };

function relatedInitiatives(items, machine) {
  const category = machine.category || 'other';
  const domains = CATEGORY_DOMAINS[category] || [];
  const rawText = `${machine.name || ''} ${machine.categoryLabel || ''}`.toLowerCase();
  const machineTerms = rawText.split(/[^a-z0-9]+/).filter(term => term.length >= 4 && !['plant', 'shop', 'line', 'machines', 'machine'].includes(term));
  const categoryTerms = CATEGORY_TERMS[category] || [];
  const scored = (items || []).map(item => {
    const text = `${item.description || ''} ${item.equipmentArea || ''} ${item.category || ''}`.toLowerCase();
    let score = domains.includes(item.domain) ? 8 : 0;
    categoryTerms.forEach(term => { if (text.includes(term)) score += 4; });
    machineTerms.forEach(term => { if (text.includes(term)) score += 2; });
    return { item, score };
  }).filter(row => row.score >= 6).sort((a, b) => b.score - a.score || (a.item.phase === 'present' ? -1 : 1));

  const unique = new Map();
  scored.forEach(({ item }) => {
    const key = (item.description || '').toLowerCase().replace(/[^a-z0-9]+/g, '') || item.groupKey || item.id;
    const current = unique.get(key);
    if (!current || (current.phase !== 'present' && item.phase === 'present')) unique.set(key, item);
  });
  return [...unique.values()].slice(0, 5);
}

function initiativeStatus(item, plant) {
  const row = (item.plantStatuses || []).find(status => status.plant === plant);
  return row && row.status ? STATUS_LABEL[row.status] || row.status : (item.phase === 'planned' ? 'Planned' : 'Not recorded');
}

function initiativeSaving(item, plant) {
  const achieved = item.achieved || {};
  const row = (item.plantStatuses || []).find(status => status.plant === plant);
  const kwh = row && !isNil(row.energySavingKwh) ? row.energySavingKwh : achieved.energyKwh;
  const cost = row && !isNil(row.energyCostSavingCr) ? row.energyCostSavingCr : achieved.energyCostCr;
  const parts = [];
  if (!isNil(kwh)) parts.push(`${fmt(kwh)} kWh recorded saving`);
  if (!isNil(cost)) parts.push(`${fmt(cost, 3)} Cr recorded cost saving`);
  return parts.length ? parts.join(' · ') : 'Saving not recorded in the source';
}

function latestPoint(months, values) {
  for (let index = (values || []).length - 1; index >= 0; index--) {
    if (!isNil(values[index])) return { index, month: months[index], value: values[index] };
  }
  return null;
}

function previousPoint(months, values, before) {
  for (let index = before - 1; index >= 0; index--) {
    if (!isNil(values[index])) return { index, month: months[index], value: values[index] };
  }
  return null;
}

/**
 * Card data for one plant, from the API only: each family's latest month that has an
 * energy value, with production and energy per unit for that same month (null where the
 * sheet has none). `plant` is a plant id, site code or sheet name.
 */
export async function plantFamilies(plant) {
  const all = await api.get('/production/sites');
  const s = (all.sites || []).find(x => x.plant === plant || x.code === plant || x.sheet === plant);
  if (!s) return { site: null, plant, mappingConfidence: null, latestMonth: null, families: [], notes: [`No production sheet maps to ${plant}.`] };
  const get = m => api.get(`/production/sites/${encodeURIComponent(s.code)}/compare`, { metric: m });
  const [en, pr, ra] = await Promise.all(METRIC_IDS.map(get));
  const val = (c, id, month) => {
    const ser = (c.series || []).find(x => x.id === id);
    const i = c.months.indexOf(month);
    return ser && i >= 0 && !isNil(ser.values[i]) ? ser.values[i] : null;
  };
  const families = s.families.map(f => {
    // latest month that actually holds an energy value (the summary's lastEnergyMonth, checked against the values)
    const ser = (en.series || []).find(x => x.id === f.id);
    let m = null;
    if (ser) for (let i = en.months.length - 1; i >= 0; i--) if (!isNil(ser.values[i])) { m = en.months[i]; break; }
    return {
      id: f.id, name: f.name, category: f.category, categoryLabel: f.categoryLabel, energyUnit: f.energyUnit,
      latestMonth: m, latestEnergy: val(en, f.id, m), daysInLatestMonth: daysIn(m),
      production: val(pr, f.id, m), productionUnit: f.productionUnit,
      energyPerUnit: val(ra, f.id, m), energyPerUnitUnit: f.energyPerUnitUnit, hasTarget: !!f.hasTarget
    };
  });
  const months = families.map(f => f.latestMonth).filter(Boolean).sort();
  return {
    site: s.code, plant: s.plant, mappingConfidence: s.mappingConfidence,
    latestMonth: months.length ? months[months.length - 1] : null,
    families, notes: [...(all.notes || [])]
  };
}

export async function render(root, ctx) {
  ensureCss();
  const embedded = !!ctx.embedded;
  const p = ctx.params || {};
  const state = {
    site: embedded ? null : (p.site || null), family: embedded ? (ctx.family || null) : (p.family || null),
    metric: METRIC_IDS.includes(p.metric) ? p.metric : 'energy',
    cat: p.cat || null,
    cmetric: METRIC_IDS.includes(p.cmetric) ? p.cmetric : 'energyPerUnit'
  };

  function syncUrl() {
    if (embedded) return; // the page frame owns the address
    const q = new URLSearchParams({ tab: 'families' });
    Object.entries({ site: state.site, family: state.family, metric: state.metric, cat: state.cat, cmetric: state.cmetric })
      .forEach(([k, v]) => { if (v) q.set(k, v); });
    history.replaceState(null, '', '#/energy?' + q.toString());
  }

  const siteBox = h('div');
  const overviewBox = h('div');
  const detailBox = h('div', { class: 'fam-block' });
  const compareBox = h('div', { class: 'fam-block' });
  const catBox = h('div', { class: 'fam-block' });
  const aboutBox = h('div', { class: 'fam-about' });
  root.append(siteBox, overviewBox, detailBox, compareBox, catBox, aboutBox);

  let sitesData;
  try {
    sitesData = await api.get('/production/sites');
  } catch (err) {
    await load(siteBox, Promise.reject(err), () => null);
    return;
  }
  const sites = sitesData.sites || [];
  if (!sites.length) { siteBox.appendChild(empty('The production workbook lists no sites.')); return; }
  if (embedded) {
    const m = sites.find(s => s.plant === ctx.plant || s.code === ctx.plant || s.sheet === ctx.plant);
    if (!m) { clear(siteBox).appendChild(empty(`No machine-family sheet in the production workbook maps to ${ctx.plant || 'this plant'}.`)); return; }
    state.site = m.code;
  } else if (!sites.some(s => s.code === state.site)) { state.site = sites[0].code; state.family = null; }
  const siteOf = () => sites.find(s => s.code === state.site);

  // ------------------------------------------------------------ site picker
  function drawSites() {
    clear(siteBox);
    const chipRow = h('div', { class: 'chips', role: 'group', 'aria-label': 'Site' });
    sites.forEach(s => {
      const on = s.code === state.site;
      chipRow.appendChild(h('button', {
        type: 'button', class: 'chip fam-site-chip' + (on ? ' active' : ''), 'aria-pressed': String(on),
        title: s.mappingConfidence !== 'high' ? `Mapping confidence: ${s.mappingConfidence}. ${s.mappingEvidence || ''}` : null,
        onClick: () => { if (!on) { state.site = s.code; state.family = null; drawAll(); } }
      }, h('span', null, `${s.plantName} (${s.code})`),
      s.mappingConfidence !== 'high' ? h('small', null, `mapping: ${s.mappingConfidence}`) : null));
    });
    const s = siteOf();
    siteBox.append(
      h('p', { class: 'fam-sub' }, 'Pick a site. Each site is one sheet of the production workbook; energy is in kWh unless stated; period FY25-26 (Apr-25 to Mar-26).'),
      chipRow,
      h('div', { class: 'fam-site-meta' },
        h('span', null, `${s.plantName} (${s.code}) · sheet ${s.sheet} · ${s.familyCount} machine families · months with data ${monthLabel(s.firstMonth)} to ${monthLabel(s.lastMonth)}`),
        s.mappingConfidence !== 'high' ? badge('Site mapping: ' + s.mappingConfidence, 'warn') : null,
        h('details', null, h('summary', null, 'How this sheet was mapped to a plant'),
          h('p', { class: 'fam-evidence' }, `Confidence: ${s.mappingConfidence}. ${s.mappingEvidence || 'No evidence recorded.'}`))));
  }

  // ------------------------------------------------------------ overview
  function drawOverview() {
    const s = siteOf();
    const get = m => api.get(`/production/sites/${encodeURIComponent(s.code)}/compare`, { metric: m });
    load(overviewBox, Promise.all(METRIC_IDS.map(get)), ([en, pr, ra]) => {
      const byId = (c, id) => (c.series || []).find(x => x.id === id);
      const at = (ser, month) => {
        if (!ser || !month) return null;
        const i = en.months.indexOf(month);
        return i < 0 ? null : (isNil(ser.values[i]) ? null : ser.values[i]);
      };
      const rows = s.families.map(f => {
        const m = f.lastEnergyMonth;
        const r = at(byId(ra, f.id), m);
        return h('tr', { class: f.id === state.family ? 'fam-selected' : null, dataset: { family: f.id } },
          h('td', null, h('button', { type: 'button', class: 'fam-pick', 'aria-current': f.id === state.family ? 'true' : null, onClick: () => selectFamily(f.id) }, f.name)),
          h('td', null, f.categoryLabel || f.category),
          h('td', null, m ? monthLabel(m) : '—'),
          h('td', { class: 'num' }, num(at(byId(en, f.id), m), f.energyUnit)),
          h('td', { class: 'num' }, f.hasProduction ? num(at(byId(pr, f.id), m), f.productionUnit) : h('span', { title: 'No production values in the sheet' }, 'none in sheet')),
          h('td', { class: 'num' }, num(r, f.energyPerUnitUnit, Math.abs(r ?? 1) < 1 ? 3 : 2)),
          h('td', null, f.hasTarget ? badge('Target in deck', 'info') : h('span', { class: 'muted' }, 'No target')));
      });
      return section(`Machine families at ${s.plantName} (${s.code})`,
        'Latest month with energy for each family, with its production and energy per production unit (units shown with each value). Select a family for the detail below.',
        h('div', { class: 'table-wrap' }, h('table', { class: 'table fam-table' },
          h('thead', null, h('tr', null, ['Family', 'Category', 'Latest month', 'Energy', 'Production', 'Energy per unit', 'Target']
            .map((t, i) => h('th', { class: i > 2 && i < 6 ? 'num' : null }, t)))),
          h('tbody', null, rows))));
    });
  }

  function selectFamily(id) {
    state.family = id;
    syncUrl();
    overviewBox.querySelectorAll('tr[data-family]').forEach(tr => {
      const on = tr.dataset.family === id;
      tr.classList.toggle('fam-selected', on);
      const b = tr.querySelector('.fam-pick');
      if (on) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
    drawDetail();
  }

  // ------------------------------------------------------------ family detail
  function kpiTile(label, value, unit, sub, digits) {
    const none = isNil(value);
    return h('div', { class: 'card kpi' }, h('div', { class: 'kpi-label' }, label),
      h('div', { class: 'kpi-value' }, none ? '—' : fmt(value, digits), !none && unit ? h('small', null, unit) : null),
      h('div', { class: 'kpi-sub' }, none ? 'Not available. ' + sub : sub));
  }

  function chartCard(title, sub) {
    const body = h('div');
    return { body, el: h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, title), h('p', null, sub))), body) };
  }

  function analysisCard(kind, title, value, text) {
    return h('article', { class: `machine-analysis-card ${kind || ''}` },
      h('div', { class: 'machine-analysis-label' }, title),
      value ? h('div', { class: 'machine-analysis-value' }, value) : null,
      h('p', null, text));
  }

  function initiativePanel(host, machine, plant) {
    load(host, api.get('/initiatives', { plant }), data => {
      const matches = relatedInitiatives(data.items || [], machine);
      const action = CATEGORY_ACTIONS[machine.category] || CATEGORY_ACTIONS.other;
      if (!matches.length) {
        return h('div', { class: 'machine-initiative-panel' },
          h('div', { class: 'machine-initiative-head' },
            h('div', null, h('h3', null, 'Initiative, action and cost'),
              h('p', null, 'No machine-specific initiative is linked in the source files.')),
            h('a', { href: `#/initiatives?plant=${encodeURIComponent(plant)}` }, 'Open Initiatives')),
          h('div', { class: 'machine-action-fallback' },
            h('strong', null, 'Recommended operational check'),
            h('p', null, action),
            h('small', null, 'This check is derived from the machine category and trend; it is not a recorded investment commitment.')),
          h('p', { class: 'machine-cost-note' }, 'Investment cost and expected financial saving: not recorded for this machine. No value has been estimated.'));
      }

      return h('div', { class: 'machine-initiative-panel' },
        h('div', { class: 'machine-initiative-head' },
          h('div', null, h('h3', null, 'Initiative, action and cost'),
            h('p', null, `${matches.length} relevant source record${matches.length === 1 ? '' : 's'} matched to ${machine.categoryLabel || machine.category}.`)),
          h('a', { href: `#/initiatives?plant=${encodeURIComponent(plant)}` }, 'Open all initiatives')),
        h('div', { class: 'machine-initiative-list' }, matches.map(item => {
          const status = initiativeStatus(item, plant);
          const investment = isNil(item.investmentRs) ? 'Not recorded' : `₹${fmt(item.investmentRs)}`;
          const expected = isNil(item.expectedSavingPerYear) ? 'Not recorded' : `${fmt(item.expectedSavingPerYear)} per year (source unit)`;
          return h('article', { class: 'machine-initiative' },
            h('div', { class: 'machine-initiative-top' },
              badge(status, /implemented|deployed/i.test(status) ? 'good' : /progress/i.test(status) ? 'info' : 'warn'),
              h('span', null, item.equipmentArea || item.domain || 'Energy')),
            h('h4', null, item.description),
            h('div', { class: 'machine-initiative-facts' },
              h('span', null, h('strong', null, 'Action: '), item.description),
              h('span', null, h('strong', null, 'Investment cost: '), investment),
              h('span', null, h('strong', null, 'Expected saving: '), expected),
              h('span', null, h('strong', null, 'Recorded result: '), initiativeSaving(item, plant))),
            h('small', null, `Source: ${(item.source && item.source.workbook) || 'initiative workbook'}${item.source && item.source.row ? `, row ${item.source.row}` : ''}`));
        })),
        h('div', { class: 'machine-action-fallback' },
          h('strong', null, 'Recommended next check for this machine'),
          h('p', null, action),
          h('small', null, 'Validate the operating condition before approving an initiative or assigning cost.')));
    });
  }

  function machineAnalysis(d, s, fam) {
    const energy = d.energy || { values: [] };
    const latest = latestPoint(d.months || [], energy.values || []);
    const previous = latest ? previousPoint(d.months || [], energy.values || [], latest.index) : null;
    const unit = energy.unit || (fam && fam.energyUnit) || 'kWh';
    const daily = latest && daysIn(latest.month) ? latest.value / daysIn(latest.month) : null;
    let trend = 'There are not enough recorded months to describe a change.';
    if (latest && previous && previous.value !== 0) {
      const pct = ((latest.value - previous.value) / Math.abs(previous.value)) * 100;
      const direction = Math.abs(pct) < 2 ? 'approximately unchanged from' : pct > 0 ? `${fmt(Math.abs(pct), 1)}% above` : `${fmt(Math.abs(pct), 1)}% below`;
      trend = `${monthLabel(latest.month)} is ${direction} ${monthLabel(previous.month)} (${fmt(previous.value)} ${unit}).`;
    }

    const ratios = d.energyPerUnit || {};
    const actualRatio = !allNull(ratios.sheetValues) ? ratios.sheetValues : ratios.computedValues;
    const ratioAtLatest = latest && actualRatio ? actualRatio[latest.index] : null;
    const targetAtLatest = latest && d.target && d.target.verifiedAgainstSheet === true && d.target.values
      ? d.target.values[latest.month] : null;
    let targetText = 'No comparable target is recorded for the latest month.';
    if (!isNil(ratioAtLatest) && !isNil(targetAtLatest) && targetAtLatest !== 0) {
      const gap = ((ratioAtLatest - targetAtLatest) / Math.abs(targetAtLatest)) * 100;
      targetText = ` Energy per unit is ${fmt(Math.abs(gap), 1)}% ${gap > 0 ? 'above' : 'below'} the deck target.`;
    }

    const initiativeHost = h('div', { class: 'machine-initiative-host' });
    initiativePanel(initiativeHost, { ...d, categoryLabel: fam && fam.categoryLabel }, s.plant);
    return h('div', { class: 'machine-analysis-summary' },
      h('div', { class: 'machine-analysis-grid' },
        analysisCard('current', 'Latest recorded consumption', latest ? `${fmt(latest.value)} ${unit}` : 'Not recorded',
          latest ? `${monthLabel(latest.month)} monthly energy${isNil(daily) ? '' : ` · ${fmt(daily, 1)} ${unit}/day average`}. This is not a live power-demand reading.` : 'The workbook has no monthly energy value for this item.'),
        analysisCard('trend', 'What the graph says', null, trend + targetText),
        analysisCard('action', 'Action that can be taken', null, CATEGORY_ACTIONS[d.category] || CATEGORY_ACTIONS.other),
        analysisCard('cost', 'Current energy cost', 'Not calculable', 'The source has no machine-level electricity tariff. Recorded initiative investment and cost saving are shown below when available.')),
      initiativeHost);
  }

  function drawDetail() {
    destroyCharts(detailBox);
    clear(detailBox);
    const s = siteOf();
    if (!state.family) { detailBox.appendChild(embedded ? h('p', { class: 'fam-sub' }, 'Select a machine family above') : empty('This site has no machine families in the workbook.')); return; }
    const fam = s.families.find(f => f.id === state.family);
    const box = h('div');
    detailBox.appendChild(box);
    load(box, api.get(`/production/sites/${encodeURIComponent(s.code)}/families/${encodeURIComponent(state.family)}`), d => {
      const labels = d.months.map(monthLabel);
      const eU = (d.energy && d.energy.unit) || (fam && fam.energyUnit) || '';
      const pU = d.production && d.production.unit;
      const rU = d.energyPerUnit && d.energyPerUnit.unit;
      const t = d.totals || {};
      const out = [];
      out.push(h('div', { class: 'section-head' }, h('h2', null, `${d.name} at ${s.plantName} (${s.code})`),
        h('p', { class: 'small' }, `${fam ? fam.categoryLabel : d.category} · monthly, ${monthLabel(d.months[0])} to ${monthLabel(d.months[d.months.length - 1])} (FY25-26). Gaps are months the sheet leaves blank.`)));

      out.push(machineAnalysis(d, s, fam));

      out.push(h('div', { class: 'grid kpis' },
        kpiTile('Energy, FY25-26 total', t.energy, eU, 'Sum of the monthly values.', 0),
        kpiTile('Energy in months with production', t.energyWhereProductionKnown, eU, 'Only months where production is filled and above 0.', 0),
        kpiTile('Production, FY25-26 total', t.production, pU, 'Sum of the monthly values.', 0),
        kpiTile('Energy per unit, FY25-26', t.energyPerUnit, rU, 'Ratio of the totals over months where both are known.', 3)));
      if (t.basis) out.push(h('p', { class: 'fam-sub' }, 'Basis of totals: ' + t.basis));

      const eCard = chartCard(`Energy per month (${eU})`, `${d.name}, FY25-26`);
      const pCard = chartCard(`Production per month (${pU || 'unit not given'})`, `${d.name}, FY25-26`);
      const rCard = chartCard(`Energy per production unit (${rU || 'unit not given'})`, `${d.name}, monthly, from the sheet ratio row`);
      out.push(h('div', { class: 'grid cols-2' }, eCard.el, pCard.el), h('div', { class: 'fam-block' }, rCard.el));

      defer(() => {
        if (!allNull(d.energy.values)) seriesChart(eCard.body, labels, [{ label: 'Energy', data: d.energy.values, type: 'bar', unit: eU }], { type: 'bar', yTitle: eU, height: 'short' });
        else eCard.body.appendChild(empty('No energy values in the sheet for this family.'));
        if (d.production && !allNull(d.production.values)) {
          seriesChart(pCard.body, labels, [{ label: 'Production', data: d.production.values, type: 'bar', tone: 'info', unit: pU }], { type: 'bar', yTitle: pU, height: 'short' });
        } else pCard.body.appendChild(empty('No production values in the sheet for this family.'));

        const epu = d.energyPerUnit || {};
        const ratio = !allNull(epu.sheetValues) ? epu.sheetValues : epu.computedValues;
        const tg = d.target;
        const series = [];
        if (ratio && !allNull(ratio)) series.push({ label: 'Actual (sheet)', data: ratio, type: 'line', unit: rU, tone: 'brand', smooth: false });
        const tgVals = tg && tg.values ? d.months.map(m => (isNil(tg.values[m]) ? null : tg.values[m])) : null;
        if (series.length && tgVals && tg.verifiedAgainstSheet === true && !allNull(tgVals)) {
          series.push({ label: 'Target (deck)', data: tgVals, type: 'line', dashed: true, points: false, tone: 'bad', unit: tg.unit || rU, smooth: false });
        }
        if (series.length) seriesChart(rCard.body, labels, series, { type: 'line', yTitle: rU, height: 'short', digits: 3, legend: true });
        else rCard.body.appendChild(empty('Energy per unit cannot be shown: the sheet has no usable production values for this family (the ratio is #DIV/0! in every month).'));
      });

      const tg = d.target;
      if (!tg) {
        out.push(h('p', { class: 'fam-sub' }, 'No target for this family: targets exist only in the Sustainability deck, and only for families with a Target vs Actual chart (H01, TLMAL, H07).'));
      } else {
        if (tg.verifiedAgainstSheet !== true) {
          out.push(h('div', { class: 'fam-caution', role: 'note' }, h('strong', null, 'Target unverified. '),
            "The deck's actual line does not reproduce the sheet's ratio, so the unit of this target is unclear and it is not drawn against the sheet values. The deck's own numbers are listed below, unconverted." +
            (tg.deckAxisTitle ? ` Deck axis title: ${tg.deckAxisTitle}.` : '')));
          const months = d.months.filter(m => !isNil(tg.values && tg.values[m]));
          out.push(h('details', null, h('summary', null, 'Deck target values (not comparable to the sheet ratio)'),
            h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
              h('thead', null, h('tr', null, h('th', null, 'Month'), h('th', { class: 'num' }, 'Deck value (unit unclear)'))),
              h('tbody', null, months.map(m => h('tr', null, h('td', null, monthLabel(m)), h('td', { class: 'num' }, fmt(tg.values[m], 3)))))))));
        }
        out.push(h('p', { class: 'fam-sub' }, `Target source: ${tg.source || 'Sustainability deck'}${tg.unit ? ` · target unit: ${tg.unit}` : ''}`));
        if ((tg.notes || []).length) out.push(notes(tg.notes));
      }

      const n = [...(d.notes || []), ...(d.extraRows || [])];
      out.push(n.length ? notes(n) : h('p', { class: 'fam-sub' }, 'The API reports no notes or gaps for this family.'));
      return out;
    });
  }

  // ------------------------------------------------------------ site compare
  function drawCompare() {
    destroyCharts(compareBox);
    clear(compareBox);
    const s = siteOf();
    const chartHost = h('div');
    compareBox.append(
      h('div', { class: 'section-head' }, h('h2', null, `Compare families within ${s.plantName} (${s.code})`),
        h('p', { class: 'small' }, 'All families of this site by month. Energy is stacked so the largest user is visible (a family reported in another energy unit is drawn as a line on the right axis); production and energy per unit are in a different unit for each family, so those are separate lines.')),
      h('div', { class: 'fam-toolbar' }, btnGroup(METRICS, state.metric, m => { state.metric = m; syncUrl(); drawCompare(); }, 'Metric for the site comparison')),
      chartHost);
    load(chartHost, api.get(`/production/sites/${encodeURIComponent(s.code)}/compare`, { metric: state.metric }), d => {
      const shown = d.series.filter(x => !allNull(x.values));
      const hidden = d.series.filter(x => allNull(x.values));
      const units = uniq(shown.map(x => x.unit));
      const metricLabel = METRICS.find(m => m.id === state.metric).label;
      if (!shown.length) return empty(`No ${metricLabel.toLowerCase()} values in the sheet for this site.`);
      // Energy: stack the families that share the main unit (kWh); any family in another unit (e.g. MWh) goes on its own right-hand axis.
      const main = units.map(u => [u, shown.filter(x => x.unit === u).length]).sort((x, y) => y[1] - x[1])[0][0];
      const stacked = state.metric === 'energy';
      const other = stacked ? shown.filter(x => x.unit !== main) : [];
      const box = h('div');
      const wrap = h('div', null,
        h('p', { class: 'fam-sub' }, `${metricLabel} per month, FY25-26 (${units.length === 1 ? units[0] : stacked ? main + (other.length ? '; ' + other.map(x => x.name + ' in ' + x.unit + ', right axis').join('; ') : '') : 'units differ by family, see tooltip'}).`), box);
      defer(() => seriesChart(box, d.months.map(monthLabel), shown.map((x, i) => {
        const side = stacked && x.unit !== main;
        return {
          label: side ? `${x.name} (${x.unit}, right axis)` : x.name, data: x.values, type: stacked && !side ? 'bar' : 'line', unit: x.unit,
          stack: stacked && !side ? 'e' : undefined, axis: side ? 'right' : undefined,
          dashed: (!stacked && i >= 8) || side, smooth: false, points: !stacked && shown.length > 6 ? false : undefined
        };
      }), { type: stacked ? 'bar' : 'line', stacked, yTitle: stacked ? main : (units.length === 1 ? units[0] : undefined), y1Title: other.length ? other[0].unit : undefined, height: 'tall', legend: true, digits: state.metric === 'energyPerUnit' ? 3 : undefined }));
      const extra = [];
      if (hidden.length) extra.push(`No ${metricLabel.toLowerCase()} values in the sheet for: ${hidden.map(x => x.name).join(', ')}.`);
      return [wrap, notes([...(d.notes || []), ...extra])];
    });
  }

  // ------------------------------------------------------------ category compare
  function drawCategory() {
    destroyCharts(catBox);
    clear(catBox);
    const host = h('div');
    catBox.appendChild(host);
    load(host, api.get('/production/categories'), cats => {
      const list = Array.isArray(cats) ? cats : (cats.categories || []);
      if (!list.length) return empty('The API lists no machine categories.');
      if (!state.cat || !list.some(c => c.category === state.cat)) {
        const fam = siteOf().families.find(f => f.id === state.family);
        state.cat = fam && list.some(c => c.category === fam.category) ? fam.category : list[0].category;
        syncUrl();
      }
      const chartHost = h('div');
      const draw = () => {
        destroyCharts(chartHost);
        clear(chartHost);
        load(chartHost, api.get(`/production/categories/${encodeURIComponent(state.cat)}/compare`, { metric: state.cmetric }), d => {
          const shown = d.series.filter(x => !allNull(x.values));
          const hidden = d.series.filter(x => allNull(x.values));
          const units = uniq(shown.map(x => x.unit));
          const metricLabel = METRICS.find(m => m.id === state.cmetric).label;
          if (!shown.length) return empty(`No ${metricLabel.toLowerCase()} values in the sheet for ${d.categoryLabel || state.cat}.`);
          const box = h('div');
          const wrap = h('div', null,
            h('p', { class: 'fam-sub' }, `${metricLabel}, ${d.categoryLabel || state.cat}: one line per family and site, monthly FY25-26 (${units.length === 1 ? units[0] : 'units differ: ' + units.join(' · ')}).`), box);
          const caution = units.length > 1
            ? h('div', { class: 'fam-caution', role: 'note' }, h('strong', null, 'Units differ. '), 'Compare the shape over time, not the size of the values.') : null;
          defer(() => seriesChart(box, d.months.map(monthLabel), shown.map((x, i) => ({
            label: `${x.site} · ${x.name}`, data: x.values, type: 'line', unit: x.unit, smooth: false, dashed: i >= 8, points: shown.length > 6 ? false : undefined
          })), { type: 'line', yTitle: units.length === 1 ? units[0] : undefined, height: 'tall', legend: true, digits: state.cmetric === 'energy' ? undefined : 3 }));
          const extra = hidden.length ? [`No ${metricLabel.toLowerCase()} values in the sheet for: ${hidden.map(x => `${x.site} ${x.name}`).join(', ')}.`] : [];
          return [wrap, caution, notes([...(d.notes || []), ...extra])];
        });
      };
      const sel = select(list.map(c => ({ id: c.category, label: `${c.label} (${(c.sites || []).length} site${(c.sites || []).length === 1 ? '' : 's'})` })),
        state.cat, v => { state.cat = v; syncUrl(); draw(); }, { id: 'fam-cat-select' });
      draw();
      return [
        h('div', { class: 'section-head' }, h('h2', null, 'Compare one category across sites'),
          h('p', { class: 'small' }, 'For example all paint booths: every site with a family in the category, from every sheet of the workbook.')),
        h('div', { class: 'fam-toolbar' }, h('label', { for: 'fam-cat-select' }, 'Category'), sel,
          btnGroup(METRICS, state.cmetric, m => { state.cmetric = m; syncUrl(); draw(); }, 'Metric for the category comparison')),
        chartHost];
    });
  }

  // ------------------------------------------------------------ about the data
  function drawAbout() {
    const host = h('div');
    aboutBox.appendChild(host);
    load(host, api.get('/production/sources'), d => {
      const plants = Object.entries((d.entrySheet && d.entrySheet.plants) || {});
      const cmp = d.tsalComparison || {};
      return h('details', null, h('summary', null, 'About this data: which source is more complete'),
        h('p', { class: 'fam-sub' }, h('strong', null, 'More complete: '), `${d.moreCompleteSource}. ${d.reason || ''}`),
        plants.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
          h('thead', null, h('tr', null, h('th', null, `Plant in the entry sheet "${d.entrySheet.name}"`), h('th', { class: 'num' }, 'Families listed'), h('th', { class: 'num' }, 'Filled value cells'))),
          h('tbody', null, plants.map(([k, v]) => h('tr', null, h('td', null, k), h('td', { class: 'num' }, fmt(v.familiesListed)), h('td', { class: 'num' }, fmt(v.filledValueCells))))))) : null,
        !isNil(cmp.kwhCellsCompared) ? h('p', { class: 'fam-sub' }, `TSAL cross-check: ${fmt(cmp.kwhCellsCompared)} kWh cells compared between the two sources, ${fmt(cmp.kwhMismatches)} mismatches.`) : null,
        notes(sitesData.notes));
    });
  }

  function drawAll() {
    const s = siteOf();
    if (embedded) {
      const sm = siteOf();
      if (sm.mappingConfidence !== 'high') siteBox.appendChild(h('div', { class: 'fam-site-meta' },
        badge('Site mapping: ' + sm.mappingConfidence, 'warn'),
        h('span', { title: sm.mappingEvidence || '' }, `${sm.plantName} (${sm.code}): ${sm.mappingEvidence || 'no evidence recorded'}`)));
      if (state.family && !sm.families.some(f => f.id === state.family)) state.family = null;
      drawDetail(); drawCompare(); drawCategory();
      return;
    }
    if (!s.families.some(f => f.id === state.family)) state.family = s.families.length ? s.families[0].id : null;
    syncUrl();
    drawSites();
    drawOverview();
    drawDetail();
    drawCompare();
    drawCategory();
  }

  drawAll();
  drawAbout();
}
