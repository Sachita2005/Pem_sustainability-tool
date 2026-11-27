// One look for every chart, in light and dark. Pages call the helpers below
// instead of `new Chart` so colours, tooltips, number formatting and clean-up
// are the same everywhere.

import { fmt, h } from './ui.js';

const live = new Set();

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function palette() {
  return ['--c1', '--c2', '--c3', '--c4', '--c5', '--c6', '--c7', '--c8'].map(css);
}

export function tone(name) {
  return css({ good: '--good', warn: '--warn', bad: '--bad', brand: '--brand', info: '--info', muted: '--text-3' }[name] || name);
}

function alpha(hex, a) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  return m ? `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})` : hex;
}

export function applyTheme() {
  if (typeof Chart === 'undefined') return;
  const d = Chart.defaults;
  d.font.family = 'Arial, Helvetica, sans-serif';
  d.font.size = 11.5;
  d.color = css('--text-2');
  d.borderColor = css('--line');
  d.animation.duration = 350;
  d.elements.line.tension = 0.3;
  d.elements.line.borderWidth = 2.2;
  d.elements.point.radius = 2.5;
  d.elements.point.hoverRadius = 5;
  d.elements.point.hitRadius = 10;
  d.elements.bar.borderRadius = 4;
  d.plugins.legend.position = 'top';
  d.plugins.legend.labels.usePointStyle = true;
  d.plugins.legend.labels.boxHeight = 7;
  d.plugins.legend.labels.padding = 14;
  const t = d.plugins.tooltip;
  t.backgroundColor = css('--surface');
  t.titleColor = css('--text');
  t.bodyColor = css('--text-2');
  t.borderColor = css('--line-strong');
  t.borderWidth = 1;
  t.padding = 10;
  t.cornerRadius = 8;
  t.boxPadding = 5;
  t.usePointStyle = true;
}

/** Redraw every chart on screen after a theme switch. */
export function refreshTheme() {
  applyTheme();
  const colours = palette();
  live.forEach(chart => {
    chart.data.datasets.forEach(ds => {
      if (ds._tone !== undefined) restyle(ds, typeof ds._tone === 'number' ? colours[ds._tone % colours.length] : tone(ds._tone), chart.config.type);
    });
    chart.options.scales && Object.values(chart.options.scales).forEach(s => {
      if (s.grid) s.grid.color = css('--line');
      if (s.ticks) s.ticks.color = css('--text-2');
      if (s.title) s.title.color = css('--text-2');
    });
    chart.update('none');
  });
}

function restyle(ds, colour, chartType) {
  const type = ds.type || chartType;
  ds.borderColor = colour;
  ds.pointBackgroundColor = colour;
  ds.backgroundColor = type === 'line' ? (ds.fill ? alpha(colour, 0.14) : colour) : colour;
}

function axis(title, extra) {
  return {
    grid: { color: css('--line') },
    ticks: { color: css('--text-2'), callback: v => fmt(v) },
    title: title ? { display: true, text: title, color: css('--text-2'), font: { size: 11 } } : { display: false },
    ...(extra || {})
  };
}

function mount(container, config, heightClass) {
  const canvas = h('canvas');
  const box = h('div', { class: 'chart' + (heightClass ? ' ' + heightClass : '') }, canvas);
  container.appendChild(box);
  const chart = new Chart(canvas.getContext('2d'), config);
  live.add(chart);
  return chart;
}

function datasets(series, chartType) {
  const colours = palette();
  return series.map((s, i) => {
    const toneKey = s.tone !== undefined ? s.tone : (s.colorIndex !== undefined ? s.colorIndex : i);
    const colour = typeof toneKey === 'number' ? colours[toneKey % colours.length] : tone(toneKey);
    const ds = {
      label: s.label,
      data: s.data,
      type: s.type,
      yAxisID: s.axis === 'right' ? 'y1' : 'y',
      stack: s.stack,
      fill: !!s.fill,
      borderDash: s.dashed ? [6, 4] : undefined,
      pointRadius: s.points === false ? 0 : undefined,
      tension: s.smooth === false ? 0 : undefined,   // straight segments for spiky data
      spanGaps: false,
      order: s.type === 'line' ? 1 : 2,
      _tone: toneKey,
      _unit: s.unit
    };
    restyle(ds, colour, chartType);
    return ds;
  });
}

