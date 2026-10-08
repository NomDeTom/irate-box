// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A page of web/ in jsdom against a fixture. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-health.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const fixture = fs.readFileSync(process.env.FIX || `${__dirname}/health-fixture.json`, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#health', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/health' && opts.method === 'POST') { posted.push(JSON.parse(opts.body)); return new Response('{"id":"x"}', { status: 202 }); }
  if (u === '/admin/health') return new Response(fixture, { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
setTimeout(() => {
  const d = w.document, t = (s) => [...d.querySelectorAll(s)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
  console.log('BANNER hidden:', d.getElementById('helper-banner').hidden, '|', t('#helper-banner')[0]);
  console.log('WHEN:', t('#health-when')[0]);
  console.log('FINDINGS:'); t('#health-findings li').forEach((x) => console.log('  ', x.slice(0, 170)));
  console.log('BUTTONS:', [...d.querySelectorAll('#health-findings button')].map((b) => `${b.textContent}${b.disabled ? ' (disabled)' : ''}`).join(', '));
  console.log('INSTALL:', t('#health-install')[0]);
  console.log('LOG lines:', d.getElementById('health-log').textContent.split('\n').length);
  console.log('BADGE:', (d.querySelector('a[href="#health"] .badge, a[href="#health"]') || {}).textContent);
  console.log('ERRORS:', errors.length ? errors : 'none');
  process.exit(0);
}, 3000);
