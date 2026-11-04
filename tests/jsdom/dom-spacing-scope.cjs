// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The spacing audit's scope: every admin pane at 1280 and 390, with the background art skipped rather than the page
// that names it, and a settings group treated as a card. The spacing itself is measured in Chromium by
// tests/browser/spacing-audit.sh (CI's spacing job); this test guards the audit's coverage, not its pixels.
const fs = require('fs');
const path = require('path');
const rules = fs.readFileSync(path.resolve(__dirname, '../browser/spacing-rules.js'), 'utf8');
const audit = fs.readFileSync(path.resolve(__dirname, '../browser/spacing-audit.cjs'), 'utf8');
let fails = 0;
const check = (name, cond, info = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`);
  fails += !cond;
};

check('the audit measures every admin pane', audit.includes("document.querySelectorAll('.admin-pane[id]')"));
check('the audit runs at a desktop width and a phone\'s', audit.includes("'1280 390'"));
check('the audit walks every page the menu builds', audit.includes('AL.pages()'));
check(
  'the art is skipped, not the page that names it',
  rules.includes("const SKIP = 'script, style, template, .admin-art, .krab';") && !rules.includes(", [data-art]'"),
);
check('a settings group is a card', rules.includes("'.settings',"));
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
