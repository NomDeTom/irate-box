// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Quick setup (web/admin.html #presets, admin.js): three purpose tiles, three durability tiles,
// a preview of every line, Apply, and Undo. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-presets.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js', 'admin-tour.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
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
  setup: ['signup', 'hotspot', 'terminal', 'addons'],
  summary: 'Apply: 2 settings change, 1 app or toolkit installed, 1 left out.',
  automatic_undo: null,
};
let record = null;
let current = fixture.current;
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/presets' && opts.method === 'POST') {
    const b = JSON.parse(opts.body); posted.push(b);
    if (b.action === 'preview') return new Response(JSON.stringify(preview), { status: 200 });
    if (b.action === 'undo') {
      record = { status: 'undone', setup_added: ['quick-setup', 'signup', 'hotspot', 'terminal', 'addons'], setup_kept: ['signup'], undone_at: 1790000000 };
      current = fixture.current;
      return new Response(JSON.stringify({ ...fixture, record, current }), { status: 202 });
    }
    record = { status: 'done', purpose: 'curated', durability: 'balanced', purpose_title: 'Curated space', durability_title: 'Balanced: logs kept', setup: ['quick-setup', 'signup', 'hotspot', 'terminal', 'addons'], setup_added: ['quick-setup', 'signup', 'hotspot', 'terminal', 'addons'] };
    current = { purpose: 'curated', durability: 'balanced' };
    return new Response(JSON.stringify({ ...fixture, record, current }), { status: 202 });
  }
  if (u === '/admin/presets') return new Response(JSON.stringify({ ...fixture, record, current }), { status: 200 });
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
  check('Quick setup: at the top of Setup steps, not a System page, with three tiles per dial',
    !!d.getElementById('page-presets') && !!d.querySelector('#page-presets #welcome')
    && purpose.length === 3 && durability.length === 3
    && ![...d.querySelectorAll('#admin-side-list a')].some((a) => a.textContent.trim() === 'Quick setup'),
    [purpose.length, durability.length]);
  check('  the purpose card is above the durability card',
    !!(d.getElementById('preset-purpose').compareDocumentPosition(d.getElementById('preset-durability'))
      & w.Node.DOCUMENT_POSITION_FOLLOWING));
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8').replace(/\s+/g, ' ');
  check('  three tiles in a row for each dial (style.css)',
    /\.preset-tiles \{ display: grid; grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/.test(css));
  check('  one tile in a row on a narrow page (style.css)',
    /@container page \(max-width: 760px\) \{ \.preset-tiles \{ grid-template-columns: 1fr; \} \}/.test(css));
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
  check('  and the setup steps it answers are named before Apply',
    /Setup steps this answers: Account registration, Hotspot security, Terminal \(shell in the browser\), Add-ons\./
      .test(d.getElementById('preset-preview').textContent), d.getElementById('preset-preview').textContent);
  const apply = [...d.querySelectorAll('#preset-buttons button')].find((b) => /Preview and apply/.test(b.textContent));
  apply.click();
  await new Promise((r) => setTimeout(r, 600));
  check('Apply sends the two choices', posted.some((b) => b.action === 'apply' && b.purpose === 'curated' && b.durability === 'longevity'), posted);
  check('  the completed setup steps are marked done in the tour',
    w.TOUR.ALL.length === 20
    && ['quick-setup', 'signup', 'hotspot', 'terminal', 'addons'].every((id) => w.TOUR.decided().has(id)),
    [...w.TOUR.decided()].join(' '));
  const undo = [...d.querySelectorAll('#preset-buttons button')].find((b) => /Undo preset/.test(b.textContent));
  check('  the page offers Undo', !!undo, d.getElementById('preset-buttons').textContent);
  undo.click();
  await new Promise((r) => setTimeout(r, 600));
  check('  Undo takes the steps it added back out, and leaves one decided since',
    !['quick-setup', 'hotspot', 'terminal', 'addons'].some((id) => w.TOUR.decided().has(id))
    && w.TOUR.decided().has('signup'),
    [...w.TOUR.decided()].join(' '));
  check('no page errors', !errors.length, errors);
  process.exit(fails ? 1 : 0);
}, 3000);
