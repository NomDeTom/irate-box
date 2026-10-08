// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// HTTPS (next-work plan step 15) in jsdom, static: /admin → Security's HTTPS section (not set up,
// on, a box moved to another subnet; make, switch, a new CA only after a confirm) and the public
// /certificate page (the fingerprint, what it may vouch for, the general step while no platform's
// steps have been tried on a device, the self-check saying whether this device trusts the box).
// Usage: [JSDOM=…/jsdom] node dom-tls.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const strip = (h) => h;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 200) => new Promise((r) => setTimeout(r, ms));
const now = Math.floor(Date.now() / 1000);
const ON = { set_up: true, on: true, ports: { main: 443 }, outside: [], pending: 0, results: [],
  ca: { fingerprint: 'AB:CD:EF', names: ['irate.home.arpa', '.irate.home.arpa', 'lyra.local'], networks: ['192.168.4.1/32', '192.168.1.0/24'] },
  cert: { names: ['irate.home.arpa', 'lyra.local'], addresses: ['192.168.4.1', '192.168.1.181'], expires: now + 300 * 86400, own: false } };
function page(file, url, fetcher, scripts) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
  vc.on('error', (...a) => { if (!/HTTP 404/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
  const dom = new JSDOM(strip(fs.readFileSync(`${WEB}/${file}`, 'utf8')), { url, runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  dom.window.fetch = fetcher;
  dom.window.confirm = () => page.confirm;
  scripts.forEach((s) => dom.window.eval(fs.readFileSync(`${WEB}/${s}`, 'utf8')));
  return { w: dom.window, d: dom.window.document, errors };
}
const json = (b, status = 200) => new Response(JSON.stringify(b), { status });
(async () => {
  // The admin section.
  let state = { set_up: false, pending: 0, results: [] };
  const posted = [];
  const admin = page('admin.html', 'http://box.local/admin/#security', async (u, o = {}) => {
    if (o.method === 'POST' && u === '/admin/tls') { posted.push(JSON.parse(o.body)); state = Object.assign({}, ON, { results: [{ id: 'r1', ok: true, message: 'made the box\'s CA' }] }); return json({ id: 'r1' }, 202); }
    if (u === '/admin/tls') return json(state);
    return new Response('{}', { status: 404 });
  }, ['admin-widgets.js', 'admin.js']);
  await wait();
  let d = admin.d;
  check('not set up: said, and Make the box\'s certificate offered', /No certificate yet/.test(t(d.getElementById('tls-state'))) && !d.getElementById('tls-make').hidden
    && d.getElementById('tls-switch').hidden && d.getElementById('tls-again').hidden);
  d.getElementById('tls-make').click(); await wait(400);
  check('Make: asked of the hub; then on, with what it covers and until when', posted[0] && posted[0].action === 'make'
    && /On: https:\/\/<this box>\/ \(port 443\) and each app's twin\. The certificate covers irate\.home\.arpa, lyra\.local, 192\.168\.4\.1, 192\.168\.1\.181, until .*, renewed by the box before then\. The CA's fingerprint: AB:CD:EF\./.test(t(d.getElementById('tls-state')))
    && /made the box's CA/.test(t(d.getElementById('tls-note'))), t(d.getElementById('tls-state')));
  check('  then Switch off, and a new CA', t(d.getElementById('tls-switch')) === 'Switch HTTPS off' && !d.getElementById('tls-again').hidden && d.getElementById('tls-make').hidden);
  page.confirm = false;
  d.getElementById('tls-again').click(); await wait();
  check('a new CA: not without saying yes', posted.length === 1);
  page.confirm = true;
  d.getElementById('tls-again').click(); await wait();
  check('  with a yes: asked as a new one', posted[1] && posted[1].action === 'make' && posted[1].again === true);
  state = Object.assign({}, ON, { outside: ['10.0.0.5'] });
  admin.w.eval('loadTls()'); await wait();
  check('moved to another subnet: said, as needing a new CA', /now at 10\.0\.0\.5, outside what its CA may vouch for: make a new CA/.test(t(d.getElementById('tls-state')))
    && d.getElementById('tls-state').classList.contains('bad'));
  // /admin over HTTPS only.
  state = Object.assign({}, ON);
  admin.w.eval('loadTls()'); await wait();
  check('admin HTTPS only: offered while on, but not from a plain-HTTP page, saying why', !d.getElementById('tls-admin-only').hidden && d.getElementById('tls-admin-only').disabled
    && /open it over HTTPS first/.test(t(d.getElementById('tls-admin-only-note'))));
  // Bring your own.
  check('own certificate: a warning on plain HTTP (the key would cross the network in clear)', !d.getElementById('tls-own-plain').hidden);
  const of = d.getElementById('tls-own-form');
  of.elements.chain.value = '-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----';
  of.elements.key.value = '-----BEGIN PRIVATE KEY-----\nBBB\n-----END PRIVATE KEY-----';
  of.dispatchEvent(new admin.w.Event('submit', { cancelable: true })); await wait();
  const imp = posted.find((b) => b.action === 'import');
  check('  Use it: the chain and the key sent, the key not left in the page', imp && /BEGIN CERTIFICATE/.test(imp.chain) && /BEGIN PRIVATE KEY/.test(imp.key) && of.elements.key.value === '');
  state = Object.assign({}, ON, { cert: Object.assign({}, ON.cert, { names: ['box.example.org'], addresses: [], own: true }) });
  admin.w.eval('loadTls()'); await wait();
  check('  in use: said as yours, to renew; and the way back to the box\'s own', /\(your own: renew it before then\)/.test(t(d.getElementById('tls-state')))
    && !d.getElementById('tls-box').hidden);
  d.getElementById('tls-box').click(); await wait();
  check('  Go back: asked of the hub', posted.some((b) => b.action === 'box'));
  check('admin: no page errors', !admin.errors.length, admin.errors.join(' | '));

  // The public page.
  const pub = (info, trusts, url = 'http://box.local/certificate.html') => page('certificate.html', url, async (u) => {
    if (u === '/certificate.json') return json(info);
    if (u.startsWith('https://')) { if (trusts) return new Response('', { status: 200 }); throw new TypeError('Failed to fetch'); }
    return new Response('{}', { status: 404 });
  }, ['certificate.js']);
  let p = pub({ set_up: false }, false);
  await wait();
  check('/certificate, none yet: said, nothing to download', /no certificate yet/.test(t(p.d.getElementById('cert-state'))) && p.d.getElementById('cert-about').hidden);
  const info = { set_up: true, on: true, port: 443, fingerprint: 'AB:CD:EF', names: ['irate.home.arpa', 'lyra.local'], expires: now + 300 * 86400 };
  p = pub(info, false);
  await wait();
  check('/certificate: the download, its fingerprint, what it may vouch for', !p.d.getElementById('cert-about').hidden && t(p.d.getElementById('cert-fingerprint')) === 'AB:CD:EF'
    && t(p.d.getElementById('cert-names')) === 'irate.home.arpa, lyra.local' && p.d.getElementById('cert-download').getAttribute('href') === '/certificate/ca.crt');
  check('  no platform\'s steps until tried on a device: the general step', !p.d.querySelector('#cert-steps details') && /guides for each kind of phone and computer follow/.test(t(p.d.getElementById('cert-steps'))));
  check('  the self-check: not trusted yet', /does not trust the box yet/.test(t(p.d.getElementById('cert-check'))), t(p.d.getElementById('cert-check')));
  check('  never a click-through: the warning is not to be clicked past', /don't click through/.test(t(p.d.getElementById('cert-about'))));
  p = pub(info, true);
  await wait();
  check('  trusted: said, with the HTTPS address', /✓ This device trusts the box: you can use https:\/\/box\.local\/\./.test(t(p.d.getElementById('cert-check'))), t(p.d.getElementById('cert-check')));
  p = pub(info, false, 'https://box.local/certificate.html');
  await wait();
  check('  opened over HTTPS: it is trusted already', /You are on HTTPS now/.test(t(p.d.getElementById('cert-check'))));
  check('public: no page errors', !p.errors.length, p.errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
