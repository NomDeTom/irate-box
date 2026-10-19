// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A toolkit import from a USB drive that the clock cannot date (audit M10): the refusal offers an import without a
// freshness check, behind a confirmation, and only for that refusal. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-kits-usb.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: 'http://box/admin/#toolkits', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const posted = [];
let results = [];
w.fetch = async (url, opts = {}) => {
  const u = String(url);
  if (opts.method === 'POST' && /\/admin\/usb$/.test(u)) {
    posted.push(JSON.parse(opts.body));
    return new Response(JSON.stringify({ id: `r${posted.length}` }), { status: 202 });
  }
  if (/\/admin\/usb$/.test(u)) return new Response(JSON.stringify({ results, pending: 0, scan: null }), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.scrollTo = () => {};
const asked = [];
w.confirm = (text) => { asked.push(text); return true; };
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
w.eval(js);
const d = w.document;
const note = () => d.getElementById('kits-usb-note');
const offer = () => [...note().querySelectorAll('button')].find((b) => /without a freshness check/.test(b.textContent));
const settle = () => new Promise((r) => setTimeout(r, 30));
const untrusted = { id: 'kits.usb.time-untrusted', en: 'The box cannot check that {file} is still current.', args: { file: 'x_InRelease' } };

(async () => {
  await w.eval(`kitsUsbAsk({ action: 'kit-import', device: 'sda1', kit: 'small' })`);
  results = [{ id: 'r1', ok: false, msg: untrusted }];
  await w.eval('loadKitsUsb()');
  await settle();
  check('refused for the clock: said, and an import without a freshness check offered', /still current/.test(note().textContent) && !!offer(), note().textContent);
  asked.length = 0;
  offer().click();
  await settle();
  check('  it asks first, saying the signatures still count and what is not checked', asked.length === 1 && /signatures and hashes/.test(asked[0])
    && /older signed set/.test(asked[0]), asked);
  check('  accepted: the same import again, with the approval', JSON.stringify(posted[1]) === JSON.stringify({ action: 'kit-import', device: 'sda1', kit: 'small', freshness: 'unknown-time' }), posted);
  w.confirm = (text) => { asked.push(text); return false; };
  await w.eval(`kitsUsbAsk({ action: 'kit-import', device: 'sda1', kit: 'small' })`);
  results = [{ id: 'r2', ok: false, msg: untrusted }];
  await w.eval('loadKitsUsb()');
  await settle();
  const before = posted.length;
  offer() && offer().click();
  await settle();
  check('  declined: nothing sent', posted.length === before, posted);
  w.confirm = () => true;
  await w.eval(`kitsUsbAsk({ action: 'kit-import', device: 'sda1', kit: 'small' })`);
  results = [{ id: `r${posted.length}`, ok: false, msg: { id: 'kits.usb.signature', en: 'Signature invalid for {file}.', args: { file: 'x' } } }];
  await w.eval('loadKitsUsb()');
  await settle();
  check('another refusal (a bad signature): no such offer', /Signature invalid/.test(note().textContent) && !offer(), note().textContent);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
