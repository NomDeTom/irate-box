// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Git's Builds on its own page (/admin/git.html, admin-git.js) in jsdom, static: the limits and
// what is offline, the runs and a run's view, the setup templates, the build cache and the runs
// kept. No hub needed. Usage: [JSDOM=…/jsdom] node dom-git-builds.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/admin-git.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-git.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const T = 1790950000;
const ciData = { installed: true, queued: 0, script: '.irate-ci.sh', time_limit: 43200, keep_runs: 5, runs_bytes: 5e6,
  memory: { max: String(300 * 2 ** 20), high: String(230 * 2 ** 20) }, mirrored: ['https://github.com/meshtastic/firmware', 'https://github.com/meshtastic/protobufs'],
  pio_deps: true, env: { CI: '1', CI_ARTIFACTS: 'a folder', HOME: 'kept' },
  templates: { make: { title: 'Run make', script: '#!/bin/bash\nmake\n' } },
  runs: [{ run: 'fw-ci/3', repo: 'fw-ci', branch: 'main', commit: 'a'.repeat(40), state: 'failed', started: T, finished: T + 60, duration: 60 },
    { run: 'fw-ci/2', repo: 'fw-ci', branch: 'main', commit: 'b'.repeat(40), state: 'passed', started: T - 99, finished: T - 50, duration: 49, keep: true }] };
const runView = { run: 'fw-ci/3', status: ciData.runs[0], log: '== start\nbuilding\n', offset: 0, next: 19, size: 19, steps: ['start'],
  artifacts: [{ name: 'fw.bin', size: 4321 }] };
const fwData = { settings: { cache: 'native' }, status: { cache: { version: '2.8.2.abcdef1', mode: 'native', bytes: 2e8 } } };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/git.html', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
require('./answer-asks.cjs')(w);
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posted.push([u, body]);
    if (u === '/admin/ci') return new Response(JSON.stringify(body.action === 'settings' ? ciData : { queued: true }), { status: 202 });
    if (u === '/admin/firmware') return new Response(JSON.stringify(fwData), { status: 200 });
    return new Response(JSON.stringify({ id: 'x' }), { status: 200 });
  }
  if (u === '/admin/ci') return new Response(JSON.stringify(ciData), { status: 200 });
  if (u.startsWith('/admin/ci/run?')) return new Response(JSON.stringify(runView), { status: 200 });
  if (u === '/admin/firmware') return new Response(JSON.stringify(fwData), { status: 200 });
  if (u === '/admin/git') return new Response(JSON.stringify({ served: true }), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 50) => new Promise((r) => setTimeout(r, ms));
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const button = (root, label) => root && [...root.querySelectorAll('button')].find((b) => t(b) === label);
(async () => {
  await wait(100);
  const d = w.document;
  check('the limits said', /Each build: up to 12 hours, memory limit 300\.0 MB, the lowest CPU priority\. The runs use 4\.8 MB\. The box retains the newest 5 runs for each repository/.test(t(d.getElementById('ci-limits'))),
    t(d.getElementById('ci-limits')));
  check('what comes from the box with no internet', /git downloads of 2 URLs in a build use the mirrors of this box \(github\.com\/meshtastic\/firmware/.test(t(d.getElementById('ci-offline')))
    && /PlatformIO packages come from the cache of the library/.test(t(d.getElementById('ci-offline'))), t(d.getElementById('ci-offline')));
  check('a kept run says so', /· retained/.test(t(d.getElementById('ci-runs'))));
  check('the setup templates and their variables', /Set up builds/.test(t(d.getElementById('ci-setup')))
    && /CI_ARTIFACTS/.test(t(d.querySelector('.ci-setup .ci-env'))) && /Run make/.test(t(d.querySelector('.ci-setup .ci-template'))));
  [...d.querySelectorAll('#ci-runs button')].find((b) => t(b) === 'View').click();
  await wait(100);
  const rv = d.getElementById('ci-run-view');
  check('a run\'s view: its steps, its log, its artifacts with sizes, Retain run and Delete run', !rv.hidden && /start/.test(t(rv.querySelector('.ci-steps')))
    && rv.querySelector('pre.ci-log').textContent === '== start\nbuilding\n' && /fw\.bin \(0\.1 MB\)/.test(t(rv)) && button(rv, 'Retain run') && button(rv, 'Delete run'), t(rv));
  w.confirm = () => true;
  button(rv, 'Delete run').click();
  await wait();
  check('  Delete asks the hub (the builder does it)', posted.some(([u, b]) => u === '/admin/ci' && b.action === 'delete' && b.run === 'fw-ci/3') && rv.hidden);
  const kf = d.getElementById('ci-keep-form');
  kf.elements.keep.value = '8';
  kf.elements.keep.dispatchEvent(new w.Event('input', { bubbles: true }));
  kf.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('runs kept per repository: a setting', posted.some(([u, b]) => u === '/admin/ci' && b.action === 'settings' && b.keep_runs === 8));
  check('the build cache: its choice and what is stored', d.querySelector('#ci-cache-form input[name=cache][value=native]').checked
    && /Stored firmware: 2\.8\.2\.abcdef1, headless, 190\.7 MB\./.test(t(d.getElementById('ci-cache-status'))), t(d.getElementById('ci-cache-status')));
  const cf = d.getElementById('ci-cache-form');
  cf.querySelector('input[name=cache][value=whole]').checked = true;
  cf.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('  saving the cache sends its mode', posted.some(([u, b]) => u === '/admin/firmware' && b.action === 'settings' && b.cache === 'whole'));
  button(d.getElementById('ci-cache-status'), 'Remove cache').click();
  await wait();
  check('  Remove cache asks the hub', posted.some(([u, b]) => u === '/admin/firmware' && b.action === 'flush-cache'));
  const walker = d.createTreeWalker(d.body, w.NodeFilter.SHOW_TEXT);
  const stray = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/^(null|undefined|NaN|\[object Object\])$/.test(n.textContent.trim())) stray.push(n.textContent);
  check('no stray null or undefined on the page', !stray.length, stray.join(','));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
