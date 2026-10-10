// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The uplink watchdog's escalations over time, for the box doctor, to show whether
// more patience or more aggression is needed. From uplink.py's ladder record
// ({at, k: kind, s: step, b: by, t: text}):
// a step line of the heaviest rung each outage reached (up, down, reconnect, restart, radio reset,
// reboot), the outages as bands, and a marker for each step taken whose shape says who took it
// (● the watchdog, ◆ by hand, ▲ a flapping link's repair) and, hollow, one held or a stall.
// Many outages ending at the first rung say a gentler pace would do; long waits at a rung that
// never helps say the reach or the pace could go further. One series: no legend box, a key in words.
// Time with no record (the watchdog not running: a freeze, a reboot) is hatched, the line broken
// across it, so an outage is never drawn as going on through it. Zoom: shorter periods, a click or
// tap on an event zooms onto its cluster, a drag across the chart chooses the span; Zoom out steps back.
const LadderChart = (() => {
  const LEVELS = ['up', 'down', 'reconnect', 'restart', 'radio', 'reboot'];
  const NAMES = { up: 'up', down: 'down', reconnect: 'reconnect', restart: 'restart', radio: 'radio reset', reboot: 'reboot', pin: 'locked to an AP' };
  const BY = { auto: 'by the watchdog', hand: 'asked on /admin', flap: 'a flapping link' };
  const PERIODS = [['1 h', 3600], ['6 h', 6 * 3600], ['24 h', 86400], ['7 days', 7 * 86400], ['30 days', 30 * 86400], ['72 days', 72 * 86400]];
  const SVG = 'http://www.w3.org/2000/svg';
  const W = 640, H = 310, L = 84, R = 12, T = 10, B = 26;   // the drawing's own units (viewBox)
  // Two panels on one time axis: above,
  // the missed checks within the pace's window and the sensitivity's line; below, the ladder.
  const SYM_H = 84, LAD_T = T + SYM_H + 28;

  const make = (tag, attrs = {}, text) => {
    const e = tag === 'svg' || /^(g|path|line|rect|circle|text|polygon|pattern|defs)$/.test(tag) ? document.createElementNS(SVG, tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    return e;
  };
  let hatches = 0;   // each chart's hatch pattern its own id (two charts can share a page)
  const level = (step) => Math.max(LEVELS.indexOf(step === 'pin' ? 'reconnect' : step), 0);

  // The line and the bands: the level at each moment, from the start of the record (so the window
  // opens at the level it was at), the heaviest rung an outage reached held until it ended.
  // Rows that are not events: the symptoms (m) and the roams between access points.
  const isEvent = (r) => r.k !== 'm' && r.k !== 'roam';

  // The spans with no record, merged: the server's (the five-minute history's empty slots) and, for
  // each start of the watchdog, from its last check before it stopped to its start.
  function holes(rows, gaps) {
    const all = (gaps || []).filter((g) => Array.isArray(g) && g[1] > g[0]).map((g) => [g[0], g[1]]);
    for (const r of rows) if (r.k === 'start' && typeof r.last === 'number' && r.last < r.at) all.push([r.last, r.at]);
    all.sort((a, b) => a[0] - b[0]);
    const out = [];
    for (const g of all) { const p = out[out.length - 1]; if (p && g[0] <= p[1]) p[1] = Math.max(p[1], g[1]); else out.push(g); }
    return out;
  }

  // segs: the line's pieces, broken at each span with no record; an outage open when one began ends there.
  function model(rows, from, to, hs = []) {
    const segs = [[]], bands = [], marks = [];
    let lv = 0, open = null, holeEnd = -Infinity;
    const seg = () => segs[segs.length - 1];
    const at = (t, v) => {
      if (t < holeEnd) { if (holeEnd >= to) return; t = holeEnd; }
      const s = seg();
      if (t < from) { s.splice(0, s.length, [from, v]); return; }
      s.push([t, v]);
    };
    at(from, 0);
    const evs = rows.filter(isEvent).concat(hs.map(([a, b]) => ({ at: a, k: 'hole', end: b })))
      .sort((a, b) => a.at - b.at || (a.k === 'hole' ? 1 : b.k === 'hole' ? -1 : 0));   // a recorded event before a gap starting with it
    for (const r of evs) {
      if (r.at > to) break;
      if (r.k === 'hole') {
        if (open != null) bands.push([open, r.at]);
        open = null;
        at(r.at, lv);
        lv = 0;
        if (r.end <= from) { seg().splice(0, seg().length, [from, 0]); continue; }
        if (r.at <= from) seg().length = 0;
        holeEnd = r.end;
        segs.push(r.end < to ? [[r.end, 0]] : []);
        continue;
      }
      if (r.k === 'down') { lv = Math.max(lv, 1); open = r.at; at(r.at, lv); }
      else if (r.k === 'up' || r.k === 'start') { if (open != null) bands.push([open, r.at]); open = null; lv = 0; at(r.at, 0); }
      else if (r.k === 'repair' && r.s) { if (open != null) { lv = Math.max(lv, level(r.s)); at(r.at, lv); } }
      if (r.at >= from && (r.k === 'repair' || (r.k === 'flap' && r.s) || r.k === 'held' || r.k === 'stalled') && r.s) marks.push(r);
    }
    if (open != null) bands.push([open, to]);
    if (seg().length) seg().push([to, lv]);
    const kept = segs.filter((s) => s.length);
    return { segs: kept, pts: kept.flat(), marks,
      bands: bands.filter(([a, b]) => b >= from).map(([a, b]) => [Math.max(a, from), b]),
      holes: hs.filter(([a, b]) => b > from && a < to).map(([a, b]) => [Math.max(a, from), Math.min(b, to)]) };
  }

  const dur = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min`
    : s < 2 * 86400 ? `${Math.floor(s / 3600)} h${Math.round((s % 3600) / 60) ? ` ${Math.round((s % 3600) / 60)} min` : ''}` : `${Math.round(s / 86400)} days`);

  function summary(rows, from, to = Infinity, hs = []) {
    const seen = rows.filter((r) => r.at >= from && r.at <= to && isEvent(r));
    const outages = seen.filter((r) => r.k === 'down').length;
    const steps = {};
    for (const r of seen) if (r.k === 'repair' && r.s) steps[r.s] = (steps[r.s] || 0) + 1;
    const held = seen.filter((r) => r.k === 'held').length, stalls = seen.filter((r) => r.k === 'stalled').length;
    const said = Object.entries(steps).map(([s, n]) => `${n} ${NAMES[s]}${n === 1 ? '' : 's'}`).join(', ');
    const blank = hs.reduce((n, [a, b]) => n + Math.max(0, Math.min(b, to) - Math.max(a, from)), 0);
    return `${outages} outage${outages === 1 ? '' : 's'}` + (said ? `; steps taken: ${said}` : '; no steps taken')
      + (held ? `; ${held} held` : '') + (stalls ? `; stalled ${stalls} time${stalls === 1 ? '' : 's'}` : '')
      + (blank >= 60 ? `; no record for ${dur(blank)} (the watchdog was not running: the box froze, restarted or was stopped)` : '') + '.';
  }

  // An event's time: the date and the minute; to the second in a span of an hour or less.
  const when = (t, span) => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    second: span <= 3600 ? '2-digit' : undefined });
  // An axis tick: the date over a day, the minute under, the second under a quarter of an hour.
  const tick = (t, span) => new Date(t * 1000).toLocaleString([], span > 86400 ? { month: 'short', day: 'numeric' }
    : span < 900 ? { hour: '2-digit', minute: '2-digit', second: '2-digit' } : { hour: '2-digit', minute: '2-digit' });
  const NAMED = (r) => (r.s ? `${NAMES[r.s] || r.s}${r.k === 'held' ? ' (held)' : r.k === 'stalled' ? ' needed' : r.k === 'skip' ? ' (not possible)' : ''}`
    : r.k === 'down' ? 'down' : r.k === 'up' ? 'back up' : r.k === 'start' ? 'watching again' : r.k);

  // The symptoms: a column for each five-minute slot with misses (its most within the window), muted
  // below the sensitivity's line and in the accent at or above it (the box stepped in); the line
  // itself, as it was set over time, labelled.
  function symptoms(svg, rows, from, now, x) {   // now: the window's end
    const all = rows.filter((r) => r.k === 'm');
    const sym = all.filter((r) => r.at + 300 >= from && r.at <= now);
    const before = all.filter((r) => r.at < from).pop();
    let th = before ? before.th : (sym[0] || all[all.length - 1] || {}).th;
    const top = Math.max(2, th || 0, ...sym.map((r) => Math.max(r.n, r.th || 0))) + 1;
    const ys = (v) => T + SYM_H - (v / top) * SYM_H;
    svg.append(make('text', { x: L - 8, y: T + 10, class: 'ladder-axis', 'text-anchor': 'end' }, 'missed'));
    svg.append(make('text', { x: L - 8, y: T + 22, class: 'ladder-axis', 'text-anchor': 'end' }, 'checks'));
    svg.append(make('line', { x1: L, x2: W - R, y1: ys(0), y2: ys(0), class: 'ladder-grid' }));
    svg.append(make('text', { x: L - 8, y: ys(0) + 4, class: 'ladder-axis', 'text-anchor': 'end' }, '0'));
    let dUnder = '', dOver = '';
    for (const r of sym) {
      const x0 = Math.max(x(r.at), L), x1 = Math.min(x(r.at + 300), W - R), wdt = Math.max(x1 - x0 - 0.5, 1.2);
      if (!r.n) continue;
      const seg = `M${x0},${ys(0)}V${ys(r.n)}h${wdt}V${ys(0)}Z`;
      if (r.th && r.n >= r.th) dOver += seg; else dUnder += seg;
    }
    if (dUnder) svg.append(make('path', { d: dUnder, class: 'ladder-sym' }));
    if (dOver) svg.append(make('path', { d: dOver, class: 'ladder-sym over' }));
    if (th) {
      let d = `M${L},${ys(th)}`;
      for (const r of sym) if (r.th && r.th !== th) { th = r.th; d += `H${Math.max(x(r.at), L)}V${ys(th)}`; }
      d += `H${W - R}`;
      svg.append(make('path', { d, class: 'ladder-th' }));
      svg.append(make('text', { x: W - R, y: ys(th) - 4, class: 'ladder-axis', 'text-anchor': 'end' }, `sensitivity ${th}`));
    }
    // A roam between access points: a short tick on the baseline, muted, one each.
    const roams = rows.filter((r) => r.k === 'roam' && r.at >= from && r.at <= now);
    if (roams.length) svg.append(make('path', { d: roams.map((r) => `M${x(r.at)},${ys(0)}v-7`).join(''), class: 'ladder-roam' }));
    return { most: Math.max(0, ...sym.map((r) => r.n)), over: sym.filter((r) => r.th && r.n >= r.th).length, roams: roams.length };
  }

  // draw(box, rows, hs, from, to, now, zoom): zoom(a, b) asks for the span [a, b].
  function draw(box, rows, hs, from, to, now, zoom) {
    const span = to - from, x = (t) => L + ((t - from) / span) * (W - L - R);
    const tAt = (clientX, rect) => from + ((((clientX - rect.left) / rect.width) * W - L) / (W - L - R)) * span;
    const y = (v) => H - B - (v / (LEVELS.length - 1)) * (H - B - LAD_T);
    const { segs, bands, marks, holes: blank } = model(rows, from, to, hs);
    const period = PERIODS.find((p) => p[1] === span && to >= now);
    const said = summary(rows, from, to, hs);
    const svg = make('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ladder-svg', role: 'img', tabindex: '0',
      'aria-label': `The watchdog's escalation ${period ? `over the last ${period[0]}` : `from ${when(from, span)} to ${when(to, span)}`}: ${said}` });
    for (const [a, b] of bands) svg.append(make('rect', { x: x(a), y: T, width: Math.max(x(b) - x(a), 1.5), height: H - B - T, class: 'ladder-band' }));
    const hatch = `ladder-hatch-${++hatches}`;
    if (blank.length) {
      const pat = make('pattern', { id: hatch, width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' });
      pat.append(make('line', { x1: 0, y1: 0, x2: 0, y2: 6, class: 'ladder-hatch' }));
      const defs = make('defs');
      defs.append(pat);
      svg.append(defs);
    }
    for (const [a, b] of blank) {
      svg.append(make('rect', { x: x(a), y: T, width: Math.max(x(b) - x(a), 1.5), height: H - B - T, class: 'ladder-hole', fill: `url(#${hatch})` }));
      if (x(b) - x(a) > 54) svg.append(make('text', { x: (x(a) + x(b)) / 2, y: H - B - 6, class: 'ladder-axis', 'text-anchor': 'middle' }, 'no record'));
    }
    const sy = symptoms(svg, rows, from, to, x);
    const symSaid = rows.some((r) => r.k === 'm') ? ` At most ${sy.most} missed check${sy.most === 1 ? '' : 's'} within the pace's window;`
      + ` at or over the line in ${sy.over} five-minute spell${sy.over === 1 ? '' : 's'}.` : '';
    const roamSaid = sy.roams ? ` ${sy.roams} roam${sy.roams === 1 ? '' : 's'} between access points (the ticks on the baseline).` : '';
    svg.setAttribute('aria-label', svg.getAttribute('aria-label') + symSaid + roamSaid);
    LEVELS.forEach((name, i) => {
      svg.append(make('line', { x1: L, x2: W - R, y1: y(i), y2: y(i), class: 'ladder-grid' }));
      svg.append(make('text', { x: L - 8, y: y(i) + 4, class: 'ladder-axis', 'text-anchor': 'end' }, NAMES[name]));
    });
    for (let k = 0; k <= 4; k++) {
      const t = from + (span * k) / 4;
      // Ticks short enough never to meet: the time over a day, the date over longer.
      svg.append(make('text', { x: x(t), y: H - 8, class: 'ladder-axis', 'text-anchor': k === 0 ? 'start' : k === 4 ? 'end' : 'middle' },
        k === 4 && to >= now ? 'now' : tick(t, span)));
    }
    let d = '';
    for (const pts of segs) {
      d += `M${x(pts[0][0])},${y(pts[0][1])}`;
      for (let i = 1; i < pts.length; i++) d += `H${x(pts[i][0])}V${y(pts[i][1])}`;
    }
    if (d) svg.append(make('path', { d, class: 'ladder-line' }));
    for (const m of marks) {
      const cx = x(m.at), cy = y(level(m.s)), hollow = m.k === 'held' || m.k === 'stalled';
      const cls = `ladder-mark${hollow ? ' hollow' : ''}`;
      if (m.b === 'hand' && !hollow) svg.append(make('polygon', { points: `${cx},${cy - 6} ${cx + 6},${cy} ${cx},${cy + 6} ${cx - 6},${cy}`, class: cls }));
      else if (m.b === 'flap') svg.append(make('polygon', { points: `${cx},${cy - 6} ${cx + 6},${cy + 5} ${cx - 6},${cy + 5}`, class: cls }));
      else svg.append(make('circle', { cx, cy, r: 4.5, class: cls }));
    }
    // Hover and keys: a crosshair that snaps to the nearest event, its words in the tooltip.
    const seen = rows.filter((r) => r.at >= from && r.at <= to && isEvent(r));
    const cross = make('line', { y1: T, y2: H - B, class: 'ladder-cross', visibility: 'hidden' });
    const sel = make('rect', { y: T, height: H - B - T, class: 'ladder-sel', visibility: 'hidden' });
    svg.append(cross, sel);
    // The readout: a line of its own above the plot (its room kept), so it never covers a mark.
    const tip = make('div', { class: 'ladder-tip', role: 'status' }, 'Point at the chart, or focus it and use ← →, for each event. '
      + 'To zoom in: click or tap an event (or press Enter), or drag across a span.');
    let cur = -1;
    const show = (i) => {
      if (!seen.length) return;
      cur = Math.max(0, Math.min(i, seen.length - 1));
      const r = seen[cur], px = x(r.at);
      cross.setAttribute('x1', px); cross.setAttribute('x2', px); cross.setAttribute('visibility', 'visible');
      tip.replaceChildren(make('strong', {}, NAMED(r)),
      make('span', {}, ` ${when(r.at, span)}${r.b ? `, ${BY[r.b]}` : ''}${r.i ? ` (${r.i})` : ''}`), make('br'), make('span', { class: 'ladder-tip-text' }, r.t || ''));
      tip.classList.add('on');
    };
    const nearest = (t) => {
      let best = -1, dist = Infinity;
      seen.forEach((r, i) => { const dd = Math.abs(r.at - t); if (dd < dist) { dist = dd; best = i; } });
      return best;
    };
    // A click or tap: the cluster around the nearest event, events closer than a fiftieth of the
    // span chained together, with a margin; at least five minutes.
    const zoomOn = (i) => {
      if (i < 0 || !zoom) return;
      const near = span / 50;
      let a = i, b = i;
      while (a > 0 && seen[a].at - seen[a - 1].at <= near) a--;
      while (b < seen.length - 1 && seen[b + 1].at - seen[b].at <= near) b++;
      let lo = seen[a].at, hi = seen[b].at;
      const pad = Math.max((hi - lo) * 0.25, 120);
      lo -= pad; hi += pad;
      if (hi - lo < 300) { const mid = (lo + hi) / 2; lo = mid - 150; hi = mid + 150; }
      if (hi - lo > span * 0.6) { lo = seen[i].at - span / 8; hi = seen[i].at + span / 8; }
      zoom(lo, hi);
    };
    let drag = null;   // {x0: clientX, t0, moved}
    svg.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0 && ev.pointerType === 'mouse') return;
      drag = { x0: ev.clientX, t0: tAt(ev.clientX, svg.getBoundingClientRect()), moved: false };
      if (svg.setPointerCapture && ev.pointerId != null) try { svg.setPointerCapture(ev.pointerId); } catch (e) { /* not captured: fine */ }
    });
    svg.addEventListener('pointermove', (ev) => {
      const rect = svg.getBoundingClientRect(), t = tAt(ev.clientX, rect);
      if (drag && Math.abs(ev.clientX - drag.x0) > 6) {
        drag.moved = true;
        const a = Math.max(Math.min(drag.t0, t), from), b = Math.min(Math.max(drag.t0, t), to);
        sel.setAttribute('x', x(a)); sel.setAttribute('width', Math.max(x(b) - x(a), 1)); sel.setAttribute('visibility', 'visible');
        tip.replaceChildren(make('strong', {}, 'Zoom to '), make('span', {}, `${when(a, b - a)} – ${when(b, b - a)}`));
        return;
      }
      const best = nearest(t);
      if (best >= 0) show(best);
    });
    svg.addEventListener('pointerup', (ev) => {
      const d0 = drag;
      drag = null;
      sel.setAttribute('visibility', 'hidden');
      if (!d0) return;
      const t = tAt(ev.clientX, svg.getBoundingClientRect());
      if (d0.moved) {
        const a = Math.max(Math.min(d0.t0, t), from), b = Math.min(Math.max(d0.t0, t), to);
        if (b - a >= 60 && zoom) zoom(a, b);
      } else zoomOn(nearest(t));
    });
    svg.addEventListener('pointercancel', () => { drag = null; sel.setAttribute('visibility', 'hidden'); });
    svg.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); });
    svg.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') { ev.preventDefault(); show(cur < 0 ? seen.length - 1 : cur + (ev.key === 'ArrowRight' ? 1 : -1)); }
      else if (ev.key === 'Enter' && cur >= 0) { ev.preventDefault(); zoomOn(cur); }
    });
    svg.addEventListener('blur', () => { cross.setAttribute('visibility', 'hidden'); });
    const table = make('details', { class: 'ladder-table' }, null);
    table.append(make('summary', {}, `The ${seen.length} event${seen.length === 1 ? '' : 's'} as a table`),
      make('table', {}, null));
    const tb = table.querySelector('table');
    tb.append(make('tr', {}, null));
    tb.firstChild.append(make('th', {}, 'When'), make('th', {}, 'What'), make('th', {}, 'Step'), make('th', {}, 'By'));
    for (const r of seen.slice().reverse()) {
      const tr = make('tr');
      tr.append(make('td', {}, when(r.at, span)), make('td', {}, r.t || r.k), make('td', {}, r.s ? NAMES[r.s] || r.s : ''), make('td', {}, r.b ? BY[r.b] : ''));
      tb.append(tr);
    }
    const frame = make('div', { class: 'ladder-frame' });
    frame.append(tip, svg);
    box.replaceChildren(frame, make('p', { class: 'setting-desc ladder-sum' }, `Over this time: ${said}${symSaid}${roamSaid}`),
      make('p', { class: 'setting-desc ladder-key' }, 'Above: missed checks within the pace\'s window, each five minutes; at or over the sensitivity\'s line'
        + ' (in colour) the link goes on the ladder. Below: the furthest each outage went; shaded, the outages. '
        + '● a step the watchdog took, ◆ one asked for on /admin, ▲ a flapping link\'s repair; hollow, one held (guests on the hotspot, say) or a stall. '
        + 'Hatched: no record (the watchdog was not running).'),
      table);
  }

  // render(box, rows, {now, gaps}): the period chips (1 h to 72 days) above the chart, 7 days to start
  // with; gaps: the server's spans with no record. A zoom is kept on the box (data-zoom, a stack of
  // [from, to]) across redraws, until Zoom out or a period is chosen.
  function render(box, rows, opts = {}) {
    const now = opts.now ?? Date.now() / 1000;
    rows = (rows || []).filter((r) => r && typeof r.at === 'number').sort((a, b) => a.at - b.at);
    const hs = holes(rows, opts.gaps);
    const chart = make('div', { class: 'ladder-chart' });
    const chips = make('div', { class: 'chip-group ladder-periods', role: 'radiogroup', 'aria-label': 'Over the last' });
    const bar = make('div', { class: 'ladder-zoom' });
    let span = Number(box.dataset.span) || 7 * 86400;
    let stack = [];
    try { stack = JSON.parse(box.dataset.zoom || '[]').filter((z) => Array.isArray(z) && z[1] > z[0]); } catch (e) { stack = []; }
    const redraw = () => {
      box.dataset.zoom = JSON.stringify(stack);
      const z = stack[stack.length - 1];
      for (const c of chips.children) { const on = !z && Number(c.dataset.span) === span; c.classList.toggle('selected', on); c.setAttribute('aria-checked', on); }
      const [from, to] = z || [now - span, now];
      bar.replaceChildren();
      if (z) {
        const out = make('button', { type: 'button', class: 'chip' }, 'Zoom out');
        out.addEventListener('click', () => { stack.pop(); redraw(); });
        bar.append(out, make('span', { class: 'setting-desc' }, ` Showing ${when(from, to - from)} – ${when(to, to - from)} (${dur(to - from)}).`));
      }
      draw(chart, rows, hs, from, to, now, (a, b) => {
        a = Math.max(a, now - 72 * 86400); b = Math.min(b, now);
        if (b - a >= 60) { stack.push([a, b]); redraw(); }
      });
    };
    const pick = (s) => { span = s; box.dataset.span = String(s); stack = []; redraw(); };
    for (const [label, s] of PERIODS) {
      const b = make('button', { type: 'button', class: 'chip', role: 'radio', 'data-span': s }, label);
      b.addEventListener('click', () => pick(s));
      chips.append(b);
    }
    box.replaceChildren(chips, bar, chart);
    if (!rows.length) {
      chart.replaceChildren(make('p', { class: 'setting-desc' }, 'Nothing recorded yet: the watchdog records each outage and each step it takes from now on.'));
      return;
    }
    redraw();
  }
  return { render, model, summary, holes, LEVELS };
})();
if (typeof window !== 'undefined') window.LadderChart = LadderChart;
