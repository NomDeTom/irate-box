// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Updates doctor and the checks of a downloaded version, drawn in jsdom from what hub_control.py itself makes
// (python3 runs its doctor with the network, git and apt stood in): each finding's parts in their places (what was
// found, why, the notes, the evidence as data, the recommended action, the commands to copy, the place to go as a
// link), each check with what it saw, and the doctor's answer said from its message. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-update-findings.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { execFileSync } = require('child_process');
const REPO = path.resolve(__dirname, '../..');
const WEB = `${REPO}/web`;
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'update-findings-'));
for (const d of ['state/control/results', 'state/library', 'etc', 'code', 'cache', 'src/irate_box']) fs.mkdirSync(`${tmp}/${d}`, { recursive: true });
const py = `
import io, json, subprocess, sys, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.root import hub_control as H
T = Path(${JSON.stringify(tmp)})
(T / "etc" / "install-options").write_text("--repo\\nhttps://example.invalid/irate-box\\n--branch\\nmain\\n")
(T / "state" / "library" / "last-run.json").write_text(json.dumps({"last_scheduled": {"started": time.time() - 3600, "finished": time.time() - 3500,
    "ok": False, "error": "AttributeError: 'dict' object has no attribute 'startswith'", "where": "mirrors (librarian.py:812)"}}))
src = T / "src"
(src / "install.sh").write_text("#!/bin/bash\\necho ok\\n")
(src / "uninstall.sh").write_text("#!/bin/bash\\nif then fi (\\n")
(src / "irate_box" / "a.py").write_text("x = 1\\n")
checks = H.verify_update(src, None, [])
H.UPDATE_STATE.write_text(json.dumps({"checks": checks}))
apt = ("Err:5 https://dl.example.org/deb stable InRelease\\n  The following signatures were invalid: EXPKEYSIG 1234\\n"
       "E: The repository 'https://dl.example.org/deb stable InRelease' is not signed.\\n")
def run(*cmd, timeout=120, **kw):
    if cmd[0] == "apt-get":
        return subprocess.CompletedProcess(cmd, 100, apt, "")
    return subprocess.CompletedProcess(cmd, 0, "", "")
def no_route(*a, **k):
    raise OSError("timed out")
with mock.patch("socket.getaddrinfo", lambda h, p, proto=0: [(2, 1, 6, "", ("192.168.4.1", p))]), \\
     mock.patch("socket.create_connection", no_route), mock.patch.object(H, "run", run), \\
     mock.patch.object(H, "_local_addresses", lambda: {"192.168.4.1"}), \\
     mock.patch.object(H.kits, "verify", lambda: []), mock.patch.object(H.kits, "definitions", lambda: {}), \\
     mock.patch.object(H.kits, "installed_state", lambda: {"debug": {"at": time.time() - 9.5 * 86400, "remove_at": None}}), \\
     mock.patch.object(H.kits, "pool_bytes", lambda: 0):
    findings = H.updates_doctor_findings()
print(json.dumps({"findings": findings, "checks": checks}))
`;
const env = { ...process.env, HUB_STATE_DIR: `${tmp}/state`, HUB_ETC_DIR: `${tmp}/etc`, HUB_RUN_DIR: `${tmp}/run`, HUB_CODE_DIR: `${tmp}/code`,
  HUB_UPDATE_DIR: `${tmp}/cache/src`, HUB_DOWNLOAD_CACHE: `${tmp}/cache/downloads`, HUB_WEB_SERVER: 'nginx' };
const made = JSON.parse(execFileSync('python3', ['-c', py], { encoding: 'utf8', env }));
fs.rmSync(tmp, { recursive: true, force: true });
const now = Math.floor(Date.now() / 1000);
const data = { signing: { level: 'off', keys: [] }, metered: {}, progress: null, pending: 0, log: [], results: [],
  state: { up_to_date: false, available: 'a4aad09', available_date: '2026-10-02', branch: 'main', fetched: now, changes: ['one'], changes_known: true,
    verified: null, checks: made.checks },
  doctor: { at: now, findings: made.findings },
  auto: { hub_check_every_hours: 24, hub_auto: 1, hub_window_start: 2, hub_window_end: 5, state: {} } };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#updoctor', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
