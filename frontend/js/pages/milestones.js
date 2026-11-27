// Milestones tab: slides 1-14 of the Sustainability Deck as a readable journey,
// grouped into the stages the API found (intro -> Aalingana -> TASL).
// Everything shown is quoted from GET /api/milestones; nothing is typed in here
// except interface labels.

import { api } from '../api.js';
import { badge, clear, empty, fmt, h, load, notes, pageHead, table } from '../ui.js';
import { seriesChart } from '../charts.js';

function ensureCss() {
  if (document.getElementById('milestones-css')) return;
  document.head.appendChild(h('link', { id: 'milestones-css', rel: 'stylesheet', href: '/static/css/milestones.css' }));
}

export function destroy() { /* charts are cleaned up by the shell */ }

const STAGE_TONE = { intro: 'info', aalingana: 'good', tasl: 'brand' };
const reportHref = n => '#/report?slide=' + n;
const hasText = s => typeof s === 'string' && s.trim() !== '';

// ---------------------------------------------------------------- pieces

function slideLink(n) {
  return h('a', { class: 'ms-slide-link', href: reportHref(n), title: 'Open slide ' + n + ' in the Report tab' }, 'Slide ' + n);
}

function slideImage(slide, { open } = {}) {
  const wrap = h('div', { class: 'ms-image' });
  if (!slide.imageUrl) {
    wrap.appendChild(h('div', { class: 'ms-image-missing' },
      'The picture of this slide is not available on this machine. ',
      h('a', { href: reportHref(slide.number) }, 'Open slide ' + slide.number + ' in the Report tab')));
    return wrap;
  }
  const show = () => {
    clear(wrap);
    const img = h('img', { src: slide.imageUrl + (slide.imageUrl.includes('?') ? '&' : '?') + 'r=' + Date.now(), alt: 'Slide ' + slide.number + ': ' + (slide.title || ''), loading: 'lazy' });
    img.addEventListener('error', () => {
      clear(wrap).appendChild(h('div', { class: 'ms-image-missing' },
        'The picture of this slide is not ready yet (it is rendered in the background after the server starts). ',
        h('button', { type: 'button', class: 'btn small', onClick: show }, 'Try again'), ' ',
        h('a', { href: reportHref(slide.number) }, 'Open slide ' + slide.number + ' in the Report tab')));
    });
    wrap.appendChild(img);
  };
  if (open) { show(); return wrap; }
  // Left collapsed for fully structured slides: loaded only when asked for.
  const det = h('details', { class: 'ms-picture' }, h('summary', null, 'Show the slide picture'));
  det.addEventListener('toggle', () => { if (det.open && !wrap.firstChild) show(); });
  det.appendChild(wrap);
  return det;
}

function bulletList(items) {
  return h('ul', { class: 'ms-list' }, items.map(it => h('li', null,
    it.marker === 'tick' ? h('span', { class: 'ms-tick', title: 'The deck gives this line a tick-mark bullet' }, '✓ ') : null,
    it.text)));
}

function bulletGroups(slide) {
  const groups = (slide.bullets || []);
  if (!groups.length) return null;
  if (groups.every(g => !(g.items && g.items.length))) {
    // Headings with no lines (slide 8): the lists live in the picture, so just name the headings.
    return h('p', { class: 'ms-none' }, 'Headings on the slide with no readable lines under them: ',
      groups.map(g => g.heading).filter(hasText).join(' · '), '. See the slide picture.');
  }
  const many = groups.length > 1;
  return h('div', { class: 'ms-groups' + (many ? ' multi' : '') }, groups.map(g => h('div', { class: 'ms-group' },
    hasText(g.heading) ? h('h4', null, g.heading) : null,
    g.items && g.items.length ? bulletList(g.items)
      : h('p', { class: 'ms-none' }, 'No lines were read from the slide text for this heading.' + (g.scopeLabelSource === 'picture' ? ' Its label comes from the slide picture.' : '')))));
}

