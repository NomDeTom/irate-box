// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Unsaved changes on /admin, in jsdom (the save-patterns plan, phase 5): a mark on the sidebar entry of each page
// with one, and on its group's head; leaving such a page asks first, and No stays on it; going anyway keeps the
// draft; closing the tab asks the browser's question; Discard clears it all. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-unsaved.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: 'http://box.local/admin/#saved', runScripts: 'outside-only', virtualConsole: new VirtualConsole(), pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.fetch = async () => new Response('{}', { status: 404 });
const asked = [];
let answer = false;
w.confirm = (q) => { asked.push(q); return answer; };
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const go = async (hash) => { w.location.hash = hash; await wait(); };
const pageOf = (id) => d.getElementById(id).closest('.admin-page');
const linkOf = (id) => w.AL.pages().find((p) => p.el === pageOf(id)).link;
const unload = () => { const e = new w.Event('beforeunload', { cancelable: true }); w.dispatchEvent(e); return e.defaultPrevented; };

(async () => {
  await wait(100);
  const store = d.getElementById('store-settings');
  store.section.fill({ store_max_total_mb: 7, store_save_ttl_hours: 24, drop_max_total_mb: 50, drop_ttl_hours: 48 });
  check('nothing changed: no mark, and closing the tab asks nothing', !d.querySelector('.admin-side-list a.unsaved') && !unload());
  const input = store.elements.store_max_total_mb;
  input.value = '12';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  await wait();
  const link = linkOf('store-settings');
  check('a change: its page\'s sidebar entry marked, naming the section', link.classList.contains('unsaved') && /Unsaved changes: Storage limits/.test(link.title), link.title);
  const group = [...d.querySelectorAll('.admin-side-group')].find((g) => { const box = d.getElementById(g.getAttribute('aria-controls')); return box && box.contains(link); });
  check('  and its group\'s head', group && group.classList.contains('unsaved'), group && group.textContent);
  check('  closing the tab asks the browser\'s question', unload());
  const here = w.location.hash;
  answer = false;
  await go('#overview');
  check('leaving the page asks first, naming what is unsaved; No stays on the page', asked.length === 1 && /unsaved changes \(Storage limits\)/.test(asked[0])
    && w.location.hash === here && !pageOf('store-settings').hidden, `${asked.join(' | ')} ${w.location.hash}`);
  answer = true;
  await go('#overview');
  check('  Yes goes, the change kept on its page, still marked', asked.length === 2 && pageOf('store-settings').hidden && !pageOf('overview').hidden
    && input.value === '12' && link.classList.contains('unsaved'));
  await go(here);
  check('back again: no question (the page left has nothing unsaved)', asked.length === 2 && !pageOf('store-settings').hidden);
  store.querySelector('.aw-discard').click();
  await wait();
  check('Discard: the mark gone, and closing the tab asks nothing', !link.classList.contains('unsaved') && !unload() && input.value === '7');
  await go('#overview');
  check('  and leaving asks nothing', asked.length === 2);
  // Rule 7: a lasting choice is a setting, held for Save: the posting rules and the switches have a footer each.
  check('the posting rules and the switches (delete at boot, the factory tile, visitor counts) wait for Save',
    !!d.querySelector('#shout-posting .aw-save') && ['shout_reset_on_boot', 'factory_tile', 'visitor_counts'].every((id) =>
      d.getElementById(id).closest('.settings').nextElementSibling.classList.contains('aw-foot')));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
