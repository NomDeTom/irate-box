// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The setup tour (menu overhaul M14; proposed 5h) on /admin, in jsdom: the setup step lists every
// decision as a link; each lives in a real section, with its part to highlight; the tour opens
// each one's page and highlights it, the clipboard krab beside; Keep and next records it on the hub;
// changing the setting where it lives decides it too; Leave takes the highlight away.
// Usage: [JSDOM=…/jsdom] node dom-tour.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const adminApps = require('./admin-apps-fixture.cjs');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const dom = new JSDOM(html, { url: 'http://box/admin/#welcome', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window, d = w.document;
let settings = { setup_done: false, setup_decided: [], names_to: 'users', visitor_counts: true };
const posted = [];
const security = { hub: [], pending: 0, scan: { at: 1, findings: [
  { id: 'ssh-password', check: 'SSH password login', title: 'SSH password login', status: 'warn', detail: 'Passwords are accepted.', actions: [{ choice: 'ssh-password-off', label: 'Turn password login off' }] },
  { id: 'listen-9090', check: 'Cockpit', title: 'Cockpit', status: 'problem', detail: 'A root-capable login.', actions: [{ choice: 'cockpit-off', label: 'Switch off' }] }] }, results: [] };
w.fetch = async (u, o = {}) => {
  const json = (x, s = 200) => new Response(JSON.stringify(x), { status: s });
  if (o.method === 'POST' && u === '/admin/settings') { const b = JSON.parse(o.body); posted.push(b); settings = { ...settings, ...b }; return json(settings); }
  if (u === '/admin/settings') return json(settings);
  if (u === '/admin/apps') return json({ apps: adminApps() });
  if (u === '/admin/security') return json(security);
  if (u === '/admin/access') return json({ apps: [{ id: 'term', title: 'Terminal', mode: 'private', login: true, kind: 'addon', unit: true, users: false, visible: 'auto' }], pages: {}, results: [] });
  return json({}, 404);
};
w.confirm = () => true;
w.sessionStorage.clear();
['admin-widgets.js', 'admin-layout.js', 'admin.js', 'admin-tour.js'].forEach((f) => w.eval(fs.readFileSync(`${WEB}/${f}`, 'utf8')));
const T = w.TOUR;
const shownPage = () => [...d.querySelectorAll('.admin-page')].find((p) => !p.hidden);
(async () => {
  await wait(400);
  // One list, every step in the same form (Tom, 2026-10-08): the setup's own steps, the decisions, a backup last.
  const rows = [...d.querySelectorAll('#setup-steps > li[data-step]')];
  check('one list: every step and decision, the backup last', rows.length === T.STEPS.length && T.STEPS.length === 17 && rows[rows.length - 1].dataset.step === 'backup', rows.length);
  check('  each the same form: its name, its pill, a line, Go there', rows.every((r) => r.querySelector('strong') && r.querySelector('[class$="-pill"]') && r.querySelector('.step-text') && r.querySelector('a[data-tour]')));
  const py = fs.readFileSync(require('path').resolve(__dirname, '../../irate_box/hub/server.py'), 'utf8');
  const ids = (py.match(/SETUP_DECISIONS = \(([\s\S]*?)\)/) || [])[1] || '';
  check('the hub knows the same steps', T.STEPS.every((x) => ids.includes(`"${x.id}"`)) && (ids.match(/"/g) || []).length / 2 === T.STEPS.length, ids);
  for (const dec of T.STEPS) {
    const sec = d.getElementById(dec.section) || d.getElementById(dec.fallback);
    check(`"${dec.label}": its section is on a page`, sec && !!sec.closest('.admin-page'), dec.section);
    if (typeof dec.target === 'string') check('  and its part is there', !!d.querySelector(dec.target), dec.target);
  }
  // The tour, from the start.
  click(d.querySelector('#welcome .tour-start'));
  await wait();
  let p = shownPage();
  check('starting: the first step not done opens, the bar counting all of them', !!d.querySelector('.tour-bar') && / of 17: /.test(d.querySelector('.tour-bar').textContent), d.querySelector('.tour-bar') && d.querySelector('.tour-bar').textContent.slice(0, 80));
  check('  the bar, with the clipboard krab', !!d.querySelector('.tour-bar img.tour-guide[src$="krab-controller-clipboard.webp"]'));
  T.go(T.STEPS.findIndex((x) => x.id === 'visitors'));
  await wait(400);
  p = shownPage();
  check('a decision\'s page opens (Security), its part highlighted', p && p.contains(d.getElementById('security')) && !!d.querySelector('.tour-target') && d.querySelector('.tour-target').contains(d.getElementById('visitor_counts')));
  click([...d.querySelectorAll('.tour-bar button')].find((b) => /Keep this/.test(b.textContent)));
  await wait();
  check('Keep and next: decided on the hub', posted.some((b) => (b.setup_decided || []).includes('visitors')), JSON.stringify(posted));
  p = shownPage();
  check('  and the next opens (Status tiles: the names)', p && p.contains(d.getElementById('status-tiles')) && !!d.querySelector('.tour-target #names-to-box, #names-to-box.tour-target'), p && p.getAttribute('aria-label'));
  // Changing it where it lives decides it too.
  d.querySelector('.tour-target').dispatchEvent(new w.Event('change', { bubbles: true }));
  await wait();
  check('changing the setting in place decides it', posted.some((b) => (b.setup_decided || []).includes('names')));
  T.go(T.STEPS.findIndex((x) => x.id === 'cockpit'));
  await wait(400);
  check('a finding drawn later is found: Cockpit highlighted', /Cockpit/.test((d.querySelector('.tour-target') || {}).textContent || ''));
  // The last step is the backup; past it, the tour ends back at the setup steps.
  T.go(T.STEPS.length - 1);
  await wait(400);
  check('the last step: Take a backup, its download highlighted', /Take a backup/.test(d.querySelector('.tour-bar').textContent) && !!d.querySelector('.tour-target[download]'));
  click([...d.querySelectorAll('.tour-bar button')].find((b) => /Skip/.test(b.textContent)));
  await wait();
  p = shownPage();
  check('past the last step: the bar is gone and the setup steps show', !d.querySelector('.tour-bar') && p && p.contains(d.getElementById('welcome')) && w.location.hash === '#welcome', p && p.getAttribute('aria-label'));
  T.go(T.STEPS.findIndex((x) => x.id === 'cockpit'));
  await wait(400);
  click([...d.querySelectorAll('.tour-bar button')].find((b) => /Leave/.test(b.textContent)));
  check('Leave the tour: no bar, no highlight', !d.querySelector('.tour-bar') && !d.querySelector('.tour-target'));
  check('the setup page says how many are left, of all 17', / of 17 still to do or decide/.test(d.getElementById('setup-left').textContent), d.getElementById('setup-left').textContent);
  click(d.querySelector('#backup a[download]'));
  await wait();
  check('downloading a backup is the backup step done', posted.some((b) => (b.setup_decided || []).includes('backup')) && /done/.test(d.querySelector('#setup-steps [data-step="backup"]').textContent));
  check('no page errors', !errors.length, errors.join('; '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
function click(el) { el.dispatchEvent(new w.MouseEvent('click', { bubbles: true })); }
