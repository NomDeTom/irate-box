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
  // The page reads the hardware document too, for the overlay and display state without a scan.
  if (u === '/admin/hardware') {
    return new Response(
      JSON.stringify({
        overlays: { items: { lcd: state.overlay } },
        display: state.overlay ? { on: !!state.overlay.display_on } : { supported: false },
        pins: state.pins,
        assignments: state.assignments || [],
      }),
      { status: 200 },
    );
  }
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
  check('  the tables of claims and roles are gone: the map and the selected pin carry them', !d.getElementById('pins-claims') && !d.getElementById('pins-roles'));
  // A known 40-pin header: left/right rows, then the table and the copyable text.
  const lyra = map(Date.now() / 1000);
  lyra.model = 'Luckfox Lyra Zero W';
  lyra.header = [
    { pin: 1, type: '3v3', name: '3V3_OUT', gpio: null, rmio: null, role: '', consumer: '', used: false },
    { pin: 2, type: '5v', name: 'VSYS', gpio: null, rmio: null, role: '', consumer: '', used: false },
    { pin: 3, type: 'gpio', name: 'GPIO0_A0', gpio: 0, chip: 'gpiochip0', line: 0, rmio: 0, role: 'TP+RTC', consumer: 'ff060000.i2c', label: 'I2C2 SDA', used: true },
    { pin: 4, type: '5v', name: 'VSYS', gpio: null, rmio: null, role: '', consumer: '', used: false },
    { pin: 5, type: 'gpio', name: 'GPIO0_A1', gpio: 5, rmio: 5, role: '', consumer: '', used: false },
    { pin: 6, type: 'gpio', name: 'GPIO0_C6', gpio: 22, rmio: 22, role: 'UART0 TX', consumer: '', used: false },
  ];
  state = { pins: lyra, pending: 0, results: [] };
  w.rkRenderPins(state);
  const rows = [...d.querySelectorAll('#pins-map .pinrow')];
  const squares = [...d.querySelectorAll('#pins-map .pin-square')];
  check(
    'a known header: two pins a row, a narrow strip of squares down the middle',
    rows.length === 3 && squares.length === 6 && /I2C2 SDA/.test(text('pins-map')),
    text('pins-map'),
  );
  const sideText = [...d.querySelectorAll('#pins-map .pin-side')].map((side) => side.textContent);
  check(
    '  each pin by its name and owner, not the kernel\'s address or RM_IO; power says nothing twice',
    !/ff060000|RM_IO/.test(text('pins-map')) && sideText[0] === '3V3_OUT' && sideText[1] === 'VSYS'
      && sideText[2] === 'I2C2 SDATP+RTC',
    sideText,
  );
  check(
    '  a free pin carries the board\'s role for it',
    sideText[5] === 'GPIO0_C6free · UART0 TX' && sideText[4] === 'GPIO0_A1free',
    sideText,
  );
  check(
    '  each square says its type: 3V3, 5V, taken and free, with a mark for e-paper',
    squares[0].classList.contains('pin-3v3') && /3V3/.test(squares[0].textContent)
      && squares[1].classList.contains('pin-5v') && /5V/.test(squares[1].textContent)
      && squares[2].classList.contains('pin-taken') && /●/.test(squares[2].textContent)
      && squares[3].classList.contains('pin-5v'),
    squares.map((square) => [square.className, square.textContent]),
  );
  // A pin selected on the header fills the card beside it.
  squares[4].click();
  check('a free pin on the map: its card and the form name it', /Read it once/.test(text('pins-gpio-selected'))
    && /Pin5NameGPIO0_A1/.test(text('pins-gpio-detail')) && /Held byfree/.test(text('pins-gpio-detail'))
    && d.getElementById('pins-gpio-pin').value === '5' && squares[4].classList.contains('is-selected'), [text('pins-gpio-selected'), text('pins-gpio-detail'), d.getElementById('pins-gpio-pin').value]);
  squares[2].click();
  check('a taken pin: its card says so, and the free pin stays the one to act on', /Only a free pin/.test(text('pins-gpio-selected'))
    && d.getElementById('pins-gpio-pin').value === '5' && squares[2].classList.contains('is-selected') && !squares[4].classList.contains('is-selected'), text('pins-gpio-selected'));
  check(
    '  its details: the name, the pinout\'s GPIO, RM_IO, the line, the kernel\'s claim and the board role',
    /NameI2C2 SDA/.test(text('pins-gpio-detail')) && /GPIOGPIO0_A0/.test(text('pins-gpio-detail'))
      && /RM_IORM_IO0/.test(text('pins-gpio-detail')) && /Linegpiochip0 line 0/.test(text('pins-gpio-detail'))
      && /Kernel claimff060000\.i2c/.test(text('pins-gpio-detail')) && /Board roleTP\+RTC/.test(text('pins-gpio-detail')),
    text('pins-gpio-detail'),
  );
  const sides = [...d.querySelectorAll('#pins-map .pin-side')];
  check(
    '  the words outward: the left side right-aligned, the right side left-aligned',
    sides.length === 6 && sides[0].classList.contains('left') && sides[1].classList.contains('right')
      && sides[2].classList.contains('left') && sides[3].classList.contains('right'),
    sides.map((side) => side.className),
  );
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  check(
    '  the strip has four columns and shrinks at 390',
    /\.pinrow\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 2\.15rem 2\.15rem minmax\(0, 1fr\)/.test(css),
  );
  check(
    '  the legend uses the same seven squares',
    ['pin-3v3', 'pin-5v', 'pin-gnd', 'pin-free', 'pin-assigned', 'pin-taken', 'pin-conflict'].every((name) =>
      d.querySelector(`#pins .pin-key.${name}`),
    ),
  );
  const shown = [...d.querySelectorAll('#pins-legend [data-state]')].filter((node) => !node.hidden).map((node) => node.dataset.state);
  check(
    '  the legend lists only the states drawn, with the mark each carries',
    shown.join() === 'taken,taken' && /●/.test(d.querySelector('#pins-legend .pin-key.pin-taken').textContent),
    shown,
  );
  check('  copy is offered once a map is drawn', !d.getElementById('pins-copy').disabled);
  // An assignment means a free pin is not free: assigned-idle is its own state, and a live stranger is a conflict.
  const assigned = map(Date.now() / 1000);
  assigned.header = [
    { pin: 5, type: 'gpio', name: 'GPIO0_A1', gpio: 5, rmio: 5, role: '', consumer: '', used: false },
    { pin: 6, type: 'gpio', name: 'GPIO0_B6', gpio: 6, rmio: 6, role: '', consumer: 'other-driver', used: true },
  ];
  w.rkRenderPins({
    pins: assigned,
    assignments: [
      { pin: 5, role: 'Display D/C', by: 'display', state: 'idle', source: 'lcdtouch' },
      { pin: 6, role: 'UART TX', by: 'serial', state: 'conflict', source: 'ownserial', active_owner: 'other-driver' },
    ],
  });
  const states = [...d.querySelectorAll('#pins-map .pin-square')].filter((square) => !square.classList.contains('pin-empty'));
  check(
    'an assignment not in use: assigned, not free; a foreign claim: conflict',
    states[0].classList.contains('pin-assigned')
      && /○/.test(states[0].textContent)
      && /for test card/.test(states[0].title)
      && states[1].classList.contains('pin-conflict')
      && /!/.test(states[1].textContent)
      && /other-driver, wanted by own serial/.test(states[1].title),
    states.map((square) => [square.className, square.textContent, square.title]),
  );
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
    /Overlay off\./.test(text('pins-overlay-state')) && !d.getElementById('pins-overlay-toggle').disabled && /Enable/.test(text('pins-overlay-toggle')),
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
    /Overlay off\./.test(text('pins-overlay-state')) && /Enable/.test(text('pins-overlay-toggle')),
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
    /display is off/.test(text('pins-overlay-state'))
      && !d.getElementById('pins-display-toggle').disabled
      && !d.getElementById('pins-test').disabled,
    [text('pins-overlay-state'), d.getElementById('pins-display-toggle').disabled],
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
    /test card is showing/.test(text('pins-overlay-state'))
      && /Turn display off/.test(text('pins-display-toggle'))
      && !d.getElementById('pins-test').disabled,
    [text('pins-overlay-state'), text('pins-display-toggle')],
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
      && /waiting for a restart/.test(text('pins-overlay-state')),
    [text('pins-overlay-state'), d.getElementById('pins-display-toggle').disabled],
  );
  // MUI's overlay holds the same pins: the test card waits, and says why.
  w.rkRenderPins({
    pins: fresh,
    overlay: { supported: true, on: false, enabled: false, installed: true, active: false, pending: false },
    mui: { status: { supported: true, overlay_enabled: true, unit_active: true, binary_present: true }, options: null },
  });
  check(
    '  MUI holding the pins: Enable and the test wait, and the card says MUI holds them',
    d.getElementById('pins-overlay-toggle').disabled && d.getElementById('pins-test').disabled
      && /MUI holds these pins/.test(text('pins-overlay-state')),
    text('pins-overlay-state'),
  );
  w.rkRenderPins({ ...activeOff, mui: null });
  // A free pin: read once, then keep it set as a pin hog pending a restart, then remove it.
  const gpioMap = map(Date.now() / 1000);
  gpioMap.header = [
    { pin: 7, type: 'gpio', name: 'GPIO0_A2', gpio: 2, rmio: 2, role: '', consumer: '', used: false },
    { pin: 8, type: 'gpio', name: 'GPIO0_C6', gpio: 22, rmio: 22, role: '', consumer: '', used: false },
  ];
  w.rkRenderPins({ pins: gpioMap, assignments: [] });
  const pinSelect = d.getElementById('pins-gpio-pin');
  check(
    'read or keep a pin: the free header pins are offered',
    pinSelect.options.length === 2 && /Pin 7/.test(pinSelect.textContent) && !d.getElementById('pins-gpio-read').disabled,
    [pinSelect.options.length, pinSelect.textContent],
  );
  posted.length = 0;
  d.getElementById('pins-gpio-read').click();
  await wait(100);
  check(
    '  Read once sends pins-gpio for the chosen pin',
    posted.length === 1 && posted[0].action === 'pins-gpio' && posted[0].pin === 7 && posted[0].mode === 'in',
    posted,
  );
  state = {
    pins: gpioMap,
    overlay: { supported: true, on: false, enabled: false, installed: false, active: false, pending: false },
    assignments: [],
    pending: 0,
    results: [{ id: 'p8', ok: true, msg: { id: 'pins-gpio.in.low', en: 'Pin 7 reads low.' } }],
  };
  await wait(700);
  check('  the answer is shown', /Pin 7 reads low/.test(text('pins-gpio-note')), text('pins-gpio-note'));
  posted.length = 0;
  d.getElementById('pins-gpio-hog').click();
  await wait(50);
  check('  Keep it set asks before changing the boot files', !!d.querySelector('.aw-ask'));
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check(
    '  the pin hog sent',
    posted.length === 1 && posted[0].action === 'pins-hog' && posted[0].pin === 7 && posted[0].mode === 'in',
    posted,
  );
  state = {
    ...state,
    assignments: [
      { pin: 7, role: 'Pin hog', by: 'pinhog', state: 'idle', source: 'pinhog', pending: true, mode: 'input', bias: 'none', value: null },
    ],
    results: [{ id: 'p9', ok: true, msg: { id: 'pinhog.added.in', en: 'Pin 7 is held as an input. Restart the box to apply it.' } }],
  };
  await wait(700);
  const hogOption = [...pinSelect.options].find((option) => option.value === '7');
  check(
    '  the hog is listed, waiting for a restart, and its pin can be chosen to change it',
    /After a restart/.test(text('pins-hogs'))
      && hogOption
      && /\(hog\)/.test(hogOption.textContent)
      && d.getElementById('pins-gpio-mode').value === 'in',
    [text('pins-hogs'), pinSelect.textContent, hogOption && hogOption.textContent],
  );
  posted.length = 0;
  const remove = [...d.querySelectorAll('#pins-hogs button')].find((button) => button.textContent === 'Remove');
  remove.click();
  await wait(50);
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check(
    '  Remove sends pins-hog with remove',
    posted.length === 1 && posted[0].action === 'pins-hog' && posted[0].remove === true && posted[0].pin === 7,
    posted,
  );
  check('no page errors', errors.length === 0, errors);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
