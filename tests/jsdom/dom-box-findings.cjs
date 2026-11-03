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
from irate_box import msg
said = [msg.M("test.duration", "It took {took}.", took=msg.duration(s)) for s in (1, 45, 90, 300, 3725, 5400, 7200, 86400, 129600, 200000, 604800)]
said.append(msg.sentences(msg.M("test.one", "One."), None, msg.M("test.two", "Two {n}.", n=2)))
from irate_box import finding as F
from irate_box.hub import netinv
net = [netinv._h("locked:u1", msg.named("Home", netinv.NETWORK), "warn", msg.M("test.locked", "Home is locked to one access point."),
                 msg.M("test.why", "The box cannot roam."), msg.M("test.fix", "Unlock it."), iface="wlan0",
                 notes=[msg.M("test.note", "Seen {n} times.", n=3)], evidence=["bssid=aa:bb"], commands=["nmcli c modify Home 802-11-wireless.bssid ''"]),
       netinv._h("plain", msg.M("test.plain", "Plain"), "ok", msg.M("test.fine", "Fine."))]
print(json.dumps({"findings": fs, "said": [[m, msg.render(m)] for m in said],
                  "net": net, "net_english": [{"id": f["id"], **F.english(f)} for f in net]}))
`;
const made = JSON.parse(execFileSync('python3', ['-c', py], { encoding: 'utf8', env: { ...process.env, HUB_LOG_DIR: logdir } }));
const findings = made.findings;
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
setTimeout(async () => {
  const d = w.document;
  const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));
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

  // A length of time and several sentences as one result: the page says them as the terminal does (msg.render).
  const differ = made.said.filter(([m, en]) => w.AW.say(m) !== en).map(([m, en]) => `${w.AW.say(m)} ≠ ${en}`);
  check('durations and sentences said on the page as msg.py says them in English', !differ.length, differ.join(' | '));

  // The Network page reads the inventory's findings as the installer's summary reads them (finding.english).
  const cards = w.netHazards({ findings: made.net }).map((h) => ({ id: h.id, title: h.title, detail: h.detail, fix: h.fix }));
  check('the network inventory\'s findings, said on the Network page as finding.english says them', JSON.stringify(cards) === JSON.stringify(made.net_english),
    JSON.stringify(cards) + ' ≠ ' + JSON.stringify(made.net_english));
  check('  an inventory from before them: its hazards as they are', w.netHazards({ hazards: [{ id: 'x' }] })[0].id === 'x');

  const all = d.getElementById('health-findings').textContent + d.getElementById('health-fine').textContent;
  check('no message drawn as an object, no placeholder left unfilled', !/\[object Object\]|\{[a-z_]+\}/.test(all),
    (all.match(/.{40}(\[object Object\]|\{[a-z_]+\}).{40}/) || [''])[0]);
  // A finding's question outlives the poll that redraws the Box doctor, follows the button's data-wait key,
  // acts when answered, and is cancelled with a word when the finding is gone for good.
  const clock = { id: 'clock', title: 'The clock', status: 'warn', summary: 'No network time.', fix: '',
    actions: [{ choice: 'clock-set', label: 'Set the clock from this browser' }] };
  const calm = { ...data, pending: 0, progress: null, helper: { ...(data.helper || {}), waiting: 0, oldest: 0, stuck: false } };
  const withClock = (findings, at) => ({ ...calm, report: { ...data.report, at, findings } });
  const fixes = [];
  let shown = withClock([clock], 1790000001);
  const plainFetch = w.fetch;
  w.fetch = async (u, opts = {}) => {
    if (u === '/admin/health' && opts.method === 'POST') {
      fixes.push(opts.body);
      return new Response('{"id":"h1"}', { status: 202 });
    }
    if (u === '/admin/health') return new Response(JSON.stringify(shown), { status: 200 });
    return plainFetch(u, opts);
  };
  w.answerAsks = false;
  w.renderHealth(shown);
  await tick();
  const clockButton = () => [...d.querySelectorAll('#hl-clock button.action-btn')].find((b) => /Set the clock/.test(b.textContent));
  const box = () => d.querySelector('.aw-ask');
  const first = clockButton();
  const key = first && first.dataset.wait;
  w.eval(`hlFix('clock', ${JSON.stringify(clock.actions[0])}, document.querySelector('#hl-clock button.action-btn'))`);
  await tick();
  check('a Box doctor question is drawn beside the button that asked', !!box() && box().previousElementSibling
    && box().previousElementSibling.contains(first), box() && box().textContent);
  for (const n of [1, 2]) {
    shown = withClock([clock], 1790000001 + n);
    w.renderHealth(shown);
    await tick();
    const fresh = [...d.querySelectorAll('[data-wait]')].find((node) => node.dataset.wait === key);
    check(`  after poll ${n} it stays, beside the button drawn again`, !!box() && !!fresh && box().previousElementSibling
      && box().previousElementSibling.contains(fresh), row('clock') && t(row('clock')));
  }
  box().querySelector('button.primary').click();
  await tick();
  check('  answering it acts', fixes.some((body) => /"choice":"clock-set:/.test(body || '')), fixes);
  const goneBefore = fixes.length;
  shown = withClock([clock], 1790000010);
  w.renderHealth(shown);
  await tick();
  w.eval(`hlFix('clock', ${JSON.stringify(clock.actions[0])}, document.querySelector('#hl-clock button.action-btn'))`);
  await tick();
  check('a Box doctor question is open again', !!box());
  shown = withClock([], 1790000011);
  w.renderHealth(shown);
  await tick();
  const gone = d.querySelector('.aw-ask-gone');
  check('a finding gone while its question is open: the question cancelled, with a word',
    !box() && gone && /question was cancelled/.test(gone.textContent), gone && gone.textContent);
  await tick(50);
  check('  and nothing is sent for the answer it never got', fixes.length === goneBefore, fixes.slice(goneBefore));
  check('no errors', !errors.length, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
}, 150);
