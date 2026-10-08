// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Emoji picker for text inputs. No dependencies -- the hub is offline, so nothing is
// fetched. Attach with a `data-emoji` attribute on an <input> or <textarea>; a toggle
// button is added beside it and one shared panel is used for every field on the page.

const EMOJI_GROUPS = [
  ['Smileys', '😀😃😄😁😆😅🤣😂🙂🙃😉😊😇🥰😍🤩😘😋😛😜🤪😝🤑🤗🤭🤫🤔🫡🤐🤨😐😑😶😏😒🙄😬😮‍💨🤥😌😔😪🤤😴😷🤒🤕🤢🤮🥵🥶🥴😵🤯🤠🥳🥸😎🤓🧐😕😟🙁😮😯😲😳🥺😦😧😨😰😥😢😭😱😖😣😞😓😩😫🥱😤😡😠🤬💀☠️💩🤡👹👺👻👽🤖'],
  ['Gestures', '👋🤚🖐️✋🖖👌🤌🤏✌️🤞🤟🤘🤙👈👉👆🖕👇☝️👍👎✊👊🤛🤜👏🙌👐🤲🤝🙏💪🫶'],
  ['Hearts', '❤️🧡💛💚💙💜🖤🤍🤎💔❣️💕💞💓💗💖💘💝💯💢💥💫💦💨🔥⭐✨⚡'],
  ['Pirate', '🏴‍☠️☠️⚓🦜🗡️⚔️🔱🧭🗺️🏝️🏖️🌊🐙🦑🦈🐚⛵🚤🛶💰🪙💎🍺🍻🥃🍖🔭⏳🪝🦴🎲'],
  ['Things', '📡📻🔋🔌💡🔦🔧🔨🛠️⚙️🧰🔩📦📚📖✏️📝📎📌🔑🔒🔓⏰📶🛜💻🖥️📱🔊🔇🎵🎶🎉🎈🎁'],
  ['Nature', '🌞🌝🌚🌙☁️🌧️⛈️❄️☔🌈🌍🌲🌴🌵🌸🌻🍀🍄🐶🐱🐭🐸🐢🐍🦎🐟🐬🐋🦀🦞🐌🐛🦋🐝🐞'],
  ['Food', '🍎🍊🍋🍌🍉🍇🍓🥝🍅🥕🌽🥔🍞🧀🥚🍳🥓🍔🍟🍕🌭🌮🍜🍣🍦🍩🍪🎂🍫🍿☕🍵🧃🥤'],
];

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

function buildPanel() {
  panel = document.createElement('div');
  panel.className = 'emoji-panel';
  panel.hidden = true;
  for (const [label, chars] of EMOJI_GROUPS) {
    const h = document.createElement('div');
    h.className = 'emoji-group';
    h.textContent = label;
    panel.appendChild(h);
    const grid = document.createElement('div');
    grid.className = 'emoji-grid';
    for (const ch of graphemes(chars)) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = ch;
      grid.appendChild(b);
    }
    panel.appendChild(grid);
  }
  panel.addEventListener('click', (e) => {
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
