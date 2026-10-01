// Shared page shell: remember whether the services block is collapsed.
const services = document.querySelector('details.services');
if (services) {
  try {
    if (localStorage.getItem('services-collapsed') === '1') services.open = false;
  } catch (_) {}
  services.addEventListener('toggle', () => {
    try { localStorage.setItem('services-collapsed', services.open ? '0' : '1'); } catch (_) {}
  });
}

// Theme: "light" / "dark" pin the palette via data-theme; "auto" removes it and lets
// prefers-color-scheme decide. Applied before first paint by the inline snippet in <head>.
const picker = document.querySelector('.theme-picker');
if (picker) {
  const buttons = picker.querySelectorAll('[data-theme-choice]');
  function showTheme() {
    let t = 'auto';
    try { t = localStorage.getItem('theme') || 'auto'; } catch (_) {}
    if (t !== 'light' && t !== 'dark') t = 'auto';
    buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.themeChoice === t)));
  }
  picker.addEventListener('click', (e) => {
    const b = e.target.closest('[data-theme-choice]');
    if (!b) return;
    const t = b.dataset.themeChoice;
    if (t === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    try {
      if (t === 'auto') localStorage.removeItem('theme'); else localStorage.setItem('theme', t);
    } catch (_) {}
    showTheme();
    // app.html carries this choice into the framed app (app.js).
    document.dispatchEvent(new CustomEvent('hub-theme', { detail: t }));
  });
  showTheme();
}

// A choice made in another tab arrives as a storage event: follow it live, so an open
// admin page (or app) does not stay in the old theme until it is reloaded.
window.addEventListener('storage', (e) => {
  if (e.key !== 'theme' && e.key !== null) return;
  let t = 'auto';
  try { t = localStorage.getItem('theme') || 'auto'; } catch (_) {}
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  else { t = 'auto'; delete document.documentElement.dataset.theme; }
  if (picker) {
    picker.querySelectorAll('[data-theme-choice]').forEach(
      (b) => b.setAttribute('aria-pressed', String(b.dataset.themeChoice === t)));
  }
  document.dispatchEvent(new CustomEvent('hub-theme', { detail: t }));
});

// Service status: /status says whether Caddy is in front and which backends are
// listening. Anything carrying data-service -- the home tiles, the sub-page lists --
// is dimmed while its service is down, rather than left as a dead link.
const grid = document.querySelector('.service-grid');
if (grid || document.querySelector('[data-service]')) {
  const note = document.createElement('p');
  note.className = 'services-note';
  note.hidden = true;
  if (grid) grid.parentNode.appendChild(note);

  const fmt = (bytes) => {
    const gb = bytes / 2 ** 30;
    return gb >= 1 ? `${gb.toFixed(gb >= 10 ? 0 : 1)} GB` : `${Math.round(bytes / 2 ** 20)} MB`;
  };
  // One meter: the bar is how much is used, the text how much is left.
  function meter(name, free, total) {
    const el = document.getElementById(`${name}-meter`);
    if (!el || !(total > 0)) return;
    const used = 1 - free / total;
    document.getElementById(`${name}-text`).textContent = `${fmt(free)} free`;
    const bar = document.getElementById(`${name}-bar`);
    bar.style.width = `${Math.round(used * 100)}%`;
    bar.classList.toggle('high', used > 0.9);
    el.title = `${fmt(total - free)} of ${fmt(total)} used`;
    el.hidden = false;
  }

  async function refreshStatus() {
    let data;
    try {
      const r = await fetch('/status');
      if (!r.ok) return;
      data = await r.json();
    } catch (_) { return; }
    // Presence. The count is whoever has a page open; `joined` (dnsmasq leases) only
    // exists on the box, so it is shown as a second line when the hub can see it.
    const count = document.getElementById('people-count');
    if (count && typeof data.online === 'number') {
      count.textContent = data.online;
      const desc = document.getElementById('people-desc');
      desc.textContent = data.online === 1 ? 'user online' : 'users online';
      if (typeof data.joined === 'number') {
        desc.textContent += ` \u00b7 ${data.joined} on the WiFi`;
      }
    }

    // The escape hatch is an ordinary card -- greyed while its unit is off -- unless
    // the operator has hidden it in the admin options. A missing `settings` (an older
    // hub, or a /status that failed) leaves it visible, which is the safe default: a
    // greyed card tells the truth, where a silently missing one does not.
    const termCard = document.getElementById('term-card');
    if (termCard) {
      termCard.hidden = !!(data.settings && data.settings.show_term_card === false);
    }

    if (data.system) {
      meter('mem', data.system.mem_available, data.system.mem_total);
      meter('disk', data.system.disk_free, data.system.disk_total);
    }

    const byPath = new Map(data.services.map((s) => [s.path, s]));
    document.querySelectorAll('[data-service]').forEach((el) => {
      const svc = byPath.get(el.dataset.service);
      const down = !!svc && !svc.up;
      el.classList.toggle('down', down);
      el.title = !down ? '' : svc.state === 'missing' ? 'Not installed' : 'Not running';
    });
    if (!grid) return;
    if (!data.proxied) {
      note.textContent = 'Served without Caddy in front — the hub works, the apps are not reachable.';
      note.hidden = false;
    } else if (grid.querySelector('.service-card.down:not([hidden])')) {
      // Read back from the grid, not from the service list: only a tile that is
      // actually greyed should make the note claim there is one.
      note.textContent = 'Greyed-out services are not running.';
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }
  refreshStatus();
  setInterval(refreshStatus, 15000);
}

// Join tile: a QR of the address this page was loaded from -- 192.168.4.1 on the box,
// whatever it is here -- so the next person joins from the first one's screen.
const qrEl = document.getElementById('hub-qr');
if (qrEl && typeof qrcode === 'function') {
  const url = location.origin + '/';
  try {
    const q = qrcode(0, 'M');
    q.addData(url);
    q.make();
    qrEl.innerHTML = q.createSvgTag({ cellSize: 4, margin: 1, scalable: true });
    const label = document.getElementById('hub-qr-url');
    if (label) label.textContent = url.replace(/^http:\/\//, '');
  } catch (_) {}
}
