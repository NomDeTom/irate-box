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
const js = ['el.js', 'admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
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
  const pills = (id) => [...d.querySelectorAll(`#${id}-chips .info-pill, #${id}-chips .warn-pill`)].map((c) => c.textContent);
  check('the states are info pills, not choice chips', pills('os').join('|') === '2 waiting|1 downloaded'
    && pills('board').join('|') === '1 waiting|None downloaded|1 held'
    && !d.querySelector('#updates-os .chip') && !d.querySelector('#updates-board .chip'), [pills('os'), pills('board')]);
  const osInstall = d.querySelector('#os-buttons button[data-update="install"]');
  const boardFetch = d.querySelector('#board-buttons button[data-update="fetch"]');
  osInstall.click();
  boardFetch.click();
  await wait();
  check('each button asks the helper for its own kind\'s choice', posted.map((p) => p.choice).join(',') === 'os-updates,board-fetch', posted);
  // Nothing waiting, six board packages held: one info pill for the hold, and Install plainly disabled.
  const held = ['linux-image-vendor-rockchip', 'linux-dtb-vendor-rockchip', 'linux-u-boot-luckfox-lyra', 'armbian-bsp-cli-luckfox-lyra', 'armbian-firmware', 'armbian-config'];
  sec.kinds = { at: Math.floor(Date.now() / 1000), findings: [
    { ...kinds.findings[0], status: 'ok', summary: M('osupdates.os.none', 'No other Debian update is waiting.'), waiting: 0, fetched: 0, packages: [] },
    { ...kinds.findings[1], status: 'ok', summary: M('osupdates.board.ok', '{n} board packages are held, and none waits for an update.', { n: held.length }), waiting: 0, fetched: 0, packages: [], held }] };
  await w.eval('loadSecurity()');
  await wait();
  check('nothing waiting, six held: the hold is an info pill, not \'Not held\'', pills('board').join('|') === 'None waiting|None downloaded|6 held'
    && /Held: linux-image-vendor-rockchip, .*armbian-config\./.test(t(d.getElementById('updates-board-lines'))), [pills('board'), t(d.getElementById('updates-board-lines'))]);
  const install = d.querySelector('#board-buttons button[data-update="install"]');
  check('  Install is disabled and looks it', install.disabled && install.classList.contains('idle'));
  // No report at all: one pill says so, and the line says what to do.
  sec.kinds = null;
  await w.eval('loadSecurity()');
  await wait();
  check('no report: one \'Not checked yet\' pill, and the words say how to check', pills('os').join('|') === 'Not checked yet'
    && /^Not checked yet\. Select Check now\.$/.test(t(d.getElementById('updates-os-lines'))), [pills('os'), t(d.getElementById('updates-os-lines'))]);
  check('no report, the board: the holds are not known yet, never \'Not held\'', pills('board').join('|') === 'Not checked yet'
    && /Holds: not known yet\./.test(t(d.getElementById('updates-board-lines')))
    && !/Not held/.test(t(d.getElementById('updates-board-lines'))), [pills('board'), t(d.getElementById('updates-board-lines'))]);
  check('no page errors', !errors.length, errors.slice(0, 2));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
