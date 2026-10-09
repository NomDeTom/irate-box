// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The update badge (Tom, 2026-10-09) in jsdom: "update available" when a check found one, "up to
// date" only when found so within a week, how old an older look is, a failed or missing check.
// Static: no hub needed. Usage: [JSDOM=…/jsdom] node dom-update-badges.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only' });
dom.window.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));
const { updatePill } = dom.window.AW;
const now = Date.now() / 1000;
const said = (p) => `${p.className}: ${p.textContent}`;
check('found one: update available', said(updatePill(now, true)) === 'warn-pill update-pill: update available', said(updatePill(now, true)));
check('found one, even on an old look', updatePill(now - 30 * 86400, true).textContent === 'update available');
check('nothing newer, today: up to date', said(updatePill(now - 3600, false)) === 'ok-pill update-pill: up to date', said(updatePill(now - 3600, false)));
check('an ISO time is read too', updatePill(new Date(Date.now() - 86400e3).toISOString().replace(/\.\d+Z$/, 'Z'), false).textContent === 'up to date');
check('nothing newer, but a fortnight ago: says how old', said(updatePill(now - 14 * 86400, false)) === 'info-pill update-pill: checked 14 days ago',
  said(updatePill(now - 14 * 86400, false)));
check('its last look failed', said(updatePill(now, false, true)) === 'bad-pill update-pill: check failed');
check('never looked', said(updatePill(null, false)) === 'info-pill update-pill: not checked yet');
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
