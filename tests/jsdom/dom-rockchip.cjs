// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Rockchip boards (/admin/rockchip.html, admin-rockchip.html) in jsdom against a stood-in /admin/rkboard: no board, a board running its own
// system (Switch to Loader mode), in Loader mode (Erase the flash, asked first), in Maskrom mode (the chip chosen,
// sent with the erase), two boards, the toolkit missing, and the root helper's answer said under the buttons. Boards to
// set up: none in range, a setup hotspot's card and its drawer (the routes, the networks, the warning beside sending the
// password, a typed value Armbian's presets cannot take refused beside its field), two boards sharing a name, Set up off
// while the box's half is not built.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-rockchip.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin-rockchip.html`, 'utf8');
const js = ['admin-widgets.js', 'names.js', 'admin-rockchip.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/rockchip.html', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const loaders = [{ id: 'rk3506b', chip: 'RK3506B', boards: ['Luckfox Lyra Zero W'] }, { id: 'rk3506', chip: 'RK3506', boards: ['Luckfox Lyra Plus'] }];
const board = (mode) => ({ path: '1-1.2', id: mode === 'system' ? '2207:0013' : '2207:350f', product: mode === 'loader' ? 'USB download gadget' : 'rk3xxx', serial: 'b5', mode });
let state = { boards: [], tools: { rkdeveloptool: true, adb: true }, loaders, pending: 0, results: [] };
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/rkboard' && opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posted.push(body);
    if (body.action === 'secret') return new Response(JSON.stringify({ secret: body.id === 'rk1' ? { root: 'Root16charsAbcd1', user: 'salty-parrot', user_password: 'User16charsAbcd2' } : null }), { status: 200 });
    return new Response('{"id":"rk1"}', { status: 202 });
  }
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
const show = async (s) => { state = { ...state, ...s }; w.rkRender(state); await wait(50); };
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
  check('the toolkit missing: said, with the way to Toolkits; Erase disabled', !d.getElementById('rk-tools').hidden && /Rockchip boards toolkit/.test(text('rk-tools')) && d.querySelector('#rk-tools a[href="/admin/#toolkits"]') && buttons()[0].disabled, text('rk-tools'));
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  check('a card after a pane\'s words set apart by the shared rule, not a rule of this page\'s', css.includes('.admin-pane > p + :is(.net-card, .setting, .aw-block)') && css.includes('.admin-pane > p + div:not([class]) > .aw-block:first-child') && !css.includes('.rk-card') && css.includes('.admin-main > .admin-pane:not([hidden]) + .admin-pane:not([hidden])'));
  // The loader showed the SD card (an erase refused): writing an image to the card offered instead.
  const sha = 'a'.repeat(64);
  await show({ boards: [board('loader')], tools: { rkdeveloptool: true, adb: true }, images: [], results: [{ id: 'rk9', ok: false, msg: { id: 'rkboard.card-shown', en: 'The board\'s loader shows 14.6 GB…' } }] });
  check('the loader showed the card: writing an image to it offered; no image on the box yet, said where to put one', !!d.getElementById('rk-write') && /Write an image to the board's card/.test(text('rk-write')) && /rk-images/.test(text('rk-write')));
  await show({ images: [{ name: 'mPWRD-OS_lyra-zero-w.img.xz', size: 505341292, sha256: sha }] });
  const shaField = d.querySelector('#rk-write input[name="rk-sha256"]');
  const writeBtn = [...d.querySelectorAll('#rk-write button')].find((x) => x.textContent === 'Write the image to the card');
  check('  an image on the box: chosen, its sha256 from beside it, Write on', d.querySelector('#rk-images input[type=radio]').checked && shaField.value === sha && writeBtn && !writeBtn.disabled);
  shaField.value = 'not hex';
  shaField.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('  a sha256 that is not 64 hex digits: Write off', writeBtn.disabled);
  shaField.value = sha;
  shaField.dispatchEvent(new w.Event('input', { bubbles: true }));
  posted.length = 0;
  writeBtn.click();
  await wait(20);
  const confirmBtn = [...d.querySelectorAll('.aw-ask button')].find((x) => x.textContent === 'Write the image');
  check('  asked first, saying the card is replaced', confirmBtn && /Everything on the card is replaced/.test(d.querySelector('.aw-ask').textContent));
  confirmBtn.click();
  await wait(50);
  check('  then sent: write, the image, its sha256, no loader in Loader mode', posted.some((x) => x.action === 'write' && x.image === 'mPWRD-OS_lyra-zero-w.img.xz' && x.sha256 === sha && x.loader === null), posted);
  await show({ results: [{ id: 'rk1', ok: true, msg: { id: 'rkboard.card-written', en: 'The image is on the board\'s SD card…' } }] });
  check('  once written, the offer goes', !d.getElementById('rk-write'));
  await show({ results: [], images: [] });

  // Cards in a reader.
  w.rkRenderCards({ cards: [], images: [], pending: 0 });
  check('cards in a reader: none, said', /No card is in a reader on this box/.test(text('rk-cards')));
  const sdcard = { name: 'sda', path: '/dev/sda', size: 15634268160, model: 'STORAGE DEVICE', serial: '000000001206', mounted: false };
  w.rkRenderCards({ cards: [sdcard], images: [{ name: 'board.img.xz', size: 505341292, sha256: 'b'.repeat(64) }], pending: 0 });
  const cc = d.querySelector('#rk-cards .aw-card');
  check('  a card: its model and size, where it is', cc && /STORAGE DEVICE, 15.6 GB/.test(cc.textContent) && /In a USB reader \(\/dev\/sda\)/.test(cc.textContent));
  cc.querySelector('.aw-card-head').click();
  await wait(20);
  const cbtn = (label) => [...cc.querySelectorAll('button')].find((x) => x.textContent === label);
  check('  its drawer: Check, Test that it is genuine, and Write with the image and its sha256', cbtn('Check the card') && cbtn('Test that it is genuine') && cbtn('Write the image to the card') && !cbtn('Write the image to the card').disabled && cc.querySelector('input[name="rk-card-sha-sda"]').value === 'b'.repeat(64));
  posted.length = 0;
  cbtn('Check the card').click();
  await wait(30);
  check('  Check: sent at once (it writes nothing), with the card\'s name, size and serial', posted.some((x) => x.action === 'card-check' && x.card === 'sda' && x.size === 15634268160 && x.serial === '000000001206'), posted);
  posted.length = 0;
  cbtn('Test that it is genuine').click();
  await wait(20);
  check('  the genuine test asked first, saying everything on the card is lost', /everything on the card is lost/.test((d.querySelector('.aw-ask') || {}).textContent || '') && !posted.some((x) => x.action === 'card-genuine'));
  [...d.querySelectorAll('.aw-ask button')].find((x) => x.textContent === 'Cancel').click();
  cc.querySelector('.aw-card-head').click();
  w.rkRenderCards({ cards: [{ ...sdcard, mounted: true }], images: [], pending: 0 });
  const mc = d.querySelector('#rk-cards .aw-card');
  mc.querySelector('.aw-card-head').click();
  await wait(20);
  check('  a mounted card: said, its actions off', /Mounted: unmount it first/.test(mc.textContent) && [...mc.querySelectorAll('button.action-btn')].filter((x) => /Check|Test/.test(x.textContent)).every((x) => x.disabled));
  mc.querySelector('.aw-card-head').click();

  // Boards to set up.
  const setup = (spots, extra = {}) => ({ hotspots: spots, defaults: { ssid: 'SKYTB1H1', country: 'GB', timezone: 'Europe/London', locale: 'en_GB.UTF-8' }, ready: false, ...extra });
  const spot = { ssid: 'armbiansetup-luckfox-lyra-zero-w', host: 'luckfox-lyra-zero-w', signal: 70, boards: 1 };
  w.rkRenderSetup(setup([]));
  check('boards to set up: no hotspot in range, said; the card for a board not listed still there', /No setup hotspot is in range/.test(text('rk-setup')) && d.querySelectorAll('#rk-setup .aw-card').length === 1 && /A board not listed here/.test(text('rk-setup')));
  w.rkRenderSetup(setup([spot]));
  const card = d.querySelector('#rk-setup .aw-card');
  check('a setup hotspot: its card first, the board and how it stands; the board-not-listed card after it', card && /luckfox-lyra-zero-w/.test(card.textContent) && /Setup hotspot found, not set up/.test(card.textContent) && d.querySelectorAll('#rk-setup .aw-card').length === 2 && /A board not listed here/.test(d.querySelectorAll('#rk-setup .aw-card')[1].textContent), card && card.textContent);
  card.querySelector('.aw-card-head').click();
  await wait(20);
  const drawer = card.querySelector('.aw-drawer');
  const tile = (value) => drawer.querySelector(`input[type=radio][value="${value}"]`);
  check('  its drawer: Route, Network, Accounts, Name and place, After setup, Steps', drawer && ['Route', 'Network', 'Accounts', 'Name and place', 'After setup', 'Steps'].every((t) => [...drawer.querySelectorAll('h3')].some((h) => h.textContent === t)));
  check('  the hotspot route chosen; the routes not built folded under Other ways in, each saying why', tile('hotspot').checked && tile('card').disabled && tile('card').closest('details.sec-fold') && !tile('hotspot').closest('details') && /Not available here: the box cannot write a card yet/.test(drawer.textContent) && /only Raspberry Pi images take it/.test(drawer.textContent));
  check('  a part for another choice hidden: the typed passwords, while Generated and the box\'s own network are chosen', ['rk-wifiPass', 'rk-rootTyped', 'rk-userTyped'].every((n) => drawer.querySelector(`[name="${n}"]`).closest('[data-rk-only]').hidden));
  check("  this box's network chosen, named, with the warning beside it", tile('same').checked && /SKYTB1H1: the box sends the password/.test(drawer.textContent) && !drawer.querySelector('.warn-text').closest('[data-rk-only]').hidden);
  tile('other').click();
  await wait(10);
  check('  another network: its fields shown, the warning hidden', !drawer.querySelector('input[name="rk-otherSsid"]').closest('[data-rk-only]').hidden && drawer.querySelector('.warn-text').closest('[data-rk-only]').hidden);
  const pass = drawer.querySelector('input[name="rk-otherPass"]');
  pass.value = 'abc$defgh';
  pass.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('  a password with a dollar sign: refused beside its field, saying why', /It has a dollar sign, which Armbian's setup cannot take/.test(pass.closest('label').textContent));
  const host = drawer.querySelector('input[name="rk-hostname"]');
  check('  the name on the network: a default, and why to change it', host.value === 'lyra-1' && /Every new Lyra starts as luckfox-lyra-zero-w/.test(host.closest('label').textContent));
  const go = [...drawer.querySelectorAll('button')].find((b) => b.textContent === 'Set up the board');
  check("  Set up off while the box's half is not built, saying so", go && go.disabled && /Setting up is not built yet/.test(drawer.textContent));
  check('  the steps listed for the hotspot route, none done', drawer.querySelectorAll('.rk-steps li.step-todo').length === 7 && /Join the board's setup hotspot/.test(drawer.querySelector('.rk-steps').textContent));
  const user = drawer.querySelector('input[name="rk-userName"]');
  const dflt = [...user.closest('label').querySelectorAll('button.chip')].find((b) => b.textContent === 'Default');
  const dice = user.closest('label').querySelector('.rk-dice');
  check("  the user's name: armbian, its Default chip pressed, the dice beside it", user.value === 'armbian' && dflt && dflt.getAttribute('aria-pressed') === 'true' && dice && dice.textContent === '🎲');
  dice.click();
  check('  the dice: a made-up name as Linux takes it (lower case, hyphens), Default no longer pressed', /^[a-z]+(-[a-z]+)+$/.test(user.value) && dflt.getAttribute('aria-pressed') === 'false' && !/user's name: /.test(drawer.textContent), user.value);
  dflt.click();
  check('  Default: armbian again, pressed', user.value === 'armbian' && dflt.getAttribute('aria-pressed') === 'true');
  check('  the page polling again does not redraw an open drawer', (w.rkRenderSetup(setup([spot, { ...spot, ssid: 'armbiansetup-x', host: 'x' }])), d.querySelectorAll('#rk-setup .aw-card').length === 2 && card.querySelector('.aw-drawer')));
  card.querySelector('.aw-card-head').click();
  // A board not listed: the ways in that need no finding first, the serial console chosen; the hotspot folded, saying why.
  const otherCard = [...d.querySelectorAll('#rk-setup .aw-card')].find((c) => /A board not listed here/.test(c.textContent));
  otherCard.querySelector('.aw-card-head').click();
  await wait(20);
  const od = otherCard.querySelector('.aw-drawer');
  const otile = (value) => od.querySelector(`input[type=radio][value="${value}"]`);
  check('a board not listed: serial, card, SSH and USB first, serial chosen; the hotspot folded, saying why', otile('serial').checked && ['serial', 'card', 'ssh', 'usb'].every((v) => !otile(v).closest('details')) && otile('hotspot').closest('details.sec-fold') && /a board that raises its setup hotspot shows as a card of its own/.test(od.textContent));
  check("  the serial route's steps and its wiring line", /Find the board's console/.test(od.querySelector('.rk-steps').textContent) && /Read them back/.test(od.textContent) && !od.querySelector('[data-rk-only="route:serial"]').hidden && /TX to the board's RX/.test(od.textContent));
  check("  Armbian's default password at an address: offered, not yet choosable, saying why; its address field kept for it", otile('ssh').disabled && /Not available here: the box does not look for such a board yet/.test(od.textContent) && od.querySelector('input[name="rk-boardAddress"]').closest('[data-rk-only]').hidden);
  check('  no serial adapter on the box: Set up off, saying to connect one; the tile saying why', /Connect a USB serial adapter to this box/.test(od.textContent) && /Not available here: no USB serial adapter is connected to this box/.test(od.textContent));
  otherCard.querySelector('.aw-card-head').click();
  // A serial adapter on the box: the route offered, the adapter named, Set up sends the serial request.
  w.rkRenderSetup(setup([], { consoles: ['usb-1a86_USB2.0-Serial-if00-port0'], routes: ['serial'] }));
  const sCard = [...d.querySelectorAll('#rk-setup .aw-card')].find((c) => /A board not listed here/.test(c.textContent));
  sCard.querySelector('.aw-card-head').click();
  await wait(20);
  const sd = sCard.querySelector('.aw-drawer');
  const go2 = [...sd.querySelectorAll('button')].find((b) => b.textContent === 'Set up the board');
  check('a serial adapter on the box: the serial route offered and chosen, the adapter named, Set up on', sd.querySelector('input[type=radio][value="serial"]').checked && !sd.querySelector('input[type=radio][value="serial"]').closest('.unavailable') && /The serial adapter: usb-1a86_USB2.0-Serial-if00-port0/.test(sd.textContent) && go2 && !go2.disabled, sd.querySelector('p.setting-desc.bad') && sd.querySelector('p.setting-desc.bad').textContent);
  posted.length = 0;
  go2.click();
  await wait(20);
  [...d.querySelectorAll('.aw-ask button')].find((x) => x.textContent === 'Set up the board').click();
  await wait(50);
  const sent = posted.find((x) => x.action === 'setup');
  check('  Set up: the serial request, its adapter, the choices; waiting for the board to be powered', sent && sent.route === 'serial' && sent.console === 'usb-1a86_USB2.0-Serial-if00-port0' && sent.choices.userName === 'armbian' && /power it now/.test(sd.textContent), sent);
  w.rkRender({ ...state, results: [{ id: 'rk1', ok: true, msg: { id: 'rkserial.done', en: 'The board is set up as lyra-1, at 192.168.1.77: name lyra-1.' } }] });
  await wait(50);
  const res = d.getElementById('rk-setup-result');
  check('  set up: said above the boards, its passwords shown once, the secret asked for once', !res.hidden && /set up as lyra-1, at 192.168.1.77/.test(res.textContent) && /Root16charsAbcd1/.test(res.textContent) && /salty-parrot's password/.test(res.textContent) && /Shown once/.test(res.textContent) && posted.filter((x) => x.action === 'secret').length === 1, res.textContent);
  sCard.querySelector('.aw-card-head').click();
  w.rkRenderSetup(setup([{ ...spot, boards: 2 }]));
  check('two boards with the same name: said, one at a time', /2 boards share this hotspot's name/.test(text('rk-setup')));
  check('the check as rksetup.problem: a quote, a backtick, a line break; a fair password passes',
    /double quote/.test(w.rkProblem('a"b', 'text')) && /backtick/.test(w.rkProblem('a`b', 'text')) && /line break/.test(w.rkProblem('a\nb', 'text')) && w.rkProblem('Fair-pass-123', 'password') === '');
  check('no page errors', !errors.length, errors);
  process.exit(fails ? 1 : 0);
})();
