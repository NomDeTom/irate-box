// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hub bar around a framed app. The fragment holds the app's own path, and follows
// the app as it navigates, so a reload or a shared link lands on the same page.
const frame = document.getElementById('app');
const title = document.getElementById('app-title');
const out = document.getElementById('app-out');

// --- one theme for every app -------------------------------------------------------
// The bar's picker (hub.js) sets the hub's own "theme" key, which Docusaurus books in
// Kiwix read too. The other apps keep their own setting, and Chrome does not pass the
// frame's color-scheme through to prefers-color-scheme inside it, so write each one
// to match: before the app first reads it, and again, with a reload, on every change.
// SilverBullet keeps its choice in its own client state, so it only follows on Auto;
// Wikipedia pages, the serial terminal and ttyd have no theme to set.
const APP_THEME_KEYS = [
  ['excalidraw-theme', { light: 'light', dark: 'dark', auto: 'system' }],   // Excalidraw
  ['mode-watcher-mode', { light: 'light', dark: 'dark', auto: 'system' }],  // Mermaid
  ['nomdetom-theme-mode', { light: 'light', dark: 'dark', auto: 'auto' }],  // calculators
];

// What the apps are told: the chosen theme's base (themes.js), light, dark or auto.
function hubTheme() {
  return window.HubThemes ? window.HubThemes.base() : 'auto';
}

function pushTheme() {
  const t = hubTheme();
  for (const [key, values] of APP_THEME_KEYS) {
    try { localStorage.setItem(key, values[t]); } catch (_) {}
  }
}

pushTheme();
document.addEventListener('hub-theme', () => {
  pushTheme();
  try { frame.contentWindow.location.reload(); } catch (_) { frame.src = target(); }
});

// Same-origin paths only: "/wiki/…" yes, "//elsewhere" or "https://…" no.
function target() {
  const p = decodeURIComponent(location.hash.slice(1));
  return p.startsWith('/') && !p.startsWith('//') ? p : '/';
}

function inner() {
  try {
    const l = frame.contentWindow.location;
    const kiwix = kiwixPage(l.pathname);
    if (kiwix) return kiwix;
    return { path: l.pathname + l.search + l.hash, name: frame.contentDocument.title };
  } catch (_) { return null; }  // left the origin; nothing to mirror
}

// Kiwix's /wiki/viewer is a frame of its own: the book is in #content_iframe, and the
// viewer only copies that frame's address into its #book/path on a load event. A
// single-page book (Docusaurus) changes page with pushState, which fires none, so read
// the book's frame directly and spell the address the way the viewer accepts it.
function kiwixPage(pathname) {
  if (!pathname.endsWith('/viewer')) return null;
  const content = frame.contentDocument.getElementById('content_iframe');
  if (!content) return null;
  const root = pathname.slice(0, -'/viewer'.length);  // "/wiki"
  const l = content.contentWindow.location;
  const prefix = `${root}/content/`;
  if (!l.pathname.startsWith(prefix)) return null;    // viewer's own pages: search, etc.
  return {
    // No l.hash: the viewer's fragment is already #book/path and takes no second one.
    path: `${pathname}#${l.pathname.slice(prefix.length)}${l.search}`,
    name: content.contentDocument.title,
  };
}

// --- the bar slides away while the app scrolls down, and back on any scroll up -----
// Scroll events do not bubble, but a capturing listener on a document sees every
// scroll inside it: the window and inner panels alike (SilverBullet scrolls a div).
// Actual scroll positions, not wheel or touch gestures, so zooming Excalidraw's
// canvas, which never scrolls, leaves the bar alone.
const bar = document.getElementById('app-bar');
const HIDE_AFTER = 30;  // px of downward travel before the bar goes
const SHOW_AFTER = 10;  // px of upward travel before it comes back
const SETTLE_MS = 400;  // hiding resizes the frame, which moves scroll positions: ignore those
const watched = new WeakSet();
const lastY = new WeakMap();
let travel = 0;
let quietUntil = 0;

function setHidden(hide) {
  if (bar.classList.contains('hidden') === hide) return;
  bar.classList.toggle('hidden', hide);
  travel = 0;
  quietUntil = Date.now() + SETTLE_MS;
}

function onScroll(e) {
  const el = e.target.scrollingElement || e.target;  // a document scrolls its root
  const y = el.scrollTop;
  if (typeof y !== 'number') return;
  const prev = lastY.get(el);
  lastY.set(el, y);
  if (prev === undefined || Date.now() < quietUntil) return;
  if (y <= 0) { setHidden(false); return; }
  const dy = y - prev;
  travel = Math.sign(dy) === Math.sign(travel) ? travel + dy : dy;
  if (travel > HIDE_AFTER) setHidden(true);
  else if (travel < -SHOW_AFTER) setHidden(false);
}

// Every same-origin document in the frame, nested ones included (Kiwix's viewer holds
// the book in a frame of its own). New pages are new documents, so this re-runs.
function watch(doc, depth = 0) {
  try {
    if (!doc) return;
    if (!watched.has(doc)) {
      watched.add(doc);
      doc.addEventListener('scroll', onScroll, { capture: true, passive: true });
    }
    if (depth < 2) doc.querySelectorAll('iframe').forEach((f) => watch(f.contentDocument, depth + 1));
  } catch (_) {}  // a cross-origin frame: nothing to watch
}

let shown = null;
function sync() {
  try { watch(frame.contentDocument); } catch (_) {}
  const cur = inner();
  if (!cur || cur.path === 'blank') return;
  // Navigating back to the hub itself inside the frame: drop the frame instead.
  if (cur.path === '/' || cur.path === '/index.html') { location.replace('/'); return; }
  if (cur.path !== shown) {
    shown = cur.path;
    history.replaceState(null, '', `#${cur.path}`);
    out.href = cur.path;
  }
  title.textContent = cur.name;
  document.title = cur.name ? `${cur.name} · Hub` : 'Hub';
}

// Loads catch ordinary links; the interval catches single-page apps (SilverBullet,
// Kiwix's viewer), which change page with pushState and fire no event out here.
frame.addEventListener('load', () => { setHidden(false); sync(); });
setInterval(sync, 1000);

// An edited address or a pasted link: point the frame at the new fragment.
window.addEventListener('hashchange', () => {
  if (target() !== shown) { shown = target(); frame.src = shown; }
});

shown = target();
frame.src = shown;

// --- ↑ the list page the app was opened from --------------------------------------
// A list page's links carry ?from=<list id> on app.html itself (server.py menu_page), so it
// lasts through the app's own navigation (only the fragment changes), a reload, or a link
// passed on. The list's title and address come from the hub (/menus.json).
const up = document.getElementById('app-up');
const from = new URLSearchParams(location.search).get('from');
if (up && from && /^[a-z0-9-]+$/.test(from)) {
  fetch('/menus.json').then((r) => (r.ok ? r.json() : {})).then((lists) => {
    const list = lists[from];
    if (!list) return;
    up.href = list.href;
    up.textContent = `↑ ${list.title}`;
    up.title = `Back to the ${list.title} list`;
    up.hidden = false;
  }).catch(() => {});
}
