// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Firmware Factory's tile on the front page (hub.js) in jsdom, static:
// what is building and how far along, what is waiting, each build's files as downloads labelled by
// kind. Usage: [JSDOM=…/jsdom] node dom-factory-tile.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
const server = fs.readFileSync(path.resolve(__dirname, '../../irate_box/hub/server.py'), 'utf8');
const card = server.match(/"factory": """([\s\S]*?)""",/)[1];
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => errors.push('console: ' + a.join(' ')));
const dom = new JSDOM(`<!doctype html><body><div class="services">${card}</div></body>`, { url: 'http://box.local/', runScripts: 'outside-only', virtualConsole: vc });
const w = dom.window;
const view = { running: { name: 'Heltec V3', env: 'heltec-v3', family: 'esp32s3', ref: 'v2.8.1', started: 1, done: 0.42 }, waiting: 3, paused: false, next: [],
  built: [{ run: '12', env: 'rak4631', name: 'RAK WisBlock 4631', family: 'nrf52840', ref: 'v2.8.1', files: ['firmware-rak4631.uf2'] },
    { run: '11', env: 'tbeam', name: 'T-Beam', family: 'esp32', ref: 'develop', files: ['firmware-tbeam.bin', 'firmware-tbeam.factory.bin', 'littlefs-tbeam.bin', 'firmware-tbeam.zip'] }] };
w.fetch = async (u) => (u === '/factory.json' ? new Response(JSON.stringify(view), { status: 200 }) : new Response('{}', { status: 404 }));
w.eval(fs.readFileSync(`${WEB}/hub.js`, 'utf8'));
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
setTimeout(() => {
  const d = w.document;
  check('what is building, how far, what waits', t(d.getElementById('factory-now')) === 'Building Heltec V3 (esp32s3), about 42 %; 3 waiting', t(d.getElementById('factory-now')));
  check('  the bar', !d.getElementById('factory-bar').hidden && d.getElementById('factory-progress').style.width === '42%');
  const rows = [...d.querySelectorAll('.factory-build')];
  check('each build, its files labelled by kind', rows.length === 2 && t(rows[0]) === 'RAK WisBlock 4631 UF2' && t(rows[1]) === 'T-Beam .bin factory .bin littlefs .bin .zip', rows.map(t).join(' | '));
  const a = rows[1].querySelector('a');
  check('  as downloads, from the public route', a.getAttribute('href') === '/factory/file?run=11&name=firmware-tbeam.bin' && a.download === 'firmware-tbeam.bin' && a.title === 'firmware-tbeam.bin (develop)');
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
}, 200);
