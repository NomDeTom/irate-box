// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hub bar's ↑ (app.js): an app opened from a list page offers the way back to that list;
// one opened from a tile does not. Against a running hub (its list pages and /menus.json).
// Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-appbar.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const BASE = process.env.BASE;
let fails = 0;
const check = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'} ${n}${c ? '' : '  ' + i}`); fails += !c; };
async function open(path, storage = {}) {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(BASE + path, { runScripts: 'dangerously', resources: 'usable', virtualConsole: vc, pretendToBeVisual: true,
    beforeParse(w) { for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v); w.fetch = (u, o) => fetch(new URL(u, BASE + path), o); w.EventSource = class { close() {} }; } });
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
  // A web add-on is told the theme's base in its address (app.js frameSrc).
  d = await open('/app.html#/addons/eliza/eliza.html?script=ELIZA-script-Turing-example1', { theme: 'dark' });
  let src = d.getElementById('app').getAttribute('src');
  check('a web add-on gets hub-theme, its own query kept', src === '/addons/eliza/eliza.html?script=ELIZA-script-Turing-example1&hub-theme=dark', src);
  d.defaultView.HubThemes.choose('eink');
  await new Promise((r) => setTimeout(r, 100));
  src = d.getElementById('app').getAttribute('src');
  check('a theme change reloads it with the new base', src.endsWith('&hub-theme=light'), src);
  d = await open('/app.html#/addons/eliza/', {});
  check('Auto is passed as auto', d.getElementById('app').getAttribute('src') === '/addons/eliza/?hub-theme=auto', d.getElementById('app').getAttribute('src'));
  d = await open('/app.html#/tools/', { theme: 'dark' });
  check('an app on the hub\'s origin gets no hub-theme', d.getElementById('app').getAttribute('src') === '/tools/', d.getElementById('app').getAttribute('src'));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
