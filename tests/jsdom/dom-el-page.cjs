// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The element builder is its own small file: every page that uses it loads web/el.js, and a page outside /admin
// never loads the whole admin-widgets.js just for el().
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const pages = fs.readdirSync(WEB).filter((f) => f.endsWith('.html'));
const guest = pages.filter((f) => !f.startsWith('admin'));
check('a guest page does not load admin-widgets.js', guest.every((f) => !fs.readFileSync(`${WEB}/${f}`, 'utf8').includes('/admin-widgets.js')), guest.filter((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8').includes('/admin-widgets.js')));
for (const f of guest) {
  const text = fs.readFileSync(`${WEB}/${f}`, 'utf8');
  const uses = /\/locks\.js|\/addon-bridge\.js|\/mesh-heard\.js/.test(text);
  if (uses) check(`  ${f} loads el.js`, text.includes('/el.js'));
}
for (const f of pages.filter((x) => x.startsWith('admin'))) {
  const text = fs.readFileSync(`${WEB}/${f}`, 'utf8');
  if (!text.includes('/admin-widgets.js')) continue;
  const el = text.indexOf('/el.js');
  const widgets = text.indexOf('/admin-widgets.js');
  check(`  ${f} loads el.js before admin-widgets.js`, el >= 0 && el < widgets);
}
check('el.js is the element builder', /window\.HubEl\s*=\s*\{\s*el\s*\}/.test(fs.readFileSync(`${WEB}/el.js`, 'utf8')));
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
