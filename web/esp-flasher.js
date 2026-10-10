// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The light web flasher (/flasher/): each ESP32 build published from the Firmware Factory with ESP
// Web Tools' install button, which reads its manifest from the hub (flasher.py). Flashing needs Web
// Serial, which needs a secure context: over plain HTTP the page points at the HTTPS address, and in
// a browser without it says which would do.
(() => {
  const can = document.getElementById('flash-can');
  const list = document.getElementById('flash-list');
  const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids.filter(Boolean)); return e; };

  // What stands in the way, in words, or '' when nothing does.
  function blocker() {
    if (!window.isSecureContext) {
      const https = `https://${location.host}${location.pathname}`;
      return el('span', {}, 'Flashing from the browser needs this page over HTTPS: ', el('a', { href: https, textContent: https }),
        ' (when the box has HTTPS on: Admin → Certificate).');
    }
    if (!('serial' in navigator)) return el('span', { textContent: 'This browser cannot reach a USB port: use Chrome or Edge on a computer.' });
    return null;
  }

  function row(t, ready) {
    const what = el('span', {}, el('strong', { textContent: t.name }), ` · ${t.chip} · ${t.version.replace(/-built$/, '')}`);
    if (!ready) return el('li', {}, what);
    const btn = document.createElement('esp-web-install-button');
    btn.setAttribute('manifest', `/flasher/api/esp/${encodeURIComponent(t.version)}/${encodeURIComponent(t.env)}/manifest.json`);
    btn.append(el('button', { type: 'button', slot: 'activate', className: 'action-btn', textContent: 'Install' }),
      el('span', { slot: 'unsupported', className: 'setting-desc', textContent: 'This browser cannot flash: use Chrome or Edge on a computer.' }),
      el('span', { slot: 'not-allowed', className: 'setting-desc', textContent: 'Needs the page over HTTPS.' }));
    return el('li', {}, what, ' ', btn);
  }

  async function load() {
    let data;
    try {
      const r = await fetch('/flasher/api/esp');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e) {
      can.textContent = `The hub did not answer (${e.message}).`;
      return;
    }
    document.getElementById('flash-download').hidden = !data.download;
    const stop = blocker();
    const ready = !stop && data.engine && data.targets.length > 0;
    if (ready) {
      try { await import('/flasher/esp-web-tools/install-button.js'); } catch (e) { can.textContent = `The install button did not load (${e.message}).`; return; }
    }
    if (!data.targets.length) {
      can.textContent = 'Nothing to flash yet: ESP32 builds appear here once they are published from the Firmware Factory (Admin → Firmware Factory, a build\'s "Publish to the web flasher").';
    } else if (stop) {
      can.replaceChildren(stop);
    } else if (!data.engine) {
      can.textContent = 'The install button (ESP Web Tools) is not on this box: running the installer again with internet fetches it.';
    } else {
      can.textContent = `${data.targets.length} build${data.targets.length === 1 ? '' : 's'} to install:`;
    }
    list.replaceChildren(...data.targets.map((t) => row(t, ready)));
  }
  load();
})();
