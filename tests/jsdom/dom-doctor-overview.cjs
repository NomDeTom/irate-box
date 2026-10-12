// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Box doctor's top: the box at a glance, and its own settings set then saved (the root helper's
// progress said, then what was done); the findings left alone by a poll that brings nothing new, so a
// selection in them survives. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-doctor-overview.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const fixture = JSON.parse(fs.readFileSync(`${__dirname}/health-fixture.json`, 'utf8'));
fixture.helper = { waiting: 0, oldest: 0, stuck: false, commands: [] };
fixture.report.at = Date.now() / 1000;   // fresh: the page doesn't ask for a look of its own
fixture.report.findings.push({ id: 'uplink-report', check: 'Uplink watchdog report', status: 'ok', detail: 'up on wlan0, steady pace. More.', fix: '', actions: [] });
fixture.report.findings.push({ id: 'crash-radio', check: 'The WiFi radio', status: 'ok', detail: 'Watching: wlan0 (AIC8800DC). Crash watch notes.', fix: '', actions: [] });
fixture.report.settings = { snapshots: true, panic: false, watchdog: false, watchdog_device: 'hardware', groups: [
  { key: 'snapshots', label: "Crash watch's snapshots", desc: 'Every 30 s.', current: 'on', options: [
    { value: 'on', label: 'On', choice: 'crashwatch-snapshots:on' }, { value: 'off', label: 'Off', choice: 'crashwatch-snapshots:off' }] },
  { key: 'panic', label: 'A kernel panic, oops or lockup', desc: 'Frozen.', current: 'off', options: [
    { value: 'off', label: 'Stay stopped', choice: 'crashwatch-panic:off' },
    { value: 'on', label: 'Restart after 10 s', choice: 'crashwatch-panic:on', confirm: 'Restart the box?' }] }] };
const now = Date.now() / 1000;
fixture.watchers = { last_boot: { at: now - 7200, kind: 'boot', text: 'Started: the uplink watchdog rebooted it: Reboot (4 min down).' },
  last_repair: { at: now - 7300, kind: 'repair', text: 'Reboot (4 min down): the radio is not back 3 min after its reset.' },
  link: { state: 'up', iface: 'wlan0', cause: null } };
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|scrollIntoView/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#health', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [], asked = [];
let n = 0;
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/health' && opts.method === 'POST') { const b = JSON.parse(opts.body); posted.push(b); return new Response(JSON.stringify({ id: `p${++n}` }), { status: 202 }); }
  if (u === '/admin/health') return new Response(JSON.stringify(fixture), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = (q) => { asked.push(q); return true; };
w.eval(js);
const d = w.document;
const later = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await later(400);
  const glance = [...d.querySelectorAll('#health-glance dt')].map((dt) => [dt.textContent, dt.nextElementSibling.textContent]);
  const g = Object.fromEntries(glance);
  check('at a glance: health, the link, the radio, the last start and why, the last repair, a frozen box, snapshots, writes to the card',
    Object.keys(g).join('|') === 'Health|Staying on the network|The WiFi radio|The last start|The last repair|A frozen box|Snapshots|Writes to the card', Object.keys(g));
  check('  the last start: when, and that the watchdog rebooted it', /^2 h ago: the uplink watchdog rebooted it/.test(g['The last start']), g['The last start']);
  check('  a frozen box stays stopped; snapshots kept', g['A frozen box'].startsWith('Stays stopped') && g.Snapshots === 'Kept every 30 s.', g);
  const save = d.getElementById('health-settings-save');
  check('the settings: a row each, the current choice chosen, Save idle', d.querySelectorAll('#health-settings-groups .up-group').length === 2
    && d.querySelector('#hs-panic input[value="off"]').checked && save.disabled && save.textContent === 'Save', save.textContent);
  const on = d.querySelector('#hs-panic input[value="on"]');
  on.checked = true;
  on.dispatchEvent(new w.Event('change', { bubbles: true }));
  check('  a choice made: not saved yet, said on the button', !save.disabled && save.textContent === 'Save (1 not saved yet)', save.textContent);
  check('  nothing sent before Save', posted.filter((b) => b.action === 'fix').length === 0);
  const discard = d.getElementById('health-settings-discard');
  check('  Discard on with it', !discard.disabled);
  discard.click();
  check('  Discard: the choice as saved again, Save and Discard idle, nothing sent', d.querySelector('#hs-panic input[value="off"]').checked
    && save.disabled && discard.disabled && posted.filter((b) => b.action === 'fix').length === 0, save.textContent);
  const on2 = d.querySelector('#hs-panic input[value="on"]');
  on2.checked = true;
  on2.dispatchEvent(new w.Event('change', { bubbles: true }));
  save.click();
  await later(100);
  check('  Save: asked first when the choice asks, then sent', asked.includes('Restart the box?') && posted.some((b) => b.choice === 'crashwatch-panic:on'), posted);
  fixture.queue = { running: { action: 'kit-fetch', detail: '', for: 60 }, waiting: [{ id: 'p1', action: 'health-fix', waited: 5 }], done: [] };
  await w.eval('loadHealth()');
  await later(100);
  const note = d.getElementById('health-settings-note');
  check('  while it waits: said, with what the root helper is busy with', /waiting for the root helper \(it is busy with kit-fetch, 1 waiting\)/.test(note.textContent)
    && save.disabled && save.textContent === 'Saving…', note.textContent);
  fixture.results.unshift({ id: 'p1', ok: true, message: 'the box restarts 10 s after a kernel panic, oops or lockup', at: now });
  fixture.queue = { waiting: [], done: [] };
  await w.eval('loadHealth()');
  await later(100);
  check('  done: what was done said, and a fresh look asked for', /^Saved: the box restarts 10 s after a kernel panic/.test(note.textContent)
    && posted.some((b) => b.action === 'scan'), [note.textContent, posted]);
  // A poll bringing the same report leaves the findings alone (a selection in them survives).
  const first = d.querySelector('#health-findings .finding, #health-fine .finding');
  await w.eval('loadHealth()');
  await later(100);
  const again = d.querySelector('#health-findings .finding, #health-fine .finding');
  check('the findings not redrawn by a poll that brings nothing new', first && first === again);
  check('no script errors', errors.length === 0, errors);
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
