// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The update doctor in the other doctors' shape: the counters (a filter too), a row per finding with what to do
// (its command to copy), what was fine folded away. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-updoctor.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: 'http://box/admin/#updoctor', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
w.fetch = async () => new Response('{}', { status: 404 });
w.scrollTo = () => {};
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
w.eval(js);
const d = w.document, t = (s) => [...d.querySelectorAll(s)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
w.eval(`renderUpdoctor(null)`);
check('not run yet: said, no counters', /Not run yet/.test(d.getElementById('doctor-when').textContent) && !d.querySelector('#updoctor-summary button'));
w.eval(`renderUpdoctor({ at: 1790950000, findings: [
  { check: 'Names (DNS)', status: 'problem', detail: 'github.com does not resolve.', fix: 'sudo systemctl restart systemd-resolved' },
  { check: 'Free space', status: 'warn', detail: 'Under 1 GB free.', fix: 'Library → Books: remove one.' },
  { check: 'The clock', status: 'ok', detail: 'Right to a second.', fix: '' }] })`);
check('the counters: all, to fix, to look at', t('#updoctor-summary button').join(' | ') === '2 all | 1 to fix | 1 to look at', t('#updoctor-summary button'));
check('  a row per finding, the problem first, each with its status word', t('#doctor-findings .finding .ftitle').join(' | ') === 'Names (DNS) | Free space'
  && d.querySelector('#doctor-findings .finding').classList.contains('finding-problem'), t('#doctor-findings .ftitle'));
check('  its command in a box to copy', !!d.querySelector('#doctor-findings .finding') && /sudo systemctl restart systemd-resolved/.test(t('#doctor-findings .finding')[0])
  && /Copy/.test(t('#doctor-findings .finding')[0]), t('#doctor-findings .finding')[0]);
check('  what was fine, folded away', !d.getElementById('updoctor-fine-fold').hidden && t('#updoctor-fine .ftitle').join() === 'The clock');
[...d.querySelectorAll('#updoctor-summary button')][1].click();
check('a counter filters: only what is to fix', t('#doctor-findings .ftitle').join(' | ') === 'Names (DNS)', t('#doctor-findings .ftitle'));
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
