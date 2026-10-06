// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The MQTT explorer (next-work plan step 13): the box's broker at ws://<box>/mqtt, listened to
// with a few lines of MQTT 3.1.1 rather than a library (CONNECT, SUBSCRIBE at QoS 0, PUBLISH
// read, PINGREQ). It lists the topics as messages arrive: how many, how big, how recent, and a
// topic's last few messages, as text when they are text and as hex when not. It never publishes.
// Its Content-Security-Policy (the add-on's manifest) lets it reach the broker and nothing else.
'use strict';

const KEEP_TOPICS = 2000;   // past this, new topics are counted but not listed
const KEEP_MESSAGES = 20;   // per topic, for the detail pane
const KEEPALIVE = 60;       // seconds, said in CONNECT; a PINGREQ goes at half that

const enc = new TextEncoder();

function mqttString(s) {
  const b = enc.encode(s);
  return [b.length >> 8, b.length & 255, ...b];
}

function mqttPacket(type, body) {
  const len = [];
  let n = body.length;
  do {
    let byte = n % 128;
    n = Math.floor(n / 128);
    if (n > 0) byte |= 128;
    len.push(byte);
  } while (n > 0);
  return new Uint8Array([type, ...len, ...body]);
}

function connectPacket(clientId) {
  // Protocol "MQTT", level 4 (3.1.1), clean session, no will, no user name.
  return mqttPacket(0x10, [...mqttString('MQTT'), 4, 0x02, KEEPALIVE >> 8, KEEPALIVE & 255, ...mqttString(clientId)]);
}

function subscribePacket(id, filter) {
  return mqttPacket(0x82, [id >> 8, id & 255, ...mqttString(filter), 0]);
}

function unsubscribePacket(id, filter) {
  return mqttPacket(0xa2, [id >> 8, id & 255, ...mqttString(filter)]);
}

const PINGREQ = new Uint8Array([0xc0, 0]);

// Whole packets out of what the socket has given so far (a message may hold several, or part
// of one). Returns [packets, the bytes left over].
function splitPackets(buf) {
  const out = [];
  let i = 0;
  while (i + 2 <= buf.length) {
    let len = 0, mult = 1, j = i + 1, done = false;
    for (let k = 0; k < 4 && j < buf.length; k++, j++) {
      len += (buf[j] & 127) * mult;
      mult *= 128;
      if (!(buf[j] & 128)) { done = true; j++; break; }
    }
    if (!done) {
      if (j - i - 1 >= 4) throw new Error('a malformed packet length');
      break;
    }
    if (j + len > buf.length) break;
    out.push({ type: buf[i] >> 4, flags: buf[i] & 15, body: buf.subarray(j, j + len) });
    i = j + len;
  }
  return [out, buf.slice(i)];
}

function readPublish(p) {
  const tlen = (p.body[0] << 8) | p.body[1];
  const topic = new TextDecoder().decode(p.body.subarray(2, 2 + tlen));
  const qos = (p.flags >> 1) & 3;
  const start = 2 + tlen + (qos ? 2 : 0);
  return { topic, retain: !!(p.flags & 1), payload: p.body.slice(start) };
}

// A payload as a person would want it: JSON laid out, text as text, anything else as hex.
function showPayload(bytes) {
  let text = null;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch (e) { /* binary */ }
  if (text !== null && !/[\x00-\x08\x0e-\x1f\x7f]/.test(text)) {
    try { return { kind: 'json', text: JSON.stringify(JSON.parse(text), null, 2) }; } catch (e) { /* not JSON */ }
    return { kind: 'text', text };
  }
  const shown = bytes.subarray(0, 256);
  const lines = [];
  for (let i = 0; i < shown.length; i += 16) {
    lines.push([...shown.subarray(i, i + 16)].map((b) => b.toString(16).padStart(2, '0')).join(' '));
  }
  if (bytes.length > shown.length) lines.push(`… ${bytes.length - shown.length} more bytes`);
  return { kind: 'binary', text: lines.join('\n') };
}

