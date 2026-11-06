// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The flashing page's board tools (flasher-admin.js): hidden for a guest, shown for an administrator, each
// linking to its own page. The pages keep their own /admin sign-in. Usage: [JSDOM=…/jsdom] node dom-flasher-admin.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/esp-flasher.html`, 'utf8');
const js = fs.readFileSync(`${WEB}/flasher-admin.js`, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
function boot(answer) {
  const dom = new JSDOM(html, { url: 'http://box/flasher/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  const calls = [];
  w.fetch = async (url) => {
    calls.push(url);
    if (url === '/_irate/admin') return answer === 204 ? new Response(null, { status: 204 }) : new Response('', { status: answer });
    return new Response('{}', { status: 404 });
  };
  w.eval(js);
  w.calls = calls;
  return w;
}
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const guest = boot(401);
  await wait();
  check('a guest: the board tools stay hidden', guest.document.getElementById('flasher-board-tools').hidden);
  const admin = boot(204);
  await wait();
  const d = admin.document;
  const section = d.getElementById('flasher-board-tools');
  check('an administrator: the board tools are shown', !section.hidden);
  const links = [...section.querySelectorAll('a.git-card')].map((a) => a.getAttribute('href'));
  check('  each opens its own page', JSON.stringify(links) === JSON.stringify(['/admin/rockchip.html', '/admin/rk-setup.html', '/admin/rk-cards.html']), links);
  check('  their names and words are there', /Rockchip boards/.test(section.textContent) && /New boards/.test(section.textContent)
    && /Cards in a reader/.test(section.textContent) && /administrator sign-in/.test(section.textContent), section.textContent);
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
