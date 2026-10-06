// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hub bar's ↑ (app.js): an app opened from a list page offers the way back to that list;
// one opened from a tile does not. Against a running hub (its list pages and /menus.json).
// Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-appbar.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const BASE = process.env.BASE;
let fails = 0;
const check = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'} ${n}${c ? '' : '  ' + i}`); fails += !c; };
async function open(path) {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(BASE + path, { runScripts: 'dangerously', resources: 'usable', virtualConsole: vc, pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE + path), o); w.EventSource = class { close() {} }; } });
  await new Promise((r) => setTimeout(r, 900));
  return dom.window.document;
}
(async () => {
  let d = await open('/tools-rf.html');
  const links = [...d.querySelectorAll('.item-list a')].map((a) => a.getAttribute('href'));
  check('a list page links its apps with ?from=<list>', links.length && links.filter((h) => h.startsWith('/app.html')).every((h) => h.startsWith('/app.html?from=tools-rf#/')), links.slice(0, 3).join(' '));
  check('a list page says 🏠 Hub', /🏠\s*Hub/.test(d.querySelector('.head-nav').textContent));
  d = await open('/app.html?from=tools-rf#/tools/');
  const up = d.getElementById('app-up');
  check('opened from a list: ↑ to it, with its title', !up.hidden && up.textContent === '↑ RF & LoRa' && up.getAttribute('href') === '/tools-rf.html', `${up.hidden} ${up.textContent} ${up.getAttribute('href')}`);
  d = await open('/app.html#/tools/');
  check('opened from a tile: no ↑', d.getElementById('app-up').hidden);
  d = await open('/app.html?from=no-such-list#/tools/');
  check('an unknown list: no ↑', d.getElementById('app-up').hidden);
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
