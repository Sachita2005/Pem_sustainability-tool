// Metrics › Energy › Energy Consumption, for the plant selected in the frame.
//
//   KPI cards            energy per month, CO2 per month, FY energy, deviation, CO2 for the FY, revenue
//   Machine Families     one card per family (energy and CO2 per day) + category share
//   Family detail        the selected family: production, energy per unit, target
//   Trends               monthly, source mix, yearly, diesel, emissions, data tables
//
// Every figure is read from the workbooks through the API; where the workbook
// has no value the card shows a dash. Derived figures say how they are derived.

import { api } from '../../api.js';
import { destroyCharts, donutChart } from '../../charts.js';
import { clear, empty, errorBox, fmt, h, loading, modal, monthLabel, notes, select } from '../../ui.js';

const LAKH = 100000;
let children = [];
let machineModalClose = null;

export function destroy() {
  if (machineModalClose) machineModalClose();
  machineModalClose = null;
  children.forEach(m => { if (m && typeof m.destroy === 'function') { try { m.destroy(); } catch (err) { console.error(err); } } });
  children = [];
}

function card(value, unit, label, sub, tone) {
  return h('div', { class: 'mcard' },
    h('div', { class: 'mcard-value' + (tone ? ' ' + tone : '') }, value, unit ? h('small', null, unit) : null),
    h('div', { class: 'mcard-label' }, label),
    sub ? h('div', { class: 'mcard-sub' }, sub) : null);
}

function daysIn(month) {
  const [y, m] = String(month).split('-').map(Number);
  return new Date(y, m, 0).getDate();
}

