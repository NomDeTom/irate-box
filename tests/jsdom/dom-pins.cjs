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
const js = ['el.js', 'admin-widgets.js', 'admin-pins.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
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
  check(
    'the SPI1 overlay: not read yet, Enable disabled',
    /Read the pins/.test(text('pins-overlay-state')) && d.getElementById('pins-overlay-toggle').disabled,
    [text('pins-overlay-state'), d.getElementById('pins-overlay-toggle').disabled],
  );
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
  const rows = [...d.querySelectorAll('#pins-map .pinrow')];
  const squares = [...d.querySelectorAll('#pins-map .pin-square')];
  check(
    'a known header: two pins a row, a narrow strip of squares down the middle',
    rows.length === 2 && squares.length === 4 && /GPIO0_A0/.test(text('pins-map')) && /ff060000\.i2c · RM_IO0/.test(text('pins-map')),
    text('pins-map'),
  );
  check(
    '  each square says its type: 3V3, 5V, taken and free, with a mark for e-paper',
    squares[0].classList.contains('pin-3v3') && /3V3/.test(squares[0].textContent)
      && squares[1].classList.contains('pin-5v') && /5V/.test(squares[1].textContent)
      && squares[2].classList.contains('pin-taken') && /●/.test(squares[2].textContent)
      && squares[3].classList.contains('pin-5v'),
    squares.map((square) => [square.className, square.textContent]),
  );
  const sides = [...d.querySelectorAll('#pins-map .pin-side')];
  check(
    '  the words outward: the left side right-aligned, the right side left-aligned',
    sides.length === 4 && sides[0].classList.contains('left') && sides[1].classList.contains('right')
      && sides[2].classList.contains('left') && sides[3].classList.contains('right'),
    sides.map((side) => side.className),
  );
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  check(
    '  the strip has four columns and shrinks at 390',
    /\.pinrow\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 2\.15rem 2\.15rem minmax\(0, 1fr\)/.test(css),
  );
  check(
    '  the legend uses the same five squares',
    ['pin-3v3', 'pin-5v', 'pin-gnd', 'pin-free', 'pin-taken'].every((name) => d.querySelector(`#pins .pin-key.${name}`)),
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
  // The SPI1 LCD and touch overlay: off, Enable asks and sends; on and pending, Disable asks and sends.
  posted.length = 0;
  const off = {
    pins: fresh,
    overlay: { supported: true, on: false, enabled: false, installed: false, active: false, pending: false },
    pending: 0,
    results: [],
  };
  w.rkRenderPins(off);
  check(
    'the SPI1 overlay off: Enable offered',
    /Off\./.test(text('pins-overlay-state')) && !d.getElementById('pins-overlay-toggle').disabled && /Enable/.test(text('pins-overlay-toggle')),
    [text('pins-overlay-state'), text('pins-overlay-toggle')],
  );
  d.getElementById('pins-overlay-toggle').click();
  await wait(50);
  check('  Enable asks before changing the boot files', !!d.querySelector('.aw-ask'));
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check(
    '  the overlay action sent, on',
    posted.length === 1 && posted[0].action === 'pins-overlay' && posted[0].on === true,
    posted,
  );
  const on = {
    ...off,
    overlay: { supported: true, on: true, enabled: true, installed: true, active: false, pending: true },
    results: [{ id: 'p4', ok: true, msg: { id: 'lcdtouch.on', en: 'Saved. Restart the box to use the SPI1 display and touch.' } }],
  };
  state = on;
  await wait(700);
  check(
    '  pending after the save: Disable offered',
    /waiting for a restart/.test(text('pins-overlay-state')) && /Disable/.test(text('pins-overlay-toggle')),
    [text('pins-overlay-state'), text('pins-overlay-toggle')],
  );
  posted.length = 0;
  d.getElementById('pins-overlay-toggle').click();
  await wait(50);
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check(
    '  Disable sends the overlay off',
    posted.length === 1 && posted[0].action === 'pins-overlay' && posted[0].on === false,
    posted,
  );
  state = {
    ...on,
    overlay: { ...on.overlay, on: false, enabled: false, installed: false, active: false, pending: false },
    results: [{ id: 'p5', ok: true, msg: { id: 'lcdtouch.off', en: 'Saved. The SPI1 display and touch overlay is off after a restart.' } }],
  };
  await wait(700);
  check(
    '  off again: Enable offered',
    /Off\./.test(text('pins-overlay-state')) && /Enable/.test(text('pins-overlay-toggle')),
    [text('pins-overlay-state'), text('pins-overlay-toggle')],
  );
  // The display's own controls: on/off, and the one-minute test.
  const activeOff = {
    pins: fresh,
    overlay: { supported: true, on: true, enabled: true, installed: true, active: true, pending: false, display_on: false },
    pending: 0,
    results: [],
  };
  w.rkRenderPins(activeOff);
  check(
    'display off: both display controls offered',
    /Off\./.test(text('pins-display-state'))
      && !d.getElementById('pins-display-toggle').disabled
      && !d.getElementById('pins-test').disabled,
    [text('pins-display-state'), d.getElementById('pins-display-toggle').disabled],
  );
  posted.length = 0;
  d.getElementById('pins-display-toggle').click();
  await wait(50);
  check('  turning it on asks first', !!d.querySelector('.aw-ask'));
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check(
    '  the display action sent, on',
    posted.length === 1 && posted[0].action === 'pins-display' && posted[0].on === true,
    posted,
  );
  state = {
    ...activeOff,
    overlay: { ...activeOff.overlay, display_on: true },
    results: [{ id: 'p6', ok: true, msg: { id: 'lcdtouch.display-on', en: 'The test card is on the display.' } }],
  };
  await wait(700);
  check(
    '  on: Turn display off offered, the test still offered',
    /On\./.test(text('pins-display-state'))
      && /Turn display off/.test(text('pins-display-toggle'))
      && !d.getElementById('pins-test').disabled,
    [text('pins-display-state'), text('pins-display-toggle')],
  );
  posted.length = 0;
  d.getElementById('pins-test').click();
  await wait(50);
  check('  the one-minute test asks first', !!d.querySelector('.aw-ask'));
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check('  the test action sent', posted.length === 1 && posted[0].action === 'pins-test', posted);
  state = {
    ...state,
    overlay: { ...state.overlay, display_on: false },
    results: [{ id: 'p7', ok: true, msg: { id: 'lcdtouch.test-done', en: 'The 60-second test ran. The display is off.' } }],
  };
  await wait(700);
  check('  the test note is shown', /60-second test/.test(text('pins-display-note')), text('pins-display-note'));
  w.rkRenderPins({ ...activeOff, overlay: { ...activeOff.overlay, active: false } });
  check(
    '  overlay not active: the display controls are disabled and say why',
    d.getElementById('pins-display-toggle').disabled
      && d.getElementById('pins-test').disabled
      && /Enable the overlay/.test(text('pins-display-state')),
    [text('pins-display-state'), d.getElementById('pins-display-toggle').disabled],
  );
  check('no page errors', errors.length === 0, errors);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
