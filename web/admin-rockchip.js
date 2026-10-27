// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Rockchip boards, /admin/rockchip.html (apps.d/57-rockchip.json; hub/rkusb.py, root/rkboard.py): the board on the
// box's USB, as the hub reads it from sysfs, and the one step that fits its mode: switch a board running its own
// system to Loader mode (adb), erase a board in Loader mode, or, in Maskrom mode, send the loader for the chip
// selected and erase. Each step goes to the root helper; its answer is said under the buttons. Behind the same
// login as /admin, asking /admin/rkboard.
(function () {
  'use strict';
  const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
  function say(text, ok, at) { at.textContent = text; at.classList.toggle('bad', !ok); at.hidden = !text; }
  const el = (tag, props = {}, ...kids) => {
    // A button with no class of its own is an action button (the control vocabulary).
    if (tag === 'button' && !props.className) props = { ...props, className: 'action-btn' };
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  };
  async function getJSON(url) { const response = await fetch(url); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
  async function postJSON(url, body) {
    const response = await fetch(url, { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((data.message && AW.say(data.message)) || data.error || `HTTP ${response.status}`);
    return data;
  }
  // A result of the root helper in the reader's language: its message (msg.py), else its English.
  const resultSaid = (result) => (result.msg ? AW.say(result.msg) : result.message);
  // "a", "a and b", "a, b and c".
  const listSaid = (items) => (items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`);

  const MODE = {
    system: "A board running its own system is connected (Luckfox's factory image). The box can switch it to Loader mode.",
    loader: 'A board in Loader mode is connected. It is ready to erase.',
    maskrom: 'A board in Maskrom mode is connected. Select its chip, so that the box sends the right loader.',
    other: 'A Rockchip device is connected, but not in a mode that the box can use. Hold its boot button while you connect it.',
  };
  const ERASE_ASK = "Erase the board's onboard flash? This deletes the system on it, and it cannot be undone.";
  let waiting = null;
  let chipChosen = 'rk3506b';
  let poll = null;
  let shown = '';

  async function load() {
    clearTimeout(poll);
    let data = null;
    try {
      data = await getJSON('/admin/rkboard');
    } catch (_) {
      data = null;
    }
    if (data) render(data);
    poll = setTimeout(load, document.hidden ? 30000 : waiting ? 2000 : 3000);
  }

  function render(data) {
    const note = document.getElementById('rk-note');
    if (waiting) {
      const done = (data.results || []).find((r) => r.id === waiting);
      if (done) {
        waiting = null;
        say(resultSaid(done), done.ok, note);
      }
    }
    const tools = data.tools || {};
    const missing = ['rkdeveloptool', 'adb'].filter((t) => !tools[t]);
    const toolsEl = document.getElementById('rk-tools');
    toolsEl.hidden = !missing.length;
    toolsEl.replaceChildren(
      `${listSaid(missing)} ${missing.length === 1 ? 'is' : 'are'} not installed. They come with the Rockchip boards toolkit: install it on `,
      el('a', { href: '/admin/#toolkits', textContent: 'Toolkits' }),
      '.',
    );
    const boards = data.boards || [];
    const busy = !!waiting || data.pending > 0;
    const key = JSON.stringify([boards, busy, missing, chipChosen]);
    if (key === shown) return;
    shown = key;
    const board = document.getElementById('rk-board');
    const chip = document.getElementById('rk-chip');
    const actions = document.getElementById('rk-actions');
    chip.hidden = true;
    actions.replaceChildren();
    if (!boards.length) {
      board.replaceChildren(el('p', {
        className: 'setting-desc',
        textContent: "No Rockchip board is connected. Connect the board's USB port to a USB port of the box. A board with no system on its flash: hold its boot button while you connect it.",
      }));
      return;
    }
    if (boards.length > 1) {
      board.replaceChildren(el('p', { className: 'setting-desc warn-text', textContent: `${boards.length} boards are connected. Connect one at a time.` }));
      return;
    }
    const b = boards[0];
    board.replaceChildren(
      el('p', { textContent: MODE[b.mode] || MODE.other }),
      el('p', { className: 'setting-desc', textContent: `USB ${b.id}${b.product ? `, ${b.product}` : ''}, port ${b.path}.` }),
    );
    const step = (text, body, ask) => el('button', {
      type: 'button',
      className: 'action-btn',
      textContent: text,
      disabled: busy || (body.action === 'loader' ? !tools.adb : !tools.rkdeveloptool),
      onclick: async (event) => {
        if (ask && !(await AW.confirmHere(ask, { anchor: event.currentTarget, label: text }))) return;
        try {
          // The chip as selected now, not when the page was drawn.
          if (body.action === 'erase' && b.mode === 'maskrom') body.loader = chipChosen;
          waiting = (await postJSON('/admin/rkboard', body)).id;
          say(body.action === 'loader' ? 'Switching the board to Loader mode…' : "Erasing the board's flash…", true, note);
          shown = '';
          load();
        } catch (err) {
          say(err.message, false, note);
        }
      },
    });
    if (b.mode === 'system') actions.append(step('Switch to Loader mode', { action: 'loader' }));
    if (b.mode === 'loader') actions.append(step('Erase the flash', { action: 'erase', loader: null }, ERASE_ASK));
    if (b.mode === 'maskrom') {
      chip.hidden = false;
      const box = el('div', { id: 'rk-chip-tiles' });
      AW.choices(box, 'rk-chip-tiles', (data.loaders || []).map((l) => ({ value: l.id, title: l.chip, desc: (l.boards || []).join(', ') })), {
        value: chipChosen,
        onChange: (v) => { chipChosen = v; },
      });
      chip.replaceChildren(el('span', { className: 'setting-name', textContent: 'Chip' }), box);
      actions.append(step('Erase the flash', { action: 'erase', loader: chipChosen }, ERASE_ASK));
    }
  }

  load();
  // For the page tests: the drawing, called with data of their own.
  if (typeof window !== 'undefined') window.rkRender = render;
}());
