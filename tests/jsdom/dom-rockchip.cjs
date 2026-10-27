// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Rockchip boards (/admin#rockchip) in jsdom against a stood-in /admin/rkboard: no board, a board running its own
// system (Switch to Loader mode), in Loader mode (Erase the flash, asked first), in Maskrom mode (the chip chosen,
// sent with the erase), two boards, the toolkit missing, and the root helper's answer said under the buttons.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-rockchip.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#rockchip', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const loaders = [{ id: 'rk3506b', chip: 'RK3506B', boards: ['Luckfox Lyra Zero W'] }, { id: 'rk3506', chip: 'RK3506', boards: ['Luckfox Lyra Plus'] }];
const board = (mode) => ({ path: '1-1.2', id: mode === 'system' ? '2207:0013' : '2207:350f', product: mode === 'loader' ? 'USB download gadget' : 'rk3xxx', serial: 'b5', mode });
let state = { boards: [], tools: { rkdeveloptool: true, adb: true }, loaders, pending: 0, results: [] };
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/rkboard' && opts.method === 'POST') { posted.push(JSON.parse(opts.body)); return new Response('{"id":"rk1"}', { status: 202 }); }
  if (u === '/admin/rkboard') return new Response(JSON.stringify(state), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const d = w.document;
const text = (id) => (d.getElementById(id) || {}).textContent || '';
const buttons = () => [...d.querySelectorAll('#rk-actions button')];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const show = async (s) => { state = { ...state, ...s }; w.rkShown = ''; await w.eval('loadRockchip()'); await wait(50); };
(async () => {
  await wait(300);
  check('no board: said how to connect one, no buttons', /No Rockchip board is connected/.test(text('rk-board')) && buttons().length === 0, text('rk-board'));
  await show({ boards: [board('system')] });
  check('its own system: said, Switch to Loader mode offered', /running its own system/.test(text('rk-board')) && buttons().map((b) => b.textContent).join() === 'Switch to Loader mode', text('rk-board'));
  buttons()[0].click();
  await wait(100);
  check('  pressed: the loader action sent, said as under way', posted.some((p) => p.action === 'loader') && /Switching the board/.test(text('rk-note')), JSON.stringify(posted));
  await show({ results: [{ id: 'rk1', ok: true, message: 'The board is in Loader mode, ready to erase.' }], boards: [board('loader')] });
  check('  the answer said under the buttons', /ready to erase/.test(text('rk-note')), text('rk-note'));
  check('Loader mode: Erase the flash offered', buttons().map((b) => b.textContent).join() === 'Erase the flash', buttons().map((b) => b.textContent));
  await show({ boards: [board('maskrom')], results: [] });
  const tiles = [...d.querySelectorAll('#rk-chip-tiles input, #rk-chip-tiles [role="radio"]')];
  check('Maskrom: the chip asked, RK3506B first and chosen, Erase offered', !d.getElementById('rk-chip').hidden && /RK3506B/.test(text('rk-chip')) && /Lyra Zero W/.test(text('rk-chip')) && buttons().length === 1, text('rk-chip'));
  await show({ boards: [board('loader'), { ...board('loader'), path: '1-1.3' }] });
  check('two boards: one at a time, no buttons', /2 boards are connected/.test(text('rk-board')) && buttons().length === 0);
  await show({ boards: [board('loader')], tools: { rkdeveloptool: false, adb: false } });
  check('the toolkit missing: said, with the way to Toolkits; Erase disabled', !d.getElementById('rk-tools').hidden && /Rockchip boards toolkit/.test(text('rk-tools')) && d.querySelector('#rk-tools a[href="#toolkits"]') && buttons()[0].disabled, text('rk-tools'));
  check('no page errors', !errors.length, errors);
  process.exit(fails ? 1 : 0);
})();
