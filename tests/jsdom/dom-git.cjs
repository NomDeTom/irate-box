// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Git page as cards in jsdom, against a fixture of
// every kind: own public, anyone-pushes, private and building, private read-only, a mirror, and
// its submodule's mirror (no card). The filters and counts, the badges in words, Manage as a
// drawer and as a side panel, each part asking the hub. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-git.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['el.js', 'admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const T = 1790950000;
const repo = (o) => Object.assign({ area: 'public', write: 'admin', preset: 'public-admin-writes', preset_text: '', mirror_of: null,
  description: '', size: 2e5, branches: 1, last_commit: T, can_build: false, build: true, has_script: false, last_build: null,
  submodule_of: null }, o, { url: `${o.area === 'private' ? '/git-private/' : '/git/'}${o.name}.git/` });
const gitData = { installed: true, max_push: 67108864, free: 5e10, now: T,
  presets: { public: [{ name: 'public-everything', write: 'everyone', text: 'anyone on the network can browse, clone and push' },
    { name: 'public-admin-writes', write: 'admin', text: 'anyone can browse and clone; pushing needs the admin login' },
    { name: 'public-read-only', write: 'nobody', text: 'anyone can browse and clone; nobody can push' }],
  private: [{ name: 'private-to-admin', write: 'admin', text: 'browse, clone and push with the admin login' },
    { name: 'private-read-only', write: 'nobody', text: 'browse and clone with the admin login; nobody can push' }] },
  repos: [
    repo({ name: 'demo', description: 'Field notes from the hill', branches: 2 }),
    repo({ name: 'open', write: 'everyone', preset: 'public-everything' }),
    repo({ name: 'fw-ci', area: 'private', preset: 'private-to-admin', can_build: true, has_script: true,
      last_build: { run: 'fw-ci/3', state: 'passed', started: T, duration: 600, branch: 'main', commit: 'a'.repeat(40) } }),
    repo({ name: 'archive', area: 'private', write: 'nobody', preset: 'private-read-only', last_commit: null, branches: 0 }),
    repo({ name: 'meshtastic-firmware', write: 'nobody', preset: 'public-read-only', mirror_of: 'https://github.com/meshtastic/firmware', size: 15e6 }),
    repo({ name: 'meshtastic-firmware--protobufs', write: 'nobody', preset: 'public-read-only', mirror_of: 'https://github.com/meshtastic/protobufs',
      submodule_of: 'meshtastic-firmware' }),
  ],
  mirrors: [{ name: 'meshtastic-firmware', area: 'public', upstream: 'https://github.com/meshtastic/firmware', branches: ['master'],
    groups: [], follow: 'latest', pin: '', history: 'shallow', budget_mb: 2048, submodules: true,
    status: { outcome: 'up to date', described: 'Firmware of the mesh', size: 15e6, checked: T, updated: T,
      submodules: { protobufs: { repo: 'meshtastic-firmware--protobufs', url: 'https://github.com/meshtastic/protobufs', kept: ['b'.repeat(40)], size: 1e6 } } } }],
  running: false };
const ciData = { installed: true, queued: 0, script: '.irate-ci.sh', time_limit: 43200, keep_runs: 5, runs_bytes: 5e6,
  memory: { max: String(300 * 2 ** 20), high: String(230 * 2 ** 20) }, mirrored: ['https://github.com/meshtastic/firmware', 'https://github.com/meshtastic/protobufs'],
  pio_deps: true, env: { CI: '1', CI_ARTIFACTS: 'a folder', HOME: 'kept' },
  templates: { make: { title: 'Run make', script: '#!/bin/bash\nmake\n' } },
  runs: [{ run: 'fw-ci/3', repo: 'fw-ci', branch: 'main', commit: 'a'.repeat(40), state: 'failed', started: T, finished: T + 60, duration: 60 },
    { run: 'fw-ci/2', repo: 'fw-ci', branch: 'main', commit: 'b'.repeat(40), state: 'passed', started: T - 99, finished: T - 50, duration: 49, keep: true }] };
const runView = { run: 'fw-ci/3', status: ciData.runs[0], log: '== start\nbuilding\n', offset: 0, next: 19, size: 19, steps: ['start'],
  artifacts: [{ name: 'fw.bin', size: 4321 }] };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); }); // other panes, not stood in
