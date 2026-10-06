// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Themes page: every theme themes.js lists, and Auto, one line each. Choosing one draws
// this page in it at once, so the page is its own preview. In a block of its own: hub.js,
// loaded after it, has top-level names of the same kind.
{
  const list = document.getElementById('theme-list');
  const themes = window.HubThemes;
  if (list && themes) {
    const rows = [{ id: 'auto', emoji: '🌓', label: 'Auto', desc: "Light or dark, as this device is set (the hub's default)" },
      ...themes.list];
    const item = (t) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'theme-choice';
      b.setAttribute('role', 'radio');
      b.dataset.theme = t.id;
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = `${t.emoji} ${t.label}`;
      b.append(name);
      if (t.desc) {
        const desc = document.createElement('span');
        desc.className = 'desc';
        desc.textContent = t.desc;
        b.append(desc);
      }
      const li = document.createElement('li');
      li.append(b);
      return li;
    };
    list.replaceChildren(...rows.map(item));
    const mark = () => {
      const cur = themes.current();
      list.querySelectorAll('.theme-choice').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.theme === cur)));
    };
    list.addEventListener('click', (e) => {
      const b = e.target.closest('.theme-choice');
      if (b) themes.choose(b.dataset.theme);
    });
    document.addEventListener('hub-theme', mark);
    window.addEventListener('storage', mark);
    mark();
  }
}
