// Small building blocks shared by every page. No framework: `h()` makes DOM
// nodes, and text passed to it is always inserted as text, never as HTML.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (v === false || v === null || v === undefined) return;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') el.innerHTML = v; // only for markup written in our own code
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  });
  append(el, children);
  return el;
}

function append(el, children) {
  children.flat(Infinity).forEach(c => {
    if (c === null || c === undefined || c === false) return;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  });
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// ---------- Numbers and dates ----------

const DASH = '—';

/** Indian-grouped number; null/undefined/NaN become a dash so a gap never reads as zero. */
export function fmt(value, digits = 0) {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return DASH;
  return Number(value).toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Large values shortened the Indian way: 12.3 L (lakh), 4.56 Cr (crore). */
export function fmtShort(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  const n = Number(value), a = Math.abs(n);
  if (a >= 1e7) return fmt(n / 1e7, digits) + ' Cr';
  if (a >= 1e5) return fmt(n / 1e5, digits) + ' L';
  return fmt(n, a < 100 && !Number.isInteger(n) ? digits : 0);
}

export function fmtPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return DASH;
  return fmt(value, digits) + '%';
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-04" -> "Apr-26" */
export function monthLabel(ym) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(ym || ''));
  return m ? MONTHS[Number(m[2]) - 1] + '-' + m[1].slice(2) : String(ym || DASH);
}

/** Percentage change from `before` to `after`, or null when it cannot be worked out. */
export function change(after, before) {
  if (after === null || after === undefined || !before) return null;
  return ((after - before) / Math.abs(before)) * 100;
}

// ---------- Components ----------

export function pageHead(title, subtitle, tools) {
  return h('div', { class: 'page-head' },
    h('div', null, h('h1', null, title), subtitle ? h('p', null, subtitle) : null),
    tools ? h('div', { class: 'page-tools' }, tools) : null);
}

export function section(title, subtitle, ...content) {
  return h('section', { class: 'section' },
    h('div', { class: 'section-head' }, h('h2', null, title), subtitle ? h('p', { class: 'small' }, subtitle) : null),
    content);
}

export function card(title, subtitle, ...content) {
  return h('div', { class: 'card' },
    title ? h('div', { class: 'card-head' }, h('div', null, h('h3', null, title), subtitle ? h('p', null, subtitle) : null)) : null,
    content);
}

/** KPI tile. `tone` is good | warn | bad | info; `delta` = { value: % change, goodWhen: 'down' | 'up', label }. */
export function kpi({ label, value, unit, sub, tone, delta }) {
  let deltaEl = null;
  if (delta && delta.value !== null && delta.value !== undefined && !Number.isNaN(delta.value)) {
    const up = delta.value >= 0;
    const cls = 'delta ' + (up ? 'up' : 'down') + (delta.goodWhen === 'up' ? ' good-up' : '');
    deltaEl = h('span', { class: cls }, (up ? '▲ ' : '▼ ') + fmt(Math.abs(delta.value), 1) + '%' + (delta.label ? ' ' + delta.label : ''));
  }
  return h('div', { class: 'card kpi' + (tone ? ' ' + tone : '') },
    h('div', { class: 'kpi-label' }, label),
    h('div', { class: 'kpi-value' }, value, unit ? h('small', null, unit) : null),
    (sub || deltaEl) ? h('div', { class: 'kpi-sub' }, deltaEl, deltaEl && sub ? ' · ' : null, sub) : null);
}

export function badge(text, tone) {
  return h('span', { class: 'badge' + (tone ? ' ' + tone : '') }, text);
}

/** Notes the backend attaches to a payload (missing sheets, gaps): always shown, never hidden. */
export function notes(list) {
  const items = (list || []).filter(Boolean);
  if (!items.length) return null;
  return h('div', { class: 'notes' }, h('strong', null, 'About this data'), h('ul', null, items.map(n => h('li', null, n))));
}

export function empty(message) {
  return h('div', { class: 'empty' }, message || 'No data in the source files for this view.');
}

