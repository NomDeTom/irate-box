// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The theme picker and the Themes page: light, dark and auto as the mode row; the 🎨 menu with the box's preset (the
// default, the colours chosen on Appearance), Standard, Low contrast, High contrast, E-ink and Cybercore, one of them checked;
// each choice saved in this browser and drawn at once (data-palette for a palette, data-theme for a theme), a palette
// leaving a theme of its own colours for the main theme. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-theme-picker.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
function page(file, scripts) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/Not implemented|scrollTo/.test(e.message)) errors.push(e.message); });
  const dom = new JSDOM(fs.readFileSync(`${WEB}/${file}`, 'utf8'), { url: 'http://box.test/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = async () => new Response('{}', { status: 404 });
  for (const s of scripts) w.eval(fs.readFileSync(`${WEB}/${s}`, 'utf8'));
  return { w, d: w.document, errors };
}

const { w, d, errors } = page('index.html', ['themes.js', 'hub.js']);
const root = d.documentElement;
const items = () => [...d.querySelectorAll('.theme-menu [role="menuitemradio"]')];
const checked = () => items().filter((b) => b.getAttribute('aria-checked') === 'true').map((b) => b.querySelector('.theme-menu-name').textContent);
const more = d.querySelector('.theme-more');
check('the menu: the box\'s preset, Standard, Low contrast, High contrast, then E-ink and Cybercore',
  items().map((b) => b.querySelector('.theme-menu-name').textContent).join('|') === '🏠 Box palette|🎨 Standard palette|🌫️ Low contrast|🔆 High contrast|📖 E-ink|🏴‍☠️ Cybercore',
  items().map((b) => b.textContent));
check('  by default the box\'s preset, checked, and no palette of the visitor\'s on the page', checked().join() === '🏠 Box palette' && !root.hasAttribute('data-palette')
  && /box palette/.test(more.title), [checked(), more.title]);
const pick = (name) => items().find((b) => b.textContent.includes(name)).click();
d.querySelector('[data-theme-choice="dark"]').click();
pick('Low contrast');
check('Low contrast: saved, drawn and checked', root.getAttribute('data-palette') === 'low' && w.localStorage.getItem('hub-palette') === 'low'
  && checked().join() === '🌫️ Low contrast', checked());
pick('High contrast');
check('High contrast: saved in this browser, drawn at once, checked; the mode kept', root.getAttribute('data-palette') === 'contrast'
  && w.localStorage.getItem('hub-palette') === 'contrast' && checked().join() === '🔆 High contrast' && root.getAttribute('data-theme') === 'dark'
  && more.textContent === '🔆' && more.getAttribute('aria-pressed') === 'true', [root.outerHTML.slice(0, 80), checked()]);
pick('E-ink');
check('E-ink: a theme of its own colours; no palette checked while it is in use', root.getAttribute('data-theme') === 'eink' && checked().join() === '📖 E-ink'
  && more.textContent === '📖', checked());
pick('Standard palette');
check('Standard from E-ink: the main theme again (E-ink\'s base, light), with the hub\'s own colours', root.getAttribute('data-theme') === 'light'
  && root.getAttribute('data-palette') === 'standard' && !w.localStorage.getItem('hub-theme') && checked().join() === '🎨 Standard palette', [root.getAttribute('data-theme'), checked()]);
pick('Box palette');
check('the box\'s preset again: nothing of the visitor\'s saved, the box\'s colours apply', !root.hasAttribute('data-palette')
  && w.localStorage.getItem('hub-palette') === null && checked().join() === '🏠 Box palette' && more.textContent === '🎨');
const pressed = [...d.querySelectorAll('.theme-picker > button[data-theme-choice]')].map((b) => `${b.dataset.themeChoice}:${b.getAttribute('aria-pressed')}`).join(',');
check('the mode row: the one chosen pressed, the others not', pressed === 'light:true,dark:false,auto:false', pressed);
w.localStorage.setItem('hub-palette', 'nonsense');
check('a palette not known: the box\'s', w.HubThemes.palette() === 'box');
check('no errors on the page', !errors.length, errors);

// The Themes page: the modes and own-colour themes, then the colours, both lists marked.
const t = page('themes.html', ['themes.js', 'themes-page.js', 'hub.js']);
const rows = (id) => [...t.d.querySelectorAll(`#${id} .theme-choice`)];
check('the Themes page: the colours of light and dark, a list of their own', rows('palette-list').map((b) => b.dataset.palette).join() === 'box,standard,low,contrast'
  && rows('palette-list')[0].getAttribute('aria-checked') === 'true');
rows('palette-list')[3].click();
check('  High contrast chosen there: drawn and marked, in the picker too', t.d.documentElement.getAttribute('data-palette') === 'contrast'
  && rows('palette-list')[3].getAttribute('aria-checked') === 'true'
  && t.d.querySelector('.theme-menu [data-palette-choice="contrast"]').getAttribute('aria-checked') === 'true');
rows('theme-list').find((b) => b.dataset.theme === 'cybercore').click();
check('  Cybercore chosen: no colours row marked', rows('palette-list').every((b) => b.getAttribute('aria-checked') === 'false'));
check('  no errors on the page', !t.errors.length, t.errors);

console.log(fails ? `${fails} failure(s)` : 'ok');
process.exit(fails ? 1 : 0);
