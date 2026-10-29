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
require("./answer-asks.cjs")(w); // its questions on the page answered by its confirm stub
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
// Check for updates stays open at every stage: the branch can move on after a download.
for (const [stage, state, labels, disabled] of [
  ['initial', null, ['Check for updates', 'Check for updates and download', 'Check for updates, download and install'], [false, false, false]],
  ['available', candidate, ['Check for updates', 'Download', 'Download and install'], [false, false, false]],
  ['downloaded', { ...candidate, verified: 'aaaaaaa', checks: [{ ok: true, name: 'Syntax', detail: 'Valid' }] }, ['Check for updates', 'Download', 'Install'], [false, true, false]],
  ['failed', { ...candidate, checks: [{ ok: false, name: 'Syntax', detail: 'Invalid' }] }, ['Check for updates', 'Download', 'Install'], [false, true, true]],
]) {
  w.renderUpdate({ ...updateData, state });
  const buttons = ['check', 'fetch', 'install'].map(name => d.getElementById(`update-${name}`));
  check(`${stage}: three stage-aware actions`, buttons.every((button, index) => button.textContent === labels[index] && button.disabled === disabled[index]));
}
// The administration pages are on a port of their own: what they show and copy names the hub's, which the box tells.
check('the hub address, before the box has said its port: the page host alone', w.eval('hubHost()') === 'box' && w.eval('hubOrigin()') === 'http://box');
w.eval('hubPorts = { plain: 8080, tls: 8443 }');
check('  with the hub on port 8080: the host and that port, not this page\'s', w.eval('hubHost()') === 'box:8080' && w.eval('hubOrigin()') === 'http://box:8080', w.eval('hubHost()'));
check('  an address written http:// takes the plain port, one written https:// the HTTPS port, whatever this page is on',
  w.eval('hubHost(false)') === 'box:8080' && w.eval('hubHost(true)') === 'box:8443', `${w.eval('hubHost(false)')} ${w.eval('hubHost(true)')}`);
w.eval('hubPorts = { plain: 80, tls: 443 }');
check('  on port 80: the host alone', w.eval('hubHost()') === 'box');
check('repairs belong to the doctor, not Updates', d.getElementById('update-clear').closest('.admin-pane').id === 'updoctor');
check('  Install anyway is only a finding\'s button, not a second control in the recovery actions', !d.getElementById('update-force'));
// Debian's buttons follow the same stages as the hub card's: nothing known, waiting, downloaded.
{
  const debian = (waiting, fetched) => {
    w.drawDebianPattern([{ id: 'security-updates', waiting, fetched }], false);
    return [...d.querySelectorAll('#debian-buttons [data-update]')].map((b) => `${b.textContent.trim()}${b.disabled ? ' (off)' : ''}`).join(' | ');
  };
  check('Debian, nothing known: each button checks first', debian(0, 0) === 'Check for updates | Check for updates and download | Check for updates, download and install', debian(0, 0));
  check('  updates waiting: download, or download and install; the check is off', debian(3, 0) === 'Check for updates (off) | Download | Download and install', debian(3, 0));
  check('  downloaded: only Install is left', debian(3, 3) === 'Check for updates (off) | Download (off) | Install', debian(3, 3));
}
// The branch warning's own button asks the update helper, not the box doctor's repairs.
const posted = [];
w.fetch = async (url, init) => { posted.push([url, init && init.body]); return new Response('{"id":"r1"}', { status: 202, headers: { 'Content-Type': 'application/json' } }); };
w.eval(`renderUpdoctor({ at: 1790950000, findings: [
  { id: 'target', title: 'Update branch', status: 'warn', summary: 'Updates follow the branch fix-topic.',
    actions: [{ choice: 'poll-target', label: 'Ask the repository for its default branch' }] }] })`);
[...d.querySelectorAll('#updoctor-summary button')][0].click(); // the filter is still on what is to fix
const ask = [...d.querySelectorAll('#doctor-findings button.action-btn')].filter((b) => /default branch/.test(b.textContent));
check('the branch warning has one button to ask the repository', ask.length === 1, t('#doctor-findings .finding'));
ask[0].click();
// The cache's and the last check's buttons: each asks first, and Install anyway's question lists the checks it skips.
w.eval(`renderUpdoctor({ at: 1790950000, findings: [
  { id: 'cache', title: 'Update cache', status: 'problem', summary: 'Stale cache lock.',
    actions: [{ choice: 'clear-cache', label: 'Clear the update cache', confirm: 'Delete the cached copy of the repository and its downloads?' }] },
  { id: 'last-check', title: 'Last update check', status: 'problem', summary: 'The downloaded version failed 2 checks.',
    actions: [{ choice: 'force-install', label: 'Install anyway',
      confirm: 'Install the downloaded version although it failed these 2 checks: Python files compile and Shell syntax? The installer still stops on its own errors.' }] }] })`);
const doctorButton = (label) => [...d.querySelectorAll('#doctor-findings button.action-btn')].filter((b) => b.textContent === label);
check('the cache finding has a Clear the update cache button, and the last check an Install anyway button',
  doctorButton('Clear the update cache').length === 1 && doctorButton('Install anyway').length === 1, t('#doctor-findings .finding'));
