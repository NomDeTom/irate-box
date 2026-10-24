// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Polling and a selection, in jsdom: with text selected on /admin, each pane's poll
// that brings nothing new replaces none of the text, so the selection (and a copy in progress) stays; text that did
// change is drawn as ever. BASE is a hub of this checkout (tests/jsdom/run.sh starts one), read as admin with
// FRONT_SECRET; each answer is kept and given again, as a poll that brings nothing new.
// Usage: BASE=http://127.0.0.1:PORT FRONT_SECRET=… [JSDOM=…/jsdom] node dom-redraw.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
const BASE = process.env.BASE || 'http://127.0.0.1:18098';
if (require('child_process').spawnSync('curl', ['-s', '-o', '/dev/null', '-m', '3', new URL('/status', BASE).href]).status !== 0) {
  console.log(`skipped: no hub answering at ${BASE} (this test reads a hub's answers; start one and set BASE)`);
  process.exit(0);
}
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: BASE + '/admin/#overview', runScripts: 'outside-only', virtualConsole: new VirtualConsole(), pretendToBeVisual: true });
const w = dom.window, d = w.document;
const kept = new Map();
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') return new Response('{}', { status: 404 });
  if (!kept.has(u)) {
    const front = process.env.FRONT_SECRET ? { 'X-Irate-Front': process.env.FRONT_SECRET, 'X-Irate-Admin': '1' } : {};
    const r = await fetch(new URL(u, BASE), { ...opts, headers: { ...(opts.headers || {}), ...front } });
    kept.set(u, [r.status, await r.text()]);
  }
  const [status, body] = kept.get(u);
  return new Response(body, { status });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 100) => new Promise((r) => setTimeout(r, ms));
const texts = () => { const out = []; const tw = d.createTreeWalker(d.body, 4); let n; while ((n = tw.nextNode())) if (n.data.trim()) out.push(n); return out; };
const LOADERS = ['loadLibrary', 'loadPackages', 'loadBox', 'loadModeration', 'loadStore', 'loadUpdate', 'loadSecurity', 'loadHealth', 'loadNetwork',
  'loadAp', 'loadHotspot', 'loadKit', 'loadAccess', 'loadAddons', 'loadLocal', 'loadGit', 'loadCi', 'loadFirmware', 'loadKits', 'loadTls', 'loadAccounts'];

(async () => {
  await wait(1500);
  const sel = d.getSelection();
  for (const name of LOADERS) {
    try { await w[name](); } catch (_) { /* a pane the hub has no data for: drawn as it can be */ }
    await wait();
    const range = d.createRange();
    range.selectNodeContents(d.querySelector('main') || d.body);
    sel.removeAllRanges();
    sel.addRange(range);
    const before = new Map(texts().map((n) => [n, (n.parentElement.closest('[id]') || {}).id]));
    try { await w[name](); } catch (_) { /* as above */ }
    await wait();
    const gone = {};
    for (const [n, where] of before) if (!n.isConnected) gone[where] = (gone[where] || 0) + 1;
    check(`${name}: with text selected, a poll that brings nothing new replaces none of it`, !Object.keys(gone).length && !sel.isCollapsed, JSON.stringify(gone));
    sel.removeAllRanges();
  }
  // Text that changed is drawn, selection or not.
  const box = d.createElement('p');
  box.textContent = 'before';
  d.body.append(box);
  const r = d.createRange();
  r.selectNodeContents(box);
  sel.addRange(r);
  box.textContent = 'after';
  const list = d.createElement('ul');
  list.append(Object.assign(d.createElement('li'), { textContent: 'one' }));
  d.body.append(list);
  r.selectNodeContents(list);
  list.replaceChildren(Object.assign(d.createElement('li'), { textContent: 'two' }));
  check('text that changed is drawn while selected', box.textContent === 'after' && list.textContent === 'two');
  sel.removeAllRanges();

  // AW.drawKept: a block whose cards hold fields, or values that move at every poll.
  const card = (name, signal, value = '') => {
    const c = d.createElement('div');
    c.className = 'card';
    const p = d.createElement('p');
    p.textContent = `${name}, a WiFi adapter.`;
    const live = d.createElement('span');
    live.setAttribute('data-live', 'now');
    live.textContent = `signal ${signal} dBm`;
    const f = d.createElement('input');
    f.value = value;
    c.append(p, live, f);
    return c;
  };
  const host = d.createElement('div');
  d.body.append(host);
  w.AW.drawKept(host, [card('wlan0', -60), card('wlan1', -70)]);
  const [c0, c1] = host.children;
  const rs = d.createRange();
  rs.selectNodeContents(c0.querySelector('p'));
  sel.addRange(rs);
  w.AW.drawKept(host, [card('wlan0', -61), card('wlan1', -70)]);
  check('drawKept: a card that holds a field, only its signal moved: the same card, the selection kept, the signal drawn',
    host.children[0] === c0 && !sel.isCollapsed && c0.querySelector('[data-live]').textContent === 'signal -61 dBm', host.innerHTML);
  w.AW.drawKept(host, [card('wlan0', -61), card('wlan1 (now the hotspot)', -70)]);
  check('  a card whose words changed is drawn again, the others kept', host.children[0] === c0 && host.children[1] !== c1
    && /hotspot/.test(host.children[1].textContent) && !sel.isCollapsed);
  w.AW.drawKept(host, [card('wlan0', -61, 'typed'), card('wlan1 (now the hotspot)', -70)]);
  check('  a field\'s value counts', host.children[0] !== c0 && host.children[0].querySelector('input').value === 'typed');
  const foot = card('wlan0', -61);
  foot.append(Object.assign(d.createElement('div'), { className: 'aw-foot' }));
  const before = host.children[1];
  w.AW.drawKept(host, [foot, card('wlan1 (now the hotspot)', -70)]);
  check('  a block with a settings section (its Save) is always drawn', host.children[1] !== before && host.children[0] === foot);
  sel.removeAllRanges();

  // The Network page itself: its inventory drawn, then again with the signal moved, then with a card changed.
  const fx = JSON.parse(fs.readFileSync(`${__dirname}/netinv-fixture.json`, 'utf8'));
  const netData = JSON.parse(kept.get('/admin/network')[1]);
  const draw = (inv) => w.renderNetwork({ ...netData, inventory: inv, pending: 0 });
  const radio = (inv) => inv.radios.find((r) => r.iface === 'wlan0');
  draw(structuredClone(fx));
  const dev = d.querySelector('#net-devices > .net-device');
  const rn = d.createRange();
  rn.selectNodeContents(dev.querySelector('.net-line'));
  sel.addRange(rn);
  const moved = structuredClone(fx);
  radio(moved).link.signal -= 7;
  draw(moved);
  const now = [...dev.querySelectorAll('[data-live="now"]')].map((n) => n.textContent).join(' ');
  check('Network: a poll where only the signal moved keeps the radio\'s card and the selection in it, the signal drawn',
    d.querySelector('#net-devices > .net-device') === dev && !sel.isCollapsed && now.includes(`signal ${radio(moved).link.signal} dBm`), now);
  const other = structuredClone(moved);
  radio(other).power_save = radio(other).power_save === 'on' ? 'off' : 'on';
  draw(other);
  check('  a poll that changes what the card says draws it again', d.querySelector('#net-devices > .net-device') !== dev);
  sel.removeAllRanges();
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
