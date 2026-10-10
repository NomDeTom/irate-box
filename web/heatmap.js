// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The uptime heatmaps: one component for the network links'
// week and month on /admin → Network, and the services' grid. A cell is a bucket the hub summed,
// {up: 0-1, n: samples, drops, off}, or null for no data; it is coloured by how much of it was up
// (style.css: .hm-*), and says so in a tooltip (pointed at, tapped, or reached with the arrow keys once
// the grid has focus) and to a screen reader.
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
  // The tooltip: one for the page, fixed (a grid scrolls sideways and would clip its own), above the
  // cell or below it near the top, kept inside the window; hidden on leaving, on blur and on scroll.
  let tip = null, on = null, ids = 0;
  function show(cell) {
    if (!tip) {
      tip = make('div', 'hm-tip');
      tip.setAttribute('aria-hidden', 'true');
      document.body.append(tip);
      window.addEventListener('scroll', hide, { passive: true, capture: true });
    }
    if (on && on !== cell) on.classList.remove('hm-on');
    on = cell;
    cell.classList.add('hm-on');
    tip.textContent = cell.dataset.words;
    tip.hidden = false;
    const r = cell.getBoundingClientRect(), vw = document.documentElement.clientWidth || window.innerWidth;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const left = Math.max(4, Math.min(r.left + r.width / 2 - tw / 2, vw - tw - 4));
    const top = r.top - th - 6 >= 4 ? r.top - th - 6 : r.bottom + 6;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }
  function hide() {
    if (tip) tip.hidden = true;
    if (on) on.classList.remove('hm-on');
    on = null;
  }
  // Pointer and keys on a grid: a cell under the pointer or tapped; ← → along a row, ↑ ↓ between rows,
  // Home and End; the newest cell of the first row on focus.
  function follow(g) {
    const rows = () => [...g.querySelectorAll('.hm-row:not(.hm-head)')].map((r) => [...r.querySelectorAll('.hm-cell')]);
    const at = (cell) => {
      if (!cell) return;
      cell.id = cell.id || `hm-cell-${++ids}`;
      g.setAttribute('aria-activedescendant', cell.id);
      show(cell);
    };
    const pick = (ev) => { const c = ev.target.closest && ev.target.closest('.hm-cell'); if (c && g.contains(c)) show(c); };
    g.addEventListener('pointerover', pick);
    g.addEventListener('click', pick);
    g.addEventListener('pointerleave', (ev) => { if (ev.pointerType !== 'touch' && document.activeElement !== g) hide(); });
    g.addEventListener('focus', () => { const rs = rows(); if (rs.length) at(on && g.contains(on) ? on : rs[0][rs[0].length - 1]); });
    g.addEventListener('blur', hide);
    g.addEventListener('keydown', (ev) => {
      const rs = rows();
      if (!rs.length) return;
      let ri = rs.findIndex((r) => r.includes(on)), ci = ri >= 0 ? rs[ri].indexOf(on) : -1;
      if (ri < 0) { ri = 0; ci = rs[0].length - 1; }
      const moves = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] };
      if (moves[ev.key]) {
        ri = Math.max(0, Math.min(rs.length - 1, ri + moves[ev.key][0]));
        ci = Math.max(0, Math.min(rs[ri].length - 1, ci + moves[ev.key][1]));
      } else if (ev.key === 'Home') ci = 0;
      else if (ev.key === 'End') ci = rs[ri].length - 1;
      else if (ev.key === 'Escape') { hide(); return; }
      else return;
      ev.preventDefault();
      at(rs[ri][ci]);
    });
  }

  // rows: [{label, cells: [cell], where: (i) => text}], cols: column labels (a few shown).
  // bare: no label column or column heads, for a strip inside a row of something else.
  function grid({ rows, cols, caption, bare }) {
    const g = make('div', 'heatmap');
    g.setAttribute('role', 'group');
    g.tabIndex = 0;
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
        cell.dataset.words = words(r.where(i), c);
        cell.setAttribute('role', 'img');
        cell.setAttribute('aria-label', cell.dataset.words);
        row.append(cell);
      });
      g.append(row);
    }
    follow(g);
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
