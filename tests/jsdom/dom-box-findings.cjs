// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Box doctor draws findings in the shared shape (irate_box/finding.py) in jsdom: findings made by health.py itself
// (python3 runs it) on the fixture's report, said on the page in English, each part in its place: what was found, why,
// the notes, the evidence as data, the recommended action, the commands to copy (taken from the finding, not found in
// its words), and the place to go as a link the finding names. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-box-findings.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { execFileSync } = require('child_process');
const REPO = path.resolve(__dirname, '../..');
const WEB = `${REPO}/web`;
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const logdir = fs.mkdtempSync(path.join(os.tmpdir(), 'box-findings-'));
fs.writeFileSync(`${logdir}/install-state.json`, JSON.stringify({ started: 1790000000, aborted: true, step: 'Installing Kiwix',
  failed_command: 'apt-get install -y kiwix-tools', failed_line: 412, exit: 100 }));
const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.root import health as h
fs = h.check_install()
fs += h.link_findings({"inventory": {"wlan0": {"kind": "wifi", "up": False, "autoconnect": ["Home"]}}, "links": {}, "chosen": {},
                       "watching": "eth0"})
fs += h.check_led({"found": {"ACT": {"triggers": True}}, "settings": {}, "led": "ACT", "show": "leave"})
print(json.dumps(fs))
`;
const findings = JSON.parse(execFileSync('python3', ['-c', py], { encoding: 'utf8', env: { ...process.env, HUB_LOG_DIR: logdir } }));
fs.rmSync(logdir, { recursive: true, force: true });
const data = JSON.parse(fs.readFileSync(`${__dirname}/health-fixture.json`, 'utf8'));
data.report = { at: Math.floor(Date.now() / 1000), findings, settings: null };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#health', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
w.fetch = async (u, opts = {}) => (u === '/admin/health' && opts.method !== 'POST' ? new Response(JSON.stringify(data), { status: 200 })
  : new Response('{}', { status: 404 }));
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
setTimeout(() => {
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const row = (id) => d.getElementById(`hl-${id}`);

  const ins = row('install');
  check('the install that stopped: its title, what happened, then why, each a line of its own',
    ins && t(ins.querySelector('.ftitle')) === 'Last install'
    && /^The installer started on .* and stopped during this step: Installing Kiwix\. It did not do the steps after it\.$/.test(t(ins.querySelector('.fdetail')))
    && t(ins.querySelector('.fwhy')) === 'A command failed, with exit code 100.', ins && t(ins));
  check('  the failed command, its line and the log as evidence, data as it is',
    ins && ((e) => e.length === 3 && e[0] === 'apt-get install -y kiwix-tools' && e[1] === 'install.sh:412' && e[2].endsWith('/install.log'))(
      [...ins.querySelectorAll('.fevidence li')].map(t)), ins && [...ins.querySelectorAll('.fevidence li')].map(t));
  check('  the recommended action, then the command to copy, exactly, from the finding',
    ins && /Recommended action: Read the installer's log/.test(t(ins.querySelector('.fhow')))
    && [...ins.querySelectorAll('.fdo-cmd code')].map(t).length === 1 && /^sudo tail -n 50 .*install\.log$/.test(t(ins.querySelector('.fdo-cmd code'))), ins && t(ins));
  check('  its repair as a button', ins && [...ins.querySelectorAll('button')].some((b) => t(b) === 'Run the installer again'));

  const lk = row('uplink-uncovered-wlan0');
  check('a link down and not watched: said with its name, a reconnect named for it, and the place to watch it as a link',
    lk && /^wlan0 is down\. NetworkManager has a connection for it/.test(t(lk.querySelector('.fdetail')))
    && [...lk.querySelectorAll('button')].some((b) => t(b) === 'Reconnect wlan0')
    && [...lk.querySelectorAll('a.go-btn')].some((a) => t(a) === 'Go to Network → Network recovery →'), lk && t(lk));

  const all = d.getElementById('health-findings').textContent + d.getElementById('health-fine').textContent;
  check('no message drawn as an object, no placeholder left unfilled', !/\[object Object\]|\{[a-z_]+\}/.test(all),
    (all.match(/.{40}(\[object Object\]|\{[a-z_]+\}).{40}/) || [''])[0]);
  check('no errors', !errors.length, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
}, 150);
