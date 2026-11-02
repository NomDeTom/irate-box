// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Quick setup (web/admin.html #presets, admin.js): three purpose tiles, three durability tiles,
// a preview of every line, Apply, and Undo. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-presets.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#presets', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const fixture = {
  purposes: [
    { id: 'pirate', title: 'Pirate box', summary: 'An open drop box.', suggested_durability: 'longevity' },
    { id: 'curated', title: 'Curated space', summary: 'Accounts and moderation.', suggested_durability: 'balanced' },
    { id: 'power', title: 'Power unit', summary: 'Everything that fits.', suggested_durability: 'balanced' },
  ],
  durabilities: [
    { id: 'longevity', title: 'Longevity', summary: 'The card lives longest.' },
    { id: 'balanced', title: 'Balanced: logs kept', summary: 'At most about 5 minutes of logs lost.' },
    { id: 'full', title: 'Full logging', summary: 'Everything as it happens.', auto_return_hours: 24 },
  ],
  current: { purpose: 'pirate', durability: 'longevity' },
  record: null,
  own_changes: [],
  results: [],
};
const posted = [];
const preview = {
  purpose: 'curated', purpose_title: 'Curated space', durability: 'balanced', durability_title: 'Balanced: logs kept',
  changes: [
    { type: 'hub', label: 'Who may post in the shoutbox', why: 'Members post; guests read', before: 'guests', after: 'users' },
    { type: 'choice', label: 'Build limits', why: 'Some building, kept small', before: 'default', after: 'small' },
  ],
  installs: [{ type: 'addon', label: 'Shared notes (SilverBullet)', why: 'A shared notebook', before: false, after: true }],
  left_out: [{ label: 'Build toolkit', why: 'it needs about 384 MB of memory and the box has 512' }],
  summary: 'Apply: 2 settings change, 1 app or toolkit installed, 1 left out.',
  automatic_undo: null,
};
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/presets' && opts.method === 'POST') {
    const b = JSON.parse(opts.body); posted.push(b);
    if (b.action === 'preview') return new Response(JSON.stringify(preview), { status: 200 });
    return new Response(JSON.stringify({ ...fixture, record: { status: 'done', purpose: 'curated', durability: 'balanced', purpose_title: 'Curated space', durability_title: 'Balanced: logs kept' }, current: { purpose: 'curated', durability: 'balanced' } }), { status: 202 });
  }
  if (u === '/admin/presets') return new Response(JSON.stringify(fixture), { status: 200 });
  if (u === '/admin/tiles') return new Response('{"tiles":[],"state":{"size":{}}}', { status: 200 });
  if (u === '/admin/apps') return new Response('{"apps":[]}', { status: 200 });
  if (u === '/admin/folders') return new Response('{"folders":[],"state":{}}', { status: 200 });
  if (u === '/admin/status-tiles') return new Response('{"tiles":[],"state":{}}', { status: 200 });
  if (u === '/admin/accounts') return new Response('{"settings":{},"accounts":[],"counts":{},"admin_login":{}}', { status: 200 });
  if (u === '/admin/addons') return new Response('{"addons":[]}', { status: 200 });
  if (u === '/admin/health') return new Response('{"report":null,"results":[],"pending":0}', { status: 200 });
  if ((opts.method || 'GET').toUpperCase() === 'GET') return new Response('{}', { status: 200 });
  return new Response('{"id":"x"}', { status: 202 });
};
w.confirm = () => true;
w.eval(js);
setTimeout(async () => {
  const d = w.document;
  let fails = 0;
  const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
  const purpose = [...d.querySelectorAll('#preset-purpose-tiles .preset-tile')];
  const durability = [...d.querySelectorAll('#preset-durability-tiles .preset-tile')];
  check('Quick setup: a page under System, three purpose tiles and three durability tiles',
    !!d.getElementById('page-presets') && purpose.length === 3 && durability.length === 3,
    [purpose.length, durability.length]);
  check('  the current choices are marked',
    purpose.find((b) => b.textContent.includes('Pirate box')).classList.contains('selected')
    && durability.find((b) => b.textContent.includes('Longevity')).classList.contains('selected'));
  check('  a purpose says which durability it suggests', /Suggests Longevity/.test(purpose[0].textContent), purpose[0].textContent);
  check('  full logging says it returns by itself', /Returns by itself after 24 hours/.test(durability[2].textContent), durability[2].textContent);
  purpose.find((b) => b.textContent.includes('Curated space')).click();
  await new Promise((r) => setTimeout(r, 300));
  check('selecting a tile asks for a preview', posted.some((b) => b.action === 'preview' && b.purpose === 'curated'), posted);
  const lines = [...d.querySelectorAll('#preset-preview li')].map((x) => x.textContent.replace(/\s+/g, ' ').trim());
  check('  each change is its own line, with what it was and will be',
    lines.some((x) => /Who may post in the shoutbox: guests → users/.test(x))
    && lines.some((x) => /Shared notes \(SilverBullet\): not installed → installed/.test(x)), lines);
  check('  and what was left out says why', /it needs about 384 MB of memory/.test(d.getElementById('preset-preview').textContent));
  const apply = [...d.querySelectorAll('#preset-buttons button')].find((b) => /Preview and apply/.test(b.textContent));
  apply.click();
  await new Promise((r) => setTimeout(r, 300));
  check('Apply sends the two choices', posted.some((b) => b.action === 'apply' && b.purpose === 'curated' && b.durability === 'longevity'), posted);
  check('no page errors', !errors.length, errors);
  process.exit(fails ? 1 : 0);
}, 3000);
