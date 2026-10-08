// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
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

// Theme picker, built from themes.js (window.HubThemes, loaded in every page's <head>, which
// has already drawn the page in the saved theme). Light, Dark and Auto (the OS's choice) are
// the main row; 🎨 opens the other themes. app.html carries a change into the framed app
// through the hub-theme event (app.js).
const picker = document.querySelector('.theme-picker');
const themes = window.HubThemes;
if (picker && themes) {
  const btn = (attrs, text) => {
    const b = document.createElement('button');
    b.type = 'button';
    for (const [k, v] of Object.entries(attrs)) b.setAttribute(k, v);
    b.textContent = text;
    return b;
  };
  const main = themes.list.filter((t) => t.main);
  const others = themes.list.filter((t) => !t.main);
  const label = document.createElement('span');
  label.className = 'theme-label';
  label.setAttribute('aria-hidden', 'true');
  label.textContent = 'Theme';
  const row = [label,
    ...main.map((t) => btn({ 'data-theme-choice': t.id, title: t.label }, t.emoji)),
    btn({ 'data-theme-choice': 'auto', title: 'Follow this device (light or dark)' }, 'Auto')];
  let more = null;
  let menu = null;
  if (others.length) {
    more = btn({ class: 'theme-more', title: 'More themes', 'aria-haspopup': 'menu', 'aria-expanded': 'false' }, '🎨');
    menu = document.createElement('div');
    menu.className = 'theme-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    for (const t of others) {
      const item = btn({ role: 'menuitemradio', 'data-theme-choice': t.id }, '');
      const name = document.createElement('span');
      name.className = 'theme-menu-name';
      name.textContent = `${t.emoji} ${t.label}`;
      item.append(name);
      if (t.desc) {
        const desc = document.createElement('span');
        desc.className = 'theme-menu-desc';
        desc.textContent = t.desc;
        item.append(desc);
      }
      menu.append(item);
    }
    row.push(more, menu);
  }
  picker.replaceChildren(...row);

  const openMenu = (open) => {
    if (!menu) return;
    menu.hidden = !open;
    more.setAttribute('aria-expanded', String(open));
    if (open) (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button')).focus();
  };
  const showChoice = () => {
    const cur = themes.current();
    picker.querySelectorAll('[data-theme-choice]').forEach((b) => {
      const on = b.dataset.themeChoice === cur;
      b.setAttribute(b.getAttribute('role') === 'menuitemradio' ? 'aria-checked' : 'aria-pressed', String(on));
    });
    if (more) {
      const other = others.find((t) => t.id === cur);
      more.textContent = other ? other.emoji : '🎨';
      more.title = other ? `Theme: ${other.label} (more themes)` : 'More themes';
      more.setAttribute('aria-pressed', String(!!other));
    }
  };
  picker.addEventListener('click', (e) => {
    if (more && e.target.closest('.theme-more')) { openMenu(menu.hidden); return; }
    const b = e.target.closest('[data-theme-choice]');
    if (!b) return;
    themes.choose(b.dataset.themeChoice);
    openMenu(false);
    showChoice();
  });
  if (menu) {
    document.addEventListener('click', (e) => { if (!picker.contains(e.target)) openMenu(false); });
    picker.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !menu.hidden) { openMenu(false); more.focus(); }
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !menu.hidden) {
        const items = [...menu.querySelectorAll('button')];
        const i = items.indexOf(document.activeElement);
        items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
        e.preventDefault();
      }
    });
  }
  showChoice();
  document.addEventListener('hub-theme', showChoice);
}

// A choice made in another tab arrives as a storage event: follow it live, so an open
// admin page (or app) does not stay in the old theme until it is reloaded.
window.addEventListener('storage', (e) => {
  if (!themes || (e.key !== 'theme' && e.key !== 'hub-theme' && e.key !== null)) return;
  const t = themes.current();
  themes.show(t);
  document.dispatchEvent(new CustomEvent('hub-theme', { detail: t }));
});

