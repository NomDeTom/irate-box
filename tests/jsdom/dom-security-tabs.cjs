// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Security page in tabs: guest access (the moved level and its protections), sign-in,
// process safety, secrets and connections; the address names a tab; Network › Hotspot keeps the
// hotspot's encryption and points at the moved level; the moved level saves as it did.
// Usage: [JSDOM=…/jsdom] node dom-security-tabs.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const REPO = path.resolve(__dirname, '../..');
const WEB = `${REPO}/web`;
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const hotspot = JSON.parse(fs.readFileSync(`${__dirname}/hotspot-fixture.json`, 'utf8'));
const now = Math.floor(Date.now() / 1000);
const F = (id, title, status, actions = []) => ({ id, title, status, summary: 'd', fix: '', actions });
const scan = {
  at: now,
  listeners: [],
  findings: [
    F('guest-net', 'Guest internet access', 'warn', []),
    F('firewall', 'Hotspot firewall', 'warn', []),
    F('ssh-password', 'SSH password login', 'warn', [{ choice: 'ssh-password-off', label: 'Turn password login off' }]),
    F('root-password', "Root's password", 'warn', []),
    F('kernel-links', 'Kernel link protections', 'ok', []),
    F('proc-hidden', 'Process visibility', 'warn', []),
    F('cockpit', 'Cockpit', 'warn', []),
    F('listeners-unread', 'What listens on the network', 'warn', []),
  ],
};
const sec = { hub: [], accepted: {}, scan, log: [], pending: 0, results: [], deep: {}, imports: {}, audit: null };
const hotspotData = { ...hotspot, share: 'off', results: [] };
const settings = {
  setup_done: false, setup_decided: [], names_to: 'users', visitor_counts: true,
  signin_level: 'standard', signin_adaptive: true, signin_common: true, signin_known_by: 'either',
  signin_min_length: 12, signin_rules: 'none', signin_block_common: true, pin_sessions: true,
  report_hide_min: 2, storage_share: 'half',
};
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#security', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  const json = (x, s = 200) => new Response(JSON.stringify(x), { status: s });
  if (opts.method === 'POST' && u === '/admin/hotspot') {
    posted.push(JSON.parse(opts.body));
    return json({ id: 'share-1', message: 'kept' }, 202);
  }
  if (u === '/admin/security') return json(sec);
  if (u === '/admin/hotspot') return json(hotspotData);
  if (u === '/admin/settings') return json(settings);
  if (u === '/admin/signin') return json({ levels: {}, adaptive: {} });
  if (u === '/admin/apps') return json({ apps: [] });
  return json({}, 404);
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 80) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait();
  w.eval('loadSecurity')();
  await wait(150);
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const panel = (id) => d.getElementById(id);
  const tabs = [...d.querySelectorAll('[data-tabs="security"] [role="tab"]')];
  check('five tabs: Guest access, Sign-in, Process safety, Secrets, Connections',
    tabs.map(t).join('|') === 'Guest access|Sign-in|Process safety|Secrets|Connections', tabs.map(t).join('|'));
  check('Guest access: the level beside the four protections and the hotspot firewall',
    panel('security/guest-access').contains(d.getElementById('guest-net-box'))
    && !!panel('security/guest-access').querySelector('#sec-guest-net')
    && !!panel('security/guest-access').querySelector('#sec-firewall'));
  check('Sign-in: Sign-in and identity, SSH, root and sudo, and the root policy',
    panel('security/sign-in').contains(d.getElementById('signin-identity-box'))
    && !!panel('security/sign-in').querySelector('#sec-ssh-password')
    && !!panel('security/sign-in').querySelector('#security-policy'));
  check('Process safety: process visibility, and the units\' sandboxes',
    !!panel('security/process-safety').querySelector('#sec-proc-hidden')
    && !!panel('security/process-safety').querySelector('#security-units'));
  check('Secrets: the secrets\' modes and owners, and secrets in process arguments',
    !!panel('security/secrets').querySelector('#security-secrets')
    && !!panel('security/secrets').querySelector('#security-cmdlines'));
  check('Connections: HTTP / HTTPS, visitor data, network listeners and Cockpit',
    panel('security/connections').contains(d.getElementById('security-https'))
    && panel('security/connections').contains(d.getElementById('security-visitors'))
    && panel('security/connections').contains(d.getElementById('security-ports'))
    && !!panel('security/connections').querySelector('#sec-cockpit'));
  check('the first tab opens by itself', !panel('security/guest-access').hidden
    && d.getElementById('tab-sec-guest-access').getAttribute('aria-selected') === 'true');
  const opens = (hash, id, tab) => {
    w.location.hash = hash;
    w.dispatchEvent(new w.HashChangeEvent('hashchange'));
    return !panel(id).hidden && d.getElementById(tab).getAttribute('aria-selected') === 'true';
  };
  check('an address names the tab (#security/sign-in)', opens('#security/sign-in', 'security/sign-in', 'tab-sec-sign-in'));
  check('  and a setting inside one opens its tab (#sec-cockpit)', opens('#sec-cockpit', 'security/connections', 'tab-sec-connections'));
  check('  and Process safety has its own address too', opens('#security/process-safety', 'security/process-safety', 'tab-sec-process-safety'));
  // Network › Hotspot keeps its encryption, and says where the level went.
  check('Network › Hotspot keeps its encryption settings, without the level',
    !!panel('network/hotspot').querySelector('#hs-modes') && !panel('network/hotspot').contains(d.getElementById('guest-net-box'))
    && !!panel('network/hotspot').querySelector('#guest-net-moved a[href="#security/guest-access"]'));
  // The moved level saves as it did: a chip, then Save, posts to the hotspot as a share.
  w.location.hash = '#security/guest-access';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  const level = [...d.querySelectorAll('#guest-net-chips button')].find((b) => /All devices: no portal consent/.test(t(b)));
  check('the moved level draws its five stops', d.querySelectorAll('#guest-net-chips button').length === 5 && !!level,
    [...d.querySelectorAll('#guest-net-chips button')].map(t).join(' | '));
  level.click();
  await wait();
  d.querySelector('#guest-net-foot .aw-save').click();
  await wait(120);
  check('the moved level saves as before (POST /admin/hotspot, action share)',
    posted.some((b) => b.action === 'share' && b.level === 'open'), JSON.stringify(posted));
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  const mobileTabs = /@media \(max-width: 760px\)\s*\{[\s\S]*?\.tab-set > \.view-switch\s*\{[^}]*overflow-x: auto[^}]*\}/.test(css);
  const containerTabs = /@container page \(max-width: 760px\)\s*\{[\s\S]*?\.tab-set > \.view-switch\s*\{[^}]*overflow-x: auto[^}]*\}/.test(css);
  const noWrap = /\.tab-set > \.view-switch > \.view-switch-btn\s*\{[^}]*white-space: nowrap/.test(css);
  check('phone: the tab strip scrolls by itself and keeps each label on one line', mobileTabs && containerTabs && noWrap);
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})();
