// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// app.html frames only a path on the hub's own origin: web/app.js's
// target(), run against hashes a browser's URL parser reads as another site. Plain node.
// Usage: node dom-apptarget.cjs
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'app.js'), 'utf8');
const body = src.slice(src.indexOf('function target()'), src.indexOf('\n}\n', src.indexOf('function target()')) + 2);
let fails = 0;
function check(name, ok, info) { console.log((ok ? 'PASS ' : 'FAIL ') + name + (ok ? '' : '  ' + (info || ''))); if (!ok) fails++; }
function target(hash) {
  const location = { hash, origin: 'http://box.local' };
  return new Function('location', 'URL', body + '\nreturn target();')(location, URL);
}
check('a path on the hub', target('#/draw/') === '/draw/');
check('with a query and hash', target('#/wiki/a?b=1#c') === '/wiki/a?b=1#c');
for (const evil of ['#//evil.example/', '#/%5Cevil.example/', '#/\\evil.example/', '#/%09/evil.example/', '#http://evil.example/', '#%2F%2Fevil.example']) {
  const got = target(evil);
  check(`${JSON.stringify(evil)} stays on the hub`, !got.includes('evil.example') || new URL(got, 'http://box.local').origin === 'http://box.local', got);
}
check('a broken escape: home', target('#/%E0%A4%A') === '/');
console.log(fails ? `${fails} failure(s)` : 'ok');
process.exit(fails ? 1 : 0);
