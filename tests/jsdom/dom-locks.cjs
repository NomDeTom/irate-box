// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A page of web/ in jsdom against a running hub. Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-locks.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs'); const crypto = require('crypto');
const BASE = process.env.BASE; const errors = [];
const strip = (h) => h.replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
function page(file, scripts, seedStore) {
  const vc = new VirtualConsole(); vc.on('jsdomError', (e) => { if (!/scrollTo|navigation/.test(e.message)) errors.push(e.message); });
  const dom = new JSDOM(strip(fs.readFileSync(`${WEB}/${file}`, 'utf8')), { url: `${BASE}/${file}`, runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  for (const [k, v] of Object.entries(seedStore || {})) w.localStorage.setItem(k, v);
  w.fetch = (u, o) => fetch(new URL(u, BASE), o); w.confirm = () => true; w.alert = (m) => errors.push('alert ' + m);
  w.crypto.getRandomValues = (a) => crypto.getRandomValues(a); w.formatAge = () => 'now';
  for (const f of scripts) w.eval(fs.readFileSync(`${WEB}/${f}`, 'utf8'));
  return w;
}
const walk = (seed, n) => { let v = Buffer.from(seed, 'hex'); for (let i = 0; i < n; i++) v = crypto.createHash('sha256').update(v).digest(); return v.toString('hex'); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  // a locked file and a locked save, held by browser A
  const seedF = crypto.randomBytes(32).toString('hex'), seedS = crypto.randomBytes(32).toString('hex');
  const f = await (await fetch(`${BASE}/api/drop`, { method: 'POST', headers: { 'X-Drop-Name': 'a.txt', 'X-Lock-New': `${walk(seedF, 4096)}:4096`, 'Content-Type': 'application/octet-stream' }, body: 'hi' })).json();
  const s = await (await fetch(`${BASE}/api/saves`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lock-New': `${walk(seedS, 4096)}:4096` }, body: JSON.stringify({ id: 'pic1', kind: 'excalidraw', name: 'my drawing', state: {} }) })).json();
  const A = page('locks.html', ['qrcode.js', 'lock.js', 'locks.js'], { [`hublock:drop:${f.id}`]: seedF, 'hublock:saves:pic1': seedS, 'hublock:drop:gone123': seedF });
  await sleep(800);
  const rows = [...A.document.querySelectorAll('#locks-list li')];
  rows.forEach((r) => console.log('ROW:', r.textContent.replace(/\s+/g, ' ').trim().slice(0, 110)));
  const fileRow = rows.find((r) => r.textContent.includes('a.txt'));
  fileRow.querySelector('button').click();
  const key = fileRow.querySelector('input').value;
  console.log('KEY shown:', /^hublock:drop:[A-Za-z0-9_-]+:[0-9a-f]{64}$/.test(key), '| QR svg:', !!fileRow.querySelector('.locks-qr svg'));
  // browser B adds the key and can then remove the file from the drop page
  const B = page('locks.html', ['qrcode.js', 'lock.js', 'locks.js']);
  await sleep(300);
  B.document.getElementById('locks-import').value = 'hublock:drop:bad';
  B.document.getElementById('locks-add').click();
  console.log('BAD KEY refused:', B.document.getElementById('locks-note').classList.contains('bad'));
  B.document.getElementById('locks-import').value = key;
  B.document.getElementById('locks-add').click();
  await sleep(600);
  console.log('B now lists:', [...B.document.querySelectorAll('#locks-list li')].map((r) => r.textContent.replace(/\s+/g, ' ').trim().slice(0, 40)));
  const lockN = (await (await fetch(`${BASE}/api/drop`)).json()).files.find((x) => x.id === f.id).lock_n;
  const c = B.HubLock.change('drop', f.id, lockN);
  const del = await fetch(`${BASE}/api/drop/${f.id}`, { method: 'DELETE', headers: c.headers });
  console.log('B removes the file with the carried key:', del.status === 200);
  // forget a stale one in A
  const goneRow = [...A.document.querySelectorAll('#locks-list li')].find((r) => r.textContent.includes('no longer on the hub'));
  [...goneRow.querySelectorAll('button')].find((b) => b.textContent === 'Forget').click();
  await sleep(600);
  console.log('A after forgetting the stale key:', [...A.document.querySelectorAll('#locks-list li')].length, 'rows');
  console.log('ERRORS:', errors.length ? errors : 'none');
  process.exit(0);
})();
