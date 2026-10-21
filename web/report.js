// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Reporting a shoutbox message or a forum post: anyone can report, the admin decides what counts.
// A small ⚑ beside each one opens the reasons the owner offers; one tap sends it to /api/report, and the owner sees it on /admin → Moderation.
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
    flag.title = 'Report this to the box’s administrator';
    flag.setAttribute('aria-label', 'Report this content');
    flag.setAttribute('aria-expanded', 'false');
    const menu = document.createElement('span');
    menu.className = 'report-reasons';
    menu.hidden = true;
    (reasons && reasons.length ? reasons : ['other']).forEach((reason) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = reason;
      b.addEventListener('click', async () => {
        menu.replaceChildren(document.createTextNode('Sending report…'));
        try {
          const res = await fetch('/api/report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ app, ref, reason: reason }) });
          const d = await res.json().catch(() => ({}));
          // The answer as a message (msg), said in the reader's language where the page has AW; else its English.
          const said = d.msg && window.AW ? window.AW.say(d.msg) : d.message || d.error;
          menu.replaceChildren(document.createTextNode(said || (res.ok ? 'Report sent.' : 'Report could not be sent.')));
        } catch (_) { menu.replaceChildren(document.createTextNode('Report could not be sent.')); }
      });
      menu.append(b);
    });
    flag.addEventListener('click', () => { menu.hidden = !menu.hidden; flag.setAttribute('aria-expanded', String(!menu.hidden)); });
    wrap.append(flag, menu);
    return wrap;
  };
})();
