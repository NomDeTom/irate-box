// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hub's add-on bridge (web/addon-bridge.js), in jsdom: it takes the add-on origin from the hub,
// never from a Referer (there is none here), ignores any other origin, names the add-on that asked,
// and does nothing until the visitor picks. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-addon-bridge.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const WEB = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/addon-bridge.html`, 'utf8');
const js = ['admin-widgets.js', 'addon-bridge.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms));
const ADDON = 'http://addon.local:8090';
// No referrer at all, as on a real box: the add-on server sends none.
const dom = new JSDOM(html, { url: 'http://box.local/addon-bridge.html', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
const sent = [];
let fetches = [];
w.opener = { postMessage: (message, origin) => sent.push({ message, origin }) };
w.fetch = async (url, opts = {}) => {
  fetches.push({ url, method: opts.method || 'GET' });
  if (url === '/api/addons/origin') return { ok: true, status: 200, json: async () => ({ origin: ADDON }) };
  if (url === '/api/saves' && (opts.method || 'GET') === 'GET') {
    return { ok: true, status: 200, json: async () => ({ saves: [] }) };
  }
  return { ok: true, status: 201, json: async () => ({ id: 'save1', name: 'A picture', locked: true, lock_n: 4096 }) };
};
w.eval(js);
const message = (data, origin = ADDON) => w.dispatchEvent(new w.MessageEvent('message', { data, origin, source: w.opener }));
(async () => {
  await wait();
  check(
    'with no Referer, it still learns the add-on origin from the hub and says it is ready',
    sent.some((s) => s.message.type === 'irate-bridge-ready' && s.origin === ADDON),
    sent,
  );
  message({ type: 'irate-bridge-request', action: 'save', payload: { title: 'JS Paint', name: 'A picture', state: 'data:image/png;base64,x' } }, 'http://evil.local');
  await wait();
  check('a message from another origin is ignored', fetches.filter((f) => f.method === 'POST').length === 0 && !d.querySelector('#addon-bridge-body button'), fetches);
  message({ type: 'irate-bridge-request', action: 'save', payload: { title: 'JS Paint', name: 'A picture', state: 'data:image/png;base64,x' } });
  await wait();
  check(
    'the add-on asking is named, in words, and nothing is saved by itself',
    /JS Paint/.test(d.getElementById('addon-bridge-body').textContent) && fetches.filter((f) => f.method === 'POST').length === 0,
    { text: d.getElementById('addon-bridge-body').textContent, fetches },
  );
  d.querySelector('#addon-bridge-body button').click();
  await wait();
  check(
    '  the visitor\'s Save button does, and the answer goes only to the add-on',
    fetches.some((f) => f.url === '/api/saves' && f.method === 'POST')
      && sent.some((s) => s.message.type === 'irate-bridge-result' && s.message.action === 'save' && s.message.ok && s.origin === ADDON),
    { fetches, sent },
  );
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
