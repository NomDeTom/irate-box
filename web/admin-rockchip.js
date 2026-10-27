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
    if (data) { render(data); renderSetup(data.setup); }
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

  // ---- Boards to set up (hub/rksetup.py): one card per setup hotspot in range, its drawer the choices sent to the
  // board as Armbian's first-boot presets, and the steps. The choices are kept per board while the page is open.
  const { h } = AW;
  // What Armbian's preset file cannot hold (rksetup.UNSAFE): it is sourced by bash inside double quotes.
  const UNSAFE = { '"': 'a double quote', $: 'a dollar sign', '`': 'a backtick', '\\': 'a backslash' };
  const HOSTNAME_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
  const USER_RE = /^[a-z_][a-z0-9_-]{0,31}$/;
  // As rksetup.problem: why a value cannot be sent to a new board, or ''.
  function problem(value, kind) {
    value = value == null ? '' : String(value);
    for (const [ch, name] of Object.entries(UNSAFE)) if (value.includes(ch)) return `It has ${name}, which Armbian's setup cannot take.`;
    if ([...value].some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) return "It has a line break or another control character, which Armbian's setup cannot take.";
    if (kind === 'wifi-password' && value && (value.length < 8 || value.length > 63)) return 'A WiFi password is 8 to 63 characters.';
    if (kind === 'password' && value.length < 8) return 'A password here is at least 8 characters.';
    if (kind === 'hostname' && !HOSTNAME_RE.test(value)) return 'A name is lower-case letters, digits and hyphens, up to 63, not starting or ending with a hyphen.';
    if (kind === 'user' && !USER_RE.test(value)) return 'A user name is lower-case letters, digits, hyphens and underscores, starting with a letter.';
    return '';
  }
  const ROUTES = [
    { value: 'hotspot', title: "The board's setup hotspot", desc: "The box joins the board's hotspot for about a minute, sends the choices below, and comes back to its own network." },
    { value: 'card', title: 'Settings on the SD card', desc: "The choices written to the card before the board's first start: no hotspot, and Armbian's default password never in use.", why: 'the box cannot write a card yet' },
    { value: 'ssh', title: "Armbian's default password, over a cable", desc: 'For stock Armbian on a wired network: the box signs in as root with Armbian\'s default password at once, and sets the board up.', why: 'the box does not look for such a board yet' },
    { value: 'serial', title: 'The serial console', desc: "For a board wired to a USB serial adapter on this box: the box types the choices at the board's first start.", why: 'the box does not use a serial console yet' },
    { value: 'usb', title: "Networking over the board's USB", desc: 'The cable used to erase the board, as a network.', why: 'mPWRD-OS has not been seen to offer it' },
    { value: 'bluetooth', title: 'Bluetooth', desc: "Meshtastic's flasher sends the WiFi over Bluetooth.", why: 'only Raspberry Pi images take it' },
  ];
  const STEPS = [
    ['find', 'Find the board'],
    ['join', "Join the board's setup hotspot"],
    ['check', "Check that it is Armbian's setup"],
    ['send', 'Send the choices'],
    ['leave', "Back to this box's network"],
    ['again', 'Find the board on the network'],
    ['finish', 'Finish over SSH: the keys, the name, the setup page stopped'],
  ];
  const picked = new Map();   // hotspot name -> the choices made for that board
  let setupShown = '';
  function choicesFor(id, defaults) {
    if (!picked.has(id)) {
      picked.set(id, {
        route: 'hotspot', network: defaults.ssid ? 'same' : 'other', wifiPass: '', otherSsid: '', otherPass: '',
        rootPass: 'generated', rootTyped: '', userName: 'armbian', userPass: 'generated', userTyped: '', lockRoot: false, key: '',
        hostname: 'lyra-1', place: 'box', locale: defaults.locale || 'en_GB.UTF-8', timezone: defaults.timezone || 'Etc/UTC',
        country: defaults.country || 'GB', address: 'dhcp', ip: '', mask: '255.255.255.0', gateway: '', dns: '',
        after: 'leave',
      });
    }
    return picked.get(id);
  }
  // Every typed value the board would be sent, with its problem: [[label, problem]].
  function problems(c, ready) {
    const out = [];
    const add = (label, value, kind) => { const p = problem(value, kind); if (p) out.push([label, p]); };
    if (c.network === 'same-typed') add("The network's password", c.wifiPass, 'wifi-password');
    if (c.network === 'other') {
      if (!c.otherSsid) out.push(["The network's name", 'Enter the name of the network the board joins.']);
      add("The network's name", c.otherSsid, 'text');
      add("The network's password", c.otherPass, 'wifi-password');
    }
    if (c.rootPass === 'typed') add("Root's password", c.rootTyped, 'password');
    add("The user's name", c.userName, 'user');
    if (c.userPass === 'typed') add("The user's password", c.userTyped, 'password');
    add('Name on the network', c.hostname, 'hostname');
    if (c.place === 'chosen') { add('Locale', c.locale, 'text'); add('Time zone', c.timezone, 'text'); add('WiFi country', c.country, 'text'); }
    if (c.address === 'fixed') for (const k of ['ip', 'mask', 'gateway', 'dns']) { if (!c[k]) out.push(['Fixed address', 'Fill in the address, mask, gateway and DNS.']); add('Fixed address', c[k], 'text'); }
    add('Your SSH key', c.key, 'text');
    if (!ready) out.unshift(['', "Setting up is not built yet: the box's half (joining the hotspot and sending these choices) comes next. The choices are kept while this page is open."]);
    return out;
  }

  function setupBody(item, setup) {
    const c = choicesFor(item.id, setup.defaults || {});
    const d = setup.defaults || {};
    const action = h('button', { type: 'button', class: 'action-btn primary', text: 'Set up the board' });
    const reason = h('p', { class: 'setting-desc bad', role: 'status' });
    const refresh = () => {
      const list = problems(c, setup.ready);
      action.disabled = list.length > 0;
      reason.textContent = list.length ? list.map(([label, p]) => (label ? `${label}: ${p}` : p)).join(' ') : '';
      reason.hidden = !list.length;
    };
    // A typed value: its field, and its problem said under it as it is typed.
    const field = (label, key, kind, props = {}) => {
      const said = h('span', { class: 'setting-desc bad', hidden: true });
      const input = h(props.rows ? 'textarea' : 'input', { type: props.rows ? null : props.type || 'text', name: `rk-${key}`, autocomplete: 'off', spellcheck: 'false', rows: props.rows || null, placeholder: props.placeholder || null });
      input.value = c[key] || '';
      input.addEventListener('input', () => {
        c[key] = input.value;
        const p = kind ? problem(input.value, kind) : '';
        said.textContent = p;
        said.hidden = !p;
        refresh();
      });
      return h('label', { class: 'aw-field' }, h('span', { class: 'aw-label', text: label }), input, props.help ? h('span', { class: 'setting-desc', text: props.help }) : null, said);
    };
    const tiles = (name, key, options, after) => {
      const box = h('div', { 'aria-label': name });
      AW.choices(box, `rk-${item.id}-${key}`, options, { value: c[key], onChange: (v) => { c[key] = v; if (after) after(); refresh(); } });
      return box;
    };
    const chips = (name, key, options, after) => {
      const box = h('div', { 'aria-label': name });
      AW.choices(box, `rk-${item.id}-${key}`, options, { compact: true, value: c[key], onChange: (v) => { c[key] = v; if (after) after(); refresh(); } });
      return box;
    };
    // A part shown only for one choice: in a plain wrapper, as a field's own display would show it through [hidden].
    const only = (key, values, node) => { const wrap = h('div', { 'data-rk-only': `${key}:${values.join(',')}` }, node); wrap.hidden = !values.includes(c[key]); return wrap; };
    const showOnly = () => body.querySelectorAll('[data-rk-only]').forEach((n) => { const [k, vs] = n.dataset.rkOnly.split(':'); n.hidden = !vs.split(',').includes(c[k]); });

    const sendWarn = h('p', { class: 'setting-desc warn-text', text: "The box sends this network's password to the new board." });
    const body = h('div', { class: 'rk-setup-body' },
      h('h3', { text: 'Route' }),
      tiles('Route', 'route', ROUTES.filter((r) => !r.why)),
      // The ways in the box cannot use here, each with why, folded so the one it can use stands alone.
      h('details', { class: 'sec-fold' }, h('summary', { text: 'Other ways in' }), tiles('Other ways in', 'route', ROUTES.filter((r) => r.why))),
      h('h3', { text: 'Network' }),
      tiles('Network', 'network', [
        { value: 'same', title: "This box's network, its password sent", desc: d.ssid ? `${d.ssid}: the box sends the password it holds for it.` : "This box's WiFi network.", why: d.ssid ? '' : 'this box is not on a WiFi network' },
        { value: 'same-typed', title: "This box's network, the password typed", desc: d.ssid ? `${d.ssid}: you type its password; the box does not read its own.` : "This box's WiFi network.", why: d.ssid ? '' : 'this box is not on a WiFi network' },
        { value: 'other', title: 'Another network', desc: 'A network you name: a workshop, or where the board will live.' },
        { value: 'hotspot', title: "This box's hotspot", desc: 'The board joins as a guest: it reaches this box and nothing else.' },
        { value: 'none', title: 'No WiFi', desc: 'For a board on a cable.' },
      ], showOnly),
      only('network', ['same'], sendWarn),
      only('network', ['same-typed'], field("The network's password", 'wifiPass', 'wifi-password', { type: 'password' })),
      only('network', ['other'], h('div', {}, field("The network's name", 'otherSsid', 'text'), field("The network's password", 'otherPass', 'wifi-password', { type: 'password', help: 'Empty for an open network.' }))),
      h('h3', { text: 'Accounts' }),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: "Root's password" }),
        chips("Root's password", 'rootPass', [{ value: 'generated', title: 'Generated', says: 'Made here, letters and digits, and shown once when the board is set up. The box does not keep it.' }, { value: 'typed', title: 'Typed', says: 'You type it here.' }], showOnly)),
      only('rootPass', ['typed'], field("Root's password", 'rootTyped', 'password', { type: 'password' })),
      field("The user's name", 'userName', 'user', { help: "mPWRD-OS's own is armbian." }),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: "The user's password" }),
        chips("The user's password", 'userPass', [{ value: 'generated', title: 'Generated', says: 'Made here, and shown once.' }, { value: 'typed', title: 'Typed', says: 'You type it here.' }], showOnly)),
      only('userPass', ['typed'], field("The user's password", 'userTyped', 'password', { type: 'password' })),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: "Root's password locked after setup" }),
        lockChips(), h('span', { class: 'setting-desc', text: 'Root then signs in with a key only: this box\'s, and yours below.' })),
      field('Your SSH key', 'key', 'text', { rows: 2, placeholder: 'ssh-ed25519 AAAA… you@laptop', help: "Added for root and the user, beside this box's own key, which the box puts on the board itself." }),
      h('h3', { text: 'Name and place' }),
      field('Name on the network', 'hostname', 'hostname', { help: `Every new Lyra starts as ${item.host || 'luckfox-lyra-zero-w'}: give each its own.` }),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Locale, time zone and WiFi country' }),
        chips('Locale, time zone and WiFi country', 'place', [
          { value: 'box', title: 'As this box', says: `${d.locale || 'en_GB.UTF-8'}, ${d.timezone || 'Etc/UTC'}, ${d.country || 'GB'}.` },
          { value: 'chosen', title: 'Chosen', says: 'Set below.' },
        ], showOnly)),
      only('place', ['chosen'], h('div', {}, field('Locale', 'locale', 'text', { placeholder: 'en_GB.UTF-8' }), field('Time zone', 'timezone', 'text', { placeholder: 'Europe/London' }), field('WiFi country', 'country', 'text', { placeholder: 'GB' }))),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Address' }),
        chips('Address', 'address', [{ value: 'dhcp', title: 'From the network', says: "The network's router gives it one (DHCP)." }, { value: 'fixed', title: 'Fixed', says: 'Set below.' }], showOnly)),
      only('address', ['fixed'], h('div', {}, field('Address', 'ip', 'text', { placeholder: '192.168.1.50' }), field('Mask', 'mask', 'text'), field('Gateway', 'gateway', 'text', { placeholder: '192.168.1.1' }), field('DNS', 'dns', 'text', { placeholder: '192.168.1.1' }))),
      h('h3', { text: 'After setup' }),
      tiles('After setup', 'after', [
        { value: 'install', title: 'Install irate-box', desc: "With this box's install options. The new box then waits to be claimed: claim it at once.", why: 'not built yet' },
        { value: 'leave', title: 'Leave it as it is', desc: 'An mPWRD-OS board on the network, for Meshtastic.' },
        { value: 'next', title: 'Set up the next board', desc: 'Back to Boards to set up when this one is done.' },
      ]),
      h('p', { class: 'setting-desc', text: "The box always stops the board's setup page afterwards: left running, it answers anyone on the board's networks until the board restarts." }),
      h('h3', { text: 'Steps' }),
      h('ol', { class: 'setup-steps rk-steps' }, STEPS.map(([id, text]) => h('li', { class: 'step-todo', 'data-step': id, text }))),
      h('div', { class: 'library-buttons' }, action),
      reason);
    function lockChips() {
      let node = AW.onOffChips("Root's password locked after setup", c.lockRoot, false, (on) => { c.lockRoot = on; const next = lockChips(); node.replaceWith(next); node = next; });
      return node;
    }
    action.addEventListener('click', async (event) => {
      if (!(await AW.confirmHere(`Set up ${item.title}? The box sends it the choices above.`, { anchor: event.currentTarget, label: 'Set up the board' }))) return;
      // Generated passwords are made by the root helper, not here, and shown once in its answer.
      try {
        waiting = (await postJSON('/admin/rkboard', { action: 'setup', hotspot: item.id, choices: { ...c } })).id;
        say('Setting the board up…', true, reason);
      } catch (err) {
        say(err.message, false, reason);
      }
    });
    showOnly();
    refresh();
    return [body];
  }

  function renderSetup(setup) {
    const at = document.getElementById('rk-setup');
    if (!at || !setup) return;
    const spots = setup.hotspots || [];
    const key = JSON.stringify([spots.map((s) => [s.ssid, s.boards]), setup.ready, setup.defaults]);
    // A drawer being filled in is not drawn again under the person typing.
    if (key === setupShown || at.querySelector('.aw-card.open')) return;
    setupShown = key;
    if (!spots.length) {
      at.replaceChildren(h('p', { class: 'setting-desc', text: 'No board to set up is in range. A board with mPWRD-OS raises its setup hotspot within a few minutes of its first start; it shows here then.' }));
      return;
    }
    at.replaceChildren(AW.cards(spots.map((s) => ({
      id: s.ssid,
      title: s.host || s.ssid,
      host: s.host,
      summary: s.boards > 1 ? `${s.boards} boards share this hotspot's name: power one at a time.` : 'Setup hotspot found, not set up.',
      badges: ['mPWRD-OS'],
    })), { id: 'rk-setup-cards', body: (item) => setupBody(item, setup) }));
  }

  load();
  // For the page tests: the drawing, called with data of their own.
  if (typeof window !== 'undefined') { window.rkRender = render; window.rkRenderSetup = renderSetup; window.rkProblem = problem; }
}());
