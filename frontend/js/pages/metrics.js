// Metrics page — plant first.
//
//   [ plant bar ]                         one plant always selected
//   [ ENERGY | WATER | AIR MONITORING | SCRAP GENERATION ]
//   [ sub-tabs for the metric ]           [+ Enter Monthly Data]
//   [ the selected view ]
//
// Each view is its own module and receives the selected plant; this frame
// owns the selection and keeps it in the address (#/metrics?plant=..&metric=..&view=..)
// so any view can be linked to and survives a reload.

import { api } from '../api.js';
import { destroyCharts } from '../charts.js';
import { clear, errorBox, h, loading, toast } from '../ui.js';

// Order and labels as on the plant bar.
const PLANTS = [
  { id: 'TLMAL', label: 'TLMAL-Hyd' },
  { id: 'TSAL', label: 'TSAL-Hyd' },
  { id: 'TASL-HYD', label: 'TASL-Hyd' },
  { id: 'TBAL', label: 'TBAL-Hyd' },
  { id: 'TASL-NGP', label: 'TASL-Nagpur' },
  { id: 'TASL-BLR', label: 'TASL-Bangalore' },
  { id: 'TASL-MCA', label: 'TASL-MCA' },
  { id: 'TCOE', label: 'TCOE-HYD' }
];

const METRICS = [
  { id: 'energy', label: 'Energy', badge: null },          // badge = machine families at the plant
  { id: 'water', label: 'Water', badge: 'KL' },
  { id: 'air', label: 'Air Monitoring', badge: 'AAQ' },
  { id: 'scrap', label: 'Scrap Generation', badge: 'MT' }
];

const ENERGY_VIEWS = [
  { id: 'consumption', icon: '⚡', label: 'Energy Consumption' },
  { id: 'diesel', icon: '⛽', label: 'Diesel (KL)', param: 'diesel' },
  { id: 'revenue', icon: '₹', label: 'Revenue', param: 'revenue' },
  { id: 'manpower', icon: '👷', label: 'Manpower', param: 'manpower' },
  { id: 'afforestation', icon: '🌳', label: 'Afforestation', param: 'afforestation' },
  { id: 'tbal', icon: '❄', label: 'HVAC & Live Meters', only: 'TBAL' }   // TBAL has the meter server and HVAC model
];

let active = null;

export function destroy() {
  if (active && typeof active.destroy === 'function') {
    try { active.destroy(); } catch (err) { console.error(err); }
  }
  active = null;
}

