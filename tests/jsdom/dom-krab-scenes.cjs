// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The krab scenes' lights (web/krab-desk.js) in jsdom, drawn for real: needs the `canvas` package
// beside jsdom (or on NODE_PATH), as jsdom only draws with it; without it, this says so and stops.
// A page showing the Firmware Factory's production line gets a light per mark in its mask (8),
// each the lit picture through the mark and its glow; switching to the controller's desk gets the
// desk's 18 groups instead; back to the factory, the lights already built come back; a pane with
// no scene has none. node-canvas reads no WebP, so the pictures reach it as PNG (sharp; the same
// pixels). Usage: [JSDOM=…/jsdom] NODE_PATH=…/canvas/..:…/sharp/.. node dom-krab-scenes.cjs
const { JSDOM, VirtualConsole, ResourceLoader } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
let sharp;
try { require('canvas'); sharp = require('sharp'); } catch (e) { console.log('SKIP no canvas or sharp package: jsdom cannot draw the art'); process.exit(0); }
class AsPng extends ResourceLoader {
  fetch(url, opts) {
    if (!url.endsWith('.webp')) return super.fetch(url, opts);
    return sharp(new URL(url).pathname).png().toBuffer();
  }
}
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push('jsdom: ' + e.message));
vc.on('error', (...a) => errors.push('console: ' + a.join(' ')));
const html = `<!doctype html><body><main data-art="factory"><div class="admin-art"><span class="art-lamps"></span><span class="art-krab"></span></div></main>
<script src="krab-desk.js"></script></body>`;
const dom = new JSDOM(html, { url: `file://${WEB}/scene-test.html`, runScripts: 'dangerously', resources: new AsPng(), virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
w.Element.prototype.animate = function () { (this.__anims = this.__anims || []).push([...arguments]); return {}; };
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const until = async (cond, ms = 20000) => { const end = Date.now() + ms; while (!cond() && Date.now() < end) await new Promise((r) => setTimeout(r, 100)); return cond(); };
(async () => {
  const d = w.document, host = d.querySelector('.art-lamps'), main = d.querySelector('main');
  const lights = () => [...host.children];
  await until(() => lights().length > 0);
  const f = lights();
  check('the factory: a light per mark (8)', f.length === 8, f.length);
  check('  each the lit picture through its glow and its mark, the page\'s size', f.every((i) => i.children.length === 2 && [...i.children].every((c) => c.width === 900 && c.height === 266)));
  const lit = (c) => { const a = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < a.length; i += 4) n += a[i] > 0; return n; };
  check('  a mark shows only its own few pixels', f.every((i) => { const n = lit(i.children[1]); return n > 50 && n < 900; }), f.map((i) => lit(i.children[1])).join(' '));
  check('  each blinks, and its glow flutters', f.every((i) => i.__anims && i.__anims.length === 1 && i.children[0].__anims && i.children[0].__anims.length === 1));
  main.dataset.art = 'controller';
  await until(() => lights().length !== 8 && lights().length > 0);
  check('the controller\'s desk instead: its 18 groups', lights().length === 18 && lights()[0].children[0].width === 900 && lights()[0].children[0].height === 317, lights().length);
  main.dataset.art = 'factory';
  await until(() => lights().length === 8);
  check('back to the factory: the same lights, not built again', lights().length === 8 && lights()[0] === f[0]);
  main.dataset.art = 'librarian';
  await until(() => lights().length === 0, 2000);
  check('a scene with no lights: none', lights().length === 0);
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
