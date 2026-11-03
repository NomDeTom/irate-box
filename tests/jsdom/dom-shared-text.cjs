// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The shared text page (web/shared-text.js), in jsdom: an empty box, typing, Save's PUT, and a newer copy on the
// box not written over while the page has unsaved words. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-shared-text.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const WEB = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/notepad.html`, 'utf8');
const js = fs.readFileSync(`${WEB}/shared-text.js`, 'utf8');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms));
const dom = new JSDOM(html, { url: 'http://box.local/notepad.html', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
const calls = [];
let stored = null;
let etag = 'v1';
w.fetch = async (url, opts = {}) => {
  calls.push({ url, method: opts.method || 'GET', headers: opts.headers || {}, body: opts.body });
  if ((opts.method || 'GET') === 'PUT') {
    stored = JSON.parse(opts.body).text;
    etag = 'v2';
    return { ok: true, status: 200, json: async () => ({ ok: true }), headers: { get: () => etag } };
  }
  if (opts.headers && opts.headers['If-None-Match'] === etag) return { ok: true, status: 304, headers: { get: () => etag } };
  return {
    ok: true,
    status: stored === null ? 404 : 200,
    json: async () => ({ text: stored, updated: 1790000000 }),
    headers: { get: () => etag },
  };
};
w.eval(js);
const t = (n) => n.textContent.replace(/\s+/g, ' ').trim();
(async () => {
  await wait();
  check('an empty box: the page says so and waits', /Nothing here yet/.test(t(d.getElementById('shared-state'))), t(d.getElementById('shared-state')));
  const area = d.getElementById('shared-text');
  area.value = 'our words';
  area.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('typing marks the words unsaved and offers Save', /Unsaved/.test(t(d.getElementById('shared-state'))) && !d.getElementById('shared-save').disabled);
  d.getElementById('shared-save').click();
  await wait();
  check('Save puts the words at the namespace\'s one key', calls.some((c) => c.method === 'PUT' && c.url === '/api/v2/notepad/text' && JSON.parse(c.body).text === 'our words'));
  check('  and the page says they are saved', /Saved|Last saved/.test(t(d.getElementById('shared-state'))), t(d.getElementById('shared-state')));
  stored = 'someone else';
  area.value = 'more of ours';
  area.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('  unsaved words are not read over by the poll', /Unsaved/.test(t(d.getElementById('shared-state'))), t(d.getElementById('shared-state')));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
