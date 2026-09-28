// The hub bar around a framed app. The fragment holds the app's own path, and follows
// the app as it navigates, so a reload or a shared link lands on the same page.
const frame = document.getElementById('app');
const title = document.getElementById('app-title');
const out = document.getElementById('app-out');

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

let shown = null;
function sync() {
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
frame.addEventListener('load', sync);
setInterval(sync, 1000);

// An edited address or a pasted link: point the frame at the new fragment.
window.addEventListener('hashchange', () => {
  if (target() !== shown) { shown = target(); frame.src = shown; }
});

shown = target();
frame.src = shown;