export function loading(message) {
  return h('div', { class: 'loading' }, h('span', { class: 'spinner' }), message || 'Loading…');
}

export function errorBox(err) {
  return h('div', { class: 'error' }, h('strong', null, 'This section could not be loaded. '), String(err && err.message ? err.message : err));
}

/** Tab strip. tabs: [{ id, label }]; onChange(id). Returns { el, set(id) }. */
export function tabs(items, active, onChange) {
  const el = h('div', { class: 'tabs', role: 'tablist' });
  const buttons = {};
  const set = id => {
    Object.entries(buttons).forEach(([k, b]) => b.classList.toggle('active', k === id));
  };
  items.forEach(t => {
    buttons[t.id] = h('button', { type: 'button', role: 'tab', onClick: () => { set(t.id); onChange(t.id); } }, t.label);
    el.appendChild(buttons[t.id]);
  });
  set(active);
  return { el, set };
}

/** Chip picker (plants, families…). options: [{ id, label }]. Returns { el, set(id) }. */
export function chips(options, active, onChange) {
  const el = h('div', { class: 'chips' });
  const buttons = {};
  const set = id => Object.entries(buttons).forEach(([k, b]) => b.classList.toggle('active', k === String(id)));
  options.forEach(o => {
    buttons[o.id] = h('button', { type: 'button', class: 'chip', onClick: () => { set(o.id); onChange(o.id); } }, o.label);
    el.appendChild(buttons[o.id]);
  });
  set(active);
  return { el, set };
}

export function select(options, value, onChange, attrs) {
  const el = h('select', { class: 'input', ...(attrs || {}), onChange: e => onChange(e.target.value) },
    options.map(o => h('option', { value: o.id, selected: String(o.id) === String(value) }, o.label)));
  return el;
}

/**
 * Data table. columns: [{ key, label, num, render(row) }]. Numbers are right-aligned.
 * A null cell shows a dash.
 */
export function table(columns, rows, { emptyText } = {}) {
  if (!rows || !rows.length) return empty(emptyText);
  return h('div', { class: 'table-wrap' },
    h('table', { class: 'table' },
      h('thead', null, h('tr', null, columns.map(c => h('th', { class: c.num ? 'num' : null }, c.label)))),
      h('tbody', null, rows.map(r => h('tr', null, columns.map(c => {
        const v = c.render ? c.render(r) : r[c.key];
        return h('td', { class: c.num ? 'num' : null }, v === null || v === undefined || v === '' ? DASH : v);
      }))))));
}

export function toast(message, tone) {
  const host = document.getElementById('toast-host');
  const el = h('div', { class: 'toast' + (tone ? ' ' + tone : '') }, message);
  host.appendChild(el);
  setTimeout(() => el.remove(), tone === 'bad' ? 7000 : 3800);
}

/** Modal dialog. Returns close(). Closes on backdrop click and Escape. */
export function modal(title, body, options = {}) {
  const host = document.getElementById('modal-host');
  const close = () => { backdrop.remove(); document.removeEventListener('keydown', onKey); if (options.onClose) options.onClose(); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  const backdrop = h('div', { class: 'modal-backdrop', onClick: e => { if (e.target === backdrop) close(); } },
    h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' },
      h('div', { class: 'modal-head' }, h('h3', null, title), h('button', { type: 'button', class: 'btn small', onClick: close }, 'Close')),
      h('div', { class: 'modal-body' }, body)));
  document.addEventListener('keydown', onKey);
  host.appendChild(backdrop);
  return close;
}

/**
 * Loads data into a container: shows a spinner, then the built content, or an
 * error box. `build(data)` returns a node (or array of nodes). One failing
 * section never blanks the rest of the page.
 */
export async function load(container, promise, build) {
  clear(container).appendChild(loading());
  try {
    const data = await promise;
    const out = await build(data);
    clear(container);
    append(container, [out]);
  } catch (err) {
    console.error(err);
    clear(container).appendChild(errorBox(err));
  }
  return container;
}
