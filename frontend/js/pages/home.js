// Home: the company at a glance, then one card per plant.
// Every block loads on its own, so a slow or failing source never blanks the page.

import { api } from '../api.js';
import { seriesChart, destroyCharts } from '../charts.js';
import { badge, clear, empty, errorBox, fmt, fmtPct, h, load, loading, modal, monthLabel, notes } from '../ui.js';

const LAKH = 100000;

// Order used on the plant bar of the Metrics page.
const ORDER = ['TLMAL', 'TSAL', 'TASL-HYD', 'TBAL', 'TASL-NGP', 'TASL-BLR', 'TASL-MCA', 'TCOE'];
const SOURCES = [
  { key: 'grid', label: 'Govt Grid', css: '--c1' },
  { key: 'solar', label: 'Solar', css: '--c2' },
  { key: 'purchased', label: 'Purchased RE', css: '--c6' },
  { key: 'dg', label: 'Generator', css: '--c3' }
];

function ensureCss() {
  if (document.getElementById('home-css')) return;
  document.head.appendChild(h('link', { id: 'home-css', rel: 'stylesheet', href: '/static/css/home.css' }));
}

function mcard(value, unit, label, sub, tone) {
  return h('div', { class: 'mcard' },
    h('div', { class: 'mcard-value' + (tone ? ' ' + tone : '') }, value, unit ? h('small', null, unit) : null),
    h('div', { class: 'mcard-label' }, label),
    sub ? h('div', { class: 'mcard-sub' }, sub) : null);
}

function panel(title, pill, body, tone) {
  return h('section', { class: 'home-panel ' + (tone || '') },
    h('div', { class: 'home-panel-head' }, h('h2', null, title), pill ? h('span', { class: 'badge ' + (tone === 'water' ? 'brand' : 'good') }, pill) : null),
    body);
}

function tonnes(kg) { return kg === null || kg === undefined ? null : kg / 1000; }

function insightSection(title, body) {
  return h('section', { class: 'home-insight-section' }, h('h4', null, title), body);
}

