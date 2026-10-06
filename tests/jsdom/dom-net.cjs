// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Network pane in jsdom; BASE is a hub whose /admin/network answers without a login (a proxy that adds it).
// Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-net.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const BASE = process.env.BASE || 'http://127.0.0.1:18098';
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const errors = [];
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => errors.push('console: ' + a.join(' ')));
const dom = new JSDOM(html, { url: BASE + '/admin/#network', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST' && u === '/admin/network') posted.push(JSON.parse(opts.body));
  if (opts.method === 'POST' && u === '/admin/network') return new Response(JSON.stringify({ id: 'x' }), { status: 202 });
  if (u === '/admin/network') {
    // The root helper's answer to what was posted: done, so the page is not left busy.
    const data = await (await fetch(new URL(u, BASE), opts)).json();
    // A real inventory (the Lyra's, 2026-10-06, its network's names taken out).
    data.inventory = JSON.parse(fs.readFileSync(`${__dirname}/netinv-fixture.json`, 'utf8'));
    // The watchdog's report, with a few events, for the event log.
    const now = Math.floor(Date.now() / 1000);
    data.uplink = data.uplink || { at: now, state: 'up', iface: 'wlan0', link: {}, gateway: '192.168.1.1', backend: 'networkmanager',
      repairs: ['reconnect'], chosen: { eagerness: 'patient', forgiveness: 'normal', iface: 'auto', overrides: {} },
      events: [{ at: now - 600, text: 'wlan0 down: the gateway missed 3 checks' }, { at: now - 540, text: 'Reconnected wlan0 (nmcli device connect)' },
        { at: now - 530, text: 'wlan0 up again after 70 s' }] };
    data.results = [...(data.results || []), { id: 'x', ok: true, message: 'Done.' }];
    data.pending = 0;
    return new Response(JSON.stringify(data), { status: 200 });
  }
  return fetch(new URL(u, BASE), opts);
};
w.confirm = () => true;
w.eval(js);
setTimeout(() => {
  const d = w.document;
  const t = (sel) => [...d.querySelectorAll(sel)].map((n) => n.textContent.replace(/\s+/g, ' ').trim());
  console.log('WHEN:', t('#net-when')[0]);
  console.log('DEVICES:', [...d.querySelectorAll('#net-device option')].map((o) => o.value || 'All').join(', '));
  // A card per device (2026-10-06): plain lines, the hotspot's conditions one per line, its warnings on it.
  const cards = [...d.querySelectorAll('#net-devices .net-device')];
  check('a card per device', cards.length === 1 && cards[0].querySelector('h4').textContent.startsWith('wlan0'), cards.length);
  const card = cards[0] ? cards[0].textContent : '';
  check('what it is, in words', /USB WiFi adapter, driver aic8800/.test(card), card.slice(0, 200));
  check('what it is doing, with the signal in words', /Connected to ExampleWiFi\d*, channel \d+ \(2\.4 GHz\), signal -\d+ dBm \((excellent|good|fair|weak|poor)\)/.test(card), card);
  check('managed by, explained', /NetworkManager, the system's own network settings/.test(card));
  check('the hotspot: yes, with conditions', /Yes, with conditions \(through networkmanager\)/.test(card));
  const conds = cards[0] ? [...cards[0].querySelectorAll('.net-conditions li')].map((li) => li.className) : [];
  check('its conditions one per line, untested marked', conds.join(' ') === 'cond-how cond-limit cond-untested', conds.join(' '));
  const own = cards[0] ? [...cards[0].querySelectorAll('.admin-checks li')].length : 0;
  check('its own warnings on its card (3)', own === 3, own);
  const general = t('#net-hazards li');
  check('the rest in the general list, not repeated', general.length === 2 && !general.some((g) => /handshake/.test(g)), general.join(' | '));
  const evs = [...d.querySelectorAll('#up-events li')];
  check('the event log: one line per event, newest first', evs.length === 3 && /up again/.test(evs[0].textContent)
    && evs.every((li) => li.querySelector('.ev-time') && li.querySelector('.ev-text') && !li.querySelector('.setting-desc')), evs.map((li) => li.innerHTML).join(' | '));
  // jsdom loads no stylesheet here, so the rule itself is checked.
  check('…with no bullets (style.css)', /\.net-events \{ list-style: none;/.test(fs.readFileSync(`${WEB}/style.css`, 'utf8')));
  check('a glossary, folded', d.getElementById('net-glossary') && !d.getElementById('net-glossary').open && d.querySelectorAll('#net-glossary dt').length >= 6);
  console.log('HAZARDS:'); t('#net-hazards li').forEach((r) => console.log('  ', r.slice(0, 160)));
  console.log('STATUS:', t('#up-status')[0]);
  console.log('FIELDS:', [...d.querySelectorAll('#up-fields [data-key]')].map((i) => `${i.dataset.key}=${i.value || '(' + (i.placeholder || i.options?.[0]?.textContent) + ')'}`).join('  '));
  console.log('PROFILE:', t('#up-profile')[0] || '(none)');
  console.log('EVENTS:'); t('#up-events li').slice(0, 6).forEach((r) => console.log('  ', r));
  // Staying on the network: eagerness and forgiveness both as radio cards (2026-10-06).
  const rungs = [...d.querySelectorAll('#up-eager .up-rung')];
  check('eagerness: five cards, off to stubborn', rungs.map((r) => r.querySelector('input').value).join(' ') === 'off patient standard persistent stubborn',
    rungs.map((r) => r.querySelector('input').value).join(' '));
  check('every card shows its description and what it does', rungs.every((r) => r.querySelectorAll('.setting-desc').length === 2 && r.querySelector('.up-does').textContent.length > 10));
  check('the chosen level is marked', rungs.filter((r) => r.classList.contains('chosen')).length === 1);
  check('stubborn\'s line says it reboots', /reboots after 30 min/.test(rungs[4].querySelector('.up-does').textContent), rungs[4].querySelector('.up-does').textContent);
  check('off\'s line says it never acts', /never acts/.test(rungs[0].querySelector('.up-does').textContent));
  const frungs = [...d.querySelectorAll('#up-forgive .up-rung')];
  check('forgiveness: radio cards too, tolerant to strict', frungs.map((r) => r.querySelector('input').value).join(' ') === 'tolerant normal strict'
    && frungs.every((r) => r.querySelectorAll('.setting-desc').length === 2) && frungs.filter((r) => r.classList.contains('chosen')).length === 1);
  check('no toggle left on the page', !d.querySelector('#up-forgive button'));
  check('"what this will do" is shown', /^What this will do: it checks the link every/.test(t('#up-will')[0]), t('#up-will')[0]);
  const save = d.getElementById('up-save');
  check('Save is off until something changes', save.disabled && save.textContent === 'Save');
  rungs[4].querySelector('input').click();
  check('choosing stubborn marks it, and Save comes on', rungs[4].classList.contains('chosen') && !save.disabled && /not saved yet/.test(save.textContent));
  check('the sentence follows: it reboots', /reboots after 30 min/.test(t('#up-will')[0]), t('#up-will')[0]);
  frungs[2].querySelector('input').click();
  check('strict chosen, its card says what it does', frungs[2].classList.contains('chosen') && /Down after 2 failed checks and 15 s more/.test(frungs[2].querySelector('.up-does').textContent),
    frungs[2].querySelector('.up-does').textContent);
  const reboot = d.querySelector('[data-key="steps.reboot"]'); reboot.value = 'off'; reboot.dispatchEvent(new w.Event('input'));
  const chk = d.querySelector('[data-key="check"]'); chk.value = '45'; chk.dispatchEvent(new w.Event('input'));
  check('custom values change the sentence', /every 45 s/.test(t('#up-will')[0]) && /never reboots/.test(t('#up-will')[0]), t('#up-will')[0]);
  save.click();
  // Once that is saved, Save is off again; a bad value typed in turns it on, and is refused.
  setTimeout(() => {
    check('saved: Save off again', save.disabled && save.textContent === 'Save');
    chk.value = 'abc'; chk.dispatchEvent(new w.Event('input'));
    save.click();
  }, 1500);
  setTimeout(() => {
    const sent = posted.find((p) => p.action === 'settings');
    check('Save posts the levels and custom values', sent && sent.settings.eagerness === 'stubborn' && sent.settings.forgiveness === 'strict'
      && sent.settings.overrides.check === 45 && sent.settings.overrides.steps.reboot === null, JSON.stringify(posted));
    check('a bad custom value is refused on the page', posted.filter((p) => p.action === 'settings').length === 1 && /a number/.test(t('#up-note')[0] || ''), t('#up-note')[0]);
    check('no page errors', errors.length === 0, errors.join(' | '));
    console.log(fails ? `${fails} failure(s)` : 'ok');
    process.exit(fails ? 1 : 0);
  }, 3500);
}, 6000);
