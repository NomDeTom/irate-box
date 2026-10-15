// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// web/i18n.js, the pages' own words in another language: it reads every message as tests/static_msg_guard.py
// lists it, says a catalogue's translation with the page's elements kept (links, controls, code), and keeps the
// English for an entry that is missing, stale or does not keep every element.
// Usage: NODE_PATH=…/node_modules node dom-i18n.cjs
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..', '..');
const WEB = path.join(ROOT, 'web');
const I18N = fs.readFileSync(path.join(WEB, 'i18n.js'), 'utf8');
let fails = 0;
function check(name, ok, info) { console.log((ok ? 'PASS ' : 'FAIL ') + name + (ok ? '' : '  ' + (info || ''))); if (!ok) fails++; }

const guard = spawnSync('python3', [path.join(ROOT, 'tests', 'static_msg_guard.py'), '--json'], { encoding: 'utf8' });
const english = JSON.parse(guard.stdout);
check('the guard lists the pages\' messages', Object.keys(english).length > 500, Object.keys(english).length);

// A page as the browser has it before its own scripts run, with i18n.js in it.
function page(file) {
  const dom = new JSDOM(fs.readFileSync(path.join(WEB, file), 'utf8'), { runScripts: 'outside-only' });
  dom.window.eval(I18N);
  return dom.window;
}
const norm = (s) => s.replace(/\s+/g, ' ').trim();
// The pseudo-language: every run of words between the tags in ⟦…⟧, the tags as they are.
const pseudo = (en) => en.split(/(<\d+\/?>|<\/\d+>)/).map((p) => (/^<\/?\d+\/?>$/.test(p) || !p.trim() ? p : `⟦${p}⟧`)).join('');
const catalogue = Object.fromEntries(Object.entries(english).map(([id, en]) => [id, { text: pseudo(en), en }]));
const pages = fs.readdirSync(WEB).filter((f) => f.endsWith('.html')).sort();

for (const file of pages) {
  const w = page(file), doc = w.document;
  const els = [...doc.querySelectorAll('[data-i18n]')];
  if (!els.length) continue;
  const html = doc.documentElement.outerHTML;
  check(`${file}: with no catalogue, nothing changes`, w.I18N.catalogue() === null && doc.documentElement.outerHTML === html);
  const wrong = els.filter((el) => w.I18N.read(el).en !== english[el.getAttribute('data-i18n')]);
  check(`${file}: i18n.js reads each message as the guard lists it (${els.length})`, !wrong.length,
    wrong.slice(0, 3).map((el) => `${el.getAttribute('data-i18n')}: ${JSON.stringify(w.I18N.read(el).en)}`).join('; '));

  const kept = (sel) => [...doc.querySelectorAll(sel)];
  const before = { ids: kept('[id]'), links: kept('a[href]').map((a) => a.getAttribute('href')), controls: kept('input, select, textarea, button'),
    code: kept('code, kbd, pre').map((c) => c.textContent) };
  w.I18N.apply(catalogue, doc);
  const unsaid = els.filter((el) => norm(w.I18N.read(el).en) !== norm(pseudo(english[el.getAttribute('data-i18n')])));
  check(`${file}: a catalogue says every message`, !unsaid.length,
    unsaid.slice(0, 3).map((el) => `${el.getAttribute('data-i18n')}: ${JSON.stringify(w.I18N.read(el).en)}`).join('; '));
  const attrs = kept('*').flatMap((el) => [...el.attributes].filter((a) => a.name.startsWith('data-i18n-')).map((a) => {
    const name = a.name.slice('data-i18n-'.length);
    const v = el.getAttribute(name) ?? el.getAttribute(`data-${name}`);
    return [a.value, v];
  }));
  const plain = attrs.filter(([, v]) => !/^⟦.*⟧$/.test(v));
  check(`${file}: and every attribute (${attrs.length})`, !plain.length, JSON.stringify(plain.slice(0, 3)));
  check(`${file}: the elements kept: ids, links, controls, code`,
    before.ids.every((el) => doc.getElementById(el.id) === el) && JSON.stringify(kept('a[href]').map((a) => a.getAttribute('href'))) === JSON.stringify(before.links)
    && before.controls.every((c) => doc.contains(c)) && JSON.stringify(kept('code, kbd, pre').map((c) => c.textContent)) === JSON.stringify(before.code));
}

// One message, its elements moved: the same nodes, in the new order.
{
  const w = page('admin.html'), doc = w.document;
  const el = doc.querySelector('[data-i18n="admin.main.first-use"]');
  const { en, nodes } = w.I18N.read(el);
  const link = nodes[1];
  w.I18N.apply({ 'admin.main.first-use': { text: '<1>Einrichten</1> zuerst. <0>Kein Konto.</0>', en } }, doc);
  check('reordered: the link first, the same node, its href kept', el.firstChild === link && link.textContent === 'Einrichten'
    && link.getAttribute('href') === nodes[1].getAttribute('href') && el.textContent === 'Einrichten zuerst. Kein Konto.', el.innerHTML);
}

// What keeps the English.
{
  const w = page('admin.html'), doc = w.document;
  const el = doc.querySelector('[data-i18n="admin.main.first-use"]');
  const { en } = w.I18N.read(el), html = el.innerHTML;
  for (const [why, text, from] of [['stale (made from other English)', '<0>Kein Konto.</0> <1>Einrichten</1>', en + ' Old.'],
    ['a translation that drops an element', '<0>Kein Konto.</0> Einrichten.', en],
    ['a translation with its tags crossed', '<0>Kein <1>Konto.</0> Einrichten</1>', en],
    ['a translation with an element the page lacks', '<0>Kein Konto.</0> <1>Einrichten</1> <7/>', en]]) {
    w.I18N.apply({ 'admin.main.first-use': { text, en: from } }, doc);
    check(`${why}: the English kept`, el.innerHTML === html, el.innerHTML);
  }
  const ph = doc.querySelector('[data-i18n-placeholder]');
  const was = ph.getAttribute('placeholder');
  w.I18N.apply({ [ph.getAttribute('data-i18n-placeholder')]: { text: 'Suchen', en: was + ' old' } }, doc);
  check('an attribute whose English changed: kept', ph.getAttribute('placeholder') === was, ph.getAttribute('placeholder'));
  w.I18N.apply({ '@lang': 'de' }, doc);
  check('@lang: the page\'s language', doc.documentElement.getAttribute('lang') === 'de');
}

// A choice's own words (data-says on an option), as admin-widgets.js reads them.
{
  const w = page('admin.html'), doc = w.document;
  const opt = doc.querySelector('option[data-i18n-says]');
  w.I18N.apply({ [opt.getAttribute('data-i18n-says')]: { text: 'Gesagt.', en: norm(opt.getAttribute('data-says')) } }, doc);
  check('an option\'s data-says, translated', opt.getAttribute('data-says') === 'Gesagt.', opt.getAttribute('data-says'));
}

console.log(fails ? `${fails} failure(s)` : 'ok');
process.exit(fails ? 1 : 0);
