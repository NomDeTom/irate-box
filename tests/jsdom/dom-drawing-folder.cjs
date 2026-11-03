// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Drawing folder on the hub: with two tools and with three, the three-way place of its tile,
// the tickbox that keeps its tools inside it, the one conflict said, and its page listing all four.
// The hub part runs against the hub tests/jsdom/run.sh starts; the settings' words are checked on
// /admin from a fixture. Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-drawing-folder.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE;
const REPO = path.resolve(__dirname, '../..');
const WEB = path.join(REPO, 'web');
const STATE = path.join(process.env.TMPDIR || '', 'state');
const APPS_D = path.join(STATE, 'apps.d');
const CONTROL = path.join(STATE, 'control');
const FOLDERS = path.join(STATE, 'folders.json');
let fails = 0;
const check = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'} ${n}${c ? '' : '  ' + i}`); fails += !c; };
const text = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
async function open(pathname) {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(BASE + pathname, { runScripts: 'dangerously', resources: 'usable', virtualConsole: vc, pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE + pathname), o); w.EventSource = class { close() {} }; } });
  await new Promise((r) => setTimeout(r, 600));
  return dom.window.document;
}
const tiles = (d) => [...d.querySelectorAll('.service-card')].map((c) => ({ name: text(c.querySelector('.name')), href: c.getAttribute('href'), text: text(c) }));
const has = (d, name) => tiles(d).find((t) => t.name === name);
async function arrange(shown, keep) {
  const drawing = {};
  if (shown !== undefined) drawing.shown = shown;
  if (keep !== undefined) drawing.keep = keep;
  const r = await fetch(BASE + '/admin/folders', { method: 'POST', headers: {
    'Content-Type': 'application/json', 'X-Irate-Front': process.env.FRONT_SECRET, 'X-Irate-Admin': '1', Origin: BASE,
  }, body: JSON.stringify({ state: { drawing } }) });
  if (!r.ok) throw new Error(`/admin/folders ${r.status}: ${await r.text()}`);
}
const names = (d) => [...d.querySelectorAll('.item-list .name')].map(text);
const read = (file) => { try { return fs.readFileSync(file, 'utf8'); } catch { return null; } };
const write = (file, body) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, body); };
const before = { folders: read(FOLDERS), access: read(path.join(CONTROL, 'access.json')), apps: new Set(fs.existsSync(APPS_D) ? fs.readdirSync(APPS_D) : []) };
function restore(file, body) { if (body === null) fs.rmSync(file, { force: true }); else write(file, body); }
(async () => {
  try {
    // Two drawing tools: Excalidraw and Mermaid. Hidden by default, both still on the hub.
    let d = await open('/');
    check('two tools, the default: no Drawing tile', !has(d, 'Drawing'));
    check('  Excalidraw and Mermaid show', !!has(d, 'Excalidraw') && !!has(d, 'Mermaid'));
    d = await open('/drawing.html');
    check('  the folder page lists both', names(d).join('|') === 'Excalidraw|Mermaid', names(d).join('|'));

    await arrange('always');
    d = await open('/');
    check('Shown at two: the Drawing tile, with the palette', !!has(d, 'Drawing') && /🎨/.test(has(d, 'Drawing').text) && has(d, 'Drawing').href === '/drawing.html');
    check('  and its tools still on the hub', !!has(d, 'Excalidraw') && !!has(d, 'Mermaid'));

    // A third: draw.io. Shown still, so the folder keeps the three inside it.
    write(path.join(APPS_D, 'drawio.json'), fs.readFileSync(path.join(REPO, 'addons/drawio.json')));
    const access = JSON.parse(before.access || '{}');
    write(path.join(CONTROL, 'access.json'), JSON.stringify({ ...access, drawio: 'public', jspaint: 'public' }));
    d = await open('/');
    check('three tools, Shown: the folder stays, all three tools leave the hub', !!has(d, 'Drawing') && !has(d, 'Excalidraw') && !has(d, 'Mermaid') && !has(d, 'draw.io'), tiles(d).map((t) => t.name).join('|'));
    d = await open('/drawing.html');
    check('  its page lists the three', names(d).join('|') === 'Excalidraw|draw.io|Mermaid', names(d).join('|'));

    await arrange('auto', false);
    d = await open('/');
    check('the tickbox off: the folder goes, its tools are on the hub', !has(d, 'Drawing') && !!has(d, 'Excalidraw') && !!has(d, 'draw.io') && !!has(d, 'Mermaid'));
    await arrange('hidden', true);
    d = await open('/');
    check('Hidden with the tickbox on: the conflict leaves the tools on the hub, no folder', !has(d, 'Drawing') && !!has(d, 'Excalidraw') && !!has(d, 'draw.io') && !!has(d, 'Mermaid'));
    await arrange('always', true);
    d = await open('/');
    check('Shown with the tickbox on: the folder, and its tools inside it', !!has(d, 'Drawing') && !has(d, 'Excalidraw') && !has(d, 'draw.io') && !has(d, 'Mermaid'));

    // A fourth: JS Paint. The folder lists all four.
    write(path.join(APPS_D, 'jspaint.json'), fs.readFileSync(path.join(REPO, 'addons/jspaint.json')));
    d = await open('/drawing.html');
    check('four tools: the folder lists all four', names(d).join('|') === 'Excalidraw|draw.io|JS Paint|Mermaid', names(d).join('|'));
  } finally {
    restore(FOLDERS, before.folders);
    restore(path.join(CONTROL, 'access.json'), before.access);
    for (const f of fs.existsSync(APPS_D) ? fs.readdirSync(APPS_D) : []) if (!before.apps.has(f)) fs.rmSync(path.join(APPS_D, f), { force: true });
  }
  await new Promise((r) => setTimeout(r, 100));

  // The administrator's two settings on the Drawing folder's entry, and the conflict's warning.
  const adminApps = require('./admin-apps-fixture.cjs');
  const adminHtml = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
  const snap = { state: {}, folders: [{ id: 'drawing', title: 'Drawing', keep_members: { min: 3, word: 'drawing tools', shown: 'hidden', keep: true, installed: 3 },
    entries: ['Excalidraw', 'draw.io', 'JS Paint', 'Mermaid'].map((name, i) => ({ href: `/app.html#/drawing/${i}`, name, desc: '', kind: 'page', from: 'draw', hidden: false, extra: false })) }] };
  const posted = [];
  const vc = new VirtualConsole();
  const dom = new JSDOM(adminHtml, { url: 'http://box/admin/#page-app-drawing', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window, ad = w.document;
  w.fetch = async (u, o = {}) => {
    const json = (x, status = 200) => new Response(JSON.stringify(x), { status });
    if (o.method === 'POST' && u === '/admin/folders') { const b = JSON.parse(o.body); posted.push(b); snap.state = b.state; return json(snap); }
    if (u === '/admin/folders') return json(snap);
    if (u === '/admin/apps') return json({ apps: adminApps() });
    return json({}, 404);
  };
  ['admin-widgets.js', 'admin-layout.js', 'admin.js'].forEach((f) => w.eval(fs.readFileSync(`${WEB}/${f}`, 'utf8')));
  await new Promise((r) => setTimeout(r, 300));
  const folder = ad.querySelector('.folder-block[data-folder="drawing"]');
  check('the Drawing entry has the folder\'s place on the hub', !!/The Drawing folder on the hub/.test(text(folder)) && !!folder.querySelector('[data-setting="shown"] .chip-group'), text(folder));
  check('  its three places, Shown, When it holds the drawing tools, Hidden', [...folder.querySelectorAll('[data-setting="shown"] .chip')].map(text).join('|') === 'Shown|When it holds the drawing tools|Hidden');
  check('  the tickbox, and what it takes to act', !!/Show the drawing tools only in the Drawing folder/.test(text(folder)) && /Takes effect while three or more drawing tools are installed: now 3\./.test(text(folder)), text(folder));
  check('  the one conflict, said', /The folder is hidden, so the drawing tools stay on the hub\./.test(text(folder)), text(folder));
  const hidden = [...folder.querySelectorAll('[data-setting="shown"] .chip')].find((b) => text(b) === 'Hidden');
  check('  Hidden chosen to start', hidden.getAttribute('aria-pressed') === 'true');
  [...folder.querySelectorAll('[data-setting="shown"] .chip')].find((b) => text(b) === 'Shown').click();
  await new Promise((r) => setTimeout(r, 50));
  check('  choosing Shown clears the conflict and offers Save', !/The folder is hidden/.test(text(folder)) && !folder.querySelector('.aw-save').disabled, text(folder));
  folder.querySelector('.aw-save').click();
  await new Promise((r) => setTimeout(r, 150));
  check('  Save sends the folder\'s place, with its entries and tickbox', JSON.stringify(posted[0] && posted[0].state.drawing) === JSON.stringify({ hidden: [], order: [], extra: [], shown: 'always', keep: true }), JSON.stringify(posted[0]));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
