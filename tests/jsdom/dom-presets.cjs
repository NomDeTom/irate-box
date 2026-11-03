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
    { id: 'pirate', title: 'Pirate box', summary: 'An open drop box.' },
    { id: 'curated', title: 'Curated space', summary: 'Accounts and moderation.' },
    { id: 'power', title: 'Power unit', summary: 'Everything that fits.' },
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
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const foot = () => d.querySelector('.aw-foot[data-section="quick-setup"]');
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
  check('  the dials in the order asked: Pirate, Curated, Power; Longevity, Balanced, Full',
    purpose.map((b) => b.querySelector('strong').textContent).join(',') === 'Pirate box,Curated space,Power unit'
    && durability.map((b) => b.querySelector('strong').textContent).join(',') === 'Longevity,Balanced: logs kept,Full logging');
  check('  no purpose suggests a durability any more', ![...purpose, ...durability].some((b) => /Suggests/.test(b.textContent)));
  check('  full logging says it returns by itself', /Returns by itself after 24 hours/.test(durability[2].textContent), durability[2].textContent);
  purpose.find((b) => b.textContent.includes('Curated space')).click();
  await wait(300);
  check('selecting one tile asks for a preview, the other dial as the box has it',
    posted.some((b) => b.action === 'preview' && b.purpose === 'curated' && b.durability === 'longevity'), posted);
  check('  and the preview says the durability is what the box has now',
    /The durability is what the box has now\./.test(d.getElementById('preset-preview').textContent),
    d.getElementById('preset-preview').textContent);
  const lines = [...d.querySelectorAll('#preset-preview li')].map((x) => x.textContent.replace(/\s+/g, ' ').trim());
  check('  each change is its own line, with what it was and will be',
    lines.some((x) => /Who may post in the shoutbox: guests → users/.test(x))
    && lines.some((x) => /Shared notes \(SilverBullet\): not installed → installed/.test(x)), lines);
  check('  and what was left out says why', /it needs about 384 MB of memory/.test(d.getElementById('preset-preview').textContent));
  check('  and the setup steps it answers are named before Save',
    /Setup steps this answers: Account registration, Hotspot security, Terminal \(shell in the browser\), Add-ons\./
      .test(d.getElementById('preset-preview').textContent), d.getElementById('preset-preview').textContent);
  check('  the footer warns while the installs are unsaved',
    !!foot() && !foot().querySelector('.aw-warn').hidden
    && /This installs 1 app or toolkit\./.test(foot().querySelector('.aw-warn').textContent),
    foot() && foot().querySelector('.aw-warn').textContent);
  durability.find((b) => b.textContent.includes('Balanced: logs kept')).click();
  await wait(300);
  check('choosing the second dial previews both',
    posted.some((b) => b.action === 'preview' && b.purpose === 'curated' && b.durability === 'balanced'), posted);
  purpose.find((b) => b.textContent.includes('Power unit')).click();
  await wait(300);
  check('changing a dial redraws the preview',
    posted.some((b) => b.action === 'preview' && b.purpose === 'power' && b.durability === 'balanced'), posted);
  foot().querySelector('.aw-discard').click();
  await wait(300);
  check('Discard puts the dials back to what the box has now, and redraws the preview',
    purpose.find((b) => b.textContent.includes('Pirate box')).classList.contains('selected')
    && durability.find((b) => b.textContent.includes('Longevity')).classList.contains('selected')
    && posted.some((b) => b.action === 'preview' && b.purpose === 'pirate' && b.durability === 'longevity'),
    d.getElementById('preset-preview').textContent);
  durability.find((b) => b.textContent.includes('Full logging')).click();
  await wait(300);
  check('  Full logging, unsaved, names its writes in the footer',
    /Full logging writes everything to the card\./.test(foot().querySelector('.aw-warn').textContent),
    foot().querySelector('.aw-warn').textContent);
  purpose.find((b) => b.textContent.includes('Curated space')).click();
  await wait(300);
  const save = foot().querySelector('.aw-save');
  check('Save is on with both dials chosen', !!save && !save.disabled, save && save.disabled);
  save.click();
  await wait(600);
  check('Save sends apply with both dials', posted.some((b) => b.action === 'apply' && b.purpose === 'curated' && b.durability === 'full'), posted);
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
  // No current preset: one tile alone still previews, and the other dial is said as not chosen yet.
  current = null;
  record = null;
  w.eval('loadPresets()');
  await wait(300);
  check('with no current preset, no tile is marked',
    ![...d.querySelectorAll('.preset-tile')].some((b) => b.classList.contains('selected')));
  const beforeOne = posted.length;
  purpose.find((b) => b.textContent.includes('Curated space')).click();
  await wait(300);
  check('  one tile alone previews, with no durability selected yet',
    posted.slice(beforeOne).some((b) => b.action === 'preview' && b.purpose === 'curated' && !b.durability)
    && /No durability selected yet\./.test(d.getElementById('preset-preview').textContent),
    d.getElementById('preset-preview').textContent);
  check('no page errors', !errors.length, errors);
  process.exit(fails ? 1 : 0);
}, 3000);
