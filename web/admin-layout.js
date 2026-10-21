// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin's menu, built from one table. admin.html keeps every section as it was, each a <section class="admin-pane">
// with its own id, so admin.js binds to them unchanged; this file gathers them into pages:
//   Overview · Apps (a page per app, from /admin/apps: what each app's manifest says it owns)
//   · Hub layout (what visitors see: tiles, folders, status widgets, appearance) · Moderation · System
//   · Doctors, the doctors last.
// The address names a section (#books, #backup): the page holding it opens, scrolled to it, so
// every old link and bookmark still lands. A section no page claims goes to "More" at the end
// rather than vanish (the jsdom tests check none does).
const AL = (() => {
  'use strict';
  // The box-wide groups. A page: its title, the sections it holds, its art if not the group's.
  // The Apps and Hub layout groups also get a page per app or folder from the manifests (apps).
  // A group with `under` keeps those pages off the sidebar: they open from the page holding that
  // section (the Folders index), whose link stands for them.
  const LAYOUT = [
    // flat: no group head to fold; the links stand at the top of the sidebar.
    { name: 'Overview', art: 'controller', flat: true, pages: [
      { title: 'Overview', sections: ['overview'] },
      { title: 'Setup steps', sections: ['welcome'], art: 'welcome-controller' }] },
    { name: 'Apps', art: 'librarian', apps: 'apps', pages: [
      { title: 'All apps', sections: ['app-inventory', 'apps', 'addons'] }] },
    { name: 'Hub layout', art: 'librarian', apps: 'folders', under: 'folder-index', pages: [
      { title: 'Home tiles', sections: ['tile-order'] },
      { title: 'Folders', sections: ['folder-index'] },
      { title: 'Status widgets', sections: ['status-tiles'], art: 'controller' },
      { title: 'Appearance', sections: ['appearance'], art: 'controller' }] },
    { name: 'Moderation', art: 'librarian', pages: [
      { title: 'Moderation', sections: ['moderation'] },
      { title: 'User data', sections: ['made'] }] },
    { name: 'System', art: 'controller', pages: [
      { title: 'Accounts', sections: ['accounts', 'access'] },
      { title: 'Network', sections: ['network'] },
      { title: 'Security', sections: ['security'] },
      { title: 'The librarian', sections: ['sources'] },
      { title: 'Toolkits', sections: ['toolkits'], art: 'workbench' },
      { title: 'Clock', sections: ['clock'] },
      { title: 'Recovery and monitoring', sections: ['recovery'] },
      { title: 'Updates', sections: ['updates'] },
      { title: 'Backup and transfer', sections: ['backup'] }] },
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
  const sections = new Map([...document.querySelectorAll('.admin-main > .admin-pane')].map((section) => [section.id, section]));
  const placed = new Set();
  const pages = [];
  const badges = {};
  const hiddenLinks = new Set();
  const onBadge = [];  // told when any section's word changes (Overview's Needs attention)
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
    const adminPage = el('section', { class: 'admin-page', id, 'data-art': def.art || SECTION_ART[ids[0]] || group.art || '',
      'aria-label': def.title }, el('h2', { class: 'admin-page-title', text: def.title }));
    if (ids.length === 1 && !def.app) adminPage.classList.add('solo');  // an app's page keeps the app's name over its sections
    ids.forEach((sid) => { placed.add(sid); adminPage.append(sections.get(sid)); });
    // A section headed with the page's own title (Git on Git's page) doesn't say it twice: its
    // heading stays, for its aria-labelledby, but isn't shown.
    adminPage.querySelectorAll(':scope > .admin-pane > h2').forEach((h) => h.classList.toggle('same-as-page', !adminPage.classList.contains('solo') && h.textContent.trim() === def.title));
    adminPage.hidden = true;
    main.append(adminPage);
    const link = el('a', { href: '#' + (def.app ? id : ids[0] || id), 'data-page': id }, def.icon ? el('span', { class: 'side-icon', 'aria-hidden': 'true', text: def.icon }) : null, def.title);
    const entry = { id, el: adminPage, link, title: def.title, group: group.name };
    pages.push(entry);
    return entry;
  }
  function firstGlyph(text) {
    if (!text) return null;
    if (window.Intl && Intl.Segmenter) { const g = new Intl.Segmenter().segment(text)[Symbol.iterator]().next().value; return g ? g.segment : null; }
    return [...text][0];
  }
  // The sidebar's groups fold, open by default. Each head is a button over its links; the current page's
  // group is always open; a folded group shows its links' badges on its head. Auto-collapse keeps
  // only the current group open. Both are this viewer's own, kept in the browser (a convenience,
  // not a setting: without storage every group simply starts open).
  const FOLD_KEY = 'irate-admin-folded', AUTO_KEY = 'irate-admin-autocollapse';
  const stored = (k, fallback) => { try { const v = localStorage.getItem(k); return v == null ? fallback : JSON.parse(v); } catch (_) { return fallback; } };
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
    const words = [...g.box.querySelectorAll('a[data-badge]:not([hidden])')].map((link) => link.dataset.badge);
    // Counts add up (3 to fix and 1 to fix: 4); other words (!, new, ready) follow, each once.
    const n = words.filter((x) => /^\d+$/.test(x)).reduce((total, x) => total + Number(x), 0);
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
    const cur = pages.find((adminPage) => adminPage.link.getAttribute('aria-current') === 'page');
    fold(cur ? cur.group : null);
  });
  autoLabel();

  // The box-wide groups now; the apps' pages when /admin/apps answers (or without it, at worst).
  const slots = {};
  let appList = null;  // /admin/apps, once it answers: the All apps list and the Folders index read it
  function build(apps) {
    list.replaceChildren();
    groups.clear();
    pages.forEach((adminPage) => adminPage.el.remove());
    pages.length = 0;
    placed.clear();
    for (const g of LAYOUT) {
      const entries = [];
      const fixed = g.pages.map((groupPage) => page(g, groupPage)).filter(Boolean);
      entries.push(...fixed);
      if (g.apps && apps) {
        for (const app of apps.filter((x) => (g.apps === 'folders') === x.folder && (x.switch || x.seen || x.sections.length || x.folder || x.page || x.addon))) {
          // An app's page, in one order: what it is and what can be done now; its own settings
          // and content; then who can open it, its tile (a summary: Home tiles arranges tiles) and its
          // maintenance (updates, installation, Remove), each open to start and folded by its heading. Only the parts that apply.
          const made = (id, cls, title, build, fold) => {
            if (!sections.has(id)) {
              const body = build();
              sections.set(id, fold
                ? el('section', { class: `admin-pane ${cls}`, id }, el('details', { class: 'sec-fold app-fold', open: true }, el('summary', {}, el('h2', { text: title })), body))
                : el('section', { class: `admin-pane ${cls}`, id }, el('h2', { text: title }), body));
            }
            return id;
          };
          const ids = [];
          if (!app.folder) ids.push(made('app-head-' + app.id, 'app-head', 'Status', () => el('div', { class: 'head-block', 'data-app': app.id })));
          if (app.folder) ids.push(made('folder-' + app.id, 'folder-section', 'Folder contents', () => el('div', { class: 'folder-block', 'data-folder': app.id })));
          // An app drawn on the hub page: its width, before what it owns.
          if (app.width) ids.push(made('app-width-' + app.id, 'app-width', 'Width', () => el('div', { class: 'width-block', 'data-key': app.width })));
          ids.push(...app.sections);
          // What was reported there, for an app drawn on the hub page.
          if (app.page) ids.push(made('app-flagged-' + app.id, 'app-flagged', 'Reported content', () => el('div', { class: 'flagged-block', 'data-app': app.id })));
          if (app.switch) ids.push(made('app-access-' + app.id, 'app-access', 'Access', () => el('div', { class: 'access-block', 'data-app': app.id }), true));
          if (app.switch || app.seen) ids.push(made('app-tile-' + app.id, 'app-tile', 'Hub tile', () => el('div', { class: 'tile-block', 'data-app': app.id }), true));
          // Maintenance: its own updates and its installation, each where admin.js draws it.
          if (app.updates || app.addon) {
            ids.push(made('app-maint-' + app.id, 'app-maint', 'Maintenance', () => el('div', {},
              app.updates ? el('div', { class: 'app-updates', id: 'app-updates-' + app.id }, el('h3', { text: 'App updates' }), el('div', { class: 'updates-block', 'data-app': app.id })) : null,
              app.addon ? el('div', { class: 'app-install', id: 'app-install-' + app.id }, el('h3', { text: 'Installation' }), el('div', { class: 'addon-block', 'data-addon': app.id },
                el('p', { class: 'setting-desc', text: 'Loading installation status.' }))) : null), true));
          }
          const entry = page({ ...g, art: app.art || g.art }, { id: 'app-' + app.id, app: app.id, title: app.name, icon: firstGlyph(app.icon), sections: ids, empty: false });
          if (entry && g.under) entry.parent = fixed.find((adminPage) => adminPage.el.querySelector('#' + g.under)) || null;
          if (entry) entries.push(entry);
        }
      }
      if (g.apps === 'apps') slots.apps = entries;
      if (!entries.length) continue;
      const links = entries.filter((entry) => !entry.parent).map((entry) => entry.link);
      if (g.flat) list.append(el('div', { class: 'admin-side-links', role: 'group', 'aria-label': g.name }, links));
      else list.append(...group(g.name, links));
    }
    // Whatever no page claimed: kept on pages of its own so nothing is lost. Listed under "More" only once
    // the apps' list has come (or failed): before it, every app's section is unclaimed for a moment.
    const left = [...sections.keys()].filter((id) => !placed.has(id) && !/^(app-head-|app-access-|app-tile-|app-maint-|app-width-|folder-|app-flagged-)/.test(id));
    if (left.length) {
      const g = { name: 'More', art: '' };
      const extra = left.map((id) => page(g, { title: (sections.get(id).querySelector('h2') || {}).textContent || id, sections: [id] })).filter(Boolean);
      if (apps) list.append(...group(g.name, extra.map((entry) => entry.link)));
    }
    Object.keys(badges).forEach(paint);
    hiddenLinks.forEach((id) => hide(id, true));
    markUnsaved();
    list.append(el('p', { class: 'side-foot' }, autoBtn));
    built = true;
    onBuild.forEach((callback) => { try { callback(); } catch (error) { console.error(error); } });
  }

  // The page for an address: the page itself, or the one holding the section it names.
  function pageFor(hash) {
    const target = hash && document.getElementById(hash.slice(1));
    const adminPage = target && (target.classList.contains('admin-page') ? target : target.closest('.admin-page'));
    return { page: pages.find((x) => x.el === adminPage) || pages[0], target };
  }
  function show() {
    if (!pages.length) return;
    const { page: cur, target } = pageFor(location.hash);
    pages.forEach((adminPage) => { adminPage.el.hidden = adminPage !== cur; });
    // A page without a sidebar link of its own (a folder's) lights the link it opens from.
    pages.forEach((adminPage) => { if (adminPage === cur || adminPage === cur.parent) adminPage.link.setAttribute('aria-current', 'page'); else adminPage.link.removeAttribute('aria-current'); });
    main.dataset.art = cur.el.dataset.art || '';
    fold(cur.group);
    document.getElementById('admin-current').textContent = cur.title;
    document.title = `${cur.title} · Hub admin`;
    side.classList.remove('open');
    menu.setAttribute('aria-expanded', 'false');
    // A target inside a folded part opens it (a link to an app's installation or access).
    for (let details = target && target.closest('details'); details; details = details.parentElement && details.parentElement.closest('details')) details.open = true;
    if (target && target !== cur.el && target !== cur.el.querySelector('.admin-pane') && target.scrollIntoView) target.scrollIntoView({ block: 'start' });
    else window.scrollTo(0, 0);
  }
  // Is a section on the page showing? (admin.js's loaders that wait until their section is seen.)
  function shown(id) {
    const section = document.getElementById(id);
    const adminPage = section && section.closest('.admin-page');
    return !!adminPage && !adminPage.hidden;
  }
  // A word beside a section's sidebar entry ("new", "ready", a count): on its page's entry, the
  // first section's word that has one.
  function badge(id, text) {
    if (text) badges[id] = text; else delete badges[id];
    paint(id);
    onBadge.forEach((callback) => { try { callback(); } catch (error) { console.error(error); } });
  }
  function paint(id) {
    const section = document.getElementById(id);
    const adminPage = section && pages.find((x) => x.el === (section.classList.contains('admin-page') ? section : section.closest('.admin-page')));
    if (!adminPage) return;
    const ids = [...adminPage.el.querySelectorAll(':scope > .admin-pane')].map((x) => x.id);
    const word = ids.map((x) => badges[x]).find(Boolean);
    if (word) adminPage.link.dataset.badge = word; else delete adminPage.link.dataset.badge;
    rollUp(adminPage.group);
  }

  // Hide or show the sidebar entry of the page holding a section (kept across rebuilds).
  function hide(id, yes) {
    if (yes) hiddenLinks.add(id); else hiddenLinks.delete(id);
    const section = document.getElementById(id);
    const adminPage = section && pages.find((x) => x.el === section.closest('.admin-page'));
    if (adminPage) { adminPage.link.hidden = !!yes; rollUp(adminPage.group); }
  }

  // ---- Unsaved changes (AW.unsaved: the sections changed and not saved) ----
  // A mark on the sidebar entry of each page holding one, and on a group's head, so folding doesn't hide it.
  const unsavedOn = (adminPage) => (typeof AW === 'undefined' ? [] : AW.unsaved().filter((x) => x.el && adminPage.el.contains(x.el)));
  function markUnsaved() {
    for (const adminPage of pages) {
      const these = unsavedOn(adminPage);
      adminPage.link.classList.toggle('unsaved', !!these.length);
      if (these.length) adminPage.link.title = `Unsaved changes: ${these.map((x) => x.label || 'a setting').join(', ')}`;
      else adminPage.link.removeAttribute('title');
    }
    for (const g of groups.values()) g.head.classList.toggle('unsaved', !!g.box.querySelector('a.unsaved:not([hidden])'));
  }
  if (typeof document !== 'undefined') document.addEventListener('aw-unsaved', markUnsaved);
  // Leaving a page with unsaved changes asks first (its drafts are kept either way, as pages are hidden, not
  // destroyed); No stays on it. Closing the tab or reloading asks the browser's own question.
  let shownHash = location.hash;
  window.addEventListener('hashchange', (event) => {
    const from = pageFor(shownHash).page, to = pageFor(location.hash).page;
    const left = from && from !== to ? unsavedOn(from) : [];
    if (left.length && !window.confirm(`${from.title} has unsaved changes (${left.map((x) => x.label || 'a setting').join(', ')}). `
      + 'Go to the other page anyway? The changes stay on this page, not saved, until you save or discard them.')) {
      history.replaceState(null, '', shownHash || location.pathname);
      event.stopImmediatePropagation(); // the page's loaders are not told: nothing changed
      return;
    }
    shownHash = location.hash;
    show();
  }, true);
  window.addEventListener('beforeunload', (event) => {
    if (typeof AW === 'undefined' || !AW.unsaved().length) return;
    event.preventDefault();
    event.returnValue = '';
  });

  menu.addEventListener('click', () => { const open = side.classList.toggle('open'); menu.setAttribute('aria-expanded', String(open)); });
  build(null);
  show();
  // The apps' pages: a fetch away. When they're in, the page is rebuilt and the loaders told (a
  // hashchange), as a section they wait for may only now be showing.
  const ready = fetch('/admin/apps', { headers: { 'X-Irate-Admin': '1' } })
    .then((response) => (response.ok ? response.json() : null)).catch(() => null)
    .then((data) => {
      appList = data && Array.isArray(data.apps) ? data.apps : [];
      build(appList);
      show();
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
  return { LAYOUT, show, groups: () => groups, badges: () => ({ ...badges }), onBadge: (callback) => onBadge.push(callback),
    titleOf: (id) => { const section = document.getElementById(id), adminPage = section && pages.find((x) => x.el === section.closest('.admin-page')); return adminPage ? adminPage.title : id; }, shown, badge, hide, ready, onBuild: (callback) => onBuild.push(callback), pages: () => pages.slice(), isBuilt: () => built, apps: () => appList };
})();
if (typeof window !== 'undefined') window.AL = AL;
