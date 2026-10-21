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
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
function page(storage) {
  const vc = new VirtualConsole(); vc.on('jsdomError', (e) => { if (!/scrollTo|navigation/.test(e.message)) errors.push(e.message); });
  const dom = new JSDOM(strip(fs.readFileSync(`${WEB}/drop.html`, 'utf8')), { url: BASE + '/drop.html', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  if (storage) for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
  w.fetch = (u, o) => fetch(new URL(u, BASE), o);
  w.formatAge = () => 'just now';
  // The page asks on the page (ask.js): a browser pop-up is an error.
  w.confirm = (m) => errors.push('confirm: ' + m); w.alert = (m) => errors.push('alert: ' + m);
  w.crypto.getRandomValues = (a) => require('crypto').getRandomValues(a);
  for (const f of ['lock.js', 'ask.js', 'drop.js']) w.eval(fs.readFileSync(`${WEB}/` + f, 'utf8'));
  return w;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rowOf = (d) => [...d.querySelectorAll('#drop-list li')].find((li) => li.textContent.includes('notes.txt'));
const press = (root, label) => [...root.querySelectorAll('button')].find((b) => b.textContent === label).click();
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
  check('a locked upload: Delete file on this device only', !!(row && row.querySelector('button')) && !(orow && orow.querySelector('button')));
  // Delete asks on the page, beside the button; Cancel leaves the file.
  press(row, 'Delete file');
  const ask = row.querySelector('.aw-ask');
  check('Delete asks on the page, beside the button', !!ask && /Delete notes\.txt from the box\? This cannot be undone\./.test(ask.textContent), ask && ask.textContent);
  press(ask, 'Cancel');
  await sleep(600);
  check('Cancel: the question goes and the file stays', !d.querySelector('.aw-ask') && !!rowOf(d));
  // A refused delete: said under the file's row, and still there after the list is drawn again.
  const realFetch = w.fetch;
  w.fetch = (u, o) => (o && o.method === 'DELETE' ? Promise.resolve(new Response(JSON.stringify({ error: 'the key is out of date' }), { status: 403 })) : realFetch(u, o));
  press(rowOf(d), 'Delete file');
  press(d.querySelector('.aw-ask'), 'Delete');
  await sleep(1200);
  check('a refused delete is said under its row', /File not deleted: the key is out of date/.test(rowOf(d).textContent), rowOf(d).textContent);
  w.fetch = realFetch;
  press(rowOf(d), 'Delete file');
  press(d.querySelector('.aw-ask'), 'Delete');
  await sleep(1200);
  check('Delete: the file goes and this browser forgets its key', !rowOf(d) && !Object.keys(w.localStorage).some((k) => k.startsWith('hublock:drop:')));
  check('no browser pop-up, no page error', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
