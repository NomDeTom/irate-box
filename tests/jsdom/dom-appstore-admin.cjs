// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The App store cards on /admin in jsdom: a card stays open across a redraw, a tick not yet held survives a
// poll, and Remove asks before it sends. The snapshot is a fixture of appstore_admin.py's answer; no hub runs.
// Usage: [JSDOM=…/jsdom] node dom-appstore-admin.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(path.join(WEB, 'admin.html'), 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js']
  .map((f) => fs.readFileSync(path.join(WEB, f), 'utf8'))
  .join(';\n');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const num = (n) => ({ size_min: n, size_max: n });
const kind = (shape, state) => ({
  shape, class: 'phone app', example: shape, names: { 'v2.0.0': shape }, releases: ['v2.0.0'], position: [0],
  seen: { stable: 1 }, ...num(45000000), tags: {}, variant: [], companions: [], state,
});
const holder = {
  id: 'demo', title: 'Demo', repo: 'a/b', provider: 'github',
  holds: [{ label: 'the APK', include: 'apk$' }],
  channels: { stable: 2, 'pre-release': 0 }, extra_channels: [], tag_pattern: '^v?\\d+(\\.\\d+)+',
  min_age_days: 0, budget_mb: 0, visibility: 'public',
  status: { last_check: '2026-10-28T12:00:00Z', outcome: 'demo: 1 version', error: null, latest: null,
    surveyed: '2026-10-28T12:00:00Z', survey_error: null },
  survey: {
    window: { stable: ['v2.0.0'], 'pre-release': [] }, short: [], dimensions: {}, eras: [], twins: [],
    kinds: [kind('the-app.apk', 'all'), kind('the-app-arm64.apk', 'none')],
    own: [], preview: {}, estimate: { tag: 'v2.0.0', per_version: 45000000, total: 90000000 },
    surveyed: '2026-10-28T12:00:00Z',
  },
  kept: [{ tag: 'v2.0.0', channel: 'stable', published: '2026-10-28T00:00:00Z',
    files: [{ name: 'the-app.apk', size: 45000000, sha256: 'a'.repeat(64) }], bytes: 45000000 }],
  bytes: 45000000,
};
const snapshot = (running = false) => ({
  enabled: true, holders: [holder], presets: [],
  policy: { holders_every_hours: 24, holders_auto: false, min_free_mb: 512 },
  free_mb: 1000, bytes_all: 45000000, running, progress: null,
});
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented|navigation/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#appstore', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
require('./answer-asks.cjs')(w);
let running = false;
const posts = [];
w.fetch = async (url, opts = {}) => {
  if (opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posts.push([url, body]);
    if (url === '/admin/appstore') return new Response(JSON.stringify(snapshot(running)), { status: 200 });
    return new Response('{}', { status: 200 });
  }
  if (url === '/admin/appstore') return new Response(JSON.stringify(snapshot(running)), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
const wait = () => new Promise((r) => setTimeout(r, 80));
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const button = (root, label) => [...root.querySelectorAll('button')].find((b) => t(b) === label);
const card = () => w.document.querySelector('.appstore-holder[data-holder="demo"]');
const tick = () => card().querySelector('.appstore-kinds input[type=checkbox]');

(async () => {
  await wait();
  check('the card is drawn, its survey table with it', card() && card().querySelectorAll('.appstore-kinds tr').length === 3,
    card() && card().querySelectorAll('.appstore-kinds tr').length);
  const head = card().querySelector('.aw-card-head');
  head.click();
  check('opening the card shows its body', !card().querySelector('.appstore-card').hidden && head.getAttribute('aria-expanded') === 'true');
  tick().click();
  check('a tick changed, not yet held, is a draft', tick().checked === false && !posts.some(([, b]) => b.action === 'holds'));

  // A redraw, as an action makes: the card stays open and the draft tick stays.
  button(card(), 'Check now').click();
  await wait();
  check('a redraw keeps the card open', !card().querySelector('.appstore-card').hidden
    && card().querySelector('.aw-card-head').getAttribute('aria-expanded') === 'true',
    card().querySelector('.appstore-card').hidden + '/' + card().querySelector('.aw-card-head').outerHTML.slice(0,200) + '/' + posts.map((x) => x[1].action).join(','));
  check('  and the draft tick survives it', tick().checked === false);

  // A poll while a run works redraws only the status line: the card and the draft stay.
  running = true;
  button(card(), 'Check now').click();
  await wait();
  check('a running card starts a poll', /Working/.test(t(w.document.getElementById('appstore-status'))));
  running = false;
  await new Promise((r) => setTimeout(r, 2200));
  check('a poll leaves the card open and the draft tick as it was', !card().querySelector('.appstore-card').hidden
    && tick().checked === false && !/Working/.test(t(w.document.getElementById('appstore-status'))));

  // Remove asks first; only Confirm sends it.
  w.answerAsks = false;
  button(card(), 'Remove').click();
  await wait();
  const ask = w.document.querySelector('.aw-ask');
  check('Remove asks first, and sends nothing until answered', ask && !posts.some(([, b]) => b.action === 'remove-holder'), ask && t(ask));
  ask.querySelectorAll('button')[0].click();
  await wait();
  check('  Confirm sends the removal', posts.some(([, b]) => b.action === 'remove-holder'));
  check('no page error', errors.length === 0, errors.join(' | '));
  console.log(fails ? `${fails} failures` : 'all passed');
  process.exit(fails ? 1 : 0);
})();