function openPlantInsight(plant, name, month, ctx) {
  const body = h('div', { class: 'home-insight' }, loading());
  const close = modal(`${name} · Sustainability insight`, body, { onClose: () => destroyCharts(body) });

  Promise.all([
    api.get('/ai/recommendations', { plant }),
    api.get('/initiatives', { plant })
  ]).then(([analysis, initiativeData]) => {
    const findings = (analysis.findings || []);
    const initiatives = (initiativeData.items || []).filter(item =>
      item.phase === 'planned' || (item.plantStatuses || []).some(status =>
        status.plant === plant && ['open', 'under_study'].includes(status.status)));
    const latestFindings = findings.filter(f => (f.months || []).includes(month));
    const shownFindings = latestFindings.length ? latestFindings : findings;
    clear(body);
    body.appendChild(h('p', { class: 'small muted' },
      `Latest reported month: ${monthLabel(month)}. The charts show the available monthly history; values are not estimates unless explicitly marked.`));

    if (!shownFindings.length) {
      body.appendChild(h('div', { class: 'home-insight-ok' },
        h('strong', null, 'No abnormal trend found.'),
        h('p', null, latestFindings.length ? 'No significant issue was flagged for this plant.' : `The AI analysis found no abnormal months for ${name} in the available history.`)));
    } else {
      shownFindings.forEach(f => {
        const chartData = f.chart || {};
        const labels = chartData.months || [];
        const rec = f.recommendation || {};
        const projection = f.projection || {};
        const chartBox = h('div', { class: 'home-insight-chart' });
        const problem = h('div', { class: `home-insight-problem ${f.severity === 'red' ? 'bad' : 'warn'}` },
          h('strong', null, 'Something went wrong'),
          h('p', null, f.evidence || `${f.metricLabel || f.metric} was outside its normal pattern in ${(f.months || []).map(monthLabel).join(', ')}.`));
        const facts = h('div', { class: 'home-insight-facts' },
          h('div', null, h('b', null, 'Actual in flagged months'), h('span', null, `${fmt(f.observed, Math.abs(f.observed) < 100 ? 2 : 0)} ${f.unit}`)),
          h('div', null, h('b', null, 'Expected'), h('span', null, `${fmt(f.expected, Math.abs(f.expected) < 100 ? 2 : 0)} ${f.unit}`)),
          h('div', null, h('b', null, 'Potential reduction'), h('span', null, f.severity === 'check' ? 'Not estimated — verify the entry' : `${fmt(rec.avoidable ?? projection.avoidable, Math.abs(Number(rec.avoidable ?? projection.avoidable)) < 100 ? 2 : 0)} ${f.unit}`)));
        const actions = (rec.actions || []).length
          ? h('ol', null, rec.actions.map(action => h('li', null, action)))
          : h('p', { class: 'muted' }, 'No recommended action was returned for this finding.');
        const related = (rec.related_initiatives || []);
        const initiativeOptions = related.length
          ? h('ul', null, related.map(item => h('li', null, `${item.description} — implemented at ${(item.implementedAt || []).map(x => x.plant).join(', ') || 'another plant'}; recorded savings: ${(item.implementedAt || []).map(x => [x.energySavingKwh !== null && x.energySavingKwh !== undefined ? `${fmt(x.energySavingKwh)} kWh` : null, x.energyCostSavingCr !== null && x.energyCostSavingCr !== undefined ? `${fmt(x.energyCostSavingCr, 3)} Cr` : null].filter(Boolean).join(' · ')).filter(Boolean).join(', ') || 'not recorded'}`)))
          : h('p', { class: 'small muted' }, 'No proven cross-plant initiative is linked to this finding yet. The recommended checks above can be captured as a proposed initiative.');
        const card = h('article', { class: 'home-insight-finding' },
          h('h3', null, `${f.metricLabel || f.metric} · ${(f.months || []).map(monthLabel).join(', ')}`),
          problem,
          chartBox,
          facts,
          insightSection('Recommended actions', actions),
          insightSection('Initiative ideas', initiativeOptions));
        body.appendChild(card);

        if (labels.length) {
          const actual = chartData.actual || [];
          const series = [
            { label: 'Actual', data: actual, tone: 'brand', unit: f.unit, smooth: false },
            { label: 'Expected', data: chartData.expected || [], tone: 'muted', unit: f.unit, dashed: true, points: false },
            { label: 'Upper normal range', data: chartData.upper || [], tone: 'good', unit: f.unit, dashed: true, points: false }
          ];
          const after = projection.after;
          if (Array.isArray(after) && after.some((value, i) => value !== actual[i])) {
            series.push({ label: 'If action succeeds (illustrative)', data: after, tone: 'good', unit: f.unit, dashed: true, points: false });
          }
          seriesChart(chartBox, labels.map(monthLabel), series, { yTitle: `${f.unit} per month`, height: 'short' });
        } else {
          chartBox.appendChild(empty('No monthly series is available to plot.'));
        }
      });
    }

    const hasWater = shownFindings.some(f => f.metric === 'water');
    const hasEnergy = shownFindings.some(f => f.metric !== 'water');
    const matching = initiatives.filter(item =>
      (item.domain === 'water' && hasWater) || (item.domain !== 'water' && hasEnergy)).slice(0, 5);
    body.appendChild(insightSection('Planned initiative cost and expected result',
      matching.length
        ? h('div', { class: 'home-insight-initiatives' }, matching.map(item => h('article', null,
          h('strong', null, item.description),
          h('span', null, `Investment: ${item.investmentRs === null || item.investmentRs === undefined ? 'not recorded' : `₹${fmt(item.investmentRs)}`}`),
          h('span', null, `Expected saving: ${item.expectedSavingPerYear === null || item.expectedSavingPerYear === undefined ? 'not recorded' : fmt(item.expectedSavingPerYear) + ' per year (unit as recorded in source)'}`),
          h('span', null, `ROI: ${item.roiYears === null || item.roiYears === undefined ? 'not recorded' : fmt(item.roiYears, 1) + ' years'}`))))
        : h('p', { class: 'small muted' }, 'No matching planned initiative has cost and expected-saving details recorded in the source files. No cost or financial saving has been invented.')));

    const link = h('button', { type: 'button', class: 'btn small brand', onClick: () => { close(); ctx.navigate('initiatives', { view: 'upcoming', plant }); } }, 'Open AI initiatives');
    body.appendChild(h('div', { class: 'home-insight-footer' }, link));
  }).catch(err => {
    clear(body).appendChild(errorBox(err));
  });
}

