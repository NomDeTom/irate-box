// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A page of web/ in jsdom against a fixture. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-health.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const fixture = fs.readFileSync(process.env.FIX || `${__dirname}/health-fixture.json`, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#health', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/health' && opts.method === 'POST') { posted.push(JSON.parse(opts.body)); return new Response('{"id":"x"}', { status: 202 }); }
  if (u === '/admin/health') return new Response(fixture, { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
setTimeout(() => {
  const d = w.document, t = (s) => [...d.querySelectorAll(s)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
  console.log('BANNER hidden:', d.getElementById('helper-banner').hidden, '|', t('#helper-banner')[0]);
  console.log('WHEN:', t('#health-when')[0]);
  console.log('FINDINGS:'); t('#health-findings .finding').forEach((x) => console.log('  ', x.slice(0, 170)));
  console.log('BUTTONS:', [...d.querySelectorAll('#health-findings button')].map((b) => `${b.textContent}${b.disabled ? ' (disabled)' : ''}`).join(', '));
  console.log('INSTALL:', t('#health-install')[0]);
  console.log('LOG lines:', d.getElementById('health-log').textContent.split('\n').length);
  console.log('BADGE:', (d.querySelector('a[href="#health"] .badge, a[href="#health"]') || {}).textContent);
  console.log('ERRORS:', errors.length ? errors : 'none');
  // One shape for every finding, as the security doctor's.
  let fails = 0;
  const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
  const rows = [...d.querySelectorAll('#health-findings .finding')];
  check('each finding: a status word, its title, what it is; no emoji marks', rows.length > 0 && rows.every((r) => r.querySelector('.fstate') && r.querySelector('.ftitle') && r.querySelector('.fdetail'))
    && !/[✅⚠️❌]/.test(d.getElementById('health-findings').textContent), rows.length);
  check('worst first', rows.map((r) => r.className.match(/finding-(\w+)/)[1]).join(' ') === rows.map((r) => r.className.match(/finding-(\w+)/)[1]).sort((a, b) => ({ problem: 0, warn: 1 })[a] - ({ problem: 0, warn: 1 })[b]).join(' '));
  const kiwix = d.getElementById('hl-kiwix-down');
  check('a fix with a command: its words, then the command with Copy', kiwix && /What to do: Rebuild the library, then start it/.test(kiwix.querySelector('.fhow').textContent)
    && kiwix.querySelector('.fdo-cmd code').textContent === 'systemctl reset-failed kiwix; systemctl restart kiwix', kiwix && kiwix.textContent);
  check('  and its repair buttons beside it', kiwix && kiwix.querySelectorAll('.fdo-controls button.action-btn').length >= 1);
  const counts = t('#health-summary .sec-count');
  check('the counts double as the filter: all, to fix, to look at', counts.length === 3 && /to fix/.test(counts[1]) && /to look at/.test(counts[2]), counts);
  const fine = d.querySelectorAll('#health-fine .finding').length;
  check('the fine ones folded apart, with no "what to do"', fine > 0 && !d.querySelector('#health-fine .fhow') && !d.getElementById('health-fine-fold').open, fine);
  d.querySelectorAll('#health-summary .sec-count')[1].click();
  check('"to fix" shows only those', [...d.querySelectorAll('#health-findings .finding')].every((r) => r.classList.contains('finding-problem')));
  d.querySelectorAll('#health-summary .sec-count')[0].click();
  const s = d.getElementById('health-search'); s.value = 'kiwix'; s.dispatchEvent(new w.Event('input'));
  check('the search narrows the list', [...d.querySelectorAll('#health-findings .finding')].every((r) => /kiwix/i.test(r.textContent)) && d.querySelectorAll('#health-findings .finding').length >= 1);
  check('no page errors', !errors.length, errors);
  process.exit(fails ? 1 : 0);
}, 3000);
