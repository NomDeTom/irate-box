// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Storage page's saved work and the moderation lists, with no browser pop-up: Rename is a text field in place
// (its Rename or Enter sends it, Cancel or Escape leaves the name), and Clear and Delete ask on the page.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-store.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: 'http://box/admin/#storage', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window;
const posted = [];
const popups = [];
const store = {
  usage: { total: 2048, max_total: 2 ** 20, namespaces: { scenes: { files: 1, bytes: 2048 }, rooms: { files: 0, bytes: 0 }, files: { files: 0, bytes: 0 } } },
  saves: [{ id: 'pic1', name: 'my drawing', kind: 'excalidraw', size: 2048, created: 0 }],
  now: 60,
};
const moderation = { now: 60, messages: [{ name: 'Sam', created: 1, text: 'hello' }], drops: [], board: { threads: [] }, queue: [],
  reports: { reasons: ['spam'], all_reasons: ['spam'], threshold: 3, hide: false } };
w.fetch = async (url, opts = {}) => {
  const u = String(url);
  if (opts.method === 'POST') {
    posted.push({ url: u, body: JSON.parse(opts.body) });
    return new Response(JSON.stringify(/moderation/.test(u) ? moderation : store), { status: 200 });
  }
  if (/\/admin\/store$/.test(u)) return new Response(JSON.stringify(store), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.scrollTo = () => {};
w.prompt = (m) => { popups.push(`prompt: ${m}`); return null; };
w.confirm = (m) => { popups.push(`confirm: ${m}`); return false; };
w.alert = (m) => popups.push(`alert: ${m}`);
const errors = [];
w.addEventListener('unhandledrejection', (e) => errors.push(String(e.reason)));
w.addEventListener('error', (e) => errors.push(String(e.message)));
let fails = 0;
const check = (name, ok, extra) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok || extra === undefined ? '' : ` ${JSON.stringify(extra)}`}`); if (!ok) fails++; };
w.eval(js);
const d = w.document;
const settle = () => new Promise((r) => setTimeout(r, 30));
const button = (root, label) => [...root.querySelectorAll('button')].find((b) => b.textContent === label && !b.hidden);
const saves = () => d.getElementById('store-saves');

(async () => {
  await w.eval('loadStore()');
  await settle();
  button(saves(), 'Rename').click();
  const field = saves().querySelector('input');
  check('Rename: a text field in place, holding the name; the row\'s Rename hidden meanwhile', !!field && field.value === 'my drawing' && !saves().querySelector('strong')
    && [...saves().querySelectorAll('button')].filter((b) => b.textContent === 'Rename' && !b.hidden).length === 1);
  field.value = 'my sketch';
  field.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape' }));
  check('  Escape: the name again, nothing sent', !saves().querySelector('input') && saves().querySelector('strong').textContent === 'my drawing' && !posted.length);
  button(saves(), 'Rename').click();
  button(saves(), 'Cancel').click();
  check('  Cancel: the same, and the row\'s Rename back', !saves().querySelector('input') && !posted.length && !!button(saves(), 'Rename'));
  button(saves(), 'Rename').click();
  saves().querySelector('input').value = '  my sketch ';
  button(saves(), 'Rename').click();
  await settle();
  check('  its Rename: the new name sent, trimmed', JSON.stringify(posted[0] && posted[0].body) === JSON.stringify({ action: 'rename', id: 'pic1', name: 'my sketch' }), posted);
  button(saves(), 'Rename').click();
  saves().querySelector('input').value = 'by enter';
  saves().querySelector('input').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter' }));
  await settle();
  check('  Enter: sent too', posted.length === 2 && posted[1].body.name === 'by enter', posted);

  posted.length = 0;
  const clear = button(d.getElementById('store-clear'), 'Clear drawings') || d.querySelector('#store-clear button:not(:disabled)');
  clear.click();
  await settle();
  const ask = d.querySelector('.aw-ask');
  check('Clear asks on the page', !!ask && /This cannot be undone/.test(ask.textContent), ask && ask.textContent);
  button(ask, 'Confirm').click();
  await settle();
  check('  Confirm: the clear sent', posted.length === 1 && posted[0].body.action === 'clear', posted);

  posted.length = 0;
  w.eval(`renderModeration(${JSON.stringify(moderation)})`);
  button(d.getElementById('mod-messages'), 'Delete message').click();
  await settle();
  const modAsk = d.querySelector('.aw-ask');
  check('Delete message asks on the page, naming the message', !!modAsk && /Delete the message from Sam\?/.test(modAsk.textContent), modAsk && modAsk.textContent);
  button(modAsk, 'Cancel').click();
  await settle();
  check('  Cancel: nothing sent', !posted.length && !d.querySelector('.aw-ask'), posted);

  check('no browser pop-up, no page error', !popups.length && !errors.length, popups.concat(errors));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
