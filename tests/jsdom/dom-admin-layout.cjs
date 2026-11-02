// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin menu on a phone (web/admin-layout.js): the ☰ opens a panel bounded by the button's
// bottom, Escape and a tap outside close it, choosing a page closes it, and with nothing folded yet
// only the current group starts open. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-admin-layout.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, {
  url: 'http://box/admin/#network',
  runScripts: 'outside-only',
  virtualConsole: vc,
  pretendToBeVisual: true,
});
const w = dom.window, d = w.document;
w.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
w.HTMLElement.prototype.scrollIntoView = () => {};
w.scrollTo = () => {};
d.querySelector('.admin-menu').getBoundingClientRect = () => ({ top: 10, bottom: 50, height: 40, left: 0, right: 390, width: 390 });
w.fetch = async () => new Response('{"apps":[]}', { status: 200 });
w.eval(js);
setTimeout(async () => {
  let fails = 0;
  const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
  const click = (node) => node.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  const side = d.querySelector('.admin-side');
  const menu = d.querySelector('.admin-menu');
  const heads = [...d.querySelectorAll('.admin-side-group')];
  const openGroups = () => heads.filter((b) => b.getAttribute('aria-expanded') === 'true').map((b) => b.dataset.group);
  check('the ☰ button names the list it opens', menu.getAttribute('aria-controls') === 'admin-side-list');
  check('with nothing folded yet on a phone, only the current page\'s group starts open',
    openGroups().length === 1 && openGroups()[0] === 'System', openGroups());
  click(menu);
  check('the ☰ opens the panel', side.classList.contains('open') && menu.getAttribute('aria-expanded') === 'true');
  w.dispatchEvent(new w.Event('resize'));
  await new Promise((r) => setTimeout(r, 50));
  const room = side.style.getPropertyValue('--side-room');
  check('the open panel gets its own room, from the button\'s bottom', /^\d+px$/.test(room) && parseInt(room, 10) >= 160, room);
  d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  check('Escape closes it, and focus goes back to ☰',
    !side.classList.contains('open') && menu.getAttribute('aria-expanded') === 'false' && d.activeElement === menu,
    [side.className, menu.getAttribute('aria-expanded'), d.activeElement && d.activeElement.className]);
  click(menu);
  click(d.querySelector('.admin-main'));
  check('a tap on the page behind closes it', !side.classList.contains('open') && menu.getAttribute('aria-expanded') === 'false');
  click(menu);
  w.location.hash = '#health';
  await new Promise((r) => setTimeout(r, 80));
  check('choosing a page closes it', !side.classList.contains('open') && menu.getAttribute('aria-expanded') === 'false');
  check('  the chosen page\'s group is the one open', openGroups().length === 1 && openGroups()[0] === 'Doctors', openGroups());
  check('no page errors', !errors.length, errors.join('; '));
  process.exit(fails ? 1 : 0);
}, 300);
