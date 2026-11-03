// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin's spacing rules, as the shared stylesheet states them: a card, a list or a form after a pane's words, a
// row of buttons before a list, a form's footer inside the form's own gap, and a card's fields between their boxes.
// jsdom only, and deliberately so: the rules are read from the CSS, not measured as pixels, so a new card cannot sit
// tight without a failing test. The browser audit (tests/browser/spacing-audit.sh) still measures every pane.
// Usage: node dom-spacing.cjs
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
const css = fs.readFileSync(`${WEB}/style.css`, 'utf8').replace(/\s+/g, ' ');
const rules = fs.readFileSync(path.resolve(__dirname, '../browser/spacing-rules.js'), 'utf8');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
let fails = 0;
const check = (name, cond, info = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`);
  fails += !cond;
};
const has = (text) => css.includes(text);

check(
  'a card, a list or a form after a pane\'s words takes the shared gap',
  has('.admin-pane > p + :is(.net-card, .setting, .settings, .aw-block, .library-form) { margin-top: 1rem; }'),
);
check(
  'a list after a pane\'s buttons takes the same gap as a card',
  has('.admin-pane > :is(.library-buttons, .action-note) + :is(.net-card, .setting, .settings, .admin-list) { margin-top: 1rem; }'),
);
check(
  'a form\'s footer and update pattern drop the margin the form\'s own gap gives',
  has('.library-form > .aw-foot { margin-top: 0; }') && has('.library-form > .update-pattern { margin-bottom: 0; }'),
);
check(
  'a card\'s fields keep a line between their boxes',
  has('.aw-field + .aw-field { margin-top: 0.25rem; }'),
);
check(
  'an app\'s maintenance cards and facts keep the card and button bands',
  has('.app-updates + .app-install { margin-top: 0.7rem; }') && has('.library-buttons + .inv-facts { margin-top: 1rem; }'),
);
check(
  'the quick installer stands off the words above it and its own first line at its top',
  has('#quick-installer { margin-top: 0.7rem; }') && has('#quick-installer > :first-child { margin-top: 0; }'),
);
check(
  'the quick installer\'s chips and the setting under them keep the page\'s bands',
  has('.quick-chips + .setting { margin-top: 1rem; }'),
);
check(
  'the Firmware Factory card is a settings group after a pane\'s words',
  html.includes('<div class="settings" data-note="factory-tile-note">')
    && /<p\s+class="setting-desc"\s+data-i18n="admin\.factory\.build-meshtastic-firmware-box"/.test(html),
);
check(
  'Git\'s Mirrors puts its buttons for all before the list the rule spaces',
  html.includes('class="library-buttons update-buttons"') && html.includes('<div id="git-mirrors" class="admin-list"></div>'),
);
check(
  'the audit skips the art itself, not the page that names it',
  rules.includes("const SKIP = 'script, style, template, .admin-art, .krab';") && !rules.includes(", [data-art]'"),
);
check(
  'a settings group is a card to the audit',
  rules.includes("'.settings',"),
);
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
