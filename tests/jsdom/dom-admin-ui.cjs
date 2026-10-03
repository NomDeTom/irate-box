// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin page's layout in jsdom, against fixtures: the sidebar's groups, the clock's own
// pane, the doctors under Health, and the public/private/off switch on add-ons and the hub's
// built-in parts. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-admin-ui.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
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
], results: [] };
const addons = { addons: [{ id: 'notes', title: 'Notes', summary: 'A notebook.', added: true, active: false },
  { id: 'term', title: 'Terminal', summary: 'A shell.', added: true, active: true }], progress: null, pending: 0, results: [], log: [] };
const update = { state: { up_to_date: false, available: 'a4aad09', available_date: '2026-10-02', branch: 'main', fetched: 1790950000,
  changes: ['one'], changes_known: true, verified: null,
  checks: [{ name: 'Python files compile', ok: true, warn: false, detail: '' },
    { name: 'The new nginx site passes nginx -t', ok: false, warn: false, detail: 'irate-box.nginx is missing from the update' }] },
  progress: null, pending: 0, results: [], log: [], doctor: null,
  auto: { hub_check_every_hours: 24, hub_auto: 2, hub_window_start: 2, hub_window_end: 5,
    state: { last: { step: 'check', ok: true, message: 'update available: 1 new commit', at: 1790950000 }, note: 'a4aad09 is ready; installs between 02:00 and 05:00' } } };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#clock', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') { posted.push([u, JSON.parse(opts.body)]); return new Response('{"id":"x"}', { status: 202 }); }
  if (u === '/admin/health') return new Response(JSON.stringify(health), { status: 200 });
  if (u === '/admin/access') return new Response(JSON.stringify(accessData), { status: 200 });
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
  const side = [...d.querySelectorAll('.admin-side-list > *')].map((n) => n.textContent.trim());
  const hi = side.indexOf('Health');
  check('sidebar: a Health group last, with the three doctors', hi > 0 && side.slice(hi + 1).join('|') === 'Services doctor|Security doctor|Updates doctor', side.join('|'));
  check('sidebar: Clock under Box', side.indexOf('Clock') > side.indexOf('Box') && side.indexOf('Clock') < side.indexOf('Library'));
  check('the Clock pane is the one shown', !d.getElementById('clock').hidden && d.getElementById('health').hidden);
  check('clock findings in the Clock pane', t('#clock-findings li').length === 2 && t('#clock-findings li')[0].includes('Clock'), t('#clock-findings li'));
  check('no clock findings in the services doctor', !t('#health-findings li').some((x) => x.startsWith('🔴 Clock') || /Clock module|^.{0,3}Clock —/.test(x)), t('#health-findings li'));
  check('the clock\'s badge counts its problem', d.querySelector('a[href="#clock"]').dataset.badge === '1');
  check('the services doctor is titled so', t('#health-title')[0] === 'Services doctor');
  check('the security doctor has its own pane', !!d.querySelector('#secdoctor #audit-run') && !d.querySelector('#security #audit-run'));
  check('the updates doctor has its own pane', !!d.querySelector('#updoctor #update-doctor') && !d.querySelector('#updates #update-doctor'));
  check('Access has no Terminal card setting', !d.getElementById('show_term_card') && !t('#access h3').some((x) => /Terminal/.test(x)));
  const built = t('#builtin-list .setting-name');
  check('built-in parts listed with a switch each', built.join('|') === 'File drop|Kiwix|Git' && d.querySelectorAll('#builtin-list .access-toggle').length === 3, built);
  const on = (id) => [...d.querySelectorAll(`#${id} [aria-checked="true"]`)].map((b) => b.textContent);
  const notes = [...d.querySelectorAll('#addons-list .library-source')].find((r) => r.textContent.includes('Notes'));
  check('the Notes add-on carries its switch, set to off', notes && notes.querySelector('.access-toggle [aria-checked="true"]').textContent.includes('Off'));
  const term = [...d.querySelectorAll('#addons-list .library-source')].find((r) => r.textContent.includes('Terminal'));
  check('Terminal public still says it asks for the login', term && term.textContent.includes('still asks for the admin login'));
  const main = d.querySelector('.admin-main');
  check('background art: the controller for Box (Clock)', main.dataset.art === 'controller', main.dataset.art);
  w.location.hash = '#secdoctor'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('background art: the doctor for Health', main.dataset.art === 'doctor', main.dataset.art);
  w.location.hash = '#books'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('background art: the librarian for Library', main.dataset.art === 'librarian', main.dataset.art);
  check('the desk has its lamp host and crab layer, lit by krab-desk.js', !!d.querySelector('.art-lamps') && !!d.querySelector('.art-krab') && /src="\/krab-desk\.js"/.test(fs.readFileSync(`${WEB}/admin.html`, 'utf8')) && ['', '-lit', '-mask', '-krab'].every((n) => fs.existsSync(`${WEB}/art/krab-controller${n}.webp`)) && fs.existsSync(`${WEB}/krab-desk.js`));
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
  force.click();
  const drop = [...d.querySelectorAll('#builtin-list .library-source')][0];
  [...drop.querySelectorAll('button')].find((b) => b.textContent.includes('Private')).click();
  setTimeout(() => {
    check('Install anyway asks the hub', posted.some((p) => p[0] === '/admin/update' && p[1].action === 'force-install'), JSON.stringify(posted));
    check('Private on the drop asks the hub', JSON.stringify(posted.find((p) => p[0] === '/admin/access')) === JSON.stringify(['/admin/access', { app: 'drop', mode: 'private' }]), JSON.stringify(posted));
    check('no page errors', !errors.length, errors);
    console.log('\nfailures:', fails);
    process.exit(fails ? 1 : 0);
  }, 300);
}, 2500);