const asks = [];
const sentBefore = posted.length;
w.confirm = (text) => { asks.push(text); return true; };
doctorButton('Clear the update cache')[0].click();
doctorButton('Install anyway')[0].click();
check('  pressed: each acts at once, no question (an action that can be undone)', posted.length - sentBefore === 2 && asks.length === 0, [posted.length - sentBefore, asks]);
w.eval(`renderUpdoctor({ at: 1790950000, findings: [
  { id: 'last-check', title: 'Last update check', status: 'problem', summary: 'The downloaded version failed 1 check: Signed.' }] })`);
check('Install anyway is absent when the finding does not offer it (signing or installer syntax failed)', !doctorButton('Install anyway').length
  && !doctorButton('Clear the update cache').length, t('#doctor-findings .finding'));
const accepted = () => posted.slice(sentBefore).filter(([, b]) => b).map(([, b]) => JSON.parse(b).action);
// The source form: a section filled with the recorded source; asked first, changed only after a confirmation that
// names the repository.
w.drawSource({ source: { repo: 'https://github.com/NomDeTom/irate-box', branch: 'fix-tls-upgrade-includes' },
  branches: { repo: 'https://github.com/NomDeTom/irate-box', default: 'main', branches: ['main', 'fix-tls-upgrade-includes', 'dev'],
    branch: 'fix-tls-upgrade-includes', installed: 'b913cf4', standing: 'elsewhere' } });
const srcRepo = d.getElementById('update-source-repo'), srcBranch = d.getElementById('update-source-branch');
const srcSave = d.querySelector('#update-source-foot .aw-save');
check('the source form: filled with the recorded repository and branch, Save off', srcRepo.value === 'https://github.com/NomDeTom/irate-box'
  && srcBranch.value === 'fix-tls-upgrade-includes' && srcSave.disabled);
const srcChips = () => [...d.querySelectorAll('#update-source-choices .chip')];
check('  the repository\'s branches offered, the default first and named so', srcChips().map((c) => c.textContent).join('|') === 'main (default)|fix-tls-upgrade-includes|dev',
  srcChips().map((c) => c.textContent).join('|'));
check('  the followed branch left behind: said, and what to select', !d.getElementById('update-source-warn').hidden
  && /does not contain the installed version b913cf4; main does/.test(t('#update-source-warn')[0]) && /Select main and save/.test(t('#update-source-warn')[0]), t('#update-source-warn')[0]);
srcChips()[0].click();
check('  a branch chosen from them: the field follows, Save on', srcBranch.value === 'main' && !srcSave.disabled);
d.querySelector('#update-source-foot .aw-discard').click();
srcRepo.value = 'https://example.org/fork/irate-box';
srcRepo.dispatchEvent(new w.Event('input'));
srcBranch.value = 'dev';
srcBranch.dispatchEvent(new w.Event('input'));
check('  another repository typed: its branches are not offered (they are another repository\'s)', !srcChips().length || srcChips().every((c) => !c.classList.contains('selected')));
const srcWarn = t('#update-source-foot .aw-warn')[0] || '';
const before = posted.length;
srcSave.click();
d.getElementById('update-source-poll').click();
// A finding's link lands on the setting it names: the fold opened, the field marked.
w.eval(`renderUpdoctor({ at: 1790950000, findings: [
  { id: 'repo', title: 'Repository', status: 'problem', summary: 'Branch not found.', fix: 'Correct the branch.',
    go: { section: 'updoctor', where: 'Update source', mark: 'update-source-branch' } }] })`);
[...d.querySelectorAll('#updoctor-summary button')][0].click();
const land = [...d.querySelectorAll('#doctor-findings a.go-btn')].find((a) => /Update source/.test(a.textContent));
check('a finding links to Update source by name', !!land, t('#doctor-findings .finding'));
d.getElementById('update-maint').open = false;
d.getElementById('updoctor').hidden = false; // the page's router shows the pane before the link lands
check('  its button names the place', /^Go to Update source/.test(land.textContent.trim()), land.textContent);
land.click();
// The page lands a moment after the click (landOn waits 50 ms): wait for what it does, not for a fixed time.
const until = (done, then, left = 40) => (done() || left === 0 ? then() : setTimeout(() => until(done, then, left - 1), 50));
const branchLanded = () => d.getElementById('update-maint').open && d.getElementById('update-source-branch').classList.contains('landed');
until(branchLanded, () => {
  check('  it asks the update helper (poll-target), not the box doctor', posted.some(([u, b]) => /\/admin\/update$/.test(u) && /"action":"poll-target"/.test(b)), posted);
  check('  the link opens the fold and marks the Branch field', branchLanded(),
    [d.getElementById('update-maint').open, d.getElementById('update-source-branch').className]);
  check('the cache and Install anyway buttons, accepted, post their actions to the update helper', ['clear-cache', 'force-install'].every((a) => accepted().includes(a)), accepted());
  const sent = posted.slice(before).filter(([, b]) => b).map(([, b]) => JSON.parse(b));
  check('the source form: nothing sent before Save; Save sends the repository, the branch and the confirmation, with no question',
    posted.slice(0, before).every(([, b]) => !/set-source/.test(b)) && sent.some((b) => b.action === 'set-source' && b.repo === 'https://example.org/fork/irate-box' && b.branch === 'dev' && b.confirmed === true), sent);
  check('  its warning beside Save, while not saved: the box runs it as root', /as root/.test(srcWarn), srcWarn);
  check('  its poll asks about the typed repository', sent.some((b) => b.action === 'poll-source' && b.repo === 'https://example.org/fork/irate-box'), sent);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
});
