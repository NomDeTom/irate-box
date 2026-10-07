// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Firmware Factory page (next-work plan step 36) in jsdom, static: no hub needed. The sources,
// the refs (releases, then branches), the targets by family with readiness and estimates, the
// flasher's boards ticked to start with, the search, the queue request; the queue with its
// estimates, Up and Cancel, Pause; what was built, with what it used and its files.
// Usage: [JSDOM=…/jsdom] node dom-factory.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const now = Math.floor(Date.now() / 1000);
const snap = (over = {}) => Object.assign({
  sources: [{ name: 'meshtastic-firmware', area: 'public', mirror: true, upstream: 'https://github.com/meshtastic/firmware' },
    { name: 'my-fork', area: 'private', mirror: false, upstream: '' }],
  paused: false, max_per_request: 50, free: 40e9,
  running: { run: 'firmware-factory/7', env: 'native', name: 'native', family: 'native', source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d', state: 'running', started: now - 1200 },
  waiting: [{ id: '1791-aaaaaa', env: 'heltec-v3', name: 'Heltec V3', family: 'esp32s3', source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d', start: null, finish: null },
    { id: '1792-bbbbbb', env: 'rak4631', name: 'RAK WisBlock 4631', family: 'nrf52840', source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d', start: null, finish: null }],
  runs: [{ run: 'firmware-factory/7', state: 'running', env: 'native', family: 'native' },
    { run: 'firmware-factory/6', state: 'passed', env: 'native', name: 'native', family: 'native', source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d',
      commit: '8e6a88d0000000000000000000000000000000000', finished: now - 7200, duration: 9801, offline: true,
      resources: { cpu: 9400, peak_memory: 300 * 2 ** 20, work_bytes: 700 * 2 ** 20, hottest: 61.5, received: 5 * 2 ** 20 },
      artifacts: ['meshtasticd'] },
    { run: 'firmware-factory/5', state: 'failed', env: 'tbeam', name: 'T-Beam', family: 'esp32', source: 'my-fork', ref: 'main', commit: 'abc', finished: now - 9000, duration: 60, artifacts: [] }],
  estimates: { native: { seconds: 9801 } }, readiness: { native: 'offline ready' },
}, over);
const targets = { source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d', commit: '8e6a88d', families: { esp32s3: 2, native: 1, nrf52840: 1 },
  targets: [{ env: 'heltec-v3', family: 'esp32s3', name: 'Heltec V3', level: 'pr', support: '1', file: 'variants/esp32s3/heltec_v3/platformio.ini' },
    { env: 'tlora-t3s3-v1', family: 'esp32s3', name: 'LILYGO T3-S3', level: '', support: '1', file: 'x' },
    { env: 'native', family: 'native', name: 'native', level: 'extra', support: '', file: 'y' },
    { env: 'rak4631', family: 'nrf52840', name: 'RAK WisBlock 4631', level: 'pr', support: '1', file: 'z' }] };
const refs = { tags: [{ ref: 'v2.8.1.8e6a88d', commit: 'a' }, { ref: 'v2.7.26.54e0d8d', commit: 'b' }], branches: [{ ref: 'develop', commit: 'c' }] };
let state = snap();
const posted = [];
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/HTTP 404/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#factory', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const ok = (b, status = 200) => new Response(JSON.stringify(b), { status });
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST' && u === '/admin/factory') {
    const b = JSON.parse(opts.body); posted.push(b);
    if (b.action === 'queue') return ok({ batch: 'x', queued: b.targets.length, commit: '8e6a88d00', snapshot: state }, 202);
    if (b.action === 'tools' || b.action === 'offline') return ok({ queued: 1, env: 'rak4631', commit: '8e6a88d00', snapshot: state }, 202);
    if (b.action === 'pause') state = snap({ paused: true });
    return ok(state);
  }
  if (u === '/admin/factory') return ok(state);
  if (u.startsWith('/admin/factory/targets')) return ok(u.includes('&ref=') ? Object.assign({ refs }, targets) : { source: 'meshtastic-firmware', refs });
  if (u === '/admin/firmware') return ok({ settings: { boards: ['heltec-v3', 'nope'] } });
  return new Response('{}', { status: 404 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait(300);
  const d = w.document;
  check('a side-bar entry', [...d.querySelectorAll('.admin-side-list a')].some((a) => a.hash === '#factory' && t(a) === 'Firmware Factory'));
  check('sources: the mirror and the private fork, said which', [...d.querySelectorAll('#factory-source option')].map((o) => t(o)).join(' | ')
    === 'meshtastic-firmware (a mirror) | my-fork (private)');
  check('refs: releases, then branches', [...d.querySelectorAll('#factory-ref optgroup')].map((g) => `${g.label}: ${[...g.children].map((o) => o.value).join(',')}`).join(' | ')
    === 'Releases and tags: v2.8.1.8e6a88d,v2.7.26.54e0d8d | Branches: develop');
  const fams = [...d.querySelectorAll('.factory-family')];
  check('targets by family, with readiness and estimates', fams.length === 3 && /native 1 target; offline ready; about 2\.7 h a target here\./.test(t(fams[1].querySelector('summary')))
    && /esp32s3 2 targets, 1 chosen; untested on this box: its first build downloads its toolchain; no estimate yet\./.test(t(fams[0].querySelector('summary'))),
    fams.map((f) => t(f.querySelector('summary'))).join(' | '));
  check('  the flasher\'s boards ticked to start with, their family open', d.querySelector('input[value="heltec-v3"]').checked && fams[0].open && !fams[1].open);
  check('  the count and the button', /1 chosen, in 1 family\./.test(t(d.getElementById('factory-chosen'))) && t(d.getElementById('factory-queue')) === 'Queue 1'
    && !d.getElementById('factory-queue').disabled);
  d.getElementById('factory-search').value = 'rak'; d.getElementById('factory-search').dispatchEvent(new w.Event('input'));
  check('Show: only matching targets, by name or env', d.querySelectorAll('.factory-boards input').length === 1 && d.querySelector('.factory-family').open);
  d.querySelector('input[value="rak4631"]').click();
  d.getElementById('factory-search').value = ''; d.getElementById('factory-search').dispatchEvent(new w.Event('input'));
  check('  a choice kept across the search', d.querySelector('input[value="heltec-v3"]').checked && d.querySelector('input[value="rak4631"]').checked
    && /2 chosen, in 2 families\./.test(t(d.getElementById('factory-chosen'))));
  d.getElementById('factory-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  const q = posted.find((b) => b.action === 'queue');
  check('Queue: the source, ref and targets asked for', q && q.source === 'meshtastic-firmware' && q.ref === 'v2.8.1.8e6a88d' && q.targets.join() === 'heltec-v3,rak4631', JSON.stringify(q));
  check('  and says so, the choice cleared', /Queued 2 targets at 8e6a88d\./.test(t(d.getElementById('factory-note'))) && !d.querySelector('input[value="heltec-v3"]').checked);
  const items = [...d.querySelectorAll('#factory-waiting .admin-item')];
  check('the queue: what is building, with when it should be done', items.length === 3 && /^Building native, native: meshtastic-firmware v2\.8\.1\.8e6a88d Started 20 min ago; done about/.test(t(items[0])), t(items[0]));
  check('  waiting ones, no estimate promised where there is none', /1\. Heltec V3 \(heltec-v3\), esp32s3: .* No estimate/.test(t(items[1])));
  check('  Up on all but the first, Cancel on each', !/\bUp\b/.test(t(items[1].querySelector('.library-buttons'))) && /Up/.test(t(items[2].querySelector('.library-buttons'))));
  [...items[2].querySelectorAll('button')].find((b) => t(b) === 'Up').click();
  [...items[1].querySelectorAll('button')].find((b) => t(b) === 'Cancel').click();
  await wait();
  check('  Up and Cancel ask the hub for that job', posted.some((b) => b.action === 'up' && b.id === '1792-bbbbbb') && posted.some((b) => b.action === 'cancel' && b.id === '1791-aaaaaa'));
  const built = [...d.querySelectorAll('#factory-runs .admin-item')];
  check('built: what it used, offline said, its files and log', built.length === 2 && /Built offline native, native: .* \(8e6a88d\) 2 h ago; 2\.7 h, CPU 2\.6 h, peak memory 300 MB, disk 700 MB, hottest 62 °C, no network\./.test(t(built[0]))
    && [...built[0].querySelectorAll('a')].map((a) => a.getAttribute('href')).join(' ') === '/admin/ci/file?run=firmware-factory%2F6&name=meshtasticd /admin/ci/file?run=firmware-factory%2F6&name=log.txt',
    t(built[0]));
  check('  a failed one says so', /^failed T-Beam \(tbeam\)/.test(t(built[1])));
  const fold = [...d.querySelectorAll('.factory-family')].find((f) => t(f.querySelector('summary strong')) === 'nrf52840');
  [...fold.querySelectorAll('button')].find((b) => t(b) === 'Fetch tools').click();
  await wait();
  const tq = posted.find((b) => b.action === 'tools');
  check('Fetch tools: the family asked for at this source and ref, and said', tq && tq.family === 'nrf52840' && tq.source === 'meshtastic-firmware' && tq.ref === 'v2.8.1.8e6a88d'
    && /Fetching nrf52840's tools \(by rak4631\): queued\./.test(t(d.getElementById('factory-note'))), JSON.stringify(tq));
  [...fold.querySelectorAll('button')].find((b) => t(b) === 'Test offline').click();
  await wait();
  check('Test offline: asked for the family, and said', posted.some((b) => b.action === 'offline' && b.family === 'nrf52840')
    && /Building rak4631 with no network at all: queued\. If it passes, nrf52840 is offline ready\./.test(t(d.getElementById('factory-note'))));
  d.getElementById('factory-pause').click();
  await wait();
  check('Pause: asked, then said, the button now Resume', posted.some((b) => b.action === 'pause') && !d.getElementById('factory-paused').hidden && t(d.getElementById('factory-pause')) === 'Resume');
  const walker = d.createTreeWalker(d.getElementById('factory'), w.NodeFilter.SHOW_TEXT);
  const stray = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/\b(undefined|NaN|null)\b|\[object/.test(n.textContent)) stray.push(n.textContent.trim());
  check('no stray undefined, null or NaN', !stray.length, stray.join(' | '));
  check('no page errors', !errors.length, errors.join(' | '));
  // The web flasher: Publish on a passed build with a manifest; what is published, with Remove.
  w.renderFactory(snap({ running: null, waiting: [], runs: [{ run: 'firmware-factory/8', state: 'passed', env: 'heltec-v3', name: 'Heltec V3', family: 'esp32s3',
    source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d', commit: '8e6a88d', finished: now - 60, duration: 600,
    artifacts: ['firmware-heltec-v3-2.8.1.8e6a88d.bin', 'firmware-heltec-v3-2.8.1.8e6a88d.mt.json'] }, snap().runs[1]],
    flasher: [{ version: '2.8.1.8e6a88d-built', targets: [{ board: 'rak4631', platform: 'nrf52840', run: 'firmware-factory/3', ref: 'develop' }] }] }));
  const pubRuns = [...d.querySelectorAll('#factory-runs .admin-item')];
  const pubBtn = [...pubRuns[0].querySelectorAll('button')].find((b) => t(b) === 'Publish to the web flasher');
  check('flasher: Publish on a build with a manifest, not on native', pubBtn && ![...pubRuns[1].querySelectorAll('button')].some((b) => /Publish/.test(t(b))));
  check('  what is published listed, by board and version', /rak4631, nrf52840: 2\.8\.1\.8e6a88d from develop\./.test(t(d.getElementById('factory-flasher'))), t(d.getElementById('factory-flasher')));
  pubBtn.click();
  [...d.querySelectorAll('#factory-flasher button')].find((b) => t(b) === 'Remove').click();
  await wait();
  check('  Publish and Remove ask the hub', posted.some((b) => b.action === 'publish' && b.run === '8')
    && posted.some((b) => b.action === 'unpublish' && b.version === '2.8.1.8e6a88d-built' && b.env === 'rak4631'), JSON.stringify(posted.slice(-2)));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
