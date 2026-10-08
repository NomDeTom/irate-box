// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Accounts (next-work plan step 16) in jsdom, static: /account.html (logged out as each sign-up
// level shows it; plain HTTP warned or prevented; logged in) and /admin → Accounts (the levels,
// the list and its buttons, a one-time code shown once). Usage: [JSDOM=…/jsdom] node dom-accounts.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const strip = (h) => h;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const json = (b, status = 200) => new Response(JSON.stringify(b), { status });
function page(file, url, fetcher, scripts) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
  vc.on('error', (...a) => { if (!/HTTP 404/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
  const dom = new JSDOM(strip(fs.readFileSync(`${WEB}/${file}`, 'utf8')), { url, runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  dom.window.fetch = fetcher;
  dom.window.confirm = () => true;
  scripts.forEach((s) => dom.window.eval(fs.readFileSync(`${WEB}/${s}`, 'utf8')));
  return { w: dom.window, d: dom.window.document, errors };
}
(async () => {
  const posted = [];
  let view = { signup: 'apply', http: 'warning', https: false, me: null };
  let p = page('account.html', 'http://box.local/account.html', async (u, o = {}) => {
    if (o.method === 'POST') {
      const b = JSON.parse(o.body); posted.push({ b, h: o.headers });
      if (b.action === 'signup') return json({ state: 'asked' });
      if (b.action === 'login') { view = Object.assign({}, view, { me: { name: 'alice', role: 'user' } }); return json({ me: view.me }); }
      return json({});
    }
    return json(view);
  }, ['account.js']);
  await wait();
  let d = p.d;
  check('apply over HTTP: warned, with the certificate and the HTTPS page', !d.getElementById('acct-http').hidden && /can be read by anyone on the WiFi/.test(t(d.getElementById('acct-http-text')))
    && d.getElementById('acct-https-link').href === 'https://box.local/account.html');
  check('  asking, not making', !d.getElementById('acct-signup-form').hidden && t(d.getElementById('acct-signup-button')) === 'Ask');
  const sf = d.getElementById('acct-signup-form');
  sf.elements.name.value = 'alice'; sf.elements.password.value = 'password1';
  sf.dispatchEvent(new p.w.Event('submit', { cancelable: true })); await wait();
  check('  Ask: sent with the page\'s header; says it waits for the admin', posted[0].b.action === 'signup' && posted[0].h['X-Irate-Account'] === '1'
    && /once the box's admin accepts it/.test(t(d.getElementById('acct-note'))));
  const lf = d.getElementById('acct-login-form');
  lf.elements.name.value = 'alice'; lf.elements.password.value = 'password1';
  lf.dispatchEvent(new p.w.Event('submit', { cancelable: true })); await wait(300);
  check('log in: then the account, with Change password and Log out', !d.getElementById('acct-me').hidden && d.getElementById('acct-guest').hidden
    && /alice: a user of this box/.test(t(d.getElementById('acct-me-text'))));
  view = { signup: 'open', http: 'prevented', https: false, me: null };
  p = page('account.html', 'http://box.local/account.html', async () => json(view), ['account.js']);
  await wait(); d = p.d;
  check('prevented over HTTP: says so, every field off', /takes passwords only over HTTPS/.test(t(d.getElementById('acct-http-text')))
    && [...d.querySelectorAll('#acct-guest input')].every((i) => i.disabled));
  view = { signup: 'off', http: 'prevented', https: true, me: null };
  p = page('account.html', 'https://box.local/account.html', async () => json(view), ['account.js']);
  await wait(); d = p.d;
  check('off, over HTTPS: no warning; says the box has no accounts, no forms', d.getElementById('acct-http').hidden && /has no accounts/.test(t(d.getElementById('acct-subtitle')))
    && d.getElementById('acct-login-form').hidden && d.getElementById('acct-signup-form').hidden);
  check('account page: no errors', !p.errors.length, p.errors.join(' | '));

  // The shoutbox and forum on the home page (stage 5): as the admin set posting.
  const nowT = 1000;
  const shout = (posting) => ({ now: nowT, ttl: 86400, messages: [{ name: 'erin', text: 'hi', created: nowT - 5, account: 'erin' },
    { name: 'Salty Parrot', text: 'ahoy', created: nowT - 3 }], posting });
  const board = (posting) => ({ now: nowT, ttl: 604800, threads: [{ id: 1, title: 'T', author: 'erin', account: 'erin', created: 1, active: 1, replies: 0, excerpt: 'x' }], posting });
  const home = async (posting) => {
    const h = page('index.html', 'http://box.local/', async (u) => (u === '/messages' ? json(shout(posting)) : u === '/board/threads' ? json(board(posting)) : json({}, 404)),
      ['age.js', 'ident.js', 'emoji.js', 'shoutbox.js', 'board.js']);
    await wait(300);
    return h;
  };
  let h = await home({ who: 'guests', marks: true, me: 'erin' });
  d = h.d;
  check('home: a user\'s posts marked ✓, a guest\'s not', [...d.querySelectorAll('#messages .msg')].map((m) => !!m.querySelector('.verified')).join() === 'true,false'
    && !!d.querySelector('#threads .verified'));
  check('  logged in: the name is the account\'s, fixed, no random names', d.getElementById('name-input').value === 'erin' && d.getElementById('name-input').readOnly
    && d.getElementById('name-roll').hidden && d.getElementById('t-name').value === 'erin' && d.getElementById('t-name').readOnly);
  h = await home({ who: 'users', marks: false, me: null });
  d = h.d;
  check('users only, a guest: the forms hidden, a link to log in; marks off: none shown', d.getElementById('shout-form').hidden
    && /Log in to post here/.test(t(d.querySelector('.shout-post-note'))) && d.getElementById('thread-form').hidden
    && !d.querySelector('.verified'));
  h = await home({ who: 'off', marks: true, me: 'erin' });
  check('  off: closed, even for a user', h.d.getElementById('shout-form').hidden && /closed/.test(t(h.d.querySelector('.shout-post-note')))
    && /closed to new posts/.test(t(h.d.querySelector('.board-post-note'))));
  check('home: no errors', !h.errors.length, h.errors.join(' | '));

  // /admin → Accounts.
  const now = Math.floor(Date.now() / 1000);
  let state = { settings: { signup: 'apply', http: 'warning' }, counts: { user: 1, admins: 1, asked: 1, disabled: 0 },
    accounts: [{ name: 'bob', state: 'asked', role: 'user', created: now - 60, seen: null, by: 'signed up', password_set: true },
      { name: 'carol', state: 'user', role: 'admin', created: now - 7200, seen: now - 30, by: 'admin', password_set: true }] };
  const aposted = [];
  const a = page('admin.html', 'http://box.local/admin/#accounts', async (u, o = {}) => {
    if (o.method === 'POST' && u === '/admin/accounts') { const b = JSON.parse(o.body); aposted.push(b); return json(Object.assign({}, state, b.action === 'make' ? { code: 'ABCD-EFGH-JKLM-NPQR' } : {})); }
    if (u === '/admin/accounts') return json(state);
    return json({}, 404);
  }, ['admin-widgets.js', 'admin.js']);
  await wait(300); d = a.d;
  check('admin: a side-bar entry, the levels as set, the counts', [...d.querySelectorAll('.admin-side-list a')].some((x) => x.hash === '#accounts')
    && d.getElementById('accounts-settings').elements.signup.value === 'apply' && /1 user \(1 admin\), 1 asking, 0 switched off\./.test(t(d.getElementById('accounts-counts'))));
  const rows = [...d.querySelectorAll('#accounts-list .admin-item')];
  const btns = (r) => [...r.querySelectorAll('button')].map((b) => t(b));
  check('  one asking: Accept or Refuse', /^Asking bob/.test(t(rows[0])) && btns(rows[0]).join() === 'Accept,Refuse', btns(rows[0]).join());
  check('  an admin: Switch off, Make a user, Reset password, Delete', /^Admin carol/.test(t(rows[1])) && btns(rows[1]).join() === 'Switch off,Make a user,Reset password,Delete', btns(rows[1]).join());
  [...rows[0].querySelectorAll('button')][0].click(); await wait();
  check('  Accept asks the hub', aposted.some((b) => b.action === 'accept' && b.name === 'bob'));
  const mf = d.getElementById('accounts-make');
  mf.elements.name.value = 'dave'; mf.elements.role.value = 'user';
  mf.dispatchEvent(new a.w.Event('submit', { cancelable: true })); await wait();
  check('  Make: the code shown, once', aposted.some((b) => b.action === 'make' && b.name === 'dave') && /dave's one-time code: ABCD-EFGH-JKLM-NPQR/.test(t(d.getElementById('accounts-code'))));
  const sform = d.getElementById('accounts-settings');
  sform.elements.signup.value = 'assigned'; sform.elements.http.value = 'prevented';
  sform.dispatchEvent(new a.w.Event('submit', { cancelable: true })); await wait();
  check('  Save: both levels sent', aposted.some((b) => b.action === 'settings' && b.signup === 'assigned' && b.http === 'prevented'));
  check('admin: no errors', !a.errors.length, a.errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
