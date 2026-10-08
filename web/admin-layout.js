// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin's menu, built from one table (menu overhaul M4; Tom, 2026-10-08: the app-first layout
// "as it is now"). admin.html keeps every section as it was, each a <section class="admin-pane">
// with its own id, so admin.js binds to them unchanged; this file gathers them into pages:
//   Overview · Apps (a page per app, from /admin/apps: what each app's manifest says it owns)
//   · Folders · Moderation · Box · Doctors, the doctors last.
// The address names a section (#books, #backup): the page holding it opens, scrolled to it, so
// every old link and bookmark still lands. A section no page claims goes to "More" at the end
// rather than vanish (the jsdom tests check none does).
const AL = (() => {
  'use strict';
  // The box-wide groups. A page: its title, the sections it holds, its art if not the group's.
  // The Apps and Folders groups also get a page per app from the manifests (apps: true).
  const LAYOUT = [
    { name: 'Overview', art: 'controller', pages: [
      { title: 'Overview', sections: ['overview'] },
      { title: 'Setup steps', sections: ['welcome'], art: 'welcome-controller' }] },
    { name: 'Apps', art: 'librarian', apps: 'apps', pages: [
      { title: 'All apps, in order', sections: ['addons', 'apps'] }] },
    { name: 'Folders', art: 'librarian', apps: 'folders', pages: [] },
    { name: 'Moderation', art: 'librarian', pages: [
      { title: 'Moderation', sections: ['moderation'] },
      { title: 'Saved work and files', sections: ['saved'] }] },
    { name: 'Box', art: 'controller', pages: [
      { title: 'Accounts', sections: ['accounts', 'access'] },
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
  function groupHead(name) { return el('p', { class: 'admin-side-group', text: name }); }

  // The box-wide groups now; the apps' pages when /admin/apps answers (or without it, at worst).
  const slots = {};
  function build(apps) {
    list.replaceChildren();
    pages.forEach((p) => p.el.remove());
    pages.length = 0;
    placed.clear();
    for (const g of LAYOUT) {
      const entries = [];
      const fixed = g.pages.map((d) => page(g, d)).filter(Boolean);
      entries.push(...fixed);
      if (g.apps && apps) {
        for (let a of apps.filter((x) => (g.apps === 'folders') === x.folder && (x.switch || x.sections.length || x.folder || x.page))) {
          // Who opens it, who sees it: first on its page (M6; admin.js fills it from /admin/access).
          if (a.switch) {
            const id = 'app-access-' + a.id;
            if (!sections.has(id)) sections.set(id, el('section', { class: 'admin-pane app-access', id },
              el('h2', { text: 'Who opens it, who sees it' }), el('div', { class: 'access-block', 'data-app': a.id })));
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
            const at = a.switch ? 1 : 0;
            a = { ...a, sections: [...a.sections.slice(0, at), id, ...a.sections.slice(at)] };
          }
          const e = page({ ...g, art: a.art || g.art }, { id: 'app-' + a.id, app: a.id, title: a.name, icon: firstGlyph(a.icon), sections: a.sections, empty: false });
          if (e) entries.push(e);
        }
      }
      if (g.apps === 'apps') slots.apps = entries;
      if (!entries.length) continue;
      list.append(groupHead(g.name), ...entries.map((e) => e.link));
    }
    // Whatever no page claimed: kept, under Apps, so nothing is lost while the manifests catch up.
    const left = [...sections.keys()].filter((id) => !placed.has(id) && !/^(app-access-|app-width-|folder-)/.test(id));
    if (left.length) {
      const g = { name: 'More', art: '' };
      const extra = left.map((id) => page(g, { title: (sections.get(id).querySelector('h2') || {}).textContent || id, sections: [id] })).filter(Boolean);
      list.append(groupHead(g.name), ...extra.map((e) => e.link));
    }
    Object.keys(badges).forEach(paint);
    hiddenLinks.forEach((id) => hide(id, true));
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
  }
  function paint(id) {
    const s = document.getElementById(id);
    const p = s && pages.find((x) => x.el === (s.classList.contains('admin-page') ? s : s.closest('.admin-page')));
    if (!p) return;
    const ids = [...p.el.querySelectorAll(':scope > .admin-pane')].map((x) => x.id);
    const word = ids.map((x) => badges[x]).find(Boolean);
    if (word) p.link.dataset.badge = word; else delete p.link.dataset.badge;
  }

  // Hide or show the sidebar entry of the page holding a section (kept across rebuilds).
  function hide(id, yes) {
    if (yes) hiddenLinks.add(id); else hiddenLinks.delete(id);
    const s = document.getElementById(id);
    const p = s && pages.find((x) => x.el === s.closest('.admin-page'));
    if (p) p.link.hidden = !!yes;
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
  return { LAYOUT, show, shown, badge, hide, ready, onBuild: (f) => onBuild.push(f), pages: () => pages.slice(), isBuilt: () => built };
})();
if (typeof window !== 'undefined') window.AL = AL;