export async function render(container, ctx) {
  destroy();
  const plant = ctx.plant;
  const state = {
    fy: ctx.params.fy || null,
    machineFamily: ctx.params.machineFamily || null,
    family: ctx.params.family || null
  };

  const kpiBox = h('div');
  const familyBox = h('div');
  const detailBox = h('div', { class: 'section' });
  const trendBox = h('div', { class: 'section' });
  container.append(kpiBox, familyBox, detailBox, trendBox);

  let consumption = null, families = null;
  try {
    [consumption, families] = await Promise.all([import('../energy/consumption.js'), import('../energy/families.js')]);
    children = [consumption, families];
  } catch (err) {
    console.error(err);
    container.appendChild(errorBox(err));
    return;
  }

  // ---- Financial years this plant has, to choose the default and fill the picker.
  let years = [];
  try {
    const yearly = await api.get('/energy/yearly', { plant });
    const block = (yearly.plants || []).find(p => p.plant === plant);
    years = block ? block.years.filter(y => y.total) : [];
  } catch (err) { /* the KPI block reports its own error below */ }
  if (!state.fy || !years.some(y => y.fy === state.fy)) {
    // Latest complete year, so the cards describe a full year; otherwise the latest year there is.
    const complete = years.filter(y => y.monthsReported === 12);
    state.fy = (complete[complete.length - 1] || years[years.length - 1] || {}).fy || null;
  }

  // ---------------------------------------------------------------- KPI cards
  async function drawKpis() {
    clear(kpiBox).appendChild(loading());
    try {
      const d = await consumption.plantHeadline(plant, state.fy);
      const months = d.monthsReported || 0;
      const period = d.partial && d.toMonth ? `${months} months, to ${monthLabel(d.toMonth)}` : null;
      // The workbook reports Scope 1 in tonnes and Scope 2 in kg; both are shown in tonnes here.
      const scope2T = d.scope2Gross === null || d.scope2Gross === undefined ? null
        : (/kg/i.test(d.scope2GrossUnit || '') ? d.scope2Gross / 1000 : d.scope2Gross);
      const co2T = scope2T === null && (d.scope1 === null || d.scope1 === undefined) ? null : (scope2T || 0) + (d.scope1 || 0);
      const change = d.changePctVsPrevSamePeriod;

      const picker = years.length > 1
        ? select(years.map(y => ({ id: y.fy, label: y.fy + (y.monthsReported < 12 ? ` (${y.monthsReported} months)` : '') })).reverse(), state.fy,
            fy => { state.fy = fy; ctx.setParams && ctx.setParams({ fy }); drawKpis(); drawTrends(); }, { 'aria-label': 'Financial year' })
        : null;

      clear(kpiBox).append(
        h('div', { class: 'grid kpi5' },
          card(d.avgMonthlyKwh === null ? '—' : fmt(d.avgMonthlyKwh / LAKH, 2), 'L Units/mo', 'Energy consumption / month', period),
          card(co2T === null || !months ? '—' : fmt(co2T / months), 't CO₂/mo', 'CO₂ emission / month', co2T === null ? 'No emission figure in the workbook for this year' : null, 'amber'),
          card(d.totalKwh === null ? '—' : fmt(d.totalKwh / LAKH, 2), 'L Units', `FY of energy (${d.fy || '—'})`, period),
          card(change === null || change === undefined ? '—' : (change > 0 ? '+' : '') + fmt(change, 1) + '%', null, 'Energy deviation & CO₂ cause',
            d.comparedWith ? `Against the same ${d.comparedMonths || months} months of ${d.comparedWith}` : 'No earlier year to compare with',
            change > 0 ? 'red' : (change < 0 ? 'green' : null)),
          card(co2T === null ? '—' : fmt(co2T), 't CO₂/yr', 'CO₂ FY for year', co2T === null ? null : 'Scope 1 + Scope 2, as the workbook computes them', 'amber'),
          card(d.revenueCr === null || d.revenueCr === undefined ? '—' : '₹ ' + fmt(d.revenueCr, 2), 'Cr',
            d.revenuePerMonthCr === null || d.revenuePerMonthCr === undefined ? 'Revenue' : `Revenue (₹${fmt(d.revenuePerMonthCr, 2)} Cr/mo)`)),
        h('div', { class: 'form-row', style: { marginTop: '10px', justifyContent: 'space-between' } },
          h('span', { class: 'small muted' }, '1 L Unit = 1,00,000 kWh. Source: Sustainability Calculation workbook.'),
          picker ? h('label', { class: 'field', style: { flexDirection: 'row', alignItems: 'center', gap: '8px' } }, 'Financial year', picker) : null));
    } catch (err) {
      console.error(err);
      clear(kpiBox).appendChild(errorBox(err));
    }
  }

  // ---------------------------------------------------------------- machine families
  async function drawFamilies() {
    clear(familyBox).appendChild(loading());
    let d;
    try {
      d = await families.plantFamilies(plant);
    } catch (err) {
      console.error(err);
      clear(familyBox).appendChild(errorBox(err));
      return;
    }
    const list = (d && d.families) || [];
    let factor = null;
    try {
      const e = await api.get('/energy/emissions', { plant });
      const row = (Array.isArray(e.factors) ? e.factors : []).find(f => /purchased electricity from eb/i.test(f.appliesTo || ''));
      factor = row && typeof row.factor === 'number' ? row.factor : null;
    } catch (err) { /* cards then show kWh only */ }

    const selectedMachine = list.find(f => f.id === state.family);
    if (selectedMachine) state.machineFamily = selectedMachine.category;
    const grouped = new Map();
    list.forEach(machine => {
      const key = machine.category || 'other';
      if (!grouped.has(key)) grouped.set(key, { id: key, label: machine.categoryLabel || key, machines: [] });
      grouped.get(key).machines.push(machine);
    });
    const groups = [...grouped.values()];
    if (!groups.some(group => group.id === state.machineFamily)) state.machineFamily = null;
    const toKwh = (value, unit) => {
      if (value === null || value === undefined) return null;
      return /mwh/i.test(unit || '') ? value * 1000 : value;
    };

    const reporting = list.filter(f => f.latestEnergy !== null && f.latestEnergy !== undefined && f.latestMonth === d.latestMonth).length;
    const head = h('div', { class: 'family-head' },
      h('div', null,
        h('h2', null, 'Machine Families'),
        h('p', { class: 'small muted' }, 'Select a family to see its machines, then select a machine for analysis.')),
      h('div', { class: 'family-active' },
        h('div', { class: 'on' }, list.length ? 'Reporting' : 'No data'),
        h('div', null, h('strong', null, reporting), ' / ' + list.length),
        h('div', null, d && d.latestMonth ? `Machines in ${groups.length} families with energy in ${monthLabel(d.latestMonth)}` : `${groups.length} machine families`)));

    if (!list.length) {
      clear(familyBox).append(head, empty('The production workbook has no machine-family sheet for this plant.'), notes(d && d.notes));
      return;
    }

    function openMachinePopup(group) {
      if (machineModalClose) machineModalClose();
      const machineGrid = h('div', { class: 'machine-pick-grid machine-popup-grid' });
      group.machines.forEach(machine => {
        const has = machine.latestEnergy !== null && machine.latestEnergy !== undefined;
        const energyKwh = toKwh(machine.latestEnergy, machine.energyUnit);
        const days = machine.daysInLatestMonth || (machine.latestMonth ? daysIn(machine.latestMonth) : null);
        const perDay = energyKwh !== null && days ? energyKwh / days : null;
        machineGrid.appendChild(h('button', {
          type: 'button', class: 'machine-pick' + (machine.id === state.family ? ' active' : ''),
          'aria-pressed': String(machine.id === state.family),
          onClick: () => {
            state.machineFamily = group.id;
            state.family = machine.id;
            ctx.setParams && ctx.setParams({ machineFamily: group.id, family: machine.id });
            if (machineModalClose) machineModalClose();
            drawFamilies();
            drawDetail(true);
          }
        },
        h('span', { class: 'machine-pick-name' }, machine.name),
        h('span', { class: 'machine-pick-value' }, has ? `${fmt(energyKwh)} kWh in ${monthLabel(machine.latestMonth)}` : 'No energy value recorded'),
        h('span', { class: 'machine-pick-meta' }, perDay === null ? 'Daily average unavailable' : `${fmt(perDay)} kWh/day`, machine.hasTarget ? ' · Target available' : ''),
        h('span', { class: 'machine-popup-action' }, 'Open graph and analysis →')));
      });
      const body = h('div', { class: 'machine-popup' },
        h('p', { class: 'machine-popup-intro' }, `All ${group.machines.length} machines / monitored equipment items in ${group.label}. Select one to open only its graph and related analysis.`),
        machineGrid);
      machineModalClose = modal(`${group.label} — All machines`, body, { onClose: () => { machineModalClose = null; } });
    }

    const grid = h('div', { class: 'family-grid' });
    groups.forEach(group => {
      const current = group.machines.filter(f => f.latestMonth === d.latestMonth && f.latestEnergy !== null && f.latestEnergy !== undefined);
      const latestKwh = current.reduce((sum, f) => sum + (toKwh(f.latestEnergy, f.energyUnit) || 0), 0);
      const days = d.latestMonth ? daysIn(d.latestMonth) : null;
      const kwhPerDay = current.length && days ? latestKwh / days : null;
      grid.appendChild(h('button', {
        type: 'button', class: 'fcard family-group-card' + (group.id === state.machineFamily ? ' active' : ''),
        'aria-pressed': String(group.id === state.machineFamily), dataset: { group: group.id },
        onClick: () => {
          state.machineFamily = group.id;
          state.family = null;
          ctx.setParams && ctx.setParams({ machineFamily: group.id, family: null });
          grid.querySelectorAll('.family-group-card').forEach(card => {
            const on = card.dataset.group === group.id;
            card.classList.toggle('active', on);
            card.setAttribute('aria-pressed', String(on));
          });
          drawDetail(false);
          openMachinePopup(group);
        }
      },
      h('div', { class: 'family-group-title' }, h('h3', null, group.label), h('span', { class: 'count' }, group.machines.length)),
      h('span', { class: 'state' + (current.length ? '' : ' off'), title: current.length ? 'Energy reported' : 'No energy reported in the latest plant month' }),
      h('div', { class: 'fcard-row' }, h('span', null, 'Machines reporting'), h('strong', null, `${current.length} / ${group.machines.length}`)),
      h('div', { class: 'fcard-row' }, h('span', null, 'Family energy per day'), h('strong', null, kwhPerDay === null ? '—' : fmt(kwhPerDay) + ' kWh')),
      h('div', { class: 'fcard-row' }, h('span', null, 'CO₂ per day'),
        h('strong', { class: 'co2' }, kwhPerDay === null || factor === null ? '—' : fmt(kwhPerDay * factor) + ' kg')),
      h('div', { class: 'family-machine-preview' }, group.machines.slice(0, 3).map(f => f.name).join(' · '), group.machines.length > 3 ? ` · +${group.machines.length - 3} more` : ''),
      h('div', { class: 'family-open' }, 'View all machines in popup →')));
    });

    const slices = groups.map(group => ({
      label: group.label,
      value: group.machines
        .filter(f => f.latestEnergy > 0 && f.latestMonth === d.latestMonth)
        .reduce((sum, f) => sum + toKwh(f.latestEnergy, f.energyUnit), 0)
    })).filter(x => x.value > 0);
    const total = slices.reduce((sum, x) => sum + x.value, 0);
    const pie = h('div', { class: 'mcard' },
      h('h3', { style: { fontSize: '16px' } }, `${ctx.plantLabel || plant} — Family Share`),
      h('p', { class: 'muted', style: { marginTop: '4px' } }, slices.length ? `Total: ${fmt(total)} kWh in ${monthLabel(d.latestMonth)}` : 'No energy values to share out'));
    const pieBody = h('div');
    pie.appendChild(pieBody);

    const selectedGroup = groups.find(group => group.id === state.machineFamily);
    clear(familyBox).append(head, h('div', { class: 'family-layout' }, grid, pie));
    familyBox.appendChild(h('p', { class: 'small muted', style: { marginTop: '10px' } },
      'Energy per day = the latest month’s kWh ÷ days in that month. ' +
      (factor === null ? 'CO₂ per day is not shown because the workbook’s grid factor could not be read.' : `CO₂ per day = kWh × ${factor} kg CO₂/kWh (the workbook’s grid factor).`) +
      ' Source: Power Consumption wrt Production Units workbook. Some workbook rows describe a line, bay or production family rather than a separately identified physical asset; no unrecorded machine names are invented.' +
      (d.mappingConfidence && d.mappingConfidence !== 'high' ? ` Sheet-to-plant mapping confidence: ${d.mappingConfidence}.` : '')));
    const dataNotes = notes(d.notes);
    if (dataNotes) familyBox.appendChild(dataNotes);
    if (slices.length) donutChart(pieBody, slices, { unit: 'kWh', legend: 'bottom', height: 'tall' });
    if (selectedGroup && !state.family) openMachinePopup(selectedGroup);
  }

  // ---------------------------------------------------------------- family detail (families.js, embedded)
  async function drawDetail(scroll) {
    destroyCharts(detailBox);
    clear(detailBox);
    if (!state.family) {
      detailBox.append(h('div', { class: 'machine-analysis-empty' },
        h('h2', null, 'Machine analysis'),
        h('p', null, state.machineFamily ? 'Select a machine from the family popup. Click the family card to reopen it.' : 'Select a machine family above, then choose a machine from the popup.')));
      return;
    }
    const holder = h('div');
    detailBox.append(h('div', { class: 'section-head' }, h('h2', null, 'Machine analysis'),
      h('p', { class: 'small' }, 'Consumption graph, interpretation, initiatives, recommended action and recorded cost')), holder);
    try {
      await families.render(holder, { embedded: true, plant, family: state.family, params: {} });
      if (scroll) detailBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      console.error(err);
      clear(holder).appendChild(errorBox(err));
    }
  }

  // ---------------------------------------------------------------- trends (consumption.js, embedded)
  async function drawTrends() {
    destroyCharts(trendBox);
    clear(trendBox);
    const holder = h('div');
    trendBox.append(h('div', { class: 'section-head' }, h('h2', null, 'Energy trends'),
      h('p', { class: 'small' }, 'Monthly and yearly consumption, sources, diesel and emissions')), holder);
    try {
      await consumption.render(holder, { embedded: true, plant, params: { fy: state.fy } });
    } catch (err) {
      console.error(err);
      clear(holder).appendChild(errorBox(err));
    }
  }

  drawKpis();
  await drawFamilies();
  drawDetail(false);
  drawTrends();
}
