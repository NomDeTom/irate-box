// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin page's layout in jsdom, against fixtures: the sidebar's groups, the clock's own
// pane, the doctors under Health, and the public/private/off switch on add-ons and the hub's
// built-in parts. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-admin-ui.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const adminApps = require('./admin-apps-fixture.cjs');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const health = JSON.parse(fs.readFileSync(`${__dirname}/health-fixture.json`, 'utf8'));
health.report.findings.push(
  { id: 'clock', check: 'Clock', status: 'warn', detail: 'No network time.', fix: '', actions: [{ choice: 'clock-set', label: 'Set the clock from this browser' }] },
  { id: 'rtc', check: 'Clock module', status: 'problem', detail: 'DS3231 on bus 1 does not answer.', fix: '', actions: [] });
health.helper = { stuck: false, waiting: 0, oldest: 0, commands: [] };
const accessData = { apps: [
  { id: 'drop', title: 'File drop', mode: 'public', login: false, kind: 'builtin', unit: false },
  { id: 'wiki', title: 'Kiwix', mode: 'private', login: false, kind: 'builtin', unit: true },
  { id: 'git', title: 'Git', mode: 'public', login: false, kind: 'builtin', unit: false },
  { id: 'notes', title: 'Notes', mode: 'off', login: false, kind: 'addon', unit: true },
  { id: 'term', title: 'Terminal', mode: 'public', login: true, kind: 'addon', unit: true },
], results: [], seen: { about: 'auto', 'tools-rf': 'hidden' } };
// The librarian's snapshot, with Excalidraw kept current (F6: its own updates on its page).
const library = { policy: { keep_old: 1, check_every_hours: 24, min_free_mb: 500, auto_install: 1 }, token_set: false, running: false, types: [],
  progress: null, free_mb: 51000, job: {}, status: { draw: { last_check: '2026-10-08 03:00', outcome: 'up to date' } },
  sources: [{ kind: 'app', name: 'draw', type: 'bundle', repo: 'NomDeTom/excalidraw', workflow: 'irate-box-bundle.yml', branch: 'main' }],
  apps: { draw: { title: 'Excalidraw', installed: { commit: 'abc1234def', repository: 'NomDeTom/excalidraw', ref: 'main', built: '2026-10-01T00:00:00Z', has_previous: false }, pin: null } } };
const tilesData = { tiles: [{ id: 'drop', name: 'File drop', icon: '📥' }, { id: 'about', name: 'About', icon: 'ℹ️' }], state: { order: [] } };
const addons = { addons: [{ id: 'notes', title: 'Notes', summary: 'A notebook.', added: true, active: false },
  { id: 'term', title: 'Terminal', summary: 'A shell.', added: true, active: true }], progress: null, pending: 0, results: [], log: [] };
const update = { state: { up_to_date: false, available: 'a4aad09', available_date: '2026-10-02', branch: 'main', fetched: 1790950000,
  changes: ['one'], changes_known: true, verified: null,
  checks: [{ name: 'Python files compile', ok: true, warn: false, detail: '' },
    { name: 'The new nginx site passes nginx -t', ok: false, warn: false, detail: 'irate-box.nginx is missing from the update' }] },
  progress: null, pending: 0, results: [], log: [], doctor: null,
  auto: { hub_check_every_hours: 24, hub_auto: 2, hub_window_start: 2, hub_window_end: 5,
    state: { last: { step: 'check', ok: true, message: 'update available: 1 new commit', at: 1790950000 }, note: 'a4aad09 is ready; installs between 02:00 and 05:00' } } };
