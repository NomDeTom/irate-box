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
    // highlight.js 9 (Debian 13's) has highlightBlock; 10.7 renamed it, 11 dropped the old name.
    (hljs.highlightElement || hljs.highlightBlock).call(hljs, code);
  }

  // A bar of the hub's own at the top: back to the hub, and back to this area's index (public
  // /git/ or private /git-private/, from the address), so a visitor deep in a repository is
  // never far from the list. Without this script the page is plain cgit.
  function bar() {
    var area = location.pathname.indexOf('/git-private/') === 0 ? '/git-private/' : '/git/';
    var nav = document.createElement('nav');
    nav.className = 'hub-bar';
    var links = [['🏠 Hub', '/', 'Back to the hub'],
                 ['📚 Repositories', area, 'The list of repositories']];
    links.forEach(function (l) {
      var a = document.createElement('a');
      a.href = l[1]; a.title = l[2]; a.textContent = l[0];
      nav.appendChild(a);
    });
    document.body.insertBefore(nav, document.body.firstChild);
  }

  function start() { bar(); run(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
