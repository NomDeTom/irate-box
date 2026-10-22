// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The terminology guard for /admin (written-language review, rows R001-R005 and R260). It renders the page
// and checks that the glossary terms are what a person reads: Access and Visibility as separate settings,
// App updates and Installation under Maintenance, User data links, Download (not Fetch), Sign in (not Log in)
// and Select (not Choose). It also scans the page's own scripts for the same words in prose.
// A word guard keeps the glossary from drifting. It does not certify ASD-STE100 compliance.
// Usage: NODE_PATH=…/node_modules node dom-terms.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const adminApps = require('./admin-apps-fixture.cjs');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 300) => new Promise((r) => setTimeout(r, ms));

const accessData = { apps: [
  { id: 'drop', title: 'File drop', mode: 'public', login: false, kind: 'builtin', unit: false },
  { id: 'wiki', title: 'Kiwix', mode: 'private', login: false, kind: 'builtin', unit: true },
  { id: 'notes', title: 'Notes', mode: 'users', users: true, login: false, kind: 'addon', unit: true },
  { id: 'irc', title: 'IRC server (ngIRCd)', mode: 'off', login: false, kind: 'addon', unit: true },
], results: [], seen: { about: 'auto' } };
const library = { policy: { keep_old: 1, check_every_hours: 24, min_free_mb: 500, auto_install: 1, hub_check_every_hours: 24, hub_auto: 2, hub_window_start: 2, hub_window_end: 5 },
  token_set: false, running: false, types: [], progress: null, free_mb: 51000, job: {},
  status: { draw: { last_check: '2026-10-08 03:00', outcome: 'up to date', fetched: { version: 'v2', label: 'v2' }, latest: { version: 'v2', label: 'v2', size: 1 }, current: { version: 'v1' } } },
  sources: [{ kind: 'app', name: 'draw', type: 'bundle', repo: 'NomDeTom/excalidraw', workflow: 'irate-box-bundle.yml', branch: 'main' }],
  apps: { draw: { title: 'Excalidraw', installed: { commit: 'abc1234def', repository: 'NomDeTom/excalidraw', ref: 'main', built: '2026-10-01T00:00:00Z', has_previous: false }, pin: null } } };
const tilesData = { tiles: [{ id: 'drop', name: 'File drop', icon: '📥' }, { id: 'about', name: 'About', icon: 'ℹ️' }, { id: 'wiki', name: 'Kiwix', icon: '📚' }], state: { order: [] } };
const addons = { addons: [{ id: 'irc', title: 'IRC server (ngIRCd)', summary: 'Chat.', added: false, active: false, consent: 'Open IRC on the network?', needs: 'ngircd' },
  { id: 'notes', title: 'Notes', summary: 'A notebook.', added: true, active: true }], progress: null, pending: 0, results: [], log: [] };