// Targets that carry a year, grouped by pillar when they have one -> year-by-year timeline.
function timeline(targets) {
  const byPillar = new Map();
  targets.forEach(t => {
    const k = t.pillar || '';
    if (!byPillar.has(k)) byPillar.set(k, []);
    byPillar.get(k).push(t);
  });
  return h('div', { class: 'ms-lanes' }, [...byPillar.entries()].map(([pillar, list]) => h('div', { class: 'ms-lane' },
    pillar ? h('h4', null, pillar) : null,
    h('ol', { class: 'ms-timeline' }, list.map(t => h('li', null,
      h('span', { class: 'ms-year' }, String(t.year)),
      h('span', { class: 'ms-event' }, hasText(t.label) ? t.label : t.text)))))));
}

function measureLine(m) {
  return m.value !== null && m.value !== undefined
    ? h('span', { class: 'ms-num' }, fmt(m.value, Number.isInteger(m.value) ? 0 : 2), m.unit ? h('small', null, ' ' + m.unit) : null)
    : h('span', { class: 'ms-desc' }, m.text);
}

function goalCard(g) {
  const t = g.target || {}, c = g.current || {};
  const numeric = t.value !== null && t.value !== undefined && c.value !== null && c.value !== undefined && t.unit && t.unit === c.unit;
  const max = numeric ? Math.max(t.value, c.value) : 0;
  const bar = (cls, v) => h('div', { class: 'ms-bar' }, h('span', { class: cls, style: { width: (max > 0 ? Math.max(1.5, (v / max) * 100) : 0) + '%' } }));
  return h('div', { class: 'card ms-goal' },
    h('div', { class: 'ms-goal-head' },
      h('h4', null, g.label),
      h('div', { class: 'ms-goal-tags' }, g.year ? badge(String(g.year), 'info') : null, g.baselineType ? badge(g.baselineType) : null)),
    h('div', { class: 'ms-pair' },
      h('div', null, h('div', { class: 'ms-pair-label' }, 'Target'), h('div', { class: 'ms-pair-val' }, measureLine(t))),
      h('div', null, h('div', { class: 'ms-pair-label' }, 'Current (up to Mar-2026)'), h('div', { class: 'ms-pair-val' }, measureLine(c)))),
    numeric ? h('div', { class: 'ms-bars', role: 'img', 'aria-label': 'Target ' + fmt(t.value, 2) + ' ' + t.unit + ', current ' + fmt(c.value, 2) + ' ' + c.unit },
      h('div', { class: 'ms-bar-row' }, h('span', null, 'Target'), bar('target', t.value)),
      h('div', { class: 'ms-bar-row' }, h('span', null, 'Current'), bar('current', c.value)),
      h('div', { class: 'ms-bar-unit' }, 'Both in ' + t.unit)) : null);
}

function statusCards(statuses) {
  return h('div', { class: 'ms-groups multi' }, statuses.map(s => h('div', { class: 'card ms-status' },
    h('h4', null, s.pillar),
    h('p', { class: 'ms-status-target' }, s.target),
    s.marker ? h('p', null, h('span', { class: 'badge ' + (s.marker === 'WIP' ? 'warn' : 'brand') }, s.marker === 'tick' ? '✓ Tick mark' : s.marker),
      ' ', h('span', { class: 'small muted' }, s.marker === 'tick' ? 'shown as a picture next to this target on the slide' : 'written on the slide')) : null,
    (s.details || []).map(d => h('p', null, d)),
    s.note ? h('p', { class: 'small muted' }, s.note) : null)));
}

function keyFigures(targets) {
  return h('div', { class: 'ms-groups multi' }, targets.map(t => h('div', { class: 'card ms-fig' },
    h('h4', null, t.label),
    hasText(t.text) ? h('p', null, t.text) : null,
    t.points && t.points.length ? table(
      [{ key: 'fy', label: 'FY' }, { key: 'electricityRequirement', label: 'Electricity requirement', num: true }, { key: 'solarSharePercent', label: 'Solar share', num: true }],
      t.points) : null,
    t.deltas && t.deltas.length ? h('p', { class: 'small muted' }, t.deltas.join(' · ')) : null)));
}