// Service status: /status says whether the web server is in front and which backends are
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

    // People wide or large (M8, F5): different devices today and this week (M11), said plainly.
    const people = document.getElementById('people-card');
    if (people && (people.dataset.size === 'wide' || people.dataset.size === 'large')) {
      let more = document.getElementById('people-more');
      if (!more) {
        more = document.createElement('span');
        more.id = 'people-more';
        more.className = 'desc people-more';
        const how = document.createElement('details');
        how.className = 'field-help';
        how.innerHTML = '<summary>How is this counted?</summary><p>Each device\u2019s network address is hashed with a random salt made for the day, and another for the week, held only in a helper\u2019s memory and thrown away when the day or week ends. Only the counts are kept. Phones that use a random address may count more than once, so the numbers are approximate.</p>';
        people.append(more, how);
      }
      const v = data.visitors;
      more.textContent = (typeof data.signed_in === 'number' ? `Signed in: ${data.signed_in}` : '')
        + (v ? `${typeof data.signed_in === 'number' ? ' \u00b7 ' : ''}Seen today \u2248 ${v.day} \u00b7 this week \u2248 ${v.week}` : '');
      people.querySelector('.field-help').hidden = !v;
      // The names: only of those who said yes, and only to whom the owner allows (M12).
      let names = document.getElementById('people-names');
      if (!names) { names = document.createElement('span'); names.id = 'people-names'; names.className = 'people-names'; more.after(names); }
      const shown = Array.isArray(data.names) ? data.names : null;
      names.replaceChildren(...(shown || []).map((n) => { const s = document.createElement('span'); s.className = 'info-pill'; s.textContent = n; return s; }));
      if (shown && data.signed_in > shown.length) names.append(` +${data.signed_in - shown.length} not shown (their choice)`);
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
      el.title = !down ? '' : svc.state === 'missing' ? 'Not installed' : svc.why || 'Not running';
    });
    if (!grid) return;
    if (!data.proxied) {
      note.textContent = 'Served without the web server in front — the hub works, the apps are not reachable.';
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

// The Firmware Factory's tile (the owner shows it: /admin → Firmware Factory): what is building and
// how far along, what is waiting, and the newest builds' files to download (ESP32 images and zips,
// UF2s). Download only: flashing stays with the web flasher.
const factoryCard = document.getElementById('factory-card');
if (factoryCard) {
  const fNow = document.getElementById('factory-now');
  const fBar = document.getElementById('factory-bar');
  const fDone = document.getElementById('factory-progress');
  const fFiles = document.getElementById('factory-downloads');
  const kind = (f) => (f.endsWith('.uf2') ? 'UF2' : f.endsWith('.factory.bin') ? 'factory .bin' : f.endsWith('.zip') ? '.zip'
    : f.startsWith('littlefs') ? 'littlefs .bin' : '.bin');
  const refreshFactory = async () => {
    let d;
    try {
      const r = await fetch('/factory.json');
      if (!r.ok) return;
      d = await r.json();
    } catch (_) { return; }
    const run = d.running;
    const waiting = d.waiting ? `; ${d.waiting} waiting` : '';
    fNow.textContent = run ? `Building ${run.name} (${run.family})${run.done != null ? `, about ${Math.round(run.done * 100)} %` : ''}${waiting}`
      : d.paused ? `Paused${waiting}` : d.waiting ? `${d.waiting} waiting` : 'Nothing building now';
    fBar.hidden = !(run && run.done != null);
    if (run && run.done != null) fDone.style.width = `${Math.round(run.done * 100)}%`;
    fFiles.replaceChildren(...d.built.map((b) => {
      const row = document.createElement('span');
      row.className = 'factory-build';
      row.append(document.createTextNode(`${b.name} `));
      b.files.forEach((f) => {
        const a = document.createElement('a');
        a.href = `/factory/file?run=${encodeURIComponent(b.run)}&name=${encodeURIComponent(f)}`;
        a.download = f;
        a.title = `${f} (${b.ref})`;
        a.textContent = kind(f);
        row.append(a, document.createTextNode(' '));
      });
      return row;
    }));
  };
  refreshFactory();
  setInterval(refreshFactory, 30000);
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

// The hub bar's 🛟: a short "this is local, don't panic" note over the app. Closes on
// Escape, a click elsewhere in the bar, or a click into the app -- which, being a frame,
// shows up here only as this window losing focus.
const helpButton = document.getElementById('app-help');
const helpPop = document.getElementById('help-pop');
if (helpButton && helpPop) {
  const setHelp = (open) => {
    helpPop.hidden = !open;
    helpButton.setAttribute('aria-expanded', String(open));
  };
  helpButton.addEventListener('click', (e) => {
    e.stopPropagation();
    setHelp(helpPop.hidden);
  });
  document.addEventListener('click', (e) => { if (!helpPop.contains(e.target)) setHelp(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setHelp(false); });
  window.addEventListener('blur', () => setHelp(false));
}
