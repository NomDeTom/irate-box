// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The light web flasher's page (/flasher/, esp-flasher.js) in jsdom, static: each ESP32 build listed,
// and what stands in the way said: plain HTTP (with the HTTPS address), a browser without Web Serial,
// nothing published yet, the install button missing; the Meshtastic download offered when it is there.
// Loading ESP Web Tools itself needs a real browser. Usage: [JSDOM=…/jsdom] node dom-esp-flasher.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const html = fs.readFileSync(`${WEB}/esp-flasher.html`, 'utf8');
const js = fs.readFileSync(`${WEB}/esp-flasher.js`, 'utf8');
const two = [{ version: '2.9.0.1234567-built', env: 'heltec-v3', name: 'Meshtastic for heltec-v3', chip: 'ESP32-S3', parts: 3 },
  { version: '2.9.0.1234567-built', env: 'tbeam', name: 'Meshtastic for tbeam', chip: 'ESP32', parts: 2 }];
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');

async function page(url, api, { serial = false, secure } = {}) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = async (u) => (u === '/flasher/api/esp' ? new w.Response(JSON.stringify(api), { status: 200 }) : new w.Response('{}', { status: 404 }));
  if (w.Response === undefined) w.Response = Response;
  if (serial) Object.defineProperty(w.navigator, 'serial', { value: {} });
  if (secure !== undefined) Object.defineProperty(w, 'isSecureContext', { value: secure });
  w.eval(js);
  await new Promise((r) => setTimeout(r, 50));
  return w.document;
}

(async () => {
  global.Response = global.Response || require('node:buffer').Response;
  let d = await page('http://box.local/flasher/', { targets: two, engine: true, download: false }, { secure: false });
  check('plain HTTP: the HTTPS address offered, linked', /needs this page over HTTPS/.test(t(d.getElementById('flash-can')))
    && d.querySelector('#flash-can a').getAttribute('href') === 'https://box.local/flasher/', t(d.getElementById('flash-can')));
  check('  each build listed with its chip and version, no button', d.querySelectorAll('#flash-list li').length === 2
    && /Meshtastic for heltec-v3 · ESP32-S3 · 2\.9\.0\.1234567$/.test(t(d.querySelector('#flash-list li'))) && !d.querySelector('esp-web-install-button'),
    t(d.querySelector('#flash-list li')));
  check('  the Meshtastic download hidden when it is not there', d.getElementById('flash-download').hidden);
  d = await page('https://box.local/flasher/', { targets: two, engine: true, download: true }, { secure: true });
  check('HTTPS, no Web Serial: Chrome or Edge on a computer said', /use Chrome or Edge on a computer/.test(t(d.getElementById('flash-can'))), t(d.getElementById('flash-can')));
  check('  the Meshtastic download offered when it is there', !d.getElementById('flash-download').hidden
    && d.querySelector('#flash-download a').getAttribute('href') === '/flasher/flasher.html');
  d = await page('https://box.local/flasher/', { targets: [], engine: true, download: false }, { secure: true, serial: true });
  check('nothing published: where builds come from said', /Nothing to flash yet/.test(t(d.getElementById('flash-can'))) && /Publish to the web flasher/.test(t(d.getElementById('flash-can'))));
  d = await page('https://box.local/flasher/', { targets: two, engine: false, download: false }, { secure: true, serial: true });
  check('the install button not on the box: said, the builds still listed', /not on this box/.test(t(d.getElementById('flash-can')))
    && d.querySelectorAll('#flash-list li').length === 2 && !d.querySelector('esp-web-install-button'), t(d.getElementById('flash-can')));
  check('the licence linked', d.querySelector('a[href="/flasher/esp-web-tools/THIRD-PARTY-NOTICES.txt"]') !== null);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