export async function render(root, ctx) {
  ensureCss();
  const go = (page, params) => () => ctx.navigate(page, params);

  const banner = h('div', { class: 'home-banner' },
    h('h1', null, 'Plant Overview'),
    h('div', { class: 'home-banner-stat' }, h('span', null, 'Total plants', h('br'), 'monitored'), h('strong', { id: 'home-plant-count' }, '—')));
  const energyBox = h('div');
  const waterBox = h('div');
  const statusBox = h('div', { class: 'grid cols-4', style: { marginTop: '16px' } });
  const plantsBox = h('div');
  const notesBox = h('div');
  root.append(banner, h('div', { class: 'grid cols-2' }, energyBox, waterBox), statusBox,
    h('div', { class: 'home-section-bar' }, h('h2', null, 'Plant-Wise Sustainability'),
      h('span', { class: 'badge brand' }, 'Each card shows the plant’s latest reported month')),
    plantsBox, notesBox);

  const energy = api.get('/energy/overview');
  const water = api.get('/water/overview');
  const collected = [];

  // ---------------------------------------------------------------- company: energy
  load(energyBox, energy, d => {
    const t = d.tadl || {};
    const ytd = t.fyToDate || {};
    const full = t.latestFullYear || {};
    const co2 = full.scope2Gross === undefined && full.scope1 === undefined ? null : (tonnes(full.scope2Gross) || 0) + (full.scope1 || 0);
    const intensity = full.totalUnits && full.revenue ? (full.totalUnits / LAKH) / full.revenue : null;
    document.getElementById('home-plant-count').textContent = String((d.plants || []).length);
    collected.push(...(d.notes || []));
    return panel('⚡ Energy Consumption', d.latestMonth ? 'Till ' + monthLabel(d.latestMonth) : null,
      h('div', { class: 'grid home-kpis' },
        mcard(fmt((ytd.total || 0) / LAKH, 2), 'L Units', `${ytd.fy || ''} so far`, ytd.monthsReported ? `${ytd.monthsReported} months reported` : null),
        mcard(fmt((full.totalUnits || 0) / LAKH, 2), 'L Units', `${full.fy || ''} total energy`, intensity === null ? null : `${fmt(intensity, 2)} L Units per ₹ Cr revenue`),
        mcard(co2 === null ? '—' : fmt(co2), 't CO₂', `${full.fy || ''} total CO₂`,
          full.scope1 === undefined ? null : `Scope 1: ${fmt(full.scope1)} t · Scope 2: ${fmt(tonnes(full.scope2Gross))} t`, 'amber'),
        mcard(fmtPct((ytd.renewableShare || 0) * 100), null, 'Renewable share', `${ytd.fy || ''} so far`, 'green')));
  });

  // ---------------------------------------------------------------- company: water
  load(waterBox, water, d => {
    const t = d.tadl || {};
    const ytd = t.fyToDate || {};
    const meters = d.meters || {};
    collected.push(...(d.notes || []));
    return panel('💧 Water Consumption', t.latestMonth ? 'Till ' + monthLabel(t.latestMonth) : null,
      h('div', { class: 'grid home-kpis' },
        mcard(fmt(ytd.freshWater), 'KL', `Fresh water, ${t.fy || ''} so far`, ytd.monthsReported ? `${ytd.monthsReported} months reported` : null),
        mcard(fmt(ytd.totalReused), 'KL', 'Treated water reused', 'Sewage and process water'),
        mcard(fmtPct(ytd.reusePct), null, 'Reuse of fresh water', 'Reused ÷ fresh water', 'green'),
        mcard(fmt(meters.available), meters.toBeInstalled ? `+ ${fmt(meters.toBeInstalled)} to install` : null, 'Water flow meters', 'Across all plants')),
      'water');
  });

  // ---------------------------------------------------------------- status tiles
  const tile = (title, promise, build, onClick) => {
    const body = h('div');
    const el = h('button', { type: 'button', class: 'home-tile', onClick }, h('h3', null, title), body);
    statusBox.appendChild(el);
    load(body, promise, build);
  };

  tile('Initiatives', api.get('/initiatives/summary'), d => {
    const s = d.byStatus || {};
    return [h('div', { class: 'home-tile-value' }, fmt(d.totalInitiatives), h('small', null, 'records')),
      h('div', { class: 'home-tile-row' }, badge(`${fmt(s.implemented)} implemented`, 'good'), badge(`${fmt(s.in_progress)} in progress`, 'info'), badge(`${fmt((s.open || 0) + (s.under_study || 0))} open`, 'warn')),
      h('p', { class: 'small muted' }, `${fmt((d.byPhase || {}).planned)} planned for the coming period`)];
  }, go('initiatives'));

  tile('Recommendations', api.get('/ai/recommendations'), d => {
    const c = d.counts || {};
    return [h('div', { class: 'home-tile-value' }, fmt((c.red || 0) + (c.amber || 0)), h('small', null, 'abnormal trends')),
      h('div', { class: 'home-tile-row' }, badge(`${fmt(c.red)} red zone`, 'bad'), badge(`${fmt(c.amber)} amber`, 'warn'), badge(`${fmt(c.check)} entries to check`, 'info')),
      h('p', { class: 'small muted' }, (d.provider && d.provider.name === 'local-model') ? 'Built-in model · last 12 reported months' : 'Internal AI server')];
  }, go('initiatives', { view: 'upcoming' }));

  tile('Scrap & air', Promise.all([api.get('/scrap/overview'), api.get('/air/overview')]), ([s, a]) => {
    const byCat = ((s.company || {}).byCategory || []);
    const weight = byCat.reduce((sum, c) => sum + (c.weightKg || 0), 0);
    return [h('div', { class: 'home-tile-value' }, byCat.length ? fmt(weight) : '—', h('small', null, `kg waste, ${s.latestFy || ''}`)),
      h('div', { class: 'home-tile-row' },
        badge(`${fmt(s.latestFyMonthsReported)} months reported`, 'brand'),
        badge(`${fmt(a.readingsAboveLimit)} air readings above limit`, a.readingsAboveLimit ? 'bad' : 'good')),
      h('p', { class: 'small muted' }, 'Hazardous and e-waste only; air limits exist for TBAL only')];
  }, go('metrics', { metric: 'scrap' }));

  tile('Sustainability deck', api.get('/deck', null, { fresh: true }), d => {
    if (!d.available) return empty('Deck not found in the source folder.');
    const approved = (d.approval || {}).status === 'approved';
    return [h('div', { class: 'home-tile-value' }, fmt(d.slideCount), h('small', null, 'slides')),
      h('div', { class: 'home-tile-row' }, approved ? badge('✓ Approved' + (d.approval.approvedBy ? ' by ' + d.approval.approvedBy : ''), 'good') : badge(d.hasEditsFromOriginal ? 'Draft — edited' : 'Draft', 'warn')),
      h('p', { class: 'small muted' }, 'Open the Report tab to edit, download or approve')];
  }, go('report'));

  // ---------------------------------------------------------------- plant cards
  load(plantsBox, Promise.all([energy, water.catch(() => null)]), ([e, w]) => {
    const byPlantWater = {};
    ((w && w.plants) || []).forEach(p => { byPlantWater[p.entity] = p; });
    const plants = (e.plants || []).slice().sort((a, b) => ORDER.indexOf(a.plant) - ORDER.indexOf(b.plant));
    if (!plants.length) return empty('No plant energy data in the source workbooks.');

    return h('div', { class: 'home-plants' }, plants.map(p => {
      const latest = p.latest || {};
      const total = latest.total || 0;
      const parts = SOURCES.map(s => ({ ...s, value: latest[s.key] || 0 })).filter(s => s.value > 0);
      const wp = byPlantWater[p.plant];
      const wl = (wp && wp.latest) || null;

      return h('article', { class: 'home-plant' },
        h('header', null,
          h('button', { type: 'button', class: 'home-plant-name', onClick: go('metrics', { plant: p.plant }) }, p.name),
          p.latestReportedMonth ? h('span', { class: 'home-plant-latest' },
            h('button', { type: 'button', class: 'home-info-btn', title: `Latest month: ${monthLabel(p.latestReportedMonth)}. Open sustainability insight for ${p.name}`, 'aria-label': `Open sustainability insight for ${p.name}, ${monthLabel(p.latestReportedMonth)}`, onClick: () => openPlantInsight(p.plant, p.name, p.latestReportedMonth, ctx) }, 'i')) : null),

        h('button', { type: 'button', class: 'home-plant-block', onClick: go('metrics', { plant: p.plant, metric: 'energy' }) },
          h('div', { class: 'home-plant-title energy' }, '⚡ Energy Consumption'),
          h('div', { class: 'home-plant-figure' }, total ? fmt(total / LAKH, 2) : '—', h('small', null, 'L Units in the month')),
          total ? h('div', { class: 'home-mixbar', role: 'img', 'aria-label': parts.map(s => `${s.label} ${fmt((s.value / total) * 100, 1)}%`).join(', ') },
            parts.map(s => h('i', { style: { width: (s.value / total) * 100 + '%', background: `var(${s.css})` } }))) : null,
          h('div', { class: 'home-mixlist' }, parts.map(s => h('div', null,
            h('span', null, h('i', { style: { background: `var(${s.css})` } }), s.label),
            h('strong', null, fmtPct((s.value / total) * 100), h('small', null, ` (${fmt(s.value / LAKH, 2)} L)`)))))),

        h('button', { type: 'button', class: 'home-plant-block', onClick: go('metrics', { plant: p.plant, metric: 'water' }) },
          h('div', { class: 'home-plant-title water' }, '💧 Water Consumption', wp && wp.latestMonth && wp.latestMonth !== p.latestReportedMonth ? h('small', null, ' ' + monthLabel(wp.latestMonth)) : null),
          wl
            ? [h('div', { class: 'home-plant-figure' }, fmt(wl.freshWater), h('small', null, 'KL fresh water in the month')),
               h('div', { class: 'home-mixlist' },
                 [['Municipal', wl.municipal], ['Borewell', wl.borewell], ['Tanker', wl.tanker]].filter(x => x[1] > 0).map(x => h('div', null, h('span', null, x[0]), h('strong', null, fmt(x[1]), h('small', null, ' KL')))),
                 h('div', null, h('span', null, 'Treated water reused'), h('strong', null, fmt(wl.totalReused), h('small', null, ` KL (${fmtPct(wl.reusePct)})`))))]
            : h('p', { class: 'small muted' }, 'No water data reported for this plant.')));
    }));
  }).then(() => {
    const all = [...new Set(collected)];
    if (all.length) {
      clear(notesBox).appendChild(h('details', { class: 'home-notes' }, h('summary', null, `About this data (${all.length} notes from the source workbooks)`), notes(all)));
    }
  });
}
