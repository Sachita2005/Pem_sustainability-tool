// Report tab: the Sustainability Deck, with Edit, Save, Download and Approve.
//
// Each slide is shown as PowerPoint renders it. In edit mode the slide's text
// boxes are outlined on top of the picture; clicking one opens its paragraphs
// in the side panel. Changes are held in the browser until Save, which writes
// them into the working copy of the .pptx on the server (the original deck is
// never touched) and re-renders the changed slides.

import { api } from '../api.js';
import { badge, clear, empty, errorBox, h, loading, modal, notes, pageHead, toast } from '../ui.js';

// Appends children, skipping null/false and flattening arrays (DOM append() would print them as text).
function put(el, ...kids) {
  kids.flat(Infinity).forEach(k => {
    if (k === null || k === undefined || k === false) return;
    el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  });
  return el;
}

let pollTimer = null;
let keyHandler = null;

function ensureCss() {
  if (document.getElementById('report-css')) return;
  document.head.appendChild(h('link', { id: 'report-css', rel: 'stylesheet', href: '/static/css/report.css' }));
}

export function destroy() {
  clearInterval(pollTimer);
  pollTimer = null;
  if (keyHandler) document.removeEventListener('keydown', keyHandler);
  keyHandler = null;
  window.onbeforeunload = null;
}

