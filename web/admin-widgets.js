// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin's shared widgets: one way to show a short
// list, a list of records, a grid of cards, a block of settings and a list of findings, so every
// pane behaves the same. Plain script, no build step: it defines AW for admin.js and the sections.
//
// The rules they keep:
//   3a a card's drawer opens inside the card, which widens to the row
//   3b the title folds it again (a card's head, an entry's ▸/▾ line); no separate collapse button
//   3c an opened card has a ✕ (Esc does the same)
//   3d a long but limited list shows one line per entry; the rest opens from the line
//   3e a list of records has a limited height and scrolls
//   3f a list of more than FILTER_AT entries has a filter: a search box and chips of its states
// And one open at a time in a pane; settings wait for Save (Discard puts them back), jobs act at once.
const AW = (() => {
  'use strict';
  const FILTER_AT = 12;   // 3f: more than this many entries get a filter
  const BOUND_AT = 20;    // a short list longer than this scrolls in its own box

  // Messages from the box (irate_box/msg.py): {id, en, args}, shown in the reader's language. Only English
  // exists yet: a catalogue for another language is {id: {text}} under the same IDs, and falls back to the English.
  // A plural is a set of whole sentences by CLDR category, selected by the argument n; a list argument is joined
  // for the language; {time} is a moment said as people say it, {seconds} a length of time in a unit that suits it.
  // Placeholders are named: {reason}.
  const LANG = 'en-GB';
  const CATALOGUE = {};
  const isMessage = (v) => !!v && typeof v === 'object' && typeof v.id === 'string' && 'en' in v;
  // A length of time: in the largest unit that holds it whole ("5 minutes", "36 hours"), or else rounded in the unit
  // its size suits ("67 minutes", "1.5 hours"). msg.py's duration_unit picks the same, for the terminal.
  const UNITS = [[86400, 'day', 172800, 1], [3600, 'hour', 5400, 1], [60, 'minute', 90, 0], [1, 'second', 0, 0]];
  function durationUnit(seconds) {
    const absolute = Math.abs(Number(seconds) || 0);
    const whole = UNITS.find(([size]) => absolute >= size && absolute % size === 0 && absolute / size < 100);
    if (whole) return [absolute / whole[0], whole[1]];
    const [size, unit, , places] = UNITS.find((row) => absolute >= row[2]);
    return [Math.floor((absolute / size) * 10 ** places + 0.5) / 10 ** places, unit];
  }
  function sayArg(v) {
    if (isMessage(v)) return say(v);
    if (Array.isArray(v)) return new Intl.ListFormat(LANG, { type: 'conjunction' }).format(v.map(sayArg));
    if (v && typeof v === 'object' && 'time' in v) {
      return new Date(v.time * 1000).toLocaleString(LANG, { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
    }
    // Several messages as one result (msg.sentences): said one after the other.
    if (v && typeof v === 'object' && Array.isArray(v.sentences)) return v.sentences.map(sayArg).filter(Boolean).join(' ');
    if (v && typeof v === 'object' && 'seconds' in v) {
      const [value, unit] = durationUnit(v.seconds);
      return new Intl.NumberFormat(LANG, { style: 'unit', unit, unitDisplay: 'long', maximumFractionDigits: 1 }).format(value);
    }
    return v == null ? '' : String(v);
  }
  function say(m) {
    if (!isMessage(m)) return sayArg(m);   // a plain string: from code not converted yet, or an add-on's own words
    const args = m.args || {};
    let text = (CATALOGUE[m.id] && CATALOGUE[m.id].text) || m.en;
    if (text && typeof text === 'object') text = text[new Intl.PluralRules(LANG).select(Number(args.n))] || text.other;
    return String(text).replace(/\{([a-z_][a-z0-9_]*)\}/g, (whole, k) => (k in args ? sayArg(args[k]) : whole));
  }
  // A report from the box with every message in it said: the panes then draw plain strings.
  function localise(v) {
    if (isMessage(v)) return say(v);
    if (Array.isArray(v)) return v.map(localise);
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, localise(x)]));
    return v;
  }

  // An element: h('div', {class, text, onclick, 'aria-x': …}, ...children). Children may nest in arrays.
  function h(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return node;
  }
  const btn = (label, props = {}) => h('button', { type: 'button', class: 'action-btn', ...props }, label);

  // A state, one meaning each: ok, warn, bad or info, from the word unless told.
  const BAD = /red|fail|down|stopped|invalid|reported|problem/i, WARN = /amber|update|behind|flagged|queued|planned|working|ready|warn|^not /i,
    OK = /^ok$|built|installed|cached|stable|running|kept|on$/i;
  function pill(text, tone) {
    const t = tone || (BAD.test(text) ? 'bad' : WARN.test(text) ? 'warn' : OK.test(text) ? 'ok' : 'info');
    return h('span', { class: t + '-pill', text });
  }
  // What a check for updates found, as one badge wherever updates are checked:
  // "update available" when one is; "up to date" only when that was found within RECENT_DAYS; an
  // older look says how old it is, as nothing newer it found then still holds.
  // checked: when it last looked, epoch seconds or an ISO string; available: whether it found one;
  // failed: its last look failed, so it found nothing either way.
  const RECENT_DAYS = 7;
  function updatePill(checked, available, failed) {
    if (available) return h('span', { class: 'warn-pill update-pill', text: 'update available' });
    if (failed) return h('span', { class: 'bad-pill update-pill', text: 'check failed' });
    const at = typeof checked === 'string' ? Date.parse(checked) / 1000 : checked;
    if (!at) return h('span', { class: 'info-pill update-pill', text: 'not checked yet' });
    const days = Math.floor((Date.now() / 1000 - at) / 86400);
    return days < RECENT_DAYS ? h('span', { class: 'ok-pill update-pill', text: 'up to date', title: 'Checked within the last week. Nothing newer found.' })
      : h('span', { class: 'info-pill update-pill', text: `checked ${days} days ago`, title: 'Nothing newer was found then. Check again to be sure.' });
  }
  // ---- One pattern for every update: how often to look, and how far to take what it finds. The same two choices, the
  // same words and the same three buttons wherever something is updated: Debian's security updates, the
  // hub's own, apps and books, packages, the toolkits' cache, the mirrors. Values are the librarian's:
  // hours (0 Manual) and 0 Flag / 1 Download / 2 Install. Selects underneath, so forms read them and
  // chipSelect draws them as chips.
  const OFTEN = [['0', 'Manual', 'The box checks only when you select Check now. It then does what the setting below says.'],
    ['6', '6 hours', 'The box checks every 6 hours.'], ['24', '24 hours', 'The box checks once a day.'], ['168', 'Weekly', 'The box checks once a week.']];
  const ACT = [['0', 'Flag', 'The box shows that a newer version exists, here and on the badge. It downloads nothing.'],
    ['1', 'Download', 'The box shows that a newer version exists. It downloads and checks the version. You install it.'],
    ['2', 'Install', 'The box shows that a newer version exists. It downloads, checks and installs the version.']];
  const PATTERN_LABELS = { often: 'Check interval', act: 'When something newer is found' };
  // The saved pattern in one sentence, for a folded policy's title: what the box will actually do.
  const OFTEN_SAYS = { 0: 'Only when you select Check now', 6: 'Checks every 6 hours', 24: 'Checks once a day', 168: 'Checks once a week' };
  const ACT_SAYS = { 0: 'Flags what is newer', 1: 'Downloads it. You install it', 2: 'Installs it' };
  function patternSays(often, act) {
    const o = Number(often), actLevel = Number(act);
    return o ? `${OFTEN_SAYS[o] || `Checks every ${o} hours`}. ${ACT_SAYS[actLevel] || ''}.` : `${OFTEN_SAYS[0]}.`;
  }
  //   opts: {often: name, act: name, acts: ['0','1'] (a subset where fetching is the update), says: {'2': …},
  //          oftenSays: {'0': …}, value: {often, act}, why: a line saying why a choice is missing}
  function updatePattern(opts) {
    const sel = (name, rows, says, value, label) => h('label', { class: 'update-choice' }, label,
      h('select', { name, 'data-pattern': name === opts.often ? 'often' : 'act' }, rows.map(([v, t, row]) => {
        const o = h('option', { value: v, 'data-says': (says && says[v]) || row }, t);
        if (String(value) === v) o.selected = true;
        return o;
      })));
    const acts = ACT.filter(([v]) => !opts.acts || opts.acts.map(String).includes(v));
    return h('div', { class: 'update-pattern' },
      sel(opts.often, OFTEN, opts.oftenSays, opts.value && opts.value.often, PATTERN_LABELS.often),
      sel(opts.act, acts, opts.says, opts.value && opts.value.act, PATTERN_LABELS.act),
      opts.why ? h('p', { class: 'setting-desc', text: opts.why }) : null);
  }
  // A placeholder in the page, <div data-update-pattern data-often="name" data-act="name" data-acts="0,1"
  // data-says-2="…" data-why="…">, filled with the pattern (before its form's code reads the selects).
  function updatePatterns(root) {
    (root || document).querySelectorAll('[data-update-pattern]:not([data-filled])').forEach((box) => {
      const says = {}, oftenSays = {};
      for (const [k, v] of Object.entries(box.dataset)) {
        if (/^says-?\d$/.test(k)) says[k.replace(/^says-?/, '')] = v;
        if (/^oftenSays-?\d+$/.test(k)) oftenSays[k.replace(/^oftenSays-?/, '')] = v;
      }
      box.dataset.filled = '1';
      box.replaceWith(updatePattern({ often: box.dataset.often, act: box.dataset.act, acts: box.dataset.acts ? box.dataset.acts.split(',') : null,
        says, oftenSays, why: box.dataset.why || null }));
    });
  }
  // The three buttons beside them: Check now, Download (greyed with why when nothing newer is known or it is
  // fetched already), Install (fetching first if needed). Each: {onclick, disabled, why, label}; null leaves
  // one out (Install where fetching is the update). The badge (updatePill) goes first.
  function updateButtons(b) {
    const row = h('div', { class: 'library-buttons update-buttons' });
    const why = h('p', { class: 'setting-desc update-why', hidden: true });
    row.update = (state) => {
      const whys = [];
      for (const [k, label] of [['check', 'Check now'], ['fetch', 'Download'], ['install', 'Install']]) {
        const shown = { ...b[k], ...(state || {})[k] }, node = row.querySelector(`[data-update="${k}"]`);
        if (!node) continue;
        if (shown.label) node.textContent = shown.label;
        node.disabled = !!shown.disabled;
        node.title = shown.disabled && shown.why ? shown.why : '';
        if (shown.disabled && shown.why) whys.push({ label: shown.label || label, why: shown.why });
      }
      // One reason for every greyed button (the root helper busy, say) is said once, as a sentence; reasons of
      // their own, each after its button's name.
      const reasons = [...new Set(whys.map((w) => w.why))];
      why.textContent = whys.length > 1 && reasons.length === 1
        ? reasons[0].charAt(0).toUpperCase() + reasons[0].slice(1)
        : whys.map((w) => `${w.label}: ${w.why}`).join(' ');
      why.hidden = !whys.length;
    };
    for (const [k, label] of [['check', 'Check now'], ['fetch', 'Download'], ['install', 'Install']]) {
      if (!b[k]) continue;
      row.append(btn(b[k].label || label, { 'data-update': k, class: 'action-btn' + (k === 'install' ? ' primary' : ''), onclick: b[k].onclick }));
    }
    if (b.badge) row.prepend(b.badge);
    row.update();
    const box = h('div', { class: 'update-actions' }, row, why);
    box.update = row.update;
    return box;
  }
  const scopeOf = (node) => node.closest('.admin-pane, [data-aw-scope]') || document.body;

  // ---- A filter bar (3f) over any list whose rows carry data-text and data-tags ----
  function filterBar(list, rowSel, items) {
    const tags = [...new Set(items.flatMap((i) => i.badges || []))].slice(0, 8);
    const active = new Set();
    const count = h('span', { class: 'aw-count' });
    const q = h('input', { type: 'search', placeholder: 'Filter…', 'aria-label': 'Filter this list' });
    const apply = () => {
      const needle = q.value.trim().toLowerCase();
      let shown = 0;
      list.querySelectorAll(rowSel).forEach((row) => {
        const ok = (!needle || row.dataset.text.includes(needle))
          && (!active.size || [...active].some((t) => row.dataset.tags.split('|').includes(t)));
        row.hidden = !ok;
        if (ok) shown++;
      });
      count.textContent = `${shown} of ${items.length}`;
    };
    q.addEventListener('input', apply);
    const chips = tags.map((t) => h('button', { type: 'button', class: 'filter-chip', 'aria-pressed': 'false', onclick: (event) => {
      if (active.has(t)) active.delete(t); else active.add(t);
      event.currentTarget.classList.toggle('active', active.has(t));
      event.currentTarget.setAttribute('aria-pressed', String(active.has(t)));
      apply();
    } }, t));
    const bar = h('div', { class: 'aw-filter', 'data-filter-for': list.id }, q, chips.length ? h('div', { class: 'filter-chips' }, chips) : null, count);
    bar.apply = apply;
    count.textContent = `${items.length} of ${items.length}`;
    return bar;
  }
  const rowData = (i) => ({ 'data-text': `${i.title} ${i.summary || ''}`.toLowerCase(), 'data-tags': (i.badges || []).join('|') });
  let serial = 0;
  const listId = (opts, kind) => opts.id || `aw-${kind}-${++serial}`;

  // ---- One open at a time (in a pane): entries and cards alike ----
  // A card with unsaved changes asks before it closes; kept, it stays open and the other does not open (false).
  function closeOthers(scope, keep) {
    const kept = [...scope.querySelectorAll('.aw-card.open')].filter((c) => c !== keep && !closeCard(c));
    scope.querySelectorAll('.aw-row-head[aria-expanded="true"]').forEach((hd) => hd !== keep && foldRow(hd, false));
    return !kept.length;
  }
  function foldRow(head, open) {
    const detail = head.nextElementSibling;
    if (open && AW.single) closeOthers(scopeOf(head), head);
    detail.hidden = !open;
    head.setAttribute('aria-expanded', String(open));
    if (open && head._fill && !detail.dataset.filled) { detail.dataset.filled = '1'; detail.append(...[head._fill()].flat(Infinity).filter(Boolean)); }
  }

  // ---- A short-entry list (3d): one line each; the details open from the line ----
  //   items: [{id, title, summary, badges, detail: {Label: value} | () => nodes}]
  //   opts:  {id, empty}
  function shortList(items, opts = {}) {
    const id = listId(opts, 'list');
    const list = h('div', { class: 'aw-list' + (items.length > BOUND_AT ? ' bounded' : ''), id, 'data-list': 'limited', 'data-count': items.length });
    for (const it of items) {
      const detail = h('div', { class: 'aw-row-detail', hidden: true });
      const head = h('button', { type: 'button', class: 'aw-row-head', 'aria-expanded': 'false', 'data-disclosure': '',
        onclick: () => foldRow(head, detail.hidden) },
        h('span', { class: 't', text: it.title }), h('span', { class: 's', text: it.summary || '' }), h('span', { class: 'p' }, (it.badges || []).map((b) => pill(b))));
      head._fill = () => (typeof it.detail === 'function' ? it.detail(it) : dl(it.detail));
      list.append(h('div', { class: 'aw-row', 'data-aw-id': it.id, ...rowData(it) }, head, detail));
    }
    if (!items.length && opts.empty) list.append(h('p', { class: 'setting-desc', text: opts.empty }));
    return h('div', { class: 'aw-block' }, items.length > FILTER_AT ? filterBar(list, '.aw-row', items) : null, list);
  }
  const dl = (obj) => obj ? h('dl', { class: 'aw-dl' }, Object.entries(obj).map(([k, v]) => [h('dt', { text: k }), h('dd', { text: String(v) })])) : null;

  // ---- Records (3e): newest first, a limited height and a scroll bar ----
  //   items: [{id, title}]   opts: {id, noun}
  function records(items, opts = {}) {
    const id = listId(opts, 'records');
    // It scrolls, so it takes the keyboard's focus (the arrow keys scroll it) and is named for that.
    const list = h('div', { class: 'aw-records', id, 'data-list': 'records', 'data-count': items.length, tabindex: '0', role: 'region', 'aria-label': `${items.length} ${opts.noun || 'records'}` },
      items.map((i) => h('div', { 'data-aw-id': i.id, ...rowData(i), text: i.title })));
    return h('div', { class: 'aw-block' }, h('p', { class: 'aw-records-head', text: `${items.length} ${opts.noun || 'records'}, newest first` }),
      items.length > FILTER_AT ? filterBar(list, 'div[data-aw-id]', items) : null, list);
  }

  // ---- Cards (3a–3c): the drawer opens inside the card, which widens to the row ----
  //   items: [{id, title, summary, badges}]
  //   opts:  {id, body(item, setDirty) → nodes, save(item) → Promise|void (false: not saved, said already)}
  //   A drawer with settings (setDirty called) is a section: Save and Discard; Discard rebuilds the body.
  function cards(items, opts = {}) {
    const id = listId(opts, 'cards');
    const grid = h('div', { class: 'aw-cards', id, 'data-list': 'cards', 'data-count': items.length });
    for (const it of items) {
      const card = h('div', { class: 'aw-card', 'data-aw-id': it.id, 'data-card': '', ...rowData(it) });
      const head = h('button', { type: 'button', class: 'aw-card-head', 'aria-expanded': 'false', 'data-disclosure': '',
        onclick: () => (card._drawer ? closeCard(card) : openCard(card, it, opts)) },
        h('span', { class: 't', text: it.title }), h('span', { class: 's', text: it.summary || '' }), h('span', { class: 'p' }, (it.badges || []).map((b) => pill(b))));
      card.append(head);
      grid.append(card);
    }
    return h('div', { class: 'aw-block' }, items.length > FILTER_AT ? filterBar(grid, '.aw-card', items) : null, grid);
  }
  const openStack = [];
  function openCard(card, it, opts) {
    if (AW.single && !closeOthers(scopeOf(card), card)) return null;
    // The drawer's settings are a section: the body says whether it differs from what is saved (setDirty), the
    // section gives the footer, Saving…, the reason on failure and the unsaved list. Discard draws the body again.
    const body = h('div', { class: 'aw-drawer-body' });
    const fill = () => { body.replaceChildren(...[opts.body ? opts.body(it, setDirty) : dl(it.detail)].flat(Infinity).filter(Boolean)); };
    const sec = section({
      id: `card-${card.closest('[data-list="cards"]') ? card.closest('[data-list="cards"]').id : 'cards'}-${it.id}`,
      label: it.title,
      saved: { changed: false },
      fresh: true,
      idle: 'Changes apply after you select Save. Actions run immediately.',
      save: async () => {
        const saved = opts.save ? await opts.save(it) : true;
        if (saved !== false) setTimeout(() => { sec.update({ changed: false }); });
        return saved;
      },
      onChange: () => { if (!sec.dirty() && !sec.saving()) fill(); },
    });
    function setDirty(dirty = true) { sec.set('changed', !!dirty, true); sec.foot.hidden = false; }
    sec.foot.hidden = true;   // shown once the body has settings (setDirty called)
    const drawer = h('div', { class: 'aw-drawer', role: 'region', 'aria-label': it.title },
      btn('✕', { class: 'action-btn aw-close', 'data-act': 'close', 'aria-label': 'Close', title: 'Close (Esc)', onclick: () => closeCard(card) }),
      body, sec.foot, sec.reason);
    fill();
    card._drawer = drawer;
    card._dirty = () => sec.dirty();
    card._saving = () => sec.saving();
    card.classList.add('open');
    card.querySelector('.aw-card-head').setAttribute('aria-expanded', 'true');
    card.append(drawer);
    openStack.push(card);
    return drawer;
  }
  function closeCard(card, force) {
    if (!card._drawer) return true;
    // A drawer being saved closes without asking: its save goes on.
    if (!force && card._dirty() && !card._saving() && !window.confirm('Discard the unsaved changes?')) return false;
    card._drawer.remove();
    card._drawer = null;
    card.classList.remove('open');
    card.querySelector('.aw-card-head').setAttribute('aria-expanded', 'false');
    const i = openStack.indexOf(card);
    if (i >= 0) openStack.splice(i, 1);
    return true;
  }
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && openStack.length) closeCard(openStack[openStack.length - 1]); });

  // ---- A section: a group of settings saved together (a card, a block, one network, one app's access) ----
  // Every section keeps the same rules (the save-patterns plan):
  //   1 its draft is compared with the saved values: Save and Discard are off while nothing differs, "Unsaved
  //     changes" shows only while something does; until the box's values are read (saved null) it says so,
  //     its controls are off and nothing counts as changed
  //   2 one footer at its end: Discard, then Save (the primary); the words are always Save and Discard
  //   3 no question at Save: a setting is saved as set. What a change costs or conflicts with is a short warning in
  //     the footer while it is not saved (opts.warn; Tom, 2026-10-21: "settings, not questions")
  //   4 "Saving…" until the save resolves (a queued job: until the root helper answers, AW.job); the draft
  //     cleared only on success; on failure it stays, with the reason under the footer
  //   5 the page's polling (update) never overwrites a changed value; a saved value changed underneath one
  //     ("Changed elsewhere") is said, and the draft kept
  //   6 every section with unsaved changes is listed (AW.unsaved), for the sidebar's mark and leaving the page
  // opts: {id, label, saved: {key: value} | null, save(changes, draft) → Promise (false: not saved, said already;
  //        a string: said when saved; throws: the reason), warn(changes, draft) → a warning (a statement) or null,
  //        idle: the footer's words while nothing has changed, onChange(section): draw the controls again,
  //        busy() → true while a save of it is in flight elsewhere (a copy drawn again while its job runs),
  //        why(changes, draft) → why these changes cannot be saved as they are (Save off, the reason under it),
  //        fresh: true starts from the saved values, not a draft left by an earlier copy (a card opened again)}
  const same = (left, right) => left === right || (left !== null && right !== null && typeof left === 'object' && typeof right === 'object' && JSON.stringify(left) === JSON.stringify(right));
  const copy = (v) => (v && typeof v === 'object' ? JSON.parse(JSON.stringify(v)) : v);
  const sections = new Map();   // id -> the section last made with it (its draft outlives a redraw)
  const IDLE = 'Changes apply after you select Save.';
  let unsavedWas = '';
  function tellUnsaved() {
    const now = unsaved().map((x) => x.id).join('|');
    if (now === unsavedWas || typeof document === 'undefined') return;
    unsavedWas = now;
    document.dispatchEvent(new CustomEvent('aw-unsaved'));
  }
  // A question drawn on the page, not the browser's pop-up (the admin pages plan, phase 5): the words, then
  // Confirm and Cancel, beside what was pressed (anchor, else the focused control). Resolves true or false.
  // One at a time: a new one answers the last one No. Cancel has the focus; Escape cancels.
  let asked = null;
  function confirmHere(text, opts = {}) {
    if (asked) asked(false);
    const at = opts.anchor || (document.activeElement !== document.body ? document.activeElement : null);
    return new Promise((resolve) => {
      const box = h('div', { class: 'aw-ask', role: 'alertdialog', 'aria-label': opts.label || 'Confirm' });
      const answer = (yes) => {
        if (asked !== answer) return;
        asked = null;
        box.remove();
        resolve(yes);
      };
      asked = answer;
      const no = btn('Cancel', { class: 'action-btn', onclick: () => answer(false) });
      box.append(h('p', { class: 'setting-desc', text }),
        h('span', { class: 'library-buttons' }, btn(opts.label || 'Confirm', { class: 'action-btn primary', onclick: () => answer(true) }), no));
      box.addEventListener('keydown', (event) => { if (event.key === 'Escape') answer(false); });
      const place = at && at.isConnected ? at.closest('.library-buttons, .aw-foot, .setting-actions') || at : null;
      if (place && place.parentNode) place.after(box);
      else document.querySelector('.admin-main, main, body').prepend(box);
      no.focus();
    });
  }
  function section(opts = {}) {
    const before = opts.id && !opts.fresh ? sections.get(opts.id) : null;
    let saved = opts.saved == null ? null : copy(opts.saved);
    let draft = saved ? copy(saved) : {};
    const elsewhere = new Set();
    let saving = false, reason = '', said = '';
    const handle = { id: opts.id || null, label: opts.label || '', opts };
    // A redraw of the same section: its unsaved changes carried over, as the page's polling would (rule 5).
    if (before && before.dirty() && saved) {
      for (const [k, v] of Object.entries(before.changes())) {
        if (!(k in saved)) continue;
        draft[k] = copy(v);
        if (!same(before.savedValue(k), saved[k])) elsewhere.add(k);
      }
      for (const k of before.elsewhereKeys()) if (k in saved && !same(draft[k], saved[k])) elsewhere.add(k);
    }
    const saveB = btn('Save', { class: 'action-btn primary aw-save', disabled: true });
    const discardB = btn('Discard', { class: 'action-btn aw-discard', disabled: true });
    const note = h('span', { class: 'note', role: 'status' });
    const warnEl = h('span', { class: 'setting-desc aw-warn', hidden: true });
    const why = h('p', { class: 'setting-desc aw-reason', role: 'status', hidden: true });
    handle.foot = h('div', { class: 'aw-foot', 'data-section': opts.id || '' }, warnEl, note, discardB, saveB);
    handle.reason = why;
    handle.loaded = () => saved !== null;
    handle.saving = () => saving || !!(opts.busy && opts.busy());
    handle.get = (k) => draft[k];
    handle.savedValue = (k) => (saved ? saved[k] : undefined);
    handle.values = () => copy(draft);
    handle.changes = () => (saved ? Object.fromEntries(Object.keys(draft).filter((k) => k in saved && !same(draft[k], saved[k])).map((k) => [k, copy(draft[k])])) : {});
    handle.dirty = () => Object.keys(handle.changes()).length > 0;
    handle.elsewhereKeys = () => [...elsewhere];
    handle.disabled = () => !saved || handle.saving();
    handle.attached = () => handle.foot.isConnected;
    handle.page = () => { const page = handle.foot.closest && handle.foot.closest('.admin-pane'); return page ? page.id : ''; };
    const paint = () => {
      const dirty = handle.dirty(), busy = handle.saving();
      const cannot = saved && dirty && !busy && opts.why ? opts.why(handle.changes(), handle.values()) || '' : '';
      saveB.disabled = !dirty || busy || !saved || !!cannot;
      discardB.disabled = !dirty || busy || !saved;
      handle.foot.classList.toggle('aw-dirty', dirty);
      saveB.classList.toggle('idle', !dirty);   // outlined while there is nothing to save
      note.textContent = !saved ? 'Reading the current settings…' : busy ? 'Saving…'
        : dirty ? (elsewhere.size ? 'Unsaved changes. Changed elsewhere since you started: Save replaces it, Discard shows it.' : 'Unsaved changes.')
          : said || opts.idle || IDLE;
      why.textContent = reason || cannot;
      why.hidden = !(reason || cannot);
      const warning = saved && dirty && opts.warn ? opts.warn(handle.changes(), handle.values()) || '' : '';
      warnEl.textContent = warning;
      warnEl.hidden = !warning;
      tellUnsaved();
    };
    const redraw = () => { paint(); if (opts.onChange) opts.onChange(handle); };
    // quiet: the control drew itself (a field being typed in): only the footer follows.
    handle.set = (k, v, quiet) => {
      if (!saved || saving) return;
      draft[k] = copy(v);
      if (same(draft[k], saved[k])) elsewhere.delete(k);
      said = '';
      if (quiet) paint(); else redraw();
    };
    // The page's polling: the saved values as they are now. A value not changed here follows them; a changed one
    // stays, said as changed elsewhere when the saved value moved under it.
    handle.update = (now) => {
      if (now == null) return;
      now = copy(now);
      if (!saved || saving) { if (!saving) { saved = now; draft = copy(now); redraw(); } return; }
      for (const k of Object.keys(now)) {
        const mine = k in draft && !same(draft[k], saved[k]);
        if (!mine) draft[k] = copy(now[k]);
        else if (!same(saved[k], now[k])) elsewhere.add(k);
        if (same(draft[k], now[k])) elsewhere.delete(k);
      }
      saved = now;
      redraw();
    };
    handle.discard = () => {
      if (!saved || saving) return;
      draft = copy(saved);
      elsewhere.clear();
      reason = '';
      redraw();
    };
    handle.save = async () => {
      if (!saved || handle.saving() || !handle.dirty()) return false;
      const changes = handle.changes();
      if (opts.why && opts.why(changes, handle.values())) return false;
      saving = true;
      reason = '';
      said = '';
      paint();
      let outcome;
      try {
        outcome = opts.save ? await opts.save(changes, handle.values()) : true;
      } catch (error) {
        outcome = false;
        reason = String((error && error.message) || error || 'Not saved.');
      }
      saving = false;
      if (outcome !== false) {
        Object.assign(saved, copy(changes));
        elsewhere.clear();
        said = typeof outcome === 'string' ? outcome : '';
      }
      redraw();
      return outcome !== false;
    };
    handle.fail = (text) => { reason = text || ''; paint(); };
    saveB.addEventListener('click', () => handle.save());
    discardB.addEventListener('click', () => handle.discard());
    if (opts.id) sections.set(opts.id, handle);
    paint();
    return handle;
  }
  // The sections with unsaved changes on the page now: [{id, label, page, el}] (el: its footer, to find where it is).
  function unsaved() {
    return [...sections.values()].filter((x) => x.attached() && x.dirty()).map((x) => ({ id: x.id, label: x.label, page: x.page(), el: x.foot }));
  }
  // A job the root helper runs: a promise a section's save returns, settled when the page's poll sees the answer.
  //   const j = AW.job(); … j.done(ok, why) — ok false rejects with why (the reason under the footer).
  function job() {
    let ok, no;
    const promise = new Promise((resolve, reject) => { ok = resolve; no = reject; });
    promise.done = (fine, why) => (fine ? ok(why || true) : no(new Error(why || 'The root helper could not do it.')));
    return promise;
  }

  // ---- Settings rows: self-labelling toggles, chips for one of a few, action buttons, readouts, as a section ----
  //   rows: [{key, label, kind: 'toggle'|'choice'|'select'|'multi'|'button'|'readout'|'text'|'colour', value, options, note, onclick, decision,
  //           max (text), unset, shown, warn (colour, select)}]
  //   opts: {save(changes) → Promise|void, id, label, ask, idle}: the section's (above); no save, no footer.
  function settings(rows, opts = {}) {
    const box = h('div', { class: 'aw-settings', 'data-aw-scope': opts.scope ? '' : null });
    const saved = Object.fromEntries(rows.filter((spec) => spec.key).map((spec) => [spec.key, spec.value]));
    const handle = section({ ...opts, saved, onChange: () => draw() });
    const draw = () => box.replaceChildren(...rows.map(row), ...(opts.save ? [handle.foot, handle.reason] : []));
    function row(spec) {
      let ctl;
      const v = handle.get(spec.key), off = handle.disabled();
      if (spec.kind === 'toggle') {
        ctl = h('button', { type: 'button', class: 'chip-btn' + (v ? ' active' : ''), 'aria-pressed': String(!!v), disabled: off,
          onclick: () => handle.set(spec.key, !handle.get(spec.key)) }, v ? 'On' : 'Off');
      } else if (spec.kind === 'choice') {
        ctl = h('div', { class: 'chip-group', role: 'group', 'aria-label': spec.label }, spec.options.map((o) => {
          const [ov, t] = Array.isArray(o) ? o : [o, o];
          return h('button', { type: 'button', class: 'chip' + (same(v, ov) ? ' selected' : ''), 'aria-pressed': String(same(v, ov)), disabled: off,
            onclick: () => handle.set(spec.key, ov) }, t);
        }));
      } else if (spec.kind === 'select') {
        // A long list as one dropdown (the WiFi country's 249). spec.warn(value): a warning beside it, live, for
        // a value that conflicts with how the box is (a setting asks no question; it says).
        const warn = h('small', { class: 'setting-desc aw-warn', role: 'status', text: (spec.warn && spec.warn(v)) || '' });
        const pick = h('select', { 'aria-label': spec.label, disabled: off, 'data-aw-key': spec.key,
          onchange: (event) => handle.set(spec.key, event.target.value) },
        spec.options.map((o) => {
          const [ov, t] = Array.isArray(o) ? o : [o, o];
          return h('option', { value: ov, selected: same(v, ov) }, t);
        }));
        ctl = h('span', { class: 'aw-select' }, pick, warn);
      } else if (spec.kind === 'multi') {
        // Any of a few, as filter chips: the value the chosen ones, in the options' order.
        const on = new Set(v || []);
        ctl = h('div', { class: 'filter-chips', role: 'group', 'aria-label': spec.label }, spec.options.map((o) => {
          const [ov, t] = Array.isArray(o) ? o : [o, o];
          return h('button', { type: 'button', class: 'filter-chip' + (on.has(ov) ? ' active' : ''), 'aria-pressed': String(on.has(ov)), disabled: off,
            onclick: () => handle.set(spec.key, spec.options.map((x) => (Array.isArray(x) ? x[0] : x)).filter((x) => (x === ov ? !on.has(x) : on.has(x)))) }, t);
        }));
      } else if (spec.kind === 'button') ctl = btn(spec.text || spec.label, { onclick: spec.onclick });
      else if (spec.kind === 'text') {
        ctl = h('input', { type: 'text', value: v ?? '', maxlength: spec.max || null, 'aria-label': spec.label, disabled: off, 'data-aw-key': spec.key,
          oninput: (event) => handle.set(spec.key, event.target.value, true) });
      } else if (spec.kind === 'colour') {
        // A colour, or '' for the theme's own (r.unset names it); r.warn(value) says what's wrong with one, live.
        const warn = h('small', { class: 'setting-desc aw-warn', role: 'status', text: (v && spec.warn && spec.warn(v)) || '' });
        const pick = h('input', { type: 'color', value: v || spec.shown || '#000000', 'aria-label': spec.label, disabled: off, 'data-aw-key': spec.key });
        const own = btn(spec.unset || 'Default', { 'aria-pressed': String(!v), class: 'action-btn' + (!v ? ' active' : ''), disabled: off });
        pick.addEventListener('input', () => {
          handle.set(spec.key, pick.value.toLowerCase(), true);
          warn.textContent = (spec.warn && spec.warn(handle.get(spec.key))) || '';
          own.setAttribute('aria-pressed', 'false');
          own.classList.remove('active');
        });
        own.addEventListener('click', () => handle.set(spec.key, ''));
        ctl = h('span', { class: 'aw-colour' }, pick, own, warn);
      }
      else ctl = h('span', { class: 'output-value', text: spec.value ?? '' });
      return h('div', { class: 'aw-field', 'data-setting': spec.key || null, 'data-decision-row': spec.decision || null },
        h('span', { class: 'aw-label', text: spec.label }), ctl,
        spec.decision ? h('span', { class: 'kind-tag', title: 'Asked in the setup steps', text: 'Setup decision' }) : null,
        spec.note ? h('small', { class: 'setting-desc', text: spec.note }) : null);
    }
    draw();
    box.section = handle;
    box.values = handle.values;
    return box;
  }

  // ---- Findings (the doctors): a short list, worst first, with the cure beside the finding ----
  //   items: [{id, title, summary, status: 'problem'|'warn'|'ok'|'info', actions: [{choice, label, confirm}]}]
  //   opts:  {act(choice, item)}
  const RANK = { problem: 0, bad: 0, warn: 1, info: 2, ok: 3 };
  function findings(items, opts = {}) {
    const sorted = [...items].sort((left, right) => (RANK[left.status] ?? 2) - (RANK[right.status] ?? 2));
    return shortList(sorted.map((finding) => ({ ...finding, badges: [finding.status].concat(finding.badges || []), detail: () => [
      finding.detail ? h('p', { class: 'setting-desc', text: finding.detail }) : null,
      (finding.actions || []).map((action) => btn(action.label, { onclick: async (event) => {
        if ((!action.confirm || (await confirmHere(action.confirm, { anchor: event.currentTarget, label: action.label }))) && opts.act) opts.act(action.choice, finding);
      } })),
    ] })), { id: opts.id });
  }

  // An older list, drawn by its pane's own code: a limited height with a scroll (3e) and, past
  // FILTER_AT entries, a filter box over it (3f). It follows the list through every redraw, so the
  // pane's code stays as it is. opts.filter false: the height only (a list with a search of its own).
  function bound(list, opts = {}) {
    if (!list || list.dataset.awBound) return;
    list.dataset.awBound = '1';
    list.classList.add('aw-bounded');
    if (opts.filter === false) return;
    const count = h('span', { class: 'aw-count' });
    const q = h('input', { type: 'search', placeholder: 'Filter…', 'aria-label': 'Filter this list' });
    const bar = h('div', { class: 'aw-filter', 'data-filter-for': list.id }, q, count);
    list.before(bar);
    const apply = () => {
      const rows = [...list.children], needle = q.value.trim().toLowerCase();
      bar.hidden = rows.length <= FILTER_AT;
      let shown = 0;
      rows.forEach((row) => { const ok = bar.hidden || !needle || row.textContent.toLowerCase().includes(needle); row.hidden = !ok; if (ok) shown++; });
      count.textContent = `${shown} of ${rows.length}`;
    };
    q.addEventListener('input', apply);
    new MutationObserver(apply).observe(list, { childList: true });
    apply();
  }
  // ---- Choice tiles: one of a few, each needing a sentence. The canonical pair: chips when each
  // choice is a word or two (settings' 'choice', and short drop-downs, below), tiles when each needs saying.
  // A tile: a real radio (so a form, or code reading input[name=…]:checked, sees it), its title, what it
  // means, what it does (optional), and why it can't be chosen here (greyed, unless it is the current one).
  //   box: the container (made a radiogroup); name: the radios' name;
  //   options: [{value, title, desc, does, why}]; opts: {value, onChange(value)}
  function choices(box, name, options, opts = {}) {
    box.classList.add('choice-tiles');
    if (!box.hasAttribute('role') && box.tagName !== 'FIELDSET') box.setAttribute('role', 'radiogroup');
    box.replaceChildren(...options.map((o) => {
      const off = !!o.why && o.value !== opts.value;
      const input = h('input', { type: 'radio', name, value: o.value, disabled: off });
      input.checked = o.value === opts.value;
      return h('label', { class: 'choice-tile' + (o.why ? ' unavailable' : '') + (input.checked ? ' chosen' : '') }, input,
        h('span', { class: 'choice-text' }, h('span', { class: 'setting-name', text: o.title }),
          o.desc ? h('span', { class: 'setting-desc', text: o.desc }) : null,
          o.does ? h('span', { class: 'setting-desc choice-does', text: o.does }) : null,
          o.why ? h('span', { class: 'setting-desc bad', text: `Not available here: ${o.why}.` }) : null));
    }));
    if (!box._choicesWired) {
      box._choicesWired = true;
      box.addEventListener('change', (event) => {
        if (event.target.type !== 'radio') return;
        box.querySelectorAll('.choice-tile').forEach((t) => t.classList.toggle('chosen', t.querySelector('input').checked));
        if (box._onChange) box._onChange(event.target.value);
      });
    }
    box._onChange = opts.onChange || null;
    return box;
  }

  // ---- A drop-down of a few, as chips: every <select> of up to CHIPS_AT choices is
  // shown as a row of chips, one selected (a radio group to a screen reader). The <select> stays, hidden,
  // as the source of truth: forms, .value and its change handlers work as they did, and the chips follow
  // whatever sets it. A longer list (languages, channels, hours) stays a drop-down; one whose choices are
  // drawn from data turns into chips or back as their number changes.
  const CHIPS_AT = 6;
  function chipSelect(sel) {
    watchSetters();
    if (sel._chips !== undefined || sel.multiple || sel.closest('[data-no-chips]')) return;
    const group = h('div', { class: 'chip-group select-chips', role: 'radiogroup' });
    sel._chips = group;
    sel.after(group);
    const label = sel.closest('label');
    const name = sel.getAttribute('aria-label') || (label ? [...label.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').trim() : '');
    if (name) group.setAttribute('aria-label', name);
    // Short chip labels, one line saying what the chosen one means: an option's data-says text, shown under the chips and changing with them.
    const says = [...sel.options].some((o) => o.dataset.says) ? h('p', { class: 'setting-desc chip-says' }) : null;
    if (says) group.after(says);
    let drawn = '';
    const draw = () => {
      const opts = [...sel.options];
      // Drawn again only when something about them changed (a poll setting the same value redraws nothing).
      const key = JSON.stringify([sel.disabled, opts.map((o) => [o.value, o.textContent, o.selected, o.disabled, o.title])]);
      if (key === drawn) return;
      drawn = key;
      const few = opts.length > 0 && opts.length <= CHIPS_AT;
      sel.hidden = few;
      group.hidden = !few;
      if (says) { const o = sel.selectedOptions[0]; says.textContent = (o && o.dataset.says) || ''; says.hidden = !few; }
      if (!few) return;
      group.replaceChildren(...opts.map((o) => h('button', {
        type: 'button', class: 'chip' + (o.selected ? ' selected' : ''), role: 'radio', 'aria-checked': String(o.selected),
        disabled: sel.disabled || o.disabled, title: o.title || null,
        onclick: () => { if (sel.value === o.value) return; sel.value = o.value; sel.dispatchEvent(new Event('change', { bubbles: true })); },
      }, o.textContent)));
    };
    sel._chipsDraw = draw;     // the value set from code redraws too (watchSetters, below)
    sel.addEventListener('change', draw);
    if (sel.form) sel.form.addEventListener('reset', () => setTimeout(draw));
    new MutationObserver(draw).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected'] });
    draw();
  }
  // A <select>'s value set from code (sel.value = …, selectedIndex) redraws its chips: the setters wrapped
  // once, on the prototype (an own property on each element confuses jsdom, which the tests run in).
  let watched = false;
  function watchSetters() {
    if (watched || typeof HTMLSelectElement === 'undefined') return;
    watched = true;
    for (const prop of ['value', 'selectedIndex']) {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
      if (!descriptor || !descriptor.set) continue;
      Object.defineProperty(HTMLSelectElement.prototype, prop, { configurable: true, enumerable: descriptor.enumerable,
        get() { return descriptor.get.call(this); }, set(v) { descriptor.set.call(this, v); if (this._chipsDraw) this._chipsDraw(); } });
    }
  }
  function chipSelects(root) {
    watchSetters();
    (root || document).querySelectorAll('select').forEach(chipSelect);
  }
  // ---- A plain form as a section: its named fields are the draft ----
  // The page gives the saved values (f.section.fill, from each poll) and never writes the fields itself; the
  // form's Save and Discard become the section's footer, where its Save button was. A value kept outside the
  // fields (the firmware's boards) is opts.extra: {key: {set(value)}}, and the page calls section.set for it.
  //   opts: the section's (save, ask, idle, label), and extra
  const FIELD = (field) => field.name && !['button', 'submit', 'reset', 'file'].includes(field.type) && !field.closest('[data-aw-ignore]');
  function form(formEl, opts = {}) {
    if (formEl.section) return formEl.section;
    const named = (n) => [...formEl.elements].filter((field) => FIELD(field) && (!n || field.name === n));
    const read = (n) => {
      const els = named(n);
      if (!els.length) return undefined;
      if (els[0].type === 'radio') { const c = els.find((x) => x.checked); return c ? c.value : ''; }
      return els[0].type === 'checkbox' ? els[0].checked : els[0].value;
    };
    const extra = opts.extra || {};
    let writing = false;
    const offByUs = new Set();
    const handle = section({ ...opts, id: opts.id || formEl.id, saved: null, onChange: () => paint() });
    // The draft into the fields; each field written raises change, so what follows a field (chips, a part shown
    // only for one choice) follows it here too. Off until the values are read.
    function paint() {
      writing = true;
      for (const [k, v] of Object.entries(handle.values())) {
        if (k in extra) { extra[k].set(v); continue; }
        for (const field of named(k)) {
          const was = field.type === 'radio' || field.type === 'checkbox' ? field.checked : field.value;
          if (field.type === 'radio') field.checked = field.value === String(v);
          else if (field.type === 'checkbox') field.checked = !!v;
          else field.value = v == null ? '' : String(v);
          if (was !== (field.type === 'radio' || field.type === 'checkbox' ? field.checked : field.value)) field.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }
      writing = false;
      if (handle.loaded()) { offByUs.forEach((field) => { field.disabled = false; if (field._chipsDraw) field._chipsDraw(); }); offByUs.clear(); }
    }
    for (const field of named()) if (!field.disabled) { field.disabled = true; offByUs.add(field); if (field._chipsDraw) field._chipsDraw(); }
    // Values as the fields hold them: a tick true or false, everything else a string.
    handle.fill = (values) => {
      const out = {};
      for (const [k, v] of Object.entries(values || {})) {
        const field = named(k)[0];
        out[k] = k in extra ? v : field && field.type === 'checkbox' ? !!v : v == null ? '' : String(v);
      }
      handle.update(out);
    };
    const touch = (event) => {
      if (writing || !event.target || !FIELD(event.target) || !handle.loaded()) return;
      handle.set(event.target.name, read(event.target.name), true);
    };
    formEl.addEventListener('input', touch);
    formEl.addEventListener('change', touch);
    formEl.addEventListener('submit', (event) => { event.preventDefault(); handle.save(); });
    const old = [...formEl.querySelectorAll('button')];
    const saveOld = old.find((b) => b.type === 'submit' && b.textContent.trim().startsWith('Save'));
    if (saveOld) saveOld.replaceWith(handle.foot); else formEl.append(handle.foot);
    handle.foot.after(handle.reason);
    old.filter((b) => b.textContent.trim().startsWith('Discard')).forEach((b) => b.remove());
    formEl.section = handle;
    return handle;
  }
  // ---- A selection survives the page's polling ----
  // While someone has text selected in a block, drawing that block again with the same content is skipped, so the
  // selection (and a copy in progress) is not taken away by a poll that brings nothing new. Content that changed is
  // drawn as ever; with nothing selected, nothing is different.
  const escapeHTML = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\u00a0/g, '&nbsp;');
  function selectionIn(node) {
    const sel = typeof document.getSelection === 'function' ? document.getSelection() : null;
    if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
    const range = sel.getRangeAt(0);
    return node.contains(range.startContainer) || node.contains(range.endContainer) || range.intersectsNode(node);
  }
  let kept = false;
  function keepSelections() {
    if (kept || typeof Element === 'undefined' || typeof Node === 'undefined') return;
    kept = true;
    const rc = Element.prototype.replaceChildren;
    Element.prototype.replaceChildren = function (...nodes) {
      // A block with fields is always drawn: a value typed or a tick is not in its HTML, so "the same" can't be told.
      const fields = (n) => n && n.nodeType === 1 && (n.matches('input, select, textarea') || n.querySelector('input, select, textarea'));
      if (selectionIn(this) && !nodes.some(fields)) {
        const html = nodes.map((n) => (n == null ? '' : typeof n === 'string' ? escapeHTML(n) : n.nodeType === 3 ? escapeHTML(n.data) : n.outerHTML || '')).join('');
        if (html === this.innerHTML) return undefined;
      }
      return rc.apply(this, nodes);
    };
    const tc = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
    if (tc && tc.set) {
      Object.defineProperty(Node.prototype, 'textContent', { configurable: true, enumerable: tc.enumerable,
        get() { return tc.get.call(this); },
        set(v) {
          // Only a block of plain text: setting it with its own words again changes nothing there.
          if (this.nodeType === 1 && v != null && String(v) === tc.get.call(this) && [...this.childNodes].every((c) => c.nodeType === 3)
            && selectionIn(this)) return;
          tc.set.call(this, v);
        } });
    }
  }
  // A block the page draws at every poll, kept where it would look the same: each child is replaced only when
  // what it shows changes, so a selection, a focus or an open fold in the others stays. Its live parts
  // ([data-live]: a signal, an uptime) are left out of that and put in place on their own. A field's value
  // counts; a block holding a settings section (its Save and Discard) is always drawn, as the section keeps
  // its draft with the copy drawn last.
  const LIVE = '[data-live]';
  function shownAs(n) {
    if (n == null || n === false) return '';
    if (typeof n === 'string') return escapeHTML(n);
    if (n.nodeType === 3) return escapeHTML(n.data);
    const c = n.cloneNode(true);
    if (c.matches(LIVE)) return `<live ${c.getAttribute('data-live')}>`;
    c.querySelectorAll(LIVE).forEach((x) => x.replaceChildren());
    const vals = [...n.querySelectorAll('input, select, textarea')].map((input) => (input.type === 'checkbox' || input.type === 'radio' ? input.checked : input.value));
    return c.outerHTML + (vals.length ? JSON.stringify(vals) : '');
  }
  function drawKept(box, nodes) {
    nodes = nodes.filter((n) => n != null && n !== false).map((n) => (typeof n === 'string' ? document.createTextNode(n) : n));
    const sigs = nodes.map(shownAs);
    const was = box._shownAs;
    const sections = nodes.some((n) => n.nodeType === 1 && (n.matches('.aw-foot') || n.querySelector('.aw-foot')));
    if (sections || !was || was.length !== sigs.length || box.childNodes.length !== was.length) {
      box.replaceChildren(...nodes);
    } else {
      const cur = [...box.childNodes];
      nodes.forEach((n, i) => {
        if (was[i] !== sigs[i]) cur[i].replaceWith(n);
        else if (n.nodeType === 1) {
          const old = cur[i].matches(LIVE) ? [cur[i]] : [...cur[i].querySelectorAll(LIVE)];
          const now = n.matches(LIVE) ? [n] : [...n.querySelectorAll(LIVE)];
          now.forEach((x, j) => { if (old[j] && old[j].outerHTML !== x.outerHTML) old[j].replaceWith(x); });
        }
      });
    }
    box._shownAs = sections ? null : sigs;
  }
  if (typeof document !== 'undefined' && typeof MutationObserver !== 'undefined') {
    keepSelections();
    const start = () => {
      updatePatterns(document);
      chipSelects(document);
      new MutationObserver((muts) => muts.forEach((m) => m.addedNodes.forEach((n) => {
        if (n.nodeType !== 1) return;
        updatePatterns(n.parentNode || n);
        if (n.tagName === 'SELECT') chipSelect(n); else chipSelects(n);
      }))).observe(document.body, { childList: true, subtree: true });
    };
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  }
  return { FILTER_AT, BOUND_AT, CHIPS_AT, single: true, confirmHere, drawKept, say, localise, h, btn, pill, updatePill, updatePattern, updatePatterns, updateButtons, patternSays, UPDATE_OFTEN: OFTEN, UPDATE_ACT: ACT, dl, shortList, records, cards, settings, findings, filterBar, closeCard, foldRow, bound, section, unsaved, job, choices, chipSelect, chipSelects, form };
})();
// Scripts evaluated one by one (the jsdom tests) see it too.
if (typeof window !== 'undefined') window.AW = AW;
