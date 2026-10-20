// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A page test run as it is, each page it made saved as jsdom left it (its fixtures drawn, its scripts taken out), for a
// real browser to draw and measure (tests/browser/card-audit.sh). Usage: node dump-page.cjs TEST.cjs OUT_DIR
const path = require('path');
const fs = require('fs');
const where = require.resolve(process.env.JSDOM || 'jsdom');
const jsdom = require(where);
const made = [];
const Real = jsdom.JSDOM;
jsdom.JSDOM = class extends Real {
  constructor(...args) {
    super(...args);
    made.push(this);
  }
};
require.cache[where].exports = jsdom;
const [test, out] = process.argv.slice(2);
const exit = process.exit;
process.exit = (code) => {
  made.forEach((dom, i) => {
    const d = dom.window.document;
    d.querySelectorAll('script').forEach((s) => s.remove());
    fs.writeFileSync(path.join(out, `${path.basename(test, '.cjs')}-${i}.html`), `<!doctype html>\n${d.documentElement.outerHTML}`);
  });
  exit(code);
};
process.argv = [process.argv[0], test];
require(path.resolve(test));
