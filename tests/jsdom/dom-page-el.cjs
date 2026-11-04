// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin pages' one element builder (AW.el): dataset, role and aria-* written where they belong, and the podcast
// and library drive pages drawing their forms (both once threw "Cannot set property dataset" in a local el()).
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push(e.message); });
const dom = new JSDOM('<!doctype html><div id="podcasts-panel"></div><div id="library-drive-panel"></div>', {
  url: 'http://box/admin/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true,
});
const w = dom.window, d = w.document;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
w.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));

// AW.el: a button with no class is an action button; dataset, role and aria-* are real.
const made = w.AW.el('button', { type: 'button', textContent: 'Go', dataset: { wait: 'x:1' }, role: 'button', 'aria-label': 'Go now' });
check('AW.el gives a bare button the action-btn class', made.className === 'action-btn' && made.textContent === 'Go');
check('  dataset is written through node.dataset', made.dataset.wait === 'x:1');
check('  role and aria-* are attributes', made.getAttribute('role') === 'button' && made.getAttribute('aria-label') === 'Go now');

// No page script hands dataset to a bare Object.assign(document.createElement(...)).
const bare = [];
for (const file of fs.readdirSync(WEB).filter((f) => f.endsWith('.js'))) {
  const text = fs.readFileSync(`${WEB}/${file}`, 'utf8');
  const re = /Object\.assign\(document\.createElement\([^)]*\)\s*,\s*\{[^}]*\}/gs;
  for (const m of text.matchAll(re)) if (/\bdataset\s*:/.test(m[0])) bare.push(file);
}
check('no page script sets dataset through a bare Object.assign', bare.length === 0, bare.join(', '));

const podcasts = {
  settings: { enabled: true, check_every_hours: 24, back_catalogue: -1, keep_count: 0, keep_days: 0, keep_mb: 0,
    cap_mb: 0, leave_free_mb: 512, max_episode_mb: 4096, keep_played: false, video: false },
  feeds: [], used: 0, free_mb: 1000, running: false,
};
const drive = { uuid: '', drives: [], kinds: { podcasts: 'card', books: 'card' }, pending: [], results: [] };
const answer = (url) => url.startsWith('/admin/podcasts') ? podcasts : drive;
w.fetch = async (url) => ({ ok: true, json: async () => answer(String(url)) });
w.eval(fs.readFileSync(`${WEB}/admin-podcasts.js`, 'utf8'));
w.eval(fs.readFileSync(`${WEB}/admin-librarydrive.js`, 'utf8'));

setTimeout(() => {
  const podcastText = d.getElementById('podcasts-panel').textContent;
  const driveText = d.getElementById('library-drive-panel').textContent;
  check('the podcast page draws its settings form, not a dataset fault',
    /Follow podcasts/.test(podcastText) && /Save/.test(podcastText) && /fault in the page/.test(podcastText) === false,
    podcastText.slice(0, 200));
  check('the library drive page draws its form, not a dataset fault',
    /Where each kind is kept/.test(driveText) && /Save drive and places/.test(driveText) && /fault in the page/.test(driveText) === false,
    driveText.slice(0, 200));
  check('no page errors', !errors.length, errors.join('; '));
  process.exit(fails ? 1 : 0);
}, 250);
