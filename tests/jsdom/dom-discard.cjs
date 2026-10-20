// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The plain forms of /admin as sections (AW.form), in jsdom: each has the one footer, Discard then Save, off until its
// user changes something; until the box's values are read it says so and its fields are off; Discard puts back the
// saved values, chips too, and sends nothing. No hub: nothing is read until the checks give the values.
// Usage: [JSDOM=…/jsdom] node dom-discard.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const dom = new JSDOM(html, { url: 'http://box.local/admin/#overview', runScripts: 'outside-only', virtualConsole: new VirtualConsole(), pretendToBeVisual: true });
const w = dom.window, d = w.document;
let sent = 0;
w.fetch = async (u, opts = {}) => { if (opts.method === 'POST') sent++; return new Response('{}', { status: 404 }); };
w.eval(js);
const FORMS = ['fw-form', 'fw-policy', 'mirrors-policy', 'kits-policy', 'kits-budget', 'library-policy', 'library-token', 'store-settings',
  'ci-cache-form', 'ci-keep-form', 'accounts-settings', 'auto-form', 'update-signing-form', 'debian-pattern'];
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
setTimeout(() => {
  const forms = FORMS.map((id) => d.getElementById(id));
  const odd = forms.filter((f) => {
    const b = [...f.querySelectorAll('button')].filter((x) => /^(Save|Discard)/.test(t(x)));
    return !f.section || b.length !== 2 || t(b[0]) !== 'Discard' || t(b[1]) !== 'Save' || b[1].type === 'submit' || b[0].parentElement !== f.section.foot;
  }).map((f) => f.id);
  check('every plain form is a section: one footer, Discard then Save, no other Save', !odd.length, odd.join());
  const unread = forms.filter((f) => f.id !== 'library-token');
  check('  not read yet (no hub): each says so, Save and Discard off', unread.every((f) => t(f.section.foot.querySelector('.note')) === 'Reading the current settings…'
    && f.section.foot.querySelector('.aw-save').disabled && f.section.foot.querySelector('.aw-discard').disabled),
  unread.filter((f) => t(f.section.foot.querySelector('.note')) !== 'Reading the current settings…').map((f) => f.id).join());
  check('  and its fields off, so nothing looks set', unread.every((f) => [...f.elements].filter((e) => e.name).every((e) => e.disabled)));
  check('  nothing counted as unsaved', !w.AW.unsaved().length);

  // The values read: a plain field changed, then discarded.
  const store = d.getElementById('store-settings');
  store.section.fill({ store_max_total_mb: 7, store_save_ttl_hours: 24, drop_max_total_mb: 50, drop_ttl_hours: 48 });
  const input = store.elements.store_max_total_mb, discard = store.querySelector('.aw-discard');
  check('read: the fields on and filled', !input.disabled && input.value === '7');
  input.value = '12';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('a field changed: Discard on, and the section counted unsaved', !discard.disabled && w.AW.unsaved().some((x) => x.id === 'store-settings'));
  store.section.fill({ store_max_total_mb: 7, store_save_ttl_hours: 24, drop_max_total_mb: 50, drop_ttl_hours: 48 });
  check('  the poll leaves it', input.value === '12');
  discard.click();
  check('  Discard puts back what was saved, and is off again; nothing sent', input.value === '7' && discard.disabled && sent === 0, input.value);

  // Chips over a select: the chip clicked, then discarded; the chips follow.
  const pol = d.getElementById('kits-policy');
  const sel = pol.querySelector('select');
  pol.section.fill(Object.fromEntries([...pol.querySelectorAll('select[name]')].map((s) => [s.name, s.options[0].value])));
  const chips = () => [...sel._chips.querySelectorAll('.chip')];
  const before = sel.value;
  const other = chips().find((c) => !c.classList.contains('selected'));
  other.click();
  check('a chip chosen: the select follows, Discard on', sel.value !== before && !pol.querySelector('.aw-discard').disabled);
  pol.querySelector('.aw-discard').click();
  check('  Discard: the select and its chips back as they were', sel.value === before
    && chips().find((c) => c.classList.contains('selected')).textContent === [...sel.options].find((o) => o.value === before).textContent
    && pol.querySelector('.aw-discard').disabled);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
}, 50);
