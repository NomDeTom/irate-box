// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The update doctor in the other doctors' shape: the counters (a filter too), a row per finding with what to do
// (its command to copy), what was fine folded away. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-updoctor.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: 'http://box/admin/#updoctor', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.fetch = async () => new Response('{}', { status: 404 });
w.scrollTo = () => {};
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
w.eval(js);
const d = w.document, t = (s) => [...d.querySelectorAll(s)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
w.eval(`renderUpdoctor(null)`);
check('not run yet: said, no counters', /Not run yet/.test(d.getElementById('doctor-when').textContent) && !d.querySelector('#updoctor-summary button'));
w.eval(`renderUpdoctor({ at: 1790950000, findings: [
  { id: 'dns', title: 'Names (DNS)', status: 'problem', summary: 'github.com does not resolve.', fix: 'Restart the name service.',
    commands: ['sudo systemctl restart systemd-resolved'] },
  { id: 'space', title: 'Free space', status: 'warn', summary: 'Under 1 GB free.', fix: 'On Books, remove one.',
    go: { section: 'books', where: 'Books' } },
  { id: 'clock', title: 'The clock', status: 'ok', summary: 'Right to a second.' }] })`);
check('the counters: all, to fix, to review', t('#updoctor-summary button').join(' | ') === '2 all | 1 to fix | 1 to review', t('#updoctor-summary button'));
check('  a row per finding, the problem first, each with its status word', t('#doctor-findings .finding .ftitle').join(' | ') === 'Names (DNS) | Free space'
  && d.querySelector('#doctor-findings .finding').classList.contains('finding-problem'), t('#doctor-findings .ftitle'));
check('  its command in a box to copy', !!d.querySelector('#doctor-findings .finding') && /sudo systemctl restart systemd-resolved/.test(t('#doctor-findings .finding')[0])
  && /Copy/.test(t('#doctor-findings .finding')[0]), t('#doctor-findings .finding')[0]);
check('  what was fine, folded away', !d.getElementById('updoctor-fine-fold').hidden && t('#updoctor-fine .ftitle').join() === 'The clock');
[...d.querySelectorAll('#updoctor-summary button')][1].click();
check('a counter filters: only what is to fix', t('#doctor-findings .ftitle').join(' | ') === 'Names (DNS)', t('#doctor-findings .ftitle'));
const updateData = { signing: { level: 'off', keys: [] }, metered: {}, progress: null, pending: 0, log: [], results: [], doctor: null,
  auto: { hub_check_every_hours: 24, hub_auto: 0, hub_window_start: 2, hub_window_end: 5, state: {} } };
const candidate = { up_to_date: false, available: 'aaaaaaa', available_sha: 'a'.repeat(40), fetched: 1790950000, changes: [], checks: [] };
for (const [stage, state, labels, disabled] of [
  ['initial', null, ['Check for updates', 'Check for updates and download', 'Check for updates, download and install'], [false, false, false]],
  ['available', candidate, ['Check for updates', 'Download', 'Download and install'], [true, false, false]],
  ['downloaded', { ...candidate, verified: 'aaaaaaa', checks: [{ ok: true, name: 'Syntax', detail: 'Valid' }] }, ['Check for updates', 'Download', 'Install'], [true, true, false]],
  ['failed', { ...candidate, checks: [{ ok: false, name: 'Syntax', detail: 'Invalid' }] }, ['Check for updates', 'Download', 'Install'], [true, true, true]],
]) {
  w.renderUpdate({ ...updateData, state });
  const buttons = ['check', 'fetch', 'install'].map(name => d.getElementById(`update-${name}`));
  check(`${stage}: three stage-aware actions`, buttons.every((button, index) => button.textContent === labels[index] && button.disabled === disabled[index]));
}
check('repairs belong to the doctor, not Updates', ['update-clear', 'update-force'].every(id => d.getElementById(id).closest('.admin-pane').id === 'updoctor'));
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
