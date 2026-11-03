// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// One IRC entry in /admin's Add-ons: one group card, the installed server marked, the other's Switch asking first
// and sending one switch request. Usage: [JSDOM=…/jsdom] node dom-addons-group.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const addons = { addons: [
  { id: 'irc', group: 'irc', group_title: 'IRC chat', group_summary: 'One IRC server.', title: 'IRC server (ngIRCd)', summary: 'A small server.', added: true, active: true },
  { id: 'inspircd', group: 'irc', group_title: 'IRC chat', group_summary: 'One IRC server.', title: 'IRC server (InspIRCd)', summary: 'A modular server.', added: false, active: false, consent: 'Open unencrypted IRC on the network?' },
], progress: null, pending: 0, results: [], log: [] };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box/admin/#addons', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') {
    posted.push([u, JSON.parse(opts.body || '{}')]);
    return new Response('{"id":"x"}', { status: 202 });
  }
  if (u === '/admin/addons') return new Response(JSON.stringify(addons), { status: 200 });
  if (u === '/admin/apps') return new Response('{"apps":[]}', { status: 200 });
  if (u === '/admin/access') return new Response('{"apps":[],"pages":{},"seen":{}}', { status: 200 });
  if (u === '/admin/settings') return new Response('{}', { status: 200 });
  return new Response('{}', { status: 404 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
setTimeout(async () => {
  const d = w.document;
  await new Promise((r) => setTimeout(r, 100));
  const cards = [...d.querySelectorAll('#addons-list .addon-group')];
  check('one card for the IRC group, not one per server', cards.length === 1 && cards[0].textContent.includes('ngIRCd') && cards[0].textContent.includes('InspIRCd'), cards.length);
  check('  the installed member is marked', /Installed\. Service running\./.test(t(cards[0])) && cards[0].querySelector('.addon-group-member.added'));
  const sw = [...cards[0].querySelectorAll('button')].find((b) => b.textContent === 'Switch to IRC server (InspIRCd)');
  check('  Switch asks for the consent tick first, with its wait key', sw && sw.disabled && sw.dataset.wait === 'addon-switch:irc:inspircd');
  const tick = cards[0].querySelector('input[type=checkbox]');
  tick.checked = true; tick.dispatchEvent(new w.Event('change', { bubbles: true }));
  check('  ticked: Switch enabled', !sw.disabled);
  posted.length = 0;
  sw.click();
  await new Promise((r) => setTimeout(r, 50));
  check('  Switch sends one request that replaces one with the other', posted.length === 1 && posted[0][0] === '/admin/addons' && JSON.stringify(posted[0][1].switch) === JSON.stringify(['irc', 'inspircd']), posted);
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
}, 300);
