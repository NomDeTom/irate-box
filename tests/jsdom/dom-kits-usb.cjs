// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A toolkit import from a USB drive that the clock cannot date: the refusal offers an import without a
// freshness check, behind a confirmation, and only for that refusal. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-kits-usb.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: 'http://box/admin/#toolkits', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
require("./answer-asks.cjs")(w); // its questions on the page answered by its confirm stub
const posted = [];
let results = [];
let holdResults = false, failPost = false;
const scan = { devices: [{ name: 'sda1', label: 'Stick', fstype: 'vfat', size: 8e9, error: null,
  kits: [{ title: 'Debugging', packages: 55, bytes: 69 * 2 ** 20, arch: 'armhf', kit: 'debug' }], zims: [] }] };
w.fetch = async (url, opts = {}) => {
  const u = String(url);
  if (opts.method === 'POST' && /\/admin\/usb$/.test(u)) {
    posted.push(JSON.parse(opts.body));
    return failPost ? new Response('{}', { status: 503 }) : new Response(JSON.stringify({ id: `r${posted.length}` }), { status: 202 });
  }
  if (/\/admin\/usb$/.test(u)) return new Response(JSON.stringify({ results: holdResults ? [] : results, pending: 0, scan }), { status: 200 });
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
  await settle();
  // A poll with the same drives leaves the row alone: a selection in it stays; a changed label draws it again.
  const sel = w.getSelection();
  const labelNode = d.querySelector('#kits-usb .setting-name').firstChild;
  const pick = d.createRange();
  pick.selectNodeContents(labelNode);
  sel.removeAllRanges();
  sel.addRange(pick);
  await w.eval('loadKitsUsb()');
  await settle();
  check('a poll that brings nothing new leaves the kit USB row (and a selection in it)', labelNode.isConnected && !sel.isCollapsed);
  scan.devices[0].label = 'Stick 2';
  await w.eval('loadKitsUsb()');
  await settle();
  check('  changed data draws the row again', !labelNode.isConnected && /Stick 2/.test(d.getElementById('kits-usb').textContent));
  sel.removeAllRanges();
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
  // A USB toolkit action keeps its wait across the poll that redraws the drive.
  holdResults = true;
  const importButton = () => [...d.querySelectorAll('#kits-usb button')].find((b) => b.textContent === 'Import toolkit');
  const ikey = importButton() && importButton().dataset.wait;
  importButton().click();
  await settle();
  const ifresh = () => [...d.querySelectorAll('[data-wait]')].find((n) => n.dataset.wait === ikey);
  check('a USB toolkit action: its button waits while the helper has it', ikey && ifresh() && ifresh().classList.contains('is-busy')
    && ifresh().getAttribute('aria-busy') === 'true', ikey);
  holdResults = false;
  results = [{ id: `r${posted.length}`, ok: true, message: 'Done.' }];
  await w.eval('loadKitsUsb()');
  await settle();
  check('  and the wait ends with the answer', ifresh() && !ifresh().classList.contains('is-busy') && !ifresh().hasAttribute('aria-busy'));
  failPost = true;
  importButton().click();
  await settle();
  check('  a refused request clears it too', ifresh() && !ifresh().classList.contains('is-busy') && !ifresh().hasAttribute('aria-busy'));
  failPost = false;
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
