// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Received mesh data on its own page, /admin/mesh.html, as the Factory's page: the packets the
// box decoded, the messages' texts too, which only an administrator can read. Behind the same login as /admin
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
    view.state.textContent = d.state === 'listening' ? `Connected to the broker: ${d.nodes.length} node${d.nodes.length === 1 ? '' : 's'} received in the last 7 days.`
      : `Reconnecting to the broker${d.error ? ` (${d.error})` : ''}. This happens automatically.`;
    const names = Object.fromEntries(d.nodes.map((n) => [n.id, n.long_name || n.id]));
    const counts = Object.entries(d.counts || {}).map(([k, n]) => `${n} ${k}`);
    view.counts.textContent = counts.length ? `Decoded packets since the hub server started: ${counts.join(', ')}.` : 'No packets received';
    view.packets.replaceChildren(...d.packets.map((p) => el('li', {},
      el('span', { textContent: `${new Date(p.at * 1000).toLocaleTimeString()}: ${p.port} packet from ${names[p.from] || p.from}${p.channel ? ` on channel ${p.channel}` : ''}` }),
      p.text != null ? el('span', { className: 'mesh-text', textContent: ` “${p.text}”` }) : null)));
  }

  async function load() {
    clearTimeout(poll);
    try {
      const r = await fetch('/admin/mesh');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      render(await r.json());
    } catch (_) { view.state.textContent = 'Could not load mesh data'; }
    poll = setTimeout(load, 15000);
  }
  load();
})();
