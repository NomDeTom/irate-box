// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /account.html: sign in or out, create an account
// (as the admin allows: open, or asking the admin), set a password with a one-time code, change
// it. Over plain HTTP it says what that means, or (when the admin has prevented it) takes no
// password at all and points at the certificate and the HTTPS page.
const $ = (id) => document.getElementById(id);
let view = null;
// Where to go once logged in: an app in users mode sends people here with ?next=<its path>. Only
// a path on this origin, never another site (an open redirect).
const next = (() => {
  const n = new URLSearchParams(location.search).get('next') || '';
  if (!n.startsWith('/')) return '';
  // Resolved as the browser would (it drops tabs and newlines, so "/\t/elsewhere" is "//elsewhere"),
  // and kept only if it is still this origin.
  try {
    const u = new URL(n, location.origin);
    return u.origin === location.origin ? u.pathname + u.search + u.hash : '';
  } catch { return ''; }
})();

function note(text, ok) {
  $('acct-note').textContent = text;
  $('acct-note').className = ok ? 'ok' : 'bad';
}

async function post(body) {
  const response = await fetch('/api/account', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Irate-Account': '1' }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function render(data) {
  view = data;
  const plain = !data.https;
  const prevented = plain && data.http === 'prevented';
  $('acct-http').hidden = !plain || data.http === 'permitted';
  $('acct-http-text').textContent = prevented
    ? 'This page is on plain HTTP, and this box takes passwords only over HTTPS: anyone on the WiFi could read one sent here.'
    : 'This page is on plain HTTP: a password sent from it can be read by anyone on the WiFi. Use one you use nowhere else, or the HTTPS page.';
  $('acct-https-link').href = `https://${location.host}${location.pathname}`;
  document.querySelectorAll('#acct-guest input, #acct-guest button, #acct-me input, #acct-me button[type=submit]').forEach((n) => { n.disabled = prevented; });
  $('acct-me').hidden = !data.me;
  $('acct-guest').hidden = !!data.me;
  if (data.me) {
    $('acct-subtitle').textContent = `Signed in as ${data.me.name}.`;
    $('acct-me-text').replaceChildren(data.me.role === 'admin' ? `${data.me.name}: an administrator of this box. ` : `${data.me.name}: a user of this box.`);
    if (data.me.role === 'admin') {
      const link = document.createElement('a'); link.href = '/admin/'; link.textContent = 'Open /admin';
      $('acct-me-text').append(link);
    }
    showPrefs(data.prefs || {}, data.names_to);
    loadNotebook();
    return;
  }
  const signup = data.signup === 'open' || data.signup === 'apply';
  // One login for everyone: the box's admins sign in here too, sign-up or not.
  const forAdmin = !!next && /^\/(admin|term|sync)(\/|$)/.test(next);
  $('acct-subtitle').textContent = forAdmin ? 'That page is for the box\'s administrator. Sign in with an administrator account.'
    : data.signup === 'off' ? 'This box does not accept new accounts. Administrators sign in here.'
      : next ? 'That page is for the box\'s users. Sign in or create an account.' : 'Sign in or create an account.';
  $('acct-signup-form').hidden = !signup;
  // From a locked tile that offers sign-up: straight to the form.
  if (signup && location.hash === '#signup') { $('acct-signup-form').scrollIntoView?.({ block: 'center' }); ($('acct-signup-form').elements.name || {}).focus?.(); }
  $('acct-signup-title').textContent = data.signup === 'apply' ? 'Request an account' : 'Create account';
  $('acct-signup-button').textContent = data.signup === 'apply' ? 'Request account' : 'Create account';
  $('acct-login-form').hidden = false;
  $('acct-code-form').hidden = false;   // an admin's one-time code (a new account, a reset) works with sign-up off too
  $('acct-signup-desc').textContent = data.signup === 'apply' ? 'An administrator must approve each request. Until then, the account cannot sign in.' : 'The account works immediately. It does not need an e-mail address or a real name.';

}

async function load() {
  try { render(await (await fetch('/api/account')).json()); } catch (_) { $('acct-subtitle').textContent = 'Could not reach the hub.'; }
}

function form(id, build, done) {
  $(id).addEventListener('submit', async (event) => {
    event.preventDefault();
    const formEl = event.target;
    try {
      const reply = await post(build(formEl));
      formEl.reset();
      done(reply);
    } catch (err) { note(err.message, false); }
  });
}

form('acct-login-form', (formEl) => ({ action: 'login', name: formEl.elements.name.value, password: formEl.elements.password.value }), () => {
  // On to the page that sent them here, or back to the hub, whose header then has their buttons (Admin for an administrator).
  location.assign(next || '/');
});
form('acct-signup-form', (formEl) => ({ action: 'signup', name: formEl.elements.name.value, password: formEl.elements.password.value }), (data) => {
  note(data.state === 'asked' ? 'Requested. You can sign in after an administrator approves it.' : 'Account created. You are signed in.', true);
  load();
});
form('acct-code-form', (formEl) => ({ action: 'code', code: formEl.elements.code.value, password: formEl.elements.password.value }), (data) => note(`Password set for ${data.name}. Sign in with it.`, true));
form('acct-password-form', (formEl) => ({ action: 'password', old: formEl.elements.old.value, new: formEl.elements.new.value }), () => note('Password changed. Your other devices are signed out.', true));
$('acct-logout').addEventListener('click', async () => {
  try { await post({ action: 'logout' }); note('Signed out.', true); load(); } catch (err) { note(err.message, false); }
});
// ---- Your settings: held until Save, theirs alone.
const HUES = [0, 40, 130, 210, 280];
let saved = {}, draft = {};
function showPrefs(p, namesTo) {
  saved = JSON.parse(JSON.stringify(p));
  draft = JSON.parse(JSON.stringify(p));
  $('acct-names-to').textContent = namesTo === 'admin' ? 'Only administrators see names. The administrator chose this.' : 'Signed-in users see names. Guests see only the number. The administrator chose this.';
  drawPrefs();
}
function drawPrefs() {
  document.querySelectorAll('#acct-prefs-form [data-pref]').forEach((b) => {
    const on = !!draft[b.dataset.pref];
    b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); b.textContent = on ? 'On' : 'Off';
  });
  $('acct-hues').replaceChildren(...HUES.map((h) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip' + (draft.hue === h ? ' selected' : ''); b.textContent = '●';
    b.style.color = `hsl(${h} var(--author-s) var(--author-l))`; b.setAttribute('aria-label', `hue ${h}`); b.setAttribute('aria-pressed', String(draft.hue === h));
    b.addEventListener('click', () => { draft.hue = h; drawPrefs(); });
    return b;
  }));
  const POST_APPS = [['shoutbox', 'Shoutbox'], ['board', 'Forum'], ['saves', 'Saved work'], ['drop', 'File drop']];
  const SEEN = [['everyone', 'everyone here'], ['users', 'signed-in people'], ['me', 'only me']];
  draft.posts = draft.posts || {};
  $('acct-posts').replaceChildren(...POST_APPS.map(([app, label]) => {
    const row = document.createElement('div');
    row.className = 'acct-pref';
    const group = document.createElement('span');
    group.className = 'chip-group'; group.setAttribute('role', 'group'); group.setAttribute('aria-label', label);
    SEEN.forEach(([v, t]) => {
      const b = document.createElement('button');
      const cur = draft.posts[app] || 'everyone';
      b.type = 'button'; b.className = 'chip' + (cur === v ? ' selected' : ''); b.textContent = t; b.setAttribute('aria-pressed', String(cur === v));
      b.addEventListener('click', () => { draft.posts = { ...draft.posts, [app]: v }; drawPrefs(); });
      group.append(b);
    });
    row.append(Object.assign(document.createElement('span'), { textContent: label }), group);
    return row;
  }));
  const email = $('acct-prefs-form').elements.email;
  if (document.activeElement !== email) email.value = draft.email || '';
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  $('acct-prefs-save').disabled = $('acct-prefs-discard').disabled = !dirty;
}
document.querySelectorAll('#acct-prefs-form [data-pref]').forEach((b) => b.addEventListener('click', () => { draft[b.dataset.pref] = !draft[b.dataset.pref]; drawPrefs(); }));
$('acct-prefs-form').elements.email.addEventListener('input', (event) => { draft.email = event.target.value.trim(); drawPrefs(); });
$('acct-prefs-discard').addEventListener('click', () => { draft = JSON.parse(JSON.stringify(saved)); drawPrefs(); });
$('acct-prefs-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const changes = Object.fromEntries(Object.keys(draft).filter((k) => JSON.stringify(draft[k]) !== JSON.stringify(saved[k])).map((k) => [k, draft[k]]));
  try { const reply = await post({ action: 'prefs', prefs: changes }); showPrefs(reply.prefs, view && view.names_to); note('Saved.', true); }
  catch (err) { note(err.message, false); }
});
load();

// My notebook: shown only where the box gives each login one (notebooks.py).
const NOTEBOOK_WORDS = { ready: 'Running now.', starting: 'Starting.', stopped: 'Not running: it starts when you open it.',
  full: 'Not running.', memory: 'Not running.' };
async function loadNotebook() {
  let st;
  try { st = await (await fetch('/api/notebook')).json(); } catch (_) { return; }
  const shown = ['ready', 'starting', 'stopped'].includes(st.state);
  $('acct-notebook').hidden = !shown;
  if (shown) $('acct-notebook-state').textContent = NOTEBOOK_WORDS[st.state];
}
