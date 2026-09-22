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
  });
  showTheme();
}

// Service status: /status says whether Caddy is in front and which backends are
// listening. Tiles for anything down are dimmed rather than left as dead links.
const grid = document.querySelector('.service-grid');
if (grid) {
  const note = document.createElement('p');
  note.className = 'services-note';
  note.hidden = true;
  grid.parentNode.appendChild(note);

  async function refreshStatus() {
    let data;
    try {
      const r = await fetch('/status');
      if (!r.ok) return;
      data = await r.json();
    } catch (_) { return; }
    const byPath = new Map(data.services.map((s) => [s.path, s.up]));
    grid.querySelectorAll('.service-card').forEach((card) => {
      const up = byPath.get(card.getAttribute('href'));
      card.classList.toggle('down', up === false);
      card.title = up === false ? 'Not running' : '';
    });
    if (!data.proxied) {
      note.textContent = 'Served without Caddy in front — the hub works, the apps are not reachable.';
      note.hidden = false;
    } else if (data.services.some((s) => !s.up)) {
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
