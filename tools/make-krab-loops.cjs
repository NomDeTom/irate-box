// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Makes the lit krab desks as seamless WebM loops (menu overhaul M3), so pages play them rather
// than working the lights out: web/art/krab-controller-loop.webm and krab-factory-loop.webm.
//
// Step 1, this script: 12 s at 12 fps of PNG frames with alpha, drawn with web/krab-desk.js's
// own blob grouping and masks from art/krab-*-lit.webp and -mask.webp, in its moods (yellow
// lights blink and their edges flutter, blue ones glow), every cycle a divisor of the loop so it
// has no seam. Needs Chromium and Playwright (PLAYWRIGHT=path to the package, CHROMIUM=binary):
//   PLAYWRIGHT=…/node_modules/playwright CHROMIUM=/usr/bin/chromium node tools/make-krab-loops.cjs /tmp/frames
// Step 2, encode each with ffmpeg (Debian's; crf 50 is plenty for art shown at 16-22% opacity):
//   ffmpeg -framerate 12 -i /tmp/frames/controller/f%04d.png -c:v libvpx-vp9 -pix_fmt yuva420p \
//     -crf 50 -b:v 0 -row-mt 1 web/art/krab-controller-loop.webm        (and the same for factory)
// Measured 2026-10-08: 92 KB and 70 KB, against 86 KB and 58 KB for the stills, lit layer, mask
// and krab that krab-desk.js worked over in ~36 canvases a page; VP8 373/198 KB; animated WebP
// 4.0/0.85 MB (the glows change too many pixels each frame).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const http = require('http'), fs = require('fs'), path = require('path');
const WEB = path.resolve(__dirname, '../web'), out = process.argv[2];
if (!out) { console.error('usage: node tools/make-krab-loops.cjs <frames dir>'); process.exit(2); }
// The scenes, as krab-desk.js has them (its SCENES), plus whether the krab sits on top.
const SCENES = { controller: { art: 'krab-controller', np: 12, ng: 6, seed: 11, halo: 3, krab: true },
  factory: { art: 'krab-factory', each: true, seed: 7, halo: 3 } };
