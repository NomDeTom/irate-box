// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin → Mock controls: every control the admin pages use, drawn by the same calls the pages make, with stand-in
// data and nothing sent. Two uses: the example to copy when a page needs a control, and the place to pick one from.
// The list follows the menu mock's Controls view (the 2026-10-07 menu overhaul), then the controls the admin has
// had since, then the cards and findings the doctors draw, then what the pages draw beyond the shared widgets. The mock itself is out of date: none of its code is here.
// Each row keeps its control in one cell (.mock-cell), so a second way of drawing it can sit beside it.
(() => {
  'use strict';
  if (typeof AW === 'undefined' || typeof AL === 'undefined') return;
  const { h, btn } = AW;
  const ID = 'mock-controls';
  // A save that waits a moment and sends nothing, as a real one waits for the box.
  const pretendSave = (words) => new Promise((resolve) => { setTimeout(() => resolve(words || 'Saved here only. Nothing was sent.'), 600); });

  // One control: its name, when to use it, the control itself, and the call that draws it.
  function row(id, name, use, code, ...control) {
    return h('section', { class: 'mock-row', id: `mock-${id}`, 'aria-labelledby': `mock-${id}-name` },
      h('h3', { id: `mock-${id}-name`, text: name }),
      h('p', { class: 'setting-desc', text: use }),
      h('div', { class: 'mock-cells' }, h('div', { class: 'mock-cell', 'data-drawn-by': 'hub' }, ...control)),
      h('p', { class: 'mock-code' }, h('span', { class: 'mock-code-label', text: 'Drawn by: ' }), h('code', { text: code })));
  }
  const group = (title, intro, ...rows) => h('div', { class: 'mock-group' }, h('h3', { class: 'mock-group-title', text: title }),
    h('p', { class: 'setting-desc', text: intro }), ...rows);
  // A checkbox setting, as a page writes one, drawn as On and Off chips.
  const mockOnOff = () => {
    const box = h('input', { type: 'checkbox', checked: true });
    const label = h('label', { class: 'setting' }, box, h('span', {}, h('span', { class: 'setting-name', text: 'Show the stand-in tile on the hub' }),
      h('span', { class: 'setting-desc', text: 'The page\'s code reads the checkbox as before.' })));
    AW.onOff(box);
    return label;
  };
  // A line under a control that says what it did, as a page's note would.
  const said = () => h('p', { class: 'setting-desc action-note', role: 'status' });

  // ---- The menu mock's Controls view, as the admin draws each one now ----
  function fromTheMock() {
    const removeNote = said();
    const actionButtons = h('div', { class: 'library-buttons' },
      btn('Refresh'),
      btn('Remove now', { onclick: async (event) => {
        const yes = await AW.confirmHere('Remove the stand-in? Nothing is removed: this page sends nothing.', { anchor: event.currentTarget, label: 'Remove now' });
        removeNote.textContent = yes ? 'Removed (here only).' : 'Kept.';
      } }),
      btn('Install', { class: 'action-btn primary' }),
      btn('Small', { class: 'action-btn small' }),
      btn('Not now', { disabled: true, title: 'Greyed, with the reason in its title' }));

    const tabs = h('div', { class: 'view-switch', role: 'group', 'aria-label': 'Range' }, ['72 hours', '30 days'].map((label, index) =>
      h('button', { type: 'button', class: 'view-switch-btn' + (index ? '' : ' active'), 'aria-pressed': String(!index), onclick: (event) => {
        event.currentTarget.parentNode.querySelectorAll('button').forEach((tab) => {
          tab.classList.toggle('active', tab === event.currentTarget);
          tab.setAttribute('aria-pressed', String(tab === event.currentTarget));
        });
      } }, label)));

    // The uptime grid: 72 hours by the hour, a few hours down and a gap with no data.
    // The column heads and each cell's words as linkhistory.py makes them: the hour every 12 columns, and each
    // hour's span ("Tue 07 14:00–15:00"), in this browser's time.
    const hourStart = (index) => { const at = new Date(); at.setMinutes(0, 0, 0); at.setHours(at.getHours() - (71 - index)); return at; };
    const clock = (at) => `${String(at.getHours()).padStart(2, '0')}:00`;
    const hourHeads = Array.from({ length: 72 }, (_, index) => (index % 12 === 0 ? clock(hourStart(index)) : ''));
    const hourSpans = Array.from({ length: 72 }, (_, index) => {
      const at = hourStart(index), end = new Date(at.getTime() + 3600e3);
      return `${at.toLocaleDateString('en-GB', { weekday: 'short' })} ${String(at.getDate()).padStart(2, '0')} ${clock(at)}–${clock(end)}`;
    });
    const hours = Array.from({ length: 72 }, (_, index) => (index > 60 && index < 64 ? null : { up: index === 20 ? 0 : index % 17 === 0 ? 0.93 : 1, n: 60, drops: index % 17 === 0 ? 1 : 0 }));
    const uptime = typeof Heatmap === 'undefined' ? h('p', { class: 'setting-desc', text: 'The heatmap script is not loaded.' })
      : h('div', {}, Heatmap.grid({ caption: 'A stand-in service, the last 72 hours by hour', cols: hourHeads, rows: [{ label: 'Stand-in', cells: hours, where: (index) => hourSpans[index] }] }), Heatmap.legend());

    const entries = Array.from({ length: 30 }, (_, index) => ({ id: `entry-${index}`, title: `Entry ${index + 1}`, summary: 'A one-line summary',
      badges: [index % 7 ? 'ok' : 'queued'], detail: { Size: `${index + 1} MB`, Added: `2026-10-${String((index % 28) + 1).padStart(2, '0')}` } }));
    const cardItems = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf'].map((title, index) => ({ id: `card-${index}`, title,
      summary: index === 2 ? 'Installed, 4 MB' : 'Not installed', badges: index === 2 ? ['installed'] : index === 4 ? ['update available'] : [] }));
    const records = Array.from({ length: 60 }, (_, index) => ({ id: `record-${index}`, title: `2026-10-22 ${String(23 - (index % 24)).padStart(2, '0')}:00  A stand-in event ${60 - index}` }));

    return group('From the menu mock', 'The controls the menu mock listed, in its order, each as the admin pages draw it now.',
      row('buttons', 'Action buttons', 'Anything that does something at once. The one main action of a block is filled. One that removes something asks first, on the page.',
        "AW.btn(label, {onclick}); {class: 'action-btn primary'}; AW.confirmHere(text, {anchor, label})", actionButtons, removeNote),
      row('chips', 'Chips: one of a few', 'An exclusive choice that is a setting, each choice a word or two.',
        "AW.settings([{key, label, kind: 'choice', options, value}])",
        AW.settings([{ key: 'access', label: 'Who can open it', kind: 'choice', options: [['guests', 'Guests'], ['users', 'Users'], ['admin', 'Administrators'], ['off', 'Off']], value: 'guests' }])),
      row('filter-chips', 'Filter chips: any of a few', 'Several at once, as a setting, or to filter a list.',
        "AW.settings([{key, label, kind: 'multi', options, value: [...]}])",
        AW.settings([{ key: 'boards', label: 'Boards', kind: 'multi', options: ['rak4631', 'heltec-v3', 't-beam', 'station-g2'], value: ['rak4631'] }])),
      row('toggle', 'On and Off chips: a yes/no setting', 'A setting that is on or off: two chips, the one in effect selected, its name beside them. Every yes/no setting is drawn so; a checkbox is for picking items from a list, an option of a one-off form, or a consent.',
        "AW.settings([{key, label, kind: 'toggle', value}])",
        AW.settings([{ key: 'keep', label: 'Keep current', kind: 'toggle', value: true }])),
      row('onoff-box', 'On and Off chips over a checkbox', 'A yes/no setting the page writes as a checkbox (a .settings group, a form): the same chips, the checkbox out of sight holding the value, so the code that reads and saves it is unchanged.',
        'AW.onOff(input[type="checkbox"])', mockOnOff()),
      row('tabs', 'Joined tabs', 'Views of the same thing: the data stays, the range changes.',
        'class="view-switch" with buttons class="view-switch-btn" (Overview → Memory and disk history)', tabs),
      row('pills', 'Pills', 'A state, one meaning each. The word sets the colour unless told.',
        "AW.pill(word[, tone]); AW.updatePill(checked, available, failed)",
        h('div', { class: 'chip-group' }, AW.pill('ok'), AW.pill('queued'), AW.pill('failed'), AW.pill('info', 'info'),
          AW.updatePill(Date.now() / 1000, false), AW.updatePill(0, true), AW.updatePill(0, false, true))),
      row('help', 'Help that expands', 'The longer explanation, next to the control it explains.',
        '<details class="field-help"><summary>…</summary>…</details>',
        h('details', { class: 'field-help' }, h('summary', { text: 'What does this do?' }), h('p', { class: 'setting-desc', text: 'The longer explanation. It stays closed until asked for.' }))),
      row('fold', 'A fold', 'A part of a page to open when wanted: its history, its access, its maintenance. Every part of a page folds this way, its name in bold. The mock\'s button that changes its own words is not used. A program\'s output and help fold smaller, in their own rows.',
        '<details class="sec-fold"><summary>…</summary>…</details>',
        h('details', { class: 'sec-fold' }, h('summary', { text: 'Memory and disk history' }), h('p', { class: 'setting-desc', text: 'What the fold holds: a chart, a table, the settings of a part.' }))),
      row('uptime', 'Uptime', 'Hours or days as cells, coloured by how much of each was up, said in a tooltip and to a screen reader.',
        'Heatmap.grid({caption, cols, rows: [{label, cells, where}]}); Heatmap.legend()', uptime),
      row('short-list', 'Short entries, with a filter', 'A long but limited list: one line each, the details open from the line. More than AW.FILTER_AT entries get a filter.',
        'AW.shortList([{id, title, summary, badges, detail}])', AW.shortList(entries, { id: 'mock-entries' })),
      row('cards', 'Cards with a drawer', 'Things to manage one at a time: the drawer opens inside the card. A drawer with settings has Save and Discard.',
        'AW.cards([{id, title, summary, badges}], {body(item, setDirty), save(item)})',
        AW.cards(cardItems, { id: 'mock-cards', save: () => pretendSave(), body: (item, setDirty) => {
          let chips;
          const draw = (on) => {
            const next = AW.onOffChips('Start at boot', on, false, (value) => { draw(value); setDirty(value); });
            if (chips) chips.replaceWith(next);
            chips = next;
          };
          draw(false);
          return [AW.dl({ Name: item.title, State: item.summary }), h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Start at boot' }), chips)];
        } })),
      row('records', 'Records', 'An unlimited list, newest first: a limited height, a scroll bar and a filter.',
        "AW.records([{id, title}], {noun})", AW.records(records, { id: 'mock-records', noun: 'events' })));
  }

  // ---- The admin's controls since the mock ----
  function sinceTheMock() {
    // A settings section: Save and Discard, Saving…, and a warning while a costly change is unsaved.
    const settings = AW.settings([
      { key: 'name', label: 'Name', kind: 'text', value: 'Stand-in', max: 40 },
      { key: 'access', label: 'Who can open it', kind: 'choice', options: [['guests', 'Guests'], ['users', 'Users'], ['off', 'Off']], value: 'guests', decision: true },
      { key: 'country', label: 'Country', kind: 'select', options: [['GB', 'United Kingdom'], ['DE', 'Germany'], ['FR', 'France'], ['IE', 'Ireland'], ['NL', 'Netherlands'], ['US', 'United States'], ['NZ', 'New Zealand']], value: 'GB',
        warn: (value) => (value === 'US' ? 'The hotspot\'s channels change with the country.' : '') },
      { key: 'boot', label: 'Start at boot', kind: 'toggle', value: false },
      { key: 'size', label: 'Space used', kind: 'readout', value: '12 MB' },
    ], { id: 'mock-settings', label: 'Stand-in settings', save: () => pretendSave(),
      warn: (changes) => (changes.access === 'off' ? 'Off: nobody can open it, guests included, until it is set again.' : null) });

    const tiles = h('div');
    AW.choices(tiles, 'mock-mode', [
      { value: 'open', title: 'Open', desc: 'Anyone in range can join.' },
      { value: 'password', title: 'Password', desc: 'People join with a password you give them.', does: 'The box makes one. You can change it.' },
      { value: 'enterprise', title: 'Accounts', desc: 'Each person signs in with their own account.', why: 'this radio has no driver support for it' },
    ], { value: 'password' });

    // In a form of the pages' kind (form.library-form), as the Updates and Firmware pages hold them: it sets the labels' size.
    const asOnPages = (...kids) => h('form', { class: 'library-form', onsubmit: (event) => event.preventDefault() }, ...kids);
    const fewSelect = asOnPages(h('label', {}, 'Check interval ', h('select', { name: 'mock-few' }, ['Manual', '6 hours', '24 hours', 'Weekly'].map((label) => h('option', { value: label }, label)))));

    const pattern = asOnPages(AW.updatePattern({ often: 'mock-often', act: 'mock-act', value: { often: '24', act: '1' } }),
      AW.updateButtons({ badge: AW.updatePill(0, true), check: { onclick: () => {} }, fetch: { onclick: () => {} }, install: { disabled: true, why: 'Download it first.' } }));

    // A block the page draws again every few seconds: select some of its words and they stay selected.
    const kept = h('div', { class: 'mock-kept' });
    let ticks = 0;
    const drawKept = () => {
      ticks += 1;
      AW.drawKept(kept, [h('p', { class: 'setting-desc', text: 'Select these words, then wait: the block is drawn again every 3 seconds and the selection stays.' }),
        h('p', { class: 'setting-desc', 'data-live': 'ticks', text: `Drawn ${ticks} times.` })]);
    };
    drawKept();
    setInterval(() => { if (AL.shown(ID)) drawKept(); }, 3000);

    return group('Since the mock', 'Controls the admin pages have had since the menu mock.',
      row('section', 'A block of settings', 'Settings saved together: Discard and Save in one footer, no question at Save, a warning in the footer while a costly change is unsaved. "Setup decision" marks one the setup steps ask.',
        "AW.settings(rows, {id, label, save, warn}); kinds: text, choice, select (with warn), toggle, readout; decision: true", settings),
      row('choice-tiles', 'Choice tiles', 'One of a few, each needing a sentence. One that is not available here says why.',
        'AW.choices(box, name, [{value, title, desc, does, why}], {value, onChange})', tiles),
      row('select-chips', 'A drop-down of a few, as chips', 'A drop-down of up to six choices is drawn as chips. The drop-down stays underneath for the form.',
        'AW.chipSelect(select) (done for every select on the page)', fewSelect),
      row('update', 'Updates: how often, how far, and the three buttons', 'The same two choices and the same three buttons wherever something is updated.',
        'AW.updatePattern({often, act, value}); AW.updateButtons({badge, check, fetch, install})', pattern),
      row('kept', 'A block kept while it is drawn again', 'A block the page redraws on every poll: a selection, a focus or an open fold in it stays.',
        'AW.drawKept(box, nodes); live parts marked data-live', kept));
  }

  // ---- The cards and findings the doctors draw, one of each ----
  function cardsAndFindings() {
    const STATES = ['problem', 'warn', 'suggest', 'info', 'ok', 'not-here', 'accepted', 'fixed'];
    const rows = typeof findingRow === 'function'
      ? STATES.map((state) => findingRow({
        id: `mock-finding-${state}`, state, title: `A stand-in finding: ${state}`,
        detail: 'What was found, in one or two sentences.',
        why: state === 'problem' || state === 'warn' ? 'Why it matters.' : '',
        evidence: state === 'problem' ? ['/etc/example.conf', 'example.service'] : [],
        how: state === 'problem' ? 'What to do about it.' : state === 'ok' ? 'How to undo it.' : '',
        commands: state === 'problem' ? ['sudo systemctl restart example'] : [],
        controls: state === 'warn' ? [btn('Change it', { class: 'action-btn small' })] : [],
      }))
      : [h('p', { class: 'setting-desc', text: 'The finding cards are drawn by admin.js, which is not loaded.' })];
    const doctors = AW.findings([
      { id: 'mock-short-problem', title: 'A problem', summary: 'Worst first', status: 'problem', detail: 'What was found.', actions: [{ choice: 'fix', label: 'Repair', confirm: 'Repair it? Nothing is sent from this page.' }] },
      { id: 'mock-short-warn', title: 'A warning', summary: 'To review', status: 'warn', detail: 'What was found.' },
      { id: 'mock-short-info', title: 'For information', summary: 'Said once', status: 'info' },
      { id: 'mock-short-ok', title: 'Passed', summary: 'Nothing to do', status: 'ok' },
    ], { id: 'mock-short-findings', act: () => {} });
    return group('Cards and findings', 'One of each kind the doctors and the Security page draw, for reference.',
      row('finding-cards', 'Finding cards, one per state', 'The subject first, then its state at the right, a word or two. The coloured edge supports the word and never replaces it.',
        "findingRow({id, state, title, detail, why, evidence, how, commands, controls, accept}) (admin.js)", h('div', { class: 'mock-findings' }, rows)),
      row('finding-list', 'Findings as a short list', 'A doctor\'s list, worst first, the cure beside the finding. An action that changes the box asks on the page.',
        'AW.findings([{id, title, summary, status, detail, actions: [{choice, label, confirm}]}], {act})', doctors));
  }

  // ---- What the pages draw beyond the shared widgets: their own markup and admin.js's helpers ----
  // Found by comparing every page's elements with this one's (2026-10-22). A part whose real code sends something
  // (an app's Access) is drawn with the same helpers and a save that sends nothing.
  function fromThePages() {
    const field = (label, input) => h('label', {}, label, ' ', input);
    const story = h('textarea', { name: 'mock-story', rows: '3', maxlength: '1000', placeholder: 'Message', 'data-emoji': '' });
    const fields = h('form', { class: 'library-form', onsubmit: (event) => event.preventDefault() },
      field('Name', h('input', { type: 'text', name: 'mock-name', maxlength: '32' })),
      field('Retries', h('input', { type: 'number', name: 'mock-retries', min: '0', max: '10', value: '3' })),
      field('Password', h('input', { type: 'password', name: 'mock-password', minlength: '8', autocomplete: 'new-password' })),
      field('Address', h('input', { type: 'url', name: 'mock-url', placeholder: 'https://' })),
      field('Message (with the emoji button)', story),
      field('A file', h('input', { type: 'file', name: 'mock-file' })),
      h('label', { class: 'setting' }, h('input', { type: 'checkbox', name: 'mock-tick' }),
        h('span', { class: 'setting-desc', text: 'I understand that this runs someone else\'s code on this box\'s address' })));
    if (window.EMOJI) window.EMOJI.attach(story);
    const colour = AW.settings([{ key: 'accent', label: 'Accent colour', kind: 'colour', value: '', shown: '#9d8ff9', unset: 'Theme colour',
      warn: (value) => (value && value.toLowerCase() === '#ffff00' ? 'Yellow on white is hard to read.' : '') }]);

    const progress = h('div', {},
      h('div', { class: 'admin-progress' }, h('progress', { class: 'admin-bar', max: '1', value: '0.6' }), h('p', { class: 'setting-desc', text: 'Step 3 of 5: unpacking.' })),
      h('details', { class: 'admin-output' }, h('summary', { text: 'Installer output' }),
        h('pre', { class: 'admin-log', text: 'Downloading… 4.1 MB\nChecking the signature… good\nUnpacking… done' })),
      h('p', { class: 'setting-desc', text: 'Space used: 1.2 GB of 4 GB' }),
      h('div', { class: 'admin-meter' }, h('div', { style: 'width: 30%' })));

    // Tabs with their panels, wired by admin.js's own initTabs (arrow keys, the choice kept in this browser).
    const tabs = h('div', { class: 'view-switch', role: 'tablist', 'aria-label': 'Where new books come from', 'data-tabs': 'mock' },
      ['Kiwix library', 'Upload', 'Address'].map((label, index) => h('button', { type: 'button', class: 'view-switch-btn', role: 'tab',
        id: `mock-tab-${index}`, 'aria-controls': `mock-panel-${index}` }, label)));
    const panels = ['Books from the Kiwix library.', 'A book from this device.', 'A book from an address.'].map((text, index) =>
      h('div', { role: 'tabpanel', id: `mock-panel-${index}`, 'aria-labelledby': `mock-tab-${index}` }, h('p', { class: 'setting-desc', text })));
    const tabbed = h('div', {}, tabs, ...panels);
    if (typeof initTabs === 'function') setTimeout(() => initTabs(tabs));

    const table = h('div', { class: 'scroll-x' }, h('table', { class: 'admin-table' },
      h('caption', { text: 'The largest processes, read at 14:05' }),
      h('thead', {}, h('tr', {}, ['Process', 'PID', 'Resident memory'].map((label) => h('th', { text: label })))),
      h('tbody', {}, [['kiwix-serve', '812', '41 MB'], ['python3', '640', '33 MB'], ['nginx', '512', '6 MB']].map((cells) =>
        h('tr', {}, cells.map((cell) => h('td', { text: cell })))))));

    const empty = h('div', {}, h('p', {}, h('span', { class: 'empty', text: 'No threads' })),
      h('p', { class: 'setting-desc', text: 'None yet.' }));

    // An app's page: its facts, then its Access (chipRow and the section, as accessBlock draws them).
    const facts = h('p', { class: 'inv-facts' }, ['App', 'Installed', '4 MB', 'Service: running'].map((text) => h('span', { class: 'inv-fact', text })),
      h('span', { class: 'inv-fact bad', text: 'Its update failed' }));
    const access = AW.section({ id: 'mock-access', label: 'Access to the stand-in app', saved: { mode: 'public' }, save: () => pretendSave(),
      warn: (changes) => (changes.mode === 'off' ? 'Disabled: its tile goes, and nobody can open it.' : null), onChange: () => drawAccess() });
    const accessBox = h('div', { class: 'access-set' });
    function drawAccess() {
      accessBox.replaceChildren(
        typeof chipRow === 'function' && typeof OPEN_CHIPS !== 'undefined'
          ? chipRow('Access', OPEN_CHIPS, access.get('mode'), (value) => access.set('mode', value))
          : h('p', { class: 'setting-desc', text: 'chipRow is drawn by admin.js, which is not loaded.' }),
        h('p', { class: 'setting-desc', text: access.get('mode') === 'off' ? 'Disabled: no tile.' : 'Anyone on the network can open it. Visibility: every visitor sees the tile.' }),
        access.foot, access.reason);
    }
    drawAccess();

    // The home page's tiles as the hub writes them (server.py's tile HTML), and two of its live tiles.
    const tile = (cls, icon, name, desc, extra) => h('a', { class: `service-card${cls}`, href: '#mock-controls' },
      h('span', { class: 'icon', text: icon }), h('span', { class: 'name', text: name }), h('span', { class: 'desc', text: desc }), extra || null);
    const tiles = h('div', { class: 'service-grid' },
      tile('', '🎨', 'Themes', 'Light, dark, E-ink and more'),
      tile(' down', '📝', 'Shared notes', 'Not running: faded, or a pill in High contrast', h('span', { class: 'tile-pill down-pill', text: 'not available' })),
      tile(' locked', '⌨️', 'Terminal', 'For administrators', h('span', { class: 'lock', text: '🔒 sign in to open' })),
      tile('', '📚', 'Kiwix', 'Seen only by administrators', h('span', { class: 'tile-pill admin-pill', title: 'Only administrators see this tile', text: 'admin only' })),
      h('div', { class: 'service-card people-card' }, h('span', { class: 'icon', text: '👥' }), h('span', { class: 'name', text: '3' }), h('span', { class: 'desc', text: 'Users online' })),
      h('div', { class: 'service-card system-card' },
        h('span', { class: 'meter' }, h('span', { class: 'meter-label' }, 'Memory ', h('b', { text: '1.8 GB free' })), h('span', { class: 'bar' }, h('span', { style: 'width: 64%' }))),
        h('span', { class: 'meter' }, h('span', { class: 'meter-label' }, 'Disk ', h('b', { text: '2.0 GB free' })), h('span', { class: 'bar' }, h('span', { style: 'width: 31%' })))));

    // The setup steps' list, as admin-tour.js's drawList writes each step.
    const step = (cls, label, pill, word, text, mute) => h('li', { class: cls }, h('strong', { text: label }), ' ', h('span', { class: pill, text: word }),
      h('span', { class: 'step-text' }, `${text} `, h('a', { href: '#mock-controls', text: 'Open settings' }),
        mute ? [' ', h('button', { type: 'button', class: 'link-button mute-step', text: mute })] : null));
    const steps = h('ol', { class: 'setup-steps' },
      step('step-ok', 'Administrator password', 'ok-pill', 'done', 'Set.'),
      step('step-todo', 'WiFi country', 'info-pill', 'to do', 'Not set: the box uses the world setting.', 'Stop reminders'),
      step('step-warn', 'Security updates', 'warn-pill', 'needs review', 'Automatic updates are off.', 'Stop reminders'),
      step('step-muted', 'Backups', 'info-pill', 'no reminders', 'No backup made yet.', 'Resume reminders'));

    const go = h('p', {}, h('a', { class: 'action-btn go-btn', href: '#mock-controls', text: 'Show reason: Updates doctor →' }));

    // A package's card on the Updates page, as pkgCard draws it (its buttons send nothing here).
    const button = (label, primary) => (typeof actionButton === 'function'
      ? actionButton(label, () => {}, primary ? { className: 'primary' } : {})
      : btn(label, primary ? { class: 'action-btn primary' } : {}));
    const updateCard = h('div', { class: 'setting library-source update-card' }, h('span', { class: 'update-card-body' },
      h('span', { class: 'setting-name' }, 'kiwix-tools ', AW.updatePill(0, true)),
      h('span', { class: 'setting-desc', text: 'Installed 3.7.0. Newer: 3.8.1, from the Debian archive.' }),
      h('span', { class: 'library-buttons' }, button('Check now'), button('Download'), button('Install', true))));

    const pickers = h('p', { class: 'setting-desc' },
      'The theme picker is the one at the top right of this page: hub.js draws it into the page\'s one .theme-picker. The name colour swatch and the emoji button for names are the home page\'s (ident.js and emoji.js). The emoji button is on the message field above.');

    return group('More from the pages', 'Controls the pages draw with their own markup or admin.js\'s helpers, beyond the shared widgets above.',
      row('fields', 'Form fields', 'Each with its label around it, as the forms on Accounts, Kiwix and Drop. The emoji button joins any field marked data-emoji.',
        '<form class="library-form"><label>Name <input …></label>…; <label class="setting"><input type="checkbox"> …; data-emoji, EMOJI.attach(field)', fields),
      row('colour', 'Colour', 'A colour, or the theme\'s own. A warning shows beside a colour that will be hard to read.',
        "AW.settings([{key, label, kind: 'colour', value, shown, unset, warn}])", colour),
      row('progress', 'Progress, output and space used', 'A job\'s progress, what its program printed, and how full something is.',
        '<div class="admin-progress"><progress class="admin-bar">; <details class="admin-output"><pre class="admin-log">; <div class="admin-meter"><div style="width: …">', progress),
      row('tabs-aria', 'Tabs with their panels', 'Parts of one page that each fill it: one showing, arrow keys move along, the choice kept.',
        'role="tablist" data-tabs, buttons role="tab" aria-controls, panels role="tabpanel"; initTabs(box) (admin.js)', tabbed),
      row('table', 'A table', 'Rows of facts with a heading per column. It scrolls sideways on a phone.',
        '<div class="scroll-x"><table class="admin-table"><caption>…', table),
      row('empty', 'An empty list', 'What a list says when it has nothing in it.',
        '<span class="empty"> (the shoutbox, the board); <p class="setting-desc">None yet.</p> (admin lists)', empty),
      row('app-page', 'An app\'s page: its facts and its Access', 'What an app is and how it stands, then who can open it.',
        '<p class="inv-facts"><span class="inv-fact">…; chipRow(label, OPEN_CHIPS, value, set) and AW.section, as accessBlock(id, app) (admin.js)', facts, accessBox),
      row('home-tiles', 'The home page\'s tiles', 'An app\'s tile, one not running (faded, or in High contrast a pill), one locked, one only administrators see, and two live tiles.',
        'server.py: the tile HTML (class="service-card", .down, .locked, .tile-pill) and WIDGET_HTML (people, system)', tiles),
      row('setup-steps', 'The setup steps', 'Each step\'s name, how it stands, what it is now, and its settings.',
        'admin-tour.js: drawList() (li.step-ok, .step-todo, .step-warn, .step-muted; .link-button.mute-step)', steps),
      row('go-link', 'A link to where the reason is', 'A failure says itself, with the way to its diagnosis beside it.',
        '<a class="action-btn go-btn" href="#updoctor">Show reason: Updates doctor →</a> (admin.js)', go),
      row('update-card', 'A package\'s card', 'One thing kept up to date: what is installed, what is newer, and the three buttons.',
        'pkgCard(id, pkg) (admin.js): .setting.library-source.update-card, AW.updatePill, actionButton', updateCard),
      row('pickers', 'The theme picker and the name colour', 'Drawn once per page by their own scripts.',
        'hub.js (.theme-picker); ident.js attachHuePicker(swatch, nameInput) (home page)', pickers));
  }

  // While this page is open, a frame across the top of the window holds the page's theme picker (as the app bar does
  // on an app's page), so the theme and colours can be changed beside any control; it goes back to the header after.
  const frame = h('div', { class: 'app-bar mock-frame', role: 'region', 'aria-label': 'Theme for this page' },
    h('span', { class: 'app-title', text: 'Mock controls' }), h('span', { class: 'mock-frame-note', text: 'Theme and colours, for every control below' }));
  const picker = document.querySelector('header .theme-picker');
  const home = picker && { parent: picker.parentNode, next: picker.nextSibling };
  function placePicker() {
    if (!picker) return;
    const open = AL.shown(ID);
    if (open && picker.parentNode !== frame) {
      document.body.prepend(frame);
      frame.append(picker);
      document.body.classList.add('mock-framed');
    } else if (!open && picker.parentNode === frame) {
      home.parent.insertBefore(picker, home.next && home.next.parentNode === home.parent ? home.next : null);
      frame.remove();
      document.body.classList.remove('mock-framed');
    }
  }

  let drawn = false;
  function draw() {
    if (drawn || !AL.shown(ID)) return;
    const box = document.getElementById('mock-controls-body');
    if (!box) return;
    drawn = true;
    box.replaceChildren(fromTheMock(), sinceTheMock(), cardsAndFindings(), fromThePages());
  }
  window.addEventListener('hashchange', () => setTimeout(() => { draw(); placePicker(); }));
  AL.onBuild(() => { draw(); placePicker(); });
  draw();
  placePicker();
})();
