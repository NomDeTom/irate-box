// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /notebook.html: open this login's own notebook (notebooks.py). Asks the hub to start it, waits
// until it answers, then goes to it on the notebooks' own origin.
const $ = (id) => document.getElementById(id);
const WORDS = {
  'signed-out': 'Sign in first: a notebook belongs to an account.',
  off: 'This box has shared notes only: no notebook of your own.',
  unsupported: 'This box\'s web server cannot serve notebooks of their own.',
  full: 'Too many notebooks are open right now. Try again in a while: one stops half an hour after its owner last used it.',
  memory: 'The box is short of memory right now, so no more notebooks are starting. Try again in a while.',
  starting: 'Starting your notebook…',
  stopped: 'Starting your notebook…',
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
  const link = (href, text) => { const a = document.createElement('a'); a.className = 'action-btn'; a.href = href; a.textContent = text; return a; };
  if (st.state === 'signed-out') box.append(link('/account.html?next=/notebook.html', 'Sign in'));
  if (st.state === 'full' || st.state === 'memory') {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'action-btn'; b.textContent = 'Try again';
    b.onclick = () => { tries = 0; open(); };
    box.append(b);
  }
  if (!['off', 'unsupported', 'signed-out'].includes(st.state)) box.append(' ', link('/api/notebook/download', 'Download it (zip)'));
}
async function open() {
  let st;
  try {
    const r = await fetch('/api/notebook', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Irate-Account': '1' }, body: JSON.stringify({ action: 'open' }) });
    st = await r.json();
  } catch (_) { $('nb-state').textContent = 'Could not reach the hub.'; return; }
  if (st.state === 'ready') { $('nb-state').textContent = 'Opening your notebook…'; location.replace(target(st)); return; }
  $('nb-state').textContent = WORDS[st.state] || st.state;
  actions(st);
  // Starting: SilverBullet takes a few seconds on a small board.
  if (['starting', 'stopped'].includes(st.state)) {
    tries += 1;
    if (tries < 60) setTimeout(open, 1000);
    else $('nb-state').textContent = 'Your notebook did not start. Try again later, or tell the box\'s owner.';
  }
}
open();