const gitData = { installed: true, max_push: 67108864, free: 5e10, now: 1790950000,
  presets: { public: [{ name: 'public-everything', write: 'everyone', text: 'anyone on the network can browse, clone and push' },
    { name: 'public-admin-writes', write: 'admin', text: 'anyone can browse and clone; pushing needs the admin login' },
    { name: 'public-read-only', write: 'nobody', text: 'anyone can browse and clone; nobody can push' }],
  private: [{ name: 'private-to-admin', write: 'admin', text: 'browse, clone and push with the admin login' },
    { name: 'private-read-only', write: 'nobody', text: 'browse and clone with the admin login; nobody can push' }] },
  repos: [{ name: 'demo', area: 'public', url: '/git/demo.git/', write: 'admin', preset: 'public-admin-writes',
    preset_text: 'anyone can browse and clone; pushing needs the admin login', description: '', size: 1000, branches: 1, last_commit: 1790950000 },
  { name: 'mirror', area: 'public', url: '/git/mirror.git/', write: 'nobody', preset: 'public-read-only',
    preset_text: 'anyone can browse and clone; nobody can push', description: '', size: 1000, branches: 1, last_commit: null },
  { name: 'secret', area: 'private', url: '/git-private/secret.git/', write: 'admin', preset: 'private-to-admin',
    preset_text: 'browse, clone and push with the admin login', description: '', size: 1000, branches: 1, last_commit: null },
  { name: 'firmware', area: 'public', url: '/git/firmware.git/', write: 'nobody', preset: 'public-read-only', mirror_of: 'https://github.com/meshtastic/firmware',
    preset_text: 'anyone can browse and clone; nobody can push', description: '', size: 5e8, branches: 1, last_commit: null }],
  mirrors: [{ name: 'firmware', area: 'public', upstream: 'https://github.com/meshtastic/firmware', branches: ['master'],
    groups: [{ name: 'release', kind: 'release', pattern: '*', keep: 2 }, { name: 'prerelease', kind: 'prerelease', pattern: '*', keep: 2 }],
    follow: 'latest', pin: '', history: 'shallow', budget_mb: 2048,
    status: { outcome: '5 fetched, 0 dropped', size: 5e8, checked: 1790950000,
      releases: { list: [['v2.8.1', true, false], ['v2.8.0.47db0e3', true, true]], fetched: 1790940000 },
      releases_cached: { since: 1790940000, why: 'GitHub API rate limit reached (60/hour without a token)' } } }], running: false };
