// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin → Mock controls: every control the admin pages use, drawn by the same calls the pages make, with stand-in
// data and nothing sent. Two uses: the example to copy when a page needs a control, and the place to pick one from.
// The list follows the menu mock's Controls view (the 2026-10-07 menu overhaul), then the controls the admin has
// had since, then the cards and findings the doctors draw. The mock itself is out of date: none of its code is here.
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
      h('p', { class: 'mock-code' }, h('code', { text: code })));
  }
  const group = (title, intro, ...rows) => h('div', { class: 'mock-group' }, h('h3', { class: 'mock-group-title', text: title }),
    h('p', { class: 'setting-desc', text: intro }), ...rows);
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
    const hours = Array.from({ length: 72 }, (_, index) => (index > 60 && index < 64 ? null : { up: index === 20 ? 0 : index % 17 === 0 ? 0.93 : 1, n: 60, drops: index % 17 === 0 ? 1 : 0 }));
    const uptime = typeof Heatmap === 'undefined' ? h('p', { class: 'setting-desc', text: 'The heatmap script is not loaded.' })
      : h('div', {}, Heatmap.grid({ caption: 'A stand-in service, the last 72 hours by hour', cols: hours, rows: [{ label: 'Stand-in', cells: hours, where: (index) => `${72 - index} hours ago` }] }), Heatmap.legend());

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
      row('toggle', 'Toggle', 'On or off. The button says which. The admin puts its name beside it, not in it.',
        "AW.settings([{key, label, kind: 'toggle', value}])",
        AW.settings([{ key: 'keep', label: 'Keep current', kind: 'toggle', value: true }])),
      row('tabs', 'Joined tabs', 'Views of the same thing: the data stays, the range changes.',
        'class="view-switch" with buttons class="view-switch-btn" (Overview → Memory and disk history)', tabs),
      row('pills', 'Pills', 'A state, one meaning each. The word sets the colour unless told.',
        "AW.pill(word[, tone]); AW.updatePill(checked, available, failed)",
        h('div', { class: 'chip-group' }, AW.pill('ok'), AW.pill('queued'), AW.pill('failed'), AW.pill('info', 'info'),
          AW.updatePill(Date.now() / 1000, false), AW.updatePill(0, true), AW.updatePill(0, false, true))),
      row('help', 'Help that expands', 'The longer explanation, next to the control it explains.',
        '<details class="field-help"><summary>…</summary>…</details>',
        h('details', { class: 'field-help' }, h('summary', { text: 'What does this do?' }), h('p', { class: 'setting-desc', text: 'The longer explanation. It stays closed until asked for.' }))),
      row('fold', 'A fold', 'A part to open when wanted (history, advanced options). The admin folds with <details>; the mock\'s button that changes its own words is not used.',
        '<details><summary>…</summary>…</details>',
        h('details', {}, h('summary', { text: 'Memory and disk history' }), h('p', { class: 'setting-desc', text: 'What the fold holds.' }))),
      row('uptime', 'Uptime', 'Hours or days as cells, coloured by how much of each was up, said in a tooltip and to a screen reader.',
        'Heatmap.grid({caption, cols, rows: [{label, cells, where}]}); Heatmap.legend()', uptime),
      row('short-list', 'Short entries, with a filter', 'A long but limited list: one line each, the details open from the line. More than AW.FILTER_AT entries get a filter.',
        'AW.shortList([{id, title, summary, badges, detail}])', AW.shortList(entries, { id: 'mock-entries' })),
      row('cards', 'Cards with a drawer', 'Things to manage one at a time: the drawer opens inside the card. A drawer with settings has Save and Discard.',
        'AW.cards([{id, title, summary, badges}], {body(item, setDirty), save(item)})',
        AW.cards(cardItems, { id: 'mock-cards', save: () => pretendSave(), body: (item, setDirty) => {
          const toggle = h('button', { type: 'button', class: 'chip-btn', 'aria-pressed': 'false', onclick: (event) => {
            const on = event.currentTarget.getAttribute('aria-pressed') !== 'true';
            event.currentTarget.setAttribute('aria-pressed', String(on));
            event.currentTarget.classList.toggle('active', on);
            event.currentTarget.textContent = on ? 'On' : 'Off';
            setDirty(on);
          } }, 'Off');
          return [AW.dl({ Name: item.title, State: item.summary }), h('div', { class: 'aw-field' }, h('span', { class: 'aw-label', text: 'Start at boot' }), toggle)];
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

    const fewSelect = h('label', {}, 'Check interval ', h('select', { name: 'mock-few' }, ['Manual', '6 hours', '24 hours', 'Weekly'].map((label) => h('option', { value: label }, label))));

    const pattern = h('div', {}, AW.updatePattern({ often: 'mock-often', act: 'mock-act', value: { often: '24', act: '1' } }),
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
      row('select-chips', 'A drop-down of a few, as chips', 'Every <select> of up to AW.CHIPS_AT choices is drawn as chips; the select stays underneath for the form.',
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

  let drawn = false;
  function draw() {
    if (drawn || !AL.shown(ID)) return;
    const box = document.getElementById('mock-controls-body');
    if (!box) return;
    drawn = true;
    box.replaceChildren(fromTheMock(), sinceTheMock(), cardsAndFindings());
  }
  window.addEventListener('hashchange', () => setTimeout(draw));
  AL.onBuild(draw);
  draw();
})();
