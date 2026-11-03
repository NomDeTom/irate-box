// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Debian's other updates and the board's own packages on /admin's Updates pane, in jsdom: each card from its own
// finding (os-updates, board-updates), what waits and what is downloaded, the board's held packages named, the
// packages listed, and each button asking the helper for its own kind's choice. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-update-kinds.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const WEB = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const M = (id, en, args) => ({ id, en, ...(args ? { args } : {}) });
const kinds = {
  at: Math.floor(Date.now() / 1000),
  findings: [
    {
      id: 'os-updates',
      title: M('osupdates.os.title', "Debian's other updates"),
      status: 'warn',
      summary: M('osupdates.os.waiting', '{n} Debian updates are waiting, apart from security\'s.', { n: 2 }),
      why: null,
      fix: null,
      notes: [],
      commands: [],
      evidence: [],
      actions: [],
      waiting: 2,
      fetched: 1,
      packages: ['tzdata', 'ca-certificates'],
    },
    {
      id: 'board-updates',
      title: M('osupdates.board.title', "The board's packages"),
      status: 'warn',
      summary: M('osupdates.board.waiting', '{n} of the board\'s own packages waits behind the hold.', { n: 1 }),
      why: null,
      fix: null,
      notes: [],
      commands: [],
      evidence: [],
      actions: [],
      waiting: 1,
      fetched: 0,
      packages: ['linux-image-vendor-rockchip'],
      held: ['linux-image-vendor-rockchip'],
    },
  ],
};
const scan = { at: Math.floor(Date.now() / 1000), listeners: [], findings: [] };
const sec = { hub: [], accepted: {}, scan, kinds, log: [], pending: 0, results: [], deep: {}, imports: {}, audit: null };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#updates', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts) => {
  if (u !== '/admin/security') return new Response('{}', { status: 404 });
  if (opts && opts.method === 'POST') {
    posted.push(JSON.parse(opts.body));
    return new Response(JSON.stringify({ id: `job-${posted.length}` }), { status: 200 });
  }
  return new Response(JSON.stringify(sec), { status: 200 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 80) => new Promise((r) => setTimeout(r, ms));
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
(async () => {
  await wait();
  w.eval('loadSecurity')();
  await wait();
  const d = w.document;
  const osLines = t(d.getElementById('updates-os-lines'));
  const boardLines = t(d.getElementById('updates-board-lines'));
  check('Debian\'s other updates: what waits and what is downloaded', /2 updates are waiting, 1 downloaded\./.test(osLines), osLines);
  check('  its packages are listed', /2 packages waiting/.test(t(d.getElementById('os-packages'))) && /tzdata/.test(t(d.getElementById('os-packages'))), t(d.getElementById('os-packages')));
  check('the board\'s packages: what waits, and the held names', /1 update is waiting\./.test(boardLines) && /Held: linux-image-vendor-rockchip\./.test(boardLines), boardLines);
  const osInstall = d.querySelector('#os-buttons button[data-update="install"]');
  const boardFetch = d.querySelector('#board-buttons button[data-update="fetch"]');
  osInstall.click();
  boardFetch.click();
  await wait();
  check('each button asks the helper for its own kind\'s choice', posted.map((p) => p.choice).join(',') === 'os-updates,board-fetch', posted);
  check('no page errors', !errors.length, errors.slice(0, 2));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
