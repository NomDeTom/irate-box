// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Mesh's Heard on its own page, /admin/mesh.html, as the Factory's page: the packets the
// box decoded, the messages' texts too, which only the admin may read. Behind the same login as /admin
// and asking the same /admin/mesh; /admin's Mesh page keeps the channels and their keys.
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const view = { state: $('mesh-heard-state'), counts: $('mesh-heard-counts'), packets: $('mesh-heard-packets') };
  const el = (tag, props = {}, ...kids) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  };
  let poll = null;

  function render(d) {
    view.state.textContent = d.state === 'listening' ? `Listening to the broker: ${d.nodes.length} node${d.nodes.length === 1 ? '' : 's'} heard in the last 7 days.`
      : `Not connected to the broker${d.error ? ` (${d.error})` : ''}: is it installed (Add-ons, MQTT broker) and running? It tries again by itself.`;
    const names = Object.fromEntries(d.nodes.map((n) => [n.id, n.long_name || n.id]));
    const counts = Object.entries(d.counts || {}).map(([k, n]) => `${n} ${k}`);
    view.counts.textContent = counts.length ? `Since the hub started: ${counts.join(', ')}.` : 'Nothing heard yet.';
    view.packets.replaceChildren(...d.packets.map((p) => el('li', {},
      el('span', { textContent: `${new Date(p.at * 1000).toLocaleTimeString()}: ${p.port} from ${names[p.from] || p.from}${p.channel ? ` on ${p.channel}` : ''}` }),
      p.text != null ? el('span', { className: 'mesh-text', textContent: ` “${p.text}”` }) : null)));
  }

  async function load() {
    clearTimeout(poll);
    try {
      const r = await fetch('/admin/mesh');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      render(await r.json());
    } catch (_) { view.state.textContent = 'Could not read the mesh.'; }
    poll = setTimeout(load, 15000);
  }
  load();
})();
