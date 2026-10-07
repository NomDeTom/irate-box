// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The krab scenes' lights (style.css: .admin-art, .art-lamps): the controller's desk, and the
// Firmware Factory's production line. Each scene's picture in art/ is dark; art/<scene>-lit.webp
// is the same picture lit, and art/<scene>-mask.webp marks the lights (pink = the yellow or orange
// ones, green = the blue ones). Each blob of the mask is one light; they are shared out into
// groups (or each is a group of its own), and each group shows the lit picture through its blobs,
// plus a soft edge grown from them, with a blink pattern of its own. The scene is the one the
// page's data-art names (on .admin-art's parent), followed as it changes; each is built the
// first time it is shown, and kept. Built by make-krab-scene.cjs (Tom's notes,
// irate-box/2026-10-02-ui-improvements) from the layered pictures; the first half of this file
// is its krab-desk-lib.js.
// Krab desk v2: turns the colour-mask layer into per-group light masks. Plain JS, DOM only in the
// blink/glow/flutter animations, so the preview page and the hub's web/krab-desk.js inline it and
// make-krab-desk-v2.cjs tests it in Node.
const KD = (() => {
  // mask pixels (RGBA, unpremultiplied) -> 0 none, 1 pink (255,0,255), 2 green (0,255,0)
  function classify(rgba, n) {
    const k = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      const r = rgba[i * 4], g = rgba[i * 4 + 1], b = rgba[i * 4 + 2], a = rgba[i * 4 + 3];
      if (a < 128) continue;
      if (r > 150 && b > 150 && g < 110) k[i] = 1;
      else if (g > 150 && r < 110 && b < 110) k[i] = 2;
    }
    return k;
  }
  // 8-connected blobs of one colour: labels (0 = none, else blob index + 1) and each blob's kind
  function blobs(kind, w, h) {
    const labels = new Int32Array(w * h), list = [], stack = [];
    for (let s = 0; s < w * h; s++) {
      if (!kind[s] || labels[s]) continue;
      const id = list.length + 1, k = kind[s];
      let size = 0; labels[s] = id; stack.push(s);
      while (stack.length) {
        const p = stack.pop(); size++;
        const x = p % w, y = (p / w) | 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          const q = ny * w + nx;
          if (kind[q] === k && !labels[q]) { labels[q] = id; stack.push(q); }
        }
      }
      list.push({ id, kind: k, size });
    }
    return { labels, list };
  }
  function rng(seed) { // mulberry32
    return () => { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  // Each blob joins one of nPink yellow-light groups or nGreen blue groups; returns a group per label
  function assign(list, nPink, nGreen, seed) {
    const r = rng(seed), g = new Int16Array(list.length + 1);
    for (const b of list) g[b.id] = b.kind === 1 ? Math.floor(r() * nPink) : nPink + Math.floor(r() * nGreen);
    return g;
  }
  // Or every blob a group of its own (a scene with a few lights, each blinking for itself),
  // pink ones first, in the order the blobs were found (top to bottom, left to right)
  function each(list) {
    const g = new Int16Array(list.length + 1); let n = 0;
    for (const k of [1, 2]) for (const b of list) if (b.kind === k) g[b.id] = n++;
    return g;
  }
  // One group's mask: the blobs themselves (core, 0/255) and a soft halo grown out of them
  function masks(labels, groupOf, group, w, h, radius) {
    const core = new Uint8Array(w * h), f = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) if (labels[i] && groupOf[labels[i]] === group) { core[i] = 255; f[i] = 1; }
    const halo = blur(f, w, h, radius);
    const out = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) out[i] = Math.min(255, Math.round(halo[i] * 255 * 2.2));
    return { core, halo: out };
  }
  function blur(src, w, h, r) { // three box passes each way ~ a gaussian
    let a = new Float32Array(src), b = new Float32Array(w * h);
    const R = Math.max(1, Math.round(r / 1.7));
    for (let pass = 0; pass < 3; pass++) {
      for (let y = 0; y < h; y++) { let s = 0; const o = y * w;
        for (let x = -R; x <= R; x++) s += a[o + Math.min(w - 1, Math.max(0, x))];
        for (let x = 0; x < w; x++) { b[o + x] = s / (2 * R + 1);
          s += a[o + Math.min(w - 1, x + R + 1)] - a[o + Math.max(0, x - R)]; } }
      for (let x = 0; x < w; x++) { let s = 0;
        for (let y = -R; y <= R; y++) s += b[Math.min(h - 1, Math.max(0, y)) * w + x];
        for (let y = 0; y < h; y++) { a[y * w + x] = s / (2 * R + 1);
          s += b[Math.min(h - 1, y + R + 1) * w + x] - b[Math.max(0, y - R) * w + x]; } }
    }
    return a;
  }
  // Blink patterns, as Web Animations on an element (the only DOM this file touches)
  // A yellow light's pattern: 3-9 steps, on and off in random lengths, in one of three moods
  // (mostly on, mostly off, even), over a cycle of 2.5-11 s that starts at a random point.
  function blink(r, el) {
    const mood = r(), n = 3 + Math.floor(r() * 7), dur = 2500 + r() * 8500;
    let on = r() < .5, t = 0; const w = [], ks = [];
    for (let i = 0; i < n; i++) { on = !on;
      const base = mood < .33 ? (on ? 3 : .5) : mood < .66 ? (on ? .5 : 3) : 1.2;
      w.push(base * (.3 + r() * 1.4)); }
    const sum = w.reduce((a, b) => a + b, 0); on = r() < .5;
    for (let i = 0; i < n; i++) { on = !on; ks.push({ offset: t, opacity: on ? 1 : 0, easing: 'step-end' }); t += w[i] / sum; }
    ks.push({ offset: 1, opacity: ks[0].opacity });
    el.animate(ks, { duration: dur, iterations: Infinity, delay: -r() * dur });
  }
  // A blue light: a slow glow up and down between a random dimmest and full, sometimes with a dip.
  function glow(r, el) {
    const dur = 3000 + r() * 6500, lo = .15 + r() * .35, dip = r() < .3;
    const ks = [{ offset: 0, opacity: 1 }, { offset: .5, opacity: lo }, { offset: 1, opacity: 1 }];
    if (dip) ks.splice(2, 0, { offset: .62 + r() * .1, opacity: Math.min(1, lo + .4) }, { offset: .72, opacity: lo + .05 });
    el.animate(ks, { duration: dur, iterations: Infinity, easing: 'ease-in-out', delay: -r() * dur });
  }
  // The edge's flutter: 5-9 random brightnesses held for a moment each, a cycle of .5-1.6 s.
  function flutter(r, el) {
    const n = 5 + Math.floor(r() * 5), ks = [];
    for (let i = 0; i < n; i++) ks.push({ offset: i / n, opacity: .2 + r() * .75, easing: 'step-end' });
    ks.push({ offset: 1, opacity: ks[0].opacity });
    el.animate(ks, { duration: 500 + r() * 1100, iterations: Infinity, delay: -r() * 1000 });
  }
  return { classify, blobs, assign, each, masks, rng, blink, glow, flutter };
})();
(() => {
  // data-art -> the scene: its files, its yellow and blue groups (np, ng; each: a group per
  // light), the seed its patterns come from, and the glow's reach in pixels.
  const SCENES = {"controller":{"art":"krab-controller","np":12,"ng":6,"seed":11,"halo":3},"factory":{"art":"krab-factory","each":true,"seed":7,"halo":3}};
  const here = document.currentScript && document.currentScript.src;
  const art = document.querySelector('.admin-art'), host = art && art.querySelector('.art-lamps');
  if (!here || !host || !document.createElement('canvas').getContext) return;
  const owner = art.parentElement;
  const still = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  const load = (file) => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no;
    i.src = new URL('art/' + file + '.webp', here).href; });
  async function build(sc) {
    const imgs = await Promise.all([load(sc.art + '-lit'), load(sc.art + '-mask')]);
    const W = imgs[0].naturalWidth, H = imgs[0].naturalHeight;
    const pixels = (img) => { const c = document.createElement('canvas'); c.width = W; c.height = H;
      const x = c.getContext('2d', { willReadFrequently: true }); x.drawImage(img, 0, 0, W, H); return x.getImageData(0, 0, W, H).data; };
    const layer = (lit, alpha) => { const c = document.createElement('canvas'); c.width = W; c.height = H;
      const x = c.getContext('2d'), id = x.createImageData(W, H), d = id.data;  // not new ImageData: jsdom has none
      d.set(lit);
      for (let i = 0; i < W * H; i++) d[i * 4 + 3] = Math.min(d[i * 4 + 3], alpha[i]);
      x.putImageData(id, 0, 0); return c; };
    const [lit, mask] = imgs.map(pixels);
    const kind = KD.classify(mask, W * H), { labels, list } = KD.blobs(kind, W, H);
    let np = sc.np, ng = sc.ng, groupOf;
    if (sc.each) {
      groupOf = KD.each(list);
      np = list.filter((b) => b.kind === 1).length; ng = list.length - np;
    } else groupOf = KD.assign(list, np, ng, sc.seed);
    const out = [];
    for (let g = 0; g < np + ng; g++) {
      const { core, halo } = KD.masks(labels, groupOf, g, W, H, sc.halo);
      const d = document.createElement('i'), h = layer(lit, halo);
      d.append(h, layer(lit, core)); out.push(d);
      if (still) continue;
      const r = KD.rng(sc.seed * 1000 + g);
      if (g < np) { KD.blink(r, d); KD.flutter(r, h); } else { KD.glow(r, d); h.style.opacity = .7; }
    }
    return out;
  }
  const built = {};
  let current = null;
  function show() {
    const name = owner.dataset.art || '';
    if (name === current) return;
    current = name;
    host.replaceChildren();
    const sc = SCENES[name];
    if (!sc) return;
    (built[name] = built[name] || build(sc))
      .then((lights) => { if (current === name) host.replaceChildren(...lights); })
      .catch(() => {});
  }
  if (window.MutationObserver) new MutationObserver(show).observe(owner, { attributes: true, attributeFilter: ['data-art'] });
  show();
})();
