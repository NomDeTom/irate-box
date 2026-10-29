// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Housecall doctor on /admin: nothing asked for until Examine is selected; the target and path go to the root
// helper; its report's findings are drawn (a damaged file named); the page says when it was examined.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-housecall.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|scrollIntoView/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#housecall', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const calls = [], posted = [];
const report = {
  at: Date.now() / 1000,
  target: '/tmp/card.img',
  findings: [
    { id: 'housecall-files:whole disk', title: { id: 't', en: 'Files on whole disk' }, status: 'problem',
      summary: { id: 's', en: '1 file is damaged.' }, why: { id: 'w', en: 'It was not kept.' },
      notes: [], evidence: ['/etc/key.pem: damaged (It reads back whole as 0xFF.)'], commands: [], actions: [] },
  ],
  counts: { files: 3, damaged: 1, suspect: 0, genuine: 0, unknown: 2 },
};
let done = false;
let running = false;
const runningReport = () => ({ at: Date.now() / 1000, running: true, target: '/tmp/card.img', findings: [], counts: {} });
w.fetch = async (u, opts = {}) => {
  calls.push([u, opts.method || 'GET']);
  if (u === '/admin/housecall' && opts.method === 'POST') {
    posted.push(JSON.parse(opts.body));
    done = true;
    return new Response(JSON.stringify({ id: 'hc1' }), { status: 202 });
  }
  if (u === '/admin/housecall') {
    return new Response(JSON.stringify({ report: running ? runningReport() : done ? report : null }), { status: 200 });
  }
  return new Response('{}', { status: 404 });
};
w.eval(js);
const d = w.document;
const later = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await later(300);
  check(
    'nothing is examined until Examine is selected',
    !calls.some(([u]) => u === '/admin/housecall'),
    calls.filter(([u]) => u === '/admin/housecall'),
  );
  const target = d.getElementById('housecall-target');
  const path = d.getElementById('housecall-path');
  target.value = 'image';
  target.dispatchEvent(new w.Event('change', { bubbles: true }));
  path.value = '/tmp/card.img';
  d.getElementById('housecall-scan').click();
  await later(1800);
  check(
    'Examine: the target and path go to the root helper',
    posted.length === 1 && posted[0].action === 'scan' && posted[0].target === 'image' && posted[0].path === '/tmp/card.img',
    posted,
  );
  const findings = [...d.querySelectorAll('#housecall-findings .finding')];
  check(
    'its report is drawn: the damaged file and what it found',
    findings.length === 1
      && findings[0].className.includes('finding-problem')
      && /1 file is damaged/.test(findings[0].textContent)
      && /\/etc\/key\.pem: damaged/.test(findings[0].textContent),
    findings.map((f) => f.textContent),
  );
  check('  and when it was examined is said', /\d/.test(d.getElementById('housecall-when').textContent), d.getElementById('housecall-when').textContent);
  check('this box\'s own files: the path is not asked for, and the package and tree boxes are offered', (() => {
    target.value = 'this-card';
    target.dispatchEvent(new w.Event('change', { bubbles: true }));
    return path.disabled && !d.getElementById('housecall-dpkg').disabled && !d.getElementById('housecall-git').disabled;
  })());
  check('  finished: no Stop offered, and Examine can be selected again', d.getElementById('housecall-stop').hidden && !d.getElementById('housecall-scan').disabled);
  // A card in a reader can be imaged first; the image path and the own-card permission go with the request.
  target.value = 'device';
  target.dispatchEvent(new w.Event('change', { bubbles: true }));
  check('a card in a reader: it can be imaged first', !d.getElementById('housecall-image-first-label').hidden);
  const first = d.getElementById('housecall-image-first');
  first.checked = true;
  first.dispatchEvent(new w.Event('change', { bubbles: true }));
  check('  its image path and own-card permission are asked for', !d.getElementById('housecall-image-first-path-label').hidden && !d.getElementById('housecall-on-own-card-label').hidden);
  path.value = '/dev/sda';
  d.getElementById('housecall-image-first-path').value = '/media/usb/card.img';
  d.getElementById('housecall-on-own-card').checked = true;
  d.getElementById('housecall-manifest').value = '/var/lib/irate-box-root/protected/manifest.json';
  posted.length = 0;
  done = false;
  d.getElementById('housecall-scan').click();
  await later(1800);
  check('  the request carries the image and the permission',
    posted.length === 1
      && posted[0].image_first === '/media/usb/card.img'
      && posted[0].on_own_card === true
      && posted[0].manifest === '/var/lib/irate-box-root/protected/manifest.json',
    posted);
  // A long examination: its running report is shown with Stop, which asks the root helper to stop it.
  target.value = 'image';
  target.dispatchEvent(new w.Event('change', { bubbles: true }));
  running = true;
  d.getElementById('housecall-scan').click();
  await later(1800);
  check(
    'while it runs: Stop is offered and Examine is not',
    !d.getElementById('housecall-stop').hidden && d.getElementById('housecall-scan').disabled,
  );
  d.getElementById('housecall-stop').click();
  await later(100);
  check('  Stop asks the root helper to stop it', posted.some((b) => b.action === 'stop'), posted);
  check('no page errors', errors.length === 0, errors);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
