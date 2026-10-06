// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Library → Toolkits (next-work plan step 28, toolkits-plan §7) in jsdom, against a fixture: the
// summary, a card per kit with its badges, the consent step before an install, remove and keep
// longer, roll back, keep current, the budget, a problem with the cache, and the answer from the
// root helper shown when it comes. Static: no hub needed. Usage: [JSDOM=…/jsdom] node dom-kits.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const now = Math.floor(Date.now() / 1000);
const def = (id, title, packages, extra = {}) => Object.assign({ id, title, summary: `${title} tools.`, consent: `${title}: read this first.`,
  packages, notes: [`${title} note`], remove_after_hours: 24 }, extra);
const kitsData = {
  kits: { debug: def('debug', 'Debugging', ['gdb', 'strace']), build: def('build', 'Building', ['cmake'], { remove_after_hours: null }),
    capture: def('capture', 'Capture', ['tcpdump', 'ngrep'], { extra: ['ngrep'] }),
    security: def('security', 'Security', ['lynis'], { git: [{ name: 'debian-cis', upstream: 'https://github.com/ovh/debian-cis' }] }),
    radio: def('radio', 'Radio tools', ['rtl-sdr', 'sox'], { owner: true, summary: 'SDR bits.' }) },
  settings: { budget_mb: 500, kits: { debug: { keep_current: true, remove_after: 24 }, build: { keep_current: true, remove_after: null },
    capture: { keep_current: false, remove_after: 24 }, security: { keep_current: true, remove_after: 24 }, radio: { keep_current: true, remove_after: 4 } } },
  status: { at: now, pool_bytes: 91 * 2 ** 20, problems: [],
    kits: { debug: { cached: { fetched: now - 3600, packages: 55, bytes: 69 * 2 ** 20, on_box: ['strace'], versions: { gdb: '16.3-1' },
        left_out: ['bpftrace', 'bpfcc-tools', 'bpftool'], arch: 'armhf' }, previous: null },
      build: { cached: null, previous: null }, radio: { cached: null, previous: null },
      capture: { cached: { fetched: now - 7200, packages: 3, bytes: 2 ** 20, on_box: [], versions: {} }, previous: null },
      security: { cached: { fetched: now - 600, packages: 38, bytes: 29 * 2 ** 20, on_box: [], versions: {} }, previous: { fetched: now - 86400 * 3, bytes: 28 * 2 ** 20 } } },
    installed: { debug: { added: ['gdb'], at: now - 600, remove_at: now + 5 * 3600, units: [], upgraded: ['libc6 2.41-12+deb13u3 → 2.41-12+deb13u4'] } } },
  feeds: { debsecan: { suite: 'trixie', fetched: now - 1800 } }, results: [] };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#toolkits', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