function chartBlock(slide, ch, afterMount) {
  const box = h('div', { class: 'card ms-chart' });
  const cats = ch.categories && ch.categories.length ? ch.categories : null;
  let labels = cats;
  if (!labels) {
    // Slide 13: the chart cache has no categories; the FY labels are structured elsewhere on the slide.
    const pts = ((slide.targets || []).find(t => t.points) || {}).points;
    const n = Math.max(...ch.series.map(s => s.values.length));
    labels = pts && pts.length === n ? pts.map(p => p.fy) : Array.from({ length: n }, (_, i) => String(i + 1));
  }
  const looksShare = s => s.values.every(v => v === null || (v >= 0 && v <= 1.5)) && ch.series.length > 1 && s !== ch.series[0];
  const units = ch.series.filter(s => !looksShare(s));
  const shares = ch.series.filter(looksShare);
  const nameOf = s => s.name || 'Series';
  box.appendChild(h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Chart data on slide ' + slide.number), h('p', null, 'Values as stored in the slide\'s chart'))));
  const a = h('div'); box.appendChild(a);
  let b = null;
  if (shares.length) { b = h('div'); box.appendChild(h('h4', { class: 'ms-sub' }, 'Shares (stored as fractions, shown as %)')); box.appendChild(b); }
  afterMount.push(() => {
    seriesChart(a, labels, units.map(s => ({ label: nameOf(s), data: s.values, type: 'bar' })), { type: 'bar', yTitle: slide.number === 14 ? 'as named in the series' : 'units', height: 'short' });
    if (b) seriesChart(b, labels, shares.map(s => ({ label: nameOf(s), data: s.values.map(v => v === null ? null : v * 100), unit: '%' })), { type: 'line', yTitle: '% of total', height: 'short', digits: 1 });
  });
  return box;
}

// ---------------------------------------------------------------- one slide

// The API title of some slides is the start of a long sentence cut short (slide 4). When the same
// sentence is shown in full below, keep only the words before its colon as the heading.
function displayTitle(slide, leads) {
  const t = slide.title;
  if (!hasText(t)) return 'Slide ' + slide.number;
  const stem = t.replace(/[\s.…]+$/, '').slice(0, -1);
  if (t.length > 60 && leads.some(l => l.length > t.length && l.startsWith(stem))) {
    const i = t.indexOf(':');
    return i > 0 ? t.slice(0, i) : 'Slide ' + slide.number;
  }
  return t;
}

function slideBlock(slide, afterMount) {
  const targets = slide.targets || [];
  const pairs = targets.filter(t => t.target && t.current);
  const dated = targets.filter(t => t.year && !(t.target && t.current));
  const loose = targets.filter(t => !t.year && !(t.target && t.current));
  const statuses = slide.statuses || [];

  // Headings already shown as a group title are not repeated.
  const used = new Set([
    ...(slide.bullets || []).map(g => g.heading),
    ...targets.map(t => t.pillar), ...statuses.map(s => s.pillar)]);
  const leads = (slide.headings || []).filter(x => hasText(x) && !used.has(x) && x !== slide.title);

  const body = [];
  leads.forEach((t, i) => body.push(h(i === 0 && leads.length === 1 ? 'p' : 'p', { class: 'ms-lead' }, t)));
  if (statuses.length) body.push(statusCards(statuses));
  else if (dated.length) body.push(timeline(dated));
  if (pairs.length) body.push(h('div', { class: 'ms-goals' }, pairs.map(goalCard)));
  if (loose.length) body.push(keyFigures(loose));
  const bl = bulletGroups(slide);
  if (bl) body.push(bl);
  (slide.charts || []).forEach(ch => body.push(chartBlock(slide, ch, afterMount)));
  (slide.tables || []).forEach(t => {
    const tbl = table((t.columns || []).map((c, i) => ({ label: c, render: r => r[i] })), t.rows || []);
    body.push(pairs.length
      ? h('details', { class: 'ms-picture' }, h('summary', null, 'Table as written on the slide'), tbl)
      : h('div', { class: 'card' }, t.title ? h('h4', { class: 'ms-sub' }, t.title) : null, tbl));
  });
  (slide.footnotes || []).forEach(f => body.push(h('p', { class: 'ms-foot small' }, f)));

  const imageFirst = slide.structure === 'image' || slide.structure === 'partial';
  return h('article', { class: 'ms-slide', id: 'slide-' + slide.number },
    h('header', { class: 'ms-slide-head' },
      h('div', null,
        h('span', { class: 'ms-kicker' }, slide.stageLabel || slide.stage),
        h('h3', null, displayTitle(slide, leads))),
      h('div', { class: 'ms-slide-tags' },
        slide.structure === 'image' ? badge('Picture only', 'warn') : slide.structure === 'partial' ? badge('Partly structured', 'warn') : null,
        slideLink(slide.number))),
    imageFirst ? slideImage(slide, { open: true }) : null,
    body,
    slide.notes && slide.notes.length ? h('div', { class: 'small muted ms-slide-notes' }, slide.notes.map(n => h('p', null, n))) : null,
    imageFirst ? null : slideImage(slide));
}

