// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Clock page's time sources in jsdom: the image's own service or chrony, the pool, a local server and GPS (with
// chrony only), held until Save and sent as one choice; Discard; how each chosen source stands; Restore.
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
  const tickOf = (word) => [...box().querySelectorAll('label')].find((l) => t(l).startsWith(word)).querySelector('input');
  // As the image has it: timesyncd running, nothing chosen here.
  w.renderHealth(report({ service: null, pool: null, gps: null, local: '', changed: false, daemon: 'systemd-timesyncd', chrony_installed: false,
    gps_device: false, sources: [] }));
  check('as the image has it: its own service selected, the pool on, Save and Discard off, no Restore',
    chip("The image's own").classList.contains('selected') && tickOf("The internet's pool").checked && btn('Save').disabled && btn('Discard').disabled
    && !btn("Restore the image's settings"), t(box()));
  check('  GPS only with chrony', tickOf('GPS').disabled && /Only with chrony/.test(t(box())));
  chip('chrony').click(); await wait();
  check('chrony chosen: Save and Discard on, said as unsaved, GPS possible', !btn('Save').disabled && !btn('Discard').disabled
    && /Unsaved changes/.test(t(box())) && !tickOf('GPS').disabled && /installs chrony and removes systemd-timesyncd/.test(t(box())));
  tickOf('GPS').click(); await wait();
  const addr = box().querySelector('input[type=text]');
  addr.value = '192.168.1.1'; addr.dispatchEvent(new w.Event('input'));
  btn('Save').click(); await wait();
  check('Save: one choice, every source in it', posted.some((b) => b.action === 'fix' && b.choice === 'clock-sources:chrony:1:1:192.168.1.1'), JSON.stringify(posted));
  // Nothing at all chosen: Save stays off.
  w.renderHealth(report({ service: 'image', pool: true, gps: false, local: '', changed: true, daemon: 'systemd-timesyncd', chrony_installed: false, gps_device: false, sources: [] }));
  tickOf("The internet's pool").click(); await wait();
  check('no source at all: Save off, said why', btn('Save').disabled && /Select at least one source/.test(t(box())));
  btn('Discard').click(); await wait();
  check('Discard: back to what is saved, Save off', tickOf("The internet's pool").checked && btn('Save').disabled);
  // Chosen here, with chrony: each source as it stands, and Restore.
  w.renderHealth(report({ service: 'chrony', pool: true, gps: true, local: '192.168.1.1', changed: true, daemon: 'chrony', chrony_installed: true, gps_device: true,
    sources: [{ source: 'gps', name: 'GPS', state: 'in-use' }, { source: 'local', name: '192.168.1.1', state: 'not-answering' },
      { source: 'pool', name: "the internet's pool", state: 'not-found' }] }));
  const states = [...box().querySelectorAll('.clock-states li')].map(t);
  check('how each chosen source stands', states.join(' | ') === "In use GPS | Not answering 192.168.1.1 | Not found The internet's pool", states.join(' | '));
  check('  saved: chrony selected, GPS ticked, the address filled', chip('chrony').classList.contains('selected') && tickOf('GPS').checked
    && box().querySelector('input[type=text]').value === '192.168.1.1');
  const n = posted.length;
  btn("Restore the image's settings").click(); await wait();
  check('Restore: asks, then sends clock-undo', posted.length === n + 1 && posted[n].choice === 'clock-undo', JSON.stringify(posted.slice(n)));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
