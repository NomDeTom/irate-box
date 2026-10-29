// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The light web flasher (/flasher/): each ESP32 build published from the Firmware Factory with ESP
// Web Tools' install button, which reads its manifest from the hub (flasher.py). Flashing needs Web
// Serial, which needs a secure context: over plain HTTP the page points at the HTTPS address, and in
// a browser without it says which would do.
(() => {
  const can = document.getElementById('flash-can');
  const list = document.getElementById('flash-list');
  const el = (tag, props = {}, ...kids) => { const node = Object.assign(document.createElement(tag), props); node.append(...kids.filter(Boolean)); return node; };

  // What stands in the way, in words, or null when nothing does. info: what /certificate.json says of HTTPS.
  function blocker(info) {
    if (!window.isSecureContext) {
      if (info && !info.on) {
        return el('span', { textContent: 'Flashing from the browser needs HTTPS, and this box does not serve HTTPS. An administrator can enable it at Administration → Security → HTTPS.' });
      }
      const https = httpsHere(info && info.port);
      return el('span', {}, 'Open this page over HTTPS: ', el('a', { href: https, textContent: https }),
        '. Flashing from the browser needs HTTPS. If this box has no HTTPS, an administrator can enable it at Administration → Security → HTTPS.');
    }
    if (!('serial' in navigator)) return el('span', { textContent: 'Unsupported browser. This browser cannot use Web Serial. Use Chrome or Edge on a computer.' });
    return null;
  }

  function row(t, ready) {
    const what = el('span', {}, el('strong', { textContent: t.name }), ` · ${t.chip} · ${t.version.replace(/-built$/, '')}${t.built ? ' · built on this box' : ' · cached upstream release'}`);
    if (!ready) return el('li', {}, what);
    const btn = document.createElement('esp-web-install-button');
    btn.setAttribute('manifest', `/flasher/api/esp/${encodeURIComponent(t.version)}/${encodeURIComponent(t.env)}/manifest.json`);
    btn.append(el('button', { type: 'button', slot: 'activate', className: 'action-btn', textContent: 'Install' }),
      el('span', { slot: 'unsupported', className: 'setting-desc', textContent: 'Unsupported browser. Use Chrome or Edge on a computer.' }),
      el('span', { slot: 'not-allowed', className: 'setting-desc', textContent: 'Open this page over HTTPS.' }));
    return el('li', {}, what, ' ', btn);
  }

  async function load() {
    let data;
    try {
      const response = await fetch('/flasher/api/esp');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      data = await response.json();
    } catch (error) {
      can.textContent = `The hub server did not answer (${error.message}).`;
      return;
    }
    document.getElementById('flash-download').hidden = !data.download;
    const stop = blocker(window.isSecureContext ? null : await httpsInfo());
    const ready = !stop && data.engine && data.targets.length > 0;
    if (ready) {
      try { await import('/flasher/esp-web-tools/install-button.js'); } catch (error) { can.textContent = `Flasher component unavailable (${error.message}).`; return; }
    }
    if (!data.targets.length) {
      can.textContent = 'No firmware available. ESP32 builds appear here after an administrator publishes a build (Administration → Firmware Factory → Publish firmware). They also appear after the librarian keeps Meshtastic releases (Administration → Web flasher → Firmware).';
    } else if (stop) {
      can.replaceChildren(stop);
    } else if (!data.engine) {
      can.textContent = 'Flasher component unavailable. ESP Web Tools is not on this box. Run the installer again with internet access to download it.';
    } else {
      can.textContent = `Firmware available: ${data.targets.length} build${data.targets.length === 1 ? '' : 's'}`;
    }
    list.replaceChildren(...data.targets.map((t) => row(t, ready)));
  }
  load();
})();