// Firmware (step 23): the source not mirrored yet; the build cache kept, its control on the Git page.
const fwFix = { settings: { enabled: false, boards: [], keep_alpha: 2, keep_beta: 1, cache: 'native', configs: true },
  status: { last_check: '2026-10-06T15:00:00Z', outcome: 'build cache 215 MB', cache: { version: '2.8.1.8e6a88d', mode: 'native', bytes: 215 * 2 ** 20 } },
  targets: [], free_mb: 9000, source: null };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#clock', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') {
    posted.push([u, JSON.parse(opts.body)]);
    if (u === '/admin/git') return new Response(JSON.stringify(gitData), { status: 200 });
    if (u === '/admin/tiles') return new Response(JSON.stringify({ tiles: [...tilesData.tiles].reverse(), state: { order: ['about', 'drop'] } }), { status: 200 });
    if (u === '/admin/firmware') {
      if (posted[posted.length - 1][1].action === 'flush-cache') return new Response(JSON.stringify({ ...fwFix, status: { ...fwFix.status, cache: null } }), { status: 200 });
      return new Response(JSON.stringify(fwFix), { status: 200 });
    }
    return new Response('{"id":"x"}', { status: 202 });
  }
  if (u === '/admin/git') return new Response(JSON.stringify(gitData), { status: 200 });
  if (u === '/admin/apps') return new Response(JSON.stringify({ apps: adminApps() }), { status: 200 });
  if (u === '/admin/health') return new Response(JSON.stringify(health), { status: 200 });
  if (u === '/admin/firmware') return new Response(JSON.stringify(fwFix), { status: 200 });
  if (u === '/admin/access') return new Response(JSON.stringify(accessData), { status: 200 });
  if (u === '/admin/tiles') return new Response(JSON.stringify(tilesData), { status: 200 });
  if (u === '/admin/moderation') return new Response(JSON.stringify({ now: 1790950000, messages: [], threads: [],
    queue: [{ key: 'shoutbox:1790949000:Moth', by: 'Moth', text: 'buy cheap things', where: 'Shoutbox', count: 3, reasons: { spam: 3 } }] }), { status: 200 });
  if (u.startsWith('/admin/library')) return new Response(JSON.stringify(library), { status: 200 });
  if (u === '/admin/update') return new Response(JSON.stringify(update), { status: 200 });
  if (u === '/admin/kit') return new Response(JSON.stringify({ kit: { name: 'irate-box-kit-abc1234-aarch64.tar', size: 95000000, at: 1790950000, books: [], contents: ['draw: from /usr/share/hub/apps/draw'] },
    progress: null, pending: 0, books: { count: 2, bytes: 3000000000 }, results: [] }), { status: 200 });
  if (u === '/admin/addons') return new Response(JSON.stringify(addons), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
setTimeout(() => {
  const d = w.document, t = (s) => [...d.querySelectorAll(s)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
  const side = [...d.querySelectorAll('.admin-side-list .admin-side-group, .admin-side-list a')].map((n) => n.textContent.trim());
  // The app-first menu (M4): Overview, Apps (a page per app, from the manifests), Moderation, System
  // (accounts under System, not Box: item 6), the Doctors last; nothing left over.
  const groups = t('.admin-side-group');
  check('sidebar: the groups in order, the Doctors last', groups.join('|') === 'Overview|Apps|Folders|Moderation|System|Doctors', groups.join('|'));
  const hi = side.indexOf('Doctors');
  check('sidebar: the three doctors under Doctors', side.slice(hi + 1).join('|') === 'Box doctor|Security doctor|Updates doctor', side.join('|'));
  check('sidebar: Clock and Appearance under System', side.indexOf('Clock') > side.indexOf('System') && side.indexOf('Appearance') > side.indexOf('System') && side.indexOf('Clock') < hi);
  check('sidebar: a page per app that owns sections, in the hub\'s order', (() => { const at = (n) => side.findIndex((x) => x.endsWith(n)); return ['Kiwix', 'Git', 'Web flasher', 'Mesh', 'Firmware Factory'].every((a, i, all) => at(a) > side.indexOf('Apps') && at(a) < side.indexOf('Moderation') && (!i || at(a) > at(all[i - 1]))); })(), side.join('|'));
  const sections = [...d.querySelectorAll('.admin-pane')];
  check('every section on exactly one page, none left over', sections.every((x) => x.parentElement.classList.contains('admin-page')) && !groups.includes('More'), sections.filter((x) => !x.parentElement.classList.contains('admin-page')).map((x) => x.id));
  const pageOf = (id) => d.getElementById(id).closest('.admin-page');
  // M6: each app's page starts with who opens it and who sees it, then its own sections (4f).
  // The access block as the mock has it (Tom, 2026-10-08): four chips for who opens it, four for who
  // sees the tile, ↑ Earlier / ↓ Later, held until Save.
  const rowOf = (root, label) => [...root.querySelectorAll('.access-field')].find((f) => f.querySelector('.aw-label').textContent === label);
  const chipsOf = (root, label) => { const r = root && rowOf(root, label); return r ? [...r.querySelectorAll('.chip')] : []; };
  const picked = (root, label) => (chipsOf(root, label).find((c) => c.getAttribute('aria-pressed') === 'true') || {}).textContent;
  const kiwix = d.getElementById('page-app-wiki');
  check('an app\'s page starts with its access, then its sections', kiwix && [...kiwix.querySelectorAll(':scope > .admin-pane')].map((x) => x.id).join(' ') === 'app-access-wiki books',
    kiwix && [...kiwix.querySelectorAll(':scope > .admin-pane')].map((x) => x.id).join(' '));
  check('  its access block: four chips each for who opens it and who sees it, Kiwix (private) at admin', kiwix
    && chipsOf(kiwix, 'Who can open it').map((c) => c.textContent).join('|') === 'guests|users|admin|off'
    && chipsOf(kiwix, 'Who sees the tile').map((c) => c.textContent).join('|') === 'guests|users|admin|hidden'
    && picked(kiwix, 'Who can open it') === 'admin' && picked(kiwix, 'Who sees the tile') === 'admin', kiwix && t('#app-access-wiki')[0]);
  check('an app with no sections still has its page, for its access', !!d.querySelector('#page-app-draw .access-block'));
  // F3: a tile with no switch (About, the folders) has who sees it, and nothing else of access.
  check('About\'s page holds only who sees its tile', !!d.querySelector('#page-app-about #app-access-about')
    && /Who sees it/.test(t('#app-access-about h2')[0]) && !rowOf(d.getElementById('app-access-about'), 'Who can open it'));
  check('a folder\'s page starts with who sees it, then what\'s in it', [...d.querySelectorAll('#page-app-tools-rf > .admin-pane')].map((x) => x.id).join(' ') === 'app-access-tools-rf folder-tools-rf');
  check('apps with a switch and no tile have a page: Serial, the calculators\' site, Syncthing', ['serial', 'tools', 'sync'].every((i) => d.querySelector(`#page-app-${i} .access-block`)));
  {
    // F3: the seen-only chips, and the tiles' order held until Save.
    const about = d.getElementById('app-access-about');
    check('About: who sees the tile, guests (the default) of four chips', picked(about, 'Who sees the tile') === 'guests');
    check('  and its place on the hub, earlier or later', !!rowOf(about, 'Order on the hub') && /2 of 2/.test(rowOf(about, 'Order on the hub').textContent));
    chipsOf(about, 'Who sees the tile')[3].click();
    check('  a choice waits for Save', picked(d.getElementById('app-access-about'), 'Who sees the tile') === 'hidden'
      && !posted.some((p) => p[0] === '/admin/visibility' && p[1].app === 'about'));
    [...d.querySelectorAll('#app-access-about button')].find((b) => b.textContent === 'Save').click();
    check('a hidden folder says so', picked(d.getElementById('app-access-tools-rf'), 'Who sees the tile') === 'hidden' && /No tile/.test(t('#app-access-tools-rf .access-block')[0]));
    const rows = [...d.querySelectorAll('#tile-order-list .aw-row')];
    check('the tiles in order, each saying who opens it and who sees it', rows.length === 2 && /opens: everyone · seen: as its access/.test(rows[0].textContent)
      && /opens: everyone · seen: everyone/.test(rows[1].textContent), rows.map((r) => r.textContent).join(' / '));
    rows[0].querySelector('.aw-row-head').click();
    [...d.querySelectorAll('#tile-order-list button')].find((b) => b.textContent === 'large').click();  // F5: sizes for all tiles
    check('a size chosen: the drop large, said in its line', /· large/.test(d.querySelector('#tile-order-list .aw-row').textContent));
    [...d.querySelectorAll('#tile-order-list button')].find((b) => /Later/.test(b.textContent)).click();
    const save = [...d.querySelectorAll('#tile-order-box button')].find((b) => b.textContent === 'Save');
    check('moved: Save offered, nothing sent yet', save && !save.disabled && !posted.some((p) => p[0] === '/admin/tiles'));
    save.click();
  }
  // F6: each app's page carries its slices: its own updates, what people made there, what was reported.
  check('Excalidraw\'s page: its access, its own updates, then Saved work', [...d.querySelectorAll('#page-app-draw > .admin-pane')].map((x) => x.id).join(' ') === 'app-access-draw app-updates-draw saved',
    [...d.querySelectorAll('#page-app-draw > .admin-pane')].map((x) => x.id).join(' '));
  check('  its own updates: the librarian\'s row, with Check and Update, without a second access switch', /Installed: abc1234/.test(t('#app-updates-draw')[0])
    && [...d.querySelectorAll('#app-updates-draw button')].some((b) => b.textContent === 'Update') && !d.querySelector('#app-updates-draw .access'));
  check('  and the Apps list still has the row, with its access', /Installed: abc1234/.test(t('#apps-list')[0]) && !!d.querySelector('#apps-list .access'));
  check('the shoutbox\'s page ends with what was reported there', [...d.querySelectorAll('#page-app-shoutbox > .admin-pane')].pop().id === 'app-flagged-shoutbox');
  check('  the shoutbox\'s reported post on its page; none on the board\'s', d.querySelectorAll('#app-flagged-shoutbox .aw-row').length === 1
    && /buy cheap things/.test(t('#app-flagged-shoutbox')[0]) && /Nothing reported here/.test(t('#app-flagged-board')[0]), t('#app-flagged-shoutbox')[0]);
  check('Moderation: everything people made, linking to all five', ['#saved', '#drop-mod', '#shoutbox-mod', '#board-mod', '#git'].every((h) => d.querySelector(`#made-list a[href="${h}"]`))
    && d.getElementById('made').closest('.admin-page').querySelector('.admin-page-title, h2').textContent.includes('Everything people made'));
  // F7: the Factory's building is inside the app, on its own page; /admin keeps its settings.
  check('the Factory in /admin: its tile setting and a link to its own page, no build form', !!d.querySelector('#factory #factory_tile')
    && d.querySelector('#factory-open').getAttribute('href') === '/admin/factory.html' && !d.getElementById('factory-form') && !d.getElementById('factory-runs'));
  check('All apps starts with the tiles in order', [...d.querySelectorAll('#page-tile-order > .admin-pane')].map((x) => x.id).join(' ') === 'tile-order apps addons');
  check('the Clock page is the one shown', !pageOf('clock').hidden && pageOf('health').hidden);
  check('clock findings in the Clock pane', t('#clock-findings li').length === 2 && t('#clock-findings li')[0].includes('Clock'), t('#clock-findings li'));
  check('no clock findings in the services doctor', !t('#health-findings li').some((x) => x.startsWith('🔴 Clock') || /Clock module|^.{0,3}Clock —/.test(x)), t('#health-findings li'));
  check('the clock\'s badge counts its problem', d.querySelector('a[href="#clock"]').dataset.badge === '1');
  check('the box doctor is titled so', t('#health-title')[0] === 'Box doctor');
  check('the security doctor has its own pane', !!d.querySelector('#secdoctor #audit-run') && !d.querySelector('#security #audit-run'));
  check('the updates doctor has its own pane', !!d.querySelector('#updoctor #update-doctor') && !d.querySelector('#updates #update-doctor'));
  check('Access has no Terminal card setting', !d.getElementById('show_term_card') && !t('#access h3').some((x) => /Terminal/.test(x)));
  const built = t('#builtin-list .setting-name');
  check('built-in parts listed with the access block each', built.join('|') === 'File drop|Kiwix|Git' && d.querySelectorAll('#builtin-list .access-set').length === 3, built);
  const on = (id) => [...d.querySelectorAll(`#${id} [aria-checked="true"]`)].map((b) => b.textContent);
  const notes = [...d.querySelectorAll('#addons-list .library-source')].find((r) => r.textContent.includes('Notes'));
  check('the Notes add-on carries its block, set to off', notes && picked(notes, 'Who can open it') === 'off');
  const term = [...d.querySelectorAll('#addons-list .library-source')].find((r) => r.textContent.includes('Terminal'));
  check('Terminal public still says it asks for the login', term && term.textContent.includes('still asks for the admin login'));
  const main = d.querySelector('.admin-main');
  check('background art: the controller for System (Clock)', main.dataset.art === 'controller', main.dataset.art);
  w.location.hash = '#secdoctor'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('background art: the doctor for Health', main.dataset.art === 'doctor', main.dataset.art);
  w.location.hash = '#books'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('#books opens Kiwix\'s own page, the librarian behind it', !pageOf('books').hidden && pageOf('books').getAttribute('aria-label') === 'Kiwix' && main.dataset.art === 'librarian', main.dataset.art);
  w.location.hash = '#backup'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('an old address still lands: #backup opens Updates and backup', !pageOf('backup').hidden && pageOf('backup').getAttribute('aria-label') === 'Updates and backup');
  w.location.hash = '#toolkits'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('background art: the workbench for Toolkits, the desk\'s size', main.dataset.art === 'workbench' && fs.existsSync(`${WEB}/art/krab-workbench.webp`)
    && /\[data-art="workbench"\] \.admin-art \{[^}]*krab-workbench\.webp[^}]*width: min\(46vw, 34rem\)/.test(fs.readFileSync(`${WEB}/style.css`, 'utf8')), main.dataset.art);
  check('the desk lit by its WebM loop (krab-loop.js, M3), over the still', !!d.querySelector('.art-lamps') && !!d.querySelector('.art-krab') && /src="\/krab-loop\.js"/.test(fs.readFileSync(`${WEB}/admin.html`, 'utf8')) && !/krab-desk\.js"/.test(fs.readFileSync(`${WEB}/admin.html`, 'utf8')) && ['', '-krab'].every((n) => fs.existsSync(`${WEB}/art/krab-controller${n}.webp`)) && ['controller', 'factory'].every((n) => fs.existsSync(`${WEB}/art/krab-${n}-loop.webm`)) && /controller: 'art\/krab-controller-loop\.webm'/.test(fs.readFileSync(`${WEB}/krab-loop.js`, 'utf8')));
  w.location.hash = '#clock'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  const af = d.getElementById('auto-form');
  check('automatic updates: the policy filled in', af.elements.hub_auto.value === '2' && af.elements.hub_window_start.value === '2' && af.elements.hub_check_every_hours.value === '24');
  check('  the window shown when installing', !d.getElementById('auto-window').hidden);
  check('  the last step and the wait said', /looked for an update/.test(t('#auto-state')[0]) && /installs between 02:00 and 05:00/.test(t('#auto-state')[0]), t('#auto-state'));
  check('books and apps: the scheduled choice offered', !!d.querySelector('#library-policy select[name="auto_install"]'));
  check('offline kit: the download offered, with its size', /Download irate-box-kit-abc1234-aarch64\.tar/.test(t('#kit-state')[0]) && d.querySelector('#kit-state a').getAttribute('href') === '/admin/kit/download', t('#kit-state'));
  check('  the books offered with their size', /2, 2\.8 GB|2, 3\.0 GB|2, 2794/.test(t('#kit-books')[0]) || /Include the books \(2,/.test(t('#kit-books')[0]), t('#kit-books'));
  const force = d.getElementById('update-force');
  check('Install anyway: offered for a version that failed verification', force && !force.disabled && t('#force-failed li').length === 1, t('#force-failed li'));
  check('Install as usual: not offered', d.getElementById('update-install').disabled);
  check('the updates doctor badged', d.querySelector('a[href="#updoctor"]').dataset.badge === '!');
  check('git: the Mirrors list shows what it keeps and its outcome', /keeps master · 2 releases · 2 prereleases · shallow · budget 2048 MB/.test(t('#git-mirrors')[0])
    && /5 fetched, 0 dropped/.test(t('#git-mirrors')[0]), t('#git-mirrors')[0]);
  check('git: a mirror says which revoked releases it leaves out', /revoked, left out: v2\.8\.0\.47db0e3/.test(t('#git-mirrors')[0]), t('#git-mirrors')[0]);
  check('git: a release list from the cache says so, and why', /release list from the cache of .*rate limit/.test(t('#git-mirrors')[0])
    && d.querySelector('#git-mirrors .setting-desc.warn'), t('#git-mirrors')[0]);
  w.confirm = () => true;
  [...d.querySelectorAll('#git-mirrors button')].find((b) => b.textContent === 'Keep revoked').click();
  [...d.querySelectorAll('#git-mirrors button')].find((b) => b.textContent === 'Update').click();
  // The repository cards are dom-git.cjs's (step 24); the mirrors' list is now Library → Mirrors.
  // A section headed with its page's title doesn't repeat it; a page of one section keeps it (F1).
  check('headings: Git\'s page says "Git" once; a page of one section keeps its heading',
    d.querySelector('#page-app-git #git > h2').classList.contains('same-as-page')
    && !d.querySelector('#page-clock #clock > h2').classList.contains('same-as-page'));
  // The sidebar's groups fold (F2; Tom, 2026-10-08: "Default open, option to toggle").
  {
    const heads = [...d.querySelectorAll('button.admin-side-group')];
    const head = (n) => heads.find((h) => h.dataset.group === n);
    const box = (n) => d.getElementById(head(n).getAttribute('aria-controls'));
    check('fold: every group head a button, open by default', heads.length >= 6 && heads.every((h) => h.getAttribute('aria-expanded') === 'true' && !box(h.dataset.group).hidden));
    w.AL.badge('secdoctor', '3');
    head('Doctors').click();
    check('fold: a head folds its group, kept in the browser', box('Doctors').hidden && head('Doctors').getAttribute('aria-expanded') === 'false'
      && JSON.parse(w.localStorage.getItem('irate-admin-folded')).includes('Doctors'));
    const words = [...box('Doctors').querySelectorAll('a[data-badge]')].map((a) => a.dataset.badge);
    const sum = words.filter((x) => /^\d+$/.test(x)).reduce((s, x) => s + Number(x), 0);
    check('fold: a folded group carries its links\' counts, summed, and their other words', sum >= 3
      && head('Doctors').dataset.badge === [sum, ...new Set(words.filter((x) => !/^\d+$/.test(x)))].join(' '), `${head('Doctors').dataset.badge} from ${words}`);
    w.location.hash = '#health'; w.AL.show();
    check('fold: opening a page in a folded group opens it', !box('Doctors').hidden && !head('Doctors').dataset.badge);
    const autoBtn = d.querySelector('.admin-side-list .side-auto');
    autoBtn.click();
    check('fold: Auto-collapse On keeps only the current page\'s group open', /On$/.test(autoBtn.textContent) && autoBtn.getAttribute('aria-pressed') === 'true'
      && heads.filter((h) => h.getAttribute('aria-expanded') === 'true').map((h) => h.dataset.group).join() === 'Doctors');
    w.location.hash = '#clock'; w.AL.show();
    check('fold: with Auto-collapse, another page\'s group opens and the last folds', !box('System').hidden && box('Doctors').hidden);
    autoBtn.click();
    check('fold: Auto-collapse Off opens what wasn\'t folded by hand', /Off$/.test(autoBtn.textContent) && !box('Doctors').hidden && !box('Apps').hidden);
    w.AL.badge('secdoctor', '');
    w.location.hash = ''; w.AL.show();
  }
  // F4: Overview's Needs attention, from the words beside the sidebar's entries.
  {
    const links = [...d.querySelectorAll('#attention .attention-list a')].map((a) => a.getAttribute('href'));
    check('needs attention: the box doctor\'s problems and the failed update check, each a link', links.includes('#health') && links.includes('#updoctor'), links.join(' '));
    w.AL.badge('moderation', '2');
    check('needs attention: a reported item appears at once, worded', /2 reported items waiting/.test(t('#attention')[0]));
    w.AL.badge('moderation', '');
    w.AL.badge('apps', 'working');
    check('needs attention: work in progress is not listed', !/Apps/.test(t('#attention li').join(' ')) && !t('#attention').join(' ').includes('#moderation'));
    w.AL.badge('apps', '');
  }
  // The words of the old menu are gone (F1): no Library pane, no Health group.
  check('no old pane names in the page\'s words', !/Library →|Library pane|under Health|under Add-ons/.test(d.body.textContent + js));
  check('mirrors: the list and its form on Git\'s own page, beside its repositories', d.querySelector('#page-app-git #mirrors #git-mirrors') && d.querySelector('#page-app-git #mirrors #git-mirror-add')
    && d.querySelector('#page-app-git #git'));
  // Firmware (step 23): no cache control on the Firmware page; it is under Git → Builds, with what is kept.
  check('firmware: the build cache is not on the Firmware page', !d.getElementById('fw-form').elements.cache);
  const cacheForm = d.getElementById('ci-cache-form');
  check('git: the build cache is under Builds, showing the setting and what is kept', d.querySelector('#git #ci-cache-form')
    && cacheForm.elements.cache.value === 'native' && /Kept: 2\.8\.1\.8e6a88d's, headless, 215/.test(t('#ci-cache-status')[0]), t('#ci-cache-status')[0]);
  check('firmware: says the source is not mirrored, with a button', /not mirrored/.test(t('#fw-source')[0])
    && [...d.querySelectorAll('#fw-source button')].some((b) => b.textContent === 'Mirror the source'), t('#fw-source')[0]);
  [...d.querySelectorAll('#fw-source button')].find((b) => b.textContent === 'Mirror the source').click();
  check('git: the build cache\'s three choices as radio buttons, not a dropdown (Tom)', cacheForm.querySelectorAll('input[type=radio][name=cache]').length === 3 && !cacheForm.querySelector('select'));
  cacheForm.elements.cache.value = 'whole';
  cacheForm.dispatchEvent(new w.Event('submit', { cancelable: true }));
  const mf = d.getElementById('git-mirror-add').elements;
  mf.upstream.value = 'https://github.com/meshtastic/firmware'; mf.name.value = 'fw2'; mf.releases.value = '2'; mf.prereleases.value = '2'; mf.branches.value = 'master develop';
  d.getElementById('git-mirror-add').dispatchEvent(new w.Event('submit', { cancelable: true }));
  force.click();
  const drop = [...d.querySelectorAll('#builtin-list .library-source')][0];
  {
    // The tile's own icon (Tom, 2026-10-08): emoji, or up to four letters and digits.
    const field = rowOf(d.getElementById('app-access-about'), 'Tile icon');
    const inp = field && field.querySelector('input');
    check('a tile icon field, the app\'s own as its placeholder', inp && inp.placeholder === 'ℹ️', inp && inp.placeholder);
    inp.value = 'TOOLONG'; inp.dispatchEvent(new w.Event('change'));
    const save = () => [...d.querySelectorAll('#app-access-about button')].find((b) => b.textContent === 'Save');
    check('  five letters refused before Save', save().disabled && /up to four letters/.test(t('#app-access-about')[0]));
    const inp2 = rowOf(d.getElementById('app-access-about'), 'Tile icon').querySelector('input');
    inp2.value = 'INFO'; inp2.dispatchEvent(new w.Event('change'));
    check('  four letters taken, Save offered', !save().disabled);
    // The tile's size in the same block (Tom, 2026-10-08: "tile icons can't choose their width and height now").
    const sizes = chipsOf(d.getElementById('app-access-about'), 'Tile size');
    check('  its size, single (the default) of single, wide, large', sizes.map((c) => c.textContent).join('|') === 'single|wide|large' && sizes[0].getAttribute('aria-pressed') === 'true');
    sizes[1].click();
    check('  wide chosen, waiting for Save', chipsOf(d.getElementById('app-access-about'), 'Tile size')[1].getAttribute('aria-pressed') === 'true');
    save().click();
  }
  chipsOf(drop, 'Who can open it').find((c) => c.textContent === 'admin').click();
  [...[...d.querySelectorAll('#builtin-list .library-source')][0].querySelectorAll('button')].find((b) => b.textContent === 'Save').click();
  setTimeout(() => {
    check('the icon and the size saved with the tiles\' state', posted.some((p) => p[0] === '/admin/tiles' && p[1].state.icon && p[1].state.icon.about === 'INFO'
      && p[1].state.size.about === 'wide'), JSON.stringify(posted.filter((p) => p[0] === '/admin/tiles')));
    check('About hidden asks the hub', posted.some((p) => p[0] === '/admin/visibility' && p[1].app === 'about' && p[1].visible === 'hidden'));
    check('the order saved: About, then the drop, large', JSON.stringify((posted.find((p) => p[0] === '/admin/tiles') || [])[1]) === JSON.stringify({ state: { order: ['about', 'drop'], size: { drop: 'large' }, icon: {} } }),
      JSON.stringify(posted.find((p) => p[0] === '/admin/tiles')));
    check('Install anyway asks the hub', posted.some((p) => p[0] === '/admin/update' && p[1].action === 'force-install'), JSON.stringify(posted));
    check('Private on the drop asks the hub', JSON.stringify(posted.find((p) => p[0] === '/admin/access')) === JSON.stringify(['/admin/access', { app: 'drop', mode: 'private' }]), JSON.stringify(posted));
    check('git: Update on a mirror asks the hub', posted.some((p) => p[0] === '/admin/git' && p[1].action === 'mirror-update' && p[1].name === 'firmware'));
    const keepRev = posted.find((p) => p[1].action === 'mirror-change');
    check('git: Keep revoked switches it off on the release groups only', keepRev && keepRev[1].mirror.groups.every((g) => g.skip_revoked === false)
      && keepRev[1].mirror.status === undefined, JSON.stringify(keepRev));
    const fwm = posted.find((p) => p[1].action === 'mirror-add' && p[1].mirror.name === 'meshtastic-firmware');
    check('firmware: Mirror the source adds meshtastic/firmware with its submodules', fwm && fwm[1].mirror.submodules === true
      && fwm[1].mirror.upstream === 'https://github.com/meshtastic/firmware' && fwm[1].mirror.groups.length === 2, JSON.stringify(fwm));
    const cs = posted.find((p) => p[0] === '/admin/firmware' && p[1].action === 'settings');
    check('git: saving the build cache sends only the cache', cs && JSON.stringify(cs[1]) === JSON.stringify({ action: 'settings', cache: 'whole' }), JSON.stringify(cs));
    const add = posted.find((p) => p[1].action === 'mirror-add' && p[1].mirror.name === 'fw2');
    check('git: adding a mirror sends its policy', add && add[1].mirror.branches.join() === 'master,develop'
      && JSON.stringify(add[1].mirror.groups) === JSON.stringify([{ kind: 'release', keep: 2 }, { kind: 'prerelease', keep: 2 }]), JSON.stringify(add));
    check('no page errors', !errors.length, errors);
    // A null handed to replaceChildren or append shows as the word itself (the access slots did,
    // 2026-10-06): no text node on the page may be just that.
    const walker = d.createTreeWalker(d.body, w.NodeFilter.SHOW_TEXT);
    const stray = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/^\s*(null|undefined)\s*$/.test(n.nodeValue)) stray.push(n.parentNode.className || n.parentNode.tagName);
    check('no stray "null" or "undefined" text on the page', stray.length === 0, stray.join(', '));
    [...d.querySelectorAll('#ci-cache-status button')].find((b) => b.textContent === 'Flush').click();
    setTimeout(() => {
      check('git: Flush asks the hub, and the cache is gone from the page', posted.some((p) => p[0] === '/admin/firmware' && p[1].action === 'flush-cache')
        && !/Kept:/.test(t('#ci-cache-status')[0]) && !d.querySelector('#ci-cache-status button'), t('#ci-cache-status')[0]);
      console.log('\nfailures:', fails);
      process.exit(fails ? 1 : 0);
    }, 100);
  }, 300);
}, 2500);
