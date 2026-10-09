// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin's menu, built from one table (menu overhaul M4; Tom, 2026-10-08: the app-first layout
// "as it is now"). admin.html keeps every section as it was, each a <section class="admin-pane">
// with its own id, so admin.js binds to them unchanged; this file gathers them into pages:
//   Overview · Apps (a page per app, from /admin/apps: what each app's manifest says it owns)
//   · Folders · Moderation · System · Doctors, the doctors last.
// The address names a section (#books, #backup): the page holding it opens, scrolled to it, so
// every old link and bookmark still lands. A section no page claims goes to "More" at the end
// rather than vanish (the jsdom tests check none does).
const AL = (() => {
  'use strict';
  // The box-wide groups. A page: its title, the sections it holds, its art if not the group's.
  // The Apps and Folders groups also get a page per app from the manifests (apps: true).
  const LAYOUT = [
    // flat: no group head to fold (Tom, 2026-10-09: Overview and the setup steps "just leave them visible all
    // the time. It's fewer clicks all around"): the links stand at the top of the sidebar.
    { name: 'Overview', art: 'controller', flat: true, pages: [
      { title: 'Overview', sections: ['overview'] },
      { title: 'Setup steps', sections: ['welcome'], art: 'welcome-controller' }] },
    { name: 'Apps', art: 'librarian', apps: 'apps', pages: [
      { title: 'All apps, in order', sections: ['tile-order', 'apps', 'addons'] }] },
    { name: 'Folders', art: 'librarian', apps: 'folders', pages: [
      { title: 'Status tiles', sections: ['status-tiles'], art: 'controller' }] },
    { name: 'Moderation', art: 'librarian', pages: [
      { title: 'Moderation', sections: ['moderation'] },
      { title: 'Everything people made', sections: ['made'] }] },
    { name: 'System', art: 'controller', pages: [
      { title: 'Accounts & users', sections: ['accounts', 'access'] },
      { title: 'Network', sections: ['network'] },
      { title: 'Security', sections: ['security'] },
      { title: 'The librarian', sections: ['sources'] },
      { title: 'Toolkits', sections: ['toolkits'], art: 'workbench' },
      { title: 'Clock', sections: ['clock'] },
      { title: 'Appearance', sections: ['appearance'] },
      { title: 'Updates and backup', sections: ['updates', 'backup'] }] },
    { name: 'Doctors', art: 'doctor', pages: [
      { title: 'Box doctor', sections: ['health'] },
      { title: 'Security doctor', sections: ['secdoctor'] },
      { title: 'Updates doctor', sections: ['updoctor'] }] },
  ];
  // A section with art of its own, wherever it lands (the Firmware Factory's production line).
  const SECTION_ART = { factory: 'factory', toolkits: 'workbench', welcome: 'welcome-controller' };

  const main = document.querySelector('.admin-main');
  const list = document.getElementById('admin-side-list');
  const side = document.querySelector('.admin-side');
  const menu = document.querySelector('.admin-menu');
  const sections = new Map([...document.querySelectorAll('.admin-main > .admin-pane')].map((s) => [s.id, s]));
  const placed = new Set();
  const pages = [];
  const badges = {};
  const hiddenLinks = new Set();
  const onBadge = [];  // told when any section's word changes (Overview's Needs attention, F4)
  const onBuild = [];  // called after each build: admin.js fills the generated sections  // sections whose page's sidebar entry is hidden (the setup steps, once done)
  let built = false;

  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => { if (v != null) { if (k === 'text') n.textContent = v; else if (k === 'class') n.className = v; else n.setAttribute(k, v); } });
    kids.flat().forEach((k) => k != null && n.append(k));
    return n;
  }
  // A page: its own <section>, holding the sections moved into it, in order.
  function page(group, def) {
    const ids = def.sections.filter((id) => sections.has(id) && !placed.has(id));
    if (!ids.length && !def.empty) return null;
    const id = 'page-' + (def.id || ids[0] || def.title.toLowerCase().replace(/[^a-z0-9]+/g, '-'));
    const p = el('section', { class: 'admin-page', id, 'data-art': def.art || SECTION_ART[ids[0]] || group.art || '',
      'aria-label': def.title }, el('h2', { class: 'admin-page-title', text: def.title }));
    if (ids.length === 1 && !def.app) p.classList.add('solo');  // an app's page keeps the app's name over its sections
    ids.forEach((sid) => { placed.add(sid); p.append(sections.get(sid)); });
    // A section headed with the page's own title (Git on Git's page) doesn't say it twice: its
    // heading stays, for its aria-labelledby, but isn't shown.
    p.querySelectorAll(':scope > .admin-pane > h2').forEach((h) => h.classList.toggle('same-as-page', !p.classList.contains('solo') && h.textContent.trim() === def.title));
    p.hidden = true;
    main.append(p);
    const link = el('a', { href: '#' + (def.app ? id : ids[0] || id), 'data-page': id }, def.icon ? el('span', { class: 'side-icon', 'aria-hidden': 'true', text: def.icon }) : null, def.title);
    const entry = { id, el: p, link, title: def.title, group: group.name };
    pages.push(entry);
    return entry;
  }
  function firstGlyph(s) {
    if (!s) return null;
    if (window.Intl && Intl.Segmenter) { const g = new Intl.Segmenter().segment(s)[Symbol.iterator]().next().value; return g ? g.segment : null; }
    return [...s][0];
  }
  // The sidebar's groups fold (Tom, 2026-10-08: "Default open, option to toggle"; "Toggle button
  // for 'auto-collapse' of the groups"). Each head is a button over its links; the current page's
  // group is always open; a folded group shows its links' badges on its head. Auto-collapse keeps
  // only the current group open. Both are this viewer's own, kept in the browser (a convenience,
  // not a setting: without storage every group simply starts open).
  const FOLD_KEY = 'irate-admin-folded', AUTO_KEY = 'irate-admin-autocollapse';
  const stored = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } };
  const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private window: not kept */ } };
  const folded = new Set(stored(FOLD_KEY, []));
  let auto = stored(AUTO_KEY, false) === true;
  const groups = new Map();  // name -> { head, box }
  function group(name, links) {
    const id = 'side-group-' + name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    const head = el('button', { type: 'button', class: 'admin-side-group', 'aria-controls': id, 'data-group': name }, el('span', { class: 'side-group-name', text: name }));
    const box = el('div', { class: 'admin-side-links', id, role: 'group', 'aria-label': name }, links);
    head.addEventListener('click', () => {
      const open = head.getAttribute('aria-expanded') !== 'true';
      if (open) folded.delete(name); else folded.add(name);
      store(FOLD_KEY, [...folded]);
      if (auto && open) groups.forEach((x, n) => { if (n !== name) setOpen(n, false); });
      setOpen(name, open);
    });
    groups.set(name, { head, box });
    return [head, box];
  }
  function setOpen(name, open) {
    const g = groups.get(name);
    if (!g) return;
    g.head.setAttribute('aria-expanded', String(open));
    g.box.hidden = !open;
    rollUp(name);
  }
  // A folded group's head carries its links' words, so a problem isn't hidden by folding.
  function rollUp(name) {
    const g = groups.get(name);
    if (!g) return;
    const words = [...g.box.querySelectorAll('a[data-badge]:not([hidden])')].map((a) => a.dataset.badge);
    // Counts add up (3 to fix and 1 to fix: 4); other words (!, new, ready) follow, each once.
    const n = words.filter((x) => /^\d+$/.test(x)).reduce((s, x) => s + Number(x), 0);
    const sum = [n || null, ...new Set(words.filter((x) => !/^\d+$/.test(x)))].filter(Boolean).join(' ');
    if (g.box.hidden && words.length) { g.head.dataset.badge = sum; g.head.title = words.join(', '); }
    else { delete g.head.dataset.badge; g.head.removeAttribute('title'); }
  }
  // Open the groups as kept, the current one always; with auto-collapse, only the current one.
  function fold(current) {
    // A page opened in a group folded by hand unfolds it for good: the viewer went there.
    if (current && folded.delete(current)) store(FOLD_KEY, [...folded]);
    groups.forEach((_, name) => setOpen(name, name === current || (!auto && !folded.has(name))));
  }
  const autoBtn = el('button', { type: 'button', class: 'action-btn side-auto', 'aria-pressed': String(auto) });
  const autoLabel = () => { autoBtn.textContent = `Auto-collapse: ${auto ? 'On' : 'Off'}`; autoBtn.setAttribute('aria-pressed', String(auto)); };
  autoBtn.addEventListener('click', () => {
    auto = !auto;
    store(AUTO_KEY, auto);
    autoLabel();
    const cur = pages.find((p) => p.link.getAttribute('aria-current') === 'page');
    fold(cur ? cur.group : null);
  });
  autoLabel();

  // The box-wide groups now; the apps' pages when /admin/apps answers (or without it, at worst).
  const slots = {};
  function build(apps) {
    list.replaceChildren();
    groups.clear();
    pages.forEach((p) => p.el.remove());
    pages.length = 0;
    placed.clear();
    for (const g of LAYOUT) {
      const entries = [];
      const fixed = g.pages.map((d) => page(g, d)).filter(Boolean);
      entries.push(...fixed);
      if (g.apps && apps) {
        for (let a of apps.filter((x) => (g.apps === 'folders') === x.folder && (x.switch || x.seen || x.sections.length || x.folder || x.page))) {
          // Who opens it, who sees it: first on its page (M6; admin.js fills it from /admin/access).
          // An app with no switch of its own (a folder, About) has only who sees its tile (F3).
          if (a.switch || a.seen) {
            const id = 'app-access-' + a.id;
            if (!sections.has(id)) sections.set(id, el('section', { class: 'admin-pane app-access', id },
              el('h2', { text: a.switch ? 'Who opens it, who sees it' : 'Who sees it' }), el('div', { class: 'access-block', 'data-app': a.id })));
            a = { ...a, sections: [id, ...a.sections] };
          }
          // An app drawn on the hub page: its width, after its first section (M9).
          if (a.width) {
            const id = 'app-width-' + a.id;
            if (!sections.has(id)) sections.set(id, el('section', { class: 'admin-pane app-width', id },
              el('h2', { text: 'Width' }), el('div', { class: 'width-block', 'data-key': a.width })));
            a = { ...a, sections: [a.sections[0], id, ...a.sections.slice(1)].filter(Boolean) };
          }
          // A folder's entries (M7): after its access, before its other sections.
          if (a.folder) {
            const id = 'folder-' + a.id;
            if (!sections.has(id)) sections.set(id, el('section', { class: 'admin-pane folder-section', id },
              el('h2', { text: 'What\'s in it' }), el('div', { class: 'folder-block', 'data-folder': a.id })));
            const at = a.switch || a.seen ? 1 : 0;
            a = { ...a, sections: [...a.sections.slice(0, at), id, ...a.sections.slice(at)] };
          }
          // The app's own updates (F6; the mock's "Its own updates"): after its access, width and
          // folder, before what it owns. Drawn by admin.js from the librarian's snapshot.
          if (a.updates) {
            const id = 'app-updates-' + a.id;
            if (!sections.has(id)) sections.set(id, el('section', { class: 'admin-pane app-updates', id },
              el('h2', { text: 'Its own updates' }), el('div', { class: 'updates-block', 'data-app': a.id })));
            const own = new Set(a.sections.filter((s) => !/^(app-access-|app-width-|folder-)/.test(s)));
            const at = a.sections.findIndex((s) => own.has(s));
            a = { ...a, sections: at < 0 ? [...a.sections, id] : [...a.sections.slice(0, at), id, ...a.sections.slice(at)] };
          }
          // What was reported there (F6; 4f: flagged items last), for an app drawn on the hub page.
          if (a.page) {
            const id = 'app-flagged-' + a.id;
            if (!sections.has(id)) sections.set(id, el('section', { class: 'admin-pane app-flagged', id },
              el('h2', { text: 'Flagged and reported' }), el('div', { class: 'flagged-block', 'data-app': a.id })));
            a = { ...a, sections: [...a.sections, id] };
          }
          const e = page({ ...g, art: a.art || g.art }, { id: 'app-' + a.id, app: a.id, title: a.name, icon: firstGlyph(a.icon), sections: a.sections, empty: false });
          if (e) entries.push(e);
        }
      }
      if (g.apps === 'apps') slots.apps = entries;
      if (!entries.length) continue;
      if (g.flat) list.append(el('div', { class: 'admin-side-links', role: 'group', 'aria-label': g.name }, entries.map((e) => e.link)));
      else list.append(...group(g.name, entries.map((e) => e.link)));
    }
    // Whatever no page claimed: kept, under Apps, so nothing is lost while the manifests catch up.
    const left = [...sections.keys()].filter((id) => !placed.has(id) && !/^(app-access-|app-width-|folder-|app-updates-|app-flagged-)/.test(id));
    if (left.length) {
      const g = { name: 'More', art: '' };
      const extra = left.map((id) => page(g, { title: (sections.get(id).querySelector('h2') || {}).textContent || id, sections: [id] })).filter(Boolean);
      list.append(...group(g.name, extra.map((e) => e.link)));
    }
    Object.keys(badges).forEach(paint);
    hiddenLinks.forEach((id) => hide(id, true));
    list.append(el('p', { class: 'side-foot' }, autoBtn));
    built = true;
    onBuild.forEach((f) => { try { f(); } catch (e) { console.error(e); } });
  }

  // The page for an address: the page itself, or the one holding the section it names.
  function pageFor(hash) {
    const target = hash && document.getElementById(hash.slice(1));
    const p = target && (target.classList.contains('admin-page') ? target : target.closest('.admin-page'));
    return { page: pages.find((x) => x.el === p) || pages[0], target };
  }
  function show() {
    if (!pages.length) return;
    const { page: cur, target } = pageFor(location.hash);
    pages.forEach((p) => { p.el.hidden = p !== cur; });
    pages.forEach((p) => { if (p === cur) p.link.setAttribute('aria-current', 'page'); else p.link.removeAttribute('aria-current'); });
    main.dataset.art = cur.el.dataset.art || '';
    fold(cur.group);
    document.getElementById('admin-current').textContent = cur.title;
    document.title = `${cur.title} · Hub admin`;
    side.classList.remove('open');
    menu.setAttribute('aria-expanded', 'false');
    if (target && target !== cur.el && target !== cur.el.querySelector('.admin-pane') && target.scrollIntoView) target.scrollIntoView({ block: 'start' });
    else window.scrollTo(0, 0);
  }
  // Is a section on the page showing? (admin.js's loaders that wait until their section is seen.)
  function shown(id) {
    const s = document.getElementById(id);
    const p = s && s.closest('.admin-page');
    return !!p && !p.hidden;
  }
  // A word beside a section's sidebar entry ("new", "ready", a count): on its page's entry, the
  // first section's word that has one.
  function badge(id, text) {
    if (text) badges[id] = text; else delete badges[id];
    paint(id);
    onBadge.forEach((f) => { try { f(); } catch (e) { console.error(e); } });
  }
  function paint(id) {
    const s = document.getElementById(id);
    const p = s && pages.find((x) => x.el === (s.classList.contains('admin-page') ? s : s.closest('.admin-page')));
    if (!p) return;
    const ids = [...p.el.querySelectorAll(':scope > .admin-pane')].map((x) => x.id);
    const word = ids.map((x) => badges[x]).find(Boolean);
    if (word) p.link.dataset.badge = word; else delete p.link.dataset.badge;
    rollUp(p.group);
  }

  // Hide or show the sidebar entry of the page holding a section (kept across rebuilds).
  function hide(id, yes) {
    if (yes) hiddenLinks.add(id); else hiddenLinks.delete(id);
    const s = document.getElementById(id);
    const p = s && pages.find((x) => x.el === s.closest('.admin-page'));
    if (p) { p.link.hidden = !!yes; rollUp(p.group); }
  }

  menu.addEventListener('click', () => { const open = side.classList.toggle('open'); menu.setAttribute('aria-expanded', String(open)); });
  window.addEventListener('hashchange', show);
  build(null);
  show();
  // The apps' pages: a fetch away. When they're in, the page is rebuilt and the loaders told (a
  // hashchange), as a section they wait for may only now be showing.
  const ready = fetch('/admin/apps', { headers: { 'X-Irate-Admin': '1' } })
    .then((r) => (r.ok ? r.json() : null)).catch(() => null)
    .then((data) => {
      if (data && Array.isArray(data.apps)) {
        build(data.apps);
        show();
        window.dispatchEvent(new HashChangeEvent('hashchange'));
      }
    });
  return { LAYOUT, show, groups: () => groups, badges: () => ({ ...badges }), onBadge: (f) => onBadge.push(f),
    titleOf: (id) => { const s = document.getElementById(id), p = s && pages.find((x) => x.el === s.closest('.admin-page')); return p ? p.title : id; }, shown, badge, hide, ready, onBuild: (f) => onBuild.push(f), pages: () => pages.slice(), isBuilt: () => built };
})();
if (typeof window !== 'undefined') window.AL = AL;
