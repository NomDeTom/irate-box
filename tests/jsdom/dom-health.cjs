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
  if (u === '/admin/health' && opts.method === 'POST') {
    const b = JSON.parse(opts.body); posted.push(b);
    // Accept answers with the list, as the hub does; the rest are root helper requests.
    if (b.action === 'accept' || b.action === 'unaccept') return new Response(JSON.stringify({ accepted: b.action === 'accept' ? { [b.key]: { at: 1, title: b.title } } : {} }), { status: 200 });
    return new Response('{"id":"x"}', { status: 202 });
  }
  if (u === '/admin/health') return new Response(fixture, { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
setTimeout(async () => {
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
  check('a fix with a command: its words, then the command with Copy', kiwix && /Recommended action: Rebuild the library, then start it/.test(kiwix.querySelector('.fhow').textContent)
    && kiwix.querySelector('.fdo-cmd code').textContent === 'systemctl reset-failed kiwix; systemctl restart kiwix', kiwix && kiwix.textContent);
  check('  and its repair buttons beside it', kiwix && kiwix.querySelectorAll('.fdo-controls button.action-btn').length >= 1);
  const counts = t('#health-summary .sec-count');
  check('the counts double as the filter: all, to fix, to review', counts.length === 3 && /to fix/.test(counts[1]) && /to review/.test(counts[2]), counts);
  const fine = d.querySelectorAll('#health-fine .finding').length;
  check('the fine ones folded apart, with no "what to do"', fine > 0 && !d.querySelector('#health-fine .fhow') && !d.getElementById('health-fine-fold').open, fine);
  d.querySelectorAll('#health-summary .sec-count')[1].click();
  check('"to fix" shows only those', [...d.querySelectorAll('#health-findings .finding')].every((r) => r.classList.contains('finding-problem')));
  d.querySelectorAll('#health-summary .sec-count')[0].click();
  const s = d.getElementById('health-search'); s.value = 'kiwix'; s.dispatchEvent(new w.Event('input'));
  check('the search narrows the list', [...d.querySelectorAll('#health-findings .finding')].every((r) => /kiwix/i.test(r.textContent)) && d.querySelectorAll('#health-findings .finding').length >= 1);
  // The findings first; the queue, the outages and the last install folded below them.
  const after = (a, b) => !!(d.getElementById(a).compareDocumentPosition(d.getElementById(b)) & w.Node.DOCUMENT_POSITION_PRECEDING);
  check('the findings before the helper\'s queue, the outage history and the last install',
    after('helper-queue', 'health-findings') && after('health-ladder-fold', 'health-findings') && after('health-install-fold', 'health-findings'));
  check('  each of those folded', ['helper-queue', 'health-ladder-fold', 'health-install-fold'].every((id) => d.getElementById(id).tagName === 'DETAILS'));
  check('  a clean install stays folded', !d.getElementById('health-install-fold').open);
  w.renderHealth({ ...JSON.parse(fixture), install: { ...JSON.parse(fixture).install, problems: ['nginx -t failed'] } });
  check('  one with problems opens by itself', d.getElementById('health-install-fold').open);
  // A review finding can be accepted: out of the review count and the open list, under Passed and accepted, and back.
  s.value = ''; s.dispatchEvent(new w.Event('input'));
  const fx = JSON.parse(fixture);
  fx.report.findings.push({ id: 'other-failed:armbian-led-state.service', title: "The image's LED service failed", status: 'warn',
    summary: 'armbian-led-state.service puts the LEDs back as they were.', fix: 'Switch it off.', actions: [{ choice: 'armbian-led:off', label: 'Switch it off' }] });
  w.renderHealth({ ...fx, accepted: {} });
  const reviewN = () => Number((t('#health-summary .sec-count')[2] || '').match(/\d+/)[0]);
  const led = () => d.getElementById('hl-other-failed:armbian-led-state.service');
  check('a review finding: its own fix (Switch it off) and Accept', led() && d.getElementById('health-findings').contains(led())
    && [...led().querySelectorAll('button')].some((b) => b.textContent === 'Switch it off') && [...led().querySelectorAll('button')].some((b) => b.textContent === 'Accept') && reviewN() === 1, reviewN());
  [...led().querySelectorAll('button')].find((b) => b.textContent === 'Accept').click();
  await new Promise((r) => setTimeout(r, 200));
  check('  Accept: sent with its id', posted.some((b) => b.action === 'accept' && b.key === 'other-failed:armbian-led-state.service'), JSON.stringify(posted.slice(-1)));
  check('  then out of the review count and the open list, under Passed and accepted as Accepted', reviewN() === 0 && d.getElementById('health-fine').contains(led())
    && /Accepted/.test(led().querySelector('.fstate').textContent) && /Passed and accepted/.test(d.querySelector('#health-fine-fold summary').textContent), reviewN());
  check('  the Summary\'s Health line counts it no more', /0 to review|Nothing to fix or review|1 to fix, 0 to review/.test(t('#health-glance dd')[0]), t('#health-glance dd')[0]);
  [...led().querySelectorAll('button')].find((b) => b.textContent === 'Reopen').click();
  await new Promise((r) => setTimeout(r, 200));
  check('  Reopen: in the review list again', posted.some((b) => b.action === 'unaccept') && d.getElementById('health-findings').contains(led()) && reviewN() === 1, reviewN());
  w.renderHealth({ ...fx, report: { ...fx.report, findings: fx.report.findings.map((f) => (f.id === 'other-failed:armbian-led-state.service' ? { ...f, status: 'problem' } : f)) },
    accepted: { 'other-failed:armbian-led-state.service': { at: 1 } } });
  check('  accepted, but now a problem: shown as one', d.getElementById('health-findings').contains(led()) && led().classList.contains('finding-problem'));
  check('no page errors', !errors.length, errors);
  process.exit(fails ? 1 : 0);
}, 3000);
