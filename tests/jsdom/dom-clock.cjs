// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Clock page's time sources in jsdom: the image's own service or chrony, the pool, a local server and GPS (with
// chrony only), in the owner's order, held until Save and sent as one choice; Discard; what each chosen source is
// doing, in its own row; Restore; and the hardware clock named for what it is.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-clock.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#clock', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
require("./answer-asks.cjs")(w); // its questions on the page answered by its confirm stub
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') { posted.push(JSON.parse(opts.body)); return new Response('{"id":"x"}', { status: 202 }); }
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const report = (clock) => ({ report: { at: Date.now() / 1000, findings: [], settings: null, clock }, helper: { stuck: false, waiting: 0, oldest: 0, commands: [] },
  pending: 0, results: [{ id: 'x', ok: true, message: 'Done.' }], log: [], install: null, queue: [], ladder: [], watchers: {} });
(async () => {
  await wait(100);
  const d = w.document, box = () => d.getElementById('clock-sources');
  const btn = (word) => [...box().querySelectorAll('button')].find((b) => t(b) === word);
  const chip = (word) => [...box().querySelectorAll('.chip')].find((b) => t(b).startsWith(word));
  // A source's On and Off chips, read and pressed as its switch: checked while On is selected.
  const tickOf = (word) => {
    const row = [...box().querySelectorAll('.aw-field')].find((r) => t(r.querySelector('.aw-label')).replace(/^\d+\. /, '').startsWith(word));
    const chipOf = (w) => [...row.querySelectorAll('.chip')].find((b) => t(b) === w);
    return {
      get checked() { return chipOf('On').classList.contains('selected'); },
      get disabled() { return chipOf('On').disabled; },
      click() { (this.checked ? chipOf('Off') : chipOf('On')).click(); },
    };
  };
  // As the image has it: timesyncd running, nothing chosen here.
  w.renderHealth(report({ service: null, pool: null, gps: null, local: '', changed: false, daemon: 'systemd-timesyncd', chrony_installed: false,
    gps_device: false, sources: [] }));
  check('as the image has it: its own service selected, the pool on, Save and Discard off, no Restore',
    chip("The image's own").classList.contains('selected') && tickOf("The internet's pool").checked && btn('Save').disabled && btn('Discard').disabled
    && !btn("Restore the image's settings"), t(box()));
  check('  GPS, the module and serving the time only with chrony', tickOf('GPS').disabled && tickOf('The hardware clock').disabled
    && tickOf('Serve the time').disabled && /Only with chrony/.test(t(box())) && /sets the time at boot only/.test(t(box())));
  chip('chrony').click(); await wait();
  check('chrony chosen: Save and Discard on, said as unsaved, GPS possible', !btn('Save').disabled && !btn('Discard').disabled
    && /Unsaved changes/.test(t(box())) && !tickOf('GPS').disabled && /installs chrony and removes systemd-timesyncd/.test(t(box())));
  tickOf('GPS').click(); await wait();
  tickOf('Serve the time').click(); await wait();
  check('  serving: says the firewall opens UDP 123 to guests', /opens UDP port 123 to guests/.test(t(box())));
  check('  no module set up: its switch off, saying so', tickOf('The hardware clock').disabled && /No external clock is set up/.test(t(box())));
  const addr = box().querySelector('input[type=text]');
  addr.value = '192.168.1.1'; addr.dispatchEvent(new w.Event('input'));
  btn('Save').click(); await wait();
  check('Save: one choice, every source in it, and the order', posted.some((b) => b.action === 'fix' && b.choice === 'clock-sources:chrony:1:1:0:1:192.168.1.1:gps,local,pool,module'), JSON.stringify(posted));
  // Nothing at all chosen: Save stays off.
  w.renderHealth(report({ service: 'image', pool: true, gps: false, local: '', changed: true, daemon: 'systemd-timesyncd', chrony_installed: false, gps_device: false, sources: [] }));
  await wait(); // the Save before it settles as the poll's answer comes
  tickOf("The internet's pool").click(); await wait();
  check('no source at all: Save off, said why', btn('Save').disabled && /Select at least one source/.test(t(box())));
  btn('Discard').click(); await wait();
  check('Discard: back to what is saved, Save off', tickOf("The internet's pool").checked && btn('Save').disabled);
  // Chosen here, with chrony: each source as it stands, and Restore.
  w.renderHealth(report({ service: 'chrony', pool: true, gps: true, module: true, module_ready: true, serve: true, local: '192.168.1.1', changed: true, daemon: 'chrony',
    chrony_installed: true, gps_device: true,
    hardware: { kind: 'module', chip: 'DS3231 (temperature-compensated)', bus: 1, addr: '0x68', by: 'found' }, order: ['gps', 'local', 'pool', 'module'],
    tracking: { ref: 'GPS', stratum: 1, synced: true },
    sources: [{ source: 'gps', name: 'GPS', state: 'in-use', mark: 'selected', ago: 12, offset: 0.0012 },
      { source: 'module', name: 'the hardware clock', state: 'answering', mark: 'spare', ago: 3, offset: -0.4 },
      { source: 'local', name: '192.168.1.1', state: 'not-answering', mark: 'silent', ago: null, offset: null },
      { source: 'pool', name: "the internet's pool", state: 'not-found', mark: null }] }));
  const said = [...box().querySelectorAll('.clock-source')].map((r) => `${t(r.querySelector('.aw-label'))}: ${t(r.querySelector('.fstate'))}`);
  check('what each chosen source is doing, in its own row and in the order', said.join(' | ')
    === "1. GPS: Sets the time | 2. A server on the local network: No answer | 3. The internet's pool: Never heard | 4. The hardware clock: Standing by", said.join(' | '));
  check('  the detail beside it: how far out, when last heard', /1\.2 ms out, heard 12 s ago/.test(t(box())), t(box()));
  check('  the hardware clock named for what it is', /The external DS3231 \(temperature-compensated\) on I2C bus 1, at 0x68/.test(t(d.getElementById('clock-hw')))
    && /set it up by itself/.test(t(d.getElementById('clock-hw'))), t(d.getElementById('clock-hw')));
  check('  saved: chrony selected, GPS, the hardware clock and serving on, the address filled', chip('chrony').classList.contains('selected') && tickOf('GPS').checked
    && tickOf('The hardware clock').checked && !tickOf('The hardware clock').disabled && tickOf('Serve the time').checked
    && box().querySelector('input[type=text]').value === '192.168.1.1');
  check('what the hotspot is served now: GPS, its stratum', /Served now: the time from GPS, as stratum 1\./.test(t(box())), t(box()));
  // The order: the pool moved up above the local server, and Save sends it.
  const pooled = [...box().querySelectorAll('.clock-source')].find((r) => /internet's pool/.test(t(r.querySelector('.aw-label'))));
  pooled.querySelector('button[aria-label="Move The internet\'s pool up"]').click(); await wait();
  const order = [...box().querySelectorAll('.clock-source .aw-label')].map(t);
  check('the pool moved up: second now, its number with it', order.join(' | ') === "1. GPS | 2. The internet's pool | 3. A server on the local network | 4. The hardware clock", order.join(' | '));
  check('  the first source cannot move up, the last not down', box().querySelector('.clock-source button[aria-label="Move GPS up"]').disabled
    && box().querySelector('.clock-source button[aria-label="Move The hardware clock down"]').disabled);
  btn('Save').click(); await wait();
  check('  Save sends the new order', posted.some((b) => b.choice === 'clock-sources:chrony:1:1:1:1:192.168.1.1:gps,pool,local,module'), JSON.stringify(posted.slice(-2)));
  // The inbuilt clock: named so, and not a source chrony can read.
  w.renderHealth(report({ service: 'chrony', pool: true, gps: false, module: false, module_ready: false, serve: false, local: '', changed: true, daemon: 'chrony',
    chrony_installed: true, gps_device: false, hardware: { kind: 'board', name: 'rtc-hym8563' }, order: ['gps', 'local', 'pool', 'module'], sources: [] }));
  await wait();
  check("the inbuilt clock: named so, its switch off with the reason", /The inbuilt clock \(rtc-hym8563\)/.test(t(d.getElementById('clock-hw')))
    && tickOf('The hardware clock').disabled && /chrony cannot read the inbuilt clock/.test(t(box())), t(box()));
  const served = (tracking) => {
    w.renderHealth(report({ service: 'chrony', pool: true, gps: false, module: true, module_ready: true, serve: true, local: '', changed: true, daemon: 'chrony',
      chrony_installed: true, gps_device: false, hardware: { kind: 'module', chip: 'DS3231', bus: 1, addr: '0x68' }, order: ['gps', 'local', 'pool', 'module'],
      tracking, sources: [] }));
    return t(box().querySelector('.clock-served'));
  };
  check('served from the hardware clock alone: named, its stratum 11, said to be weak',
    /from the external DS3231 on I2C bus 1, at 0x68 alone, as stratum 11\. That marks it as a weak source/.test(served({ ref: 'RTC', stratum: 11, synced: true })),
    served({ ref: 'RTC', stratum: 11, synced: true }));
  check('  not synchronised: nothing a device accepts', /nothing a device accepts/.test(served({ ref: '', stratum: 0, synced: false })));
  const n = posted.length;
  btn("Restore the image's settings").click(); await wait();
  check('Restore: asks, then sends clock-undo', posted.length === n + 1 && posted[n].choice === 'clock-undo', JSON.stringify(posted.slice(n)));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
