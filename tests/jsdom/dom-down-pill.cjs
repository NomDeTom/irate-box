// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// An app not running, on the home page (hub.js): its tile marked down with a "not available" pill, which goes again
// when it runs; the note under the tiles in the words of the palette (High contrast says the pill, the others the
// fading). Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-down-pill.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/index.html`, 'utf8').replace('<!-- apps.d tiles -->',
  '<a class="service-card" href="/draw/" data-service="/draw/"><span class="icon">✏️</span><span class="name">Draw</span><span class="desc">Whiteboard</span></a>'
  + '<a class="service-card" href="/wiki/" data-service="/wiki/"><span class="icon">📚</span><span class="name">Kiwix</span><span class="desc">Library</span></a>');
const hub = fs.readFileSync(`${WEB}/hub.js`, 'utf8');

let fails = 0;
function check(name, cond, info = '') {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : `  ${info}`));
  fails += !cond;
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function home(palette, drawUp) {
  const dom = new JSDOM(html, { url: 'http://box/', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  if (palette) w.document.documentElement.setAttribute('data-palette', palette);
  let up = drawUp;
  // hub.js polls /status on a timer: caught here, to run a poll at once.
  const polls = [];
  w.setInterval = (fn, ms) => { if (ms === 15000) polls.push(fn); return 0; };
  w.fetch = async (url) => {
    if (url === '/status') return new Response(JSON.stringify({ online: 1, proxied: true,
      services: [{ path: '/draw/', up, state: 'stopped' }, { path: '/wiki/', up: true, state: 'running' }] }), { status: 200 });
    return new Response('{}', { status: 404 });
  };
  try { w.eval(hub); } catch (error) { check(`hub.js ran (${palette || 'box'})`, false, error.message); }
  await wait(200);
  return { w, d: w.document, setUp: (value) => { up = value; }, poll: () => polls.forEach((fn) => fn()) };
}

(async () => {
  const { d, setUp, poll } = await home('contrast', false);
  const draw = d.querySelector('[data-service="/draw/"]');
  const pill = draw.querySelector(':scope > .down-pill');
  check('High contrast: the app not running marked down, with its pill', draw.classList.contains('down') && pill && pill.textContent === 'not available');
  check('  the app running: no pill', !d.querySelector('[data-service="/wiki/"] .down-pill'));
  const note = d.querySelector('.services-note');
  check('  the note says the pill', note && /"not available"/.test(note.textContent), note && note.textContent);
  setUp(true);
  poll();
  await wait(200);
  const after = d.querySelector('[data-service="/draw/"]');
  check('  running again: unmarked, the pill gone', !after.classList.contains('down') && !after.querySelector('.down-pill'));

  const standard = await home('', false);
  const note2 = standard.d.querySelector('.services-note');
  check('the box\'s palette: the pill there too (hidden by style.css), the note says the fading',
    !!standard.d.querySelector('[data-service="/draw/"] > .down-pill') && note2 && /Greyed-out/.test(note2.textContent), note2 && note2.textContent);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
