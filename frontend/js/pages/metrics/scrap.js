// Metrics > SCRAP GENERATION (one plant, chosen by the Metrics frame).
// Every figure comes from /api/scrap/*. All quantities are from the "Sustainability (Only to Enter)"
// workbook; weights are put on kg by the API's stated formula (T x 1000), litres are never added to weights.

import { api } from '../../api.js';
import { badge, change, clear, empty, fmt, h, load, monthLabel, notes, section, select, table } from '../../ui.js';
import { destroyCharts, seriesChart } from '../../charts.js';

let host = null;
let token = 0;

function ensureCss() {
  if (document.getElementById('metrics-scrap-css')) return;
  document.head.appendChild(h('link', { id: 'metrics-scrap-css', rel: 'stylesheet', href: '/static/css/metrics-scrap.css' }));
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
const SOURCE_NAME = 'Sustainability (Only to Enter) workbook, sheet "Scrap generation details"';

function mcard(label, value, unit, sub, tone) {
  return h('div', { class: 'mcard' },
    h('div', { class: 'mcard-value' + (tone ? ' ' + tone : '') }, value, unit ? h('small', null, unit) : null),
    h('div', { class: 'mcard-label' }, label),
    sub ? h('div', { class: 'mcard-sub' }, sub) : null);
}

/** unit text as entered -> 'kg' factor, 'L' volume or null (unknown: never charted or added) */
function unitKind(unit) {
  const u = String(unit || '').trim().toLowerCase();
  if (u === 'kg') return { kind: 'w', factor: 1 };
  if (u === 't') return { kind: 'w', factor: 1000 };
  if (['l', 'liter', 'liters', 'litre', 'litres'].includes(u)) return { kind: 'v', factor: 1 };
  return { kind: 'other', factor: null };
}

function fyMonths(fy) {
  const m = /^FY(\d{2})-(\d{2})$/.exec(fy || '');
  if (!m) return [];
  const y = 2000 + Number(m[1]);
  const out = [];
  for (let i = 0; i < 12; i++) {
    const mo = ((3 + i) % 12) + 1;
    out.push(`${mo >= 4 ? y : y + 1}-${String(mo).padStart(2, '0')}`);
  }
  return out;
}

const prevFy = fy => {
  const m = /^FY(\d{2})-(\d{2})$/.exec(fy || '');
  return m ? `FY${String(Number(m[1]) - 1).padStart(2, '0')}-${String(Number(m[2]) - 1).padStart(2, '0')}` : null;
};
const prevMonth = ym => { const m = /^(\d{4})-(\d{2})$/.exec(ym); return m ? `${Number(m[1]) - 1}-${m[2]}` : null; };

const fyOf = (it, fy) => (it.fy && it.fy[fy]) || null;
const itemTotal = (it, fy) => { const r = fyOf(it, fy); return r && !isNil(r.total) ? r.total : null; };
const itemHasData = (it, fy) => { const r = fyOf(it, fy); return !!r && ((r.total || 0) > 0 || (r.monthsReported || 0) > 0); };
const itemNonZero = (it, fy) => (itemTotal(it, fy) || 0) > 0;
const itemLabel = it => (it.category === 'E-Waste' ? it.wasteType + ' (e-waste)' : it.wasteType);

function num(v, d) {
  if (isNil(v)) return DASH;
  return fmt(v, d === undefined ? (Number.isInteger(Number(v)) ? 0 : 2) : d);
}

export async function render(container, ctx) {
  ensureCss();
  if (host) destroyCharts(host);
  const my = ++token;
  host = container;
  const plant = ctx.plant;
  const wrap = h('div', { class: 'ms-wrap' });
  clear(container).appendChild(wrap);
  const alive = () => my === token;

  const top = h('div');
  wrap.appendChild(top);

  let q, totals;
  const p = Promise.all([api.get('/scrap/quantities', { plant }), api.get('/scrap/totals')]);
  await load(top, p, ([qq, tt]) => { q = qq; totals = tt; return null; });
  if (!alive()) return;

  // sections that do not depend on quantities still load below
  const body = h('div');
  const rest = h('div');
  add(wrap, body, rest);

  const initBox = h('div');
  const dispBox = h('div');
  const tgtBox = h('div');
  const items = q ? (q.items || []) : [];
  if (q) drawQuantities(body, q, totals); else if (!top.querySelector('.error')) top.appendChild(empty('No scrap data returned.'));
  add(rest, tgtBox, dispBox, initBox);
  drawTargets(tgtBox, items);
  drawDisposal(dispBox);
  drawInitiative(initBox);

  // ================================================================== quantities
  function drawQuantities(root, qd, td) {
    const sourceBanner = () => {
      const others = [...new Set(items.map(i => i.source).filter(s => s && s !== 'entry'))];
      return h('div', { class: 'ms-source', role: 'note' },
        h('strong', null, 'Source of every scrap quantity on this page: '),
        others.length
          ? `${SOURCE_NAME}, and rows marked ${others.join(', ')}.`
          : `${SOURCE_NAME} (each row is marked source "entry"). The Sustainability Calculation workbook holds no scrap quantities. Only hazardous waste and e-waste are recorded; there is no non-hazardous (metal, plastic, wood) quantity in the source.`);
    };

    const withData = fy => items.some(i => itemHasData(i, fy));
    const fys = (qd.financialYears || []).filter(fy => items.some(i => itemNonZero(i, fy)));
    const plantRows = (td.plantYear || []).filter(r => r.plant === plant);
    const monthRows = (td.plantMonth || []).filter(r => r.plant === plant);

    if (!fys.length) {
      const names = [...new Set(items.map(i => i.wasteType))];
      add(root, 
        sourceBanner(),
        h('div', { class: 'ms-block' }, empty(items.length
          ? `No scrap quantity has been entered for ${plant} in any financial year. The source sheet lists ${items.length} item row${items.length === 1 ? '' : 's'} for this plant (${names.slice(0, 6).join(', ')}${names.length > 6 ? ', …' : ''}) but every value is 0 or blank, so there is nothing to total or chart.`
          : `The source has no scrap rows for ${plant}.`)),
        notes(qd.notes));
      return;
    }

    const initialFy = ctx.params && fys.includes(ctx.params.fy) ? ctx.params.fy : fys[fys.length - 1];
    const state = { fy: initialFy };
    const kpiBox = h('div');
    const monthBox = h('div', { class: 'ms-block' });
    const yearBox = h('div', { class: 'ms-block' });
    const tableBox = h('div', { class: 'ms-block' });
    const sheetBox = h('div', { class: 'ms-block' });

    const fySel = select(fys.map(f => ({ id: f, label: f })), state.fy, v => {
      state.fy = v;
      if (ctx.setParams) ctx.setParams({ fy: v });
      drawFy();
    }, { id: 'ms-fy', 'aria-label': 'Financial year' });
    const fyPicker = h('div', { class: 'ms-toolbar' }, h('label', { for: 'ms-fy' }, 'Financial year'), fySel,
      h('span', { class: 'ms-hint' }, `Years with a quantity for ${plant}: ${fys.join(', ')}`));

    add(root, sourceBanner(), fyPicker, kpiBox, monthBox, yearBox, tableBox, sheetBox, notes([...new Set([...(qd.notes || []), ...(td.notes || [])])]));

    const catRows = (rows, cat) => rows.filter(r => r.category === cat);

    function latestReported(cat) {
      const rows = catRows(monthRows, cat).filter(r => !isNil(r.weightKg) || (r.volumeL || 0) > 0).sort((a, b) => a.month.localeCompare(b.month));
      return rows.length ? rows[rows.length - 1] : null;
    }

    function fyTotals(cat, fy) {
      const r = plantRows.find(x => x.fy === fy && x.category === cat);
      return r && (!isNil(r.weightKg) || !isNil(r.volumeL)) ? r : null;
    }

    function drawFy() {
      drawKpis(); drawMonthly(); drawYearly(); drawItems(); drawSheet();
    }

    function monthSub(r) {
      return r && (r.volumeL || 0) > 0 ? `plus ${fmt(r.volumeL, 0)} L of liquid, kept separate` : null;
    }

    function drawKpis() {
      clear(kpiBox);
      const fy = state.fy;
      const pf = prevFy(fy);
      const hz = latestReported('Hazardous');
      const ew = latestReported('E-Waste');
      const hzFy = fyTotals('Hazardous', fy);
      const ewFy = fyTotals('E-Waste', fy);

      // change against the same months of the previous FY, only months where both exist
      const delta = cat => {
        const cur = catRows(monthRows, cat).filter(r => r.fy === fy && !isNil(r.weightKg));
        const prv = new Map(catRows(monthRows, cat).filter(r => !isNil(r.weightKg)).map(r => [r.month, r.weightKg]));
        const pairs = cur.filter(r => prv.has(prevMonth(r.month)));
        if (!pairs.length) return { value: null, months: 0, pf };
        const a = pairs.reduce((s, r) => s + r.weightKg, 0);
        const b = pairs.reduce((s, r) => s + prv.get(prevMonth(r.month)), 0);
        return { value: change(a, b), months: pairs.length, a, b, pf };
      };
      const dlt = (cat, tot) => {
        if (!tot || isNil(tot.weightKg)) return null;
        const d = delta(cat);
        if (d.value === null) return h('div', { class: 'mcard-sub' }, `No ${pf} figure for the same months to compare.`);
        const up = d.value >= 0;
        return h('div', { class: 'mcard-sub ms-delta' },
          h('span', { class: up ? 'ms-up' : 'ms-down' }, (up ? '▲ ' : '▼ ') + fmt(Math.abs(d.value), 1) + '%'),
          ` vs same ${d.months} month${d.months === 1 ? '' : 's'} of ${pf} (${fmt(d.a, 0)} kg vs ${fmt(d.b, 0)} kg)`);
      };
      const fyCard = (label, cat, tot) => {
        if (!tot) return mcard(label, DASH, null, `Nothing entered for ${cat === 'E-Waste' ? 'e-waste' : 'hazardous waste'} in ${fy}.`);
        const card = mcard(label, isNil(tot.weightKg) ? DASH : fmt(tot.weightKg, 0), isNil(tot.weightKg) ? null : 'kg',
          `${fy}, ${tot.monthsReported} of 12 months reported${(tot.volumeL || 0) > 0 ? ` · plus ${fmt(tot.volumeL, 0)} L liquid, kept separate` : ''}`);
        const d = dlt(cat, tot);
        if (d) card.appendChild(d);
        return card;
      };
      const streams = items.filter(i => itemNonZero(i, fy));
      const nHz = streams.filter(i => i.category === 'Hazardous').length;
      const nEw = streams.filter(i => i.category === 'E-Waste').length;
      const units = [...new Set(streams.map(i => i.unit))];
      kpiBox.appendChild(h('div', { class: 'grid kpi5' },
        mcard('Hazardous waste, latest month', hz ? (isNil(hz.weightKg) ? DASH : fmt(hz.weightKg, 0)) : DASH, hz && !isNil(hz.weightKg) ? 'kg' : null,
          hz ? [monthLabel(hz.month), monthSub(hz) ? ' · ' + monthSub(hz) : ''].join('') : 'No month with a hazardous quantity.'),
        mcard('E-waste, latest month', ew ? (isNil(ew.weightKg) ? DASH : fmt(ew.weightKg, 0)) : DASH, ew && !isNil(ew.weightKg) ? 'kg' : null,
          ew ? monthLabel(ew.month) : 'No e-waste quantity entered for this plant in any month.'),
        fyCard('Hazardous waste, FY to date', 'Hazardous', hzFy),
        fyCard('E-waste, FY to date', 'E-Waste', ewFy),
        mcard('Waste streams reported', String(streams.length), null,
          `${fy}: ${nHz} hazardous, ${nEw} e-waste · units as entered: ${units.join(', ') || DASH}`)));
    }

    // ---------------------------------------------------------- monthly stacked
    function drawMonthly() {
      destroyCharts(monthBox);
      clear(monthBox);
      const fy = state.fy;
      const months = fyMonths(fy);
      const labels = months.map(monthLabel);
      const live = items.filter(i => itemNonZero(i, fy));
      const reported = months.filter(m => live.some(i => fyOf(i, fy).months && !isNil(fyOf(i, fy).months[m])));
      const wItems = live.filter(i => unitKind(i.unit).kind === 'w');
      const vItems = live.filter(i => unitKind(i.unit).kind === 'v');
      const oItems = live.filter(i => unitKind(i.unit).kind === 'other');
      const series = (list, kind) => list.map(i => {
        const f = unitKind(i.unit).factor;
        const mo = (fyOf(i, fy) && fyOf(i, fy).months) || {};
        return { label: itemLabel(i), type: 'bar', stack: 's', unit: kind === 'w' ? 'kg' : 'L', data: months.map(m => (isNil(mo[m]) ? null : mo[m] * f)) };
      });
      const partial = reported.length < 12
        ? `${fy} is partial: ${reported.length} month${reported.length === 1 ? '' : 's'} reported (${reported.length ? monthLabel(reported[0]) + ' to ' + monthLabel(reported[reported.length - 1]) : 'none'}); empty months are gaps, not zero.` : `${fy}: all 12 months reported.`;
      monthBox.appendChild(h('div', { class: 'section-head' },
        h('h2', null, `Monthly quantity by waste item, ${fy}`),
        h('p', { class: 'small' }, `Stacked by item. Weights in kg (tonne rows converted at T × 1000). ${partial} Source: Only to Enter workbook.`)));
      const out = [];
      if (wItems.length) {
        const box = h('div');
        out.push(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, `Weight (kg), ${fy}`), h('p', null, `${wItems.length} item${wItems.length === 1 ? '' : 's'} entered in KG or T`))), box));
        defer(() => { if (alive()) seriesChart(box, labels, series(wItems, 'w'), { type: 'bar', stacked: true, yTitle: 'kg per month', legend: true, height: 'tall' }); });
      }
      if (vItems.length) {
        const box = h('div');
        out.push(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, `Liquid streams (litres), ${fy}`), h('p', null, 'Kept apart from the weights: litres are never added to kg'))), box));
        defer(() => { if (alive()) seriesChart(box, labels, series(vItems, 'v'), { type: 'bar', stacked: true, yTitle: 'litres per month', legend: true, height: 'short' }); });
      }
      if (oItems.length) out.push(notes([`Not charted (unit not recognised as weight or litres): ${oItems.map(i => `${i.wasteType} (${i.unit})`).join(', ')}.`]));
      if (!out.length) out.push(empty(`No monthly quantity in ${fy}.`));
      add(monthBox, ...out.map(o => (o.classList && o.classList.contains('card') ? h('div', { class: 'ms-gap' }, o) : o)));
    }

    // ---------------------------------------------------------- yearly trend
    function drawYearly() {
      destroyCharts(yearBox);
      clear(yearBox);
      const years = (qd.financialYears || []).filter(fy => plantRows.some(r => r.fy === fy && (!isNil(r.weightKg) || !isNil(r.volumeL))));
      const lab = years.map(fy => {
        const mr = Math.max(0, ...plantRows.filter(x => x.fy === fy).map(x => x.monthsReported || 0));
        return mr && mr < 12 ? [fy, `${mr} mo`] : fy;
      });
      const val = (fy, cat, k) => { const r = plantRows.find(x => x.fy === fy && x.category === cat); return r && !isNil(r[k]) ? r[k] : null; };
      const cats = [['Hazardous', 'Hazardous waste'], ['E-Waste', 'E-waste']];
      const kgSeries = cats.map(([c, l]) => ({ label: l, type: 'bar', stack: 'y', unit: 'kg', data: years.map(fy => val(fy, c, 'weightKg')) }))
        .filter(s => s.data.some(v => !isNil(v) && v > 0));
      const lSeries = cats.map(([c, l]) => ({ label: l, type: 'bar', stack: 'y', unit: 'L', data: years.map(fy => val(fy, c, 'volumeL')) }))
        .filter(s => s.data.some(v => !isNil(v) && v > 0));
      yearBox.appendChild(h('div', { class: 'section-head' }, h('h2', null, 'Yearly trend'),
        h('p', { class: 'small' }, `Financial-year totals for ${plant}, weights in kg. A second line under the year shows the months reported when a year is partial. Source: Only to Enter workbook.`)));
      if (!kgSeries.length && !lSeries.length) { yearBox.appendChild(empty('No yearly totals.')); return; }
      const grid = h('div', { class: lSeries.length ? 'grid cols-2' : '' });
      if (kgSeries.length) {
        const box = h('div');
        grid.appendChild(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Weight per financial year (kg)'), h('p', null, 'Sum of the months entered; KG and T rows put on kg'))), box));
        defer(() => { if (alive()) seriesChart(box, lab, kgSeries, { type: 'bar', stacked: true, yTitle: 'kg per year', legend: true, height: 'short' }); });
      }
      if (lSeries.length) {
        const box = h('div');
        grid.appendChild(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Liquid per financial year (litres)'), h('p', null, 'Litre rows only, never added to kg'))), box));
        defer(() => { if (alive()) seriesChart(box, lab, lSeries, { type: 'bar', stacked: true, yTitle: 'litres per year', legend: true, height: 'short' }); });
      }
      yearBox.appendChild(grid);
      const parts = years.map(fy => {
        const pr = plantRows.filter(r => r.fy === fy);
        const mr = Math.max(0, ...pr.map(r => r.monthsReported || 0));
        return mr < 12 ? `${fy}: ${mr} of 12 months` : null;
      }).filter(Boolean);
      yearBox.appendChild(h('p', { class: 'ms-hint' },
        `Years shown: ${years.join(', ')}.` + (parts.length ? ` Partial years: ${parts.join('; ')}.` : '') +
        ' Other years have no quantity entered for this plant.'));
    }

    // ---------------------------------------------------------- items table
    function drawItems() {
      clear(tableBox);
      const fy = state.fy, pf = prevFy(fy);
      const shown = items.filter(i => itemHasData(i, fy)).sort((a, b) => (itemTotal(b, fy) || 0) * (unitKind(b.unit).factor || 1) - (itemTotal(a, fy) || 0) * (unitKind(a.unit).factor || 1));
      const hidden = items.filter(i => !itemHasData(i, fy));
      const unitCell = (v, unit) => (isNil(v) ? DASH : [num(v, Math.abs(v) < 100 && !Number.isInteger(v) ? 3 : 0), h('span', { class: 'ms-unit' }, unit)]);
      const cols = [
        { key: 'wasteType', label: 'Waste item', render: r => itemLabel(r) },
        { key: 'category', label: 'Category', render: r => r.category },
        { key: 'unit', label: 'Unit as entered', render: r => r.unit },
        { key: 't', label: `${fy} total`, num: true, render: r => unitCell(itemTotal(r, fy), r.unit) },
        { key: 'kg', label: `${fy} in kg`, num: true, render: r => { const f = unitKind(r.unit); return f.kind === 'w' && !isNil(itemTotal(r, fy)) ? fmt(itemTotal(r, fy) * f.factor, 0) : (f.kind === 'v' ? 'litres: not in kg' : DASH); } },
        { key: 'months', label: 'Months entered', num: true, render: r => { const x = fyOf(r, fy); return x ? (x.basis === 'stated-total' && !x.monthsReported ? 'total only' : `${x.monthsReported} of 12`) : DASH; } },
        { key: 'p', label: `${pf || 'Previous FY'} total`, num: true, render: r => unitCell(itemTotal(r, pf), r.unit) },
        { key: 'src', label: 'Source', render: r => (r.source === 'entry' ? 'Only to Enter' : r.source) }
      ];
      add(tableBox, 
        h('div', { class: 'section-head' }, h('h2', null, `Waste items, ${fy}`),
          h('p', { class: 'small' }, 'Each item in the unit the sheet entered it in. A dash means nothing entered; 0 means zero was entered.')),
        table(cols, shown, { emptyText: `No item has a quantity in ${fy}.` }),
        hidden.length ? h('p', { class: 'ms-hint' }, `${hidden.length} further row${hidden.length === 1 ? '' : 's'} for ${plant} hold no value in ${fy}: ${hidden.map(i => i.wasteType).join(', ')}.`) : null);
    }

    // ---------------------------------------------------------- sheet's own totals
    function drawSheet() {
      clear(sheetBox);
      const rows = (td.sheetTotals || []).filter(r => r.plant === plant && fys.includes(r.fy)).sort((a, b) => a.fy.localeCompare(b.fy));
      sheetBox.appendChild(h('div', { class: 'section-head' }, h('h2', null, 'Sheet total, as stated'),
        h('p', { class: 'small' }, `For comparison only, not a headline figure. Years with a quantity for ${plant} only.`)));
      if (!rows.length) { sheetBox.appendChild(empty(`The sheet states no plant total for ${plant}.`)); return; }
      const mine = (fy, label) => { const cat = /e-?waste/i.test(label || '') ? 'E-Waste' : 'Hazardous'; const r = plantRows.find(x => x.fy === fy && x.category === cat); return r && !isNil(r.weightKg) ? fmt(r.weightKg, 0) + ' kg' : DASH; };
      add(sheetBox, 
        h('div', { class: 'ms-caution', role: 'note' }, h('strong', null, 'Why this is not used. '),
          "The sheet's own plant total adds rows entered in KG, T and litres, yet is labelled with a single unit, so it is not a consistent quantity. It is shown exactly as stated; the kg totals above add weights only."),
        table([
          { key: 'fy', label: 'FY' },
          { key: 'label', label: 'Sheet row' },
          { key: 'value', label: 'Sheet total, as stated', num: true, render: r => [num(r.value, 2), h('span', { class: 'ms-unit' }, r.unitLabel || '')] },
          { key: 'mine', label: 'Same category, weights only, summed on kg (this page)', num: true, render: r => mine(r.fy, r.label) }
        ], rows));
    }

    drawFy();
  }

  // ================================================================== targets
  function drawTargets(box, its) {
    load(box, api.get('/scrap/targets'), d => {
      const t = d.targets || [];
      const landfill = its.filter(i => /landfill/i.test(i.wasteType));
      if (!t.length) return section('Targets', 'Scrap target', empty('The source states no scrap target.'), notes(d.notes));
      const first = t[0];
      const fyList = t.map(x => x.financialYear).filter(Boolean);
      const card = h('div', { class: 'card ms-target' },
        h('div', { class: 'ms-target-row' },
          h('div', null,
            h('div', { class: 'ms-target-name' }, `${first.category.replace(' - ', ' – ')} = ${fmt(first.level, 0)}`),
            h('div', { class: 'ms-hint' }, `Stated in the Sustainability Calculation workbook, sheet "${(first.source && first.source.sheet) || 'Target Sheet'}"; the same row repeats for ${fyList[0]} to ${fyList[fyList.length - 1]}.`)),
          badge('Stated target', 'info')),
        h('dl', { class: 'ms-dl' },
          h('dt', null, 'Target year'), h('dd', null, String(first.targetYear)),
          h('dt', null, 'Level'), h('dd', null, fmt(first.level, 0)),
          h('dt', null, 'Reduction percentage stated'), h('dd', null, fmt(first.reductionPercentage, 0)),
          h('dt', null, 'Unit'), h('dd', null, isNil(first.unit) ? 'None stated in the source' : first.unit),
          h('dt', null, 'Baseline'), h('dd', null, isNil(d.baseline) ? 'None stated in the source' : JSON.stringify(d.baseline))),
        h('div', { class: 'ms-caution', role: 'note' }, h('strong', null, 'No target line is drawn. '),
          landfill.length
            ? 'Landfill rows exist for this plant but the target has no unit, so a line against them would not be meaningful.'
            : `The source holds no landfill quantity for ${plant} (or any plant): its scrap quantities are hazardous waste and e-waste only, so a landfill target line would sit against unrelated data. The charts above carry no target line.`));
      return [section('Scrap target', 'The only target stated in the source files', card), notes(d.notes)];
    });
  }

  // ================================================================== disposal
  function drawDisposal(box) {
    load(box, api.get('/scrap/disposal', { plant }), d => {
      const rows = d.rows || [];
      const stated = rows.filter(r => r.method).length;
      const out = [h('p', { class: 'ms-hint' }, `Source: Sustainability Calculation workbook, sheet "${d.sheet || 'Waste Sheet Disposal Method'}". A method is text only: the source holds no quantity by disposal method.`)];
      if (!rows.length) {
        out.push(empty(`The disposal-method sheet lists no waste streams for ${plant}.`));
      } else {
        const bm = (d.byMethod || []).filter(m => m.wastes);
        out.push(
          h('div', { class: 'ms-chips' }, bm.map(m => h('span', { class: 'badge' + (m.method === '(not stated)' ? ' warn' : '') }, `${m.method}: ${m.wastes}`))),
          table([
            { key: 'waste', label: 'Waste stream' },
            { key: 'wasteType', label: 'Type (as in the sheet)' },
            { key: 'method', label: 'Disposal method', render: r => (r.method ? r.method : badge('not stated in the sheet', 'warn')) }
          ], rows));
      }
      return [section(`Disposal methods at ${plant}`, `${rows.length} waste stream${rows.length === 1 ? '' : 's'}, ${stated} with a stated method`, out), notes(d.notes)];
    });
  }

  // ================================================================== digitalisation
  function drawInitiative(box) {
    load(box, api.get('/scrap/initiative'), d => {
      const brd = d.brd || {};
      const tr = brd.transcription || {};
      const mx = d.mendix || {};
      const out = [];
      const pdfLink = h('a', { class: 'btn primary', href: brd.pdfUrl || '/api/scrap/documents/brd.pdf', target: '_blank', rel: 'noopener' }, `Open the BRD PDF (${brd.pages || '?'} pages)`);

      // ---- BRD
      const pagesTr = (brd.pagesTranscribed || []);
      const pr = pagesTr.length ? `${pagesTr[0]}–${pagesTr[pagesTr.length - 1]}` : '';
      const caution = h('div', { class: 'ms-caution ms-caution-lg', role: 'note' },
        h('div', null, h('strong', null, `Read from the scanned BRD, pages ${pr || '—'} — verify against the PDF. `),
          `${tr.method || 'The BRD is a scan with no text layer; this content was transcribed by eye.'} Wording is as printed, typos included. Pages other than ${pr || 'those'} are not transcribed.`),
        pdfLink);
      const pg = p => h('span', { class: 'ms-pg' }, `BRD p.${p}`);
      const brdParts = [caution];

      if ((tr.objectives || []).length) {
        brdParts.push(h('div', { class: 'ms-sub' }, h('h3', null, 'Objective'),
          tr.objectives.map(o => h('p', null, o.text, ' ', pg(o.page), o.section ? h('span', { class: 'ms-pg' }, o.section) : null))));
      }
      const cp = tr.currentProcess;
      if (cp) {
        const streams = cp.wasteStreams || [];
        brdParts.push(h('div', { class: 'ms-sub' }, h('h3', null, 'Current process'),
          h('p', null, cp.statement, ' ', pg(cp.page)),
          h('div', { class: 'table-wrap' }, h('table', { class: 'table ms-matrix' },
            h('thead', null, h('tr', null, h('th', null, 'Process step'), streams.map(s => h('th', null, s)))),
            h('tbody', null, (cp.steps || []).map(st => {
              const r = st.responsible || {};
              const keys = Object.keys(r);
              const allKey = keys.find(k => /^all/i.test(k));
              return h('tr', null, h('td', null, st.process),
                allKey && keys.length === 1
                  ? h('td', { colspan: String(Math.max(1, streams.length)), class: 'ms-all' }, `${r[allKey]} (stated once for all streams)`)
                  : streams.map(s => h('td', null, isNil(r[s]) ? DASH : r[s])));
            }))))));
      }
      const tb = tr.toBeProcess;
      if (tb) {
        brdParts.push(h('div', { class: 'ms-sub' }, h('h3', null, 'To-be process'),
          h('p', null, tb.statement, ' ', pg(tb.page)),
          h('p', { class: 'ms-hint' }, `Workflow boxes as read from the flow chart, ${pg(tb.page_of_steps || tb.page).textContent}:`),
          h('ol', { class: 'ms-steps' }, (tb.flowSteps || []).map(s => h('li', null, s)))));
      }
      const rm = tr.responsibilityMatrix;
      if (rm) {
        brdParts.push(h('div', { class: 'ms-sub' }, h('h3', null, 'Responsibility matrix'),
          h('p', { class: 'ms-hint' }, `${rm.section || ''} (BRD p.${rm.page})`),
          table([
            { key: 'no', label: 'No.', num: true },
            { key: 'process', label: 'Process' },
            { key: 'fromTo', label: 'From – To' },
            { key: 'exec', label: 'Executing dept. (by type of waste)', render: r => h('ul', { class: 'ms-list' }, Object.entries(r.executingDept || {}).map(([k, v]) => h('li', null, k ? h('strong', null, k + ': ') : null, v))) },
            { key: 'authorization', label: 'Authorization' },
            { key: 'acknowledgement', label: 'Acknowledgement' },
            { key: 'page', label: 'Page', render: r => `BRD p.${r.page}` }
          ], rm.rows || [])));
      }
      const ms = tr.masters;
      if (ms) {
        brdParts.push(h('div', { class: 'ms-sub' }, h('h3', null, 'Masters required'),
          h('p', { class: 'ms-hint' }, `${ms.section || ''} (BRD p.${ms.page})`),
          h('ul', { class: 'ms-list' }, (ms.items || []).map(i => h('li', null, i))),
          ms.sampleHeader ? h('p', null, h('strong', null, 'Sample master: '), ms.sampleHeader) : null,
          (ms.sampleMasterColumns || []).length ? h('p', { class: 'ms-hint' }, 'Sample columns as printed: ' + ms.sampleMasterColumns.join(' | ')) : null,
          (ms.sampleMasterRows || []).length ? h('p', { class: 'ms-hint' }, 'Sample rows (cells as printed): ' + ms.sampleMasterRows.map(r => r.join(' · ')).join('  /  ')) : null));
      }
      if ((tr.notStated || []).length) brdParts.push(notes(tr.notStated));
      out.push(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Business Requirement Document (BRD)'),
        h('p', null, `${brd.file || ''} · ${brd.pages || '?'} pages, scanned`))), brdParts));

      // ---- Mendix
      const mxParts = [h('p', { class: 'ms-hint' }, `Source: ${mx.file || 'Mendix design document'}. Tables and text boxes are the document's own text; the templates are images.`)];
      (mx.sections || []).forEach((s, i) => {
        const imgs = (s.imageIds || []).map(id => (mx.images || []).find(m => m.id === id)).filter(Boolean);
        const inner = [];
        (s.text || []).forEach(t => inner.push(h('p', { class: 'ms-line' }, t)));
        (s.tables || []).forEach(t => {
          const rows = t.rows || [];
          if (!rows.length) return;
          const hdr = t.firstRowIsHeader;
          inner.push(h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
            hdr ? h('thead', null, h('tr', null, rows[0].map(c => h('th', null, c)))) : null,
            h('tbody', null, (hdr ? rows.slice(1) : rows).map(r => h('tr', null, r.map((c, ci) => h(ci === 0 && !hdr ? 'th' : 'td', null, c))))))));
        });
        (s.flowCharts || []).forEach(f => {
          inner.push(h('div', { class: 'ms-flow' }, h('div', { class: 'ms-hint' }, 'Flow-chart text boxes. ' + (f.note || '')),
            h('div', { class: 'ms-chips' }, (f.boxes || []).map(b => h('span', { class: 'ms-box' }, b)))));
        });
        if (imgs.length) {
          inner.push(h('div', { class: 'ms-imgs' }, imgs.map(m => h('a', { href: m.url, target: '_blank', rel: 'noopener', title: 'Open the full-size image' },
            h('img', { src: m.url, alt: `Template image ${m.id} from the Mendix document, section "${s.title}"`, loading: 'lazy' })))));
        }
        mxParts.push(h('details', { class: 'ms-det', open: i < 1 },
          h('summary', null, s.title || `Section ${i + 1}`),
          h('div', { class: 'ms-det-body' }, inner, h('div', { class: 'ms-hint' }, `From: ${mx.file || 'Mendix document'}, section ${i + 1} "${s.title || ''}"`))));
      });
      out.push(h('div', { class: 'card ms-gap' }, h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Mendix application design'), h('p', null, `${(mx.sections || []).length} sections, ${(mx.images || []).length} embedded images`))), mxParts));

      if (!(d.targetsOrBenefits || []).length) out.push(h('p', { class: 'ms-hint' }, 'Neither document states a quantified target or benefit for the digitalisation.'));
      return [section('Scrap management digitalisation', 'Mendix application and BRD for the EHS scrap-management solution', out), notes(d.notes)];
    });
  }
}
