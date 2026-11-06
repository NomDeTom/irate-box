// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Pins page's Activate MUI picker in jsdom: candidates per bus, fixed/unavailable pins, duplicate
// detection, the replace-radio warning, activate/deactivate/restart requests.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-mui.cjs
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
const d = w.document;
const posted = [];
let asked = 0;
let state;
const signal = (pin, name, rmio, cs) => {
  const item = { pin, name, gpio: 40 + pin, rmio, signal: cs === undefined ? 'clk' : `csn${cs}`, label: `rm_io${rmio}_spi1_${cs === undefined ? 'clk' : 'csn' + cs}`, usable: true, why: 'free' };
  if (cs !== undefined) item.cs = cs;
  return item;
};
const gpio = (pin, name, gpioNo, fixed = '') => ({
  pin, name, gpio: gpioNo, rmio: gpioNo, fixed, gpiochip: Math.floor(gpioNo / 32), line: gpioNo % 32,
  usable: !fixed, why: fixed ? `fixed for ${fixed}` : 'free',
});
const defaults = { bus: 1, clk: 27, mosi: 29, miso: 31, display_cs: 35, touch_cs: 37, dc: 26, rst: 28, bl: 36, touch: 'lgfx', replace_radio: false, log_level: 'warn', log_destination: 'ram', map_tiles: 'dummy', map_tiles_folder: '' };
const options = {
  supported: true,
  board: 'Luckfox Lyra Zero W',
  roles: {},
  buses: [
    { bus: 0, signals: {
      clk: [signal(12, 'GPIO0_B6', 12)],
      mosi: [signal(13, 'GPIO0_A4', 13)],
      miso: [signal(15, 'GPIO0_A5', 15)],
      csn0: [signal(24, 'GPIO0_B2', 10, 0)],
      csn1: [signal(26, 'GPIO0_B1', 9, 1)],
    } },
    { bus: 1, signals: {
      clk: [signal(27, 'GPIO1_B1', 24)],
      mosi: [signal(29, 'GPIO1_B2', 25)],
      miso: [signal(31, 'GPIO1_B3', 26)],
      csn0: [signal(35, 'GPIO0_C0', 16, 0), signal(37, 'GPIO1_C3', 28, 0)],
      csn1: [signal(37, 'GPIO1_C3', 28, 1), signal(35, 'GPIO0_C0', 16, 1)],
    } },
  ],
  gpios: [
    gpio(26, 'GPIO0_B1', 9), gpio(28, 'GPIO1_D3', 59), gpio(36, 'GPIO1_D1', 57),
    gpio(7, 'GPIO0_A2', 2), gpio(8, 'GPIO0_C6', 22, 'UART0 TX'), gpio(11, 'GPIO0_A3', 3),
    gpio(12, 'GPIO0_B6', 14), gpio(13, 'GPIO0_A4', 4), gpio(15, 'GPIO0_A5', 5),
    gpio(24, 'GPIO0_B2', 10), gpio(26, 'GPIO0_B1', 9),
  ],
  touch_modes: ['lgfx', 'none'],
  log_levels: ['warn', 'info', 'debug'],
  log_destinations: ['ram', 'card'],
  map_tiles: ['dummy', 'folder'],
  defaults,
  selection: defaults,
  binary: '/usr/local/bin/meshtasticd-per-host-spi',
  binary_present: true,
};
const header = [
  { pin: 1, type: '3v3', name: '3V3_OUT', gpio: null, rmio: null, role: '', consumer: '', used: false },
  { pin: 27, type: 'gpio', name: 'GPIO1_B1', gpio: 41, rmio: 24, role: '', consumer: 'ff130000.spi', used: true },
  { pin: 29, type: 'gpio', name: 'GPIO1_B2', gpio: 42, rmio: 25, role: '', consumer: 'ff130000.spi', used: true },
  { pin: 31, type: 'gpio', name: 'GPIO1_B3', gpio: 43, rmio: 26, role: '', consumer: 'ff130000.spi', used: true },
  { pin: 35, type: 'gpio', name: 'GPIO0_C0', gpio: 16, rmio: 16, role: '', consumer: 'ff130000.spi', used: true },
  { pin: 37, type: 'gpio', name: 'GPIO1_C3', gpio: 51, rmio: 28, role: '', consumer: 'ff130000.spi', used: true },
  { pin: 26, type: 'gpio', name: 'GPIO0_B1', gpio: 9, rmio: 9, role: '', consumer: 'portduino', used: true },
  { pin: 28, type: 'gpio', name: 'GPIO1_D3', gpio: 59, rmio: 31, role: '', consumer: 'portduino', used: true },
  { pin: 36, type: 'gpio', name: 'GPIO1_D1', gpio: 57, rmio: 29, role: '', consumer: 'lcd-backlight', used: true },
  { pin: 7, type: 'gpio', name: 'GPIO0_A2', gpio: 2, rmio: 2, role: '', consumer: '', used: false },
  { pin: 8, type: 'gpio', name: 'GPIO0_C6', gpio: 22, rmio: 22, role: 'UART0 TX', consumer: '', used: false },
  { pin: 11, type: 'gpio', name: 'GPIO0_A3', gpio: 3, rmio: 3, role: '', consumer: '', used: false },
];
const status = (extra = {}) => ({
  supported: true, selection: defaults, overlay_enabled: false, overlay_installed: false, active: false, pending: false,
  unit_active: false, binary_present: true, binary: options.binary,
  spidev: { display: 'spidev1.0', touch: 'spidev1.1' },
  radio: { module: 'sim', spidev: 'spidev0.0', configured: true, replaced_meshtasticd: false },
  ...extra,
});
state = { pins: { at: Date.now() / 1000, board: 'lyra', model: 'Luckfox Lyra Zero W', header, lines: [], note: '' }, mui: { status: status(), options }, pending: 0, results: [] };
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/hardware') return new Response(JSON.stringify({ pins: state.pins, assignments: [] }), { status: 200 });
  if (u !== '/admin/pins') return new Response('{}', { status: 404 });
  if (opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posted.push(body);
    asked += 1;
    if (body.action === 'mui-activate') {
      state.mui.status = status({ pending: true, selection: body.selection, radio: { ...status().radio, replaced_meshtasticd: true } });
      state.results = [{ id: `m${asked}`, ok: true, msg: { id: 'mui.saved', en: 'MUI is saved with SimRadio on spidev0.0. Restart the box to apply the selected SPI pins.' } }];
    } else if (body.action === 'mui-deactivate') {
      state.mui.status = status({ pending: false, selection: body.selection || defaults });
      state.results = [{ id: `m${asked}`, ok: true, msg: { id: 'mui.off', en: 'MUI is stopped.' } }];
    } else if (body.action === 'mui-restart') {
      state.results = [{ id: `m${asked}`, ok: true, msg: { id: 'mui.restart', en: 'The box is restarting to apply the MUI overlay.' } }];
    }
    return new Response(JSON.stringify({ id: `m${asked}` }), { status: 202 });
  }
  return new Response(JSON.stringify(state), { status: 200 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (id) => (d.getElementById(id) || {}).textContent || '';
const option = (id, value) => [...d.getElementById(id).options].find((o) => o.value === String(value));
(async () => {
  await wait(150);
  check(
    'the picker is filled from the saved SPI1 selection and picks the free touch CS',
    d.getElementById('pins-mui-bus').value === '1'
      && d.getElementById('pins-mui-clk').value === '27'
      && d.getElementById('pins-mui-touch-cs').value === '37'
      && d.getElementById('pins-mui-touch-cs').selectedOptions[0].dataset.cs === '1'
      && d.getElementById('pins-mui-log-level').value === 'warn'
      && d.getElementById('pins-mui-log-destination').value === 'ram'
      && d.getElementById('pins-mui-map-tiles').value === 'dummy'
      && text('pins-mui-state') === 'Off.',
    [d.getElementById('pins-mui-clk').value, d.getElementById('pins-mui-touch-cs').value, d.getElementById('pins-mui-touch-cs').selectedOptions[0].dataset.cs, text('pins-mui-state')],
  );
  const logLevel = d.getElementById('pins-mui-log-level');
  const logDest = d.getElementById('pins-mui-log-destination');
  const mapTiles = d.getElementById('pins-mui-map-tiles');
  logLevel.value = 'debug';
  logLevel.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('debug logging shows the plain warning', !d.getElementById('pins-mui-log-warning').hidden && /writes about 100 lines a minute/.test(text('pins-mui-log-warning')));
  logDest.value = 'card';
  logDest.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('card logging keeps the warning visible', !d.getElementById('pins-mui-log-warning').hidden);
  mapTiles.value = 'folder';
  mapTiles.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('the tiles-folder path appears and blocks Activate until filled', !d.getElementById('pins-mui-map-folder-box').hidden && d.getElementById('pins-mui-activate').disabled);
  const mapFolder = d.getElementById('pins-mui-map-folder');
  mapFolder.value = '/home/lyra/maps';
  mapFolder.dispatchEvent(new w.Event('input'));
  const replaceBox = d.getElementById('pins-mui-replace-radio');
  replaceBox.checked = true;
  replaceBox.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('a filled tiles folder removes its guard on Activate', !d.getElementById('pins-mui-activate').disabled);
  replaceBox.checked = false;
  replaceBox.dispatchEvent(new w.Event('change'));
  await wait(20);
  // Return the visual controls to the remaining pin tests.
  logLevel.value = 'warn';
  logLevel.dispatchEvent(new w.Event('change'));
  logDest.value = 'ram';
  logDest.dispatchEvent(new w.Event('change'));
  mapTiles.value = 'dummy';
  mapTiles.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('a fixed pin is offered unavailable with its reason', option('pins-mui-dc', 8).disabled && /fixed for UART0 TX/.test(option('pins-mui-dc', 8).textContent), option('pins-mui-dc', 8).textContent);
  const bus = d.getElementById('pins-mui-bus');
  bus.value = '0';
  bus.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('SPI0 filters the SPI pins', d.getElementById('pins-mui-clk').value === '12' && /Pin 12/.test(option('pins-mui-clk', 12).textContent), [...d.getElementById('pins-mui-clk').options].map((o) => o.textContent));
  check(
    'a touch CS with the display CS number is disabled with the reason',
    option('pins-mui-touch-cs', 24).disabled && /same chip select/.test(option('pins-mui-touch-cs', 24).title),
    [option('pins-mui-touch-cs', 24).disabled, option('pins-mui-touch-cs', 24).title],
  );
  const clk = d.getElementById('pins-mui-clk');
  const dc = d.getElementById('pins-mui-dc');
  dc.value = clk.value;
  dc.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('a duplicate pin disables Activate and says so', d.getElementById('pins-mui-activate').disabled && /same pin/.test(text('pins-mui-state')), [d.getElementById('pins-mui-activate').disabled, text('pins-mui-state')]);
  dc.value = '7';
  dc.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('the replace-radio warning sits beside the setting when a real radio is configured', !d.getElementById('pins-mui-radio-warning').hidden && d.getElementById('pins-mui-activate').disabled, [d.getElementById('pins-mui-radio-warning').hidden, d.getElementById('pins-mui-activate').disabled]);
  const replace = d.getElementById('pins-mui-replace-radio');
  replace.checked = true;
  replace.dispatchEvent(new w.Event('change'));
  await wait(20);
  check('ticking replace_radio enables Activate and keeps the warning visible', !d.getElementById('pins-mui-activate').disabled && !d.getElementById('pins-mui-radio-warning').hidden);
  d.getElementById('pins-mui-activate').click();
  await wait(30);
  check('Activate asks before changing the boot files', !!d.querySelector('.aw-ask'));
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(700);
  const sent = posted.find((body) => body.action === 'mui-activate');
  check(
    'Activate posts the selected pins and replace_radio',
    sent &&
      sent.selection.bus === 0 &&
      sent.selection.clk === 12 &&
      sent.selection.dc === 7 &&
      sent.selection.replace_radio === true &&
      sent.selection.log_level === 'warn' &&
      sent.selection.log_destination === 'ram' &&
      sent.selection.map_tiles === 'dummy',
    sent,
  );
  check('  pending: Restart now is offered', !d.getElementById('pins-mui-restart').hidden && /waiting for a restart/.test(text('pins-mui-state')), [d.getElementById('pins-mui-restart').hidden, text('pins-mui-state')]);
  d.getElementById('pins-mui-restart').click();
  await wait(30);
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check('  restart sends the mui-restart action', posted.some((body) => body.action === 'mui-restart'), posted);
  posted.length = 0;
  state.mui.status = status({ unit_active: true, selection: defaults });
  w.rkRenderPins({ ...state, mui: state.mui });
  d.getElementById('pins-mui-deactivate').click();
  await wait(30);
  d.querySelector('.aw-ask .action-btn.primary').click();
  await wait(100);
  check('Deactivate sends the action', posted.some((body) => body.action === 'mui-deactivate'), posted);
  check('no page errors', errors.length === 0, errors);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
