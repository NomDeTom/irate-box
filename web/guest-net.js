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
    'users-web': 'The administrator lets signed-in users reach the web through this box.',
    'sheet-web': 'The administrator lets guests reach the web through this box.',
    'sheet-all': 'The administrator lets guests reach the internet through this box.',
  };
  function show(data) {
    if (!data.here || data.out || !words[data.level]) { box.hidden = true; return; }
    const p = document.createElement('p');
    p.textContent = `${words[data.level]} Access lasts ${data.hours} hours on this device. Your traffic uses the box's connection, which belongs to the administrator. `
      + 'The box does not log the sites you visit. Other systems on the route, such as the internet provider, can still log them.';
    const act = document.createElement(data.level === 'users-web' && !data.signed_in ? 'a' : 'button');
    act.className = 'action-btn primary';
    if (act.tagName === 'A') {
      act.href = '/account.html?next=%2F';
      act.textContent = 'Sign in for web access';
    } else {
      act.type = 'button';
      act.textContent = data.level === 'sheet-all' ? 'Enable internet access' : 'Enable web access';
      act.addEventListener('click', async () => {
        act.disabled = true;
        try {
          const response = await fetch('/api/guest-net', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agree: true }) });
          const j = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(j.error || `HTTP ${response.status}`);
          p.textContent = `Done. This device can reach ${data.level === 'sheet-all' ? 'the internet' : 'the web'} in a few seconds.`;
          act.remove();
        } catch (err) { p.textContent = `Request failed: ${err.message}. Select the button to try again.`; act.disabled = false; }
      });
    }
    box.replaceChildren(p, act);
    box.hidden = false;
  }
  fetch('/api/guest-net').then((response) => (response.ok ? response.json() : null)).then((data) => data && show(data)).catch(() => {});
})();