function ago(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

function sizeText(n) {
  return n < 10240 ? `${n} B` : n < 10485760 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

const explorer = {
  topics: new Map(),   // topic -> {count, bytes, last, retained, messages: [{at, size, retain, payload}]}
  overflow: 0,         // messages on topics past KEEP_TOPICS
  total: 0,
  started: Date.now(),
  paused: false,
  selected: null,
  filter: 'msh/#',
  ws: null,
  buf: new Uint8Array(0),
  nextId: 1,
  retry: 0,
  timer: null,
  connected: false,
};

function brokerUrl() {
  // ws://{box}/mqtt in the manifest: the hub's own host, on its usual port, not this page's.
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.hostname}/mqtt`;
}

function setState(text, cls) {
  const el = document.getElementById('state');
  el.textContent = text;
  el.className = `state ${cls || ''}`;
}

function send(bytes) {
  if (explorer.ws && explorer.ws.readyState === 1) explorer.ws.send(bytes);
}

function connect() {
  clearTimeout(explorer.timer);
  const url = brokerUrl();
  setState(`Connecting to ${url}…`);
  let ws;
  try {
    ws = new WebSocket(url, ['mqtt']);
  } catch (e) {
    setState(`Cannot open ${url}: ${e.message}`, 'bad');
    return;
  }
  explorer.ws = ws;
  explorer.buf = new Uint8Array(0);
  explorer.connected = false;
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => send(connectPacket(`irate-explorer-${Math.random().toString(36).slice(2, 10)}`));
  ws.onmessage = (ev) => receive(new Uint8Array(ev.data));
  ws.onclose = () => {
    if (explorer.ws !== ws) return;
    clearInterval(explorer.ping);
    const wait = Math.min(60, 5 * 2 ** explorer.retry++);
    setState(explorer.connected
      ? `The broker went away; trying again in ${wait} s.`
      : `No broker answered at ${url}. It is installed with install.sh --with-mqtt, and /admin can switch it off. Trying again in ${wait} s.`, 'bad');
    explorer.connected = false;
    explorer.timer = setTimeout(connect, wait * 1000);
  };
}

function subscribe(filter) {
  if (explorer.connected && explorer.filter && explorer.filter !== filter) send(unsubscribePacket(explorer.nextId++ & 0xffff || 1, explorer.filter));
  explorer.filter = filter;
  if (explorer.connected) send(subscribePacket(explorer.nextId++ & 0xffff || 1, filter));
}

function receive(bytes) {
  const joined = new Uint8Array(explorer.buf.length + bytes.length);
  joined.set(explorer.buf);
  joined.set(bytes, explorer.buf.length);
  let packets;
  try {
    [packets, explorer.buf] = splitPackets(joined);
  } catch (e) {
    setState(`The broker sent something unreadable (${e.message}); reconnecting.`, 'bad');
    explorer.ws.close();
    return;
  }
  for (const p of packets) {
    if (p.type === 2) {          // CONNACK
      const code = p.body[1];
      if (code !== 0) {
        setState(`The broker refused the connection (code ${code}).`, 'bad');
        continue;
      }
      explorer.connected = true;
      explorer.retry = 0;
      setState(`Listening to ${explorer.filter} at ${brokerUrl()}`, 'ok');
      clearInterval(explorer.ping);
      explorer.ping = setInterval(() => send(PINGREQ), KEEPALIVE * 500);
      send(subscribePacket(explorer.nextId++ & 0xffff || 1, explorer.filter));
    } else if (p.type === 9) {   // SUBACK
      if (p.body[2] === 0x80) setState(`The broker does not allow ${explorer.filter} (it allows msh/#).`, 'bad');
      else setState(`Listening to ${explorer.filter} at ${brokerUrl()}`, 'ok');
    } else if (p.type === 3) {   // PUBLISH
      if (!explorer.paused) record(readPublish(p));
    }
  }
}

function record(m) {
  explorer.total++;
  let t = explorer.topics.get(m.topic);
  if (!t) {
    if (explorer.topics.size >= KEEP_TOPICS) { explorer.overflow++; scheduleDraw(); return; }
    t = { count: 0, bytes: 0, last: 0, retained: false, messages: [] };
    explorer.topics.set(m.topic, t);
  }
  t.count++;
  t.bytes += m.payload.length;
  t.last = Date.now();
  t.retained = m.retain;
  t.messages.push({ at: t.last, size: m.payload.length, retain: m.retain, payload: m.payload });
  if (t.messages.length > KEEP_MESSAGES) t.messages.shift();
  scheduleDraw();
}

