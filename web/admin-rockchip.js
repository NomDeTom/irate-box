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
  const el = AW.el;
  async function getJSON(url) { const response = await fetch(url); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
  async function postJSON(url, body) {
    const response = await fetch(url, { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((data.message && AW.say(data.message)) || data.error || `HTTP ${response.status}`);
    return data;
  }
  // A result of the root helper in the reader's language: its message (msg.py), else its English.
  const resultSaid = (result) => (result.msg ? AW.say(result.msg) : result.message);
  // The root helper answers a step by writing a result; wait for that one, not the next poll's.
  async function awaitResult(id) {
    for (let i = 0; i < 60; i++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const data = await getJSON('/admin/rkboard');
      const done = (data.results || []).find((result) => result.id === id);
      if (done) return done;
    }
    return null;
  }
  // Read first, show what the flash holds, then erase with the token the read named. A blank flash goes at once.
  async function readThenErase(body, at, anchor) {
    say('Reading the onboard flash…', true, at);
    try {
      const id = (await postJSON('/admin/rkboard', { action: 'read', loader: body.loader })).id;
      const read = await awaitResult(id);
      if (!read) return say('The flash was not read in time.', false, at);
      if (!read.ok) return say(resultSaid(read), false, at);
      const token = (read.msg && read.msg.token) || '';
      if (token !== 'blank') {
        const ask = `${resultSaid(read)} Erase it anyway? Everything on it is lost.`;
        if (!(await AW.confirmHere(ask, { anchor, label: 'Erase the flash' }))) return say('Nothing was erased.', true, at);
      }
      say("Erasing the board's flash…", true, at);
      waiting = (await postJSON('/admin/rkboard', { ...body, confirm: token })).id;
      shown = '';
      load();
    } catch (err) {
      say(err.message, false, at);
    }
  }
  // The same guard for installing a bootloader: read, say what is there, then ask with what will be written.
  async function readThenInstall(body, at, anchor, title, what) {
    say('Reading the onboard flash…', true, at);
    try {
      const id = (await postJSON('/admin/rkboard', { action: 'read', loader: body.loader })).id;
      const read = await awaitResult(id);
      if (!read) return say('The flash was not read in time.', false, at);
      if (!read.ok) return say(resultSaid(read), false, at);
      const token = (read.msg && read.msg.token) || '';
      const ask = `${resultSaid(read)} ${what} This erases the board's onboard flash, including any logs kept on the NAND. It cannot be undone.`;
      if (!(await AW.confirmHere(ask, { anchor, label: title }))) return say('Nothing was written.', true, at);
      say('Writing the bootloader to the board…', true, at);
      waiting = (await postJSON('/admin/rkboard', { ...body, confirm: token })).id;
      shown = '';
      load();
    } catch (err) {
      say(err.message, false, at);
    }
  }
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
  // The loader showed the SD card when an erase was asked (rkboard.card-shown): writing an image to it is offered.
  let cardShown = false;
  let imageChosen = '';
  let shaTyped = '';
  // The bootloader to install, and the Alpine rescue image and card to add beside it.
  let bootloaderChosen = 'nand-uboot';
  let rescueChosen = '';
  let rescueCard = '';

  async function load() {
    clearTimeout(poll);
    let data = null;
    try {
      data = await getJSON('/admin/rkboard');
    } catch (_) {
      data = null;
    }
    if (data) { render(data); renderCards(data); renderImages(data); renderSetup(data.setup); renderFinish(data.setup); renderInstallLog(data.install_log); }
    poll = setTimeout(load, document.hidden ? 30000 : waiting ? 2000 : 3000);
  }

  function render(data) {
    renderFinish(data.setup);
    const note = document.getElementById('rk-note');
    if (waiting) {
      const done = (data.results || []).find((r) => r.id === waiting);
      if (done) {
        waiting = null;
        if (done.msg && /^(?:rkserial|rkssh|rkspot|rkpresets|rkfinish)\./.test(done.msg.id || '')) setupResult(done);
        else if (waitingCard) { waitingCard = false; say(resultSaid(done), done.ok, document.getElementById('rk-card-result')); cardsShown = ''; }
        else say(resultSaid(done), done.ok, note);
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
    const lastRk = (data.results || []).find((r) => r.msg && (r.msg.id || '').startsWith('rkboard.'));
    if (lastRk) cardShown = lastRk.msg.id === 'rkboard.card-shown';
    if (!boards.length) cardShown = false;
    const images = data.images || [];
    const key = JSON.stringify([boards, busy, missing, chipChosen, cardShown, images, data.bootloaders, data.rescues, data.cards, bootloaderChosen, rescueChosen, rescueCard]);
    if (key === shown) return;
    shown = key;
    const board = document.getElementById('rk-board');
    const chip = document.getElementById('rk-chip');
    const actions = document.getElementById('rk-actions');
    chip.hidden = true;
    actions.replaceChildren();
    document.querySelectorAll('#rk-write, #rk-install').forEach((n) => n.remove());
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
          if (body.action === 'erase') return readThenErase(body, note, event.currentTarget);
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
    if (cardShown && (b.mode === 'loader' || b.mode === 'maskrom')) actions.after(writeBlock(images, busy, b, note));
    if (b.mode === 'loader' || b.mode === 'maskrom') actions.after(installBlock(data, b, busy, note));
  }

  // Installing a bootloader on the onboard flash, and optionally the Alpine rescue on a card in a reader. The
  // bootloaders are the original and the NAND U-Boot; the rescue image is one of the alpine-rescue*.img files.
  function installBlock(data, b, busy, note) {
    const box = el('div', { id: 'rk-install', className: 'rk-write' },
      el('h3', { textContent: 'Install a bootloader' }),
      el('p', { className: 'setting-desc', textContent: "Write a bootloader to the board's onboard flash. The original is the one Luckfox ships; the irate-box NAND U-Boot boots from the SPI NAND. The Alpine rescue image can be written to a card in a reader in the same step." }),
      el('p', { className: 'setting-desc warn-text', textContent: "Installing a bootloader erases the board's onboard flash. If logs were being kept on the NAND, they are erased. There is no migration: the board is prepared again from scratch." }));
    const boots = data.bootloaders || [];
    if (!boots.length) {
      box.append(el('p', { className: 'setting-desc', textContent: 'No bootloader is described on this box.' }));
      return box;
    }
    if (!boots.some((x) => x.id === bootloaderChosen)) bootloaderChosen = boots[0].id;
    const bootTiles = el('div', { id: 'rk-bootloaders' });
    AW.choices(bootTiles, 'rk-bootloaders', boots.map((x) => ({
      value: x.id,
      title: x.title,
      desc: x.ready
        ? `${x.summary} Ready, ${(x.size / 2 ** 20).toFixed(1)} MB.`
        : `${x.summary} Not ready: ${[...(x.missing || []), ...(x.damaged || [])].join(', ') || 'stage its files'}.`,
    })), { value: bootloaderChosen, onChange: (v) => { bootloaderChosen = v; shown = ''; load(); } });
    box.append(el('span', { className: 'setting-name', textContent: 'Bootloader' }), bootTiles);

    const rescues = data.rescues || [];
    const rescueChoices = [{ value: '', title: 'Do not add it', desc: 'Install the bootloader only.' }].concat(
      rescues.map((x) => ({ value: x.name, title: x.name, desc: `${(x.size / 2 ** 20).toFixed(0)} MB${x.sha256 ? ', its sha256 beside it' : ', no sha256 beside it'}` })));
    if (!rescueChoices.some((x) => x.value === rescueChosen)) rescueChosen = '';
    const rescueTiles = el('div', { id: 'rk-rescues' });
    AW.choices(rescueTiles, 'rk-rescues', rescueChoices, { value: rescueChosen, onChange: (v) => { rescueChosen = v; shown = ''; load(); } });
    box.append(el('span', { className: 'setting-name', textContent: 'Alpine rescue' }), rescueTiles);
    if (!rescues.length) {
      box.append(el('p', { className: 'setting-desc', textContent: 'No Alpine rescue image is on the box. Put an alpine-rescue .img (or .img.xz) in /var/lib/hub/rk-images, with its sha256 in a .sha256 file beside it.' }));
    }

    const cards = (data.cards || []).filter((c) => !c.mounted);
    if (rescueChosen && !cards.some((c) => c.name === rescueCard)) rescueCard = cards.length ? cards[0].name : '';
    let cardTiles = null;
    if (rescueChosen) {
      if (!cards.length) {
        box.append(el('p', { className: 'setting-desc', textContent: 'Put the card for the rescue image in a USB reader on this box, and unmount it if a window opened for it.' }));
      } else {
        cardTiles = el('div', { id: 'rk-rescue-cards' });
        AW.choices(cardTiles, 'rk-rescue-cards', cards.map((c) => ({
          value: c.name,
          title: `${c.model || 'SD card'}, ${(c.size / 1e9).toFixed(1)} GB`,
          desc: `In a USB reader (${c.path}).`,
        })), { value: rescueCard, onChange: (v) => { rescueCard = v; shown = ''; load(); } });
        box.append(el('span', { className: 'setting-name', textContent: 'Rescue card' }), cardTiles);
      }
    }

    const chosen = boots.find((x) => x.id === bootloaderChosen) || boots[0];
    const rescue = rescues.find((x) => x.name === rescueChosen);
    const card = cards.find((c) => c.name === rescueCard);
    const go = el('button', { type: 'button', className: 'action-btn', textContent: rescue ? 'Install the bootloader and the rescue' : 'Install the bootloader' });
    go.disabled = busy || !(data.tools || {}).rkdeveloptool || !chosen.ready || (!!rescue && (!card || !rescue.sha256));
    go.addEventListener('click', (event) => {
      const body = { action: 'install', bootloader: chosen.id, loader: b.mode === 'maskrom' ? chipChosen : null };
      if (rescue && card) {
        body.rescue = { image: rescue.name, sha256: rescue.sha256, card: card.name, size: card.size, serial: card.serial };
      }
      readThenInstall(body, note, event.currentTarget, go.textContent,
        `Install ${chosen.title}${rescue ? ` and write ${rescue.name} to the card` : ''}?`);
    });
    box.append(el('div', { className: 'library-buttons' }, go));
    return box;
  }

  // Writing an image to the SD card the board's loader shows: one of the images on the box (rkusb.IMAGES), its
  // publisher's sha256, and the root helper writes it through the loader, reading each piece back.
  function writeBlock(images, busy, b, note) {
    document.querySelectorAll('#rk-write').forEach((n) => n.remove());
    const box = el('div', { id: 'rk-write', className: 'rk-write' },
      el('h3', { textContent: "Write an image to the board's card" }),
      el('p', { className: 'setting-desc', textContent: "The board's loader shows its SD card, so the card can be written from here: the image replaces everything on the card, and each piece is read back as it is written." }));
    if (!images.length) {
      box.append(el('p', { className: 'setting-desc', textContent: 'No image is on the box yet. Put one in /var/lib/hub/rk-images: an .img or .img.xz, with its sha256 in a .sha256 file beside it.' }));
      return box;
    }
    if (!images.some((i) => i.name === imageChosen)) imageChosen = images[0].name;
    const tiles = el('div', { id: 'rk-images' });
    AW.choices(tiles, 'rk-images', images.map((i) => ({ value: i.name, title: i.name, desc: `${(i.size / 2 ** 20).toFixed(0)} MB${i.sha256 ? ', its sha256 beside it' : ''}` })), {
      value: imageChosen,
      onChange: (v) => { imageChosen = v; shaTyped = ''; shown = ''; },
    });
    const chosen = images.find((i) => i.name === imageChosen);
    const sha = el('input', { type: 'text', name: 'rk-sha256', autocomplete: 'off', spellcheck: false, placeholder: '64 hex digits, as its publisher lists it' });
    sha.value = shaTyped || chosen.sha256;
    const go = el('button', { type: 'button', className: 'action-btn', textContent: 'Write the image to the card' });
    const ok = () => /^[0-9a-fA-F]{64}$/.test(sha.value.trim());
    go.disabled = busy || !ok();
    sha.addEventListener('input', () => { shaTyped = sha.value; go.disabled = busy || !ok(); });
    go.addEventListener('click', async (event) => {
      if (!(await AW.confirmHere(`Write ${imageChosen} to the board's SD card? Everything on the card is replaced.`, { anchor: event.currentTarget, label: 'Write the image' }))) return;
      try {
        waiting = (await postJSON('/admin/rkboard', { action: 'write', image: imageChosen, sha256: sha.value.trim(), loader: b.mode === 'maskrom' ? chipChosen : null })).id;
        say("Writing the image to the board's card…", true, note);
        shown = '';
        load();
      } catch (err) {
        say(err.message, false, note);
      }
    });
    box.append(tiles, el('label', { className: 'aw-field' }, el('span', { className: 'aw-label', textContent: 'sha256' }), sha), el('div', { className: 'library-buttons' }, go));
    return box;
  }

  // ---- Cards in a reader (hub/rkusb.cards, root/rkcard.py): one card each, its drawer the three actions. Each is
  // sent with the card's name, size and serial, which the root helper checks again (a card swapped is refused).
  let cardsShown = '';
  let waitingCard = false;
  const cardImage = new Map();   // card name -> {image, sha}
  function renderCards(data) {
    const at = document.getElementById('rk-cards');
    if (!at) return;
    const list = data.cards || [];
    const images = data.images || [];
    const busy = !!waiting || data.pending > 0;
    const key = JSON.stringify([list, images, busy]);
    if (key === cardsShown || at.querySelector('.aw-card.open')) return;
    cardsShown = key;
    if (!list.length) {
      at.replaceChildren(h('p', { class: 'setting-desc', text: 'No card is in a reader on this box.' }));
      return;
    }
    at.replaceChildren(AW.cards(list.map((card) => ({
      id: card.name,
      title: `${card.model || 'SD card'}, ${(card.size / 1e9).toFixed(1)} GB`,
      summary: card.mounted ? 'Mounted: unmount it first (close the window the desktop opened for it).' : `In a USB reader (${card.path}).`,
      badges: card.mounted ? ['Mounted'] : [],
      card,
    })), { id: 'rk-cards-list', body: (item) => cardBody(item.card, images, busy) }));
  }
  // Board images: the shared parts in the approved order, then the images the box holds.
  function renderImages(data) {
    const at = document.getElementById('rk-images-panel');
    if (!at) return;
    const cfg = (data.rk_images && data.rk_images.settings) || {};
    const st = (data.rk_images && data.rk_images.status) || {};
    const images = data.images || [];
    const checked = st.last_check ? String(st.last_check).replace('T', ' ').replace('Z', ' UTC') : '';
    const note = el('p', { className: 'setting-desc action-note', role: 'status', hidden: true });
    const run = (action, event) => {
      const button = event.currentTarget;
      button.disabled = true;
      postJSON('/admin/library', { action, names: ['rk-images'] })
        .then(() => {
          say(action === 'check' ? 'Checked. The outcome shows here when the librarian is done.' : 'Downloading the newer images.', true, note);
          setTimeout(load, 1200);
        })
        .catch((err) => say(err.message, false, note))
        .finally(() => { button.disabled = false; });
    };
    const pill = AW.updatePill(checked, false, !!st.error);
    const card = el('div', { className: 'net-card setting' },
      el('h3', {}, 'Board images ', pill),
      el('p', { className: 'setting-desc' },
        'Access: Administrators. This page and the image files are behind the administrator sign-in.'),
      el('p', { className: 'setting-desc' },
        `Schedule: every ${cfg.check_every_hours || 24} hours, set in rk-images.json; the librarian syncs it. There is no schedule control on this page.`),
      el('p', { className: 'setting-desc' },
        'Space: no budget of its own; the images are files on the box.'),
      el('p', { className: 'library-buttons update-buttons' },
        el('button', { type: 'button', className: 'action-btn', textContent: 'Check now', onclick: (event) => run('check', event) }),
        el('button', { type: 'button', className: 'action-btn', textContent: 'Update', onclick: (event) => run('fetch', event) })),
      el('p', { className: `setting-desc${st.error ? ' bad' : ''}` },
        ...(checked
          ? [
              st.error
                ? `Last checked ${checked}: the check failed. ${st.error} `
                : `Last checked ${checked}: ${st.outcome || 'nothing newer'}. `,
              el('a', { href: '/admin/#updoctor', textContent: 'Open the Updates doctor.' }),
            ]
          : ['Not checked yet.']),
      ),
      note,
      el('div', { className: 'admin-list' },
        ...(images.length
          ? images.map((image) => el('p', { className: 'setting-desc' },
              el('strong', { textContent: image.name }),
              ` ${(image.size / 2 ** 20).toFixed(0)} MB${image.sha256 ? ', its publisher SHA-256 beside it' : ''}.`))
          : [el('p', { className: 'setting-desc', textContent: 'No image is on the box yet.' })])),
    );
    at.replaceChildren(card);
  }

  function cardBody(card, images, busy) {
    const result = document.getElementById('rk-card-result');
    const ident = { card: card.name, size: card.size, serial: card.serial };
    const send = async (body, said) => {
      try {
        waiting = (await postJSON('/admin/rkboard', { ...ident, ...body })).id;
        waitingCard = true;
        say(said, true, result);
        load();
      } catch (err) {
        say(err.message, false, result);
      }
    };
    const off = busy || card.mounted;
    const checkB = h('button', { type: 'button', class: 'action-btn', text: 'Check the card', disabled: off });
    checkB.addEventListener('click', () => send({ action: 'card-check' }, 'Reading the card…'));
    const genuineB = h('button', { type: 'button', class: 'action-btn', text: 'Test that it is genuine', disabled: off });
    genuineB.addEventListener('click', async (event) => {
      if (!(await AW.confirmHere("Test the card? The test writes all over it: everything on the card is lost. It takes a few minutes.", { anchor: event.currentTarget, label: 'Test the card' }))) return;
      send({ action: 'card-genuine' }, 'Testing the card…');
    });
    const parts = [
      h('p', { class: 'setting-desc', text: 'Check the card reads it and writes nothing: its partition table, and each filesystem on it.' }),
      h('div', { class: 'library-buttons' }, checkB, genuineB),
      h('h3', { text: 'Write an image to the card' }),
    ];
    if (!images.length) {
      parts.push(h('p', { class: 'setting-desc', text: 'No image is on the box yet. Put one in /var/lib/hub/rk-images: an .img or .img.xz, with its sha256 in a .sha256 file beside it.' }));
      return parts;
    }
    const pick = cardImage.get(card.name) || { image: images[0].name, sha: '' };
    if (!images.some((i) => i.name === pick.image)) pick.image = images[0].name;
    cardImage.set(card.name, pick);
    const tiles = h('div', { 'aria-label': 'Image' });
    const sha = h('input', { type: 'text', name: `rk-card-sha-${card.name}`, autocomplete: 'off', spellcheck: 'false', placeholder: '64 hex digits, as its publisher lists it' });
    const fill = () => { sha.value = pick.sha || (images.find((i) => i.name === pick.image) || {}).sha256 || ''; };
    AW.choices(tiles, `rk-card-image-${card.name}`, images.map((i) => ({ value: i.name, title: i.name, desc: `${(i.size / 2 ** 20).toFixed(0)} MB` })), {
      value: pick.image,
      onChange: (v) => { pick.image = v; pick.sha = ''; fill(); ok(); },
    });
    fill();
    const flashB = h('button', { type: 'button', class: 'action-btn', text: 'Write the image to the card' });
    const ok = () => { flashB.disabled = off || !/^[0-9a-fA-F]{64}$/.test(sha.value.trim()); };
    sha.addEventListener('input', () => { pick.sha = sha.value; ok(); });
    ok();
    flashB.addEventListener('click', async (event) => {
      if (!(await AW.confirmHere(`Write ${pick.image} to the card? Everything on the card is replaced.`, { anchor: event.currentTarget, label: 'Write the image' }))) return;
      send({ action: 'card-flash', image: pick.image, sha256: sha.value.trim() }, 'Writing the image to the card…');
    });
    parts.push(tiles, h('label', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'sha256' }), sha), h('div', { class: 'library-buttons' }, flashB));
    return parts;
  }

  // ---- New boards (hub/rksetup.py): one card per setup hotspot in range, and one for a board not found by itself;
  // its drawer the choices sent as Armbian's first-boot presets, and the steps. The choices are kept per board
  // while the page is open. Board-neutral: the board family selects the console speed and the naming.
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
  const ROUTES_ALL = [
    { value: 'hotspot', title: "The board's setup hotspot", desc: "The box joins the board's hotspot for about a minute, sends the choices, and comes back to its own network. On a box with one radio, this box's internet drops for that minute." },
    { value: 'card', title: 'Settings on the SD card', desc: "The choices written to an Armbian card already in a reader: no hotspot, and Armbian's default password never in use." },
    { value: 'ssh', title: "Armbian's default password, over a cable", desc: 'For stock Armbian on the box\'s own hotspot or a cable: the box signs in as root with Armbian\'s default password at once, and sets the board up.' },
    { value: 'serial', title: 'The serial console', desc: "For a board wired to a USB serial adapter on this box: press Set up, then power the board, and the box answers its first-login questions as it boots." },
    { value: 'usb', title: "Networking over the board's USB", desc: 'The cable used to erase the board, as a network.', why: 'mPWRD-OS has not been seen to offer it' },
    { value: 'bluetooth', title: 'Bluetooth', desc: "Meshtastic's flasher sends the WiFi over Bluetooth.", why: 'only Raspberry Pi images take it' },
  ];
  // The steps, by route: what the box will do, in order.
  const AGAIN = ['again', 'Find the board on the network'];
  const FINISH = ['finish', 'Finish over SSH: the keys, the name, the setup page stopped'];
  const STEPS = {
    hotspot: [['find', 'Find the board'], ['join', "Join the board's setup hotspot"], ['check', "Check that it is Armbian's setup"], ['send', 'Send the choices'], ['leave', "Back to this box's network"], AGAIN, FINISH],
    serial: [['console', "Find the board's console"], ['send', 'Send the choices over the console'], ['check', 'Read them back, to check they arrived whole'], ['start', 'Start its first login'], AGAIN, FINISH],
    card: [['write', 'Write the image and the choices to the card'], ['start', 'Put the card in the board and start it'], AGAIN, FINISH],
    ssh: [['signin', "Sign in at its address as root, with Armbian's default password"], ['send', 'Send the choices'], ['start', 'Start its first login'], AGAIN, FINISH],
    usb: [['link', "Join the board's USB network"], ['send', 'Send the choices'], ['start', 'Start its first login'], AGAIN, FINISH],
    bluetooth: [['send', 'Send the WiFi over Bluetooth'], AGAIN, FINISH],
  };
  // A board the box cannot find by itself: set up by a way in that needs no discovery (its console, its card, its
  // address), always offered after the boards found.
  const OTHER = 'other';
  const picked = new Map();   // hotspot name -> the choices made for that board
  let setupShown = '';
  // A board's name: two made-up words (names.js), not one the box can already see in a lease or a setup hotspot.
  function pickName(setup, avoid) {
    const taken = new Set(((setup && setup.taken) || []).map((x) => String(x).toLowerCase()));
    if (avoid) taken.add(String(avoid).toLowerCase());
    for (let i = 0; i < 1000; i++) {
      const name = window.HubNames ? window.HubNames.username() : 'salty-parrot';
      if (!taken.has(name)) return name;
    }
    return 'salty-parrot';
  }
  function choicesFor(id, defaults, setup) {
    if (!picked.has(id)) {
      const c = {
        route: id === OTHER ? 'serial' : 'hotspot', boardAddress: '', network: defaults.ssid ? 'same' : 'other', wifiPass: '', otherSsid: '', otherPass: '',
        rootPass: 'generated', rootTyped: '', userName: 'armbian', userPass: 'generated', userTyped: '', lockRoot: false, key: '',
        hostname: '', place: 'box', locale: defaults.locale || 'en_GB.UTF-8', timezone: defaults.timezone || 'Etc/UTC',
        country: defaults.country || 'GB', address: 'dhcp', ip: '', mask: '255.255.255.0', gateway: '', dns: '',
        board: 'auto', card: '', after: 'leave',
      };
      c.hostname = pickName(setup, c.userName);
      picked.set(id, c);
    }
    return picked.get(id);
  }
  // Every typed value the board would be sent, with its problem: [[label, problem]].
  // The serial consoles this box can offer: its USB adapters, and its own header pins when they are on.
  function consoleChoices(setup) {
    const own = setup.own_serial || {};
    const list = (setup.consoles || []).map((name) => ({ value: name, title: name.replace(/^usb-/, '').replace(/-if\d+-port\d+$/, ''), says: `/dev/serial/by-id/${name}` }));
    if (own.on && !own.pending) list.push({ value: 'own', title: "This box's own pins", says: own.device || 'header pins 8 and 10' });
    return list;
  }

  function problems(c, setup) {
    const ready = (setup.routes || []).includes(c.route);
    const out = [];
    const add = (label, value, kind) => { const found = problem(value, kind); if (found) out.push([label, found]); };
    if (c.route === 'ssh') {
      if (!c.boardAddress) out.push(["The board's address", 'Select a board the box found, or enter its address.']);
      add("The board's address", c.boardAddress, 'text');
    }
    if (c.route === 'card' && !c.card) out.push(['', 'Select a card in a reader on this box.']);
    if (c.route === 'card' && c.after === 'install') out.push(['After setup', 'The board has not started yet: install irate-box once it is on the network, over its own address.']);
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
    if (c.route === 'serial' && c.address === 'fixed') out.push(['Address', 'A fixed address is not set over the serial console yet: select From the network.']);
    if (c.route === 'serial' && !consoleChoices(setup).length) out.unshift(['', "Connect a USB serial adapter to this box, or turn on this box's own pins, wired to the board's debug console."]);
    else if (!ready) out.unshift(['', "Setting up is not built yet: the box's half (reaching the board and sending these choices) comes next. The choices are kept while this page is open."]);
    return out;
  }

  function setupBody(item, setup) {
    const c = choicesFor(item.id, setup.defaults || {}, setup);
    const boxValues = setup.defaults || {};
    const action = h('button', { type: 'button', class: 'action-btn primary', text: 'Set up the board' });
    const result = document.getElementById('rk-setup-result');
    const own = setup.own_serial || {};
    const ownGo = (on, label) => {
      const button = h('button', { type: 'button', class: 'action-btn', text: label });
      button.addEventListener('click', async (event) => {
        if (!(await AW.confirmHere(on ? `Use this box's own pins as a serial console? ${own.warning || ''}` : "Put this box's own pins back to how they were?", { anchor: event.currentTarget, label: on ? 'Use the pins' : 'Turn them off' }))) return;
        try {
          await postJSON('/admin/rkboard', { action: 'own-serial', on });
          say('Saved. Restart the box for its own pins to change.', true, result);
        } catch (err) {
          say(err.message, false, result);
        }
      });
      return button;
    };
    const reason = h('p', { class: 'setting-desc bad', role: 'status' });
    const refresh = () => {
      const list = problems(c, setup);
      action.disabled = list.length > 0;
      reason.textContent = list.length ? list.map(([label, found]) => (label ? `${label}: ${found}` : found)).join(' ') : '';
      reason.hidden = !list.length;
    };
    // A typed value: its field, and its problem said under it as it is typed.
    const field = (label, key, kind, props = {}) => {
      const said = h('span', { class: 'setting-desc bad', hidden: true });
      const input = h(props.rows ? 'textarea' : 'input', { type: props.rows ? null : props.type || 'text', name: `rk-${key}`, autocomplete: 'off', spellcheck: 'false', rows: props.rows || null, placeholder: props.placeholder || null });
      input.value = c[key] || '';
      input.addEventListener('input', () => {
        c[key] = input.value;
        const found = kind ? problem(input.value, kind) : '';
        said.textContent = found;
        said.hidden = !found;
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
    // A board found by its hotspot leads with that route; a board not listed, with the ways in that need no finding.
    const NO_FINDING = ['serial', 'card', 'ssh', 'usb'];
    const serials = consoleChoices(setup);
    if (!serials.some((x) => x.value === c.console)) c.console = (serials[0] || {}).value || '';
    const seenBoards = setup.boards || [];
    if (!c.boardAddress && seenBoards.length) { c.boardAddress = seenBoards[0].address; c.network = 'none'; }
    const cardsHere = setup.cards || [];
    if (!cardsHere.some((x) => x.name === c.card)) c.card = (cardsHere[0] || {}).name || '';
    // A route is offered when the box can do it now: its half is built, and what it needs (an adapter, a card, a
    // board seen) is here.
    const available = setup.routes || ['hotspot'];
    const NEEDS = {
      serial: "no USB serial adapter is connected and this box's own pins are off",
      card: 'no card is in a reader on this box',
      ssh: 'no board is seen on this box\'s own network',
      hotspot: 'no setup hotspot is in range',
    };
    const ROUTES = ROUTES_ALL.map((r) => {
      if (r.why) return r;
      if (available.includes(r.value) && !(r.value === 'serial' && !serials.length)) return r;
      return { ...r, why: NEEDS[r.value] || 'the box cannot do this yet' };
    });
    const routes = item.id === OTHER
      ? {
          main: ROUTES.filter((r) => NO_FINDING.includes(r.value)),
          folded: ROUTES.filter((r) => !NO_FINDING.includes(r.value)).map((r) => (r.value === 'hotspot' ? { ...r, why: 'a board that raises its setup hotspot shows as a card of its own' } : r)),
        }
      : { main: ROUTES.filter((r) => !r.why), folded: ROUTES.filter((r) => r.why) };
    const steps = h('ol', { class: 'setup-steps rk-steps' });
    function drawSteps() {
      steps.replaceChildren(...(STEPS[c.route] || STEPS.hotspot).map(([id, text]) => h('li', { class: 'step-todo', 'data-step': id, text })));
      showOnly();
    }
    const body = h('div', { class: 'rk-setup-body' },
      h('h3', { text: 'Route' }),
      tiles('Route', 'route', routes.main, drawSteps),
      // The other ways in, each with why it is not offered here, folded so the ones that fit this board stand alone.
      h('details', { class: 'sec-fold' }, h('summary', { text: 'Other ways in' }), tiles('Other ways in', 'route', routes.folded, drawSteps)),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: "This box's own pins" }),
        h('p', { class: 'setting-desc', text: own.supported
          ? own.on
            ? own.pending ? `On: restart the box to use ${own.device || 'them'}.` : `On: ${own.device || 'the header pins'}.`
            : 'Off. A USB serial adapter works without them.'
          : 'This board has no route irate-box knows from its header pins to a serial port.' }),
        own.supported && !own.on ? ownGo(true, "Use this box's own pins") : null,
        own.supported && own.on ? ownGo(false, "Turn this box's own pins off") : null,
        own.warning ? h('p', { class: 'setting-desc warn-text', text: own.warning }) : null),
      only('route', ['ssh'], h('div', {},
        (setup.boards || []).length ? h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Board found' }),
          chips('Board found', 'boardAddress', (setup.boards || []).map((b) => ({ value: b.address, title: b.host || b.address, says: `${b.address}${b.vendor ? `, ${b.vendor}` : ''}` })))) : null,
        field("The board's address", 'boardAddress', 'text', { placeholder: '192.168.1.60', help: "Its address on this box's own hotspot or cable, from the box's list or the board's screen." }))),
      only('route', ['card'], h('div', {},
        (setup.cards || []).length ? h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Card in a reader' }),
          chips('Card in a reader', 'card', (setup.cards || []).map((c) => ({ value: c.name, title: `${c.model || 'SD card'}, ${(c.size / 1e9).toFixed(1)} GB`, says: c.mounted ? 'Mounted: unmount it first.' : c.path })))) : null,
        h('p', { class: 'setting-desc', text: "Write an Armbian image to the card on this page first, with the chip's boot system in place. The box then writes these choices onto its root filesystem." }))),
      only('route', ['serial'], h('div', {},
        serials.length > 1 ? h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Serial console' }),
          chips('Serial console', 'console', serials)) : null,
        serials.length === 1 ? h('p', { class: 'setting-desc', text: `The serial console: ${serials[0].value === 'own' ? 'this box\'s own pins' : serials[0].value}.` }) : null,
        h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Board family' }),
          chips('Board family', 'board', [
            { value: 'auto', title: 'Find the speed', says: 'The box tries the known console speeds and keeps the one that hears the board.' },
            { value: 'rockchip', title: 'Rockchip', says: '1 500 000 baud: a Lyra and other Rockchip boards.' },
            { value: 'allwinner', title: 'Allwinner', says: '115 200 baud: a Banana Pi and other Allwinner boards.' },
            { value: 'raspberrypi', title: 'Raspberry Pi', says: '115 200 baud.' },
          ])),
        h('p', { class: 'setting-desc', text: "Wired to the board's debug console: the adapter's TX to the board's RX, its RX to the board's TX, GND to GND, at 3.3 V." }))),
      h('h3', { text: 'Network' }),
      tiles('Network', 'network', [
        { value: 'same', title: "This box's network, its password sent", desc: boxValues.ssid ? `${boxValues.ssid}: the box sends the password it holds for it.` : "This box's WiFi network.", why: boxValues.ssid ? '' : 'this box is not on a WiFi network' },
        { value: 'same-typed', title: "This box's network, the password typed", desc: boxValues.ssid ? `${boxValues.ssid}: you type its password; the box does not read its own.` : "This box's WiFi network.", why: boxValues.ssid ? '' : 'this box is not on a WiFi network' },
        { value: 'other', title: 'Another network', desc: 'A network you name: a workshop, or where the board will live.' },
        { value: 'hotspot', title: "This box's hotspot", desc: 'The board joins as a guest: it reaches this box and nothing else.' },
        { value: 'none', title: 'No WiFi', desc: 'For a board on a cable.' },
      ], showOnly),
      only('network', ['same'], sendWarn),
      only('route', ['hotspot'], only('network', ['same', 'same-typed', 'other'], h('p', {
        class: 'setting-desc warn-text',
        text: "Anyone nearby can raise a setup hotspot with the same name and Armbian's public password. The box checks the page before it sends anything, but that does not prove the hotspot is your board. To send no WiFi password, choose This box's hotspot, then move the board later.",
      }))),
      only('network', ['same-typed'], field("The network's password", 'wifiPass', 'wifi-password', { type: 'password' })),
      only('network', ['other'], h('div', {}, field("The network's name", 'otherSsid', 'text'), field("The network's password", 'otherPass', 'wifi-password', { type: 'password', help: 'Empty for an open network.' }))),
      h('h3', { text: 'Accounts' }),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: "Root's password" }),
        chips("Root's password", 'rootPass', [{ value: 'generated', title: 'Generated', says: 'Made here, letters and digits, and shown once when the board is set up. The box does not keep it.' }, { value: 'typed', title: 'Typed', says: 'You type it here.' }], showOnly)),
      only('rootPass', ['typed'], field("Root's password", 'rootTyped', 'password', { type: 'password' })),
      userName(),
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
          { value: 'box', title: 'As this box', says: `${boxValues.locale || 'en_GB.UTF-8'}, ${boxValues.timezone || 'Etc/UTC'}, ${boxValues.country || 'GB'}.` },
          { value: 'chosen', title: 'Chosen', says: 'Set below.' },
        ], showOnly)),
      only('place', ['chosen'], h('div', {}, field('Locale', 'locale', 'text', { placeholder: 'en_GB.UTF-8' }), field('Time zone', 'timezone', 'text', { placeholder: 'Europe/London' }), field('WiFi country', 'country', 'text', { placeholder: 'GB' }))),
      h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Address' }),
        chips('Address', 'address', [{ value: 'dhcp', title: 'From the network', says: "The network's router gives it one (DHCP)." }, { value: 'fixed', title: 'Fixed', says: 'Set below.' }], showOnly)),
      only('address', ['fixed'], h('div', {}, field('Address', 'ip', 'text', { placeholder: '192.168.1.50' }), field('Mask', 'mask', 'text'), field('Gateway', 'gateway', 'text', { placeholder: '192.168.1.1' }), field('DNS', 'dns', 'text', { placeholder: '192.168.1.1' }))),
      h('h3', { text: 'After setup' }),
      tiles('After setup', 'after', [
        { value: 'install', title: 'Install irate-box', desc: "With this box's install options, over SSH. The new box then waits to be claimed: claim it at once." },
        { value: 'leave', title: 'Leave it as it is', desc: 'An mPWRD-OS board on the network, for Meshtastic.' },
        { value: 'next', title: 'Set up the next board', desc: 'Back to Boards to set up when this one is done.' },
      ]),
      h('p', { class: 'setting-desc', text: "The box always stops the board's setup page afterwards: left running, it answers anyone on the board's networks until the board restarts." }),
      h('h3', { text: 'Steps' }),
      steps,
      h('div', { class: 'library-buttons' }, action),
      reason);
    // The user's name: typed, made up by the dice (names.js, as the shoutbox's handle), or the image's default.
    function userName() {
      const DEFAULT_USER = 'armbian';
      const wrap = field("The user's name", 'userName', 'user', { help: "Armbian's and mPWRD-OS's own is armbian." });
      const input = wrap.querySelector('input');
      const chip = h('button', { type: 'button', class: 'chip', text: 'Default' });
      const mark = () => { const on = input.value === DEFAULT_USER; chip.classList.toggle('selected', on); chip.setAttribute('aria-pressed', String(on)); };
      const put = (value) => { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); };
      chip.addEventListener('click', () => put(DEFAULT_USER));
      const dice = h('button', { type: 'button', class: 'action-btn rk-dice', title: 'Make up a name', 'aria-label': 'Make up a name', text: '🎲' });
      dice.addEventListener('click', () => put(window.HubNames ? window.HubNames.username() : DEFAULT_USER));
      input.addEventListener('input', mark);
      input.after(dice, chip);
      mark();
      return wrap;
    }
    function lockChips() {
      let node = AW.onOffChips("Root's password locked after setup", c.lockRoot, false, (on) => { c.lockRoot = on; const next = lockChips(); node.replaceWith(next); node = next; });
      return node;
    }
    action.addEventListener('click', async (event) => {
      if (!(await AW.confirmHere(`Set up ${item.title}? The box sends it the choices above.`, { anchor: event.currentTarget, label: 'Set up the board' }))) return;
      // Generated passwords are made by the root helper, not here, and shown once in its answer.
      try {
        const body = { action: 'setup', route: c.route, choices: { ...c } };
        if (c.route === 'serial') body.console = c.console;
        if (c.route === 'card') { const chosen = (setup.cards || []).find((x) => x.name === c.card) || {}; body.card = c.card; body.size = chosen.size; body.serial = chosen.serial; }
        if (c.route === 'ssh') body.address = c.boardAddress;
        if (c.route === 'hotspot') body.hotspot = item.id;
        waiting = (await postJSON('/admin/rkboard', body)).id;
        say(c.route === 'serial' ? 'Waiting for the board on its console: power it now…' : 'Setting the board up…', true, reason);
      } catch (err) {
        say(err.message, false, reason);
      }
    });
    drawSteps();
    refresh();
    return [body];
  }

  // A setup's answer, above the boards: what was done, and the passwords made for the board, given once by the hub
  // (rk-secrets) and not kept by the box.
  async function setupResult(done) {
    const at = document.getElementById('rk-setup-result');
    if (!at) return;
    at.hidden = false;
    at.replaceChildren(h('p', { class: done.ok ? 'setting-desc' : 'setting-desc bad', text: resultSaid(done) }));
    if (!done.ok || !done.msg || !['rkserial.done', 'rkssh.done', 'rkspot.done', 'rkpresets.done'].includes(done.msg.id)) return;
    let secret = null;
    try {
      secret = (await postJSON('/admin/rkboard', { action: 'secret', id: done.id })).secret;
    } catch (_) {
      secret = null;
    }
    if (!secret) return;
    at.append(h('p', { class: 'setting-desc warn-text', text: 'Shown once, and not kept by this box: write them down.' }),
      AW.dl({ "Root's password": secret.root, [`${secret.user}'s password`]: secret.user_password }));
  }

  function renderInstallLog(text) {
    const at = document.getElementById('rk-install-log');
    if (!at) return;
    at.hidden = !text;
    if (text && at.textContent !== text) at.textContent = text;
  }

  // A board the box already found, finished or given irate-box over SSH: for the card and hotspot routes, the
  // board joins the network after the choices are sent, so the finish is a step of its own.
  let finishShown = '';
  const finishNames = new Map();   // address -> the name picked for it, kept while the page is open
  function renderFinish(setup) {
    const at = document.getElementById('rk-finish');
    if (!at || !setup) return;
    const boards = setup.boards || [];
    const stateKey = JSON.stringify([boards.map((b) => [b.address, b.host]), setup.ready]);
    if (stateKey === finishShown || at.querySelector('input:focus, textarea:focus')) return;
    finishShown = stateKey;
    if (!boards.length) {
      at.replaceChildren(h('p', { class: 'setting-desc', text: "No board is on this box's own network to finish yet. Start the new board, and it shows here once it has joined." }));
      return;
    }
    const first = boards[0];
    const address = h('input', { type: 'text', name: 'rk-finish-address', autocomplete: 'off', spellcheck: false, placeholder: '192.168.1.60' });
    address.value = first.address;
    const name = h('input', { type: 'text', name: 'rk-finish-name', autocomplete: 'off', spellcheck: false, placeholder: 'salty-parrot' });
    if (!finishNames.has(first.address)) finishNames.set(first.address, pickName(setup));
    name.value = finishNames.get(first.address);
    name.addEventListener('input', () => finishNames.set(first.address, name.value));
    const key = h('textarea', { name: 'rk-finish-key', autocomplete: 'off', spellcheck: false, rows: 2, placeholder: 'ssh-ed25519 AAAA… you@laptop' });
    const result = document.getElementById('rk-setup-result');
    const send = async (body, said) => {
      try {
        waiting = (await postJSON('/admin/rkboard', body)).id;
        say(said, true, result);
      } catch (err) {
        say(err.message, false, result);
      }
    };
    const finishB = h('button', { type: 'button', class: 'action-btn', text: 'Finish over SSH' });
    finishB.addEventListener('click', async (event) => {
      if (!(await AW.confirmHere(`Finish the board at ${address.value}, and stop its setup page?`, { anchor: event.currentTarget, label: 'Finish the board' }))) return;
      send({ action: 'finish', address: address.value.trim(), choices: { hostname: name.value.trim(), userName: 'armbian', key: key.value.trim(), lockRoot: false } }, 'Finishing the board over SSH…', event.currentTarget);
    });
    const installB = h('button', { type: 'button', class: 'action-btn', text: 'Install irate-box' });
    installB.addEventListener('click', async (event) => {
      if (!(await AW.confirmHere(`Install irate-box on the board at ${address.value}? Its output is shown here.`, { anchor: event.currentTarget, label: 'Install irate-box' }))) return;
      send({ action: 'board-install', address: address.value.trim() }, 'Installing irate-box on the board…', event.currentTarget);
    });
    at.replaceChildren(h('div', { class: 'net-card setting' },
      h('h3', { text: 'Finish a board on the network' }),
      h('p', { class: 'setting-desc', text: "A board that has just joined, from its card or its setup hotspot, is finished here: its setup page stopped, the box's key and yours put on it, and its name set. Install irate-box after that." }),
      h('label', { class: 'aw-field' }, h('span', { class: 'aw-label', text: "The board's address" }), address),
      h('label', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Name on the network' }), name),
      h('label', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Your SSH key' }), key),
      h('div', { class: 'library-buttons' }, finishB, installB)));
  }

  function renderSetup(setup) {
    const at = document.getElementById('rk-setup');
    if (!at || !setup) return;
    const spots = setup.hotspots || [];
    const key = JSON.stringify([spots.map((s) => [s.ssid, s.boards]), setup.ready, setup.defaults, setup.consoles, setup.own_serial, setup.cards, setup.boards, setup.routes]);
    // A drawer being filled in is not drawn again under the person typing.
    if (key === setupShown || at.querySelector('.aw-card.open')) return;
    setupShown = key;
    const found = spots.map((spot) => ({
      id: spot.ssid,
      title: spot.host || spot.ssid,
      host: spot.host,
      summary: spot.boards > 1 ? `${spot.boards} boards share this hotspot's name: power one at a time.` : 'Setup hotspot found, not set up.',
      badges: ['mPWRD-OS'],
    }));
    const other = { id: OTHER, title: 'A board not listed here', summary: 'Set up over its serial console, its SD card or its address: ways in the box does not find by itself.', badges: [] };
    at.replaceChildren(
      spots.length ? '' : h('p', { class: 'setting-desc', text: 'No setup hotspot is in range. A board with mPWRD-OS raises one within a few minutes of its first start, and shows here then.' }),
      AW.cards([...found, other], { id: 'rk-setup-cards', body: (item) => setupBody(item, setup) }));
  }

  load();
  // For the page tests: the drawing, called with data of their own.
  if (typeof window !== 'undefined') { window.rkRender = render; window.rkRenderSetup = renderSetup; window.rkRenderCards = renderCards; window.rkProblem = problem; }
}());
