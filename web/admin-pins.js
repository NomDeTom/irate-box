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
  let lastOverlay = null;
  let lastAssignments = new Map();
  let selectedPin = null;
  let muiStatus = null;
  const gpioSay = (text, ok) => { const n = $('pins-gpio-note'); n.textContent = text; n.classList.toggle('bad', !ok); n.hidden = !text; };
  const say = (text, ok) => { const n = $('pins-note'); n.textContent = text; n.classList.toggle('bad', !ok); n.hidden = !text; };
  const ovSay = (text, ok) => { const n = $('pins-overlay-note'); n.textContent = text; n.classList.toggle('bad', !ok); n.hidden = !text; };
  const dispSay = (text, ok) => { const n = $('pins-display-note'); n.textContent = text; n.classList.toggle('bad', !ok); n.hidden = !text; };
  // The test card's fixed SPI1 overlay and MUI's own overlay hold the same pins: while MUI holds them, the test card
  // waits, and says why.
  const muiHolds = () => Boolean(muiStatus && (muiStatus.overlay_enabled || muiStatus.unit_active || muiStatus.pending));
  function overlayState(o) {
    if (!o) return 'Read the pins to see the test card.';
    if (!o.supported) return 'The test card is for the Luckfox Lyra Zero W only.';
    if (!o.on && muiHolds()) return 'MUI holds these pins. Deactivate MUI to use the test card.';
    if (o.on && (o.pending || !o.active)) return 'Overlay on, waiting for a restart.';
    if (o.on && muiStatus && muiStatus.unit_active) return 'Overlay on. MUI is running on the panel, so the test waits.';
    if (o.on) return o.display_on ? 'Overlay on. The test card is showing.' : 'Overlay on. The display is off.';
    if (o.enabled && !o.installed) return 'Enabled in the boot files, but the overlay file is missing.';
    return 'Overlay off.';
  }
  function renderOverlay(o) {
    lastOverlay = o || null;
    const state = $('pins-overlay-state');
    const button = $('pins-overlay-toggle');
    if (state) state.textContent = overlayState(lastOverlay);
    if (button) {
      button.disabled = !(lastOverlay && lastOverlay.supported) || (!lastOverlay.on && muiHolds());
      button.textContent = lastOverlay && lastOverlay.on ? 'Disable' : 'Enable';
    }
    const displayButton = $('pins-display-toggle');
    const testButton = $('pins-test');
    const ready = Boolean(
      lastOverlay && lastOverlay.supported && lastOverlay.on && lastOverlay.active && !(muiStatus && muiStatus.unit_active),
    );
    if (displayButton) {
      displayButton.disabled = !ready;
      displayButton.textContent = lastOverlay && lastOverlay.display_on ? 'Turn display off' : 'Turn display on';
    }
    if (testButton) testButton.disabled = !ready;
  }
  let lastMui = null;
  let muiOptions = null;
  let muiPopulated = false;
  const MUI_ROLE_SELECTS = {
    clk: 'pins-mui-clk',
    mosi: 'pins-mui-mosi',
    miso: 'pins-mui-miso',
    display_cs: 'pins-mui-display-cs',
    touch_cs: 'pins-mui-touch-cs',
    dc: 'pins-mui-dc',
    rst: 'pins-mui-rst',
    bl: 'pins-mui-bl',
  };
  const muiSayText = (text, ok) => { const n = $('pins-mui-note'); n.textContent = text; n.classList.toggle('bad', !ok); n.hidden = !text; };
  function muiHeaderRow(pin) {
    const rows = (lastPins && lastPins.header) || [];
    return rows.find((row) => row && row.pin === pin) || null;
  }
  function muiCandidateRows(role, bus) {
    if (!muiOptions) return [];
    if (role === 'dc' || role === 'rst' || role === 'bl') return muiOptions.gpios || [];
    const found = (muiOptions.buses || []).find((item) => Number(item.bus) === Number(bus));
    if (!found) return [];
    if (role === 'display_cs' || role === 'touch_cs') return [...(found.signals.csn0 || []), ...(found.signals.csn1 || [])];
    return found.signals[role] || [];
  }
  function muiFillSelect(id, rows, value) {
    const select = $(id);
    if (!select) return;
    const chosen = String(value === undefined || value === null ? '' : value);
    const options = rows.map((row) => {
      const option = document.createElement('option');
      const usable = row.usable === true;
      const reason = row.why || (usable ? 'free' : 'unavailable');
      option.value = String(row.pin);
      option.disabled = !usable;
      option.dataset.usable = String(usable);
      option.dataset.why = reason;
      if (row.cs !== undefined) option.dataset.cs = String(row.cs);
      // The pin by the name the map gives it; why only when it cannot be used. A pin MUI or the test card holds
      // now is one this choice takes over, not a reason.
      const header = muiHeaderRow(row.pin);
      const name = header ? pinLabel(header) : row.name || '';
      option.textContent = `Pin ${row.pin}${name ? ` — ${name}` : ''}${usable ? '' : ` (${reason})`}`;
      option.title = reason;
      return option;
    });
    if (!options.length) options.push(Object.assign(document.createElement('option'), { value: '', textContent: 'No valid pin' }));
    select.replaceChildren(...options);
    if (options.some((option) => option.value === chosen)) select.value = chosen;
  }
  function muiSelectionFrom(saved) {
    const sel = saved || {};
    return {
      bus: Number(sel.bus === undefined ? 1 : sel.bus),
      touch: sel.touch || 'lgfx',
      clk: sel.clk,
      mosi: sel.mosi,
      miso: sel.miso,
      display_cs: sel.display_cs,
      touch_cs: sel.touch_cs,
      dc: sel.dc,
      rst: sel.rst,
      bl: sel.bl,
      log_level: sel.log_level || 'warn',
      log_destination: sel.log_destination || 'ram',
      map_tiles: sel.map_tiles || 'dummy',
      map_tiles_folder: sel.map_tiles_folder || '',
      replace_radio: sel.replace_radio === true,
    };
  }
  function muiRebuild(initial) {
    if (!muiOptions) return;
    const bus = Number($('pins-mui-bus').value || 1);
    const touch = $('pins-mui-touch').value || 'lgfx';
    const saved = muiSelectionFrom(initial || (muiPopulated ? muiSelectionNow() : (muiStatus && muiStatus.selection) || muiOptions.selection || muiOptions.defaults));
    for (const role of Object.keys(MUI_ROLE_SELECTS)) {
      const rows = muiCandidateRows(role, bus);
      muiFillSelect(MUI_ROLE_SELECTS[role], rows, saved[role]);
    }
    if (touch !== 'none') {
      const display = $('pins-mui-display-cs');
      const touchCs = $('pins-mui-touch-cs');
      if (display.value && display.value === touchCs.value) {
        const other = [...touchCs.options].find((option) => option.value !== display.value && !option.disabled);
        if (other) touchCs.value = other.value;
      }
    }
    $('pins-mui-touch-cs').disabled = touch === 'none';
    $('pins-mui-replace-radio').checked = saved.replace_radio === true;
    $('pins-mui-log-level').value = saved.log_level || 'warn';
    $('pins-mui-log-destination').value = saved.log_destination || 'ram';
    $('pins-mui-map-tiles').value = saved.map_tiles || 'dummy';
    const folderSelect = $('pins-mui-map-folder');
    const folders = (muiOptions && muiOptions.map_folders) || [];
    const folderOptions = folders.map((name) => Object.assign(document.createElement('option'), { value: name, textContent: name }));
    if (!folderOptions.length) folderOptions.push(Object.assign(document.createElement('option'), { value: '', textContent: 'No tile folders' }));
    folderSelect.replaceChildren(...folderOptions);
    if (folders.includes(saved.map_tiles_folder)) folderSelect.value = saved.map_tiles_folder;
    muiUpdate();
  }
  function muiSelectionNow() {
    const sel = { bus: Number($('pins-mui-bus').value || 1), touch: $('pins-mui-touch').value || 'lgfx' };
    for (const role of Object.keys(MUI_ROLE_SELECTS)) sel[role] = Number($(MUI_ROLE_SELECTS[role]).value);
    sel.replace_radio = Boolean($('pins-mui-replace-radio').checked);
    sel.log_level = $('pins-mui-log-level').value || 'warn';
    sel.log_destination = $('pins-mui-log-destination').value || 'ram';
    sel.map_tiles = $('pins-mui-map-tiles').value || 'dummy';
    sel.map_tiles_folder = $('pins-mui-map-folder').value;
    return sel;
  }
  function muiDuplicates(sel) {
    const seen = new Map();
    const roles = sel.touch === 'none' ? Object.keys(MUI_ROLE_SELECTS).filter((role) => role !== 'touch_cs') : Object.keys(MUI_ROLE_SELECTS);
    for (const role of roles) {
      const pin = sel[role];
      if (!Number.isInteger(pin)) continue;
      if (seen.has(pin)) return [seen.get(pin), role];
      seen.set(pin, role);
    }
    return null;
  }
  function muiStateText() {
    if (!muiStatus) return 'Read the pins to see MUI.';
    if (!muiStatus.supported) return 'MUI is for the Luckfox Lyra Zero W only.';
    if (!muiStatus.binary_present) return 'The per-host meshtasticd binary is missing; MUI cannot be activated.';
    if (muiStatus.unit_active) {
      const radio = muiStatus.radio && muiStatus.radio.spidev ? `SimRadio on ${muiStatus.radio.spidev}` : 'SimRadio';
      const display = muiStatus.spidev && muiStatus.spidev.display ? muiStatus.spidev.display : 'the selected display';
      const touch = muiStatus.spidev && muiStatus.spidev.touch ? `, touch ${muiStatus.spidev.touch}` : '';
      return `Running: ${radio}, display ${display}${touch}.`;
    }
    if (muiStatus.pending) return 'Saved, waiting for a restart.';
    if (muiStatus.overlay_enabled) return 'Enabled, waiting for the SPI devices.';
    return 'Off.';
  }
  function muiUpdate() {
    if (!lastMui) return;
    const displaySelect = $('pins-mui-display-cs');
    const touchSelect = $('pins-mui-touch-cs');
    const displayCs = displaySelect && displaySelect.selectedOptions[0] ? displaySelect.selectedOptions[0].dataset.cs : undefined;
    if (touchSelect && $('pins-mui-touch').value !== 'none') {
      for (const option of touchSelect.options) {
        if (displayCs !== undefined && option.dataset.cs !== undefined && option.dataset.cs === displayCs) {
          option.disabled = true;
          option.title = 'same chip select as the display';
        } else {
          option.disabled = option.dataset.usable !== 'true';
          option.title = option.dataset.why || '';
        }
      }
      if (touchSelect.selectedOptions[0] && touchSelect.selectedOptions[0].disabled) {
        const same = [...touchSelect.options].find((option) => option.value === touchSelect.value && !option.disabled);
        const other = same || [...touchSelect.options].find((option) => !option.disabled);
        if (other) touchSelect.selectedIndex = [...touchSelect.options].indexOf(other);
      }
    }
    const sel = muiSelectionNow();
    const dup = muiDuplicates(sel);
    const logWarning = $('pins-mui-log-warning');
    if (logWarning) logWarning.hidden = !(sel.log_level === 'debug' || sel.log_destination === 'card');
    const folderBox = $('pins-mui-map-folder-box');
    if (folderBox) folderBox.hidden = sel.map_tiles !== 'folder';
    const touchOption = touchSelect && touchSelect.selectedOptions[0];
    const csConflict = Boolean(
      touchOption && displayCs !== undefined && touchOption.dataset.cs !== undefined && touchOption.dataset.cs === displayCs
    );
    const configured = Boolean(muiStatus && muiStatus.radio && muiStatus.radio.configured);
    const warning = $('pins-mui-radio-warning');
    if (warning) warning.hidden = !configured || sel.replace_radio;
    // Pin 36 is the MeshAdv-Pi HAT's IRQ on the Lyra's header: said beside the setting that takes it.
    const blWarning = $('pins-mui-bl-warning');
    if (blWarning) blWarning.hidden = !(muiOptions && muiOptions.supported && sel.bl === 36);
    const activate = $('pins-mui-activate');
    const deactivate = $('pins-mui-deactivate');
    const restart = $('pins-mui-restart');
    const supported = Boolean(muiOptions && muiStatus && muiStatus.supported);
    const binary = Boolean(muiStatus && muiStatus.binary_present);
    const folderMissing = sel.map_tiles === 'folder' && !sel.map_tiles_folder;
    if (activate) {
      activate.disabled = !supported || !binary || Boolean(dup) || csConflict || folderMissing || (configured && !sel.replace_radio);
      activate.textContent = muiHolds() ? 'Apply' : 'Activate';
    }
    if (deactivate) {
      deactivate.disabled = !muiStatus || !(muiStatus.overlay_enabled || muiStatus.unit_active || muiStatus.pending);
    }
    if (restart) restart.hidden = !(muiStatus && muiStatus.pending);
    const state = $('pins-mui-state');
    if (state) {
      state.textContent = dup
        ? 'Two roles use the same pin. Select different pins.'
        : csConflict
          ? 'Display and touch need different chip selects.'
          : folderMissing
            ? 'Select a tiles folder, or the dummy tile.'
            : muiStateText();
    }
  }
  function renderMui(mui) {
    lastMui = mui || null;
    muiStatus = (mui && mui.status) || null;
    muiOptions = (mui && mui.options) || null;
    if (!muiOptions) {
      const state = $('pins-mui-state');
      if (state) state.textContent = 'Read the pins to see MUI.';
      if ($('pins-mui-activate')) $('pins-mui-activate').disabled = true;
      if ($('pins-mui-deactivate')) $('pins-mui-deactivate').disabled = true;
      return;
    }
    if (!muiPopulated) {
      const saved = muiSelectionFrom((muiStatus && muiStatus.selection) || muiOptions.selection || muiOptions.defaults);
      $('pins-mui-bus').value = String(saved.bus === 0 ? 0 : 1);
      $('pins-mui-touch').value = saved.touch || 'lgfx';
      muiPopulated = true;
      muiRebuild(saved);
    } else {
      muiUpdate();
    }
  }
  async function waitMui(id) {
    for (let i = 0; i < 180; i++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      let data;
      try { data = await get(); } catch (_) { continue; }
      const done = (data.results || []).find((result) => result.id === id);
      if (!done) continue;
      if (data.mui) renderMui(data.mui);
      if (!done.ok) muiSayText(answerSaid(done) || 'MUI could not be changed.', false);
      return done;
    }
    muiSayText('The MUI change was not confirmed in time.', false);
    return null;
  }
  // The hardware document carries the overlay and display state without a Box doctor scan; /admin/pins carries
  // the last pin read and the helper's answers. On a box whose root helper has not published one yet, the pin
  // page's own copy of the overlay is used.
  function overlayFromHardware(hw) {
    const lcd = hw && hw.overlays && hw.overlays.items && hw.overlays.items.lcd;
    if (!lcd) return null;
    const display = hw.display || {};
    return { ...lcd, display_on: lcd.display_on === undefined ? Boolean(display.on) : lcd.display_on };
  }
  async function get() {
    const response = await fetch('/admin/pins');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    try {
      const hwResponse = await fetch('/admin/hardware');
      if (hwResponse.ok) {
        const hw = await hwResponse.json();
        data.overlay = overlayFromHardware(hw) || data.overlay;
        data.assignments = hw.assignments || data.assignments || [];
        if (!data.pins && hw.pins && hw.pins.header) data.pins = hw.pins;
      }
    } catch (_) { /* the pin page's own copy still stands */ }
    return data;
  }
  async function post(body) {
    const response = await fetch('/admin/pins', { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
    return data;
  }

  // Who wants a pin, by the feature's name on this page rather than the module's.
  const SOURCE_NAMES = { mui: 'MUI', lcdtouch: 'test card', ownserial: 'own serial', pinhog: 'pin hog' };
  // A process that holds lines through libgpiod, by the program a user knows.
  const CONSUMER_NAMES = { portduino: 'meshtasticd' };
  const sourceName = (item) => SOURCE_NAMES[item.source] || item.by || item.source || '';
  function assignmentFor(pin) {
    return pin && typeof pin.pin === 'number' ? lastAssignments.get(pin.pin) : null;
  }
  const isHeld = (assigned) => Boolean(assigned && (assigned.state === 'holding' || assigned.holding));
  // A pin's name: what the feature that holds it calls it (SPI1 CLK, Display D/C), the pin-mux function's name
  // (I2C2 SDA), then the pinout's own (GPIO0_A2). A bare consumer is not a name: it is the owner, below.
  function pinLabel(pin) {
    if (pin.type === 'gpio') {
      const assigned = assignmentFor(pin);
      if (assigned && assigned.state === 'active' && assigned.source !== 'pinhog' && assigned.role) return assigned.role;
      if (pin.label && pin.label !== pin.consumer) return pin.label;
    }
    return pin.name || (pin.gpio === null || pin.gpio === undefined ? 'power' : `GPIO${pin.gpio}`);
  }
  // What holds a pin, in words: the feature, the devices on its bus (spidev0.0), or the program; for a free pin,
  // the board's own role for it, when it has one.
  function pinOwner(pin) {
    if (pin.type !== 'gpio') return '';
    const assigned = assignmentFor(pin);
    if (isHeld(assigned) && assigned.source === 'pinhog') return 'pin hog';
    if (assigned && assigned.state === 'conflict') {
      return `${CONSUMER_NAMES[assigned.active_owner] || assigned.active_owner || 'another driver'}, wanted by ${sourceName(assigned)}`;
    }
    if (assigned && (assigned.state === 'active' || isHeld(assigned))) return sourceName(assigned);
    if (pin.used) {
      if (pin.owner) return pin.owner;
      if (CONSUMER_NAMES[pin.consumer]) return CONSUMER_NAMES[pin.consumer];
      if (pin.role) return pin.role;
      // A function the pin mux named (I2C2 SDA) with nothing a user configures on its bus: its driver is the kernel's.
      return pin.label && pin.label !== pin.consumer ? 'kernel' : pin.consumer || 'taken';
    }
    if (assigned && assigned.state !== 'removed') return `for ${sourceName(assigned)}`;
    return pin.role ? `free · ${pin.role}` : 'free';
  }
  function pinState(pin) {
    if (pin.type !== 'gpio') return pin.type;
    const assigned = assignmentFor(pin);
    if (assigned && assigned.state === 'conflict') return 'conflict';
    if (isHeld(assigned)) return 'taken';
    if (assigned && !pin.used) return 'assigned';
    return pin.used ? 'taken' : 'free';
  }
  const pinDetails = (pin) => pinOwner(pin);
  function pinTypeClass(pin) {
    if (pin.type === '3v3') return 'pin-3v3';
    if (pin.type === '5v') return 'pin-5v';
    if (pin.type === 'gnd') return 'pin-gnd';
    const state = pinState(pin);
    return state === 'conflict' ? 'pin-conflict' : state === 'assigned' ? 'pin-assigned' : state === 'taken' ? 'pin-taken' : 'pin-free';
  }
  function pinMark(pin) {
    if (pin.type === '3v3') return '3V3';
    if (pin.type === '5v') return '5V';
    if (pin.type === 'gnd') return 'GND';
    const state = pinState(pin);
    if (state === 'conflict') return '!';
    if (state === 'assigned') return '○';
    if (state === 'taken') return '●';
    return '';
  }
  function pinTitle(pin) {
    const bits = [`Pin ${pin.pin}`, pinLabel(pin)];
    if (pin.type !== 'gpio') bits.push(pin.type === 'gnd' ? 'ground' : 'power');
    else bits.push(pinOwner(pin));
    return bits.join(', ');
  }
  function pinSquare(pin) {
    const square = document.createElement('button');
    square.type = 'button';
    square.className = `pin-square ${pin ? pinTypeClass(pin) : 'pin-empty'}`;
    if (!pin) return square;
    square.title = pinTitle(pin);
    square.setAttribute('aria-label', pinTitle(pin));
    square.dataset.pin = String(pin.pin);
    square.addEventListener('click', () => showPin(pin));
    const number = document.createElement('b');
    number.textContent = String(pin.pin);
    const mark = document.createElement('small');
    mark.textContent = pinMark(pin);
    square.append(number, mark);
    return square;
  }
  function pinSide(pin, side) {
    const box = document.createElement('div');
    box.className = `pin-side ${side}`;
    if (!pin) return box;
    const name = document.createElement('span');
    name.textContent = pinLabel(pin);
    box.append(name);
    const owner = pinDetails(pin);
    if (owner) box.append(Object.assign(document.createElement('small'), { textContent: owner }));
    return box;
  }
  function pinCard(pin) {
    const state = pinState(pin);
    const cls = pin.type === 'gpio' ? (state === 'taken' ? 'used' : state) : 'power';
    const card = document.createElement('button');
    card.type = 'button';
    card.className = `pin ${cls} ${pinTypeClass(pin)}`;
    card.title = pinTitle(pin);
    card.dataset.pin = String(pin.pin);
    card.addEventListener('click', () => showPin(pin));
    const number = document.createElement('b');
    number.textContent = String(pin.pin);
    const name = document.createElement('span');
    name.textContent = pinLabel(pin);
    const detail = document.createElement('small');
    detail.textContent = pinDetails(pin);
    card.append(number, name, detail);
    return card;
  }
  function pinByNumber(number) {
    return ((lastPins && lastPins.header) || []).find((pin) => pin && pin.pin === number) || null;
  }
  function markSelected() {
    const map = $('pins-map');
    if (!map) return;
    map.querySelectorAll('.is-selected').forEach((node) => node.classList.remove('is-selected'));
    if (selectedPin) map.querySelectorAll(`[data-pin="${selectedPin.pin}"]`).forEach((node) => node.classList.add('is-selected'));
  }
  // A pin selected on the header fills the card beside it: its names, what holds it and where the kernel has it,
  // and, for a free pin, the choices below it.
  function pinFacts(pin) {
    const facts = [['Pin', String(pin.pin)], ['Name', pinLabel(pin)]];
    if (pin.type !== 'gpio') {
      facts.push(['Supply', pin.type === 'gnd' ? 'ground' : pin.type === '5v' ? '5 V' : '3.3 V']);
      return facts;
    }
    if (pin.name && pin.name !== pinLabel(pin)) facts.push(['GPIO', pin.name]);
    if (pin.rmio !== null && pin.rmio !== undefined) facts.push(['RM_IO', `RM_IO${pin.rmio}`]);
    if (pin.chip) facts.push(['Line', `${pin.chip} line ${pin.line}`]);
    facts.push(['Held by', pinOwner(pin)]);
    if (pin.used && pin.consumer && pin.consumer !== pinOwner(pin)) facts.push(['Kernel claim', pin.consumer]);
    if (pin.role) facts.push(['Board role', pin.role]);
    return facts;
  }
  function showPin(pin) {
    selectedPin = pin || null;
    markSelected();
    const note = $('pins-gpio-selected');
    const detail = $('pins-gpio-detail');
    if (!note) return;
    if (!pin) {
      note.textContent = 'Select a pin on the map, or in the list below.';
      if (detail) { detail.replaceChildren(); detail.hidden = true; }
      return;
    }
    if (detail) {
      detail.replaceChildren(
        ...pinFacts(pin).flatMap(([term, value]) => [
          Object.assign(document.createElement('dt'), { textContent: term }),
          Object.assign(document.createElement('dd'), { textContent: value }),
        ]),
      );
      detail.hidden = false;
    }
    const state = pinState(pin);
    const assigned = assignmentFor(pin);
    const hog = assigned && assigned.source === 'pinhog' && !assigned.removed;
    const free = pin.type === 'gpio' && (state === 'free' || Boolean(hog));
    note.textContent = free ? 'Read it once, or keep it set from boot, below.' : 'Only a free pin can be read or set.';
    if (!free) return;
    const select = $('pins-gpio-pin');
    if (select && [...select.options].some((option) => option.value === String(pin.pin))) {
      select.value = String(pin.pin);
      syncPinChoice();
    }
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
      row.append(pinSide(left, 'left'), pinSquare(left), pinSquare(right), pinSide(right, 'right'));
      map.append(row);
    }
  }
  // The legend lists a state only when a pin is drawn in it: a key for a state no pin is in is one more thing to read.
  function drawLegend(header) {
    const states = new Set((header || []).map(pinState));
    document.querySelectorAll('#pins-legend [data-state]').forEach((node) => { node.hidden = !states.has(node.dataset.state); });
  }
  function textOf(pins) {
    if (!pins || !pins.header || !pins.header.length) return '';
    const title = pins.model || pins.board || 'this board';
    const lines = [title, ''];
    const header = pins.header;
    const desc = (pin) => {
      if (pin.type !== 'gpio') return pinLabel(pin);
      const bits = [pinLabel(pin), pinOwner(pin)];
      if (pin.name && pin.name !== pinLabel(pin)) bits.push(pin.name);
      if (pin.rmio !== null && pin.rmio !== undefined) bits.push(`RM_IO${pin.rmio}`);
      if (pin.consumer && pin.consumer !== pinOwner(pin)) bits.push(pin.consumer);
      return bits.join(' · ');
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
    // Every other line in use, off the header: the card, the eMMC, the board's own LEDs and switches.
    const onHeader = new Set(header.filter((pin) => pin.chip).map((pin) => `${pin.chip}:${pin.line}`));
    const others = (pins.lines || []).filter((line) => line.used && !onHeader.has(`${line.chip}:${line.line}`));
    if (others.length) {
      lines.push('', 'Other lines in use');
      for (const line of others) lines.push(`${line.chip} ${line.line} ${line.consumer || 'claimed'} ${line.direction || ''}`.trimEnd());
    }
    return lines.join('\n');
  }
  function render(data) {
    lastAssignments = new Map(
      ((data && data.assignments) || [])
        .filter((item) => item && typeof item.pin === 'number')
        .map((item) => [item.pin, item]),
    );
    const pins = data && data.pins;
    if (pins) {
      lastAt = pins.at || lastAt;
      lastPins = pins;
    }
    renderMui(data && data.mui);
    renderOverlay(data && data.overlay);
    $('pins-copy').disabled = true;
    if (!pins) { $('pins-board').textContent = 'Not read yet.'; return; }
    const at = pins.at
      ? new Date(pins.at * 1000).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
      : 'now';
    const when = `${pins.model || pins.board || 'this board'}, read ${at}.`;
    $('pins-board').textContent = pins.note ? `${when} ${pins.note}` : when;
    drawHeader(pins.header || []);
    drawLegend(pins.header || []);
    fillPinChoices(pins.header || []);
    showPin(selectedPin ? pinByNumber(selectedPin.pin) : null);
    renderHogs((data && data.assignments) || []);
    $('pins-copy').disabled = false;
  }
  function fillPinChoices(header) {
    const select = $('pins-gpio-pin');
    if (!select) return;
    const chosen = select.value;
    const offered = header.filter((pin) => {
      if (pin.type !== 'gpio') return false;
      const assigned = assignmentFor(pin);
      return pinState(pin) === 'free' || (assigned && assigned.source === 'pinhog' && !assigned.removed);
    });
    select.replaceChildren(
      ...(offered.length
        ? offered.map((pin) => {
            const option = document.createElement('option');
            const assigned = assignmentFor(pin);
            option.value = String(pin.pin);
            option.textContent = `Pin ${pin.pin} — ${pinLabel(pin)}${assigned && assigned.source === 'pinhog' ? ' (hog)' : ''}`;
            return option;
          })
        : [Object.assign(document.createElement('option'), { value: '', textContent: 'No free pin' })]),
    );
    if (offered.some((pin) => String(pin.pin) === chosen)) select.value = chosen;
    syncPinChoice();
  }
  function renderHogs(assignments) {
    const host = $('pins-hogs');
    if (!host) return;
    const hogs = assignments.filter((item) => item && item.source === 'pinhog' && typeof item.pin === 'number');
    host.replaceChildren();
    if (!hogs.length) {
      host.append(
        Object.assign(document.createElement('p'), {
          className: 'setting-desc',
          textContent: 'No pin is held by a pin hog.',
        }),
      );
      return;
    }
    const table = document.createElement('table');
    table.className = 'admin-table';
    const head = document.createElement('thead');
    const headRow = document.createElement('tr');
    for (const title of ['Pin', 'Held as', 'Applied', '']) {
      const th = document.createElement('th');
      th.textContent = title;
      headRow.append(th);
    }
    head.append(headRow);
    const body = document.createElement('tbody');
    for (const hog of hogs) {
      const tr = document.createElement('tr');
      const how = hog.mode === 'output' ? `Output ${hog.value ? 'high' : 'low'}` : 'Input';
      const pull = hog.bias && hog.bias !== 'none' ? `, pull-${hog.bias}` : '';
      const when = hog.pending
        ? hog.removed
          ? 'Removing after a restart'
          : 'After a restart'
        : hog.state === 'conflict'
          ? 'Conflict'
          : hog.state === 'holding'
            ? 'Held from boot'
            : 'Did not apply';
      for (const value of [String(hog.pin), how + pull, when]) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      }
      const td = document.createElement('td');
      const button = document.createElement('button');
      button.className = 'action-btn';
      button.type = 'button';
      button.textContent = 'Remove';
      button.addEventListener('click', async () => {
        if (!(await AW.confirmHere(`Remove pin ${hog.pin}'s pin hog? A restart applies it.`, { anchor: button, label: 'Remove' }))) return;
        button.disabled = true;
        gpioSay('Removing the pin hog…', true);
        try {
          const reply = await post({ action: 'pins-hog', pin: hog.pin, remove: true });
          await waitPin(reply.id, button);
        } catch (err) {
          gpioSay(err.message || 'The pin hog could not be removed.', false);
          button.disabled = false;
        }
      });
      td.append(button);
      tr.append(td);
      body.append(tr);
    }
    table.append(head, body);
    host.append(table);
  }
  async function waitPin(id, button) {
    for (let i = 0; i < 40; i++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      let data;
      try { data = await get(); } catch (_) { continue; }
      const done = (data.results || []).find((result) => result.id === id);
      if (!done) continue;
      render(data);
      gpioSay(answerSaid(done) || (done.ok ? 'Done.' : 'The pin action failed.'), done.ok);
      if (button) button.disabled = false;
      return;
    }
    gpioSay('The pin action was not confirmed in time.', false);
    if (button) button.disabled = false;
  }
  function syncPinChoice() {
    const assigned = lastAssignments.get(Number($('pins-gpio-pin').value));
    if (!assigned || assigned.source !== 'pinhog' || assigned.removed) return;
    $('pins-gpio-mode').value = assigned.mode === 'output' ? 'out' : 'in';
    $('pins-gpio-bias').value = assigned.bias || 'none';
    $('pins-gpio-value').value = String(assigned.value ? 1 : 0);
    $('pins-gpio-value-box').hidden = assigned.mode !== 'output';
  }
  function pinChoice() {
    const mode = $('pins-gpio-mode').value;
    const pin = Number($('pins-gpio-pin').value);
    const body = { pin, mode, bias: $('pins-gpio-bias').value };
    if (mode === 'out') body.value = Number($('pins-gpio-value').value);
    return body;
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
  async function waitOverlay(id) {
    for (let i = 0; i < 40; i++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      let data;
      try { data = await get(); } catch (_) { continue; }
      const done = (data.results || []).find((result) => result.id === id);
      if (!done) continue;
      if (done.ok) {
        renderOverlay(data.overlay);
        ovSay(answerSaid(done) || 'Saved.', true);
      } else {
        ovSay(answerSaid(done) || 'The overlay could not be changed.', false);
        $('pins-overlay-toggle').disabled = false;
      }
      return;
    }
    ovSay('The overlay change was not confirmed in time.', false);
    $('pins-overlay-toggle').disabled = false;
  }
  async function waitDisplay(id, long) {
    const tries = long ? 150 : 40;
    for (let i = 0; i < tries; i++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      let data;
      try { data = await get(); } catch (_) { continue; }
      const done = (data.results || []).find((result) => result.id === id);
      if (!done) continue;
      renderOverlay(data.overlay);
      if (done.ok) dispSay(answerSaid(done) || 'Done.', true);
      else dispSay(answerSaid(done) || 'The display could not be changed.', false);
      return;
    }
    renderOverlay(lastOverlay);
    dispSay('The display change was not confirmed in time.', false);
  }
  $('pins-gpio-pin').addEventListener('change', () => {
    syncPinChoice();
    showPin(pinByNumber(Number($('pins-gpio-pin').value)));
  });
  $('pins-gpio-mode').addEventListener('change', () => {
    $('pins-gpio-value-box').hidden = $('pins-gpio-mode').value !== 'out';
  });
  $('pins-gpio-read').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const body = pinChoice();
    if (!body.pin) return;
    button.disabled = true;
    gpioSay('Reading the pin…', true);
    try {
      const reply = await post({ action: 'pins-gpio', ...body });
      await waitPin(reply.id, button);
    } catch (err) {
      gpioSay(err.message || 'The pin could not be read.', false);
      button.disabled = false;
    }
  });
  $('pins-gpio-hog').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const body = pinChoice();
    if (!body.pin) return;
    const how = body.mode === 'out' ? `an output, ${body.value ? 'high' : 'low'}` : 'an input';
    if (
      !(await AW.confirmHere(`Keep pin ${body.pin} as ${how} from boot? This changes the boot files; a restart applies it.`, {
        anchor: button,
        label: 'Keep it set',
      }))
    )
      return;
    button.disabled = true;
    gpioSay('Saving the pin hog…', true);
    try {
      const reply = await post({ action: 'pins-hog', ...body });
      await waitPin(reply.id, button);
    } catch (err) {
      gpioSay(err.message || 'The pin hog could not be saved.', false);
      button.disabled = false;
    }
  });
  // After a MUI change too: the map is then this read, not the one before the change.
  async function readPins() {
    $('pins-scan').disabled = true;
    say('Reading the GPIO lines…', true);
    const before = lastAt;
    try {
      const reply = await post({ action: 'pins' });
      await wait(reply.id, before);
    } catch (err) { say(err.message || 'The pins could not be read.', false); $('pins-scan').disabled = false; }
  }
  $('pins-scan').addEventListener('click', readPins);
  $('pins-copy').addEventListener('click', async () => {
    if (!lastPins) return;
    const ok = await copyText(textOf(lastPins));
    say(ok ? 'The header is copied as text.' : 'The header could not be copied.', ok);
  });
  $('pins-overlay-toggle').addEventListener('click', async (event) => {
    if (!lastOverlay || !lastOverlay.supported) return;
    const on = !lastOverlay.on;
    const button = event.currentTarget;
    const question = on
      ? 'Enable the SPI1 display and touch overlay? A restart applies it.'
      : 'Disable the SPI1 display and touch overlay? A restart removes it.';
    if (!(await AW.confirmHere(question, { anchor: button, label: on ? 'Enable' : 'Disable' }))) return;
    button.disabled = true;
    ovSay(on ? 'Saving the overlay…' : 'Turning the overlay off…', true);
    try {
      const reply = await post({ action: 'pins-overlay', on });
      await waitOverlay(reply.id);
    } catch (err) {
      ovSay(err.message || 'The overlay could not be changed.', false);
      button.disabled = false;
    }
  });
  $('pins-display-toggle').addEventListener('click', async (event) => {
    if (!lastOverlay || !lastOverlay.supported || !lastOverlay.active) return;
    const on = !lastOverlay.display_on;
    const button = event.currentTarget;
    const question = on ? 'Turn the display on and show the test card?' : 'Turn the display off?';
    if (!(await AW.confirmHere(question, { anchor: button, label: on ? 'Turn on' : 'Turn off' }))) return;
    button.disabled = true;
    dispSay(on ? 'Turning the display on…' : 'Turning the display off…', true);
    try {
      const reply = await post({ action: 'pins-display', on });
      await waitDisplay(reply.id);
    } catch (err) {
      dispSay(err.message || 'The display could not be changed.', false);
      button.disabled = false;
    }
  });
  $('pins-test').addEventListener('click', async (event) => {
    if (!lastOverlay || !lastOverlay.supported || !lastOverlay.active) return;
    if (
      !(await AW.confirmHere('Run the 60-second screen test? The display turns off at the end.', {
        anchor: event.currentTarget,
        label: 'Run the test',
      }))
    )
      return;
    $('pins-test').disabled = true;
    $('pins-display-toggle').disabled = true;
    dispSay('Running the 60-second test…', true);
    try {
      const reply = await post({ action: 'pins-test' });
      await waitDisplay(reply.id, true);
    } catch (err) {
      dispSay(err.message || 'The test could not be run.', false);
      renderOverlay(lastOverlay);
    }
  });
  $('pins-mui-bus').addEventListener('change', () => { muiRebuild(); });
  $('pins-mui-touch').addEventListener('change', () => { muiRebuild(); });
  for (const id of Object.values(MUI_ROLE_SELECTS)) $(id).addEventListener('change', () => { muiUpdate(); });
  $('pins-mui-replace-radio').addEventListener('change', () => { muiUpdate(); });
  $('pins-mui-log-level').addEventListener('change', () => { muiUpdate(); });
  $('pins-mui-log-destination').addEventListener('change', () => { muiUpdate(); });
  $('pins-mui-map-tiles').addEventListener('change', () => { muiUpdate(); });
  $('pins-mui-map-folder').addEventListener('input', () => { muiUpdate(); });
  $('pins-mui-activate').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    if (!muiOptions || !muiStatus) return;
    const selection = muiSelectionNow();
    const dup = muiDuplicates(selection);
    if (dup) { muiSayText(`Two roles use pin ${selection[dup[0]]}. Select different pins.`, false); return; }
    if (muiStatus.radio && muiStatus.radio.configured && !selection.replace_radio) {
      muiSayText('meshtasticd has a real radio configured. Tick the SimRadio setting to replace it.', false);
      return;
    }
    const applying = muiHolds();
    const question = applying
      ? 'Apply these MUI settings? A changed pin changes the boot files, and a restart applies it.'
      : 'Activate MUI with these pins? This changes the boot files and the radio daemon; a restart applies it.';
    if (!(await AW.confirmHere(question, { anchor: button, label: applying ? 'Apply' : 'Activate MUI' }))) return;
    button.disabled = true;
    muiSayText('Saving MUI…', true);
    try {
      const reply = await post({ action: 'mui-activate', selection });
      const done = await waitMui(reply.id);
      if (done && done.ok) {
        muiSayText(answerSaid(done) || 'MUI is saved.', true);
        readPins();
      }
    } catch (err) {
      muiSayText(err.message || 'MUI could not be activated.', false);
      muiUpdate();
    }
  });
  $('pins-mui-deactivate').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    if (!(await AW.confirmHere('Turn MUI off and re-enable meshtasticd when it was on? A restart removes the overlay.', { anchor: button, label: 'Turn MUI off' }))) return;
    button.disabled = true;
    muiSayText('Turning MUI off…', true);
    try {
      const reply = await post({ action: 'mui-deactivate' });
      const done = await waitMui(reply.id);
      if (done && done.ok) {
        muiSayText(answerSaid(done) || 'MUI is off.', true);
        readPins();
      }
    } catch (err) {
      muiSayText(err.message || 'MUI could not be turned off.', false);
      muiUpdate();
    }
  });
  $('pins-mui-restart').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    if (!(await AW.confirmHere('Restart the box now to apply the MUI overlay?', { anchor: button, label: 'Restart now' }))) return;
    button.disabled = true;
    muiSayText('The box is restarting…', true);
    try {
      const reply = await post({ action: 'mui-restart' });
      const done = await waitMui(reply.id);
      if (done && done.ok) muiSayText(answerSaid(done) || 'The box is restarting.', true);
    } catch (err) {
      muiSayText(err.message || 'The box could not be restarted.', false);
      button.disabled = false;
    }
  });
  if (typeof window !== 'undefined') window.rkRenderPins = render;
  get().then(render).catch(() => {});
})();
