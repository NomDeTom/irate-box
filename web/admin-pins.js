// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The pin map page: read gpioinfo and the pin mux through the root helper, then draw the board's 40-pin header and
// what claims each pin. The layout follows the nomdetom.github.io pinout map: physical pins in their real order.
(function () {
  'use strict';
  const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
  const $ = (id) => document.getElementById(id);
  let lastAt = 0;
  let lastPins = null;
  const say = (text, ok) => { const n = $('pins-note'); n.textContent = text; n.classList.toggle('bad', !ok); n.hidden = !text; };
  async function get() { const response = await fetch('/admin/pins'); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
  async function post(body) {
    const response = await fetch('/admin/pins', { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
    return data;
  }

  function pinLabel(pin) {
    return pin.name || (pin.gpio === null || pin.gpio === undefined ? 'power' : `GPIO${pin.gpio}`);
  }
  function pinDetails(pin) {
    const bits = [];
    if (pin.rmio !== null && pin.rmio !== undefined) bits.push(`RM_IO${pin.rmio}`);
    if (pin.role) bits.push(pin.role);
    if (pin.gpio !== null && pin.gpio !== undefined) bits.push(`GPIO ${pin.gpio}`);
    if (pin.type !== 'gpio') bits.push(pin.type === 'gnd' ? 'ground' : 'power');
    else if (pin.used) bits.push(pin.consumer || 'taken');
    else bits.push('free');
    return bits.join(' · ');
  }
  function pinCard(pin) {
    const cls = pin.type === 'gpio' ? (pin.used ? 'used' : 'free') : 'power';
    const card = document.createElement('div');
    card.className = `pin ${cls}`;
    const number = document.createElement('b');
    number.textContent = String(pin.pin);
    const name = document.createElement('span');
    name.textContent = pinLabel(pin);
    const detail = document.createElement('small');
    detail.textContent = pinDetails(pin);
    card.append(number, name, detail);
    return card;
  }
  function drawHeader(header) {
    const map = $('pins-map');
    map.replaceChildren();
    if (!header || !header.length) return;
    if (!header.every((pin) => typeof pin.pin === 'number')) {
      // A board whose header is not known: its lines, one card each, as the page did before.
      const grid = document.createElement('div');
      grid.className = 'pinmap';
      for (const pin of header) grid.append(pinCard(pin));
      map.append(grid);
      return;
    }
    const byPin = new Map(header.map((pin) => [pin.pin, pin]));
    const max = Math.max(...header.map((pin) => pin.pin));
    for (let n = 1; n <= max; n += 2) {
      const row = document.createElement('div');
      row.className = 'pinrow';
      const left = byPin.get(n);
      const right = byPin.get(n + 1);
      row.append(left ? pinCard(left) : document.createElement('span'));
      row.append(right ? pinCard(right) : document.createElement('span'));
      map.append(row);
    }
  }
  function drawTable(header) {
    const host = $('pins-table');
    host.replaceChildren();
    if (!header || !header.length) return;
    const table = document.createElement('table');
    table.className = 'admin-table';
    const head = document.createElement('thead');
    const headRow = document.createElement('tr');
    for (const title of ['Pin', 'Name', 'GPIO', 'RM_IO', 'Function', 'Claimed by', 'State']) {
      const th = document.createElement('th');
      th.textContent = title;
      headRow.append(th);
    }
    head.append(headRow);
    const body = document.createElement('tbody');
    for (const pin of header) {
      const tr = document.createElement('tr');
      const cells = [
        String(pin.pin),
        pinLabel(pin),
        pin.gpio === null || pin.gpio === undefined ? '' : String(pin.gpio),
        pin.rmio === null || pin.rmio === undefined ? '' : `RM_IO${pin.rmio}`,
        pin.role || (pin.type === 'gpio' ? '' : pin.type === 'gnd' ? 'ground' : 'power'),
        pin.consumer || '',
        pin.type !== 'gpio' ? 'power' : pin.used ? 'taken' : 'free',
      ];
      for (const value of cells) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(head, body);
    host.append(table);
  }
  function textOf(pins) {
    if (!pins || !pins.header || !pins.header.length) return '';
    const title = pins.model || pins.board || 'this board';
    const lines = [title, ''];
    const header = pins.header;
    const desc = (pin) => {
      const bits = [pinLabel(pin)];
      if (pin.rmio !== null && pin.rmio !== undefined) bits.push(`RM_IO${pin.rmio}`);
      if (pin.role) bits.push(pin.role);
      if (pin.consumer) bits.push(pin.consumer);
      else if (pin.type === 'gpio') bits.push('free');
      return bits.join(' ');
    };
    if (header.every((pin) => typeof pin.pin === 'number')) {
      const byPin = new Map(header.map((pin) => [pin.pin, pin]));
      const max = Math.max(...header.map((pin) => pin.pin));
      const width = Math.max(...header.map((pin) => desc(pin).length));
      for (let n = 1; n <= max; n += 2) {
        const left = byPin.get(n);
        const right = byPin.get(n + 1);
        const leftText = left ? `${String(n).padStart(2)} ${desc(left).padEnd(width)}` : '';
        const rightText = right ? `${String(n + 1).padStart(2)} ${desc(right)}` : '';
        lines.push(`${leftText}   ${rightText}`.trimEnd());
      }
    } else {
      for (const pin of header) lines.push(`${pin.pin} ${desc(pin)}`);
    }
    return lines.join('\n');
  }
  function drawClaims(lines) {
    const claims = $('pins-claims');
    claims.replaceChildren();
    const used = (lines || []).filter((line) => line.used);
    if (!used.length) { claims.append(Object.assign(document.createElement('p'), { textContent: 'No GPIO line is claimed.' })); return; }
    const ul = document.createElement('ul');
    for (const line of used) {
      const li = document.createElement('li');
      li.textContent = `${line.chip} line ${line.line}: ${line.consumer || line.name || 'claimed'}`;
      ul.append(li);
    }
    claims.append(ul);
  }
  function render(data) {
    const pins = data && data.pins;
    $('pins-copy').disabled = true;
    if (!pins) { $('pins-board').textContent = 'Not read yet.'; return; }
    lastAt = pins.at || lastAt;
    lastPins = pins;
    const when = `${pins.model || pins.board || 'this board'}, read ${pins.at ? new Date(pins.at * 1000).toLocaleString() : 'now'}.`;
    $('pins-board').textContent = pins.note ? `${when} ${pins.note}` : when;
    drawHeader(pins.header || []);
    drawTable(pins.header || []);
    drawClaims(pins.lines || []);
    $('pins-copy').disabled = false;
  }
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
      area.remove();
      return ok;
    }
  }
  const answerSaid = (result) => (result.msg && typeof AW !== 'undefined' ? AW.say(result.msg) : result.message);
  async function wait(id, before) {
    for (let i = 0; i < 40; i++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      let data;
      try { data = await get(); } catch (_) { continue; }
      const done = (data.results || []).find((result) => result.id === id);
      if (done && !done.ok) {
        say(answerSaid(done) || 'The pins could not be read.', false);
        $('pins-scan').disabled = false;
        return;
      }
      // The answer is the request's own; its at is the box's clock, compared with the box's at before the click,
      // so a browser and box whose clocks differ cannot leave a good read waiting or an old one looking fresh.
      if (done && done.ok && data.pins && data.pins.at && data.pins.at >= before) {
        render(data);
        say(answerSaid(done) || 'Read.', true);
        $('pins-scan').disabled = false;
        return;
      }
    }
    say('The pins were not read in time.', false);
    $('pins-scan').disabled = false;
  }
  $('pins-scan').addEventListener('click', async () => {
    $('pins-scan').disabled = true;
    say('Reading the GPIO lines…', true);
    const before = lastAt;
    try {
      const reply = await post({ action: 'pins' });
      await wait(reply.id, before);
    } catch (err) { say(err.message || 'The pins could not be read.', false); $('pins-scan').disabled = false; }
  });
  $('pins-copy').addEventListener('click', async () => {
    if (!lastPins) return;
    const ok = await copyText(textOf(lastPins));
    say(ok ? 'The header is copied as text.' : 'The header could not be copied.', ok);
  });
  if (typeof window !== 'undefined') window.rkRenderPins = render;
  get().then(render).catch(() => {});
})();
