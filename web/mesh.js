// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The mesh heard through the box: the nodes the decoder bridge knows
// (meshbridge.py, /mesh.json) and the recent traffic, refreshed every 15 seconds. Messages are
// guests' words: /mesh.json never has them (only /admin does).
const $ = (id) => document.getElementById(id);
const make = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
const KINDS = { text: 'messages', position: 'positions', nodeinfo: 'node names', telemetry: 'telemetry', routing: 'routing',
  traceroute: 'traceroutes', neighborinfo: 'neighbours', map_report: 'map reports', encrypted: 'encrypted (no key here)' };

function ago(t) {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - t));
  return seconds < 90 ? `${seconds} s ago` : seconds < 5400 ? `${Math.round(seconds / 60)} min ago` : seconds < 172800 ? `${Math.round(seconds / 3600)} h ago` : `${Math.round(seconds / 86400)} days ago`;
}

function nodeName(n) {
  return n.long_name ? `${n.long_name}${n.short_name ? ` (${n.short_name})` : ''}` : n.id;
}

function render(data) {
  $('mesh-state').textContent = data.state === 'listening'
    ? `Connected to the box's MQTT broker: ${data.nodes.length} node${data.nodes.length === 1 ? '' : 's'} received in the last 7 days.`
    : 'Reconnecting to the box\'s MQTT broker. This happens automatically.';
  const counts = Object.entries(data.counts).sort((left, right) => right[1] - left[1]).map(([k, n]) => `${n} ${KINDS[k] || k}`);
  $('mesh-counts').textContent = counts.length ? `Packets received since ${new Date(data.since * 1000).toLocaleString()}: ${counts.join(', ')}.` : 'No packets received';
  drawMap(data.nodes);
  const names = Object.fromEntries(data.nodes.map((n) => [n.id, nodeName(n)]));
  $('mesh-nodes').tBodies[0].replaceChildren(...data.nodes.map((n) => {
    const tr = make('tr');
    const t = n.telemetry || {};
    const p = n.position;
    tr.append(make('td', nodeName(n)), make('td', ago(n.last)),
      make('td', t.battery != null ? `${t.battery > 100 ? 'externally powered' : `${t.battery} %`}${t.voltage ? `, ${t.voltage} V` : ''}` : '—'),
      make('td', p ? `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}${p.alt != null ? `, ${p.alt} m` : ''}` : '—'),
      make('td', [n.hops != null ? `${n.hops} hop${n.hops === 1 ? '' : 's'}` : '', n.snr != null ? `SNR ${n.snr}` : '', n.rssi != null ? `${n.rssi} dBm` : ''].filter(Boolean).join(', ') || '—'),
      make('td', String(n.packets || 0)));
    return tr;
  }));
  $('mesh-packets').replaceChildren(...data.packets.map((p) => make('li',
    `${new Date(p.at * 1000).toLocaleTimeString()}: ${p.port} packet from ${names[p.from] || p.from}${p.to && p.to !== 'all' ? ` to ${names[p.to] || p.to}` : ''}${p.channel ? ` on channel ${p.channel}` : ''}`)));
}

// The nodes with a position, on Web Mercator fitted to them (a margin round the edges), with their
// short names and a scale bar. No tiles underneath: those are for later (offline OSM tiles).
const SVG = 'http://www.w3.org/2000/svg';
function svg(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  if (text != null) node.textContent = text;
  return node;
}
function mercY(lat) { return Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)); }
function drawMap(nodes) {
  const map = $('mesh-map');
  const W = 600, H = 360, M = 40;
  const placed = nodes.filter((n) => n.position && Number.isFinite(n.position.lat) && Number.isFinite(n.position.lon)
    && Math.abs(n.position.lat) <= 85 && Math.abs(n.position.lon) <= 180 && !(n.position.lat === 0 && n.position.lon === 0));
  map.replaceChildren();
  map.hidden = !placed.length;
  $('mesh-map-note').textContent = placed.length
    ? `${placed.length} node${placed.length === 1 ? '' : 's'} with a position. Positions are shown without a background map. No internet access is needed.`
    : 'No reported positions';
  if (!placed.length) return;
  const xs = placed.map((n) => (n.position.lon * Math.PI) / 180), ys = placed.map((n) => mercY(n.position.lat));
  let [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const span = Math.max(x1 - x0, y1 - y0, 2e-6);          // about 10 m at the least, so one node sits mid-map
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const k = Math.min((W - 2 * M) / span, (H - 2 * M) / span);
  const px = (x) => W / 2 + (x - cx) * k, py = (y) => H / 2 - (y - cy) * k;
  // The scale bar: a round length near a fifth of the width, in metres at the map's middle latitude.
  const lat = (Math.atan(Math.sinh(cy)) * 180) / Math.PI;
  const metresPerUnit = 6371000 * Math.cos((lat * Math.PI) / 180);  // per radian of longitude
  const want = ((W / 5) / k) * metresPerUnit;
  const round = [1, 2, 5].flatMap((m) => [1, 10, 100, 1000, 10000, 100000].map((p) => m * p)).filter((v) => v <= want).pop() || 1;
  const len = (round / metresPerUnit) * k;
  map.append(svg('rect', { x: 0, y: 0, width: W, height: H, class: 'mesh-map-bg' }),
    svg('line', { x1: M, y1: H - 14, x2: M + len, y2: H - 14, class: 'mesh-map-scale' }),
    svg('text', { x: M, y: H - 20, class: 'mesh-map-label' }, round >= 1000 ? `${round / 1000} km` : `${round} m`));
  placed.forEach((n, i) => {
    const x = px(xs[i]), y = py(ys[i]);
    const g = svg('g', { class: 'mesh-map-node' });
    g.append(svg('circle', { cx: x, cy: y, r: 6 }), svg('text', { x: x + 9, y: y + 4, class: 'mesh-map-label' }, n.short_name || n.long_name || n.id));
    g.append(svg('title', {}, `${nodeName(n)}: ${n.position.lat.toFixed(5)}, ${n.position.lon.toFixed(5)}${n.position.alt != null ? `, ${n.position.alt} m` : ''}, last received ${ago(n.last)}`));
    map.append(g);
  });
}

async function refresh() {
  try {
    const response = await fetch('/mesh.json', { cache: 'no-store' });
    if (response.status === 404) {
      $('mesh-state').textContent = 'Mesh view disabled or restricted. An administrator can change this in /admin → Access, under the MQTT broker.';
      return;
    }
    render(await response.json());
  } catch (_) {
    $('mesh-state').textContent = 'The box did not answer.';
  }
}

refresh();
setInterval(refresh, 15000);
