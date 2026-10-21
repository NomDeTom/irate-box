// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Firmware Factory page in jsdom, static: no hub needed. The sources,
// the refs (releases, then branches), the targets by family with readiness and estimates, the
// flasher's boards ticked to start with, the search, the queue request; the queue with its
// estimates, Up and Cancel, Pause; what was built, with what it used and its files.
// Usage: [JSDOM=…/jsdom] node dom-factory.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
// The Factory's own page: /admin/factory.html, its code in factory.js (after admin-widgets.js, as the page loads them).
const html = fs.readFileSync(`${WEB}/admin-factory.html`, 'utf8');
const js = ['admin-widgets.js', 'factory.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
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
  estimates: { native: { seconds: 9801 } }, readiness: { native: { id: 'factory.ready.offline-test', en: 'Offline build passed for the test target.' } },
}, over);
const targets = { source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d', commit: '8e6a88d', families: { esp32s3: 2, native: 1, nrf52840: 1 },
  suggested: { esp32s3: 'heltec-v3', native: 'native', nrf52840: 'rak4631' },
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
const dom = new JSDOM(html, { url: 'http://box.local/admin/factory.html', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
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
  check('its own page: the Factory\'s title, and its settings a link back to /admin', t(d.querySelector('h1')) === 'Firmware Factory' && !!d.querySelector('header a[href="/admin/#page-app-box-factory"]') && !d.querySelector('.admin-side-list'));
  check('sources: the mirror and the local fork, said which', [...d.querySelectorAll('#factory-source option')].map((o) => t(o)).join(' | ')
    === 'meshtastic-firmware (mirror) | my-fork (local repository)');
  check('refs: releases, then branches', [...d.querySelectorAll('#factory-ref optgroup')].map((g) => `${g.label}: ${[...g.children].map((o) => o.value).join(',')}`).join(' | ')
    === 'Releases and tags: v2.8.1.8e6a88d,v2.7.26.54e0d8d | Branches: develop');
  const fams = [...d.querySelectorAll('.factory-family')];
  check('targets by family, with readiness and estimates', fams.length === 3 && /native 1 target\. Offline build passed for the test target\. About 2\.7 h a target here\./.test(t(fams[1].querySelector('summary')))
    && /esp32s3 2 targets\. 1 selected\. Not tested on this box\. Its first build downloads its toolchain\. No build estimate\./.test(t(fams[0].querySelector('summary'))),
    fams.map((f) => t(f.querySelector('summary'))).join(' | '));
  check('  the flasher\'s boards ticked to start with, their family open', d.querySelector('input[value="heltec-v3"]').checked && fams[0].open && !fams[1].open);
  check('  the count and the button', /1 selected, in 1 family\./.test(t(d.getElementById('factory-chosen'))) && t(d.getElementById('factory-queue')) === 'Add 1 target to queue'
    && !d.getElementById('factory-queue').disabled);
  d.getElementById('factory-search').value = 'rak'; d.getElementById('factory-search').dispatchEvent(new w.Event('input'));
  check('Show: only matching targets, by name or env', d.querySelectorAll('.factory-boards input').length === 1 && d.querySelector('.factory-family').open);
  d.querySelector('input[value="rak4631"]').click();
  d.getElementById('factory-search').value = ''; d.getElementById('factory-search').dispatchEvent(new w.Event('input'));
  check('  a choice kept across the search', d.querySelector('input[value="heltec-v3"]').checked && d.querySelector('input[value="rak4631"]').checked
    && /2 selected, in 2 families\./.test(t(d.getElementById('factory-chosen'))));
  d.getElementById('factory-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  const q = posted.find((b) => b.action === 'queue');
  check('Queue: the source, ref and targets asked for', q && q.source === 'meshtastic-firmware' && q.ref === 'v2.8.1.8e6a88d' && q.targets.join() === 'heltec-v3,rak4631', JSON.stringify(q));
  check('  and says so, the choice cleared', /Added 2 targets to the queue at 8e6a88d\./.test(t(d.getElementById('factory-note'))) && !d.querySelector('input[value="heltec-v3"]').checked);
  const items = [...d.querySelectorAll('#factory-waiting .admin-item')];
  check('the queue: what is building, with when it should be done', items.length === 3 && /^Building native, native: meshtastic-firmware v2\.8\.1\.8e6a88d Started 20 min ago\. Finishes about/.test(t(items[0])), t(items[0]));
  check('  waiting ones, no estimate promised where there is none', /1\. Heltec V3 \(heltec-v3\), esp32s3: .* No build estimate/.test(t(items[1])));
  check('  Up on all but the first, Cancel on each', !/Move earlier/.test(t(items[1].querySelector('.library-buttons'))) && /Move earlier/.test(t(items[2].querySelector('.library-buttons'))));
  [...items[2].querySelectorAll('button')].find((b) => t(b) === 'Move earlier').click();
  [...items[1].querySelectorAll('button')].find((b) => t(b) === 'Cancel queued build').click();
  await wait();
  check('  Move earlier and Cancel queued build ask the hub for that job', posted.some((b) => b.action === 'up' && b.id === '1792-bbbbbb') && posted.some((b) => b.action === 'cancel' && b.id === '1791-aaaaaa'));
  const built = [...d.querySelectorAll('#factory-runs .admin-item')];
  check('built: what it used, offline said, its files and log', built.length === 2 && /Built offline native, native: .* \(8e6a88d\) 2 h ago\. 2\.7 h, CPU 2\.6 h, peak memory 300 MB, disk 700 MB, peak temperature 62 °C, network disabled during build\./.test(t(built[0]))
    && [...built[0].querySelectorAll('a')].map((a) => a.getAttribute('href')).join(' ') === '/admin/ci/file?run=firmware-factory%2F6&name=meshtasticd /admin/ci/file?run=firmware-factory%2F6&name=log.txt',
    t(built[0]));
  check('  a failed one says so', /^failed T-Beam \(tbeam\)/.test(t(built[1])));
  const fold = [...d.querySelectorAll('.factory-family')].find((f) => t(f.querySelector('summary strong')) === 'nrf52840');
  [...fold.querySelectorAll('button')].find((b) => t(b) === 'Download build dependencies').click();
  await wait();
  const tq = posted.find((b) => b.action === 'tools');
  check('Download build dependencies: the family asked for at this source and ref, with its test target, and said', tq && tq.family === 'nrf52840' && tq.env === 'rak4631' && tq.source === 'meshtastic-firmware' && tq.ref === 'v2.8.1.8e6a88d'
    && /Downloading build dependencies for rak4631, a nrf52840 target: queued\./.test(t(d.getElementById('factory-note'))), JSON.stringify(tq));
  [...fold.querySelectorAll('button')].find((b) => t(b) === 'Test target offline').click();
  await wait();
  check('Test target offline: asked for the family, and said', posted.some((b) => b.action === 'offline' && b.family === 'nrf52840')
    && /Building rak4631 with the network disabled: queued\. A pass shows that rak4631 builds offline/.test(t(d.getElementById('factory-note'))));
  const s3 = [...d.querySelectorAll('.factory-family')].find((x) => t(x.querySelector('summary strong')) === 'esp32s3');
  const sel = s3.querySelector('.factory-test-target select');
  check('the test target: the suggested board to start with, any of the family to choose', sel.value === 'heltec-v3' && sel.options.length === 2);
  sel.value = [...sel.options].find((o) => o.value !== 'heltec-v3').value; sel.dispatchEvent(new w.Event('change'));
  [...s3.querySelectorAll('button')].find((b) => t(b) === 'Test target offline').click();
  await wait();
  check('  the one chosen is the one asked for', posted.some((b) => b.action === 'offline' && b.family === 'esp32s3' && b.env === sel.value), JSON.stringify(posted.slice(-1)));
  d.getElementById('factory-pause').click();
  await wait();
  check('Pause: asked, then said, the button now Resume queue', posted.some((b) => b.action === 'pause') && !d.getElementById('factory-paused').hidden && t(d.getElementById('factory-pause')) === 'Resume queue');
  check('the production line behind the pane (krab-loop.js plays it lit)', d.querySelector('.admin-main').dataset.art === 'factory');
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
  const pubBtn = [...pubRuns[0].querySelectorAll('button')].find((b) => t(b) === 'Publish firmware');
  check('flasher: Publish on a build with a manifest, not on native', pubBtn && ![...pubRuns[1].querySelectorAll('button')].some((b) => /Publish/.test(t(b))));
  check('  what is published listed, by board and version', /rak4631, nrf52840: 2\.8\.1\.8e6a88d from develop\./.test(t(d.getElementById('factory-flasher'))), t(d.getElementById('factory-flasher')));
  pubBtn.click();
  [...d.querySelectorAll('#factory-flasher button')].find((b) => t(b) === 'Unpublish firmware').click();
  await wait();
  check('  Publish and Unpublish firmware ask the hub', posted.some((b) => b.action === 'publish' && b.run === '8')
    && posted.some((b) => b.action === 'unpublish' && b.version === '2.8.1.8e6a88d-built' && b.env === 'rak4631'), JSON.stringify(posted.slice(-2)));
  // A build cut off by a restart (ci.as_shown): said, with Build again; its early times said too.
  w.renderFactory(snap({ running: null, waiting: [], runs: [{ run: 'firmware-factory/23', state: 'interrupted', env: 'rak4631', name: 'RAK WisBlock 4631', family: 'nrf52840',
    source: 'meshtastic-firmware', ref: 'v2.8.1.8e6a88d', commit: '8e6a88d', finished: now - 3600, duration: 1345, clock_synced: false, artifacts: [] }, snap().runs[2]] }));
  const cut = [...d.querySelectorAll('#factory-runs .admin-item')];
  check('interrupted: said, with when its log stopped and why its times may be early', /^interrupted RAK WisBlock 4631 \(rak4631\).* 60 min ago\. 22 min\. The box or the builder stopped during this build\. Its times may be early/.test(t(cut[0])), t(cut[0]));
  check('  Build again on it alone, not on the failed one', [...cut[0].querySelectorAll('button')].some((b) => t(b) === 'Build again') && ![...cut[1].querySelectorAll('button')].some((b) => t(b) === 'Build again'));
  [...cut[0].querySelectorAll('button')].find((b) => t(b) === 'Build again').click();
  await wait();
  check('  Build again asks the hub for that run', posted.some((b) => b.action === 'again' && b.run === '23'), JSON.stringify(posted.slice(-1)));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
