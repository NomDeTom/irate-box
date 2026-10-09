// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The krab desks lit, the art as WebM. The
// controller's desk and the Firmware Factory's line are seamless 12 s loops, VP9 with alpha,
// made once by tools/make-krab-loops.cjs from art/krab-*-lit.webp and -mask.webp: the page plays
// them and works nothing out (krab-desk.js read the art's pixels and built ~36 canvases on every
// load, and could not run from a page opened from disk). Like it, this follows data-art on
// .admin-art's parent. The still stays underneath: what shows with reduced motion, and in
// Safari, which does not play VP9's alpha.
(function () {
  'use strict';
  var LOOPS = { controller: 'art/krab-controller-loop.webm', factory: 'art/krab-factory-loop.webm' };
  var art = document.querySelector('.admin-art');
  if (!art) return;
  var owner = art.parentElement;
  var here = (document.currentScript && document.currentScript.src) || location.href;
  var ua = navigator.userAgent;
  var still = (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)
    || (/Safari\//.test(ua) && !/Chrome\/|Chromium\/|Firefox\//.test(ua));
  if (still) return;
  var v = document.createElement('video');
  v.className = 'art-loop';
  v.muted = true; v.loop = true; v.autoplay = true; v.playsInline = true;
  v.setAttribute('muted', ''); v.setAttribute('playsinline', ''); v.setAttribute('aria-hidden', 'true');
  v.hidden = true;
  art.append(v);
  function show() {
    var src = LOOPS[owner.dataset.art || ''];
    art.classList.toggle('looping', !!src);
    v.hidden = !src;
    if (!src) { if (v.pause) try { v.pause(); } catch (e) {} return; }
    var url = new URL(src, here).href;
    if (v.src !== url) v.src = url;
    try { var p = v.play(); if (p && p.catch) p.catch(function () {}); } catch (e) {}
  }
  if (window.MutationObserver) new MutationObserver(show).observe(owner, { attributes: true, attributeFilter: ['data-art'] });
  show();
})();