export async function render(root, ctx) {
  ensureCss();
  destroy();

  const state = {
    info: null,
    slides: [],
    current: Math.max(1, parseInt(ctx.params.slide, 10) || 1),
    model: null,          // text boxes of the current slide
    editing: false,
    selected: null,       // shape id selected in edit mode
    pending: new Map()    // "slide:shapeId" -> [paragraph text, ...]  (unsaved)
  };

  const head = pageHead('Report', 'Sustainability Deck — view, edit, save, download and approve');
  const toolbar = h('div', { class: 'card report-toolbar' });
  const noteBox = h('div');
  const rail = h('div', { class: 'report-rail', 'aria-label': 'Slides' });
  const stage = h('div', { class: 'report-stage' });
  const panel = h('div', { class: 'report-panel card' });
  const body = h('div', { class: 'report-body' }, rail, h('div', { class: 'report-main' }, stage), panel);
  put(root, head, toolbar, noteBox, body);
  clear(stage).appendChild(loading('Opening the deck…'));

  try {
    const [info, list] = await Promise.all([api.get('/deck', null, { fresh: true }), api.get('/deck/slides', null, { fresh: true })]);
    state.info = info;
    state.slides = list.slides || [];
  } catch (err) {
    clear(stage).appendChild(errorBox(err));
    return;
  }
  if (!state.info.available || !state.slides.length) {
    clear(stage).appendChild(empty('The Sustainability Deck (.pptx) was not found in the common folder of the source data.'));
    noteBox.appendChild(notes(state.info.notes) || h('span'));
    return;
  }
  state.current = Math.min(state.current, state.slides.length);

  // ---------------------------------------------------------------- helpers
  const key = (slide, id) => slide + ':' + id;
  const dirtyCount = () => state.pending.size;
  const slideMeta = n => state.slides[n - 1];
  const paragraphsOf = box => {
    const held = state.pending.get(key(state.current, box.id));
    return held || box.paragraphs.map(p => p.text);
  };

  window.onbeforeunload = () => (dirtyCount() ? 'There are unsaved slide edits.' : undefined);

  // ---------------------------------------------------------------- toolbar
  function drawToolbar() {
    const a = state.info.approval || {};
    const approved = a.status === 'approved';
    const rendering = state.info.render && state.info.render.state === 'rendering';
    const dirty = dirtyCount();

    const jump = h('input', {
      class: 'input report-jump', type: 'number', min: 1, max: state.slides.length, value: state.current,
      'aria-label': 'Slide number',
      onChange: e => go(parseInt(e.target.value, 10) || state.current)
    });

    put(clear(toolbar), 
      h('div', { class: 'report-toolbar-group' },
        h('button', { type: 'button', class: 'btn', disabled: state.current <= 1, onClick: () => go(state.current - 1) }, '◀ Prev'),
        h('span', { class: 'report-count' }, 'Slide ', jump, ' of ' + state.slides.length),
        h('button', { type: 'button', class: 'btn', disabled: state.current >= state.slides.length, onClick: () => go(state.current + 1) }, 'Next ▶')),
      h('div', { class: 'report-toolbar-group' },
        approved
          ? badge('✓ Approved' + (a.approvedBy ? ' by ' + a.approvedBy : ''), 'good')
          : badge(state.info.hasEditsFromOriginal ? 'Draft — edited' : 'Draft', 'warn'),
        rendering ? badge(`Rendering slides ${state.info.render.done}/${state.info.render.total}`, 'info') : null,
        state.info.renderer === 'none' ? badge('No slide pictures on this machine', 'warn') : null,
        h('button', { type: 'button', class: 'btn' + (state.editing ? ' primary' : ''), onClick: toggleEdit }, state.editing ? 'Editing…' : 'Edit'),
        h('button', { type: 'button', class: 'btn primary', disabled: !dirty, onClick: save }, dirty ? `Save (${dirty})` : 'Save'),
        h('a', { class: 'btn', href: api.url('/deck/download'), onClick: e => { if (dirty && !confirm('There are unsaved edits. Download the deck without them?')) e.preventDefault(); } }, 'Download'),
        approved
          ? h('button', { type: 'button', class: 'btn', onClick: withdraw }, 'Withdraw approval')
          : h('button', { type: 'button', class: 'btn good', onClick: approve }, '✓ Approve'),
        h('button', { type: 'button', class: 'btn', onClick: showHistory }, 'History')));
  }

  // ---------------------------------------------------------------- slide rail
  function drawRail() {
    put(clear(rail), state.slides.map(s => {
      const edited = [...state.pending.keys()].some(k => k.startsWith(s.number + ':'));
      return h('button', {
        type: 'button', class: 'report-thumb' + (s.number === state.current ? ' active' : ''), id: 'thumb-' + s.number,
        title: s.title || 'Slide ' + s.number, onClick: () => go(s.number)
      },
      h('span', { class: 'report-thumb-pic' }, s.imageUrl ? h('img', { src: s.imageUrl, alt: '', loading: 'lazy' }) : h('span', { class: 'report-thumb-blank' }, s.number)),
      h('span', { class: 'report-thumb-text' }, h('strong', null, s.number + (edited ? ' •' : '')), ' ', s.title || 'Untitled'));
    }));
    const active = document.getElementById('thumb-' + state.current);
    if (active) rail.scrollTo({ top: active.offsetTop - rail.clientHeight / 2 + active.offsetHeight / 2 });
  }

  function markRail() {
    rail.querySelectorAll('.report-thumb').forEach(b => b.classList.toggle('active', b.id === 'thumb-' + state.current));
    const active = document.getElementById('thumb-' + state.current);
    if (active) rail.scrollTo({ top: active.offsetTop - rail.clientHeight / 2 + active.offsetHeight / 2, behavior: 'smooth' });
  }

  // ---------------------------------------------------------------- stage
  function drawStage() {
    const meta = slideMeta(state.current);
    const model = state.model;
    const aspect = (state.info.size && state.info.size.aspect) || 16 / 9;
    const canvas = h('div', { class: 'report-slide' + (state.editing ? ' editing' : ''), style: { aspectRatio: String(aspect) } });
    const imageUrl = (model && model.imageUrl) || meta.imageUrl;

    if (imageUrl) {
      canvas.appendChild(h('img', { class: 'report-slide-img', src: imageUrl, alt: meta.title || 'Slide ' + meta.number }));
    } else {
      canvas.classList.add('no-image');
      canvas.appendChild(h('div', { class: 'report-slide-wait' },
        state.info.renderer === 'none'
          ? 'Slide pictures are not available on this machine (PowerPoint is not installed). The text of the slide is shown instead.'
          : 'This slide’s picture is being prepared…'));
    }

    if (model) {
      model.textBoxes.forEach(box => {
        const isDirty = state.pending.has(key(state.current, box.id));
        // Without a picture every box shows its text; with one, only edited boxes cover the old text.
        const showText = !imageUrl || isDirty;
        if (!state.editing && !showText) return;
        const el = h('button', {
          type: 'button',
          class: 'report-box' + (isDirty ? ' dirty' : '') + (state.selected === box.id ? ' selected' : '') + (showText ? ' with-text' : ''),
          style: { left: box.left * 100 + '%', top: box.top * 100 + '%', width: box.width * 100 + '%', height: box.height * 100 + '%', transform: box.rotation ? `rotate(${box.rotation}deg)` : null },
          title: state.editing ? 'Click to edit this text' : null,
          tabindex: state.editing ? 0 : -1,
          onClick: () => { if (state.editing) { state.selected = box.id; drawStage(); drawPanel(); } }
        }, showText ? paragraphsOf(box).filter(t => t.trim()).join('\n') : null);
        canvas.appendChild(el);
      });
    }
    clear(stage).appendChild(canvas);
  }

  // ---------------------------------------------------------------- side panel
  function drawPanel() {
    clear(panel);
    const meta = slideMeta(state.current);
    panel.appendChild(h('div', { class: 'card-head' }, h('div', null, h('h3', null, 'Slide ' + meta.number), h('p', null, meta.title || 'Untitled'))));

    if (!state.model) { panel.appendChild(loading()); return; }
    const boxes = state.model.textBoxes;

    if (!state.editing) {
      panel.appendChild(h('p', { class: 'muted small' }, boxes.length
        ? `${boxes.length} text box${boxes.length > 1 ? 'es' : ''} on this slide. Choose Edit to change the text; charts and pictures are kept as they are.`
        : 'This slide has no editable text.'));
      return;
    }
    if (!boxes.length) { panel.appendChild(empty('This slide has no editable text.')); return; }

    const box = boxes.find(b => b.id === state.selected);
    if (!box) {
      panel.appendChild(h('p', { class: 'muted small' }, 'Click an outlined text on the slide, or pick one here:'));
      panel.appendChild(h('div', { class: 'report-box-list' }, boxes.map(b => h('button', {
        type: 'button', class: 'report-box-pick' + (state.pending.has(key(state.current, b.id)) ? ' dirty' : ''),
        onClick: () => { state.selected = b.id; drawStage(); drawPanel(); }
      }, (paragraphsOf(b).find(t => t.trim()) || '(empty)').slice(0, 90)))));
      return;
    }

    const texts = paragraphsOf(box).slice();
    const commit = () => {
      const original = box.paragraphs.map(p => p.text);
      const same = texts.length === original.length && texts.every((t, i) => t === original[i]);
      if (same) state.pending.delete(key(state.current, box.id));
      else state.pending.set(key(state.current, box.id), texts.slice());
      drawToolbar();
      drawStage();
    };

    panel.appendChild(h('p', { class: 'muted small' }, 'One field per paragraph. Formatting (font, size, colour) is kept from the original.'));
    const fields = h('div', { class: 'report-fields' });
    texts.forEach((text, i) => {
      const area = h('textarea', { class: 'input report-para', rows: Math.min(8, Math.max(2, Math.ceil(text.length / 44))), 'aria-label': 'Paragraph ' + (i + 1) }, text);
      area.addEventListener('input', () => { texts[i] = area.value.replace(/\n/g, ' '); commit(); });
      fields.appendChild(h('div', { class: 'report-para-row' }, area,
        h('button', { type: 'button', class: 'btn small danger', title: 'Remove this paragraph', onClick: () => { texts.splice(i, 1); commit(); drawPanel(); } }, '✕')));
    });
    put(panel, fields,
      h('div', { class: 'form-row', style: { marginTop: '10px' } },
        h('button', { type: 'button', class: 'btn small', onClick: () => { texts.push(''); commit(); drawPanel(); } }, '+ Paragraph'),
        h('button', { type: 'button', class: 'btn small', disabled: !state.pending.has(key(state.current, box.id)), onClick: () => { state.pending.delete(key(state.current, box.id)); drawToolbar(); drawStage(); drawPanel(); } }, 'Undo changes'),
        h('button', { type: 'button', class: 'btn small', onClick: () => { state.selected = null; drawStage(); drawPanel(); } }, 'Other texts')));
  }

  // ---------------------------------------------------------------- actions
  async function go(n) {
    state.current = Math.max(1, Math.min(state.slides.length, n));
    state.selected = null;
    state.model = null;
    history.replaceState(null, '', '#/report?slide=' + state.current);
    drawToolbar();
    markRail();
    drawStage();
    drawPanel();
    const wanted = state.current;
    try {
      const model = await api.get('/deck/slides/' + wanted, null, { fresh: true });
      if (state.current !== wanted) return;
      state.model = model;
      if (model.imageUrl) slideMeta(wanted).imageUrl = model.imageUrl;
    } catch (err) {
      if (state.current === wanted) { clear(stage).appendChild(errorBox(err)); }
      return;
    }
    drawStage();
    drawPanel();
  }

  function toggleEdit() {
    state.editing = !state.editing;
    state.selected = null;
    drawToolbar();
    drawStage();
    drawPanel();
  }

  async function save() {
    const edits = [...state.pending.entries()].map(([k, paragraphs]) => {
      const cut = k.indexOf(':');
      return { slide: Number(k.slice(0, cut)), shapeId: k.slice(cut + 1), paragraphs };
    });
    if (!edits.length) return;
    try {
      const res = await api.post('/deck/save', { edits });
      if (res.errors && res.errors.length) toast('Some edits were not saved: ' + res.errors.join('; '), 'bad');
      const saved = new Set((res.changed || []).map(c => key(c.slide, c.shapeId)));
      // Keep anything the server did not accept so the user's typing is not lost.
      [...state.pending.keys()].forEach(k => { if (saved.has(k) || !(res.errors && res.errors.length)) state.pending.delete(k); });
      toast(`Saved ${res.changed ? res.changed.length : 0} text change(s) to the deck.` + (res.rendering ? ' Slide pictures are being updated.' : ''));
      await refreshInfo();
      drawRail();
      await go(state.current);
      if (res.rendering) startPolling();
    } catch (err) {
      toast('Save failed: ' + err.message, 'bad');
    }
  }

  function approve() {
    const name = h('input', { class: 'input', placeholder: 'Your name', 'aria-label': 'Approved by' });
    const note = h('textarea', { class: 'input', rows: 3, placeholder: 'Note (optional)', 'aria-label': 'Note' });
    const warn = dirtyCount() ? h('div', { class: 'notes' }, `There are ${dirtyCount()} unsaved edit(s). They are not part of what you approve — save first if they should be.`) : null;
    const close = modal('Approve the deck', h('div', { style: { display: 'grid', gap: '10px' } },
      h('p', { class: 'muted' }, 'Approval is recorded with your name and the time. If the deck is edited afterwards it returns to draft.'),
      warn, h('label', { class: 'field' }, 'Approved by', name), h('label', { class: 'field' }, 'Note', note),
      h('div', { class: 'form-row' }, h('button', {
        type: 'button', class: 'btn good', onClick: async () => {
          if (!name.value.trim()) { name.focus(); return; }
          try {
            await api.post('/deck/approval/approve', { by: name.value.trim(), note: note.value.trim() || null });
            close();
            toast('Deck approved.');
            await refreshInfo();
            drawToolbar();
          } catch (err) { toast('Approval failed: ' + err.message, 'bad'); }
        }
      }, '✓ Approve'))));
    name.focus();
  }

  async function withdraw() {
    if (!confirm('Withdraw the approval? The deck returns to draft.')) return;
    try {
      await api.post('/deck/approval/withdraw', {});
      toast('Approval withdrawn.');
      await refreshInfo();
      drawToolbar();
    } catch (err) { toast('Could not withdraw: ' + err.message, 'bad'); }
  }

  async function showHistory() {
    const box = h('div', null, loading());
    modal('Deck history', box);
    try {
      const [approval, edits] = await Promise.all([api.get('/deck/approval', null, { fresh: true }), api.get('/deck/edits', null, { fresh: true })]);
      const when = t => (t ? new Date(t).toLocaleString('en-IN') : '—');
      const hist = approval.history || [];
      const log = (edits.edits || []).slice().reverse();
      put(clear(box), 
        h('h4', null, 'Approval'),
        hist.length
          ? h('div', { class: 'table-wrap', style: { margin: '8px 0 16px' } }, h('table', { class: 'table' },
              h('thead', null, h('tr', null, ['When', 'Action', 'By', 'Note'].map(t => h('th', null, t)))),
              h('tbody', null, hist.slice().reverse().map(e => h('tr', null, h('td', null, when(e.at)), h('td', null, ({ approved: 'Approved', returned_to_draft: 'Returned to draft', approval_withdrawn: 'Approval withdrawn' })[e.event] || e.event || '—'), h('td', null, e.by || '—'), h('td', null, e.note || e.reason || '—'))))))
          : h('p', { class: 'muted', style: { margin: '6px 0 16px' } }, 'No approvals recorded yet.'),
        h('h4', null, `Text edits (${log.length})`),
        log.length
          ? h('div', { class: 'table-wrap', style: { marginTop: '8px' } }, h('table', { class: 'table' },
              h('thead', null, h('tr', null, ['When', 'Slide', 'Before', 'After'].map(t => h('th', null, t)))),
              h('tbody', null, log.slice(0, 200).map(e => h('tr', null, h('td', null, when(e.at)), h('td', { class: 'num' }, e.slide),
                h('td', null, String(e.before === undefined ? '' : (Array.isArray(e.before) ? e.before.join(' ¶ ') : e.before)).slice(0, 160) || '—'),
                h('td', null, String(e.after === undefined ? '' : (Array.isArray(e.after) ? e.after.join(' ¶ ') : e.after)).slice(0, 160) || '—'))))))
          : h('p', { class: 'muted', style: { marginTop: '6px' } }, 'The deck has not been edited.'),
        state.info.hasEditsFromOriginal ? h('div', { class: 'form-row', style: { marginTop: '14px' } },
          h('button', { type: 'button', class: 'btn danger', onClick: resetDeck }, 'Discard all edits and restore the original deck')) : null);
    } catch (err) { clear(box).appendChild(errorBox(err)); }
  }

  async function resetDeck() {
    if (!confirm('Discard every saved edit and go back to the original deck? This cannot be undone.')) return;
    try {
      await api.post('/deck/reset', {});
      state.pending.clear();
      document.querySelectorAll('.modal-backdrop').forEach(m => m.remove());
      toast('The original deck has been restored.');
      await refreshAll();
    } catch (err) { toast('Reset failed: ' + err.message, 'bad'); }
  }

  // ---------------------------------------------------------------- refresh
  async function refreshInfo() {
    state.info = await api.get('/deck', null, { fresh: true });
  }

  async function refreshAll() {
    await refreshInfo();
    state.slides = (await api.get('/deck/slides', null, { fresh: true })).slides || [];
    drawRail();
    await go(Math.min(state.current, state.slides.length));
    if (state.info.render && state.info.render.state === 'rendering') startPolling();
  }

  // While slides are being rendered, pick up each picture as it becomes ready.
  function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const progress = await api.get('/deck/render/progress', null, { fresh: true });
        state.info.render = { ...(state.info.render || {}), ...progress };
        const fresh = (await api.get('/deck/slides', null, { fresh: true })).slides || [];
        let changed = false;
        fresh.forEach((s, i) => {
          if (state.slides[i] && state.slides[i].imageUrl !== s.imageUrl) { state.slides[i].imageUrl = s.imageUrl; changed = true; }
        });
        if (changed) {
          drawRail();
          const mine = slideMeta(state.current);
          if (mine && state.model && state.model.imageUrl !== mine.imageUrl) { state.model.imageUrl = mine.imageUrl; drawStage(); }
        }
        drawToolbar();
        if (progress.state !== 'rendering') {
          clearInterval(pollTimer);
          pollTimer = null;
          if (progress.error) toast('Slide pictures could not be updated: ' + progress.error, 'bad');
        }
      } catch (err) { /* keep polling; a single failed poll is not worth a message */ }
    }, 2500);
  }

  keyHandler = e => {
    if (e.target.closest('input, textarea, select, .modal')) return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown') go(state.current + 1);
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') go(state.current - 1);
  };
  document.addEventListener('keydown', keyHandler);

  const n = notes(state.info.notes);
  if (n) noteBox.appendChild(n);
  drawRail();
  await go(state.current);

  // First visit: ask the server to prepare any missing slide pictures.
  const render = state.info.render || {};
  if (state.info.renderer !== 'none' && (render.state === 'rendering' || render.slidesWithoutCurrentImage > 0)) {
    if (render.state !== 'rendering') { try { await api.post('/deck/render', {}); } catch (err) { /* shown by polling */ } }
    state.info.render = { ...render, state: 'rendering', total: render.total || render.slidesWithoutCurrentImage, done: render.done || 0 };
    drawToolbar();
    startPolling();
  }
}