// ---------------------------------------------------------------- page

export async function render(root, ctx) {
  ensureCss();
  root.appendChild(pageHead('Milestones', 'Sustainability objectives and milestones: slides 1–14 of the Sustainability Deck, in deck order'));
  const box = h('div');
  root.appendChild(box);

  load(box, api.get('/milestones'), data => {
    if (!data.available || !(data.slides || []).length) {
      return [empty('The Sustainability Deck was not found, so there are no milestones to show.'), notes(data.notes)];
    }
    const stages = data.stages || [];
    const slidesOf = st => (st.slides || []).map(n => data.slides.find(s => s.number === n)).filter(Boolean);
    let active = stages.some(s => s.id === ctx.params.stage) ? ctx.params.stage : null;
    if (!active && ctx.params.slide) {
      const st = stages.find(s => (s.slides || []).includes(Number(ctx.params.slide)));
      if (st) active = st.id;
    }
    active = active || (stages[0] && stages[0].id);

    const stepper = h('nav', { class: 'ms-stepper', 'aria-label': 'Stages' });
    const content = h('div', { class: 'ms-stage-body' });
    const mounts = [];

    const drawStepper = () => {
      clear(stepper);
      stages.forEach((st, i) => stepper.appendChild(h('button', {
        type: 'button', class: 'ms-step t-' + (STAGE_TONE[st.id] || 'brand') + (st.id === active ? ' active' : ''),
        'aria-current': st.id === active ? 'step' : null, onClick: () => go(st.id)
      },
      h('span', { class: 'ms-step-n' }, String(i + 1)),
      h('span', { class: 'ms-step-text' },
        h('strong', null, st.label),
        h('small', null, (st.slides && st.slides.length > 1 ? 'Slides ' + st.slides[0] + '–' + st.slides[st.slides.length - 1] : 'Slide ' + (st.slides || [])[0]))))));
    };

    function go(id, scrollTop) {
      active = id;
      drawStepper();
      const st = stages.find(s => s.id === id);
      const idx = stages.indexOf(st);
      const slides = slidesOf(st);
      clear(content);
      content.className = 'ms-stage-body t-' + (STAGE_TONE[id] || 'brand');
      content.appendChild(h('div', { class: 'ms-stage-intro' },
        h('h2', null, st.label),
        hasText(st.evidence) ? h('p', { class: 'muted' }, st.evidence) : null,
        slides.length > 1 ? h('div', { class: 'ms-jump' }, slides.map(s => h('a', { href: '#slide-' + s.number, onClick: e => { e.preventDefault(); const el = document.getElementById('slide-' + s.number); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); } }, String(s.number) + (hasText(s.title) ? ' · ' + s.title : '')))) : null));
      const after = [];
      slides.forEach(s => content.appendChild(slideBlock(s, after)));
      content.appendChild(h('div', { class: 'ms-nextprev' },
        idx > 0 ? h('button', { type: 'button', class: 'btn', onClick: () => go(stages[idx - 1].id, true) }, '◀ ' + stages[idx - 1].label) : h('span'),
        idx < stages.length - 1 ? h('button', { type: 'button', class: 'btn primary', onClick: () => go(stages[idx + 1].id, true) }, stages[idx + 1].label + ' ▶') : h('span')));
      after.forEach(fn => fn());
      if (scrollTop) stepper.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    const out = [
      stepper, content,
      h('div', { class: 'ms-about' }, notes(data.notes))
    ];
    // Build after the nodes are attached so charts can measure their boxes.
    mounts.push(() => go(active));
    setTimeout(() => {
      mounts.forEach(f => f());
      if (ctx.params.slide) {
        const el = document.getElementById('slide-' + ctx.params.slide);
        if (el) el.scrollIntoView({ block: 'start' });
      }
    }, 0);
    return out;
  });
}
