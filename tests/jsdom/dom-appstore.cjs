// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The App store page (appstore.py writes it) in jsdom: the filter chips the script adds hide the files that
// do not carry their word, remember their choice, and say when a holder is quiet; the QR draws from the
// address in the page. The page itself is rendered by python3 here on a fixture holder, so no hub runs.
// Usage: [JSDOM=…/jsdom] node dom-appstore.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const REPO = path.resolve(__dirname, '../..');
const WEB = path.join(REPO, 'web');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };

// --- the fixture: a holder with two versions and their files, as the keeper publishes them ---
const crypto = require('crypto');
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
const state = fs.mkdtempSync(path.join(os.tmpdir(), 'domappstore-'));
const holds = path.join(state, 'holds');
const name1 = 'androidApp-fdroid-universal-release.apk';
const name2 = 'androidApp-fdroid-arm64-v8a-release.apk';
fs.mkdirSync(path.join(holds, 'demo', 'v2.0.0'), { recursive: true });
fs.mkdirSync(path.join(holds, 'demo', 'v1.9.0'), { recursive: true });
fs.writeFileSync(path.join(holds, 'demo', 'v2.0.0', 'sha256.json'), JSON.stringify({
  [name1]: [100, 1, sha(name1)], [name2]: [80, 1, sha(name2)],
}));
fs.writeFileSync(path.join(holds, 'demo', 'index.json'), JSON.stringify({
  id: 'demo', title: 'Demo', repo: 'a/b', provider: 'github', visibility: 'public', updated: 'x',
  versions: [
    { tag: 'v2.0.0', channel: 'stable', published: '2026-10-02T00:00:00Z', notes: 'newest', files: [
      { name: name1, size: 45000000, sha256: sha(name1) },
      { name: name2, size: 32000000, sha256: sha(name2) },
    ] },
    { tag: 'v1.9.0', channel: 'pre-release', published: '2026-10-01T00:00:00Z', notes: '', files: [
      { name: name1, size: 44000000, sha256: sha(name1) },
    ] },
  ],
}));
fs.mkdirSync(path.join(state, 'library'), { recursive: true });
fs.writeFileSync(path.join(state, 'library', 'holders.json'), JSON.stringify({
  enabled: true, holders: [{ id: 'demo', title: 'Demo', repo: 'a/b', holds: [{ label: 'h', include: 'apk$' }], visibility: 'public' }],
}));
const html = execFileSync('python3', ['-c', `
import sys, os
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.hub import appstore
sys.stdout.buffer.write(appstore.page('box.example', 'http', False))
`], { env: { ...process.env, HUB_STATE_DIR: state, HUB_HOLDS_ROOT: holds } }).toString();

const errors = [];
function page(storage) {
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/scrollTo|navigation/.test(e.message)) errors.push(e.message); });
  const dom = new JSDOM(html, { url: 'http://box.example/appstore/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  if (storage) for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
  w.eval(fs.readFileSync(path.join(WEB, 'qrcode.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(WEB, 'appstore.js'), 'utf8'));
  return w;
}

const w = page();
const d = w.document;
check('the page renders the holder and its two versions', d.querySelectorAll('.appstore-files').length === 2
  && html.includes(name1) && html.includes(name2));
check('newest first, its notes and digest there', (() => {
  const first = d.querySelector('.appstore-files').querySelector('li');
  return first.textContent.includes(name1) && d.querySelector('.appstore-notes pre').textContent === 'newest'
    && first.querySelector('details').textContent.includes(sha(name1));
})(), d.querySelector('details') && d.querySelector('details').textContent.slice(0, 40));
check('the links are the hub\'s own addresses', d.querySelector('.appstore-files li a').getAttribute('href')
  === `/appstore/files/demo/v2.0.0/${name1}`);

const buttons = [...d.querySelectorAll('#appstore-filters button')];
check('chips appear for the words in the names', buttons.length > 0
  && buttons.some((b) => b.textContent === 'fdroid') && buttons.some((b) => b.textContent === 'arm64-v8a'),
  buttons.map((b) => b.textContent).join(','));
const fdroid = buttons.find((b) => b.textContent === 'fdroid');
const arm = buttons.find((b) => b.textContent === 'arm64-v8a');
fdroid.click();
check('a pressed chip reads as pressed', fdroid.getAttribute('aria-pressed') === 'true');
check('every file is fdroid, so nothing hides',
  [...d.querySelectorAll('.appstore-files li')].every((li) => !li.hidden));
arm.click();
const rows = [...d.querySelectorAll('.appstore-files li')];
check('the arch chip keeps only the arm64 row', rows.filter((li) => !li.hidden).length === 1
  && rows.find((li) => !li.hidden).textContent.includes(name2), rows.map((li) => li.hidden).join(','));
check('  the other list goes quiet', d.querySelectorAll('.appstore-quiet').length === 1);
check('the choice is remembered', JSON.parse(w.localStorage.getItem('appstore-chips')).architecture === 'arm64-v8a');
arm.click();
check('unticked again, all rows are back', [...d.querySelectorAll('.appstore-files li')].every((li) => !li.hidden));

const kept = page({ 'appstore-chips': JSON.stringify({ architecture: 'arm64-v8a' }) });
check('a returning visitor finds the filter on',
  [...kept.document.querySelectorAll('.appstore-files li')].filter((li) => !li.hidden).length === 1
  && [...kept.document.querySelectorAll('#appstore-filters button')].find((b) => b.textContent === 'arm64-v8a').getAttribute('aria-pressed') === 'true');

const qr = d.getElementById('appstore-qr');
check('the QR draws the page address', qr.querySelector('svg, img, canvas') !== null
  && qr.getAttribute('data-url') === 'http://box.example/appstore/');
check('no script errors', errors.length === 0, errors.join(' | '));

fs.rmSync(state, { recursive: true, force: true });
console.log(fails ? `${fails} failure(s)` : 'ok');
process.exit(fails ? 1 : 0);
