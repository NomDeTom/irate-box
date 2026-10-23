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
fs += s.cockpit_findings([{"proto": "tcp", "port": 9090, "addr": "0.0.0.0", "unit": "cockpit.socket", "pid": 1, "name": s.named("Cockpit")}], {})
fs += [s.security_updates_finding(["openssl", "libc6"], 3.2)]
fs[-1].update({"lists_age_days": 3.2, "waiting": 2, "fetched": 0, "packages": ["openssl", "libc6"]})  # as the scan adds them
fs += [finding("firewall", M("firewall.title", "Hotspot firewall"), "problem",
               M("firewall.not-loaded", "The hotspot firewall is enabled, but its rules are not loaded."),
               commands=["nft list table inet irate_box"])]
# Guest internet access shared, two of its four protections off (each a row of On and Off).
from irate_box.root import share, firewall
st = dict(share.load()); st["contain"] = {k: i % 2 == 0 for i, k in enumerate(share.CONTAIN)}
share.load = lambda: st; share.on = lambda _st: True; firewall.loaded = lambda: True
fs += s.share_findings()
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
const posted = [];
w.fetch = async (u, opts) => {
  if (u !== '/admin/security') return new Response('{}', { status: 404 });
  if (opts && opts.method === 'POST') {
    posted.push(JSON.parse(opts.body));
    return new Response(JSON.stringify({ id: `job-${posted.length}` }), { status: 200 });
  }
  return new Response(JSON.stringify(sec), { status: 200 });
};
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
  check('a choice card: what was found, then the notes; why it matters is the Security doctor\'s',
    pw && JSON.stringify(lines(pw).slice(0, 2)) === JSON.stringify(['SSH accepts passwords.', 'These accounts have a key for SSH on the box: lyra and pi.'])
    && !/guess a password/.test(t(pw)), pw && lines(pw));
  check('  the note, its list of accounts said whole', /These accounts have a key for SSH on the box: lyra and pi\./.test(t(pw)), t(pw));
  check('  a setting, not a card to act on: both values as options, the one in effect marked; no recommended action',
    [...pw.querySelectorAll('.chip')].map((b) => `${t(b)}${b.classList.contains('selected') ? '*' : ''}`).join('|') === 'A password or a key*|A key only'
    && !/Recommended action/.test(t(pw)), t(pw));

  const ck = card('cockpit');
  check('Cockpit: its evidence (port, address, unit) as data, one item each',
    ck && JSON.stringify([...ck.querySelectorAll('.fevidence li')].map(t)) === JSON.stringify(['TCP 9090', '0.0.0.0', 'cockpit.socket']), ck && t(ck));
  const row = d.querySelector('#security-listeners tbody tr');
  check('Cockpit: one setting with its three values, the one in effect marked',
    ck && [...ck.querySelectorAll('.chip')].map((b) => `${t(b)}${b.classList.contains('selected') ? '*' : ''}`).join('|') === 'On the network*|On loopback only|Off', ck && t(ck));
  check('the listening table: Cockpit\'s port links to its setting, no port and address said again (the columns say them)',
    row && !!row.querySelector('a[href="#sec-cockpit"]') && !/TCP 9090 on/.test(t(row)), row && t(row));

  const fw = card('firewall');
  check('a command: in a box to copy, exactly as typed, no full stop added',
    fw && [...fw.querySelectorAll('.fdo-cmd code')].map(t).join('|') === 'nft list table inet irate_box', fw && t(fw));

  // The Updates card draws the state, not the finding: the count, the lists' age, the packages in a fold.
  const upd = d.getElementById('updates-security');
  check('Updates: the plural said whole, the age of the lists, the packages in a fold',
    upd && /2 security updates are waiting\./.test(t(upd)) && /Checked 3 days ago\./.test(t(upd)) && !/null/.test(t(upd)), upd && t(upd));
  // Last on the card, as a package card's builds kept.
  const waitingFold = d.querySelector('#debian-packages details');
  check('  the packages waiting in a fold of their own, last on the card, one item each',
    waitingFold && t(waitingFold.querySelector('summary')) === '2 packages waiting'
    && [...waitingFold.querySelectorAll('li')].map(t).join('|') === 'openssl|libc6'
    && !!(d.getElementById('debian-auto-fold').compareDocumentPosition(waitingFold) & w.Node.DOCUMENT_POSITION_FOLLOWING),
    waitingFold && t(waitingFold));

  // Guest internet access: two rows turned back on at once, both shown as chosen, both sent at one Save.
  const guest = card('guest-net');
  const offRows = guest ? [...guest.querySelectorAll('.aw-field')].filter((f) => /Off\*/.test(
    [...f.querySelectorAll('.chip')].map((b) => `${t(b)}${b.classList.contains('selected') ? '*' : ''}`).join('|'))) : [];
  check('Guest internet access: a row of On and Off for each protection, two of them off', offRows.length === 2, guest && t(guest));
  const onChip = (f) => [...f.querySelectorAll('.chip')].find((b) => t(b) === 'On');
  offRows.forEach((f) => onChip(f).click());
  await wait();
  const rowsNow = [...card('guest-net').querySelectorAll('.aw-field')].filter((f) => f.querySelector('.chip.pending'));
  check('  both rows turned on stay chosen until Save (the second does not undo the first)',
    rowsNow.length === 2 && rowsNow.every((f) => t(f.querySelector('.chip.selected')) === 'On'), t(card('guest-net')));
  card('guest-net').querySelector('.aw-save').click();
  await wait(300);
  const sent = posted.filter((b) => b.action === 'fix').map((b) => b.choice);
  check('  Save sends each row\'s choice, one request each',
    sent.length === 2 && new Set(sent).size === 2 && sent.every((c) => /^share-contain-[a-z_]+-on$/.test(c)), JSON.stringify(posted));

  const all = d.body.textContent;
  check('no message drawn as an object, no placeholder left unfilled', !/\[object Object\]|\{[a-z_]+\}/.test(all),
    (all.match(/.{40}(\[object Object\]|\{[a-z_]+\}).{40}/) || [''])[0]);
  check('no errors', !errors.length, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
