// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Network pane in jsdom; BASE is a hub whose /admin/network answers without a login (a proxy that adds it).
// Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-net.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const BASE = process.env.BASE || 'http://127.0.0.1:18098';
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push('jsdom: ' + e.message));
vc.on('error', (...a) => errors.push('console: ' + a.join(' ')));
const dom = new JSDOM(html, { url: BASE + '/admin/#network', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST' && u === '/admin/network') posted.push(JSON.parse(opts.body));
  if (opts.method === 'POST' && u === '/admin/network') return new Response(JSON.stringify({ id: 'x' }), { status: 202 });
  return fetch(new URL(u, BASE), opts);
};
w.confirm = () => true;
w.eval(js);
setTimeout(() => {
  const d = w.document;
  const t = (sel) => [...d.querySelectorAll(sel)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
  console.log('WHEN:', t('#net-when')[0]);
  console.log('DEVICES:', [...d.querySelectorAll('#net-device option')].map((o) => o.value || 'All').join(', '));
  console.log('RADIOS:'); t('#net-radios tbody tr').forEach((r) => console.log('  ', r));
  console.log('HAZARDS:'); t('#net-hazards li').forEach((r) => console.log('  ', r.slice(0, 160)));
  console.log('STATUS:', t('#up-status')[0]);
  console.log('LEVELS:', d.getElementById('up-eager').value, d.getElementById('up-forgive').value, '|', t('#up-eager-desc')[0], '|', t('#up-forgive-desc')[0]);
  console.log('FIELDS:', [...d.querySelectorAll('#up-fields [data-key]')].map((i) => `${i.dataset.key}=${i.value || '(' + (i.placeholder || i.options?.[0]?.textContent) + ')'}`).join('  '));
  console.log('PROFILE:', t('#up-profile')[0] || '(none)');
  console.log('EVENTS:'); t('#up-events li').slice(0, 6).forEach((r) => console.log('  ', r));
  // Change eagerness → descriptions and placeholders follow, then save with a custom value.
  const e = d.getElementById('up-eager'); e.value = 'stubborn'; e.dispatchEvent(new w.Event('change'));
  console.log('AFTER stubborn:', t('#up-eager-desc')[0], '| reboot placeholder:', d.querySelector('[data-key="steps.reboot"]').placeholder);
  d.querySelector('[data-key="steps.reboot"]').value = 'off';
  d.querySelector('[data-key="check"]').value = '45';
  d.getElementById('up-save').click();
  d.querySelector('[data-key="check"]').value = 'abc';
  d.getElementById('up-save').click();
  setTimeout(() => {
    console.log('POSTED:', JSON.stringify(posted));
    console.log('NOTE:', t('#up-note')[0]);
    console.log('ERRORS:', errors.length ? errors : 'none');
    process.exit(0);
  }, 1500);
}, 6000);