const T = 12, FPS = 12;
const srv = http.createServer((q, r) => {
  const u = decodeURIComponent(q.url.split('?')[0]);
  if (u === '/render.html') { r.writeHead(200, { 'Content-Type': 'text/html' }); r.end('<!doctype html><script src="/krab-desk.js"></script>'); return; }
  const f = path.join(WEB, path.normalize(u));
  if (!f.startsWith(WEB + path.sep)) { r.writeHead(403); r.end(); return; }
  fs.readFile(f, (e, d) => { if (e) { r.writeHead(404); r.end(); return; } r.writeHead(200, { 'Content-Type': f.endsWith('.js') ? 'text/javascript' : 'image/webp' }); r.end(d); });
}).listen(0, '127.0.0.1');
(async () => {
  await new Promise((ok) => (srv.listening ? ok() : srv.once('listening', ok)));
  const port = srv.address().port;
  const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM, args: ['--no-sandbox'] } : {});
  const p = await b.newPage();
  p.on('pageerror', (e) => console.error('page:', String(e)));
  await p.goto(`http://127.0.0.1:${port}/render.html`);
  for (const [name, sc] of Object.entries(SCENES)) {
    const dir = path.join(out, name);
    fs.mkdirSync(dir, { recursive: true });
    const n = await p.evaluate(async ([sc, T, FPS]) => {
      const load = (f) => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = '/art/' + f + '.webp'; });
      const [base, lit, mask, krab] = await Promise.all([load(sc.art), load(sc.art + '-lit'), load(sc.art + '-mask'), sc.krab ? load(sc.art + '-krab') : null]);
      const W = lit.naturalWidth, H = lit.naturalHeight;
      const cv = () => { const c = document.createElement('canvas'); c.width = W; c.height = H; return c; };
      const px = (img) => { const c = cv(), x = c.getContext('2d'); x.drawImage(img, 0, 0, W, H); return x.getImageData(0, 0, W, H).data; };
      const L = px(lit), M = px(mask);
      const kind = KD.classify(M, W * H), { labels, list } = KD.blobs(kind, W, H);
      let np = sc.np, ng = sc.ng, groupOf;
      if (sc.each) { groupOf = KD.each(list); np = list.filter((x) => x.kind === 1).length; ng = list.length - np; } else groupOf = KD.assign(list, np, ng, sc.seed);
      const layer = (alpha) => { const c = cv(), x = c.getContext('2d'), id = x.createImageData(W, H), d = id.data; d.set(L);
        for (let i = 0; i < W * H; i++) d[i * 4 + 3] = Math.min(d[i * 4 + 3], alpha[i]); x.putImageData(id, 0, 0); return c; };
      const pick = (r, opts) => opts[Math.floor(r() * opts.length)];
      const groups = [];
      for (let g = 0; g < np + ng; g++) {
        const { core, halo } = KD.masks(labels, groupOf, g, W, H, sc.halo), r = KD.rng(sc.seed * 1000 + g);
        let main, edge = null;
        if (g < np) {  // a yellow light: on and off in one of three moods; its edge flutters
          const n = 3 + Math.floor(r() * 7), mood = r(); let on = r() < 0.5; const w = [];
          for (let i = 0; i < n; i++) { on = !on; const base = mood < 0.33 ? (on ? 3 : 0.5) : mood < 0.66 ? (on ? 0.5 : 3) : 1.2; w.push(base * (0.3 + r() * 1.4)); }
          const sum = w.reduce((a, c) => a + c, 0); on = r() < 0.5; let t = 0; const ks = [];
          for (let i = 0; i < n; i++) { on = !on; ks.push([t, on ? 1 : 0]); t += w[i] / sum; }
          main = { dur: pick(r, [3, 4, 6, 12]), ks, step: true, off: r() };
          const fn = 5 + Math.floor(r() * 5), fks = [];
          for (let i = 0; i < fn; i++) fks.push([i / fn, 0.2 + r() * 0.75]);
          edge = { dur: pick(r, [0.5, 0.75, 1, 1.5]), ks: fks, step: true, off: r() };
        } else {  // a blue light: a slow glow down and up
          const lo = 0.15 + r() * 0.35;
          main = { dur: pick(r, [3, 4, 6]), ks: [[0, 1], [0.5, lo], [1, 1]], step: false, off: r() };
        }
        groups.push({ c: layer(core), h: layer(halo), main, edge });
      }
      const at = (a, t) => {
        const ph = ((t / a.dur + a.off) % 1 + 1) % 1, ks = a.ks;
        if (a.step) { let v = ks[0][1]; for (const [o, val] of ks) if (ph >= o) v = val; return v; }
        for (let i = 0; i < ks.length - 1; i++) if (ph >= ks[i][0] && ph <= ks[i + 1][0]) {
          const u = (ph - ks[i][0]) / (ks[i + 1][0] - ks[i][0]), e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
          return ks[i][1] + (ks[i + 1][1] - ks[i][1]) * e;
        }
        return ks[ks.length - 1][1];
      };
      window.frames_ = [];
      const c = cv(), x = c.getContext('2d');
      for (let f = 0; f < T * FPS; f++) {
        const t = f / FPS;
        x.clearRect(0, 0, W, H); x.globalAlpha = 1; x.drawImage(base, 0, 0, W, H);
        for (const g of groups) { const m = at(g.main, t); x.globalAlpha = m * (g.edge ? at(g.edge, t) : 0.7); x.drawImage(g.h, 0, 0); x.globalAlpha = m; x.drawImage(g.c, 0, 0); }
        x.globalAlpha = 1;
        if (krab) x.drawImage(krab, 0, 0, W, H);
        window.frames_.push(c.toDataURL('image/png'));
      }
      return window.frames_.length;
    }, [sc, T, FPS]);
    for (let i = 0; i < n; i++) {
      const data = await p.evaluate((i) => window.frames_[i], i);
      fs.writeFileSync(path.join(dir, `f${String(i).padStart(4, '0')}.png`), Buffer.from(data.split(',')[1], 'base64'));
    }
    console.log(`${name}: ${n} frames in ${dir}`);
  }
  await b.close();
  srv.close();
})();