let answered = false;
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST' && u === '/admin/kits') {
    const body = JSON.parse(opts.body);
    posted.push(body);
    return new Response(JSON.stringify(Object.assign({}, kitsData, body.action === 'settings' ? {} : { id: `id-${posted.length}` })),
      { status: body.action === 'settings' ? 200 : 202 });
  }
  if (u === '/admin/kits') {
    // The root helper's answer to the first request, once asked for again.
    const results = answered ? [{ id: 'id-1', ok: true, message: 'Debugging: removed 1 packages' }] : [];
    return new Response(JSON.stringify(Object.assign({}, kitsData, { results })), { status: 200 });
  }
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 50) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait();
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const card = (title) => [...d.querySelectorAll('#kits-grid .kit-card')].find((c) => t(c.querySelector('h4')) === title);
  const button = (root, label) => [...root.querySelectorAll('button')].find((b) => t(b) === label);
  check('Toolkits sits in Library, after Mirrors', [...d.querySelectorAll('.admin-side-list a')].map((a) => a.getAttribute('href')).join(' ').includes('#mirrors #toolkits #sources'));
  check('the summary: how many cached, the size against the budget, the newest fetch, the feed',
    /^3 of 5 toolkits cached, 91\.0 MB of 500 MB; newest fetch \d{4}-\d\d-\d\d\. Cached kits install with no internet\. debsecan's data: \d{4}-/.test(t(d.getElementById('kits-summary'))),
    t(d.getElementById('kits-summary')));
  check('a card per kit', d.querySelectorAll('#kits-grid .kit-card').length === 5);
  check('installed: its time left, and what installing it upgraded', /Installed: removed in [45] h/.test(t(card('Debugging').querySelector('.badges')))
    && /also brought these up to date .*libc6/.test(t(card('Debugging'))), t(card('Debugging')));
  check('not cached: Install is off, and says why', button(card('Building'), 'Install…').disabled && /Refresh it while the box has internet/.test(t(card('Building'))));
  check('two versions: the badge, and Roll back with its date', /2 versions/.test(t(card('Security').querySelector('.badges')))
    && [...card('Security').querySelectorAll('button')].some((b) => /^Roll back to \d{4}-/.test(t(b))));
  check('details: the notes, the packages and what the box has, the git source', /Debugging note/.test(t(card('Debugging').querySelector('details')))
    && /55 packages: gdb 16\.3-1\. Already on the box: strace\./.test(t(card('Debugging').querySelector('details')))
    && /From git: debian-cis \(github\.com\/ovh\/debian-cis, a mirror\)/.test(t(card('Security').querySelector('details'))));
  check('what a 32-bit board leaves out is said', /Left out on this armhf board, as they need a 64-bit one: bpftrace, bpfcc-tools, bpftool\./.test(t(card('Debugging').querySelector('details'))));
  check('keep current shown as set', !card('Capture').querySelector('input[type=checkbox]').checked && card('Debugging').querySelector('input[type=checkbox]').checked);

  button(card('Debugging'), 'Remove now').click();
  await wait();
  check('Remove asks the hub', posted[0] && posted[0].action === 'remove' && posted[0].kit === 'debug', JSON.stringify(posted));
  check('  and the page says it is working on it', /Working…/.test(t(d.getElementById('kits-summary'))));
  answered = true;
  await wait(3300);
  check('the root helper\'s answer is shown when it comes', /removed 1 packages/.test(t(d.getElementById('kits-note'))) && !/Working…/.test(t(d.getElementById('kits-summary'))),
    t(d.getElementById('kits-note')));

  // The consent step before an install.
  button(card('Capture'), 'Install…').click();
  const consent = card('Capture').querySelector('.kit-consent');
  check('Install… shows the consent and when it goes again, before anything happens', consent && /Capture: read this first\./.test(t(consent))
    && consent.querySelector('select.kit-hours').value === '24' && posted.length === 1);
  consent.querySelector('select.kit-hours').value = '168';
  button(consent, 'Install').click();
  await wait();
  check('Install sends the kit and the time chosen', JSON.stringify(posted[1]) === JSON.stringify({ action: 'install', kit: 'capture', hours: 168 }), JSON.stringify(posted[1]));
  const keep = card('Debugging').querySelector('select.kit-hours');
  keep.value = 'never';
  button(card('Debugging'), 'Keep longer').click();
  await wait();
  check('Keep longer: never', JSON.stringify(posted[2]) === JSON.stringify({ action: 'keep', kit: 'debug', hours: null }), JSON.stringify(posted[2]));
  [...card('Security').querySelectorAll('button')].find((b) => /^Roll back/.test(t(b))).click();
  await wait();
  check('Roll back asks, then asks the hub', posted[3] && posted[3].action === 'rollback' && posted[3].kit === 'security');
  const kc = card('Capture').querySelector('input[type=checkbox]');
  kc.checked = true; kc.dispatchEvent(new w.Event('change'));
  await wait();
  check('keep current is a setting', JSON.stringify(posted[4]) === JSON.stringify({ action: 'settings', kits: { capture: { keep_current: true } } }), JSON.stringify(posted[4]));
  const f = d.getElementById('kits-budget');
  f.elements.budget.value = '800';
  f.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('the budget is saved', JSON.stringify(posted[5]) === JSON.stringify({ action: 'settings', budget_mb: 800 }), JSON.stringify(posted[5]));
  kitsData.status.problems = ['gdb_16.3-1_armhf.deb has changed since it was fetched'];
  w.eval('loadKits()');
  await wait();
  check('a problem with the cache is shown', /has changed since it was fetched/.test(t(d.getElementById('kits-problems'))));
  // Step 38: your own kits, and extra tools in a shipped kit.
  check('your own kit says so, with Edit and Delete', /Yours/.test(t(card('Radio tools').querySelector('.badges')))
    && button(card('Radio tools'), 'Edit') && button(card('Radio tools'), 'Delete'));
  check('a shipped kit has no Delete, and offers extra tools', !button(card('Debugging'), 'Delete') && card('Debugging').querySelector('input.kit-extra'));
  check('a kit with extra tools says so, and lists them', /\+1 extra/.test(t(card('Capture').querySelector('.badges'))) && card('Capture').querySelector('input.kit-extra').value === 'ngrep');
  const ex = card('Debugging').querySelector('input.kit-extra');
  ex.value = 'ltrace,  gdbserver';
  button(ex.parentNode, 'Save').click();
  await wait();
  check('extra tools: Save sends the names', JSON.stringify(posted[posted.length - 1]) === JSON.stringify({ action: 'extra', kit: 'debug', packages: ['ltrace', 'gdbserver'] }),
    JSON.stringify(posted[posted.length - 1]));
  button(card('Radio tools'), 'Edit').click();
  const af = d.getElementById('kits-add').elements;
  check('Edit fills the form, and says it is a change', af.id.value === 'radio' && af.packages.value === 'rtl-sdr sox' && af.hours.value === '4'
    && /Change Radio tools/.test(t(d.getElementById('kits-form-title'))));
  af.packages.value = 'rtl-sdr sox gqrx-sdr';
  d.getElementById('kits-add').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('saving sends the kit, with its id', JSON.stringify(posted[posted.length - 1]) === JSON.stringify({ action: 'define',
    kit: { title: 'Radio tools', summary: 'SDR bits.', packages: ['rtl-sdr', 'sox', 'gqrx-sdr'], remove_after_hours: 4, id: 'radio' } }), JSON.stringify(posted[posted.length - 1]));
  check('  and the form is a new one again', af.id.value === '' && /Add a toolkit of your own/.test(t(d.getElementById('kits-form-title'))));
  af.title.value = 'Mesh'; af.packages.value = 'mosquitto-clients'; af.hours.value = 'never';
  d.getElementById('kits-add').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check('adding one: no id (the hub makes it), never removed', JSON.stringify(posted[posted.length - 1].kit) === JSON.stringify({ title: 'Mesh', summary: '', packages: ['mosquitto-clients'], remove_after_hours: null }));
  button(card('Radio tools'), 'Delete').click();
  await wait();
  check('Delete asks, then asks the hub', JSON.stringify(posted[posted.length - 1]) === JSON.stringify({ action: 'undefine', kit: 'radio' }));
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  check('the consent step is set apart (style.css)', /\.kit-consent \{[^}]*border: 1px solid var\(--warn\)/.test(css));
  const walker = d.createTreeWalker(d.body, w.NodeFilter.SHOW_TEXT);
  const stray = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/^(null|undefined|NaN|\[object Object\])$/.test(n.textContent.trim())) stray.push(n.textContent);
  check('no stray null or undefined on the page', !stray.length, stray.join(','));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
