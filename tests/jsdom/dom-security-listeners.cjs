// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Security page's Network listeners table in jsdom, from listeners laid out as the Lyra's (2026-10-21) and
// findings made by security.py itself (python3 runs it): one row per program, not per port and protocol; runs
// of ports as a range; a program's random outgoing UDP ports not listed; who each belongs to; the hotspot's
// state said truly (off now: its guests reach nothing). Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-security-listeners.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { execFileSync } = require('child_process');
const REPO = path.resolve(__dirname, '../..');
const WEB = `${REPO}/web`;
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const state = fs.mkdtempSync(path.join(os.tmpdir(), 'sec-listeners-'));
fs.mkdirSync(`${state}/control`);
fs.writeFileSync(`${state}/control/ap.json`, JSON.stringify({ up: false }));
const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.root import security as s
def L(proto, port, unit, process, owner, addr="0.0.0.0, *"):
    return {"proto": proto, "port": port, "addr": addr, "unit": unit, "process": process, "pid": 1,
            "name": s.named(process), "owner": owner}
hub = {"kind": "hub", "name": "irate-box"}
found = [L("tcp", p, "nginx.service", "nginx", hub) for p in [443, 8080] + list(range(8090, 8099))]
found += [L("udp", 21027, "syncthing@hub.service", "syncthing", hub), L("tcp", 22000, "syncthing@hub.service", "syncthing", hub),
          L("udp", 22000, "syncthing@hub.service", "syncthing", hub), L("udp", 33510, "syncthing@hub.service", "syncthing", hub),
          L("udp", 41882, "syncthing@hub.service", "syncthing", hub)]
found += [L("tcp", 111, "rpcbind.service", "rpcbind", {"kind": "package", "name": "rpcbind"}),
          L("udp", 111, "rpcbind.service", "rpcbind", {"kind": "package", "name": "rpcbind"})]
found += [L("tcp", 5000, None, "mystery", {"kind": "unknown", "name": "/opt/old/mystery"})]
# Avahi on three UDP ports, each one open or closed to the hotspot on its own (its findings as with the hotspot
# firewall on).
avahi = [L("udp", p, "avahi-daemon.service", "avahi-daemon", {"kind": "package", "name": "avahi-daemon"}) for p in (5353, 44242, 59491)]
print(json.dumps({"at": 1790000000, "listeners": found + avahi,
                  "findings": s.listener_findings(found, {}) + s.listener_findings(avahi, {"firewall": {"services": []}})}))
`;
const scan = JSON.parse(execFileSync('python3', ['-c', py], { encoding: 'utf8', env: { ...process.env, HUB_STATE_DIR: state } }));
scan.at = Math.floor(Date.now() / 1000); // fresh: the page does not ask for a scan of its own
const sec = { hub: [], accepted: {}, scan, log: [], pending: 0, results: [], deep: {}, imports: {}, audit: null };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#security', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
w.fetch = async (u) => (u === '/admin/security' ? new Response(JSON.stringify(sec), { status: 200 }) : new Response('{}', { status: 404 }));
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 80) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait();
  w.eval('loadSecurity')();
  await wait();
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const rows = [...d.querySelectorAll('#security-listeners tbody tr')];
  const row = (unit) => rows.find((r) => t(r.cells[0]).includes(unit));
  const cell = (unit, i) => t(row(unit) && row(unit).cells[i]);
  check('Network listeners: the ledger\'s heading, and the Belongs to column', t(d.getElementById('security-ports')) === 'Network listeners'
    && [...d.querySelectorAll('#security-listeners th')].map(t).join('|') === 'Service|Ports|Address|Belongs to|Expected exposure');
  check('one row per program, not per port and protocol', rows.length === 5, rows.map((r) => t(r.cells[0])).join(' / '));
  check('  runs of ports as a range', cell('nginx.service', 1) === 'TCP 443, 8080, 8090–8098', cell('nginx.service', 1));
  check('  a program\'s random outgoing UDP ports not listed', cell('syncthing@hub.service', 1) === 'TCP 22000; UDP 21027, 22000', cell('syncthing@hub.service', 1));
  check('  TCP and UDP of one program: one row', cell('rpcbind.service', 1) === 'TCP 111; UDP 111', cell('rpcbind.service', 1));
  check('belongs to: the hub\'s, a Debian package, or nobody known with what to do',
    cell('nginx.service', 3) === 'irate-box' && cell('rpcbind.service', 3) === 'Debian package rpcbind'
    && /^Not known\. No package owns \/opt\/old\/mystery\. If nothing needs it, stop it\.$/.test(cell('mystery', 3)), cell('mystery', 3));
  check('the hotspot off: said so, not "devices on the hotspot can reach it"',
    /The hotspot is off now\./.test(cell('rpcbind.service', 4)) && !/so devices on the hotspot can reach it too/.test(cell('rpcbind.service', 4)), cell('rpcbind.service', 4));
  const rp = row('rpcbind.service');
  check('  its buttons: Stop and disable once for the program', [...rp.querySelectorAll('button')].filter((b) => /^Stop and disable/.test(t(b))).length === 1,
    [...rp.querySelectorAll('button')].map(t).join(' | '));
  check('  one Save and Discard for the row, no port named with nothing after it',
    rp.querySelectorAll('.aw-foot').length === 1 && !/UDP 111:/.test(t(rp)), `${rp.querySelectorAll('.aw-foot').length} feet: ${t(rp)}`);
  check('  every row its state as a pill, the filter\'s words', [...d.querySelectorAll('#security-listeners tbody tr')]
    .every((tr) => tr.querySelector('[class$="-pill"]') && t(tr.querySelector('[class$="-pill"]')) === tr.dataset.tags));
  check('the doctor\'s links to a port land in its program\'s row', !!rp.querySelector('#sec-port-tcp-111') && !!rp.querySelector('#sec-port-udp-111'));
  check('  and no card of its own among the settings (rpcbind was a "To fix" card)', !d.querySelector('#security-findings [id^="sec-port-"]'));
  const av = row('avahi-daemon.service');
  check('Avahi: Stop and disable once, then each port a row of Open and Closed, Closed marked',
    av && [...av.querySelectorAll('button')].filter((b) => /^Stop and disable/.test(t(b))).length === 1
    && [...av.querySelectorAll('.aw-label')].map(t).join('|') === 'UDP 5353 on the hotspot|UDP 44242 on the hotspot|UDP 59491 on the hotspot'
    && [...av.querySelectorAll('.chip.selected')].every((b) => t(b) === 'Closed'), av && t(av));
  check('  one Save and Discard for the row, whatever ports it has', av.querySelectorAll('.aw-foot').length === 1, `${av.querySelectorAll('.aw-foot').length} feet`);
  check('  no "select Accept risk on the Security doctor" in the table (the doctor\'s)', !/Accept risk/.test(t(d.getElementById('security-listeners'))));
  check('no page errors', !errors.length, errors.join(' | '));
  fs.rmSync(state, { recursive: true, force: true });
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
