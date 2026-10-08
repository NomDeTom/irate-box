// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A page of web/ in jsdom against a fixture. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-hs.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const fixture = fs.readFileSync(process.env.FIX || `${__dirname}/hotspot-fixture.json`, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push(e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#security', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window; const posted = [];
w.fetch = async (u, o = {}) => {
  if (u === '/admin/hotspot' && o.method === 'POST') { posted.push(JSON.parse(o.body)); return new Response(JSON.stringify({ settings: {}, message: 'Saved' }), { status: 200 }); }
  if (u === '/admin/hotspot') return new Response(fixture, { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.crypto = { getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = i * 7919; return a; } };
w.eval(js);
setTimeout(() => {
  const d = w.document, t = (s) => [...d.querySelectorAll(s)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
  console.log('MODES:'); [...d.querySelectorAll('.hs-mode')].forEach((m) => console.log('  ', m.querySelector('input').disabled ? '[disabled]' : '[ ]', m.textContent.replace(/\s+/g, ' ').trim().slice(0, 120)));
  console.log('FIELDS hidden (open):', d.getElementById('hs-fields').hidden);
  console.log('WARNINGS (open):', t('#hs-warnings li').length);
  const sae = d.querySelector('input[value="sae"]'); sae.checked = true; sae.dispatchEvent(new w.Event('change'));
  console.log('FIELDS hidden (sae):', d.getElementById('hs-fields').hidden, '| password shown:', !d.getElementById('hs-password').parentElement.hidden);
  d.getElementById('hs-generate').click(); console.log('GENERATED:', d.getElementById('hs-password').value);
  d.getElementById('hs-wpa2').checked = true; d.getElementById('hs-wpa2').dispatchEvent(new w.Event('change'));
  console.log('WARNINGS (sae+wpa2):'); t('#hs-warnings li').forEach((x) => console.log('   -', x.slice(0, 110)));
  d.getElementById('hs-save').click();
  setTimeout(() => { console.log('POSTED:', JSON.stringify(posted)); console.log('ERRORS:', errors.length ? errors : 'none'); process.exit(0); }, 300);
}, 1500);
