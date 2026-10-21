// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /notebook.html: open this account's own notebook (notebooks.py). Asks the hub to start it, waits
// until it answers, then goes to it on the notebooks' own origin.
const $ = (id) => document.getElementById(id);
const WORDS = {
  'signed-out': 'Sign in to open your notebook. A notebook belongs to an account.',
  off: 'Private notebooks disabled. This box has shared notes only.',
  unsupported: 'Unsupported web server. The web server of this box cannot serve private notebooks.',
  full: 'Too many notebooks are open. Try again after another notebook stops. An idle notebook stops after 30 minutes.',
  memory: 'Insufficient memory. The box cannot start another notebook. Try again after another notebook stops.',
  starting: 'Starting notebook',
  stopped: 'Starting notebook',
};
let tries = 0;
function target(st) {
  const https = location.protocol === 'https:';
  const port = st.ports ? st.ports[https ? 'https' : 'http'] : '';
  return `${location.protocol}//${location.hostname}${port ? `:${port}` : ''}/notebook/`;
}
function actions(st) {
  const box = $('nb-actions');
  box.replaceChildren();
  const link = (href, text) => { const anchor = document.createElement('a'); anchor.className = 'action-btn'; anchor.href = href; anchor.textContent = text; return anchor; };
  if (st.state === 'signed-out') box.append(link('/account.html?next=/notebook.html', 'Sign in'));
  if (st.state === 'full' || st.state === 'memory') {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'action-btn'; b.textContent = 'Try again';
    b.onclick = () => { tries = 0; open(); };
    box.append(b);
  }
  if (!['off', 'unsupported', 'signed-out'].includes(st.state)) box.append(' ', link('/api/notebook/download', 'Download notebook (ZIP)'));
}
async function open() {
  let st;
  try {
    const response = await fetch('/api/notebook', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Irate-Account': '1' }, body: JSON.stringify({ action: 'open' }) });
    st = await response.json();
  } catch (_) { $('nb-state').textContent = 'Could not reach the hub server.'; return; }
  if (st.state === 'ready') { $('nb-state').textContent = 'Opening notebook'; location.replace(target(st)); return; }
  $('nb-state').textContent = WORDS[st.state] || st.state;
  actions(st);
  // Starting: SilverBullet takes a few seconds on a small board.
  if (['starting', 'stopped'].includes(st.state)) {
    tries += 1;
    if (tries < 60) setTimeout(open, 1000);
    else $('nb-state').textContent = 'The notebook did not start. Try again later, or tell an administrator.';
  }
}
open();
