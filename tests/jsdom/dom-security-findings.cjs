// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Security page draws findings in the shared shape (irate_box/finding.py) in jsdom: findings made by security.py
// itself (python3 runs it), sent as messages ({id, en, args}) and said on the page in English, each part in its
// place: what was found, why it matters, the notes, the evidence as data, the recommended action, the commands to
// copy; plurals and lists said whole; no message drawn as an object. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-security-findings.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const { execFileSync } = require('child_process');
const REPO = path.resolve(__dirname, '../..');
const WEB = `${REPO}/web`;
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.root import security as s
from irate_box.finding import finding
from irate_box.msg import M
fs = s.ssh_findings({"permitrootlogin": "no", "passwordauthentication": "yes"}, ["lyra", "pi"], {})
fs += s.listener_findings([{"proto": "tcp", "port": 9090, "addr": "0.0.0.0", "unit": "cockpit.socket", "pid": 1, "name": s.named("Cockpit")}], {})
fs += [s.security_updates_finding(["openssl", "libc6"], 3.2)]
fs += [finding("firewall", M("firewall.title", "Hotspot firewall"), "problem",
               M("firewall.not-loaded", "The hotspot firewall is enabled, but its rules are not loaded."),
               commands=["nft list table inet irate_box"])]
print(json.dumps({"at": 1790000000, "listeners": [{"proto": "tcp", "port": 9090, "addr": "0.0.0.0", "unit": "cockpit.socket", "name": s.named("Cockpit")}],
                  "findings": fs}))
`;
const scan = JSON.parse(execFileSync('python3', ['-c', py], { encoding: 'utf8' }));
scan.at = Math.floor(Date.now() / 1000);   // fresh: the page does not ask for a scan of its own
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
  const card = (id) => d.getElementById(`sec-${id}`);

  const pw = card('ssh-password');
  const lines = (n) => [...n.querySelectorAll('.setting-desc')].map(t);
  check('a choice card: what was found, then why it matters, each a line of its own',
    pw && JSON.stringify(lines(pw).slice(0, 2)) === JSON.stringify(['SSH accepts passwords.', 'Any device on the network can try to guess a password.']), pw && lines(pw));
  check('  the note, its list of accounts said whole', /These accounts have a key for SSH on the box: lyra and pi\./.test(t(pw)), t(pw));
  check('  a setting, not a card to act on: both values as options, the one in effect marked; no recommended action',
    [...pw.querySelectorAll('.chip')].map((b) => `${t(b)}${b.classList.contains('selected') ? '*' : ''}`).join('|') === 'A password or a key*|A key only'
    && !/Recommended action/.test(t(pw)), t(pw));
  check('  the Security doctor named for what it makes of it', !!pw.querySelector('a[href="#secdoctor"]'));

  const ck = card('port-tcp-9090');
  check('Cockpit: its evidence (port, address, unit) as data, one item each',
    ck && JSON.stringify([...ck.querySelectorAll('.fevidence li')].map(t)) === JSON.stringify(['TCP 9090', '0.0.0.0', 'cockpit.socket']), ck && t(ck));
  const row = d.querySelector('#security-listeners tbody tr');
  check('the listening table: the finding\'s own words, no port and address said again (the columns say them)',
    row && /Cockpit, the image's Linux web console, listens on the network\. Its sign-in page gives root access/.test(t(row)) && !/TCP 9090 on/.test(t(row)), row && t(row));

  const fw = card('firewall');
  check('a command: in a box to copy, exactly as typed, no full stop added',
    fw && [...fw.querySelectorAll('.fdo-cmd code')].map(t).join('|') === 'nft list table inet irate_box', fw && t(fw));

  const upd = d.getElementById('sec-security-updates');
  check('Updates: the plural said whole, the packages as evidence, the notes after',
    upd && /2 security updates are waiting\./.test(t(upd)) && [...upd.querySelectorAll('.fevidence li')].map(t).join(' ') === 'openssl libc6'
    && /The package lists are 3 days old\. Newer fixes can be available\./.test(t(upd)), upd && t(upd));

  const all = d.body.textContent;
  check('no message drawn as an object, no placeholder left unfilled', !/\[object Object\]|\{[a-z_]+\}/.test(all),
    (all.match(/.{40}(\[object Object\]|\{[a-z_]+\}).{40}/) || [''])[0]);
  check('no errors', !errors.length, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