const dom = new JSDOM(html, { url: 'http://box.local/admin/#git', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
require("./answer-asks.cjs")(w); // its questions on the page answered by its confirm stub
const posted = [];
let holdGit = false, failGit = false, releaseGit = null;
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posted.push([u, body]);
    if (failGit) return new Response('{}', { status: 503 });
    if (holdGit) return new Promise((resolve) => { releaseGit = () => resolve(new Response(JSON.stringify(u === '/admin/git' ? gitData : { id: 'x' }), { status: 200 })); });
    // As the hub would: a moved repository is in its new area (its card and drawer follow it).
    if (body.action === 'move') Object.assign(gitData.repos.find((r) => r.name === body.name), { area: body.to, url: `/git-private/${body.name}.git/` });
    if (u === '/admin/ci') return new Response(JSON.stringify(body.action === 'build' ? { branch: 'main', commit: 'c'.repeat(40), queued: true, snapshot: ciData }
      : body.action === 'settings' ? ciData : { queued: true }), { status: 202 });
    return new Response(JSON.stringify(u === '/admin/git' ? gitData : { id: 'x' }), { status: 200 });
  }
  if (u === '/admin/git') return new Response(JSON.stringify(gitData), { status: 200 });
  if (u === '/admin/ci') return new Response(JSON.stringify(ciData), { status: 200 });
  if (u.startsWith('/admin/ci/run?')) return new Response(JSON.stringify(runView), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = () => new Promise((r) => setTimeout(r, 50));
(async () => {
  await wait();
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const cards = () => [...d.querySelectorAll('#git-grid > .source-card')].filter((c) => c.id !== 'git-add');
  const card = (name) => cards().find((c) => t(c.querySelector('.source-title')) === `${name}.git`);
  const button = (root, label) => root && [...root.querySelectorAll('button')].find((b) => t(b) === label);

  check('a source card per local repository, then the New card last',
    cards().length === 4 && d.querySelector('#git-grid > :last-child .source-title').textContent === 'New repository', cards().length);
  check('a mirror is not in Git Repositories', !card('meshtastic-firmware') && !!d.querySelector('#git-mirrors .source-card .source-title'));
  check('the mirror list has its own card and the add card', d.querySelectorAll('#git-mirrors .source-card').length === 2
    && t(d.querySelector('#git-mirrors .source-card .source-title')) === 'meshtastic-firmware.git');
  const bar = d.querySelector('#git-filter .aw-filter');
  check('the shared filter bar: search, tags and a count', bar && bar.querySelector('input[type=search]')
    && [...bar.querySelectorAll('.filter-chip')].map(t).join('|') === 'Guests|Administrators|Builds'
    && t(bar.querySelector('.aw-count')) === '4 of 4', bar && t(bar));
  const demo = card('demo');
  check('the card head: local source, pill, checked state and facts', /Local repository/.test(t(demo))
    && /Access: Guests/.test(t(demo)) && /Schedule: on push/.test(t(demo)), t(demo));
  [...bar.querySelectorAll('.filter-chip')].find((c) => t(c) === 'Guests').click();
  check('a tag shows only its repositories', cards().filter((c) => !c.hidden).length === 2);
  [...bar.querySelectorAll('.filter-chip')].find((c) => t(c) === 'Guests').click();
  const search = bar.querySelector('input[type=search]');
  search.value = 'hill'; search.dispatchEvent(new w.Event('input'));
  check('the search looks at descriptions too', cards().filter((c) => !c.hidden).map((c) => t(c.querySelector('.source-title'))).join() === 'demo.git');
  search.value = ''; search.dispatchEvent(new w.Event('input'));

  demo.querySelector('.source-head').click();
  let panel = d.querySelector('#git-grid .source-panel:not([hidden])');
  check('a card opens a drawer under the grid', panel && !panel.hidden
    && [...panel.querySelectorAll('.git-manage section > h4')].map(t).join(' | ') === 'Access | About | Publish to… | Delete');
  check('Access has its questions and chips', [...panel.querySelectorAll('legend')].map(t).join(' | ') === 'Who can see it? | Who can push? | Build on push?'
    && panel.querySelectorAll('.chip-group[aria-label="Who can see it?"] .chip').length === 2);
  posted.length = 0;
  [...panel.querySelectorAll('.chip-group[aria-label="Who can see it?"] .chip')].find((c) => t(c) === 'Administrators').click();
  await wait();
  check('choosing Administrators moves the repository', posted.some(([u, b]) => u === '/admin/git' && b.action === 'move' && b.name === 'demo' && b.to === 'private'));

  const mirror = d.querySelector('#git-mirrors .source-card');
  mirror.querySelector('.source-head').click();
  const mp = d.querySelector('#git-mirrors .source-panel:not([hidden])');
  const mirrorBox = d.getElementById('mirror-description-meshtastic-firmware');
  check('the mirror drawer: the description and its submodule chips', mp && mirrorBox
    && mirrorBox.placeholder === 'Firmware of the mesh'
    && mp.querySelectorAll('.chip-group[aria-label="Mirror its submodules too"] .chip').length === 2);
  mirrorBox.value = ' Mesh firmware ';
  [...mp.querySelectorAll('button')].find((b) => t(b) === 'Change description').click();
  await wait();
  check('Change description asks the hub, trimmed', posted.some(([, b]) => b.action === 'mirror-change' && b.mirror.description === 'Mesh firmware'));

  const addNew = d.getElementById('git-add');
  addNew.querySelector('.source-head').click();
  const np = d.querySelector('#git-grid .source-panel:not([hidden])');
  check('New opens the create form in its drawer', np && np.querySelector('#git-create'));
  const f = d.getElementById('git-create').elements;
  f.name.value = 'notes'; f.area.value = 'private';
  d.getElementById('git-create').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('creating asks the hub', posted.some(([, b]) => b.action === 'create' && b.name === 'notes' && b.area === 'private'));

  check('Builds has its own page, and no build controls here', d.getElementById('git-builds-open').getAttribute('href') === '/admin/git.html'
    && !d.getElementById('ci-runs') && !d.getElementById('ci-cache-form'));
  check('no stray null or undefined on the page', ![...d.querySelectorAll('main *')].some((n) => /^(null|undefined|NaN|\[object Object\])$/.test(t(n))));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
