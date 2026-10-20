// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// hub-return.js on an app's own origin (Excalidraw, Mermaid): the hub's theme comes in its address (hub-theme=…, put there by
// the hub's app bar) and is written to the app's own storage, where the app reads it. Usage: [JSDOM=…/jsdom] node dom-hub-return.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const js = fs.readFileSync(path.resolve(__dirname, '../../web/hub-return.js'), 'utf8');
let fails = 0;
const check = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'} ${n}${c ? '' : '  ' + i}`); fails += !c; };
const run = (url, stored = {}) => {
  const dom = new JSDOM('<!doctype html><body></body>', { url, runScripts: 'outside-only' });
  for (const [k, v] of Object.entries(stored)) dom.window.localStorage.setItem(k, v);
  dom.window.eval(js);
  const ls = dom.window.localStorage;
  return { draw: ls.getItem('excalidraw-theme'), mermaid: ls.getItem('mode-watcher-mode'), pill: !!dom.window.document.getElementById('hub-return') };
};
let got = run('http://box:8096/draw/?hub-theme=dark');
check('dark: both apps\' keys set', got.draw === 'dark' && got.mermaid === 'dark', JSON.stringify(got));
got = run('http://box:8096/draw/?x=1&hub-theme=light');
check('light, among other query parts', got.draw === 'light' && got.mermaid === 'light', JSON.stringify(got));
got = run('http://box:8097/mermaid/?hub-theme=auto');
check('auto is system, as the apps spell it', got.draw === 'system' && got.mermaid === 'system', JSON.stringify(got));
got = run('http://box:8096/draw/?hub-theme=evil', { 'excalidraw-theme': 'light' });
check('anything else is ignored: the app\'s own choice stands', got.draw === 'light' && got.mermaid === null, JSON.stringify(got));
got = run('http://box:8096/draw/');
check('no theme in the address: nothing written, the link back still drawn', got.draw === null && got.mermaid === null && got.pill, JSON.stringify(got));
// Mermaid reads its theme before its build adds this script: a change is announced as a storage event, as another
// tab's write would be, so it follows at once (on the Lyra it took the new theme only at the next load).
{
  const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://box:8097/mermaid/?hub-theme=light', runScripts: 'outside-only' });
  dom.window.localStorage.setItem('mode-watcher-mode', 'dark');
  dom.window.localStorage.setItem('excalidraw-theme', 'light');
  const heard = [];
  dom.window.addEventListener('storage', (e) => heard.push(`${e.key}:${e.oldValue}>${e.newValue}`));
  dom.window.eval(js);
  check('a theme changed: announced to the app at once, only for the key that changed', heard.join() === 'mode-watcher-mode:dark>light', heard.join());
}
console.log(fails ? `${fails} failure(s)` : 'ok');
process.exit(fails ? 1 : 0);
