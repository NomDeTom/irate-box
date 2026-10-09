// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The box doctor's settings as chips: shown on a finding that is ok too, the current choice selected and
// inert, another choice sent. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-health-chips.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const fixture = JSON.parse(fs.readFileSync(`${__dirname}/health-fixture.json`, 'utf8'));
const chip = (group, choice, label, on, confirm) => ({ choice, label, group, on, ...(confirm ? { confirm } : {}) });
fixture.helper = { waiting: 0, oldest: 0, stuck: false, commands: [] };
fixture.results.unshift({ id: 'x', ok: true, message: 'doctor: done', at: 1790926200 });  // each request answered at once
fixture.report.findings.push({ id: 'crash-hang', check: 'A frozen box', status: 'ok', detail: 'Nothing restarts it.', fix: '', actions: [
  chip('Kernel panic or lockup', 'crashwatch-panic:off', 'Stay stopped', true),
  chip('Kernel panic or lockup', 'crashwatch-panic:on', 'Restart after 10 s', false, 'Restart the box?'),
  chip('Watchdog', 'crashwatch-watchdog:off', 'Off', true),
  chip('Watchdog', 'crashwatch-watchdog:on', "On (the board's, after a restart)", false, 'Let a watchdog restart the box?')] });
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#health', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [], asked = [];
const fixes = () => posted.filter((b) => b.action !== 'scan');  // the page asks for a scan by itself
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/health' && opts.method === 'POST') { posted.push(JSON.parse(opts.body)); return new Response('{"id":"x"}', { status: 202 }); }
  if (u === '/admin/health') return new Response(JSON.stringify(fixture), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = (q) => { asked.push(q); return true; };
w.eval(js);
setTimeout(() => {
  const d = w.document;
  const row = d.getElementById('hl-crash-hang');
  check('the finding is there', !!row);
  const groups = row ? [...row.querySelectorAll('.chip-group')] : [];
  check('an ok finding still shows its settings: two rows of chips', groups.length === 2, groups.length);
  const labels = row ? [...row.querySelectorAll('.aw-label')].map((n) => n.textContent) : [];
  check('  each row named', labels.join('|') === 'Kernel panic or lockup|Watchdog', labels);
  const sel = row ? [...row.querySelectorAll('.chip.selected')].map((b) => b.textContent) : [];
  check('  the current choices selected', sel.join('|') === 'Stay stopped|Off', sel);
  const btn = (text) => row && [...row.querySelectorAll('.chip')].find((b) => b.textContent === text);
  btn('Stay stopped') && btn('Stay stopped').click();
  check('  the selected chip does nothing', fixes().length === 0 && asked.length === 0, posted);
  const on = d.getElementById('hl-crash-hang') && [...d.getElementById('hl-crash-hang').querySelectorAll('.chip')].find((b) => b.textContent.startsWith('On ('));
  if (on) on.click();
  setTimeout(() => {
    check('  another chip asks first, then sends its choice', asked.length === 1 && fixes().length === 1
      && JSON.stringify(fixes()[0]).includes('crashwatch-watchdog:on'), { asked, posted });
    check('no script errors', errors.length === 0, errors);
    console.log(`failures: ${fails}`);
    process.exit(fails ? 1 : 0);
  }, 300);
}, 600);
