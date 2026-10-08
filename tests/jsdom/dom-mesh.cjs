// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The mesh pages (next-work plan step 18) in jsdom, static: /mesh.html (the nodes, the traffic by
// kind, recent packets, never a message; the page when its owner hasn't made it public) and
// /admin → Mesh (the bridge's state, the channels without their keys, adding one and the public
// LongFast, removing, and the messages). Usage: [JSDOM=…/jsdom] node dom-mesh.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const strip = (h) => h;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 200) => new Promise((r) => setTimeout(r, ms));
const now = Math.floor(Date.now() / 1000);
const nodes = [{ id: '!433d0a1c', long_name: 'Base camp', short_name: 'BASE', last: now - 60, packets: 12, hops: 1, snr: 6.25, rssi: -97,
  telemetry: { battery: 87, voltage: 4.05 }, position: { lat: 51.50071, lon: -0.12462, alt: 25 } },
  { id: '!deadbeef', last: now - 7200, packets: 3 }];
const packets = [{ at: now - 30, from: '!433d0a1c', to: 'all', port: 'text', channel: 'LongFast', text: 'meet at the gate' },
  { at: now - 60, from: '!deadbeef', to: 'all', port: 'encrypted', channel: 'Family' }];
function page(file, url, fetcher, scripts) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
  vc.on('error', (...a) => { if (!/HTTP 404/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
  const dom = new JSDOM(strip(fs.readFileSync(`${WEB}/${file}`, 'utf8')), { url, runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  dom.window.fetch = fetcher;
  scripts.forEach((s) => dom.window.eval(fs.readFileSync(`${WEB}/${s}`, 'utf8')));
  return { w: dom.window, d: dom.window.document, errors };
}
const json = (b, status = 200) => new Response(JSON.stringify(b), { status });
(async () => {
  const publicView = { state: 'listening', nodes, since: now - 3600, counts: { text: 1, encrypted: 1, position: 4 },
    packets: packets.map(({ text, ...p }) => p) };
  let pub = page('mesh.html', 'http://box.local/mesh.html', async (u) => (u === '/mesh.json' ? json(publicView) : json({}, 404)), ['mesh.js']);
  await wait();
  let d = pub.d;
  check('/mesh.html: listening, the node count', /Listening to the box's MQTT broker: 2 nodes heard in the last 7 days\./.test(t(d.getElementById('mesh-state'))));
  check('  the traffic by kind, most first', /4 positions, 1 messages, 1 encrypted \(no key here\)\./.test(t(d.getElementById('mesh-counts'))), t(d.getElementById('mesh-counts')));
  const row = [...d.querySelectorAll('#mesh-nodes tbody tr')][0];
  // 60 or 61 s: the fixture's clock is read a moment before the page renders.
  check('  a node: its name, when heard, battery, where, how heard, packets', /^Base camp \(BASE\) \| 6[01] s ago \| 87 %, 4\.05 V \| 51\.5007, -0\.1246, 25 m \| 1 hop, SNR 6\.25, -97 dBm \| 12$/
    .test([...row.cells].map((c) => t(c)).join(' | ')), [...row.cells].map((c) => t(c)).join(' | '));
  check('  one never named: its id, the rest blank', [...[...d.querySelectorAll('#mesh-nodes tbody tr')][1].cells].map((c) => t(c)).join(' | ') === '!deadbeef | 2 h ago | — | — | — | 3');
  check('  recent traffic by name, never a message', /text from Base camp \(BASE\) on LongFast/.test(t(d.getElementById('mesh-packets'))) && !/meet at the gate/.test(d.body.textContent));
  // The map: placed as they are, no tiles. Add a node 1 km east, and one at 0,0 (no fix): left out.
  const mapView = Object.assign({}, publicView, { nodes: [...nodes, { id: '!00000001', short_name: 'EAST', last: now, packets: 1,
    position: { lat: 51.50071, lon: -0.12462 + 1000 / (111320 * Math.cos(51.5 * Math.PI / 180)) } },
    { id: '!00000002', short_name: 'NOFIX', last: now, packets: 1, position: { lat: 0, lon: 0 } }] });
  pub = page('mesh.html', 'http://box.local/mesh.html', async (u) => (u === '/mesh.json' ? json(mapView) : json({}, 404)), ['mesh.js']);
  await wait();
  const dots = [...pub.d.querySelectorAll('#mesh-map .mesh-map-node')];
  check('the map: the nodes with a position, by short name; one at 0,0 (no fix) left out', dots.length === 2 && dots.map((g) => t(g.querySelector('text'))).join(',') === 'BASE,EAST'
    && /2 nodes with a position, placed as they are: no map underneath/.test(t(pub.d.getElementById('mesh-map-note'))));
  const [a, b] = dots.map((g) => Number(g.querySelector('circle').getAttribute('cx')));
  const scale = pub.d.querySelector('#mesh-map .mesh-map-scale');
  const len = Number(scale.getAttribute('x2')) - Number(scale.getAttribute('x1'));
  const said = t(pub.d.querySelector('#mesh-map > text'));
  const metres = said.endsWith(' km') ? parseFloat(said) * 1000 : parseFloat(said);
  check('  east is to the right, and the scale bar agrees with the 1 km between them', b > a && Math.abs((b - a) / len * metres - 1000) < 20, `${a} ${b} ${len} ${said}`);
  pub = page('mesh.html', 'http://box.local/mesh.html', async () => json({ error: 'not shown' }, 404), ['mesh.js']);
  await wait();
  check('  not public: says the owner chooses, and where', /not shown here: its owner chooses \(\/admin → Access, the MQTT broker, public\)/.test(t(pub.d.getElementById('mesh-state'))));
  check('public: no page errors', !pub.errors.length, pub.errors.join(' | '));

  // /admin → Mesh.
  let channels = [{ name: 'LongFast', public_key: true, no_key: false }, { name: 'Family', public_key: false, no_key: false }];
  const posted = [];
  const admin = page('admin.html', 'http://box.local/admin/#mesh', async (u, o = {}) => {
    if (o.method === 'POST' && u === '/admin/mesh') { const b = JSON.parse(o.body); posted.push(b); return json({ channels }); }
    if (u === '/admin/mesh') return json({ state: 'listening', nodes, packets, counts: { text: 1 }, since: now, channels });
    return json({}, 404);
  }, ['admin-widgets.js', 'admin-layout.js', 'admin.js']);
  await wait();
  d = admin.d;
  check('admin: a side-bar entry, and the state', [...d.querySelectorAll('.admin-side-list a')].some((a) => a.hash === '#mesh')
    && /Listening to the broker: 2 nodes/.test(t(d.getElementById('mesh-admin-state'))));
  check('  the channels, without their keys', /LongFast Meshtastic's public default key: anyone can read this channel\./.test(t(d.getElementById('mesh-channels')))
    && /Family Its own key \(not shown\)\./.test(t(d.getElementById('mesh-channels'))));
  check('  Heard on a page of its own (item 9): a link, and no messages on /admin', d.getElementById('mesh-open').getAttribute('href') === '/admin/mesh.html'
    && !/meet at the gate/.test(t(d.getElementById('mesh'))));
  const f = d.getElementById('mesh-channel-form');
  f.elements.name.value = 'Hikers'; f.elements.key.value = 'q2Fc9u0aBcDeFgHiJkLmNw==';
  f.dispatchEvent(new admin.w.Event('submit', { cancelable: true })); await wait();
  check('  Add: the name and key sent, the key cleared from the page', posted[0] && posted[0].action === 'add-channel' && posted[0].name === 'Hikers'
    && posted[0].key === 'q2Fc9u0aBcDeFgHiJkLmNw==' && f.elements.key.value === '');
  d.getElementById('mesh-longfast').click(); await wait();
  check('  the public LongFast in one click', posted.some((b) => b.name === 'LongFast' && b.key === 'AQ=='));
  [...d.querySelectorAll('#mesh-channels button')].find((b) => t(b) === 'Remove').click(); await wait();
  check('  Remove', posted.some((b) => b.action === 'remove-channel' && b.name === 'LongFast'));
  check('admin: no page errors', !admin.errors.length, admin.errors.join(' | '));

  // /admin/mesh.html: Heard, the messages too.
  const heard = page('admin-mesh.html', 'http://box.local/admin/mesh.html', async (u) => (u === '/admin/mesh'
    ? json({ state: 'listening', nodes, packets, counts: { text: 1 }, since: now, channels }) : json({}, 404)), ['mesh-heard.js']);
  await wait();
  d = heard.d;
  check('heard: the state and the counts', /Listening to the broker: 2 nodes/.test(t(d.getElementById('mesh-heard-state'))) && /1 text/.test(t(d.getElementById('mesh-heard-counts'))));
  check('  the messages, here only', /“meet at the gate”/.test(t(d.getElementById('mesh-heard-packets'))));
  check('heard: no page errors', !heard.errors.length, heard.errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
