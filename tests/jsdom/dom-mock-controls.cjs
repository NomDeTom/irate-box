// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin → Mock controls in jsdom: last in the sidebar, drawn only once opened, every control in its row with the
// line naming its code, the controls working on stand-ins, and nothing sent to the box.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-mock-controls.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['heatmap.js', 'emoji.js', 'admin-widgets.js', 'admin-layout.js', 'admin.js', 'admin-mock.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', (error) => { if (!/scrollTo|scrollIntoView/.test(error.message)) errors.push(error.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#overview', runScripts: 'outside-only', virtualConsole, pretendToBeVisual: true });
const w = dom.window;
const sent = [];
w.fetch = async (url, options = {}) => {
  if (options.method && options.method !== 'GET') sent.push(url);
  return new Response('{}', { status: 404 });
};
w.eval(js);

let fails = 0;
function check(name, cond, info = '') {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : `  ${info}`));
  fails += !cond;
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const d = w.document;
const text = (node) => (node ? node.textContent.replace(/\s+/g, ' ').trim() : '');

(async () => {
  await wait(800);
  const links = [...d.querySelectorAll('#admin-side-list a')];
  // Last of the box's own groups, after the doctors (here the apps' list never comes, so "More" may follow it).
  const at = links.map(text).indexOf('Mock controls');
  check('in the sidebar, right after the doctors', at > 0 && text(links[at - 1]) === 'Updates doctor', links.map(text).slice(at - 2, at + 1).join(' | '));
  check('not drawn before it is opened', !d.getElementById('mock-controls-body').children.length);

  w.location.hash = '#mock-controls';
  w.AL.show();
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  await wait(300);
  const rows = [...d.querySelectorAll('#mock-controls .mock-row')];
  const names = rows.map((row) => text(row.querySelector('h3')));
  const want = ['Action buttons', 'Chips: one of a few', 'Filter chips: any of a few', 'On and Off chips: a yes/no setting', 'On and Off chips over a checkbox', 'Joined tabs', 'Pills', 'Help that expands', 'A fold',
    'Uptime', 'Short entries, with a filter', 'Cards with a drawer', "A card after a pane's words", 'Records', 'A block of settings', 'Choice tiles', 'An ordered scale, as a slider', 'A drop-down of a few, as chips',
    'Updates: how often, how far, and the three buttons', 'A block kept while it is drawn again', 'Finding cards, one per state', 'Findings as a short list',
    'Form fields', 'Colour', 'Progress, output and space used', 'Tabs with their panels', 'A table', 'An empty list',
    'An app\'s page: its facts and its Access', 'The home page\'s tiles', 'The setup steps', 'A link to where the reason is', 'A package\'s card',
    'The theme picker and the name colour'];
  check('every control, in order', JSON.stringify(names) === JSON.stringify(want), names.join(' | '));
  check('each row: a cell with its control and the line naming its code',
    rows.every((row) => row.querySelector('.mock-cell[data-drawn-by="hub"]').children.length && text(row.querySelector('.mock-code code'))), rows.length);
  check('the doctors\' cards: one per state', d.querySelectorAll('#mock-finding-cards .finding').length === 8);
  const heads = [...d.querySelectorAll('#mock-uptime .hm-col')].map((head) => head.textContent).filter(Boolean);
  const firstCell = d.querySelector('#mock-uptime .hm-cell[aria-label]');
  check('the uptime grid: an hour every 12 columns, each cell its span', heads.length === 6 && heads.every((head) => /^\d\d:00$/.test(head))
    && /^\w{3} \d\d \d\d:00–\d\d:00: /.test(firstCell.getAttribute('aria-label')), `${heads.join(' ')} | ${firstCell && firstCell.getAttribute('aria-label')}`);

  // The settings block: Off warns in the footer while unsaved; Save saves here only.
  const off = [...d.querySelectorAll('#mock-section .chip')].find((chip) => text(chip) === 'Off');
  off.click();
  const foot = d.querySelector('#mock-section .aw-foot');
  check('a costly change warns in the footer while unsaved', /nobody can open it/.test(text(foot.querySelector('.aw-warn'))) && !foot.querySelector('.aw-save').disabled, text(foot));
  foot.querySelector('.aw-save').click();
  await wait(800);
  check('  Save: saved here, said so', /Saved here only/.test(text(foot)) && foot.querySelector('.aw-save').disabled, text(foot));

  // A card opens; a setting in its drawer brings its footer.
  d.querySelector('#mock-cards .aw-card-head').click();
  [...d.querySelectorAll('#mock-cards .aw-drawer .chip')].find((chip) => text(chip) === 'On').click();
  check('a card\'s drawer: its setting brings Save and Discard', !d.querySelector('#mock-cards .aw-foot').hidden && /Unsaved/.test(text(d.querySelector('#mock-cards .aw-foot'))));

  // Remove now asks on the page; Cancel keeps it.
  const remove = [...d.querySelectorAll('#mock-buttons button')].find((button) => text(button) === 'Remove now');
  remove.click();
  await wait(50);
  const ask = d.querySelector('#mock-buttons .aw-ask');
  check('Remove now asks on the page, not in a pop-up', !!ask, text(d.getElementById('mock-buttons')));
  [...ask.querySelectorAll('button')].find((button) => text(button) === 'Cancel').click();
  await wait(50);
  check('  Cancel: kept, said so', /Kept\./.test(text(d.getElementById('mock-buttons'))));

  // Joined tabs: one pressed at a time.
  const tabs = [...d.querySelectorAll('#mock-tabs .view-switch-btn')];
  tabs[1].click();
  check('joined tabs: one pressed at a time', tabs[1].getAttribute('aria-pressed') === 'true' && tabs[0].getAttribute('aria-pressed') === 'false');

  // The pages' own: tabs wired by initTabs (one panel showing), every kind of field, an app's Access saving here only.
  await wait(50);
  const panels = [...d.querySelectorAll('#mock-tabs-aria [role="tabpanel"]')];
  check('tabs with panels: one showing, its tab selected', panels.filter((panel) => !panel.hidden).length === 1
    && d.querySelectorAll('#mock-tabs-aria [role="tab"][aria-selected="true"]').length === 1, panels.map((panel) => panel.hidden).join());
  const kinds = [...d.querySelectorAll('#mock-fields input, #mock-fields textarea')].map((input) => input.type);
  check('form fields: text, number, password, address, message, file, tick', ['text', 'number', 'password', 'url', 'textarea', 'file', 'checkbox'].every((kind) => kinds.includes(kind)), kinds.join());
  const offChip = [...d.querySelectorAll('#mock-app-page .chip')].find((chip) => text(chip) === 'Disabled');
  offChip.click();
  check('an app\'s Access: Disabled warns in its footer', /nobody can open it/.test(text(d.querySelector('#mock-app-page .aw-foot'))), text(d.querySelector('#mock-app-page')));
  d.querySelector('#mock-app-page .aw-save').click();
  await wait(800);
  check('  Save: saved here only', /Saved here only/.test(text(d.querySelector('#mock-app-page .aw-foot'))));
  check('the home tiles: one not running, one locked, one admin only', !!d.querySelector('#mock-home-tiles .service-card.down')
    && !!d.querySelector('#mock-home-tiles .service-card.locked .lock') && !!d.querySelector('#mock-home-tiles .admin-pill'));
  // The frame across the top: the page's theme picker in it while the page is open, back in the header after.
  check('the frame: the theme picker in it while the page is open', !!d.querySelector('.mock-frame .theme-picker') && !d.querySelector('header .theme-picker')
    && d.body.classList.contains('mock-framed'));
  w.confirm = () => true;   // the card's drawer above still has unsaved changes: leaving asks first, and is told yes
  w.location.hash = '#overview';
  w.AL.show();
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  await wait(50);
  check('  another page: the picker back in the header, no frame', !!d.querySelector('header .theme-picker') && !d.querySelector('.mock-frame')
    && !d.body.classList.contains('mock-framed'));
  check('nothing sent to the box', !sent.length, sent.join(' '));
  check('no script errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
