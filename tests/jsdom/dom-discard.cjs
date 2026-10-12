// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Save paired with Discard throughout /admin, in jsdom, static: every form with a Save has a Discard (its own or
// the widget's), off until its user changes something; Discard puts back what was there before, chips too, and
// sends nothing. Usage: [JSDOM=…/jsdom] node dom-discard.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window, d = w.document;
w.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));
const isSave = (b) => b.classList.contains('primary') && b.type === 'submit' && /^Save/.test(b.textContent.trim());
const forms = [...d.querySelectorAll('form')].filter((f) => [...f.querySelectorAll('button')].some(isSave));
const discardOf = (f) => [...f.querySelectorAll('button')].find((b) => /^Discard/.test(b.textContent.trim()));
const missing = forms.filter((f) => !discardOf(f)).map((f) => f.id || f.className);
check('every form with a Save has a Discard', forms.length >= 10 && !missing.length, `${forms.length} forms; without: ${missing}`);
check('  each off to start', forms.every((f) => discardOf(f).disabled));

// A plain field: changed, then discarded.
const f = forms.find((x) => x.querySelector('input[type="number"][name]') && discardOf(x).classList.contains('aw-discard'));
const input = f.querySelector('input[type="number"][name]');
input.value = '7';
input.dispatchEvent(new w.Event('focusin', { bubbles: true }));
input.value = '12';
input.dispatchEvent(new w.Event('input', { bubbles: true }));
check(`a field changed (${f.id || f.className}): Discard on`, !discardOf(f).disabled);
let sent = 0;
f.addEventListener('submit', () => { sent++; });
discardOf(f).click();
check('  Discard puts back what was there, and is off again; nothing sent', input.value === '7' && discardOf(f).disabled && sent === 0, input.value);

// Chips over a select: the chip clicked, then discarded; the chips follow.
const pol = d.getElementById('kits-policy');
const sel = pol.querySelector('select');
const chips = () => [...sel._chips.querySelectorAll('.chip')];
const before = sel.value;
const other = chips().find((c) => !c.classList.contains('selected'));
other.dispatchEvent(new w.Event('pointerdown', { bubbles: true }));
other.click();
check('a chip chosen: the select follows, Discard on', sel.value !== before && !discardOf(pol).disabled);
discardOf(pol).click();
check('  Discard: the select and its chips back as they were', sel.value === before
  && chips().find((c) => c.classList.contains('selected')).textContent === [...sel.options].find((o) => o.value === before).textContent
  && !pol.dataset.dirty && discardOf(pol).disabled);
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
