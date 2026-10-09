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
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
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
    status: { outcome: 'up to date', size: 15e6, checked: T, updated: T,
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
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posted.push([u, body]);
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
  const cards = () => [...d.querySelectorAll('#git-grid > .git-card:not(.git-new)')];
  const card = (name) => cards().find((c) => t(c.querySelector('h4')) === `${name}.git`);
  const button = (root, label) => root && [...root.querySelectorAll('button')].find((b) => t(b) === label);
  const chip = (label) => [...d.querySelectorAll('#git-chips-kind .chip, #git-chips-area .chip')].find((b) => t(b).replace(/ \d+$/, '') === label);

  check('a card per repository, then the New card last', d.querySelector('#git-grid > :last-child').classList.contains('git-new') && cards().length === 5, cards().length);
  check('a submodule mirror has no card of its own', !card('meshtastic-firmware--protobufs'));
  check('chips with counts: All 5 · Mine 4 · Mirrors 1 · Builds 1', ['All 5', 'Mine 4', 'Mirrors 1', 'Builds 1'].every((l) => chip(l.split(' ')[0]) && t(chip(l.split(' ')[0])) === l),
    [...d.querySelectorAll('#git-chips-kind .chip')].map(t).join(', '));
  check('and Public 3 · Private 2', t(chip('Public')) === 'Public 3' && t(chip('Private')) === 'Private 2', [...d.querySelectorAll('#git-chips-area .chip')].map(t).join(', '));
  check('badges in words: public, the admin pushes', /Public/.test(t(card('demo').querySelector('.badges'))) && /the admin pushes/.test(t(card('demo').querySelector('.badges'))));
  check('anyone pushes', /anyone pushes/.test(t(card('open').querySelector('.badges'))));
  check('read-only, private', /Private/.test(t(card('archive').querySelector('.badges'))) && /read-only/.test(t(card('archive').querySelector('.badges'))));
  check('the last build, with its result and date', /✅ build passed, 2026-/.test(t(card('fw-ci').querySelector('.badges'))), t(card('fw-ci').querySelector('.badges')));
  check('the description labelled, only when there is one', /^About Field notes/.test(t(card('demo').querySelector('.git-about'))) && !card('open').querySelector('.git-about'));
  check('a facts line: last commit, branches, size', /^Last commit 2026-\d\d-\d\d · 2 branches · /.test(t(card('demo').querySelector('.git-facts'))), t(card('demo').querySelector('.git-facts')));
  check('an empty one says so', /^Empty/.test(t(card('archive').querySelector('.git-facts'))));
  check('access in one sentence', t(card('fw-ci').querySelector('.git-access')) === 'Only the admin can see and clone it; only the admin can push; pushes are built.',
    t(card('fw-ci').querySelector('.git-access')));
  const mc = card('meshtastic-firmware');
  check('a mirror: the Mirror badge, from where, its update; Manage in Library, no Manage of its own', /Mirror/.test(t(mc.querySelector('.badges')))
    && /from github\.com\/meshtastic\/firmware · updated 2026-/.test(t(mc)) && mc.querySelector('a[href="#mirrors"]')
    && ![...mc.querySelectorAll('button')].some((b) => t(b) === 'Manage'), t(mc));

  chip('Mirrors').click();
  check('the Mirrors chip shows only mirrors', cards().map((c) => t(c.querySelector('h4'))).join() === 'meshtastic-firmware.git');
  chip('Builds').click();
  check('the Builds chip shows only what builds', cards().map((c) => t(c.querySelector('h4'))).join() === 'fw-ci.git');
  chip('All').click(); chip('Private').click();
  check('Private: the two private ones', cards().length === 2);
  chip('Public and private').click();
  const search = d.getElementById('git-search');
  search.value = 'hill'; search.dispatchEvent(new w.Event('input'));
  check('search looks at descriptions too', cards().map((c) => t(c.querySelector('h4'))).join() === 'demo.git');
  search.value = ''; search.dispatchEvent(new w.Event('input'));

  // Manage as a drawer: right after its card, across the grid.
  [...card('demo').querySelectorAll('button')].find((b) => t(b) === 'Manage').click();
  let drawer = d.querySelector('#git-grid > .git-drawer');
  check('Manage opens a drawer right after its card', drawer && drawer.previousElementSibling === card('demo')
    && card('demo').querySelector('[aria-expanded="true"]'), drawer && drawer.previousElementSibling && t(drawer.previousElementSibling.querySelector('h4')));
  const legends = [...drawer.querySelectorAll('legend')].map(t);
  check('Access in three questions', legends.join(' | ') === 'Who can see it? | Who can push? | Build on push?', legends.join(' | '));
  check('a public repository: three push choices, and "never build" said', drawer.querySelectorAll('input[name^="push-"]').length === 3
    && /Public repositories never build/.test(t(drawer)) && !drawer.querySelector('input.git-build'));
  check('the parts: Access, About, Publish to…, and Delete last, apart', [...drawer.querySelectorAll('section > h4')].map(t).join(' | ') === 'Access | About | Publish to… | Delete'
    && drawer.lastElementChild.classList.contains('git-danger'));
  drawer.querySelector('input[name^="push-"][value="public-everything"]').click();
  await wait();
  check('choosing Anyone asks first, then asks the hub', posted.some(([u, b]) => u === '/admin/git' && b.action === 'preset' && b.name === 'demo' && b.preset === 'public-everything'),
    JSON.stringify(posted));
  drawer = d.querySelector('#git-grid > .git-drawer');
  check('the drawer stays open after a change', drawer && drawer.previousElementSibling === card('demo'));
  drawer.querySelector('input[name^="see-"][value="private"]').click();
  await wait();
  check('Only the admin (see) moves it to private', posted.some(([, b]) => b.action === 'move' && b.name === 'demo' && b.to === 'private'));
  check('  and its drawer follows it', d.querySelector('.git-drawer') && /Only the admin can see/.test(t(d.querySelector('.git-drawer .git-manage-head + section'))));
  drawer = d.querySelector('.git-drawer');
  drawer.querySelector('textarea.git-desc').value = 'New words';
  [...drawer.querySelectorAll('button')].find((b) => t(b) === 'Save').click();
  await wait();
  check('About: Save sends the description', posted.some(([, b]) => b.action === 'describe' && b.description === 'New words'));
  drawer = d.querySelector('.git-drawer');
  drawer.querySelector('select.git-publish-to').value = 'private/fw-ci';
  drawer.querySelector('input.git-publish-refs').value = 'main dev';
  [...drawer.querySelectorAll('button')].find((b) => t(b) === 'Publish').click();
  await wait();
  check('Publish sends the target and the refs', posted.some(([, b]) => b.action === 'publish' && b.to.area === 'private' && b.to.name === 'fw-ci' && b.refs.join() === 'main,dev'));
  check('mirrors are not offered as a target', ![...d.querySelectorAll('.git-drawer select.git-publish-to option')].some((o) => /meshtastic/.test(o.value)));
  [...d.querySelectorAll('.git-drawer button')].find((b) => /^Delete /.test(t(b))).click();
  await wait();
  check('Delete asks, then asks the hub, and the drawer closes', posted.some(([, b]) => b.action === 'delete' && b.name === 'demo') && !d.querySelector('.git-drawer'));

  // A private repository that builds: the switch.
  [...card('fw-ci').querySelectorAll('button')].find((b) => t(b) === 'Manage').click();
  drawer = d.querySelector('.git-drawer');
  const sw = drawer.querySelector('input.git-build');
  check('a building repository: the build switch, on, and two push choices', sw && sw.checked && drawer.querySelectorAll('input[name^="push-"]').length === 2);
  sw.checked = false; sw.dispatchEvent(new w.Event('change'));
  await wait();
  check('switching builds off asks the hub', posted.some(([, b]) => b.action === 'build' && b.name === 'fw-ci' && b.on === false));

  // Manage opens under its card only: no side panel, nothing beside the grid.
  check('no side panel, nor its choice', !d.getElementById('git-side') && !d.getElementById('git-manage-as'));
  [...d.querySelectorAll('.git-drawer button')].find((b) => t(b) === 'Close').click();
  check('Close folds the drawer', !d.querySelector('#git-grid .git-drawer'));

  // New: an empty repository, or a mirror (under Mirrors).
  d.querySelector('#git-grid .git-new').click();
  const np = d.querySelector('#git-grid .git-drawer.git-manage, #git-grid .git-drawer .git-manage, #git-grid .git-manage');
  check('New offers an empty repository or a mirror', np && /An empty repository/.test(t(np)) && np.querySelector('a[href="#mirrors"]') && np.querySelector('#git-create'));
  const f = d.getElementById('git-create').elements;
  f.name.value = 'notes'; f.area.value = 'private';
  d.getElementById('git-create').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('creating asks the hub', posted.some(([, b]) => b.action === 'create' && b.name === 'notes' && b.area === 'private'));

  // Builds: the limits, what is offline, a run's own view, Build now and the templates.
  await wait(100);
  check('builds: the limits said', /Each build: up to 12 hours, memory capped at 300\.0 MB, the lowest CPU priority\. The runs use 4\.8 MB; the newest 5 per repository are kept/.test(t(d.getElementById('ci-limits'))),
    t(d.getElementById('ci-limits')));
  check('builds: what comes from the box with no internet', /2 URLs come from this box's mirrors \(github\.com\/meshtastic\/firmware/.test(t(d.getElementById('ci-offline')))
    && /PlatformIO's packages come from the library's cache/.test(t(d.getElementById('ci-offline'))), t(d.getElementById('ci-offline')));
  check('builds: a kept run says so', /· kept/.test(t(d.getElementById('ci-runs'))));
  [...d.querySelectorAll('#ci-runs button')].find((b) => t(b) === 'View').click();
  await wait(100);
  const rv = d.getElementById('ci-run-view');
  check('a run\'s view: its steps, its log, its artifacts with sizes, Keep and Delete', !rv.hidden && /start/.test(t(rv.querySelector('.ci-steps')))
    && rv.querySelector('pre.ci-log').textContent === '== start\nbuilding\n' && /fw\.bin \(0\.1 MB\)/.test(t(rv)) && button(rv, 'Keep') && button(rv, 'Delete'), t(rv));
  w.confirm = () => true;
  button(rv, 'Delete').click();
  await wait();
  check('  Delete asks the hub (the builder does it)', posted.some(([u, b]) => u === '/admin/ci' && b.action === 'delete' && b.run === 'fw-ci/3') && rv.hidden);
  [...card('fw-ci').querySelectorAll('button')].find((b) => t(b) === 'Manage').click();
  const dr = d.querySelector('.git-drawer');
  check('Manage: Build now, and Set up builds with the variables and templates', button(dr, 'Build now') && /Set up builds/.test(t(dr.querySelector('details.ci-setup')))
    && /CI_ARTIFACTS/.test(t(dr.querySelector('.ci-env'))) && /Run make/.test(t(dr.querySelector('.ci-template'))));
  button(dr, 'Build now').click();
  await wait();
  check('Build now asks the hub, and says what it queued', posted.some(([u, b]) => u === '/admin/ci' && b.action === 'build' && b.repo === 'fw-ci')
    && /Queued: fw-ci\.git main at cccccc/.test(t(d.getElementById('git-note'))), t(d.getElementById('git-note')));
  const kf = d.getElementById('ci-keep-form');
  kf.elements.keep.value = '8';
  kf.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('runs kept per repository: a setting', posted.some(([u, b]) => u === '/admin/ci' && b.action === 'settings' && b.keep_runs === 8));
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  check('the grid as Kiwix\'s: minmax(18rem, 1fr)', /\.card-grid \{[^}]*minmax\(18rem, 1fr\)/.test(css));
  check('a drawer spans the grid', /\.git-drawer \{ grid-column: 1 \/ -1; \}/.test(css));
  const walker = d.createTreeWalker(d.body, w.NodeFilter.SHOW_TEXT);
  const stray = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/^(null|undefined|NaN|\[object Object\])$/.test(n.textContent.trim())) stray.push(n.textContent);
  check('no stray null or undefined on the page', !stray.length, stray.join(','));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
