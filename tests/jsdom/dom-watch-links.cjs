// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Network → Network recovery, with a watchdog per link: a row for each link (watched, not watched, or as any
// other link) saying what it is doing, the choices for a new link, the steps that reach every link and a cable
// unplugged, Save sending them, and the status and log naming each watched link. No hub needed: its answer is stood
// in, the watchdog's levels taken from uplink.py itself. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-watch-links.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const WEB = path.resolve(__dirname, '../../web');
const REPO = path.resolve(__dirname, '../..');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const levels = JSON.parse(require('child_process').execFileSync('python3', ['-c',
  'import json, sys; sys.path.insert(0, sys.argv[1]); from irate_box.hub import uplink; print(json.dumps(uplink.levels()))', REPO]).toString());
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box.test/admin/#network', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const now = Math.floor(Date.now() / 1000);
const inventory = JSON.parse(fs.readFileSync(`${__dirname}/netinv-fixture.json`, 'utf8'));
inventory.at = now;
inventory.wired = [{ iface: 'eth0', carrier: true, operstate: 'up', driver: 'lan78xx' }];
const chosen = { ...levels.default, watch: {}, watch_new: 'uplink' };
// The Pi's morning: the cable holds the route and is watched; the WiFi is down, parked, and watched by nobody.
const uplink = { at: now, state: 'up', iface: 'eth0', primary: 'eth0', link: {}, gateway: '192.168.1.1', gateway_answers: true, backend: 'networkmanager',
  repairs: ['reconnect', 'restart', 'radio', 'reboot'], chosen, settings: { check: 120, window: 1800, sensitivity: 3 },
  links: { eth0: { watched: true, kind: 'ethernet', state: 'up' } },
  inventory: { eth0: { kind: 'ethernet', watched: true, up: true, autoconnect: ['netplan-eth0'] },
    wlan0: { kind: 'wifi', watched: false, up: false, autoconnect: ['netplan-wlan0-SKYTB1H1'] } },
  events: [{ at: now - 60, text: 'Watching eth0.', iface: 'eth0' }] };
let answer = { inventory, uplink, levels, results: [], pending: 0 };
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') { posted.push({ u, body: JSON.parse(opts.body) }); return new Response(JSON.stringify({ id: 'x' }), { status: 202 }); }
  if (String(u).startsWith('/admin/network')) return new Response(JSON.stringify(answer), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
const later = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await later(500);
  const d = w.document;
  const rows = [...d.querySelectorAll('#up-watch-rows .up-watch-row')];
  check('a row per link: the WiFi and the cable', rows.map((r) => r.querySelector('label').textContent).join('|') === 'wlan0 (WiFi)|eth0 (wired)',
    rows.map((r) => r.textContent).join(' / '));
  const wl = d.getElementById('up-watch-wlan0');
  check('  each a choice: as any other link (the default here), watched, not watched',
    wl && [...wl.options].map((o) => o.value).join(',') === ',on,off' && wl.value === '', wl && wl.value);
  check('  what each is doing now: the WiFi not watched and down, and what it would start by itself',
    /Not monitored, and down\. Autoconnect: netplan-wlan0-SKYTB1H1/.test(rows[0].textContent) && /Monitoring: up/.test(rows[1].textContent),
    rows.map((r) => r.textContent).join(' / '));
  const tiles = (id) => [...d.querySelectorAll(`#${id} input`)].map((i) => `${i.value}${i.checked ? '*' : ''}`).join(',');
  check('a link not set here: while it holds the route (chosen), looked at, every link, none', tiles('up-watchnew') === 'uplink*,look,all,none', tiles('up-watchnew'));
  check('steps that reach every link: only when nothing works (chosen), for the route\'s link, for any', tiles('up-boxsteps') === 'nothing-works*,uplink,any', tiles('up-boxsteps'));
  check('a cable unplugged: wait (chosen), reconnect now and then, the whole ladder', tiles('up-cable') === 'wait*,reconnect,ladder', tiles('up-cable'));
  check('how far on a cable: reconnect chosen by default, each reach offered', tiles('up-cablereach') === 'watch,reconnect*,restart,radio,reboot', tiles('up-cablereach'));
  check('the old single "Watch" field is gone', !d.getElementById('up-iface'));
  // Watch the WiFi, and let a cable be met with the ladder.
  wl.value = 'on';
  wl.dispatchEvent(new w.Event('change'));
  d.querySelector('#up-cable input[value="ladder"]').click();
  const save = d.querySelector('#up-foot .aw-save'), upNote = d.querySelector('#up-foot .aw-foot .note');
  check('  a change: Save offered', !save.disabled && /Unsaved changes/.test(upNote.textContent), upNote.textContent);
  save.click();
  await later(300);
  const sent = posted.find((p) => p.body.action === 'settings');
  check('Save sends the links watched and the new choices', sent && JSON.stringify(sent.body.settings.watch) === '{"wlan0":true}'
    && sent.body.settings.cable_out === 'ladder' && sent.body.settings.watch_new === 'uplink' && sent.body.settings.box_steps === 'nothing-works'
    && sent.body.settings.iface === 'auto' && sent.body.settings.cable_reach === 'reconnect', JSON.stringify(sent && sent.body.settings));
  // The watchdog now watches both: the status names the other, and the log each line's link.
  answer = { ...answer, results: [{ id: 'x', ok: true, message: 'Saved.' }], uplink: { ...uplink, at: now + 5, chosen: { ...chosen, watch: { wlan0: true }, cable_out: 'ladder' },
    links: { ...uplink.links, wlan0: { watched: true, kind: 'wifi', state: 'stalled', stall: { text: 'Stalled: nothing the box can do from here could help.' } } },
    inventory: { ...uplink.inventory, wlan0: { ...uplink.inventory.wlan0, as: 'wlan9' } },
    events: [...uplink.events, { at: now, text: 'Now watching wlan0 too.', iface: 'wlan0' }] } };
  w.loadNetwork();
  await later(500);
  const status = d.getElementById('up-status').textContent;
  check('the status: the route\'s link, then the others watched', /Up on eth0\..*Also monitoring: wlan0, stalled \(Stalled: nothing the box can do/.test(status), status);
  const log = [...d.querySelectorAll('#up-events li .ev-text')].map((n) => n.textContent);
  check('  the log: each line with its link, now that two are watched', log[0] === 'wlan0: Now watching wlan0 too.' && log[1] === 'eth0: Watching eth0.', log);
  check('  the WiFi\'s row: set to be watched', d.getElementById('up-watch-wlan0').value === 'on');
  const wrow = d.getElementById('up-watch-wlan0').closest('.up-watch-row').textContent;
  check('  a link known before under another name: said, and whose setting it follows', /Known before as wlan9 \(the same hardware\)/.test(wrow), wrow);
  check('no errors on the page', !errors.length, errors);
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
