// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The MQTT explorer (extras/mqtt-explorer) in jsdom, with a stand-in
// WebSocket playing the broker: it connects to ws://<the hub's host>/mqtt and subscribes once the
// broker accepts; packets split across messages and run together are read; topics are counted
// and listed, a topic's messages shown as JSON, text or hex; the filter, Pause and Clear; a
// refused topic and a broker that is not there are said; it never publishes.
// Usage: [JSDOM=…/jsdom] node dom-mqtt-explorer.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const DIR = require('path').resolve(__dirname, '../../extras/mqtt-explorer');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${DIR}/index.html`, 'utf8');
const js = fs.readFileSync(`${DIR}/explorer.js`, 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push('jsdom: ' + e.message));
vc.on('error', (...a) => errors.push('console: ' + a.join(' ')));
const dom = new JSDOM(html, { url: 'http://box.local:8090/mqtt-explorer/index.html?hub-theme=light', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
const sockets = [];
class FakeWS {
  constructor(url, protocols) { this.url = url; this.protocols = protocols; this.sent = []; this.readyState = 0; sockets.push(this); }
  send(b) { this.sent.push([...b]); }
  close() { this.readyState = 3; this.onclose && this.onclose(); }
  open() { this.readyState = 1; this.onopen(); }
  feed(bytes) { this.onmessage({ data: new Uint8Array(bytes).buffer }); }
}
w.WebSocket = FakeWS;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 300) => new Promise((r) => setTimeout(r, ms));
const str = (s) => { const b = [...Buffer.from(s)]; return [b.length >> 8, b.length & 255, ...b]; };
const pkt = (type, body) => {
  const len = []; let n = body.length;
  do { let x = n % 128; n = Math.floor(n / 128); if (n) x |= 128; len.push(x); } while (n);
  return [type, ...len, ...body];
};
const publish = (topic, payload, flags = 0) => pkt(0x30 | flags, [...str(topic), ...(flags & 6 ? [0, 7] : []), ...payload]);
(async () => {
  await wait(50);
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const ws = sockets[0];
  check('connects to the hub\'s own host on its usual port, at /mqtt, as MQTT', ws && ws.url === 'ws://box.local/mqtt' && JSON.stringify(ws.protocols) === '["mqtt"]', ws && ws.url);
  check('the theme from the hub bar', d.documentElement.dataset.theme === 'light');
  ws.open();
  const c = ws.sent[0];
  check('CONNECT: MQTT 3.1.1, a clean session, a client id of its own', c[0] === 0x10 && Buffer.from(c.slice(4, 8)).toString() === 'MQTT' && c[8] === 4 && c[9] === 2
    && /^irate-explorer-/.test(Buffer.from(c.slice(14)).toString()), JSON.stringify(c));
  check('  and nothing else until the broker answers', ws.sent.length === 1);
  ws.feed([0x20, 2, 0, 0]);
  const s = ws.sent[1];
  check('accepted: subscribes to msh/# at QoS 0', s && s[0] === 0x82 && Buffer.from(s.slice(6, 11)).toString() === 'msh/#' && s[s.length - 1] === 0, JSON.stringify(s));
  check('  and says it is listening', /Listening to msh\/# at ws:\/\/box\.local\/mqtt/.test(t(d.getElementById('state'))), t(d.getElementById('state')));
  ws.feed([0x90, 3, 0, 1, 0]);
  // An encrypted packet split over two messages, then two packets in one.
  const enc = publish('msh/EU_868/2/e/LongFast/!aabbccdd', [0x0a, 0x00, 0xff, 0x13, ...Array(300).fill(7)]);
  ws.feed(enc.slice(0, 5)); ws.feed(enc.slice(5));
  const json = publish('msh/EU_868/2/json/LongFast/!aabbccdd', [...Buffer.from('{"from":1,"type":"text","payload":{"text":"hi"}}')]);
  const txt = publish('msh/EU_868/2/stat/!aabbccdd', [...Buffer.from('online')], 1);
  ws.feed([...json, ...txt, ...enc]);
  ws.feed(publish('msh/qos1', [...Buffer.from('x')], 2));
  await wait();
  const rows = [...d.querySelectorAll('#topics tbody tr')];
  check('topics listed in order, each counted', rows.length === 4 && t(rows[0].cells[0]) === 'msh/EU_868/2/e/LongFast/!aabbccdd' && t(rows[0].cells[1]) === '2'
    && t(rows[0].cells[2]) === '608 B', rows.map((r) => t(r)).join(' | '));
  check('  a retained one says so', rows.some((r) => t(r.cells[0]) === 'msh/EU_868/2/stat/!aabbccdd (retained)'));
  check('  a QoS 1 message (its packet id skipped) reads right', rows.some((r) => t(r.cells[0]) === 'msh/qos1' && t(r.cells[2]) === '1 B'));
  check('the totals', /^4 topics, 5 messages/.test(t(d.getElementById('totals'))), t(d.getElementById('totals')));
  rows[0].cells[0].click(); await wait(50);
  check('a topic\'s messages: binary as hex', !d.getElementById('detail').hidden && /binary \(hex\)/.test(t(d.getElementById('detail-list')))
    && /^0a 00 ff 13 07/.test(d.querySelector('#detail-list pre').textContent) && /… 48 more bytes/.test(t(d.getElementById('detail-list'))));
  d.querySelector('#topics tbody tr:nth-child(2) td').click(); await wait(50);
  check('  JSON laid out', /"text": "hi"/.test(d.querySelector('#detail-list pre').textContent) && /json/.test(t(d.querySelector('#detail-list .meta'))));
  d.getElementById('search').value = 'json'; d.getElementById('search').dispatchEvent(new w.Event('input'));
  check('Show: only the topics containing it', d.querySelectorAll('#topics tbody tr').length === 1 && /1 shown/.test(t(d.getElementById('totals'))));
  d.getElementById('search').value = ''; d.getElementById('search').dispatchEvent(new w.Event('input'));
  d.getElementById('pause').click();
  ws.feed(publish('msh/late', [1]));
  await wait();
  check('Pause: nothing more is recorded', !d.querySelector('tr[data-topic="msh/late"]') && /paused/.test(t(d.getElementById('totals'))));
  d.getElementById('pause').click();
  d.getElementById('clear').click();
  check('Clear: an empty list', d.querySelectorAll('#topics tbody tr').length === 0 && !d.getElementById('empty').hidden && d.getElementById('detail').hidden);
  d.getElementById('filter').value = 'other/#';
  d.getElementById('sub').dispatchEvent(new w.Event('submit', { cancelable: true }));
  const [un, sub] = ws.sent.slice(-2);
  check('a new filter: the old one dropped, the new one asked for', un[0] === 0xa2 && Buffer.from(un.slice(6)).toString() === 'msh/#' && sub[0] === 0x82, JSON.stringify([un, sub]));
  ws.feed([0x90, 3, 0, 3, 0x80]);
  check('  refused by the broker: said, with what it allows', /does not allow other\/# \(it allows msh\/#\)/.test(t(d.getElementById('state'))), t(d.getElementById('state')));
  check('it never publishes', ws.sent.every((p) => (p[0] >> 4) !== 3));
  ws.close();
  check('the broker gone: said, and tried again', /went away; trying again in 5 s/.test(t(d.getElementById('state'))), t(d.getElementById('state')));
  const second = new Promise((r) => setTimeout(r, 5200));
  await second;
  const ws2 = sockets[1];
  check('  a new connection after the wait', ws2 && ws2.url === 'ws://box.local/mqtt');
  ws2.close();
  check('no broker at all: says how it is installed', /No broker answered .*install\.sh --with-mqtt/.test(t(d.getElementById('state'))), t(d.getElementById('state')));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
