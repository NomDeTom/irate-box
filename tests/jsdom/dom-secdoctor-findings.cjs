// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Security doctor draws its joint report in the shared shape (irate_box/finding.py) in jsdom: a report made by
// secdoctor.py itself (python3 runs it) from a kernel with its protections off, debsecan's view of two packages and
// two Security page lines that are a problem together. Said on the page in English, each part in its place: what was
// found, why, the notes, the evidence as data, the commands to copy, the place to go that the cross-reference table
// names, each source's line, the raw report and what the doctor cannot see. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-secdoctor-findings.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { execFileSync } = require('child_process');
const REPO = path.resolve(__dirname, '../..');
const WEB = `${REPO}/web`;
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const sys = fs.mkdtempSync(path.join(os.tmpdir(), 'secdoctor-findings-'));
for (const [k, v] of Object.entries({ 'fs/protected_symlinks': '0', 'fs/protected_hardlinks': '1', 'fs/protected_regular': '2',
  'fs/protected_fifos': '2', 'kernel/yama/ptrace_scope': '1', 'kernel/kptr_restrict': '1', 'kernel/dmesg_restrict': '1' })) {
  fs.mkdirSync(path.dirname(`${sys}/${k}`), { recursive: true });
  fs.writeFileSync(`${sys}/${k}`, `${v}\n`);
}
const py = `
import json, sys, time
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.finding import finding
from irate_box.msg import M
from irate_box.root import secdoctor as s
kernel = s.step_kernel({})
deb = s.debsecan_findings({"openssl": [("CVE-2026-1", {"fixed": True, "remote": True, "urgency": "high"})],
                           "perl-base": [("CVE-2026-2", {"fixed": False, "remote": False, "urgency": ""})]},
                          {}, {}, 1790000000, {"openssl": "openssl", "perl-base": "perl"})
page = [finding(i, M("t." + i, "A line"), "warn", M("t.s", "Something."), source="security-page") for i in ("page-sudo-nopasswd", "page-ssh-password")]
steps = [{"id": sid, "title": t, "findings": f} for (sid, t, *_), f in
         ((st, f) for st in s.STEPS for sid2, f in (("kernel", kernel), ("debsecan", deb), ("security-page", page)) if st[0] == sid2)]
flat = [f for st in steps for f in st["findings"]]
print(json.dumps({"at": time.time(), "root": True, "steps": steps, "joint": s.joint(steps, {"doctor": time.time()}),
                  "counts": {k: sum(1 for f in flat if f["status"] == k) for k in ("problem", "warn", "ok")},
                  "not_covered": s.NOT_COVERED, "new": [], "previous_at": None}))
`;
const audit = JSON.parse(execFileSync('python3', ['-c', py], { encoding: 'utf8', env: { ...process.env, HUB_SYSCTL_DIR: sys } }));
fs.rmSync(sys, { recursive: true, force: true });
const now = Math.floor(Date.now() / 1000);
const sec = { hub: [], accepted: {}, scan: { at: now, listeners: [], findings: [] }, log: [], pending: 0, results: [], imports: {}, audit };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#secdoctor', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
w.fetch = async (u) => (u === '/admin/security' ? new Response(JSON.stringify(sec), { status: 200 }) : new Response('{}', { status: 404 }));
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
setTimeout(() => {
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const row = (key) => d.getElementById(`find-${key.replace(/[^A-Za-z0-9_-]/g, '-')}`);

  const k = row('setting:kernel-links');
  check('the kernel item: the cross-reference title, what was found, then why, each a line of its own',
    k && t(k.querySelector('.ftitle')) === 'Kernel link protections (protected_symlinks, protected_hardlinks)'
    && t(k.querySelector('.fdetail')) === "The kernel's protections for links in shared folders are off."
    && /follow links that another account makes/.test(t(k.querySelector('.fwhy'))), k && t(k));
  check('  the setting as evidence, the command to copy exactly as the finding gives it',
    k && [...k.querySelectorAll('.fevidence li')].map(t).includes('fs.protected_symlinks=0')
    && [...k.querySelectorAll('.fdo-cmds code, .fdo-cmd code')].some((c) => /61-links\.conf && sudo sysctl --system$/.test(t(c))), k && t(k));

  const u = row('setting:security-updates');
  check('debsecan: one fix not installed, said as a whole sentence, its CVE as evidence, the tracker date as a note',
    u && t(u.querySelector('.fdetail')) === 'Debian has released a fix for 1 known vulnerability that is not installed.'
    && [...u.querySelectorAll('.fevidence li')].map(t).includes('CVE-2026-1 remote high')
    && [...u.querySelectorAll('.fnote')].some((n) => /^The tracker data is from /.test(t(n))), u && t(u));
  check('  the place to go, from the cross-reference table, as a link',
    u && [...u.querySelectorAll('a.go-btn')].some((a) => t(a) === "Go to Updates → Debian's security updates →"), u && t(u));

  const c = row('compound:sudo-and-passwords');
  check('a compound item: its own words, and each source line under it',
    c && t(c.querySelector('.fdetail')) === 'An account has a passwordless sudo rule, and SSH accepts passwords.'
    && c.querySelectorAll('.fsaid li').length === 2, c && t(c));

  const raw = t(d.getElementById('secdoctor'));
  check('the raw report and what the doctor cannot see: said, in words', /Kernel protections/.test(raw)
    && /Code review and tests cover them: app installs checked and unpacked by path, paths in the firmware cache/.test(raw), raw.slice(0, 300));
  check('no message drawn as an object, no placeholder left unfilled', !/\[object Object\]|\{[a-z_]+\}/.test(raw),
    (raw.match(/.{40}(\[object Object\]|\{[a-z_]+\}).{40}/) || [''])[0]);
  check('no errors', !errors.length, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
}, 150);
