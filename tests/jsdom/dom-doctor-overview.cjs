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
fixture.report.findings.push({ id: 'uplink-report', title: 'Uplink watchdog report', status: 'ok', summary: 'up on wlan0, steady pace. More.', fix: '', actions: [] });
fixture.report.findings.push({ id: 'led', title: 'Status light', status: 'ok', summary: 'work-led: alive and on its network; now 1 s on, 1 s off: well (driven by the box: this kernel has no LED triggers, so nothing else can blink it).', fix: '', actions: [],
  glance: 'work-led: alive and on its network.' });
fixture.report.findings.push({ id: 'crash-radio', title: 'The WiFi radio', status: 'ok', summary: 'Watching: wlan0 (AIC8800DC). Crash watch notes.', fix: '', actions: [] });
fixture.report.settings = { snapshots: true, panic: false, watchdog: false, watchdog_device: 'hardware', groups: [
  { key: 'snapshots', label: "Crash watch's snapshots", desc: 'Every 30 s.', current: 'on', options: [
    { value: 'on', label: 'On', choice: 'crashwatch-snapshots:on' }, { value: 'off', label: 'Off', choice: 'crashwatch-snapshots:off' }] },
  { key: 'panic', label: 'A kernel panic, oops or lockup', desc: 'Frozen.', current: 'off', options: [
    { value: 'off', label: 'Stay stopped', choice: 'crashwatch-panic:off' },
    { value: 'on', label: 'Restart after 10 s', choice: 'crashwatch-panic:on', confirm: 'The box restarts 10 seconds after a kernel panic.' }] }] };
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
require("./answer-asks.cjs")(w); // its questions on the page answered by its confirm stub
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
  check('at a glance: health, the link, the radio, the last start and why, the last repair, a frozen box, snapshots, the status light, writes to the card',
    Object.keys(g).join('|') === 'Health|Network recovery|The WiFi radio|Last boot|Last repair|Frozen-system recovery|Snapshot interval|Status light|SD card writes', Object.keys(g));
  check('  the status light: its finding\'s glance, not the whole detail', g['Status light'] === 'work-led: alive and on its network.', g['Status light']);
  check('  a finding with no glance: the first sentence of what it found', g['The WiFi radio'] === 'Watching: wlan0 (AIC8800DC).', g['The WiFi radio']);
  check('  the last start: when, and why in a few words (the reason, not its explanation)', g['Last boot'] === '2 h ago: The uplink watchdog rebooted it.', g['Last boot']);
  check('  a frozen box stays stopped; snapshots kept', g['Frozen-system recovery'].startsWith('Stays stopped') && g['Snapshot interval'] === 'Every 30 s.', g);
  const save = d.querySelector('#health-settings-foot .aw-save'), footNote = () => d.querySelector('#health-settings-foot .aw-foot .note').textContent;
  check('the settings: a row each, the current choice chosen, Save idle', d.querySelectorAll('#health-settings-groups .up-group').length === 2
    && d.querySelector('#hs-panic input[value="off"]').checked && save.disabled && save.textContent === 'Save', save.textContent);
  const on = d.querySelector('#hs-panic input[value="on"]');
  on.checked = true;
  on.dispatchEvent(new w.Event('change', { bubbles: true }));
  check('  a choice made: not saved yet, said beside Save', !save.disabled && save.textContent === 'Save' && footNote() === 'Unsaved changes.', footNote());
  check('  nothing sent before Save', posted.filter((b) => b.action === 'fix').length === 0);
  const discard = d.querySelector('#health-settings-foot .aw-discard');
  check('  Discard on with it', !discard.disabled);
  discard.click();
  check('  Discard: the choice as saved again, Save and Discard idle, nothing sent', d.querySelector('#hs-panic input[value="off"]').checked
    && save.disabled && discard.disabled && posted.filter((b) => b.action === 'fix').length === 0, save.textContent);
  const on2 = d.querySelector('#hs-panic input[value="on"]');
  on2.checked = true;
  on2.dispatchEvent(new w.Event('change', { bubbles: true }));
  const panicWarn = (d.querySelector('#health-settings-foot .aw-warn') || {}).textContent || '';
  save.click();
  await later(100);
  check('  its cost said beside Save while it is not saved; Save sends it, no question', /restarts 10 seconds after a kernel panic/.test(panicWarn)
    && posted.some((b) => b.choice === 'crashwatch-panic:on') && !asked.length, [panicWarn, posted]);
  fixture.queue = { running: { action: 'kit-fetch', detail: '', for: 60 }, waiting: [{ id: 'p1', action: 'health-fix', waited: 5 }], done: [] };
  await w.eval('loadHealth()');
  await later(100);
  const note = d.getElementById('health-settings-note');
  check('  while it waits: said, with what the root helper is busy with', /Waiting for the root helper \(busy with kit-fetch, 1 waiting\)/.test(note.textContent)
    && save.disabled && footNote() === 'Saving…', note.textContent);
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
