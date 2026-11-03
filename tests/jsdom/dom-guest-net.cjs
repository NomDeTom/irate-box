// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The public guest-net button (web/guest-net.js) in jsdom: its wait while the request is in flight, and
// the button gone when the hub accepts it. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-guest-net.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const dom = new JSDOM('<!doctype html><body><div id="guest-net"></div></body>', { runScripts: 'outside-only', url: 'http://box/' });
const w = dom.window, d = w.document;
let release;
w.fetch = async (url, opts = {}) => {
  if ((opts.method || 'GET').toUpperCase() === 'POST') {
    return new Promise((resolve) => { release = () => resolve(new Response(JSON.stringify({ out: true }), { status: 202 })); });
  }
  return new Response(JSON.stringify({ here: true, out: false, level: 'sheet-all', hours: 4, signed_in: true }), { status: 200 });
};
w.eval(fs.readFileSync(path.resolve(__dirname, '../../web/guest-net.js'), 'utf8'));
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait();
  const button = () => d.querySelector('#guest-net button');
  check('the offer is drawn', !!button() && button().textContent === 'Enable internet access');
  button().click();
  await wait();
  check('clicked: disabled with the wait', button() && button().disabled && button().classList.contains('is-busy')
    && button().getAttribute('aria-busy') === 'true', button() && button().outerHTML);
  release();
  await wait();
  check('accepted: the button goes, the paragraph says done', !button() && /Done\. This device can reach the internet/.test(d.getElementById('guest-net').textContent));
  // A refused POST: the button comes back, no ring, and the paragraph says to try again.
  const dom2 = new JSDOM('<!doctype html><body><div id="guest-net"></div></body>', { runScripts: 'outside-only', url: 'http://box/' });
  const w2 = dom2.window, d2 = w2.document;
  w2.fetch = async (url, opts = {}) => ((opts.method || 'GET').toUpperCase() === 'POST'
    ? new Response(JSON.stringify({ error: 'No room on the hotspot.' }), { status: 503 })
    : new Response(JSON.stringify({ here: true, out: false, level: 'sheet-all', hours: 4, signed_in: true }), { status: 200 }));
  w2.eval(fs.readFileSync(path.resolve(__dirname, '../../web/guest-net.js'), 'utf8'));
  await wait();
  const failedButton = () => d2.querySelector('#guest-net button');
  failedButton().click();
  await wait();
  check('refused: the button is enabled again, without the wait', failedButton() && !failedButton().disabled
    && !failedButton().classList.contains('is-busy') && !failedButton().hasAttribute('aria-busy'),
  failedButton() && failedButton().outerHTML);
  check('  and the paragraph says to try again', /Request failed: No room on the hotspot\.+ Select the button to try again\./.test(d2.getElementById('guest-net').textContent),
    d2.getElementById('guest-net').textContent);
  console.log(fails ? `failures: ${fails}` : 'all passed');
  process.exit(fails ? 1 : 0);
})();
