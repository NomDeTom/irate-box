// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The theme manager (web/themes.js, the picker in hub.js) on the real pages of a running hub:
// Light, Dark and Auto, the 🎨 menu of other themes, what is saved, the first paint after a
// reload, the move of an old "cybercore" value, a list page, app.html and the Themes page.
// Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-themes.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const BASE = process.env.BASE;
let fails = 0; const check = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'} ${n}${c ? '' : '  ' + i}`); fails += !c; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function open(path, storage = {}) {
  const errors = [];
  const vc = new VirtualConsole(); vc.on('jsdomError', (e) => { if (!/scrollTo|Could not load link|navigation/.test(e.message)) errors.push(e.message); });
  const dom = await JSDOM.fromURL(BASE + path, { runScripts: 'dangerously', resources: 'usable', virtualConsole: vc, pretendToBeVisual: true,
    beforeParse(w) { for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v); w.fetch = (u, o) => fetch(new URL(u, BASE), o); w.EventSource = class { close() {} }; } });
  await new Promise((r) => dom.window.addEventListener('load', r)); await sleep(300);
  return { w: dom.window, d: dom.window.document, errors };
}
(async () => {
  let { w, d, errors } = await open('/');
  const p = d.querySelector('header .theme-picker');
  const labels = [...p.querySelectorAll(':scope > button')].map((b) => b.textContent);
  check('default: Light, Dark, Auto and 🎨', labels.join(' ') === '☀️ 🌙 Auto 🎨', labels.join(' '));
  check('default: no data-theme (follows the OS)', !d.documentElement.hasAttribute('data-theme'));
  check('default: Auto pressed', p.querySelector('[data-theme-choice="auto"]').getAttribute('aria-pressed') === 'true');
  const more = p.querySelector('.theme-more'), menu = p.querySelector('.theme-menu');
  check('menu closed at first', menu.hidden);
  more.click();
  check('🎨 opens the menu', !menu.hidden && more.getAttribute('aria-expanded') === 'true');
  const items = [...menu.querySelectorAll('button')].map((b) => b.querySelector('.theme-menu-name').textContent);
  check('menu lists E-ink and Cybercore', items.join('|') === '📖 E-ink|🏴‍☠️ Cybercore', items.join('|'));
  let announced = null; d.addEventListener('hub-theme', (e) => { announced = e.detail; });
  menu.querySelector('[data-theme-choice="eink"]').click(); await sleep(100);
  check('E-ink: data-theme', d.documentElement.dataset.theme === 'eink');
  check('E-ink: its stylesheet', d.getElementById('hub-theme-css') && d.getElementById('hub-theme-css').getAttribute('href') === '/themes/eink.css');
  check('E-ink: saved as base light + hub-theme', w.localStorage.getItem('theme') === 'light' && w.localStorage.getItem('hub-theme') === 'eink');
  check('E-ink: 🎨 shows 📖, pressed; menu closed', more.textContent === '📖' && more.getAttribute('aria-pressed') === 'true' && menu.hidden);
  check('E-ink: announced to the apps', announced === 'eink' && w.HubThemes.base() === 'light');
  p.querySelector('[data-theme-choice="dark"]').click(); await sleep(50);
  check('Dark: stylesheet gone, hub-theme cleared', !d.getElementById('hub-theme-css') && w.localStorage.getItem('hub-theme') === null && w.localStorage.getItem('theme') === 'dark' && d.documentElement.dataset.theme === 'dark');
  check('Dark: 🎨 back to 🎨', more.textContent === '🎨' && more.getAttribute('aria-pressed') === 'false');
  p.querySelector('[data-theme-choice="auto"]').click(); await sleep(50);
  check('Auto: nothing saved, no data-theme', w.localStorage.getItem('theme') === null && !d.documentElement.hasAttribute('data-theme'));
  check('no page errors', errors.length === 0, errors.join(' | '));
  ({ w, d, errors } = await open('/', { theme: 'light', 'hub-theme': 'eink' }));
  const link = d.getElementById('hub-theme-css');
  check('reload in E-ink: stylesheet in <head> from the first paint', link && link.parentNode === d.head && d.documentElement.dataset.theme === 'eink');
  ({ w, d } = await open('/', { theme: 'cybercore' }));
  check('an old saved "cybercore" moves to hub-theme, base dark', w.localStorage.getItem('hub-theme') === 'cybercore' && w.localStorage.getItem('theme') === 'dark' && d.documentElement.dataset.theme === 'cybercore');
  ({ w, d } = await open('/', { 'hub-theme': 'nonsense', theme: 'purple' }));
  check('unknown saved values: Auto', !d.documentElement.hasAttribute('data-theme'));
  ({ w, d } = await open('/tools-rf.html', { theme: 'light', 'hub-theme': 'cybercore' }));
  check('a list page (server-rendered) gets the picker and the theme', d.querySelector('.theme-picker .theme-more') && d.documentElement.dataset.theme === 'cybercore' && d.getElementById('hub-theme-css'));
  ({ w, d } = await open('/app.html#/tools/', { theme: 'dark', 'hub-theme': 'cybercore' }));
  check('app.html: apps told the base (dark)', w.localStorage.getItem('excalidraw-theme') === 'dark' && w.localStorage.getItem('nomdetom-theme-mode') === 'dark', w.localStorage.getItem('excalidraw-theme'));
  check('app.html: picker in the bar', !!d.querySelector('.theme-picker .theme-more'));
  ({ w, d, errors } = await open('/themes.html'));
  const rows = [...d.querySelectorAll('#theme-list .theme-choice')];
  check('Themes page: Auto and every theme, one line each', rows.map((b) => b.dataset.theme).join(' ') === 'auto ' + w.HubThemes.list.map((t) => t.id).join(' '),
    rows.map((b) => b.dataset.theme).join(' '));
  check('Themes page: Auto checked at first', d.querySelector('[data-theme="auto"]').getAttribute('aria-checked') === 'true');
  d.querySelector('#theme-list [data-theme="eink"]').click(); await sleep(50);
  check('Themes page: choosing E-ink draws the page in it', d.documentElement.dataset.theme === 'eink'
    && d.querySelector('#theme-list [data-theme="eink"]').getAttribute('aria-checked') === 'true'
    && d.querySelector('header .theme-more').textContent === '📖');
  d.querySelector('header .theme-picker [data-theme-choice="dark"]').click(); await sleep(50);
  check('Themes page: follows the header picker', d.querySelector('#theme-list [data-theme="dark"]').getAttribute('aria-checked') === 'true');
  check('Themes page: no errors', errors.length === 0, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok'); process.exit(fails ? 1 : 0);
})();
