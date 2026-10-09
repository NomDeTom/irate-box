// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hotspot card on /admin → Network in jsdom: what it would do before it's on, the
// radio and channel choices, the try, switching on and off, a radio that can't do both (asked,
// with the 5-minute warning), and Keep it. Usage: [JSDOM=…/jsdom] node dom-ap.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 200) => new Promise((r) => setTimeout(r, ms));
const json = (b, status = 200) => new Response(JSON.stringify(b), { status });
const radios = [{ iface: 'wlan0', channels: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13] }];
const preview = { rung: 2, kind: 'own-channel', iface: 'wlan0', channel: 11, to_try: true, needs_choice: false,
  text: "The hotspot shares the radio with the box's WiFi link, on a channel of its own: guests don't notice the link roam." };
function page(running) {
  const posted = [], errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push(e.message); });
  // Its script tags stay: jsdom loads no src and runs none of the page's own scripts (outside-only).
  const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
  const dom = new JSDOM(html, { url: 'http://box.local/admin/#network', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  dom.window.confirm = () => true;
  dom.window.fetch = async (u, o = {}) => {
    if (o.method === 'POST' && u === '/admin/hotspot') { posted.push(JSON.parse(o.body)); return json({ id: 'x' }, 202); }
    if (u === '/admin/hotspot') return json({ running: running() });
    return json({}, 404);
  };
  dom.window.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));
  dom.window.eval(fs.readFileSync(`${WEB}/admin-layout.js`, 'utf8'));
  dom.window.eval(fs.readFileSync(`${WEB}/admin.js`, 'utf8'));
  return { w: dom.window, d: dom.window.document, posted, errors };
}
(async () => {
  let run = { up: false, preview, radios, owner: {}, tried: {}, confirmed: true, note: '' };
  let p = page(() => run);
  await wait(400);
  let d = p.d;
  check('off: what it would do, and that the try is still to come', /Off\. Switched on, it would run on a second interface beside wlan0, channel 11\./.test(t(d.getElementById('ap-state')))
    && /still to be tried/.test(t(d.getElementById('ap-state'))), t(d.getElementById('ap-state')));
  check('  the radio and its channels to choose, Automatic first', [...d.getElementById('ap-radio').options].map((o) => o.value).join() === ',wlan0'
    && d.getElementById('ap-channel').options.length === 14 && !d.getElementById('ap-try').hidden && t(d.getElementById('ap-switch')) === 'Switch on');
  d.getElementById('ap-channel').value = '6';
  d.getElementById('ap-form').dispatchEvent(new p.w.Event('submit', { cancelable: true })); await wait();
  check('Switch on: the owner\'s channel sent', p.posted[0] && p.posted[0].action === 'on' && p.posted[0].channel === 6 && !p.posted[0].radio, JSON.stringify(p.posted));
  check('  while the box works on it, the buttons wait', d.getElementById('ap-try').disabled && d.getElementById('ap-switch').disabled);
  p = page(() => run); await wait(400); d = p.d;
  d.getElementById('ap-try').click(); await wait();
  check('Try a channel of its own: asked', p.posted.some((b) => b.action === 'try'));
  run = { up: true, plan: Object.assign({}, preview, { channel: 6, to_try: false }), radios, owner: { channel: 6 }, confirmed: true, note: 'up on ap0, channel 6' };
  p = page(() => run); await wait(400); d = p.d;
  check('on: where it runs, Switch off', /^On on a second interface beside wlan0, channel 6\./.test(t(d.getElementById('ap-state'))) && t(d.getElementById('ap-switch')) === 'Switch off'
    && d.getElementById('ap-try').hidden, t(d.getElementById('ap-state')));
  d.getElementById('ap-form').dispatchEvent(new p.w.Event('submit', { cancelable: true })); await wait();
  check('  Switch off: asked', p.posted.some((b) => b.action === 'off'));
  const choose = { rung: 4, kind: 'choose', iface: 'wlan0', channel: null, needs_choice: true, text: "This radio can't run the hotspot and stay on your WiFi at once: choose one." };
  run = { up: false, preview: choose, radios, owner: {}, confirmed: true, note: '' };
  p = page(() => run); await wait(400); d = p.d;
  check('a radio that can\'t do both: the owner\'s choice offered', !d.getElementById('ap-take-label').hidden && /choose one/.test(t(d.getElementById('ap-state'))));
  d.getElementById('ap-form').dispatchEvent(new p.w.Event('submit', { cancelable: true })); await wait();
  check('  not ticked: nothing asked, it says why', !p.posted.length && /tick the box/.test(t(d.getElementById('ap-note'))));
  d.getElementById('ap-take').checked = true;
  d.getElementById('ap-form').dispatchEvent(new p.w.Event('submit', { cancelable: true })); await wait();
  check('  ticked and confirmed: asked with the radio given up', p.posted[0] && p.posted[0].take_radio === true);
  run = { up: true, plan: Object.assign({}, choose, { channel: 11, needs_choice: false, drops_uplink: true, iface: 'wlan0', kind: 'choose' }), radios, owner: { take_radio: true }, confirmed: false, note: '' };
  p = page(() => run); await wait(400); d = p.d;
  check('waiting to be kept: said, and Keep it', /Keep it from the hotspot/.test(t(d.getElementById('ap-state'))) && !d.getElementById('ap-confirm').hidden);
  d.getElementById('ap-confirm').click(); await wait();
  check('  Keep it: asked', p.posted.some((b) => b.action === 'confirm'));
  check('no page errors', !p.errors.length, p.errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
