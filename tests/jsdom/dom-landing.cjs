// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A Box doctor's "Go to …" link lands on the very setting: the page holding its section shown, and the
// setting inside it marked as the setup tour marks one, even when it is drawn only after the link is
// followed. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-landing.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const fixture = JSON.parse(fs.readFileSync(`${__dirname}/health-fixture.json`, 'utf8'));
fixture.helper = { waiting: 0, oldest: 0, stuck: false, commands: [] };
fixture.report.findings.push({ id: 'uplink-skip-wlan0-reconnect', check: "wlan0: reconnecting isn't helping", status: 'warn',
  detail: 'Reconnecting mended 1 of the 9 outages it was tried on.', fix: 'Network → Connection settings → Recovery actions: clear Reconnect for wlan0.',
  actions: [], go: { section: 'up-steps', where: 'Network → Recovery actions', mark: 'up-step-wlan0-reconnect' } });
fixture.report.findings.push({ id: 'crash-radio', check: 'The WiFi radio', status: 'warn', detail: 'wlan0 failed.',
  fix: 'What is done when it fails: Network → Connection settings → Failed radio.', actions: [],
  go: { section: 'net-watchdog-card', where: 'Network → Failed radio', mark: 'up-wedge' } });
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|scrollIntoView/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#health', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/health' && !opts.method) return new Response(JSON.stringify(fixture), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.HTMLElement.prototype.scrollIntoView = function () { this.dataset.scrolled = '1'; };
w.eval(js);
const d = w.document;
const later = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await later(400);
  const link = (id) => d.getElementById(`hl-${id}`) && d.getElementById(`hl-${id}`).querySelector('a.go-btn');
  const a = link('uplink-skip-wlan0-reconnect');
  check('the finding links to the section, in words that name it', a && a.getAttribute('href') === '#up-steps' && a.textContent === 'Go to Network → Recovery actions →',
    a && [a.getAttribute('href'), a.textContent]);
  a.click();
  w.location.hash = '#up-steps';
  await later(250);
  const page = d.getElementById('up-steps').closest('.admin-page');
  check('  the Network page shown', page && !page.hidden);
  // The connection's ticks come with the page's data, after the link was followed.
  const label = d.createElement('label');
  label.id = 'up-step-wlan0-reconnect';
  d.getElementById('up-steps').append(label);
  await later(300);
  check('  the tick drawn later is waited for, marked as the tour marks a setting, and brought into view',
    label.classList.contains('landed') && label.dataset.scrolled === '1', label.className);
  check('  not the whole section', !d.getElementById('up-steps').classList.contains('landed'));
  label.dispatchEvent(new w.Event('change', { bubbles: true }));
  check('  changing it ends the mark', !label.classList.contains('landed'));
  w.location.hash = '#health';
  await later(300);
  const b = link('crash-radio');
  b.click();
  w.location.hash = '#net-watchdog-card';
  await later(300);
  check('a setting that is always there: marked at once', d.getElementById('up-wedge').classList.contains('landed'));
  w.location.hash = '#health';
  await later(200);
  w.location.hash = '#net-watchdog-card';
  await later(200);
  check('a plain link to the section (no setting named): the section itself marked', d.getElementById('net-watchdog-card').classList.contains('landed'));
  check('no script errors', errors.length === 0, errors);
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
