// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The pin map page: read gpioinfo through the root helper, then draw the header and what claims each pin.
(function () {
  'use strict';
  const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
  const $ = (id) => document.getElementById(id);
  const say = (text, ok) => { const n = $('pins-note'); n.textContent = text; n.classList.toggle('bad', !ok); n.hidden = !text; };
  async function get() { const response = await fetch('/admin/pins'); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
  async function post(body) {
    const response = await fetch('/admin/pins', { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
    return data;
  }
  function pinRow(pin) {
    const used = pin.used;
    const label = pin.gpio === null || pin.gpio === undefined ? 'power' : `GPIO${pin.gpio}`;
    const row = document.createElement('div');
    row.className = `pin ${used ? 'used' : 'free'}`;
    const number = document.createElement('b');
    number.textContent = String(pin.pin);
    const gpio = document.createElement('span');
    gpio.textContent = label;
    const who = document.createElement('small');
    who.textContent = used ? pin.consumer || pin.name || 'taken' : 'free';
    row.append(number, gpio, who);
    return row;
  }
  function render(data) {
    const pins = data && data.pins;
    if (!pins) { $('pins-board').textContent = 'Not read yet.'; return; }
    const when = `${pins.board || 'this board'}, read ${pins.at ? new Date(pins.at * 1000).toLocaleString() : 'now'}.`;
    $('pins-board').textContent = pins.note ? `${when} ${pins.note}` : when;
    const map = $('pins-map');
    map.replaceChildren(...(pins.header || []).map(pinRow));
    const used = (pins.lines || []).filter((line) => line.used);
    const claims = $('pins-claims');
    claims.replaceChildren();
    const h = document.createElement('h3');
    h.textContent = 'Taken by something else';
    claims.append(h);
    if (!used.length) { claims.append(Object.assign(document.createElement('p'), { textContent: 'No GPIO line is claimed.' })); return; }
    const ul = document.createElement('ul');
    for (const line of used) {
      const li = document.createElement('li');
      li.textContent = `${line.chip} line ${line.line}: ${line.consumer || line.name || 'claimed'}`;
      ul.append(li);
    }
    claims.append(ul);
  }
  const answerSaid = (result) => (result.msg && typeof AW !== 'undefined' ? AW.say(result.msg) : result.message);
  async function wait(id, since) {
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
      if (data.pins && data.pins.at && data.pins.at >= since) {
        render(data);
        say((done && answerSaid(done)) || 'Read.', true);
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
    const since = Date.now() / 1000;
    try {
      const reply = await post({ action: 'pins' });
      await wait(reply.id, since);
    } catch (err) { say(err.message || 'The pins could not be read.', false); $('pins-scan').disabled = false; }
  });
  get().then(render).catch(() => {});
})();
