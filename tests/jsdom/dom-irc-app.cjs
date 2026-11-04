// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// IRC as one app on the hub, in /admin's menu and in Access: with ngIRCd installed and with
// InspIRCd, one tile, one app page and one Access row, the installed member's words and state.
// Against the hub tests/jsdom/run.sh starts. Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-irc-app.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE;
const WEB = path.resolve(__dirname, '../../web');
const OPTIONS = path.join(process.env.TMPDIR || '', 'state', 'install-options');
let fails = 0;
const check = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'} ${n}${c ? '' : '  ' + i}`); fails += !c; };
const text = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
let before = null;
try { before = fs.readFileSync(OPTIONS, 'utf8'); } catch {}

async function open(pathname) {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(BASE + pathname, { runScripts: 'dangerously', resources: 'usable', virtualConsole: vc, pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE + pathname), o); w.EventSource = class { close() {} }; } });
  await new Promise((r) => setTimeout(r, 500));
  return dom.window.document;
}
const headers = () => ({ 'X-Irate-Front': process.env.FRONT_SECRET });
async function json(pathname) {
  const r = await fetch(BASE + pathname, { headers: headers() });
  return r.ok ? r.json() : null;
}
const tiles = (d) => [...d.querySelectorAll('.service-card')].map((c) => ({ name: text(c.querySelector('.name')), desc: text(c.querySelector('.desc')), href: c.getAttribute('href') }));
async function scenario(option, id, member, desc, browser) {
  fs.mkdirSync(path.dirname(OPTIONS), { recursive: true });
  fs.writeFileSync(OPTIONS, option);
  const home = await open('/');
  const t = tiles(home).filter((x) => /^IRC chat/.test(x.name));
  check(`${member}: one IRC tile`, t.length === 1 && t[0].name === 'IRC chat' && t[0].href === '/irc.html', t);
  check('  its words are that member\'s', !!(t[0] && t[0].desc.includes(desc) && t[0].desc.includes('browser') === browser), t[0]);
  const status = await json('/status');
  const ircServices = (status.services || []).filter((s) => /IRC server/.test(s.name));
  check('  one Overview service, named for it', ircServices.length === 1 && ircServices[0].name === member, ircServices);

  const apps = await json('/admin/apps');
  const access = await json('/admin/access');
  const addons = await json('/admin/addons');
  const app = (apps.apps || []).filter((a) => a.group === 'irc');
  const row = (access.apps || []).filter((a) => a.id === app[0].id);
  check('  one /admin app, the server choice its installation', app.length === 1 && app[0].id === id && app[0].name === 'IRC chat' && app[0].addon, app);
  check('  one Access row, the same app', row.length === 1 && row[0].title === 'IRC chat', row);

  const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
  const vc = new VirtualConsole();
  const dom = new JSDOM(html, { url: `http://box/admin/#page-app-${app[0].id}`, runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window, d = w.document;
  w.fetch = async (u) => {
    if (u === '/admin/apps') return new Response(JSON.stringify(apps), { status: 200 });
    if (u === '/admin/access') return new Response(JSON.stringify(access), { status: 200 });
    if (u === '/status') return new Response(JSON.stringify(status), { status: 200 });
    if (u === '/admin/addons') return new Response(JSON.stringify(addons), { status: 200 });
    if (u === '/admin/presets') return new Response(JSON.stringify({ purposes: [], durabilities: [], current: {} }), { status: 200 });
    if (u === '/admin/folders') return new Response(JSON.stringify({ folders: [], state: {} }), { status: 200 });
    if (u === '/admin/tiles') return new Response(JSON.stringify({ tiles: [], state: {} }), { status: 200 });
    if (u === '/admin/status-tiles') return new Response(JSON.stringify({ tiles: [], state: {} }), { status: 200 });
    if (u === '/admin/visibility') return new Response(JSON.stringify({ apps: [], pages: {}, seen: {}, locked_as: {}, results: [] }), { status: 200 });
    if (u === '/admin/local-addons') return new Response(JSON.stringify({ added: [], catalogue: [], errors: {}, job: {}, running: false }), { status: 200 });
    if (u === '/admin/settings') return new Response(JSON.stringify({}), { status: 200 });
    return new Response('{}', { status: 200 });
  };
  ['el.js', 'admin-widgets.js', 'admin-layout.js', 'admin.js'].forEach((f) => w.eval(fs.readFileSync(`${WEB}/${f}`, 'utf8')));
  await new Promise((r) => setTimeout(r, 400));
  const links = [...d.querySelectorAll('.admin-side-list a')].filter((a) => /IRC chat/.test(text(a)));
  check('  one IRC entry in the admin menu', links.length === 1 && links[0].getAttribute('href') === `#page-app-${app[0].id}`, links.map((a) => a.outerHTML));
  const page = d.getElementById(`page-app-${app[0].id}`);
  const block = d.querySelector(`#app-access-${app[0].id}`);
  check('  its page has the one Access', !!page && !!block && /Guests/.test(text(block)) && !!block.querySelector('.chip'), block && text(block));
  const install = d.querySelector(`#app-install-${app[0].id} .addon-group`);
  check('  and the installation offers either server there', !!install && /IRC server \(ngIRCd\)/.test(text(install)) && /IRC server \(InspIRCd\)/.test(text(install)), install && text(install));
}
(async () => {
  try {
    await scenario('--with-irc\n', 'irc', 'IRC server (ngIRCd)', 'Real-time chat in any IRC app', false);
    await scenario('--with-inspircd\n', 'irc', 'IRC server (InspIRCd)', 'Real-time chat in any IRC app, or in the browser', true);
  } finally {
    if (before === null) fs.rmSync(OPTIONS, { force: true });
    else fs.writeFileSync(OPTIONS, before);
  }
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
