// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Folders of apps on /admin (menu overhaul M7; checklist 5e), in jsdom: a Folders group, a page per
// folder with what's in it; an entry hidden, moved, and put in another folder, all held until Save,
// which sends the whole arrangement; Discard puts it back. Usage: [JSDOM=…/jsdom] node dom-folders.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const adminApps = require('./admin-apps-fixture.cjs');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 200) => new Promise((r) => setTimeout(r, ms));
const E = (href, name, kind = 'page', from) => ({ href, name, desc: '', kind, from, hidden: false, extra: false });
let snap = { state: {}, folders: [
  { id: 'tools-rf', title: 'RF & LoRa', entries: [E('/app.html#/tools/air.html', 'Airtime', 'page', 'tools'), E('/app.html#/tools/frame.html', 'Frame', 'page', 'tools'), E('/serial/dl.html', 'Serial download', 'download', 'serial')] },
  { id: 'tools-electronics', title: 'Electronics', entries: [E('/app.html#/tools/ohm.html', 'Ohm', 'page', 'tools')] },
  { id: 'meshtastic', title: 'Meshtastic', entries: [E('/app.html#/tools/air.html', 'Airtime', 'page', 'tools')] }] };
const posted = [];
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const dom = new JSDOM(html, { url: 'http://box/admin/#page-app-tools-rf', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.fetch = async (u, o = {}) => {
  const json = (x, s = 200) => new Response(JSON.stringify(x), { status: s });
  if (o.method === 'POST' && u === '/admin/folders') { const b = JSON.parse(o.body); posted.push(b); snap = { ...snap, state: b.state }; return json(snap); }
  if (u === '/admin/folders') return json(snap);
  if (u === '/admin/apps') return json({ apps: adminApps() });
  return json({}, 404);
};
w.confirm = () => true;
['admin-widgets.js', 'admin-layout.js', 'admin.js'].forEach((f) => w.eval(fs.readFileSync(`${WEB}/${f}`, 'utf8')));
const click = (el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const block = () => d.querySelector('.folder-block[data-folder="tools-rf"]');
const row = (name) => [...block().querySelectorAll('.aw-row')].find((r) => t(r.querySelector('.t')) === name);
const button = (root, label) => [...root.querySelectorAll('button')].find((b) => t(b) === label);
(async () => {
  await wait();
  const groups = [...d.querySelectorAll('.admin-side-group')].map(t);
  check('a Folders group, after Apps', groups.indexOf('Folders') === groups.indexOf('Apps') + 1, groups.join('|'));
  const page = d.getElementById('page-app-tools-rf');
  check('the RF folder\'s page opens, with what\'s in it', page && !page.hidden && !!block(), page && page.hidden);
  check('  one line per entry, each saying what it is', block().querySelectorAll('.aw-row').length === 3 && /a download/i.test(t(row('Serial download'))));
  const save = () => button(block(), 'Save');
  check('Save waits for a change', save().disabled);
  click(row('Airtime').querySelector('.aw-row-head'));
  click(button(row('Airtime'), 'Shown: On'));
  check('hiding an entry: marked hidden, Save offered', /hidden/i.test(t(row('Airtime').querySelector('.p'))) && !save().disabled);
  check('  its line stays open after the change', row('Airtime').querySelector('.aw-row-head').getAttribute('aria-expanded') === 'true');
  click(button(row('Airtime'), 'Electronics'));
  click(button(row('Airtime'), '↓ Later'));
  check('moved: the order changes', [...block().querySelectorAll('.aw-row .t')].map(t).join('|') === 'Frame|Airtime|Serial download', [...block().querySelectorAll('.aw-row .t')].map(t).join('|'));
  click(save());
  await wait();
  const st = (posted[0] || {}).state || {};
  check('Save sends the whole arrangement: hidden here, put in Electronics, the order',
    JSON.stringify(st['tools-rf'].hidden) === '["/app.html#/tools/air.html"]' && (st['tools-electronics'].extra || []).includes('/app.html#/tools/air.html')
    && st['tools-rf'].order[0] === '/app.html#/tools/frame.html', JSON.stringify(st));
  check('  and Save waits again', save().disabled);
  click(row('Frame').querySelector('.aw-row-head'));
  click(button(row('Frame'), 'Shown: On'));
  click(button(block(), 'Discard'));
  check('Discard puts back what was saved', !/hidden/i.test(t(row('Frame').querySelector('.p'))) && save().disabled);
  check('no page errors', !errors.length, errors.join('; '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
