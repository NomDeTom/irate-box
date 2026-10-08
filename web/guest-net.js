// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The sign-in sheet's offer of the internet (root/share.py), for a guest on the box's hotspot when
// the owner shares the box's connection one device at a time: what it means, then one tap (or,
// for users only, a sign-in). Nothing shows when the owner shares nothing, shares with everyone,
// or this device is already out.
(() => {
  const box = document.getElementById('guest-net');
  if (!box) return;
  const words = {
    'users-web': 'The owner lets signed-in users reach the web through this box.',
    'sheet-web': 'The owner lets guests reach the web through this box.',
    'sheet-all': 'The owner lets guests reach the internet through this box.',
  };
  function show(d) {
    if (!d.here || d.out || !words[d.level]) { box.hidden = true; return; }
    const p = document.createElement('p');
    p.textContent = `${words[d.level]} Your traffic leaves as the box's, for ${d.hours} hours on this device; `
      + 'the box keeps no record of where you go, but its owner\'s connection carries it.';
    const act = document.createElement(d.level === 'users-web' && !d.signed_in ? 'a' : 'button');
    act.className = 'action-btn primary';
    if (act.tagName === 'A') {
      act.href = '/account.html?next=%2F';
      act.textContent = 'Sign in to use the internet';
    } else {
      act.type = 'button';
      act.textContent = 'Use the internet';
      act.addEventListener('click', async () => {
        act.disabled = true;
        try {
          const r = await fetch('/api/guest-net', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agree: true }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
          p.textContent = 'Done: this device can reach the internet now (give it a few seconds).';
          act.remove();
        } catch (err) { p.textContent = `Not this time: ${err.message}`; act.disabled = false; }
      });
    }
    box.replaceChildren(p, act);
    box.hidden = false;
  }
  fetch('/api/guest-net').then((r) => (r.ok ? r.json() : null)).then((d) => d && show(d)).catch(() => {});
})();
