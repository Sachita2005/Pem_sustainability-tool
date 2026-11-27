// App shell: navigation between pages, clock, theme, and the data-status dot.
//
// Each page is a module in ./pages/<name>.js exporting
//   render(root, ctx)   build the page into `root`; ctx = { params, rest, navigate }
//   destroy?()          optional clean-up (timers) when leaving the page
// Pages are loaded on demand, so a page that is missing or broken shows a
// message in its own tab and leaves the rest of the site working.

import { api } from './api.js';
import { applyTheme, destroyCharts, refreshTheme } from './charts.js';
import { clear, errorBox, h, loading, modal } from './ui.js';

const PAGES = ['home', 'metrics', 'initiatives', 'report', 'milestones'];
// Earlier addresses that now live inside the Metrics page.
const MOVED = { energy: { metric: 'energy' }, water: { metric: 'water' }, scrap: { metric: 'scrap' }, air: { metric: 'air' } };
const view = document.getElementById('view');
let current = null;
let renderToken = 0;

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  const parts = path.split('/').filter(Boolean);
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  if (MOVED[parts[0]]) return { page: 'metrics', rest: [], params: { ...params, ...MOVED[parts[0]] } };
  const page = PAGES.includes(parts[0]) ? parts[0] : 'home';
  return { page, rest: parts.slice(1), params };
}

export function navigate(page, params) {
  const qs = new URLSearchParams(params || {}).toString();
  location.hash = '#/' + page + (qs ? '?' + qs : '');
}

async function route() {
  const { page, rest, params } = parseHash();
  const token = ++renderToken;

  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  if (current && current.module && typeof current.module.destroy === 'function') {
    try { current.module.destroy(); } catch (err) { console.error(err); }
  }
  destroyCharts(view);
  clear(view).appendChild(loading());
  window.scrollTo(0, 0);

  try {
    const module = await import(`./pages/${page}.js`);
    if (token !== renderToken) return;
    clear(view);
    current = { page, module };
    await module.render(view, { params, rest, navigate });
  } catch (err) {
    if (token !== renderToken) return;
    console.error(err);
    clear(view).appendChild(errorBox(err));
    current = null;
  }
}

// ---------- Clock (India time) ----------

function initClock() {
  const el = document.getElementById('clock');
  if (!el) return;
  const format = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
  });
  const tick = () => { el.textContent = format.format(new Date()).replace(',', '') + ' IST'; };
  tick();
  setInterval(tick, 1000);
}

// ---------- Theme ----------

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('pem-theme', theme); } catch (err) { /* private window */ }
  refreshTheme();
}

function initTheme() {
  let theme = 'light';
  try { theme = localStorage.getItem('pem-theme') || theme; } catch (err) { /* private window */ }
  document.documentElement.dataset.theme = theme;
  applyTheme();
  document.getElementById('theme-toggle').addEventListener('click', () => {
    setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  });
}

// ---------- Data status ----------

async function initStatus() {
  const dot = document.getElementById('data-status');
  if (!dot) return;
  let health = null;
  try {
    health = await api.get('/health', null, { fresh: true });
  } catch (err) {
    dot.className = 'status-dot bad';
    dot.title = 'Server not reachable';
    return;
  }
  const states = Object.entries(health.domains || {});
  const broken = states.filter(([, s]) => s !== 'ok');
  if (!health.sourceDirFound) {
    dot.className = 'status-dot bad';
    dot.title = 'Source folder not found';
  } else if (broken.length) {
    dot.className = 'status-dot warn';
    dot.title = `${states.length - broken.length} of ${states.length} data areas ready`;
  } else {
    dot.className = 'status-dot ok';
    dot.title = 'Source data connected';
  }
  dot.addEventListener('click', () => {
    modal('Data sources', h('div', null,
      h('p', { class: 'muted' }, 'Source folder: ' + health.sourceDir + (health.sourceDirFound ? '' : ' (not found)')),
      h('div', { class: 'table-wrap', style: { marginTop: '12px' } },
        h('table', { class: 'table' },
          h('thead', null, h('tr', null, h('th', null, 'Area'), h('th', null, 'State'))),
          h('tbody', null, states.map(([name, state]) => h('tr', null, h('td', null, name), h('td', null, state))))))));
  });
}

initTheme();
initClock();
initStatus();
window.addEventListener('hashchange', route);
route();