const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const dom = new JSDOM(html, { url: 'http://box/admin/#overview', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.fetch = async (u, o = {}) => {
  const json = (x, s = 200) => new Response(JSON.stringify(x), { status: s });
  if (o.method === 'POST') return json({ id: 'x' }, 202);
  if (u === '/admin/apps') return json({ apps: adminApps() });
  if (u === '/admin/access') return json(accessData);
  if (u === '/admin/tiles') return json(tilesData);
  if (u.startsWith('/admin/library')) return json(library);
  if (u === '/admin/addons') return json(addons);
  return json({}, 404);
};
w.confirm = () => true;
['admin-widgets.js', 'admin-layout.js', 'admin.js'].forEach((f) => w.eval(fs.readFileSync(`${WEB}/${f}`, 'utf8')));

// The text a person reads: the page without its scripts and styles.
const visible = () => {
  const c = d.body.cloneNode(true);
  c.querySelectorAll('script, style, template').forEach((n) => n.remove());
  return c.textContent.replace(/\s+/g, ' ');
};
const chips = (root, label) => {
  const g = [...root.querySelectorAll('[role="group"]')].find((x) => x.getAttribute('aria-label') === label);
  return g ? [...g.querySelectorAll('button')].map(t) : null;
};

(async () => {
  await wait(600);

  // Access and Visibility are separate settings, each with its own heading (R001, R002, R110).
  const kiwix = d.getElementById('page-app-wiki');
  check('an app page has an Access heading, who can open the app', kiwix && [...kiwix.querySelectorAll('h2')].some((h) => t(h) === 'Access'));
  check('  Access values: Guests, Users, Administrators, Disabled', (chips(kiwix, 'Access') || []).join('|') === 'Guests|Users|Administrators|Disabled', chips(kiwix, 'Access'));
  check('  the Hub tile section is separate and says Visibility', [...kiwix.querySelectorAll('h2')].some((h) => t(h) === 'Hub tile')
    && /Visibility:/.test(t(kiwix.querySelector('#app-access-wiki'))), t(kiwix.querySelector('#app-access-wiki')));
  const tileRows = [...d.querySelectorAll('#tile-order-list .aw-row')].map((r) => t(r.querySelector('.s')));
  check('Home tiles rows state Access and Visibility apart', tileRows.length >= 3 && tileRows.every((s) => /^Access: .* · Visibility: /.test(s)), tileRows.join(' / '));
  const wikiRow = [...d.querySelectorAll('#tile-order-list .aw-row')].find((r) => /Kiwix/.test(t(r.querySelector('.t'))));
  wikiRow.querySelector('.aw-row-head').click();
  check('  the open tile row has a Visibility chip row: Guests, Users, Administrators, Hidden', (chips(wikiRow, 'Visibility') || []).join('|') === 'Guests|Users|Administrators|Hidden', chips(wikiRow, 'Visibility'));
  check('  and a Locked-tile action row, not "seen but not opened"', !!chips(wikiRow, 'Locked-tile action') && !/[Ss]een but not opened/.test(visible()));

  // Maintenance has App updates and Installation as sub-headings (R003, R004).
  const draw = d.getElementById('page-app-draw'), irc = d.getElementById('page-app-irc');
  check('an app with updates: Maintenance, then the App updates sub-heading', draw && [...draw.querySelectorAll('h2')].some((h) => t(h) === 'Maintenance')
    && t(draw.querySelector('#app-maint-draw h3')) === 'App updates');
  check('an add-on: Maintenance, then the Installation sub-heading', irc && t(irc.querySelector('#app-maint-irc h3')) === 'Installation');
  check('  the add-on installation buttons name what they do', /Install add-on/.test(t(irc.querySelector('#app-install-irc'))) && !/Fetch/.test(t(irc.querySelector('#app-install-irc'))));
  const maintHeads = [...d.querySelectorAll('.app-maint h3')].map(t);
  check('  no Maintenance sub-heading promises updates only above an install control', maintHeads.every((h) => ['App updates', 'Installation'].includes(h)), maintHeads.join('|'));

  // User data, not "everything people made" (R005).
  const made = d.getElementById('made');
  check('User data: the page is named, and its list links to each place', made && [...d.querySelectorAll('.admin-side-list a')].some((a) => t(a) === 'User data')
    && [...made.querySelectorAll('#made-list a')].length >= 4 && /User data/.test(t(made.querySelector('h2'))), made && t(made));
  check('  no "Everything people made"', !/Everything people made/i.test(visible()));

  // Download, not Fetch (R081, R090, R120).
  const drawBtns = [...draw.querySelectorAll('button')].map(t);
  check('Download is the verb for getting a file: the app row has Download, not Fetch', drawBtns.includes('Download') && !drawBtns.some((b) => /Fetch/i.test(b)), drawBtns.join('|'));
  check('  no button anywhere says Fetch', ![...d.querySelectorAll('button, a.action-btn')].some((b) => /\bFetch/i.test(t(b))));
  check('  no visible word fetch, fetches, fetched or fetching', !/\b[Ff]etch(es|ed|ing)?\b/.test(visible()), (visible().match(/.{30}\b[Ff]etch\w*.{20}/) || [])[0]);

  // Sign in, not Log in (R039, R123).
  check('Sign in is the action: no Log in, Log out or login noun in the page text', !/\b(Log in|Log out|log in|log out)\b|(?<![-\w])[Ll]ogin\b|\blogged in\b/.test(visible()),
    (visible().match(/.{30}(?<![-\w])[Ll]og ?(in|out|ged)\b.{20}/) || [])[0]);

  // Select, not Choose.
  check('Select is the verb for a choice: no "choose", "choosing" or "chosen" in the page text', !/\b[Cc]hoos(e|es|ing)\b|\b[Cc]hosen\b/.test(visible()),
    (visible().match(/.{30}\b[Cc]hoos\w*.{20}|.{30}\b[Cc]hosen.{20}/) || [])[0]);

  // Public and Private are not audience words.
  check('Audience words are Guests, Users and Administrators: no Public or Private chip', ![...d.querySelectorAll('.chip, .chip-btn')].some((c) => /^(Public|Private)$/.test(t(c))));

  // The same words in the scripts' own prose (strings with a space), not in comments or code names.
  const files = ['admin.js', 'admin-widgets.js', 'admin-layout.js', 'admin-tour.js', 'admin-mock.js'];
  const bad = [];
  for (const f of files) {
    const src = fs.readFileSync(`${WEB}/${f}`, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    for (const m of src.matchAll(/'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\\n]|\\.)*)`|"((?:[^"\\\n]|\\.)*)"/g)) {
      const s = (m[1] ?? m[2] ?? m[3]).replace(/\$\{[^}]*\}/g, '');   // the words, not a template's code
      // prose only: a code name or a path has no space; a call, a tag or an attribute (Mock controls' code lines) is code
      if (!/\S +\S/.test(s) || /^[/#.\[<]|^[\w$.]+\(|^\w+="/.test(s)) continue;
      if (/\b(Log in|Log out|log in|log out|Choose|choose|choosing|chosen)\b|(?<![-\w])[Ll]ogin\b|\b[Ff]etch(es|ed|ing)?\b|n't\b/.test(s)) bad.push(`${f}: ${s.slice(0, 90)}`);
    }
  }
  check('no Log in, Choose, Fetch or contraction in the scripts\' prose', !bad.length, bad.slice(0, 5).join(' | '));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