let drawPending = false;
function scheduleDraw() {
  if (drawPending) return;
  drawPending = true;
  setTimeout(() => { drawPending = false; draw(); }, 250);
}

function cell(tr, text, cls) {
  const td = document.createElement('td');
  td.textContent = text;
  if (cls) td.className = cls;
  tr.appendChild(td);
}

function draw() {
  const now = Date.now();
  const search = document.getElementById('search').value.trim().toLowerCase();
  const names = [...explorer.topics.keys()].filter((n) => !search || n.toLowerCase().includes(search)).sort();
  const body = document.querySelector('#topics tbody');
  const rows = document.createDocumentFragment();
  for (const name of names) {
    const t = explorer.topics.get(name);
    const tr = document.createElement('tr');
    tr.dataset.topic = name;
    if (now - t.last < 5000) tr.classList.add('fresh');
    if (name === explorer.selected) tr.classList.add('on');
    cell(tr, name + (t.retained ? ' (retained)' : ''));
    cell(tr, String(t.count), 'n');
    cell(tr, sizeText(t.bytes), 'n');
    cell(tr, ago(now - t.last), 'n');
    rows.appendChild(tr);
  }
  body.replaceChildren(rows);
  document.getElementById('empty').hidden = explorer.topics.size > 0;
  const mins = Math.max(1, (now - explorer.started) / 60000);
  const bytes = [...explorer.topics.values()].reduce((a, t) => a + t.bytes, 0);
  document.getElementById('totals').textContent = `${explorer.topics.size} topics, ${explorer.total} messages`
    + ` (${(explorer.total / mins).toFixed(1)} a minute), ${sizeText(bytes)}`
    + (search ? `; ${names.length} shown` : '')
    + (explorer.overflow ? `; ${explorer.overflow} on topics past the first ${KEEP_TOPICS}, counted only` : '')
    + (explorer.paused ? '; paused' : '');
  drawDetail();
}

function drawDetail() {
  const pane = document.getElementById('detail');
  const t = explorer.selected && explorer.topics.get(explorer.selected);
  pane.hidden = !t;
  if (!t) return;
  document.getElementById('detail-topic').textContent = explorer.selected;
  const list = document.createDocumentFragment();
  for (const m of [...t.messages].reverse()) {
    const li = document.createElement('li');
    const shown = showPayload(m.payload);
    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = `${new Date(m.at).toLocaleTimeString()}, ${sizeText(m.size)}, ${shown.kind === 'binary' ? 'binary (hex)' : shown.kind}`
      + (m.retain ? ', retained' : '');
    const pre = document.createElement('pre');
    pre.textContent = shown.text;
    li.append(meta, pre);
    list.appendChild(li);
  }
  document.getElementById('detail-list').replaceChildren(list);
}

function start() {
  const theme = new URLSearchParams(location.search).get('hub-theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  document.getElementById('sub').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const filter = document.getElementById('filter').value.trim();
    if (filter) subscribe(filter);
  });
  document.getElementById('pause').addEventListener('click', (ev) => {
    explorer.paused = !explorer.paused;
    ev.target.setAttribute('aria-pressed', String(explorer.paused));
    ev.target.textContent = explorer.paused ? 'Resume' : 'Pause';
    draw();
  });
  document.getElementById('clear').addEventListener('click', () => {
    explorer.topics.clear();
    explorer.total = explorer.overflow = 0;
    explorer.started = Date.now();
    explorer.selected = null;
    draw();
  });
  document.getElementById('search').addEventListener('input', draw);
  document.querySelector('#topics tbody').addEventListener('click', (ev) => {
    const tr = ev.target.closest('tr');
    if (!tr) return;
    explorer.selected = explorer.selected === tr.dataset.topic ? null : tr.dataset.topic;
    draw();
  });
  setInterval(draw, 5000);
  draw();
  connect();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
else start();
