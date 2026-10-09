// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The uptime heatmaps (next-work plan steps 34 and 35): one component for the network links'
// week and month on /admin → Network, and the services' grid. A cell is a bucket the hub summed,
// {up: 0-1, n: samples, drops, off}, or null for no data; it is coloured by how much of it was up
// (style.css: .hm-*), and says so on hover, on focus and to a screen reader.
const Heatmap = (() => {
  const LEVELS = [['full', 'up all the time'], ['most', 'up most of the time'], ['part', 'down part of the time'],
    ['down', 'down'], ['off', 'switched off'], ['none', 'no data']];
  function level(c) {
    if (!c) return 'none';
    if (c.off && c.up === 0) return 'off';
    return c.up >= 0.995 ? 'full' : c.up >= 0.9 ? 'most' : c.up > 0 ? 'part' : 'down';
  }
  // 100 % only when it was; a decimal from 90 % up, where the differences matter (99.2 %).
  function percent(up) {
    if (up >= 1) return '100 %';
    return up >= 0.9 ? `${(Math.floor(up * 1000) / 10).toFixed(1)} %` : `${Math.floor(up * 100)} %`;
  }
  // What a cell says: "Tue 07 14:00–15:00: up 92 %, 2 drops".
  function words(where, c) {
    if (!c) return `${where}: no data`;
    if (c.off && c.up === 0) return `${where}: switched off`;
    const extra = [c.drops ? `${c.drops} drop${c.drops === 1 ? '' : 's'}` : '', c.off ? 'switched off for a while' : '',
      c.restarts ? `${c.restarts} restart${c.restarts === 1 ? '' : 's'}` : ''].filter(Boolean);
    return `${where}: up ${percent(c.up)}${extra.length ? `, ${extra.join(', ')}` : ''}`;
  }
  function make(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  // rows: [{label, cells: [cell], where: (i) => text}], cols: column labels (a few shown).
  // bare: no label column or column heads, for a strip inside a row of something else.
  function grid({ rows, cols, caption, bare }) {
    const g = make('div', 'heatmap');
    g.setAttribute('role', 'group');
    g.style.setProperty('--hm-cols', String(cols.length));
    if (caption) g.setAttribute('aria-label', caption);
    if (bare) g.classList.add('hm-bare');
    else {
      const head = make('div', 'hm-row hm-head');
      head.append(make('span', 'hm-label', ''));
      cols.forEach((c) => head.append(make('span', 'hm-col', c)));
      g.append(head);
    }
    for (const r of rows) {
      const row = make('div', 'hm-row');
      if (!bare) row.append(make('span', 'hm-label', r.label));
      r.cells.forEach((c, i) => {
        const cell = make('span', `hm-cell hm-${level(c)}${c && (c.drops || c.restarts) ? ' hm-mark' : ''}`);
        cell.title = words(r.where(i), c);
        cell.setAttribute('role', 'img');
        cell.setAttribute('aria-label', cell.title);
        row.append(cell);
      });
      g.append(row);
    }
    return g;
  }
  function legend() {
    const l = make('div', 'hm-legend');
    for (const [k, text] of LEVELS) {
      const item = make('span', 'hm-key');
      item.append(make('span', `hm-cell hm-${k}`), make('span', '', text));
      l.append(item);
    }
    return l;
  }
  return { grid, legend, level, words, percent };
})();
