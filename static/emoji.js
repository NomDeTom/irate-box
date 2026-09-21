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
  let left = r.right + window.scrollX - w;
  if (left < 8) left = 8;
  panel.style.left = `${left}px`;
  panel.style.top = `${r.bottom + window.scrollY + 6}px`;
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
  const wrap = document.createElement('span');
  wrap.className = 'emoji-wrap';
  field.parentNode.insertBefore(wrap, field);
  wrap.appendChild(field);
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
  wrap.appendChild(btn);
}

buildPanel();
document.querySelectorAll('[data-emoji]').forEach(attach);
