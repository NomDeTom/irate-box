// Shared identity and text rendering for the shoutbox and the board, so a guest looks
// the same wherever they post. Loaded after age.js (which provides esc) and before the
// page script that uses it.

// --- Author colour ----------------------------------------------------------
// Only a hue (0-359) is ever stored or sent; style.css supplies the saturation and
// lightness per palette, so a name stays legible in light and dark. An entry with no
// hue is coloured from its name, so renaming re-colours you while old posts keep
// whatever they were made with.
function hueFor(name) {
  const s = String(name ?? '');
  let h = 2166136261;                       // FNV-1a
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % 360;
}

// The hue to draw an entry with: the one it carries, else one hashed from its name.
function hueOf(entry, nameKey) {
  return Number.isFinite(entry.hue) ? entry.hue : hueFor(entry[nameKey]);
}

const HUE_PRESETS = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330];

let myHue = null;   // null = auto, i.e. hash the name
try {
  const raw = localStorage.getItem('shout-hue');
  const n = Number(raw);
  if (raw !== null && Number.isFinite(n)) myHue = ((n % 360) + 360) % 360;
} catch (_) {}

// Adds `hue` to a request body only when the guest actually picked one -- absent
// means "derive it from the name", which is what keeps renaming honest.
function withHue(body) {
  if (myHue !== null) body.hue = myHue;
  return body;
}

// --- Inline Markdown --------------------------------------------------------
// Inline only. GitHub's *block* grammar -- headings, tables, fences, lists -- has
// nothing to say in a chat line or a short post, and a real GFM parser is 40-100 KB of
// dependency on a box whose whole point is that it ships none. This is the subset chat
// clients actually use.
//
// Order matters, and it is a security property, not a style choice: the text is
// HTML-escaped FIRST (esc() in age.js) and every pattern below runs over the escaped
// string. So the only tags that can reach the DOM are the ones written here -- anything
// the guest typed is already &lt; by the time a regex sees it.
function mdInline(raw) {
  let s = esc(raw);

  // Code spans come out first and go back last, so their contents are never treated
  // as markup: `a ** b` stays literal.
  const spans = [];
  s = s.replace(/`([^`]+)`/g, (_, code) => `\u0000${spans.push(code) - 1}\u0000`);

  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  // CommonMark's left/right-flanking rule, in miniature: the opener must be followed
  // by a non-space and the closer preceded by one, so `2 * 3 * 4` stays arithmetic.
  s = s.replace(/(^|[^\w*_])([*_])(?=\S)([^*_]*[^\s*_])\2(?![\w*_])/g, '$1<em>$3</em>');

  // Autolink http(s) only -- never a bare scheme, so javascript: can't sneak in. The
  // URL is already escaped, so it cannot break out of the quoted href.
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<>()]+)/g,
                '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');

  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${spans[i]}</code>`);
}

// --- Hue picker -------------------------------------------------------------
// One shared panel for every swatch on the page: the board has two forms, and they
// must not disagree about what colour you are.
const swatches = [];
let huePanel = null;
let activeSwatch = null;

function paintSwatches() {
  for (const { swatch, dot, nameInput } of swatches) {
    dot.style.setProperty('--author-hue', myHue === null ? hueFor(nameInput.value.trim()) : myHue);
    swatch.title = myHue === null ? 'Name colour: from your name' : 'Name colour: chosen';
  }
}

function setHue(h) {
  myHue = h;
  try {
    if (h === null) localStorage.removeItem('shout-hue');
    else localStorage.setItem('shout-hue', String(h));
  } catch (_) {}
  paintSwatches();
  markPressed();
}

function markPressed() {
  if (!huePanel) return;
  huePanel.querySelectorAll('.hue-grid button').forEach((b) => {
    b.setAttribute('aria-pressed', String(myHue !== null && Number(b.dataset.hue) === myHue));
  });
  huePanel.querySelector('.hue-auto').setAttribute('aria-pressed', String(myHue === null));
}

function buildHuePanel() {
  huePanel = document.createElement('div');
  huePanel.className = 'hue-panel';
  huePanel.hidden = true;

  const grid = document.createElement('div');
  grid.className = 'hue-grid';
  for (const h of HUE_PRESETS) {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.hue = String(h);
    b.style.setProperty('--author-hue', h);
    b.title = `Hue ${h}`;
    b.setAttribute('aria-label', `Hue ${h}`);
    b.addEventListener('click', () => { setHue(h); closeHuePanel(); });
    grid.appendChild(b);
  }
  huePanel.appendChild(grid);

  const auto = document.createElement('button');
  auto.type = 'button';
  auto.className = 'hue-auto';
  auto.textContent = 'Auto (from name)';
  auto.addEventListener('click', () => { setHue(null); closeHuePanel(); });
  huePanel.appendChild(auto);

  document.addEventListener('click', (e) => {
    if (huePanel.hidden || huePanel.contains(e.target)) return;
    if (swatches.some(({ swatch }) => swatch.contains(e.target))) return;
    closeHuePanel();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeHuePanel(); });
  window.addEventListener('resize', () => { if (!huePanel.hidden) positionHuePanel(); });
  document.body.appendChild(huePanel);
}

function positionHuePanel() {
  const r = activeSwatch.getBoundingClientRect();
  const w = huePanel.offsetWidth;
  let left = r.left + window.scrollX;
  if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
  if (left < 8) left = 8;
  huePanel.style.left = `${left}px`;
  huePanel.style.top = `${r.bottom + window.scrollY + 6}px`;
}

function closeHuePanel() {
  if (huePanel) huePanel.hidden = true;
  activeSwatch = null;
}

// Wire a swatch button to the name field it belongs to. On auto the colour follows
// that field as it is typed.
function attachHuePicker(swatch, nameInput) {
  if (!swatch || !nameInput) return;
  const dot = swatch.querySelector('.dot');
  swatches.push({ swatch, dot, nameInput });
  swatch.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!huePanel.hidden && activeSwatch === swatch) { closeHuePanel(); return; }
    activeSwatch = swatch;
    huePanel.hidden = false;
    markPressed();
    positionHuePanel();
  });
  nameInput.addEventListener('input', paintSwatches);
  paintSwatches();
}

buildHuePanel();
