// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// cgit's code view, highlighted in the visitor's browser (config/cgit-head.html loads this after
// Debian's highlight.js, /git-hl/). On the box it cost 2-5 s a page with Pygments; here it is
// the phone's work, and the box only serves files. The language comes from the file's name;
// highlight.js guesses when the name says nothing. Colours: web/cgit-hub.css, from the palette.
(function () {
  'use strict';
  var MAX = 512 * 1024; // larger files stay plain: a phone must not stall on a dump
  // Names highlight.js 9 does not know by their extension alone.
  var BY_NAME = { 'makefile': 'makefile', 'dockerfile': 'dockerfile', 'cmakelists.txt': 'cmake',
                  'caddyfile': 'nginx', 'pkgbuild': 'bash', 'irate-box': 'bash' };
  var BY_EXT = { 'sh': 'bash', 'cjs': 'javascript', 'mjs': 'javascript', 'h': 'cpp', 'hpp': 'cpp',
                 'ino': 'cpp', 'yml': 'yaml', 'toml': 'ini', 'service': 'ini', 'timer': 'ini',
                 'socket': 'ini', 'path': 'ini', 'conf': 'nginx', 'nginx': 'nginx', 'proto': 'protobuf' };

  function language(path) {
    var name = decodeURIComponent(path.split('/').pop() || '').toLowerCase();
    if (BY_NAME[name]) return BY_NAME[name];
    var dot = name.lastIndexOf('.');
    if (dot < 0) return null;
    var ext = name.slice(dot + 1);
    var lang = BY_EXT[ext] || ext;
    return window.hljs && hljs.getLanguage(lang) ? lang : null;
  }

  function run() {
    if (!window.hljs) return;
    var code = document.querySelector('table.blob td.lines code');
    if (!code || code.textContent.length > MAX) return;
    var lang = language(location.pathname);
    if (lang) code.className = 'language-' + lang;
    hljs.highlightBlock(code);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
