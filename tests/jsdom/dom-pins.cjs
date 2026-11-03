// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The pin map (/admin/#pins, admin-pins.js) in jsdom against a stood-in /admin/pins: nothing read yet, the
// helper's answer read by the request's id, a stale map not shown as fresh, a free and a taken pin drawn, the
// incomplete-map note said, and a failed read's own message shown.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-pins.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-pins.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|scrollIntoView/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#pins', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
let state = { pins: null, pending: 0, results: [] };
let asked = 0;
const posted = [];
const map = (at) => ({
  at,
  board: 'lyra',
  note: '',
  header: [
    { pin: 'gpiochip0:6', type: 'gpio', gpio: 6, name: 'unnamed', consumer: 'ff120000.spi', used: true },
    { pin: 'gpiochip0:2', type: 'gpio', gpio: 2, name: 'unnamed', consumer: '', used: false },
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
  check('  the claims list names the function', /ff120000\.spi/.test(text('pins-claims')), text('pins-claims'));
  // A known 40-pin header: left/right rows, then the table and the copyable text.
  const lyra = map(Date.now() / 1000);
  lyra.model = 'Luckfox Lyra Zero W';
  lyra.header = [
    { pin: 1, type: '3v3', name: '3V3_OUT', gpio: null, rmio: null, role: '', consumer: '', used: false },
    { pin: 2, type: '5v', name: 'VSYS', gpio: null, rmio: null, role: '', consumer: '', used: false },
    { pin: 3, type: 'gpio', name: 'GPIO0_A0', gpio: 0, rmio: 0, role: 'TP+RTC', consumer: 'ff060000.i2c', used: true },
    { pin: 4, type: '5v', name: 'VSYS', gpio: null, rmio: null, role: '', consumer: '', used: false },
  ];
  state = { pins: lyra, pending: 0, results: [] };
  w.rkRenderPins(state);
  check(
    'a known header: two pins a row, the used one red and the free/power ones said',
    d.querySelectorAll('#pins-map .pinrow').length === 2
      && d.querySelectorAll('#pins-map .pin.used').length === 1
      && /GPIO0_A0/.test(text('pins-map')) && /ff060000\.i2c · RM_IO0/.test(text('pins-map')),
    text('pins-map'),
  );
  check(
    '  the header does not mix in the board role; the roles table has it separately',
    !/TP\+RTC/.test(text('pins-map')) && /TP\+RTC/.test(text('pins-roles')) && /GPIO0_A0/.test(text('pins-roles')),
    [text('pins-map'), text('pins-roles')],
  );
  check(
    '  in use now is a table of claims, not every pin again',
    d.querySelectorAll('#pins-claims tbody tr').length === 1 && /ff120000\.spi/.test(text('pins-claims')),
    text('pins-claims'),
  );
  check('  copy is offered once a map is drawn', !d.getElementById('pins-copy').disabled);
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
