// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin's shared widgets (menu overhaul, step M1; checklist 3): one way to show a short
// list, a list of records, a grid of cards, a block of settings and a list of findings, so every
// pane behaves the same. Plain script, no build step: it defines AW for admin.js and the sections.
//
// The rules they keep (snaglist-checklist-2026-10-07, checklist 3, as Tom settled them on the
// menu mock):
//   3a a card's drawer opens inside the card, which widens to the row
//   3b the title folds it again (a card's head, an entry's ▸/▾ line); no separate collapse button
//   3c an opened card has a ✕ (Esc does the same)
//   3d a long but limited list shows one line per entry; the rest opens from the line
//   3e a list of records has a limited height and scrolls
//   3f a list of more than FILTER_AT entries has a filter: a search box and chips of its states
// And one open at a time in a pane; settings wait for Save (Discard puts them back), jobs act at once.
'use strict';

const AW = (() => {
  const FILTER_AT = 12;   // 3f: more than this many entries get a filter
  const BOUND_AT = 20;    // a short list longer than this scrolls in its own box

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

  // A state, one meaning each (rule 6c): ok, warn, bad or info, from the word unless told.
  const BAD = /red|fail|down|stopped|invalid|reported|problem/i, WARN = /amber|update|behind|flagged|queued|planned|working|ready|warn/i,
    OK = /^ok$|built|installed|cached|stable|running|kept|on$/i;
  function pill(text, tone) {
    const t = tone || (BAD.test(text) ? 'bad' : WARN.test(text) ? 'warn' : OK.test(text) ? 'ok' : 'info');
    return h('span', { class: t + '-pill', text });
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
    const chips = tags.map((t) => h('button', { type: 'button', class: 'filter-chip', 'aria-pressed': 'false', onclick: (e) => {
      if (active.has(t)) active.delete(t); else active.add(t);
      e.currentTarget.classList.toggle('active', active.has(t));
      e.currentTarget.setAttribute('aria-pressed', String(active.has(t)));
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
  function closeOthers(scope, keep) {
    scope.querySelectorAll('.aw-card.open').forEach((c) => c !== keep && closeCard(c, true));
    scope.querySelectorAll('.aw-row-head[aria-expanded="true"]').forEach((hd) => hd !== keep && foldRow(hd, false));
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
    const list = h('div', { class: 'aw-records', id, 'data-list': 'records', 'data-count': items.length },
      items.map((i) => h('div', { 'data-aw-id': i.id, ...rowData(i), text: i.title })));
    return h('div', { class: 'aw-block' }, h('p', { class: 'aw-records-head', text: `${items.length} ${opts.noun || 'records'}, newest first` }),
      items.length > FILTER_AT ? filterBar(list, 'div[data-aw-id]', items) : null, list);
  }

  // ---- Cards (3a–3c): the drawer opens inside the card, which widens to the row ----
  //   items: [{id, title, summary, badges}]
  //   opts:  {id, body(item, setDirty) → nodes, save(item) → Promise|void, saveLabel}
  //   A drawer with settings (setDirty called) shows Save and Discard; Discard rebuilds the body.
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
    if (AW.single) closeOthers(scopeOf(card), card);
    let dirty = false;
    const foot = h('div', { class: 'aw-foot', hidden: true });
    const saveB = btn(opts.saveLabel || 'Save', { class: 'action-btn primary', disabled: true });
    const discard = btn('Discard', { disabled: true });
    const setDirty = (d = true) => { dirty = d; saveB.disabled = discard.disabled = !d; foot.hidden = false; };
    const body = h('div', { class: 'aw-drawer-body' });
    const fill = () => { body.replaceChildren(...[opts.body ? opts.body(it, setDirty) : dl(it.detail)].flat(Infinity).filter(Boolean)); };
    discard.addEventListener('click', () => { fill(); setDirty(false); });
    saveB.addEventListener('click', async () => {
      saveB.disabled = true;
      try { const r = opts.save && opts.save(it); if (r && r.then) await r; setDirty(false); } catch (e) { setDirty(true); foot.querySelector('.note').textContent = String(e.message || e); }
    });
    foot.append(h('span', { class: 'note', text: 'Settings wait for Save; jobs act at once.' }), discard, saveB);
    const drawer = h('div', { class: 'aw-drawer', role: 'region', 'aria-label': it.title },
      btn('✕', { class: 'action-btn aw-close', 'data-act': 'close', 'aria-label': 'Close', title: 'Close (Esc)', onclick: () => closeCard(card) }),
      body, foot);
    fill();
    card._drawer = drawer;
    card._dirty = () => dirty;
    card.classList.add('open');
    card.querySelector('.aw-card-head').setAttribute('aria-expanded', 'true');
    card.append(drawer);
    openStack.push(card);
    return drawer;
  }
  function closeCard(card, force) {
    if (!card._drawer) return;
    if (!force && card._dirty() && !window.confirm('Discard the changes you haven\'t saved?')) return;
    card._drawer.remove();
    card._drawer = null;
    card.classList.remove('open');
    card.querySelector('.aw-card-head').setAttribute('aria-expanded', 'false');
    const i = openStack.indexOf(card);
    if (i >= 0) openStack.splice(i, 1);
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openStack.length) closeCard(openStack[openStack.length - 1]); });

  // ---- Settings rows: self-labelling toggles, chips for one of a few, action buttons, readouts ----
  //   rows: [{key, label, kind: 'toggle'|'choice'|'button'|'readout'|'text', value, options, note, onclick, decision}]
  //   opts: {save(values) → Promise|void}: changed values are held until Save; Discard puts them back.
  function settings(rows, opts = {}) {
    const values = Object.fromEntries(rows.filter((r) => r.key).map((r) => [r.key, r.value]));
    const draft = { ...values };
    const box = h('div', { class: 'aw-settings', 'data-aw-scope': opts.scope ? '' : null });
    const saveB = btn('Save', { class: 'action-btn primary', disabled: true });
    const discard = btn('Discard', { disabled: true });
    const changed = () => rows.some((r) => r.key && draft[r.key] !== values[r.key]);
    const sync = () => { saveB.disabled = discard.disabled = !changed(); };
    const draw = () => box.replaceChildren(...rows.map(row), opts.save ? h('div', { class: 'aw-foot' },
      h('span', { class: 'note', text: 'Settings wait for Save.' }), discard, saveB) : null);
    function row(r) {
      let ctl;
      if (r.kind === 'toggle') {
        ctl = h('button', { type: 'button', class: 'chip-btn' + (draft[r.key] ? ' active' : ''), 'aria-pressed': String(!!draft[r.key]),
          onclick: (e) => { draft[r.key] = !draft[r.key]; e.currentTarget.classList.toggle('active', draft[r.key]);
            e.currentTarget.setAttribute('aria-pressed', String(draft[r.key])); e.currentTarget.textContent = draft[r.key] ? 'On' : 'Off'; sync(); } },
          draft[r.key] ? 'On' : 'Off');
      } else if (r.kind === 'choice') {
        ctl = h('div', { class: 'chip-group', role: 'group', 'aria-label': r.label }, r.options.map((o) => {
          const [v, t] = Array.isArray(o) ? o : [o, o];
          return h('button', { type: 'button', class: 'chip' + (draft[r.key] === v ? ' selected' : ''), 'aria-pressed': String(draft[r.key] === v),
            onclick: (e) => { draft[r.key] = v; e.currentTarget.parentNode.querySelectorAll('.chip').forEach((c) => {
              c.classList.toggle('selected', c === e.currentTarget); c.setAttribute('aria-pressed', String(c === e.currentTarget)); }); sync(); } }, t);
        }));
      } else if (r.kind === 'button') ctl = btn(r.text || r.label, { onclick: r.onclick });
      else if (r.kind === 'text') ctl = h('input', { type: 'text', value: draft[r.key] ?? '', 'aria-label': r.label, oninput: (e) => { draft[r.key] = e.target.value; sync(); } });
      else ctl = h('span', { class: 'output-value', text: r.value ?? '' });
      return h('div', { class: 'aw-field', 'data-setting': r.key || null, 'data-decision-row': r.decision || null },
        h('span', { class: 'aw-label', text: r.label }), ctl,
        r.decision ? h('span', { class: 'info-pill', title: 'Asked in the setup steps', text: 'setup decision' }) : null,
        r.note ? h('small', { class: 'setting-desc', text: r.note }) : null);
    }
    discard.addEventListener('click', () => { Object.assign(draft, values); draw(); sync(); });
    saveB.addEventListener('click', async () => {
      saveB.disabled = true;
      const out = Object.fromEntries(Object.keys(draft).filter((k) => draft[k] !== values[k]).map((k) => [k, draft[k]]));
      try { const r = opts.save(out); if (r && r.then) await r; Object.assign(values, draft); } catch (e) { box.querySelector('.aw-foot .note').textContent = String(e.message || e); }
      sync();
    });
    draw();
    box.values = () => ({ ...draft });
    return box;
  }

  // ---- Findings (the doctors): a short list, worst first, with the cure beside the finding ----
  //   items: [{id, title, summary, status: 'problem'|'warn'|'ok'|'info', actions: [{choice, label, confirm}]}]
  //   opts:  {act(choice, item)}
  const RANK = { problem: 0, bad: 0, warn: 1, info: 2, ok: 3 };
  function findings(items, opts = {}) {
    const sorted = [...items].sort((a, b) => (RANK[a.status] ?? 2) - (RANK[b.status] ?? 2));
    return shortList(sorted.map((f) => ({ ...f, badges: [f.status].concat(f.badges || []), detail: () => [
      f.detail ? h('p', { class: 'setting-desc', text: f.detail }) : null,
      (f.actions || []).map((a) => btn(a.label, { onclick: () => { if (!a.confirm || window.confirm(a.confirm)) opts.act && opts.act(a.choice, f); } })),
    ] })), { id: opts.id });
  }

  return { FILTER_AT, BOUND_AT, single: true, h, btn, pill, dl, shortList, records, cards, settings, findings, filterBar, closeCard, foldRow };
})();
