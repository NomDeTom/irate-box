// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /account.html (next-work plan step 16, accounts-plan stage 1): log in or out, make an account
// (as the admin allows: open, or asking the admin), set a password with a one-time code, change
// it. Over plain HTTP it says what that means, or (when the admin has prevented it) takes no
// password at all and points at the certificate and the HTTPS page.
const $ = (id) => document.getElementById(id);
let view = null;
// Where to go once logged in: an app in users mode sends people here with ?next=<its path>. Only
// a path on this origin, never another site (an open redirect).
const next = (() => {
  const n = new URLSearchParams(location.search).get('next') || '';
  return n.startsWith('/') && !n.startsWith('//') && !n.startsWith('/\\') ? n : '';
})();

function note(text, ok) {
  $('acct-note').textContent = text;
  $('acct-note').className = ok ? 'ok' : 'bad';
}

async function post(body) {
  const r = await fetch('/api/account', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Irate-Account': '1' }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

function render(d) {
  view = d;
  const plain = !d.https;
  const prevented = plain && d.http === 'prevented';
  $('acct-http').hidden = !plain || d.http === 'permitted';
  $('acct-http-text').textContent = prevented
    ? 'This page is on plain HTTP, and this box takes passwords only over HTTPS: anyone on the WiFi could read one sent here.'
    : 'This page is on plain HTTP: a password sent from it can be read by anyone on the WiFi. Use one you use nowhere else, or the HTTPS page.';
  $('acct-https-link').href = `https://${location.host}${location.pathname}`;
  document.querySelectorAll('#acct-guest input, #acct-guest button, #acct-me input, #acct-me button[type=submit]').forEach((n) => { n.disabled = prevented; });
  $('acct-me').hidden = !d.me;
  $('acct-guest').hidden = !!d.me;
  if (d.me) {
    $('acct-subtitle').textContent = `Logged in as ${d.me.name}.`;
    $('acct-me-text').replaceChildren(d.me.role === 'admin' ? `${d.me.name}: an admin of this box. ` : `${d.me.name}: a user of this box.`);
    if (d.me.role === 'admin') {
      const a = document.createElement('a'); a.href = '/admin/'; a.textContent = 'Open /admin';
      $('acct-me-text').append(a);
    }
    return;
  }
  const signup = d.signup === 'open' || d.signup === 'apply';
  $('acct-subtitle').textContent = d.signup === 'off' ? 'This box has no accounts: everything here is open to guests, or kept for its admin.'
    : next ? 'That page is for the box\'s users: log in, or make an account.' : 'Log in, or make an account.';
  $('acct-signup-form').hidden = !signup;
  $('acct-signup-title').textContent = d.signup === 'apply' ? 'Ask for an account' : 'Make an account';
  $('acct-signup-button').textContent = d.signup === 'apply' ? 'Ask' : 'Make it';
  $('acct-login-form').hidden = d.signup === 'off';
  $('acct-code-form').hidden = d.signup === 'off';
  $('acct-signup-desc').textContent = d.signup === 'apply' ? 'The box\'s admin accepts each one; until then it can\'t log in.' : 'Usable straight away. No e-mail, no real name.';

}

async function load() {
  try { render(await (await fetch('/api/account')).json()); } catch (_) { $('acct-subtitle').textContent = 'Could not reach the hub.'; }
}

function form(id, build, done) {
  $(id).addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const d = await post(build(f));
      f.reset();
      done(d);
    } catch (err) { note(err.message, false); }
  });
}

form('acct-login-form', (f) => ({ action: 'login', name: f.elements.name.value, password: f.elements.password.value }), () => {
  if (next) { location.assign(next); return; }
  note('Logged in.', true); load();
});
form('acct-signup-form', (f) => ({ action: 'signup', name: f.elements.name.value, password: f.elements.password.value }), (d) => {
  note(d.state === 'asked' ? 'Asked: you can log in once the box\'s admin accepts it.' : 'Made, and logged in.', true);
  load();
});
form('acct-code-form', (f) => ({ action: 'code', code: f.elements.code.value, password: f.elements.password.value }), (d) => note(`Password set for ${d.name}: log in with it.`, true));
form('acct-password-form', (f) => ({ action: 'password', old: f.elements.old.value, new: f.elements.new.value }), () => note('Changed. Your other devices are logged out.', true));
$('acct-logout').addEventListener('click', async () => {
  try { await post({ action: 'logout' }); note('Logged out.', true); load(); } catch (err) { note(err.message, false); }
});
load();
