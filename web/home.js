// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The landing page's two tabs, shoutbox and board, and the expand to a full-page view.
// The fragment picks the tab: #board or a thread (#t12, which board.js handles), and
// anything else is the shoutbox. ?full shows only the open tab, filling the window, with
// the header and tiles hidden; Escape or the button brings them back.
(() => {
  const tabs = document.querySelectorAll('.home-tabs [data-tab]');
  const panes = { shout: document.getElementById('tab-shout'), board: document.getElementById('tab-board') };
  const expand = document.getElementById('tab-expand');
  if (!expand || !panes.shout || !panes.board) return;

  // A tab the hub left out for this visitor (data-off) is never chosen.
  const on = (name) => !panes[name].hasAttribute('data-off');
  const want = () => (/^#(board|t\d+)$/.test(location.hash) ? 'board' : 'shout');
  const currentTab = () => (on(want()) ? want() : on('shout') ? 'shout' : on('board') ? 'board' : null);
  if (!on('shout') && !on('board')) { document.querySelector('.home-tabs').hidden = true; return; }

  function showTab() {
    const tab = currentTab();
    for (const [name, pane] of Object.entries(panes)) pane.hidden = name !== tab;
    tabs.forEach((a) => {
      const on = a.dataset.tab === tab;
      a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  }

  function setFull(full) {
    document.body.classList.toggle('full', full);
    expand.textContent = full ? '⤡ Hub view' : '⤢ Expand';
    expand.title = full ? 'Show the hub around it again' : 'Full-page view of this tab';
    const params = new URLSearchParams(location.search);
    if (full) params.set('full', ''); else params.delete('full');
    const query = params.toString().replace(/=(?=&|$)/g, '');
    history.replaceState(null, '', location.pathname + (query ? `?${query}` : '') + location.hash);
  }

  expand.addEventListener('click', () => setFull(!document.body.classList.contains('full')));
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
    if (e.key === 'Escape' && !typing && document.body.classList.contains('full')) setFull(false);
  });
  window.addEventListener('hashchange', showTab);
  showTab();
  setFull(new URLSearchParams(location.search).has('full'));
})();
