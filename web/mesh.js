// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The mesh heard through the box (next-work plan step 18): the nodes the decoder bridge knows
// (meshbridge.py, /mesh.json) and the recent traffic, refreshed every 15 seconds. Messages are
// guests' words: /mesh.json never has them (only /admin does).
const $ = (id) => document.getElementById(id);
const make = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const KINDS = { text: 'messages', position: 'positions', nodeinfo: 'node names', telemetry: 'telemetry', routing: 'routing',
  traceroute: 'traceroutes', neighborinfo: 'neighbours', map_report: 'map reports', encrypted: 'encrypted (no key here)' };

function ago(t) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 90 ? `${s} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
}

function nodeName(n) {
  return n.long_name ? `${n.long_name}${n.short_name ? ` (${n.short_name})` : ''}` : n.id;
}

function render(d) {
  $('mesh-state').textContent = d.state === 'listening'
    ? `Listening to the box's MQTT broker: ${d.nodes.length} node${d.nodes.length === 1 ? '' : 's'} heard in the last 7 days.`
    : 'Not connected to the box\'s MQTT broker just now: it tries again by itself.';
  const counts = Object.entries(d.counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n} ${KINDS[k] || k}`);
  $('mesh-counts').textContent = counts.length ? `Since ${new Date(d.since * 1000).toLocaleString()}: ${counts.join(', ')}.` : 'Nothing heard yet.';
  const names = Object.fromEntries(d.nodes.map((n) => [n.id, nodeName(n)]));
  $('mesh-nodes').tBodies[0].replaceChildren(...d.nodes.map((n) => {
    const tr = make('tr');
    const t = n.telemetry || {};
    const p = n.position;
    tr.append(make('td', nodeName(n)), make('td', ago(n.last)),
      make('td', t.battery != null ? `${t.battery > 100 ? 'powered' : `${t.battery} %`}${t.voltage ? `, ${t.voltage} V` : ''}` : '—'),
      make('td', p ? `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}${p.alt != null ? `, ${p.alt} m` : ''}` : '—'),
      make('td', [n.hops != null ? `${n.hops} hop${n.hops === 1 ? '' : 's'}` : '', n.snr != null ? `SNR ${n.snr}` : '', n.rssi != null ? `${n.rssi} dBm` : ''].filter(Boolean).join(', ') || '—'),
      make('td', String(n.packets || 0)));
    return tr;
  }));
  $('mesh-packets').replaceChildren(...d.packets.map((p) => make('li',
    `${new Date(p.at * 1000).toLocaleTimeString()}: ${KINDS[p.port] ? p.port : p.port} from ${names[p.from] || p.from}${p.to && p.to !== 'all' ? ` to ${names[p.to] || p.to}` : ''}${p.channel ? ` on ${p.channel}` : ''}`)));
}

async function refresh() {
  try {
    const r = await fetch('/mesh.json', { cache: 'no-store' });
    if (r.status === 404) {
      $('mesh-state').textContent = 'The mesh view is not shown here: its owner chooses (/admin → Access, the MQTT broker, public).';
      return;
    }
    render(await r.json());
  } catch (_) {
    $('mesh-state').textContent = 'The box did not answer.';
  }
}

refresh();
setInterval(refresh, 15000);
