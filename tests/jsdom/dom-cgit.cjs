// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// cgit's code view (web/cgit-hub.js): the language from the file's name, a stand-in for
// highlight.js asked to highlight the code element (never handed HTML), large files left plain.
// Static: no hub needed.  Usage: [JSDOM=…/jsdom] node dom-cgit.cjs
const path = require('path');
const fs = require('fs');
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const script = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'cgit-hub.js'), 'utf8');
let fails = 0;
function check(name, ok, info) {
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (ok ? '' : '  ' + (info || '')));
  if (!ok) fails++;
}
const KNOWN = ['python', 'py', 'bash', 'javascript', 'js', 'cpp', 'nginx', 'ini', 'makefile', 'yaml', 'css'];
function page(file, text) {
  const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body><div id="cgit"><table class="blob"><tr>
    <td class="linenumbers"><pre><a id="n1">1</a></pre></td><td class="lines"><pre><code></code></pre></td></tr></table></div></body></html>`,
  { url: 'http://box.local/git/repo.git/tree/' + file, runScripts: 'outside-only' });
  const w = dom.window;
  w.document.querySelector('td.lines code').textContent = text;
  const seen = [];
  w.hljs = { getLanguage: (l) => KNOWN.includes(l), highlightBlock: (el) => seen.push({ cls: el.className, html: el.innerHTML }) };
  w.eval(script);
  // Highlighting waits for DOMContentLoaded, as it would in a browser.
  return new Promise((resolve) => setTimeout(() => resolve({ w, seen }), 50));
}
(async () => {
let r = await page('irate_box/hub/server.py', 'def f():\n    return "<b>"\n');
check('a .py file: highlighted as python', r.seen.length === 1 && r.seen[0].cls === 'language-py', JSON.stringify(r.seen));
check('the code is text: "<b>" stays escaped in the element', r.seen[0] && r.seen[0].html.includes('&lt;b&gt;') && !r.w.document.querySelector('td.lines code b'));
r = await page('install.sh', 'echo hi');
check('a .sh file: bash', r.seen[0] && r.seen[0].cls === 'language-bash', JSON.stringify(r.seen));
r = await page('src/main.h', 'int x;');
check('a .h file: cpp', r.seen[0] && r.seen[0].cls === 'language-cpp', JSON.stringify(r.seen));
r = await page('Makefile', 'all:\n\ttrue');
check('a Makefile, by its name', r.seen[0] && r.seen[0].cls === 'language-makefile', JSON.stringify(r.seen));
r = await page('notes.zzz', 'something');
check('an unknown kind: highlight.js guesses (no language class)', r.seen.length === 1 && r.seen[0].cls === '', JSON.stringify(r.seen));
r = await page('dump.py', 'x = 1\n'.repeat(100000));
check('a file over 512 KB stays plain', r.seen.length === 0);
const dom = new JSDOM('<!DOCTYPE html><body><div id="cgit"><table class="list"></table></div></body>', { runScripts: 'outside-only' });
let called = false;
dom.window.hljs = { getLanguage: () => true, highlightBlock: () => { called = true; } };
dom.window.eval(script);
await new Promise((resolve) => setTimeout(resolve, 50));
check('a page with no code (an index, a log) is left alone', !called);
console.log(fails ? `${fails} failure(s)` : 'ok');
process.exit(fails ? 1 : 0);
})();