function tooltipLabel(digits) {
  return ctx => {
    const v = ctx.parsed.y !== undefined && ctx.chart.options.indexAxis !== 'y' ? ctx.parsed.y : ctx.parsed.x;
    if (v === null || v === undefined) return null;
    const unit = ctx.dataset._unit ? ' ' + ctx.dataset._unit : '';
    return ` ${ctx.dataset.label}: ${fmt(v, digits !== undefined ? digits : (Math.abs(v) < 100 && !Number.isInteger(v) ? 2 : 0))}${unit}`;
  };
}

/**
 * Line or bar chart over categories (months, years, plants).
 *   series: [{ label, data, type?: 'line'|'bar', axis?: 'right', stack?, fill?, dashed?, tone?, unit?, points? }]
 *   opts:   { type: 'line'|'bar', yTitle, y1Title, stacked, horizontal, height: 'tall'|'short', digits, max, onClick(index) }
 * Null values are drawn as gaps, never as zero.
 */
export function seriesChart(container, labels, series, opts = {}) {
  const type = opts.type || 'line';
  const hasRight = series.some(s => s.axis === 'right');
  const scales = {
    x: { grid: { display: false }, ticks: { color: css('--text-2'), maxRotation: 45, autoSkip: true }, stacked: !!opts.stacked },
    y: axis(opts.yTitle, { beginAtZero: opts.beginAtZero !== false, stacked: !!opts.stacked, max: opts.max })
  };
  // Small quantities (diesel in KL, ratios) need decimals on the axis: opts.tickDigits.
  if (opts.xTitle) scales.x.title = { display: true, text: opts.xTitle, color: css('--text-2'), font: { size: 11 } };
  if (opts.tickDigits !== undefined) scales.y.ticks.callback = v => fmt(v, opts.tickDigits);
  if (hasRight) scales.y1 = axis(opts.y1Title, { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false } });
  if (opts.horizontal) { const x = scales.x; scales.x = scales.y; scales.y = x; }

  return mount(container, {
    type,
    data: { labels, datasets: datasets(series, type) },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: opts.horizontal ? 'y' : 'x',
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: series.length > 1 || opts.legend === true },
        tooltip: { callbacks: { label: tooltipLabel(opts.digits) } }
      },
      scales,
      onClick: opts.onClick ? (evt, items) => { if (items.length) opts.onClick(items[0].index); } : undefined
    }
  }, opts.height);
}

/** Doughnut with the share of each slice in the tooltip. slices: [{ label, value, tone? }] */
export function donutChart(container, slices, opts = {}) {
  const colours = palette();
  const total = slices.reduce((s, x) => s + (Number(x.value) || 0), 0) || 1;
  const chart = mount(container, {
    type: 'doughnut',
    data: {
      labels: slices.map(s => s.label),
      datasets: [{
        data: slices.map(s => s.value),
        backgroundColor: slices.map((s, i) => (s.tone !== undefined ? tone(s.tone) : colours[i % colours.length])),
        borderColor: css('--surface'),
        borderWidth: 2,
        hoverOffset: 6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      plugins: {
        legend: { position: opts.legend || 'bottom' },
        tooltip: { callbacks: { label: c => ` ${c.label}: ${fmt(c.parsed, opts.digits || 0)}${opts.unit ? ' ' + opts.unit : ''} (${fmt((c.parsed / total) * 100, 1)}%)` } }
      }
    }
  }, opts.height || 'short');
  return chart;
}

/** Horizontal line across a chart (a target or a limit) as an extra series. */
export function targetSeries(label, value, length, toneName = 'bad') {
  return { label, data: Array.from({ length }, () => value), type: 'line', dashed: true, points: false, tone: toneName };
}

/** Destroy every chart inside a container before the page is replaced. */
export function destroyCharts(container) {
  live.forEach(chart => {
    if (!container || container.contains(chart.canvas)) {
      chart.destroy();
      live.delete(chart);
    }
  });
}
