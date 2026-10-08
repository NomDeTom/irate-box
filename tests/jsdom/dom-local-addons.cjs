// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin's Web add-ons in jsdom, against fixtures: what is added (with its switch), the
// catalogue (Add asks the consent text first), and pasting a manifest (refused until the
// warning's box is ticked). Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-local-addons.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8') + ';\n' + fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const PIN = '4ac38e6694526f2a5c849014d3312a20cdddf842';
const local = {
  addon_port: 8090, running: false, job: { action: null, result: null }, errors: { 'bad.json': 'bad.json: tile: icon, name and desc' },
  added: [{ id: 'eliza', title: 'ELIZA (chatbot)', summary: 'The 1966 chatbot.', mode: 'off', installed: { commit: PIN, ref: 'pinned', repository: 'https://github.com/anthay/ELIZA' },
    status: {}, pin: PIN, repo: 'https://github.com/anthay/ELIZA', capabilities: { connect: [], storage: false }, from_catalogue: true, consent: { how: 'catalogue' }, href: '/eliza.html',
    catalogue: { status: 'held', at: 1791250000, held: ['now connects to wss://example.org'] } },
    { id: 'notes2', title: 'Notebook', summary: 'A notebook.', mode: 'off', installed: { commit: 'c'.repeat(40), ref: 'pinned' }, status: {}, pin: 'c'.repeat(40),
      repo: 'https://example.org/n', capabilities: {}, from_catalogue: true, consent: { how: 'catalogue' }, href: '/app.html#/addons/notes2/',
      catalogue: { status: 'updated', at: 1791250000, changed: ['source', 'tile'] } },
    { id: 'old', title: 'Old thing', summary: '', mode: 'off', installed: null, status: {}, pin: 'd'.repeat(40), repo: 'https://example.org/o',
      capabilities: {}, from_catalogue: false, consent: { how: 'catalogue' }, href: '/app.html#/addons/old/', catalogue: { status: 'gone', at: 1791250000 } }],
  catalogue: [{ id: 'eliza', title: 'ELIZA (chatbot)', added: true, summary: '', consent: 'ELIZA now talks to example.org. Add it?', repo: '', pin: PIN, capabilities: { connect: ['wss://example.org'] } },
    { id: 'mqttx', title: 'MQTT explorer', added: false, summary: 'Watch the broker.', consent: 'This downloads MQTTX. Add it?', repo: 'https://github.com/emqx/MQTTX', pin: 'b'.repeat(40),
      capabilities: { connect: ['ws://{box}/mqtt'], storage: true } }],
};
const accessData = { apps: [{ id: 'eliza', title: 'ELIZA (chatbot)', mode: 'off', login: false, kind: 'local', unit: false, default: 'off', note: '' }], results: [] };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#addons', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') { posted.push([u, JSON.parse(opts.body), opts.headers]); return new Response('{"added":"x"}', { status: 202 }); }
  if (u === '/admin/local-addons') return new Response(JSON.stringify(local), { status: 200 });
  if (u === '/admin/access') return new Response(JSON.stringify(accessData), { status: 200 });
  if (u === '/admin/addons') return new Response(JSON.stringify({ addons: [], progress: null, pending: 0, results: [], log: [] }), { status: 200 });
  return new Response('{}', { status: 404 });
};
const asked = [];
w.confirm = (text) => { asked.push(text); return true; };
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await sleep(300);
  const d = w.document;
  const added = d.getElementById('local-added');
  const rows = [...d.querySelectorAll('#local-added .library-source')];
  const eliza = rows.find((r) => /ELIZA/.test(r.textContent));
  check('a held catalogue version: said, with what changed', /newer version that changes what you agreed to: now connects to wss:\/\/example\.org/.test(eliza.textContent), eliza.textContent);
  const acceptBtn = [...eliza.querySelectorAll('button')].find((b) => b.textContent === 'Accept the new version');
  check('…and an Accept button', !!acceptBtn);
  check('an updated one: what changed', /Updated from the catalogue on .*: source, tile\./.test(rows.find((r) => /Notebook/.test(r.textContent)).textContent));
  check('one gone from the catalogue: said', /No longer in the catalogue/.test(rows.find((r) => /Old thing/.test(r.textContent)).textContent));
  acceptBtn.click();
  await sleep(100);
  check('Accept asks the new consent text, with the changes', asked.some((t) => t.startsWith('ELIZA now talks to example.org.') && /What changed: now connects to wss:\/\/example\.org/.test(t)), asked.join(' | '));
  const acc = posted.find(([u, b]) => u === '/admin/local-addons' && b.action === 'accept');
  check('Accept posts it, agreed', acc && acc[1].id === 'eliza' && acc[1].agree === true, JSON.stringify(acc));
  asked.length = 0;
  check('added: ELIZA, installed at the pinned commit', /ELIZA/.test(added.textContent) && /Installed: 4ac38e6 \(the pinned commit\)/.test(added.textContent), added.textContent);
  check('added: its access switch in its row', added.querySelectorAll('.access-toggle button').length === 3);
  check('added: Open and Remove', [...added.querySelectorAll('a, button')].some((b) => b.textContent === 'Open') && [...added.querySelectorAll('button')].some((b) => b.textContent === 'Remove'));
  const cat = d.getElementById('local-catalogue');
  check('catalogue: only what is not added', /MQTT explorer/.test(cat.textContent) && !/ELIZA/.test(cat.textContent), cat.textContent);
  check('catalogue: what it connects to', /ws:\/\/\{box\}\/mqtt/.test(cat.textContent));
  check('a left-out manifest is said', /Left out: bad.json/.test(d.getElementById('local-errors').textContent));
  [...cat.querySelectorAll('button')].find((b) => b.textContent === 'Add').click();
  await sleep(100);
  check('Add asks the consent text first', asked.length === 1 && asked[0].startsWith('This downloads MQTTX.') && /starts off/.test(asked[0]), asked[0]);
  const add = posted.find(([u, b]) => u === '/admin/local-addons' && b.action === 'add');
  check('Add posts the id, agreed, with the admin header', add && add[1].id === 'mqttx' && add[1].agree === true && add[2]['X-Irate-Admin'] === '1', JSON.stringify(add));
  check('the paste warning is there', /Only do this with a manifest you have read/.test(d.querySelector('#local-paste .danger-box').textContent));
  d.getElementById('local-paste-text').value = JSON.stringify({ id: 'x', addon: { title: 'X', consent: 'Take it?' } });
  d.getElementById('local-paste-go').click(); await sleep(50);
  check('paste refused until the box is ticked', !posted.some(([, b]) => b.action === 'paste') && /tick the box/.test(d.getElementById('local-paste-note').textContent));
  d.getElementById('local-paste-ok').checked = true;
  d.getElementById('local-paste-go').click(); await sleep(100);
  const paste = posted.find(([, b]) => b.action === 'paste');
  check('ticked: posts it with the acknowledgement', paste && paste[1].understood === "I understand this runs someone else's code on this box's address" && paste[1].manifest.id === 'x');
  check('no page errors', errors.length === 0, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
