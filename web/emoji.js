// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Emoji picker for text inputs. No dependencies, and nothing from the internet: the list is the
// hub's own emoji-data.js (Unicode's emoji up to 13.1, in Unicode's groups, made by
// tools/make-emoji-data.cjs), loaded from the hub the first time a panel opens. Attach with a
// `data-emoji` attribute on an <input> or <textarea>; a toggle button is added beside it and one
// shared panel is used for every field on the page: a search, a skin tone, a tab per group.

// The hub's own tab, beside Unicode's groups.
const PIRATE = ['Pirate', '🏴‍☠️', '🏴‍☠️☠️⚓🦜🗡️⚔️🔱🧭🗺️🏝️🏖️🌊🐙🦑🦈🐚⛵🚤🛶💰🪙💎🍺🍻🥃🍖🔭⏳🪝🦴🎲'];
const TONE_HANDS = ['✋', '✋🏻', '✋🏼', '✋🏽', '✋🏾', '✋🏿'];
const TONE_WORDS = ['No skin tone', 'Light', 'Medium-light', 'Medium', 'Medium-dark', 'Dark'];
const TONE_KEY = 'irate-emoji-tone';
const SHOWN_AT_MOST = 150;   // search results drawn at once
const DATA_URL = ((document.currentScript && document.currentScript.src) || '/emoji.js').replace(/emoji\.js(\?.*)?$/, 'emoji-data.js');

// Split a string into emoji, keeping ZWJ sequences and variation selectors together.
const segmenter = typeof Intl !== 'undefined' && Intl.Segmenter
  ? new Intl.Segmenter('en', { granularity: 'grapheme' })
  : null;
function graphemes(s) {
  if (segmenter) return Array.from(segmenter.segment(s), (x) => x.segment);
  return Array.from(s); // fallback: code points; ZWJ sequences may split on very old browsers
}

let panel = null;
let target = null;   // the field the panel is currently inserting into
let toggle = null;   // the button that opened it
let groups = null;   // [[label, icon, [[emoji, name, tones?]]]], once emoji-data.js has loaded
let shown = 0;       // the tab showing
let tone = 0;        // 0 none, 1–5 light to dark
try { tone = Math.min(5, Math.max(0, Number(localStorage.getItem(TONE_KEY)) || 0)); } catch (_) { /* no storage */ }
const parts = {};

const make = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const withTone = (e) => (tone && e[2] ? e[2][tone - 1] : e[0]);

function loadData() {
  if (window.EMOJI_DATA) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = make('script', { src: DATA_URL, onload: resolve, onerror: () => reject(new Error('emoji-data.js')) });
    document.head.appendChild(s);
  });
}

function drawTones() {
  parts.tones.replaceChildren(...TONE_HANDS.map((hand, i) => {
    const b = make('button', { type: 'button', textContent: hand, title: TONE_WORDS[i], className: i === tone ? 'on' : '' });
    b.setAttribute('aria-pressed', String(i === tone));
    // Redrawn on a click, so the click goes no further: the page's "outside the panel" would close it.
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      tone = i;
      try { localStorage.setItem(TONE_KEY, String(i)); } catch (_) { /* no storage */ }
      drawTones();
      drawGrid();
    });
    return b;
  }));
}

function drawTabs() {
  parts.tabs.replaceChildren(...groups.map(([label, icon], i) => {
    const b = make('button', { type: 'button', textContent: icon, title: label, className: i === shown ? 'on' : '' });
    b.setAttribute('aria-pressed', String(i === shown));
    b.setAttribute('aria-label', label);
    b.addEventListener('click', (e) => { e.stopPropagation(); shown = i; parts.search.value = ''; drawTabs(); drawGrid(); parts.grid.scrollTop = 0; });
    return b;
  }));
}

function drawGrid() {
  if (!groups) return;
  const q = parts.search.value.trim().toLowerCase();
  let list;
  if (q) {
    const found = groups.flatMap((g) => g[2]).filter((e) => e[1] && e[1].includes(q));
    list = found.slice(0, SHOWN_AT_MOST);
    parts.head.textContent = found.length ? `${found.length} found${found.length > list.length ? `, the first ${list.length} shown` : ''}` : 'None found';
  } else {
    list = groups[shown][2];
    parts.head.textContent = groups[shown][0];
  }
  parts.grid.replaceChildren(...list.map((e) => make('button', { type: 'button', textContent: withTone(e), title: e[1] || '' })));
}

