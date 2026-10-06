// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hub's themes: the one place that lists them. Every page loads this in its <head>, after
// style.css and before anything is drawn, so a page never paints in the wrong theme; hub.js
// builds the picker from the same list. To add a theme: a line below and its file in themes/.
//
// The main theme is light and dark, in style.css; "auto" (no choice saved) follows the OS.
// Other themes are a file each, themes/<id>.css, loaded only while chosen, styling
// [data-theme="<id>"]. Each names its base, light or dark: what the apps under the hub bar
// (Excalidraw, Mermaid, the calculators, Docusaurus books in Kiwix) are told, since they only
// know those two.
//
// Saved as two keys: "theme" is the base (light, dark, or none for auto), which Docusaurus
// books read as well, so it never holds anything else; "hub-theme" is another theme's id.
(function () {
  'use strict';
  var THEMES = [
    { id: 'light', label: 'Light', emoji: '☀️', base: 'light', main: true,
      desc: 'The hub in its light colours, whatever this device prefers' },
    { id: 'dark', label: 'Dark', emoji: '🌙', base: 'dark', main: true,
      desc: 'The hub in its dark colours, whatever this device prefers' },
    { id: 'eink', label: 'E-ink', emoji: '📖', base: 'light', css: '/themes/eink.css',
      desc: 'Black on white, no colour, no motion: for e-paper screens and bright sun' },
    { id: 'cybercore', label: 'Cybercore', emoji: '🏴‍☠️', base: 'dark', css: '/themes/cybercore.css',
      desc: 'Neon on black, from the PirateBox skin' },
  ];

  function byId(id) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) return THEMES[i];
    return null;
  }
  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function write(key, value) {
    try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (e) {}
  }

  // What is chosen: a theme's id, or "auto".
  function current() {
    var t = read('theme');
    if (t && t !== 'light' && t !== 'dark' && byId(t)) {
      // Saved before the split (the Cybercore theme kept its id in "theme"): move it.
      write('hub-theme', t);
      write('theme', byId(t).base);
    }
    var other = byId(read('hub-theme'));
    if (other && !other.main) return other.id;
    t = read('theme');
    return t === 'light' || t === 'dark' ? t : 'auto';
  }

  function stylesheet(theme) {
    var link = document.getElementById('hub-theme-css');
    if (!theme || !theme.css) {
      if (link) link.parentNode.removeChild(link);
      return;
    }
    if (link) {
      if (link.getAttribute('href') !== theme.css) link.setAttribute('href', theme.css);
    } else if (document.readyState === 'loading' && !document.body) {
      // While the <head> is still being read: written into it, so the page waits for it.
      document.write('<link id="hub-theme-css" rel="stylesheet" href="' + theme.css + '">');
    } else {
      link = document.createElement('link');
      link.id = 'hub-theme-css';
      link.rel = 'stylesheet';
      link.href = theme.css;
      document.head.appendChild(link);
    }
  }

  // Draw the page in a theme, without saving it (another tab's change, or the first paint).
  function show(id) {
    var theme = byId(id);
    var root = document.documentElement;
    if (theme) root.setAttribute('data-theme', id);
    else root.removeAttribute('data-theme');
    stylesheet(theme);
  }

  // The visitor's choice: saved, drawn, and announced (app.js tells the apps).
  function choose(id) {
    var theme = byId(id);
    if (!theme) { write('theme', null); write('hub-theme', null); id = 'auto'; }
    else if (theme.main) { write('theme', id); write('hub-theme', null); }
    else { write('theme', theme.base); write('hub-theme', id); }
    show(id);
    document.dispatchEvent(new CustomEvent('hub-theme', { detail: id }));
  }

  // The base the apps are told: light, dark or auto.
  function base(id) {
    var theme = byId(id === undefined ? current() : id);
    return theme ? theme.base : 'auto';
  }

  window.HubThemes = { list: THEMES, byId: byId, current: current, show: show, choose: choose, base: base };
  show(current());
})();