// The doctor's answer, once asked for: the result file as the root helper writes it (the English, and the message beside it).
let answered = [];
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/update' && opts.method === 'POST') {
    answered = [{ id: 'r1', ok: true, message: 'English kept for older readers',
      msg: { id: 'update.doctor.problems', en: { one: 'The update doctor found {n} problem: {first}.', other: 'The update doctor found {n} problems. The first is {first}.' },
        args: { n: 4, first: { id: 'update.dns.title', en: 'Name lookup (DNS)' } } } }];
    return new Response(JSON.stringify({ id: 'r1' }), { status: 202 });
  }
  return u === '/admin/update' ? new Response(JSON.stringify({ ...data, results: answered }), { status: 200 }) : new Response('{}', { status: 404 });
};
w.scrollTo = () => {};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
setTimeout(() => {
  const d = w.document;
  w.renderUpdate(data);
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const row = (id) => d.getElementById(`ud-${id}`);

  const dns = row('dns');
  check('the hotspot: its title, what was found, why, each a line of its own; the address as evidence; Network the place to go',
    dns && t(dns.querySelector('.ftitle')) === 'Name lookup (DNS)'
    && t(dns.querySelector('.fdetail')) === 'The DNS lookup of github.com failed. The name leads to this box itself.'
    && t(dns.querySelector('.fwhy')) === 'In hotspot mode, the box answers every name lookup itself.'
    && [...dns.querySelectorAll('.fevidence li')].map(t).join() === '192.168.4.1'
    && [...dns.querySelectorAll('a.go-btn')].some((a) => t(a) === 'Go to Network →'), dns && t(dns));

  const src = row('sources');
  check('apt failing: the source as evidence, the recommended action, then the command to copy, exactly, from the finding',
    src && [...src.querySelectorAll('.fevidence li')].map(t).some((e) => e === 'https://dl.example.org/deb')
    && /Recommended action: Find the list file of the failing source/.test(t(src.querySelector('.fhow')))
    && [...src.querySelectorAll('.fdo-cmd code')].map(t).join() === 'grep -rlF dl.example.org /etc/apt/sources.list /etc/apt/sources.list.d/'
    && [...src.querySelectorAll('.fnote')].map(t).join() === 'The installer repairs the Caddy package source itself.', src && t(src));

  const sch = row('scheduled');
  check('a scheduled run that crashed: when, the error and where as evidence, the log to copy, the librarian the place to go',
    sch && /^Scheduled run failed\. The run of \d+ \w+ at \d\d:\d\d stopped with an error\.$/.test(t(sch.querySelector('.fdetail')))
    && [...sch.querySelectorAll('.fevidence li')].map(t).some((e) => /AttributeError/.test(e))
    && [...sch.querySelectorAll('.fdo-cmd code')].map(t).join() === 'journalctl -u irate-box-librarian -n 80'
    && [...sch.querySelectorAll('a.go-btn')].some((a) => t(a) === 'Go to The librarian →'), sch && t(sch));

  const lc = row('last-check');
  check('the last download\'s failed check: counted, then said by its own name and sentence, Updates the place',
    lc && t(lc.querySelector('.fdetail')) === 'The downloaded version failed 3 checks.'
    && [...lc.querySelectorAll('.fnote')].map(t).includes('Shell syntax of uninstall.sh: The shell finds a syntax error in it.')
    && [...lc.querySelectorAll('.fnote')].map(t).includes('Security changes: The update has no security manifest, so its security changes cannot be checked.')
    && !lc.querySelector('a.go-btn'), lc && t(lc));

  const kit = row('kits-installed-debug');
  check('the debug toolkit installed for 9 days: a plural said in full, its why, what to select on Toolkits',
    kit && t(kit.querySelector('.fdetail')) === 'The debug toolkit has been installed for 9 days.'
    && /select Remove toolkit/.test(t(kit.querySelector('.fhow'))), kit && t(kit));

  const fine = t(d.getElementById('updoctor-fine'));
  check('what was fine folded away, said, with no place to go', /Updates come from the branch main of https:\/\/example\.invalid\/irate-box\./.test(fine)
    && !d.querySelector('#updoctor-fine a.go-btn'), fine);

  const items = [...d.querySelectorAll('#update-checks li')].map(t);
  check('the checks of the download: each with what it found, the failed one with the shell\'s line as evidence',
    items.some((i) => /^✅ Shell syntax of install\.sh — The shell finds no syntax error in it\.$/.test(i))
    && items.some((i) => /^❌ Shell syntax of uninstall\.sh — The shell finds a syntax error in it\..*line 2/.test(i))
    && items.some((i) => /Python files compile — 1 file compiles\..*does not prove that the update is safe/.test(i)), items.join(' | '));

  const all = ['doctor-findings', 'updoctor-fine', 'update-checks'].map((id) => d.getElementById(id).textContent).join(' ');
  // An update found and downloaded: Check for updates stays open (the branch can move on after the download);
  // Download is done, so it is not.
  check('an update downloaded: Check for updates still open, Download not', !d.getElementById('update-check').disabled
    && d.getElementById('update-fetch').disabled, `${d.getElementById('update-check').disabled} ${d.getElementById('update-fetch').disabled}`);
  check('no message drawn as an object, no placeholder left unfilled', !/\[object Object\]|\{[a-z_]+\}/.test(all),
    (all.match(/.{40}(\[object Object\]|\{[a-z_]+\}).{40}/) || [''])[0]);
  // The doctor's answer, said from its message (as the result file keeps it), under its button.
  d.getElementById('update-doctor').click();
  setTimeout(() => {
    check('the doctor\'s answer said from its message, under its button', t(d.getElementById('doctor-note')) === 'The update doctor found 4 problems. The first is Name lookup (DNS).',
      t(d.getElementById('doctor-note')));
    check('no errors', !errors.length, errors.join(' | '));
    console.log(fails ? `${fails} failure(s)` : 'ok');
    process.exit(fails ? 1 : 0);
  }, 300);
}, 150);
