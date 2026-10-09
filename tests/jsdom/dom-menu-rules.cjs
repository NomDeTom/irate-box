// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin's shared widgets (web/admin-widgets.js) against the menu's rules: drawers inside their
// card, the title folds it, a ✕ on an opened card, short entries, records with a limited height, a
// filter on long lists, one open at a time, settings held until Save, and the control vocabulary.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-menu-rules.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
const css = fs.readFileSync(`${WEB}/style.css`, 'utf8').replace(/@import[^;]*;/g, '');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push('jsdom: ' + e.message));
const dom = new JSDOM(`<!doctype html><html><head><style>${css}</style></head><body class="admin"><main class="admin-main">
  <section class="admin-pane" id="a"></section><section class="admin-pane" id="b"></section></main></body></html>`,
{ url: 'http://box/admin/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8') + ';window.AW = AW;');
const AW = w.AW;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const click = (el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const type = (el, v) => { el.value = v; el.dispatchEvent(new w.Event('input', { bubbles: true })); };
const paneA = d.getElementById('a'), paneB = d.getElementById('b');

// ---- a short list of 30 ----
const items = Array.from({ length: 30 }, (_, i) => ({ id: 'e' + i, title: `Entry ${i}`, summary: i % 7 ? 'fine' : 'needs a look', badges: [i % 7 ? 'ok' : 'warn'], detail: { More: 'x' + i } }));
paneA.append(AW.shortList(items, { id: 'list1' }));
const rows = paneA.querySelectorAll('#list1 .aw-row');
check('every entry starts as one line', [...rows].every((r) => r.querySelector('.aw-row-detail').hidden), rows.length);
check('a list of 30 has a filter', !!paneA.querySelector('[data-filter-for="list1"]'));
check('over 20 entries scroll in their own box', w.getComputedStyle(d.getElementById('list1')).maxHeight !== 'none');
const q = paneA.querySelector('[data-filter-for="list1"] input');
type(q, 'entry 2');
check('the search narrows the list', [...rows].filter((r) => !r.hidden).length === 11, [...rows].filter((r) => !r.hidden).length);
type(q, '');
click(paneA.querySelector('[data-filter-for="list1"] .filter-chip'));
check('a state chip narrows the list', [...rows].filter((r) => !r.hidden).length === 25 || [...rows].filter((r) => !r.hidden).length === 5);
click(paneA.querySelector('[data-filter-for="list1"] .filter-chip'));
const h0 = rows[0].querySelector('.aw-row-head'), h1 = rows[1].querySelector('.aw-row-head');
click(h0);
check('an entry opens from its line, its details filled', !rows[0].querySelector('.aw-row-detail').hidden && /x0/.test(rows[0].textContent));
check('its head says it is open', h0.getAttribute('aria-expanded') === 'true');
click(h1);
check('one open at a time: the next entry closes the first', rows[0].querySelector('.aw-row-detail').hidden && !rows[1].querySelector('.aw-row-detail').hidden);
click(h1);
check('the line itself folds it again', rows[1].querySelector('.aw-row-detail').hidden && h1.getAttribute('aria-expanded') === 'false');
check('no separate collapse button', !paneA.querySelector('[data-act="collapse"]'));

// ---- records ----
paneA.append(AW.records(Array.from({ length: 60 }, (_, i) => ({ id: 'r' + i, title: `10-08 00:${String(i).padStart(2, '0')} an event` })), { id: 'rec1' }));
const rs = w.getComputedStyle(d.getElementById('rec1'));
check('records have a limited height and scroll', rs.maxHeight !== 'none' && /auto|scroll/.test(rs.overflowY), `${rs.maxHeight} ${rs.overflowY}`);
check('60 records have a filter', !!paneA.querySelector('[data-filter-for="rec1"]'));
paneA.append(AW.records([{ id: 'x', title: 'one' }], { id: 'rec2' }));
check('a short list has none', !paneA.querySelector('[data-filter-for="rec2"]'));

// ---- cards, with settings in the drawer ----
let saved = null;
const cardItems = Array.from({ length: 14 }, (_, i) => ({ id: 'c' + i, title: `Card ${i}`, summary: 'a card', badges: i === 3 ? ['installed'] : [] }));
paneB.append(AW.cards(cardItems, { id: 'cards1', body: (it, setDirty) => [AW.h('p', { text: 'about ' + it.title }),
  AW.h('button', { type: 'button', class: 'chip-btn', onclick: () => setDirty(true) }, 'Keep current: On')], save: (it) => { saved = it.id; } }));
const c0 = paneB.querySelector('[data-aw-id="c0"]'), c1 = paneB.querySelector('[data-aw-id="c1"]');
click(c0.querySelector('.aw-card-head'));
const dr = c0.querySelector('.aw-drawer');
check('the drawer opens inside its card', !!dr && c0.contains(dr));
check('the card widens to the row', c0.classList.contains('open'));
check('an opened card has a ✕', !!dr.querySelector('[data-act="close"]'));
check('14 cards have a filter', !!paneB.querySelector('[data-filter-for="cards1"]'));
const saveB = [...dr.querySelectorAll('.aw-foot .action-btn')].find((b) => b.textContent === 'Save');
check('Save waits until something changes', saveB.disabled && dr.querySelector('.aw-foot').hidden);
click(dr.querySelector('.chip-btn'));
check('a change enables Save and Discard', !saveB.disabled && !dr.querySelector('.aw-foot').hidden);
click(saveB);
check('Save sends it', saved === 'c0');
click(c1.querySelector('.aw-card-head'));
check('one open at a time: the next card closes the first', !c0.classList.contains('open') && c1.classList.contains('open'));
click(c1.querySelector('.aw-card-head'));
check('the title folds it again', !c1.classList.contains('open') && !c1.querySelector('.aw-drawer'));
click(c1.querySelector('.aw-card-head'));
d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape' }));
check('Esc closes it', !c1.classList.contains('open'));
click(c1.querySelector('.aw-card-head'));
click(c1.querySelector('[data-act="close"]'));
check('the ✕ closes it', !c1.classList.contains('open'));
click(c0.querySelector('.aw-card-head'));
click(paneB.ownerDocument.querySelector('#a .aw-row-head'));
check('one open per pane: another pane keeps its card open', c0.classList.contains('open'));

// ---- settings: held until Save, Discard puts them back ----
let sent = null;
const st = AW.settings([
  { key: 'keep', label: 'Keep current', kind: 'toggle', value: false },
  { key: 'when', label: 'Runs at', kind: 'choice', value: '03:00', options: ['03:00', '04:00', 'by hand'] },
  { key: 'https', label: 'HTTPS', kind: 'choice', value: 'ca', options: [['ca', 'the box\'s own CA'], ['off', 'off']], decision: 'https' },
  { label: 'Free', kind: 'readout', value: '9 GB' },
], { save: (v) => { sent = v; } });
paneB.append(st);
const sSave = [...st.querySelectorAll('.aw-foot .action-btn')].find((b) => b.textContent === 'Save');
check('settings: Save waits for a change', sSave.disabled);
click(st.querySelector('[data-setting="keep"] .chip-btn'));
click([...st.querySelectorAll('[data-setting="when"] .chip')][2]);
check('settings: the toggle says its state', st.querySelector('[data-setting="keep"] .chip-btn').textContent === 'On');
click(sSave);
check('settings: Save sends only what changed', JSON.stringify(sent) === JSON.stringify({ keep: true, when: 'by hand' }), JSON.stringify(sent));
click([...st.querySelectorAll('[data-setting="when"] .chip')][0]);
click([...st.querySelectorAll('.aw-foot .action-btn')].find((b) => b.textContent === 'Discard'));
check('settings: Discard puts back what was saved', st.querySelector('[data-setting="when"] .chip.selected').textContent === 'by hand');
check('settings: a setup decision is marked where it lives', !!st.querySelector('[data-decision-row="https"] .info-pill'));

// ---- findings: worst first, the cure beside it ----
let acted = null;
paneB.append(AW.findings([{ id: 'f1', title: 'All fine', status: 'ok' }, { id: 'f2', title: 'Link protections off', status: 'problem', detail: 'fs.protected_*',
  actions: [{ choice: 'kernel-links', label: 'Turn on' }] }], { id: 'find1', act: (c) => { acted = c; } }));
const fr = paneB.querySelectorAll('#find1 .aw-row');
check('findings: worst first', /Link protections/.test(fr[0].textContent));
click(fr[0].querySelector('.aw-row-head'));
click([...fr[0].querySelectorAll('.action-btn')].find((b) => b.textContent === 'Turn on'));
check('findings: the cure beside the finding acts', acted === 'kernel-links');

// ---- every button is one of the vocabulary; every disclosure says its state ----
const VOCAB = ['action-btn', 'chip', 'filter-chip', 'chip-btn', 'view-switch-btn', 'aw-row-head', 'aw-card-head'];
const odd = [...d.querySelectorAll('button')].filter((b) => !VOCAB.some((c) => b.classList.contains(c)));
check('every button is one of the vocabulary', !odd.length, odd.map((b) => b.outerHTML.slice(0, 60)).join(' | '));
check('every disclosure carries aria-expanded', [...d.querySelectorAll('[data-disclosure]')].every((b) => b.hasAttribute('aria-expanded')));
check('no page errors', !errors.length, errors.join('; '));

// ---- The same, on the real /admin: every page built, every button and disclosure looked at ----
{
  const adminApps = require('./admin-apps-fixture.cjs');
  const page = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
  const code = ['admin-widgets.js', 'admin-layout.js', 'admin.js', 'admin-tour.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
  const vc2 = new VirtualConsole();
  const real = new JSDOM(page, { url: 'http://box/admin/', runScripts: 'outside-only', virtualConsole: vc2, pretendToBeVisual: true });
  const rw = real.window;
  rw.fetch = async (u) => new rw.Response(JSON.stringify(String(u).startsWith('/admin/apps') ? { apps: adminApps() } : {}), { status: 200 });
  rw.HTMLElement.prototype.scrollIntoView = () => {};
  rw.scrollTo = () => {};
  try { rw.eval(code); } catch (e) { /* a loader with nothing to draw: the structure is what is looked at */ }
  setTimeout(() => {
    const rd = rw.document;
    // Besides the vocabulary: the menu's own controls (the sidebar, its ☰) and a link drawn as a button.
    const SHELL = ['admin-menu', 'admin-side-group', 'link-button', 'emoji-toggle'];
    const odd2 = [...rd.querySelectorAll('.admin-main button, .admin-side button')].filter((b) => ![...VOCAB, ...SHELL].some((c) => b.classList.contains(c)));
    const kinds = [...new Set(odd2.map((b) => b.className || '(none)'))];
    check('on the real /admin: every button one of the vocabulary', !odd2.length, `${odd2.length}: ${kinds.join(' | ')}`);
    // The lists drawn from data stay empty here, so their buttons are looked for in the source: each
    // el('button', {…}) the admin's scripts make names its class (the base button rule is gone).
    const unclassed = ['admin.js', 'admin-layout.js', 'admin-tour.js', 'factory.js'].flatMap((f) => {
      const src = fs.readFileSync(`${WEB}/${f}`, 'utf8');
      return [...src.matchAll(/el\('button', \{([^}]*)/g)].filter((m) => !/class(Name)?:/.test(m[1]))
        .map((m) => `${f}:${src.slice(0, m.index).split('\n').length}`);
    });
    check('in the admin\'s scripts: every button they make names its class', !unclassed.length, unclassed.join(' '));
    check('on the real /admin: every sidebar group head says its state', [...rd.querySelectorAll('.admin-side-group')].every((b) => b.hasAttribute('aria-expanded')));
    // The stub answers {} beyond the apps, so lists drawn from data are empty here: what is looked at
    // is every button the page and its empty states draw, on every page.
    const pagesBuilt = rd.querySelectorAll('.admin-page').length;
    check('the real /admin: a page for every sidebar entry', pagesBuilt > 20 && pagesBuilt === rd.querySelectorAll('.admin-side-list a[data-page]').length, pagesBuilt);
    // The older lists: bounded, and a filter once long, through every redraw.
    const msgs = rd.getElementById('mod-messages');
    check('an older record list bounded: the shoutbox\'s messages', msgs.classList.contains('aw-bounded'));
    msgs.replaceChildren(...Array.from({ length: 20 }, (_, i) => Object.assign(rd.createElement('div'), { textContent: i === 7 ? 'Moth: anyone on 868?' : `Ann: hello ${i}` })));
    setTimeout(() => {
      const bar = rd.querySelector('[data-filter-for="mod-messages"]');
      check('past twelve, a filter over it', bar && !bar.hidden && /20 of 20/.test(bar.textContent), bar && bar.textContent);
      const q = bar.querySelector('input');
      q.value = '868'; q.dispatchEvent(new rw.Event('input'));
      check('  the filter shows what matches', [...msgs.children].filter((r) => !r.hidden).length === 1 && /1 of 20/.test(bar.textContent));
      msgs.replaceChildren(...Array.from({ length: 5 }, () => rd.createElement('div')));
      setTimeout(() => {
        check('  a short list again: no filter, everything shown', bar.hidden && [...msgs.children].every((r) => !r.hidden));
        console.log(`failures: ${fails}`);
        process.exit(fails ? 1 : 0);
      }, 20);
    }, 20);
  }, 2500);
}
