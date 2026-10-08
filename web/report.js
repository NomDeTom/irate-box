// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Reporting a shoutbox message or a forum post (menu overhaul M10; Tom, 2026-10-08: "anyone can
// report a post - admin decides what counts"). A small ⚑ beside each one opens the reasons the
// owner offers; one tap sends it to /api/report, and the owner sees it on /admin → Moderation.
// The hub takes one report per visitor per post.
const reportButton = (() => {
  'use strict';
  return function reportButton(app, ref, reasons) {
    const wrap = document.createElement('span');
    wrap.className = 'report';
    const flag = document.createElement('button');
    flag.type = 'button';
    flag.className = 'report-flag';
    flag.textContent = '⚑';
    flag.title = 'Report this to the box’s owner';
    flag.setAttribute('aria-label', 'Report this');
    flag.setAttribute('aria-expanded', 'false');
    const menu = document.createElement('span');
    menu.className = 'report-reasons';
    menu.hidden = true;
    (reasons && reasons.length ? reasons : ['other']).forEach((r) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = r;
      b.addEventListener('click', async () => {
        menu.replaceChildren(document.createTextNode('Sending…'));
        try {
          const res = await fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ app, ref, reason: r }) });
          const d = await res.json().catch(() => ({}));
          menu.replaceChildren(document.createTextNode(d.message || d.error || (res.ok ? 'Reported.' : 'Could not send.')));
        } catch (_) { menu.replaceChildren(document.createTextNode('Could not send.')); }
      });
      menu.append(b);
    });
    flag.addEventListener('click', () => { menu.hidden = !menu.hidden; flag.setAttribute('aria-expanded', String(!menu.hidden)); });
    wrap.append(flag, menu);
    return wrap;
  };
})();
