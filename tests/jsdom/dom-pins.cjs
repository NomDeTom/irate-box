// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The pin map (/admin/pins.html, admin-pins.js) in jsdom against a stood-in /admin/pins: nothing read yet, the
// helper's answer read by the request's id, a stale map not shown as fresh, a free and a taken pin drawn, the
// incomplete-map note said, and a failed read's own message shown.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-pins.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin-pins.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-pins.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|scrollIntoView/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/pins.html', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
let state = { pins: null, pending: 0, results: [] };
let asked = 0;
const posted = [];
const map = (at) => ({
  at,
  board: 'lyra',
  note: '',
  header: [
    { pin: 'gpiochip0:6', gpio: 6, name: 'unnamed', consumer: 'ff120000.spi', used: true },
    { pin: 'gpiochip0:2', gpio: 2, name: 'unnamed', consumer: '', used: false },
  ],
  lines: [
    { chip: 'gpiochip0', line: 6, name: 'unnamed', consumer: 'ff120000.spi', used: true },
    { chip: 'gpiochip0', line: 2, name: 'unnamed', consumer: '', used: false },
  ],
});
w.fetch = async (u, opts = {}) => {
  if (u !== '/admin/pins') return new Response('{}', { status: 404 });
  if (opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posted.push(body);
    asked += 1;
    return new Response(JSON.stringify({ id: `p${asked}` }), { status: 202 });
  }
  return new Response(JSON.stringify(state), { status: 200 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const d = w.document;
const text = (id) => (d.getElementById(id) || {}).textContent || '';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait(100);
  check('nothing read yet: said so, no pins', /Not read yet/.test(text('pins-board')) && !d.querySelector('#pins-map .pin'), text('pins-board'));
  const at = Date.now() / 1000;
  state = { pins: map(at - 3600), pending: 1, results: [] };
  d.getElementById('pins-scan').click();
  await wait(100);
  check('Read the pins: the action sent, while it runs', posted.length === 1 && posted[0].action === 'pins', posted);
  await wait(700);
  check('  the old map is not shown as the new read', !d.querySelector('#pins-map .pin') && /Reading the GPIO lines/.test(text('pins-note')), text('pins-note'));
  const fresh = map(Date.now() / 1000);
  state = {
    pins: fresh,
    pending: 0,
    results: [{ id: 'p1', ok: true, message: '2 GPIO lines read.', msg: { id: 'control.pins', en: '2 GPIO lines read.' } }],
  };
  await wait(700);
  const pins = [...d.querySelectorAll('#pins-map .pin')];
  check(
    '  the helper answer is shown, and the map: a taken pin and a free one',
    /2 GPIO lines read/.test(text('pins-note'))
      && pins.length === 2
      && pins[0].className.includes('used')
      && /ff120000\.spi/.test(pins[0].textContent)
      && pins[1].className.includes('free')
      && /free/.test(pins[1].textContent),
    pins.map((p) => [p.className, p.textContent]),
  );
  check('  the claims list names the function', /gpiochip0 line 6: ff120000\.spi/.test(text('pins-claims')), text('pins-claims'));
  state = { ...state, pending: 1, results: [] };
  d.getElementById('pins-scan').click();
  await wait(100);
  state = {
    pins: { ...fresh, at: Date.now() / 1000, note: 'The pin-mux view is not available, so a pin a function holds may show as free.' },
    pending: 0,
    results: [{ id: 'p2', ok: true, message: '2 GPIO lines read.', msg: { id: 'control.pins', en: '2 GPIO lines read.' } }],
  };
  await wait(700);
  check('an incomplete map: the note is shown beside it', /may show as free/.test(text('pins-board')), text('pins-board'));
  state = { ...state, pending: 1, results: [] };
  d.getElementById('pins-scan').click();
  await wait(100);
  state = {
    pins: fresh,
    pending: 0,
    results: [{ id: 'p3', ok: false, message: 'gpioinfo is not installed (it comes with the gpiod package).' }],
  };
  await wait(700);
  check('a failed read: the helper\'s own message, not "not read in time"', /gpiod package/.test(text('pins-note')), text('pins-note'));
  check('no page errors', errors.length === 0, errors);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
