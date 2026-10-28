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
const side1 = `${name1}.sha256`;
fs.mkdirSync(path.join(holds, 'demo', 'v2.0.0'), { recursive: true });
fs.mkdirSync(path.join(holds, 'demo', 'v1.9.0'), { recursive: true });
fs.writeFileSync(path.join(holds, 'demo', 'v2.0.0', side1), 'checksum');
fs.writeFileSync(path.join(holds, 'demo', 'v2.0.0', 'sha256.json'), JSON.stringify({
  [name1]: [100, 1, sha(name1)], [name2]: [80, 1, sha(name2)], [side1]: [8, 1, sha(side1)],
}));
fs.writeFileSync(path.join(holds, 'demo', 'index.json'), JSON.stringify({
  id: 'demo', title: 'Demo', repo: 'a/b', provider: 'github', visibility: 'public', updated: 'x',
  versions: [
    { tag: 'v2.0.0', channel: 'stable', published: '2026-10-02T00:00:00Z', notes: 'newest', files: [
      { name: name1, size: 45000000, sha256: sha(name1) },
      { name: name2, size: 32000000, sha256: sha(name2) },
      { name: side1, size: 8, sha256: sha(side1), of: name1 },
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
fs.writeFileSync(path.join(state, 'library', 'firmware.json'), JSON.stringify({ enabled: true }));
// The firmware mirror's own layout: one version, two boards, their files and digests.
const fw = '2.8.2.abc1234';
const fwDir = path.join(state, 'firmware', fw);
fs.mkdirSync(fwDir, { recursive: true });
const fwFiles = {
  rak4631: [['firmware-rak4631-2.8.2.abc1234.uf2', null], ['firmware-rak4631-2.8.2.abc1234.hex', null]],
  'heltec-v3': [['firmware-heltec-v3-2.8.2.abc1234.factory.bin', null], ['littlefs-heltec-v3-2.8.2.abc1234.bin', 'spiffs']],
};
const digests = {};
for (const [board, files] of Object.entries(fwFiles)) {
  const entries = files.map(([name, part]) => {
    const data = `${fw}:${name}`;
    fs.writeFileSync(path.join(fwDir, name), data);
    digests[name] = [data.length, 1, sha(data)];
    return { name, bytes: data.length, md5: sha(data), ...(part ? { part_name: part } : {}) };
  });
  fs.writeFileSync(path.join(fwDir, `firmware-${board}-${fw}.mt.json`), JSON.stringify({
    displayName: board === 'rak4631' ? 'RAK WisBlock 4631' : 'Heltec V3',
    architecture: board === 'rak4631' ? 'nrf52840' : 'esp32-s3', build_epoch: 1792886400, files: entries,
  }));
}
fs.writeFileSync(path.join(fwDir, 'sha256.json'), JSON.stringify(digests));
fs.writeFileSync(path.join(state, 'firmware', 'status.json'), JSON.stringify({
  versions: { [fw]: { channel: 'beta', boards: Object.keys(fwFiles), files: 4, bytes: 4000 } },
  last_check: '2026-10-28T00:00:00Z',
}));
fs.writeFileSync(path.join(state, 'firmware', 'index.json'), JSON.stringify({
  releases: { stable: [{ id: `v${fw}`, release_notes: 'Firmware notes as text.' }], alpha: [] },
}));
const html = execFileSync('python3', ['-c', `
import sys, os
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.hub import appstore
sys.stdout.buffer.write(appstore.page('box.example', 'http', False, True))
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
const demo = () => d.querySelector('section[data-holder="demo"]');
check('the page renders the holder and its two versions', demo().querySelectorAll(':scope > .appstore-files').length === 2
  && html.includes(name1) && html.includes(name2));
check('newest first, its notes and digest there', (() => {
  const first = d.querySelector('.appstore-files').querySelector('li');
  return first.textContent.includes(name1) && d.querySelector('.appstore-notes pre').textContent === 'newest'
    && first.querySelector('details').textContent.includes(sha(name1));
})(), d.querySelector('details') && d.querySelector('details').textContent.slice(0, 40));
check('a companion is listed under its file, not beside it', demo().querySelector(`li > .appstore-companions [href$="${side1}"]`) !== null
  && demo().querySelector('.appstore-companions li').parentElement.closest('li') === demo().querySelector('.appstore-files > li'),
  demo().querySelector('.appstore-companions') && demo().querySelector('.appstore-companions').outerHTML.slice(0, 120));
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
  [...demo().querySelectorAll('.appstore-files li')].every((li) => !li.hidden));
arm.click();
const rows = [...demo().querySelectorAll('.appstore-files li')];
check('the arch chip keeps only the arm64 row', rows.filter((li) => !li.hidden).length === 1
  && rows.find((li) => !li.hidden).textContent.includes(name2), rows.map((li) => li.hidden).join(','));
check('  the other list goes quiet', demo().querySelectorAll('.appstore-quiet').length === 1);
check('the choice is remembered', JSON.parse(w.localStorage.getItem('appstore-chips')).architecture === 'arm64-v8a');
arm.click();
check('unticked again, all rows are back', [...demo().querySelectorAll('.appstore-files li')].every((li) => !li.hidden));

const boards = [...d.querySelectorAll('details.appstore-board')];
check('the firmware boards are folded by default', boards.length === 2 && boards.every((board) => !board.open),
  boards.map((board) => board.open).join(','));
const boardFilter = d.getElementById('appstore-board-filter');
check('the board filter is added by the script', boardFilter !== null);
boardFilter.value = 'heltec';
boardFilter.dispatchEvent(new w.Event('input'));
check('  typing a board hides the others', boards.filter((board) => !board.hidden).length === 1
  && boards.find((board) => !board.hidden).getAttribute('data-board') === 'heltec-v3',
  boards.map((board) => `${board.getAttribute('data-board')}:${board.hidden}`).join(','));
boardFilter.value = 'nrf52840';
boardFilter.dispatchEvent(new w.Event('input'));
check("  and a chip's word finds it too", boards.find((board) => !board.hidden).getAttribute('data-board') === 'rak4631');
boardFilter.value = '';
boardFilter.dispatchEvent(new w.Event('input'));
check('  an empty filter shows every board', boards.every((board) => !board.hidden));

const kept = page({ 'appstore-chips': JSON.stringify({ architecture: 'arm64-v8a' }) });
check('a returning visitor finds the filter on',
  [...kept.document.querySelectorAll('section[data-holder="demo"] .appstore-files li')].filter((li) => !li.hidden).length === 1
  && [...kept.document.querySelectorAll('#appstore-filters button')].find((b) => b.textContent === 'arm64-v8a').getAttribute('aria-pressed') === 'true');

const qr = d.getElementById('appstore-qr');
check('the QR draws the page address', qr.querySelector('svg, img, canvas') !== null
  && qr.getAttribute('data-url') === 'http://box.example/appstore/');
check('no script errors', errors.length === 0, errors.join(' | '));

fs.rmSync(state, { recursive: true, force: true });
console.log(fails ? `${fails} failure(s)` : 'ok');
process.exit(fails ? 1 : 0);
