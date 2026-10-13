// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The box tile's trends on the hub page (hub.js), in jsdom: no triangle where /api/trends refuses (a guest, a user);
// for the admin a triangle on the tile, closed at first, opening a panel below the tiles with the day's memory and disk
// lines (the disk's broken where it has no samples). Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-box-trends.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const page = fs.readFileSync(`${WEB}/index.html`, 'utf8').replace('<!-- apps.d box tiles -->',
  '<div class="service-card system-card" id="system-card"><span class="icon">💽💾</span></div>');
const hubjs = fs.readFileSync(`${WEB}/hub.js`, 'utf8');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const now = 1790950000;
const trends = { now, every: 300, samples: [
  { at: now - 3600, total: 1000, available: 600, swap_total: 0, swap_free: 0 },
  { at: now - 600, total: 1000, available: 500, swap_total: 0, swap_free: 0, disk_total: 8000, disk_free: 4000 },
  { at: now - 300, total: 1000, available: 400, swap_total: 0, swap_free: 0, disk_total: 8000, disk_free: 3000 },
  { at: now, total: 1000, available: 450, swap_total: 0, swap_free: 0, disk_total: 8000, disk_free: 2000 }] };
async function run(admin) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push(e.message));
  const dom = new JSDOM(page, { url: 'http://box/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  let asked = 0;
  w.fetch = async (u) => {
    if (u === '/api/trends') { asked++; return admin ? new Response(JSON.stringify(trends), { status: 200 }) : new Response('{"error":"no"}', { status: 403 }); }
    return new Response('{}', { status: 404 });
  };
  try { w.eval(hubjs); } catch (e) { errors.push(String(e)); }
  await new Promise((r) => setTimeout(r, 100));
  return { w, d: w.document, errors, asked: () => asked };
}
(async () => {
  {
    const { d } = await run(false);
    check('refused (a guest, a user): no triangle, no panel', !d.querySelector('.trend-toggle') && !d.getElementById('box-trends'));
  }
  const { d, asked } = await run(true);
  const toggle = d.querySelector('#system-card .trend-toggle'), panel = d.getElementById('box-trends');
  check('the admin: a triangle on the tile, closed, naming the panel it opens', toggle && toggle.getAttribute('aria-expanded') === 'false'
    && toggle.getAttribute('aria-controls') === 'box-trends' && /^▸ Trends$/.test(toggle.textContent) && panel && panel.hidden);
  check('  the panel below the tiles, not in the grid', panel && !panel.closest('.service-grid') && panel.previousElementSibling.classList.contains('service-grid'));
  const before = asked();
  toggle.click();
  await new Promise((r) => setTimeout(r, 50));
  check('  opened: shown, the triangle turned, the trends asked again', !panel.hidden && toggle.getAttribute('aria-expanded') === 'true'
    && /^▾/.test(toggle.textContent) && asked() === before + 1);
  const mem = panel.querySelector('path.trend-mem'), disk = panel.querySelector('path.trend-disk');
  check('  memory\'s line over every sample; the disk\'s from the samples that have it', mem && (mem.getAttribute('d').match(/[ML]/g) || []).length === 4
    && disk && (disk.getAttribute('d').match(/[ML]/g) || []).length === 3, `${mem && mem.getAttribute('d')} | ${disk && disk.getAttribute('d')}`);
  check('  said in words too, with the way to more', /Memory used 55% at the last sample; disk 75% \(dotted\)\./.test(panel.textContent)
    && !!panel.querySelector('a[href="/admin/#overview"]'), panel.textContent);
  toggle.click();
  check('  closed again: hidden', panel.hidden && toggle.getAttribute('aria-expanded') === 'false');
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
