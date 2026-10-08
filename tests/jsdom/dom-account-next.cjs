// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// /account.html's ?next= (where a login goes on to): a path on this origin only. The expression is
// taken from account.js and run against each address as a browser would read it (it drops tabs and
// newlines, so "/<tab>/elsewhere" is "//elsewhere": CodeQL's open-redirect alert, 2026-10-08).
// Usage: node dom-account-next.cjs
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(require('path').resolve(__dirname, '../../web/account.js'), 'utf8');
const m = src.match(/const next = (\(\(\) => \{[\s\S]*?\n\}\)\(\));/);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
check('account.js still has its next expression', !!m);
const next = (raw) => vm.runInNewContext(m[1], {
  location: { search: `?next=${encodeURIComponent(raw)}`, origin: 'http://box.local' }, URLSearchParams, URL,
});
for (const [raw, want] of [
  ['/draw/', '/draw/'],
  ['/admin/settings?x=1#top', '/admin/settings?x=1#top'],
  ['', ''],
  ['https://elsewhere.example/', ''],
  ['//elsewhere.example/', ''],
  ['/\\elsewhere.example/', ''],
  ['/\t/elsewhere.example/', ''],
  ['/\n/elsewhere.example/', ''],
  ['javascript:alert(1)', ''],
]) check(`next=${JSON.stringify(raw)} → ${JSON.stringify(want)}`, m && next(raw) === want, m && next(raw));
process.exit(fails ? 1 : 0);
