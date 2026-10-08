// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A page of web/ in jsdom against a running hub. Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-drop.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const BASE = process.env.BASE;
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const strip = (h) => h;
const errors = [];
function page(storage) {
  const vc = new VirtualConsole(); vc.on('jsdomError', (e) => { if (!/scrollTo|navigation/.test(e.message)) errors.push(e.message); });
  const dom = new JSDOM(strip(fs.readFileSync(`${WEB}/drop.html`, 'utf8')), { url: BASE + '/drop.html', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  if (storage) for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
  w.fetch = (u, o) => fetch(new URL(u, BASE), o);
  w.formatAge = () => 'just now';
  w.confirm = () => true; w.alert = (m) => errors.push('alert: ' + m);
  w.crypto.getRandomValues = (a) => require('crypto').getRandomValues(a);
  for (const f of ['lock.js', 'drop.js']) w.eval(fs.readFileSync(`${WEB}/` + f, 'utf8'));
  return w;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const w = page();
  const d = w.document;
  d.getElementById('drop-lock').checked = true; d.getElementById('drop-lock').dispatchEvent(new w.Event('change'));
  const file = new w.File(['secret notes'], 'notes.txt', { type: 'text/plain' });
  const picker = d.getElementById('drop-files');
  Object.defineProperty(picker, 'files', { value: [file] });
  picker.dispatchEvent(new w.Event('change'));
  await sleep(1500);
  console.log('UPLOAD STATUS:', [...d.querySelectorAll('#drop-uploads li')].map((li) => li.textContent.replace(/\s+/g, ' ').trim()).join(' | ') || '(row gone)');
  const row = [...d.querySelectorAll('#drop-list li')].find((li) => li.textContent.includes('notes.txt'));
  console.log('LIST ROW (this device):', row && row.textContent.replace(/\s+/g, ' ').trim());
  const seeds = Object.keys(w.localStorage).filter((k) => k.startsWith('hublock:drop:'));
  console.log('SEED KEPT:', seeds.length === 1);
  const other = page(); await sleep(800);
  const orow = [...other.document.querySelectorAll('#drop-list li')].find((li) => li.textContent.includes('notes.txt'));
  console.log('LIST ROW (another device):', orow && orow.textContent.replace(/\s+/g, ' ').trim(), '| Remove button:', !!(orow && orow.querySelector('button')));
  row.querySelector('button').click();
  await sleep(1200);
  const after = [...d.querySelectorAll('#drop-list li')].some((li) => li.textContent.includes('notes.txt'));
  console.log('AFTER REMOVE: still listed:', after, '| seed forgotten:', !Object.keys(w.localStorage).some((k) => k.startsWith('hublock:drop:')));
  console.log('ERRORS:', errors.length ? errors : 'none');
  process.exit(0);
})();