export async function render(root, ctx) {
  const state = {
    plant: PLANTS.some(p => p.id === ctx.params.plant) ? ctx.params.plant : PLANTS[0].id,
    metric: METRICS.some(m => m.id === ctx.params.metric) ? ctx.params.metric : 'energy',
    view: ctx.params.view || 'consumption',
    extra: {}            // view-owned parameters (fy, family, …) kept in the address
  };
  Object.entries(ctx.params).forEach(([k, v]) => { if (!['plant', 'metric', 'view'].includes(k)) state.extra[k] = v; });

  const familyCounts = {};
  const plantBar = h('div', { class: 'plant-bar bleed', role: 'tablist', 'aria-label': 'Plant' });
  const metricBar = h('div', { class: 'metric-bar bleed', role: 'tablist', 'aria-label': 'Metric' });
  const subBar = h('div', { class: 'sub-bar bleed' });
  const body = h('div');
  root.append(plantBar, metricBar, subBar, body);
  let token = 0;

  function energyViews() {
    return ENERGY_VIEWS.filter(v => !v.only || v.only === state.plant);
  }

  function writeAddress() {
    const params = { plant: state.plant, metric: state.metric };
    if (state.metric === 'energy') params.view = state.view;
    Object.assign(params, state.extra);
    history.replaceState(null, '', '#/metrics?' + new URLSearchParams(params).toString());
  }

  function drawPlantBar() {
    clear(plantBar);
    PLANTS.forEach(p => {
      const count = familyCounts[p.id];
      plantBar.appendChild(h('button', {
        type: 'button', role: 'tab', class: 'plant-tab' + (p.id === state.plant ? ' active' : ''),
        title: count === undefined ? p.label : `${p.label}: ${count} machine families in the production workbook`,
        onClick: () => { if (p.id !== state.plant) { state.plant = p.id; state.extra = {}; show(); } }
      }, p.label, count === undefined ? null : h('span', { class: 'count' }, count)));
    });
    const on = plantBar.querySelector('.active');
    if (on) plantBar.scrollTo({ left: on.offsetLeft - 40 });
  }

  function drawMetricBar() {
    clear(metricBar);
    METRICS.forEach(m => {
      const badge = m.id === 'energy' ? familyCounts[state.plant] : m.badge;
      metricBar.appendChild(h('button', {
        type: 'button', role: 'tab', class: 'metric-tab' + (m.id === state.metric ? ' active' : ''),
        onClick: () => { if (m.id !== state.metric) { state.metric = m.id; state.extra = {}; show(); } }
      }, m.label, badge === undefined || badge === null ? null : h('span', { class: 'count' }, badge)));
    });
  }

  function drawSubBar() {
    clear(subBar);
    subBar.style.display = state.metric === 'energy' ? '' : 'none';
    if (state.metric !== 'energy') return;
    // Keep all six energy tabs visible. The TBAL-only view stays disabled at
    // other plants, so the strip never changes shape or drops a lone tab.
    const views = ENERGY_VIEWS;
    const tabList = h('div', {
      class: 'sub-tab-list', role: 'tablist', 'aria-label': 'Energy views',
      style: { '--energy-tab-count': views.length }
    });
    views.forEach(v => {
      const unavailable = !!v.only && v.only !== state.plant;
      tabList.appendChild(h('button', {
        type: 'button', class: 'sub-tab' + (v.id === state.view ? ' active' : '') + (unavailable ? ' unavailable' : ''),
        role: 'tab', 'aria-selected': String(v.id === state.view), 'aria-disabled': String(unavailable),
        disabled: unavailable,
        title: unavailable ? `${v.label} is available only for TBAL-Hyd` : null,
        onClick: () => { if (!unavailable && v.id !== state.view) { state.view = v.id; state.extra = {}; show(); } }
      }, h('span', { 'aria-hidden': 'true' }, v.icon), v.label));
    });
    subBar.append(tabList, h('div', { class: 'sub-bar-tools' },
      h('button', { type: 'button', class: 'btn enter', onClick: enterData }, '+ Enter Monthly Data')));
  }

  async function enterData() {
    try {
      const entry = await import('./energy/entry.js');
      const current = ENERGY_VIEWS.find(v => v.id === state.view);
      entry.openEntryDialog({
        plant: state.plant,
        param: (current && current.param) || 'diesel',
        onSaved: () => { api.clearCache('/manual'); if (current && current.param) show(); }
      });
    } catch (err) {
      console.error(err);
      toast('The data-entry form could not be opened: ' + err.message, 'bad');
    }
  }

  // Which module draws the current selection, and what it is told.
  function target() {
    const base = {
      plant: state.plant,
      plantLabel: (PLANTS.find(p => p.id === state.plant) || {}).label,
      embedded: true,
      params: { ...state.extra },
      navigate: ctx.navigate,
      setParams: obj => {
        Object.entries(obj || {}).forEach(([k, v]) => {
          if (v === null || v === undefined || v === '') delete state.extra[k]; else state.extra[k] = String(v);
        });
        writeAddress();
      }
    };
    if (state.metric === 'water') return { path: './metrics/water.js', ctx: base };
    if (state.metric === 'air') return { path: './metrics/air.js', ctx: base };
    if (state.metric === 'scrap') return { path: './metrics/scrap.js', ctx: base };
    const view = energyViews().find(v => v.id === state.view) || ENERGY_VIEWS[0];
    state.view = view.id;
    if (view.id === 'consumption') return { path: './metrics/energy.js', ctx: base };
    if (view.id === 'tbal') return { path: './energy/tbal.js', ctx: base };
    return { path: './energy/entry.js', ctx: { ...base, param: view.param } };
  }

  async function show() {
    const mine = ++token;
    destroy();
    destroyCharts(body);
    const t = target();          // may correct state.view (e.g. leaving TBAL while on its extra tab)
    drawPlantBar();
    drawMetricBar();
    drawSubBar();
    writeAddress();
    clear(body).appendChild(loading());
    try {
      const module = await import(t.path);
      if (mine !== token) return;
      clear(body);
      active = module;
      await module.render(body, t.ctx);
    } catch (err) {
      if (mine !== token) return;
      console.error(err);
      clear(body).appendChild(errorBox(err));
    }
  }

  await show();

  // The badge on each plant is the number of machine families the production
  // workbook lists for it — a real count, filled in once the data arrives.
  api.get('/production/sites').then(data => {
    (data.sites || []).forEach(s => { if (s.plant) familyCounts[s.plant] = (familyCounts[s.plant] || 0) + (s.familyCount || (s.families || []).length); });
    drawPlantBar();
    drawMetricBar();
  }).catch(() => { /* badges are optional */ });
}
