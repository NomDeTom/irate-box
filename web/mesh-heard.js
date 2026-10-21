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

  function render(data) {
    view.state.textContent = data.state === 'listening' ? `Connected to the broker: ${data.nodes.length} node${data.nodes.length === 1 ? '' : 's'} received in the last 7 days.`
      : `Reconnecting to the broker${data.error ? ` (${data.error_message && window.AW ? window.AW.say(data.error_message) : data.error})` : ''}. This happens automatically.`;
    const names = Object.fromEntries(data.nodes.map((n) => [n.id, n.long_name || n.id]));
    const counts = Object.entries(data.counts || {}).map(([k, n]) => `${n} ${k}`);
    view.counts.textContent = counts.length ? `Decoded packets since the hub server started: ${counts.join(', ')}.` : 'No packets received';
    view.packets.replaceChildren(...data.packets.map((p) => el('li', {},
      el('span', { textContent: `${new Date(p.at * 1000).toLocaleTimeString()}: ${p.port} packet from ${names[p.from] || p.from}${p.channel ? ` on channel ${p.channel}` : ''}` }),
      p.text != null ? el('span', { className: 'mesh-text', textContent: ` “${p.text}”` }) : null)));
  }

  async function load() {
    clearTimeout(poll);
    try {
      const response = await fetch('/admin/mesh');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (_) { view.state.textContent = 'Could not load mesh data'; }
    poll = setTimeout(load, 15000);
  }
  load();
})();
