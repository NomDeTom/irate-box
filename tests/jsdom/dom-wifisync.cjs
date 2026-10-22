// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Network page's wifisync switch in jsdom, drawn from the finding as netinv.py writes it: always a way to turn
// it the other way, its state said as systemd has it (enabled, disabled, masked; running or stopped), and the
// undo naming the image's own state. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-wifisync.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', (error) => { if (!/scrollTo/.test(error.message)) errors.push(error.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#network', runScripts: 'outside-only', virtualConsole, pretendToBeVisual: true });
const w = dom.window;
w.fetch = async () => new Response('{}', { status: 404 });
w.eval(js);

let fails = 0;
function check(name, cond, info = '') {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : `  ${info}`));
  fails += !cond;
}
const message = (id, en) => ({ id, en, args: {} });
function inventory(status, extra) {
  return { findings: [{ id: 'wifisync', title: message('netinv.wifisync.title', 'mPWRD-OS wifisync'), status,
    summary: message('netinv.wifisync.x', 'wifisync.'), why: null, fix: null, notes: [], commands: [], evidence: [], actions: [], ...extra }] };
}
function draw(inv, record = {}) {
  w.renderWifisync(inv, { wifisync: record }, false);
  const box = w.document.getElementById('net-wifisync');
  return { text: box.textContent.replace(/\s+/g, ' ').trim(), buttons: [...box.querySelectorAll('button')].map((button) => button.textContent.trim()) };
}

setTimeout(() => {
  let shown = draw(inventory('warn', { unit_state: 'enabled', running: true }));
  check('enabled and running: Disable, and said so', shown.buttons[0] === 'Disable wifisync' && /is enabled, and running\./.test(shown.text), JSON.stringify(shown));
  shown = draw(inventory('warn', { unit_state: 'disabled', running: true }));
  check('disabled but running: still Disable', shown.buttons[0] === 'Disable wifisync' && /is disabled, and running\./.test(shown.text), JSON.stringify(shown));
  shown = draw(inventory('ok', { unit_state: 'masked', running: false }), { was: 'enabled', now: 'masked' });
  check('masked: Enable, and Undo to the image\'s enabled', shown.buttons.join('|') === 'Enable wifisync|Undo: enabled, as the image had it'
    && /is masked, and stopped\./.test(shown.text), JSON.stringify(shown));
  shown = draw(inventory('warn', { unit_state: 'static', running: false }), { was: 'masked' });
  check('another of systemd\'s words: said as its word; Undo to masked', shown.buttons.join('|') === 'Disable wifisync|Undo: masked, as the image had it'
    && /is set to "static" by systemd, and stopped\./.test(shown.text), JSON.stringify(shown));
  shown = draw(inventory('ok', {}));
  check('an older inventory without the two facts: disabled, Enable', shown.buttons[0] === 'Enable wifisync' && /is disabled\./.test(shown.text), JSON.stringify(shown));
  shown = draw({ findings: [] });
  check('not installed: nothing drawn', shown.text === '' && !shown.buttons.length, JSON.stringify(shown));
  check('no script errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
}, 1500);
