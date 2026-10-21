// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// AW.section, the one Save and Discard of /admin, in jsdom: every rule of the save-patterns plan. Static.
// Usage: [JSDOM=…/jsdom] node dom-section.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const dom = new JSDOM('<!doctype html><body><main><section class="admin-pane" id="p1"></section><section class="admin-pane" id="p2"></section></main></body>', { runScripts: 'outside-only' });
const w = dom.window, d = w.document;
w.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));
const AW = w.AW;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 10) => new Promise((r) => setTimeout(r, ms));
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const btns = (s) => ({ save: s.foot.querySelector('.aw-save'), discard: s.foot.querySelector('.aw-discard'), note: s.foot.querySelector('.note') });
let asked = [], answer = true;
w.confirm = (q) => { asked.push(q); return answer; };
const place = (s, page = 'p1') => { d.getElementById(page).append(s.foot, s.reason); return s; };

(async () => {
  // Not read yet: nothing to change, nothing counted.
  let sent = [];
  const s = place(AW.section({ id: 'one', label: 'One', saved: null, save: async (c) => { sent.push(c); } }));
  check('not read yet: says so, Save and Discard off', t(btns(s).note) === 'Reading the current settings…' && btns(s).save.disabled && btns(s).discard.disabled);
  s.set('a', 1);
  check('  a pick before the values are read changes nothing', !s.dirty() && !AW.unsaved().length);
  s.update({ a: 1, b: 'x' });
  check('read: the idle words, both off', t(btns(s).note) === 'Changes apply after you select Save.' && btns(s).save.disabled && btns(s).discard.disabled);

  // 1. Compared with the saved values.
  s.set('a', 2);
  check('1. changed: "Unsaved changes.", Save and Discard on', t(btns(s).note) === 'Unsaved changes.' && !btns(s).save.disabled && !btns(s).discard.disabled);
  s.set('a', 1);
  check('   changed back: off again, nothing unsaved', btns(s).save.disabled && !s.dirty() && t(btns(s).note) !== 'Unsaved changes.');
  s.set('b', ['x', 'y']);
  s.set('b', 'x');
  check('   a list or an object compared by what it holds', !s.dirty());

  // 2. One footer: Discard, then Save, the primary.
  const kids = [...s.foot.querySelectorAll('button')];
  check('2. the footer: Discard then Save, Save the primary, the words always these', kids.length === 2 && t(kids[0]) === 'Discard' && t(kids[1]) === 'Save'
    && kids[1].classList.contains('primary') && !kids[0].classList.contains('primary'));
  s.set('a', 5);
  btns(s).discard.click();
  check('   Discard: the saved values back, nothing sent', s.get('a') === 1 && !s.dirty() && !sent.length);

  // 3. No question: what the changes cost, said beside Save while they are not saved.
  const q = place(AW.section({ id: 'ask', saved: { a: 1, b: 1 }, warn: (c) => `${Object.keys(c).join(' and ')} change at once.`, save: async () => {} }));
  q.set('a', 2);
  q.set('b', 2);
  check('3. no question per pick, the warning beside Save', !asked.length && q.foot.querySelector('.aw-warn').textContent === 'a and b change at once.');
  await q.save();
  check('   Save: saved, no question, the warning gone', !q.dirty() && !asked.length && q.foot.querySelector('.aw-warn').hidden);

  // 4. Saving: words while it waits; cleared on success only; the reason on failure.
  let job;
  const sv = place(AW.section({ id: 'job', saved: { a: 1 }, save: () => (job = AW.job()) }));
  sv.set('a', 2);
  const saving = sv.save();
  await wait();
  check('4. "Saving…" while the root helper works, both off, picks ignored', t(btns(sv).note) === 'Saving…' && btns(sv).save.disabled && btns(sv).discard.disabled
    && (sv.set('a', 3), sv.get('a') === 2));
  sv.update({ a: 1 });
  check('   the poll meanwhile changes nothing', sv.get('a') === 2);
  job.done(false, 'The disk is full.');
  await saving;
  check('   failed: the draft kept, the reason under the footer, Save on again', sv.get('a') === 2 && sv.dirty() && t(sv.reason) === 'The disk is full.' && !sv.reason.hidden && !btns(sv).save.disabled);
  const again = sv.save();
  await wait();
  job.done(true);
  await again;
  check('   done: saved, the reason gone', !sv.dirty() && sv.reason.hidden && sv.savedValue('a') === 2);
  const said = place(AW.section({ saved: { a: 1 }, save: async () => 'Saved. It applies at the next hourly cycle.' }));
  said.set('a', 2);
  await said.save();
  check('   a save may say what happens next', t(btns(said).note) === 'Saved. It applies at the next hourly cycle.');
  const no = place(AW.section({ saved: { a: 1 }, save: async () => false }));
  no.set('a', 2);
  await no.save();
  check('   a save that resolves false (said already): the draft kept', no.dirty());

  // 5. Polling never overwrites a change.
  const p = place(AW.section({ id: 'poll', saved: { a: 1, b: 1 }, onChange: () => {} }));
  p.set('a', 2);
  p.update({ a: 1, b: 7 });
  check('5. the poll: a changed value kept, an unchanged one follows', p.get('a') === 2 && p.get('b') === 7 && t(btns(p).note) === 'Unsaved changes.');
  p.update({ a: 9, b: 7 });
  check('   saved underneath it: kept, and "changed elsewhere" said', p.get('a') === 2 && /Changed elsewhere since you started/.test(t(btns(p).note)));
  p.discard();
  check('   Discard shows the other change', p.get('a') === 9 && !p.dirty());
  p.set('a', 3);
  const redrawn = place(AW.section({ id: 'poll', saved: { a: 9, b: 7 } }));
  p.foot.remove();
  check('   a section drawn again with the same id keeps its draft', redrawn.get('a') === 3 && redrawn.dirty());
  const moved = AW.section({ id: 'poll', saved: { a: 4, b: 7 } });
  check('   and says when the saved value moved meanwhile', moved.get('a') === 3 && moved.elsewhereKeys().includes('a'));
  redrawn.foot.replaceWith(moved.foot);

  // 6. Every unsaved section listed, with its page.
  const other = place(AW.section({ id: 'other', label: 'Other', saved: { a: 1 } }), 'p2');
  other.set('a', 2);
  let told = 0;
  d.addEventListener('aw-unsaved', () => told++);
  const list = AW.unsaved();
  check('6. AW.unsaved: each changed section, its label and page', list.some((x) => x.id === 'other' && x.page === 'p2' && x.label === 'Other') && list.some((x) => x.id === 'poll'), JSON.stringify(list));
  other.discard();
  check('   told when the list changes', told >= 1 && !AW.unsaved().some((x) => x.id === 'other'));
  other.foot.remove();
  check('   a section no longer on the page is not listed', !AW.unsaved().some((x) => x.id === 'other'));

  // AW.settings is a section with rows.
  const block = AW.settings([{ key: 'on', label: 'On', kind: 'toggle', value: false }, { key: 'name', label: 'Name', kind: 'text', value: 'a' }],
    { id: 'rows', save: async (c) => { sent.push(c); } });
  d.getElementById('p1').append(block);
  block.querySelector('.chip-btn').click();
  const input = block.querySelector('input');
  input.focus();
  input.value = 'ab';
  input.dispatchEvent(new w.Event('input'));
  check('settings rows: a toggle and a typed field, the field not drawn again while typed in', block.values().on === true && block.values().name === 'ab'
    && d.activeElement === input && t(block.querySelector('.aw-foot .note')) === 'Unsaved changes.');
  block.querySelector('.aw-save').click();
  await wait();
  check('   Save sends only the changes', JSON.stringify(sent[sent.length - 1]) === '{"on":true,"name":"ab"}' && !block.section.dirty());

  // Any of a few (moderation's reasons): filter chips, the value in the options' order; one that must keep one says why.
  const multi = AW.settings([{ key: 'r', label: 'Reasons', kind: 'multi', value: ['spam'], options: ['spam', 'abuse', 'other'] }],
    { id: 'multi', save: async () => {}, why: (c, v) => (v.r.length ? null : 'Offer at least one reason.') });
  d.getElementById('p1').append(multi);
  const chipsOf = () => [...multi.querySelectorAll('.filter-chip')];
  chipsOf()[2].click();
  check('multi: a chip added, in the options\' order, held for Save', JSON.stringify(multi.values().r) === '["spam","other"]' && !multi.querySelector('.aw-save').disabled);
  chipsOf()[0].click();
  chipsOf()[2].click();
  check('  none left: Save off, and why', multi.querySelector('.aw-save').disabled && /at least one/.test(t(multi.querySelector('.aw-reason'))));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
