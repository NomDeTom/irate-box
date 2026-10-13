// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Themes page: every theme themes.js lists, and Auto, one line each; then the colours of light
// and dark (the box's preset, Standard, Increased contrast). Choosing one draws this page in it at
// once, so the page is its own preview. In a block of its own: hub.js,
// loaded after it, has top-level names of the same kind.
{
  const list = document.getElementById('theme-list');
  const themes = window.HubThemes;
  if (list && themes) {
    const rows = [{ id: 'auto', emoji: '🌓', label: 'Auto', desc: "Light or dark, as this device is set (the hub's default)" },
      ...themes.list];
    const item = (t, key = 'theme') => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'theme-choice';
      b.setAttribute('role', 'radio');
      b.dataset[key] = t.id;
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
    list.replaceChildren(...rows.map((t) => item(t)));
    const pals = document.getElementById('palette-list');
    if (pals && themes.palettes) pals.replaceChildren(...themes.palettes.map((t) => item(t, 'palette')));
    const mark = () => {
      const cur = themes.current();
      const own = themes.byId(cur) && !themes.byId(cur).main;   // a theme of its own colours: no palette in use
      list.querySelectorAll('.theme-choice').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.theme === cur)));
      if (pals) pals.querySelectorAll('.theme-choice').forEach((b) => b.setAttribute('aria-checked', String(!own && b.dataset.palette === themes.palette())));
    };
    list.addEventListener('click', (e) => {
      const b = e.target.closest('.theme-choice');
      if (b) themes.choose(b.dataset.theme);
    });
    if (pals) {
      pals.addEventListener('click', (e) => {
        const b = e.target.closest('.theme-choice');
        if (b) themes.choosePalette(b.dataset.palette);
      });
    }
    document.addEventListener('hub-theme', mark);
    window.addEventListener('storage', mark);
    mark();
  }
}