function buildPanel() {
  parts.search = make('input', { type: 'search', placeholder: 'Search emoji', className: 'emoji-search' });
  parts.search.setAttribute('aria-label', 'Search emoji by name');
  parts.search.addEventListener('input', drawGrid);
  parts.tones = make('div', { className: 'emoji-tones', role: 'group' });
  parts.tones.setAttribute('aria-label', 'Skin tone');
  parts.tabs = make('div', { className: 'emoji-tabs', role: 'group' });
  parts.tabs.setAttribute('aria-label', 'Emoji groups');
  parts.head = make('div', { className: 'emoji-group', textContent: 'Loading…' });
  parts.grid = make('div', { className: 'emoji-grid' });
  panel = make('div', { className: 'emoji-panel', hidden: true },
    make('div', { className: 'emoji-top' }, parts.search, parts.tones), parts.tabs, parts.head, parts.grid);
  drawTones();
  parts.grid.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b && target) insert(target, b.textContent);
  });
  document.addEventListener('click', (e) => {
    if (!panel.hidden && !panel.contains(e.target) && e.target !== toggle) close();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  window.addEventListener('resize', () => { if (!panel.hidden) position(); });
  document.body.appendChild(panel);
}

// The first time a panel opens: Unicode's groups from emoji-data.js, then the hub's own.
function fill() {
  if (groups) return;
  loadData().then(() => {
    const pirate = [PIRATE[0], PIRATE[1], graphemes(PIRATE[2]).map((ch) => [ch, ''])];
    groups = [...window.EMOJI_DATA.groups, pirate];
    drawTabs();
    drawGrid();
    if (!panel.hidden) position();
  }, () => { parts.head.textContent = 'The emoji list did not load: type or paste one instead.'; });
}

function insert(field, ch) {
  const max = field.maxLength > 0 ? field.maxLength : Infinity;
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  if (field.value.length - (end - start) + ch.length > max) return;
  field.setRangeText(ch, start, end, 'end');
  field.dispatchEvent(new Event('input', { bubbles: true }));
  field.focus();
}

function position() {
  const r = toggle.getBoundingClientRect();
  const w = panel.offsetWidth;
  const h = panel.offsetHeight;
  // Right-aligned to the toggle when it is at a field's right edge (shoutbox);
  // left-aligned when that would run off the left (the board's toggle beside Send).
  let left = r.right - w;
  if (left < 8) left = r.left;
  left = Math.max(8, Math.min(left, document.documentElement.clientWidth - w - 8));
  panel.style.left = `${left + window.scrollX}px`;
  // The send rows sit at the foot of the page, so opening downward usually runs off
  // the bottom of the screen. Open upward when there is not room below and there is
  // more above; either way, keep the whole panel inside the viewport.
  const below = window.innerHeight - r.bottom - 6;
  const above = r.top - 6;
  let top = below < h && above > below ? r.top - 6 - h : r.bottom + 6;
  top = Math.max(8, Math.min(top, window.innerHeight - h - 8));
  panel.style.top = `${top + window.scrollY}px`;
}

function open(field, btn) {
  target = field;
  toggle = btn;
  panel.hidden = false;
  fill();
  position();
}

function close() {
  if (panel) panel.hidden = true;
  target = null;
  toggle = null;
}

function attach(field) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'emoji-toggle';
  btn.title = 'Insert emoji';
  btn.setAttribute('aria-label', 'Insert emoji');
  btn.textContent = '😊';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!panel.hidden && target === field) close(); else open(field, btn);
  });
  // data-emoji="#send": the toggle stands on its own just before that element (the
  // form's Send button) and the field keeps its full width. A bare data-emoji fuses
  // the toggle to the field's right edge, as the one-line shoutbox wants.
  const beside = field.dataset.emoji && document.querySelector(field.dataset.emoji);
  if (beside) {
    btn.classList.add('standalone');
    beside.parentNode.insertBefore(btn, beside);
    return;
  }
  const wrap = document.createElement('span');
  wrap.className = 'emoji-wrap';
  field.parentNode.insertBefore(wrap, field);
  wrap.appendChild(field);
  wrap.appendChild(btn);
}

buildPanel();
document.querySelectorAll('[data-emoji]').forEach(attach);
// For fields drawn after the page loads (/admin's tile icon).
window.EMOJI = { attach };
