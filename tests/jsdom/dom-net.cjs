// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Network pane in jsdom; BASE is a hub whose /admin/network answers without a login (a proxy that adds it).
// Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-net.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const BASE = process.env.BASE || 'http://127.0.0.1:18098';
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
// It reads a running hub's /admin/network: with none at BASE it says so and stops, rather than fail on nothing.
if (require('child_process').spawnSync('curl', ['-s', '-o', '/dev/null', '-m', '3', new URL('/status', BASE).href]).status !== 0) {
  console.log(`skipped: no hub answering at ${BASE} (this test reads a hub's /admin/network; start one and set BASE)`);
  process.exit(0);
}
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
    // A real inventory, its network's names taken out.
    data.inventory = JSON.parse(fs.readFileSync(`${__dirname}/netinv-fixture.json`, 'utf8'));
    // Roaming as the hardware scan reports it: three access points, two channels, the hotspot on the radio.
    const wl = data.inventory.radios.find((r) => r.iface === 'wlan0');
    wl.roaming = { ssid: wl.link.ssid, aps: [{ bssid: 'aa:00:00:00:00:01', channel: 6, freq: 2437, signal: 100 }, { bssid: 'aa:00:00:00:00:02', channel: 6, freq: 2437, signal: 87 },
      { bssid: 'aa:00:00:00:00:03', channel: 11, freq: 2462, signal: 80 }], channels: [6, 11], bgscan: 'simple:30:-65:300', nm_version: '1.52.1',
      choices: ['roam', 'no-scan', 'lock'], why: {}, hotspot_shares: true };
    // The watchdog's report, with a few events, for the event log.
    const now = Math.floor(Date.now() / 1000);
    data.uplink = data.uplink || { at: now, state: 'up', iface: 'wlan0', link: {}, gateway: '192.168.1.1', backend: 'networkmanager',
      repairs: ['reconnect'], chosen: { pace: 'gentle', reach: 'reboot', guests: 'protect', on_wedge: 'ladder', sensitivity: 3, iface: 'auto', overrides: {} },
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
  console.log('DEVICES:', [...d.querySelectorAll('#up-iface option')].map((o) => o.value).join(', '));
  // A card per device: plain lines, the hotspot's conditions one per line, its warnings on it.
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
  // Each labelled part a section, a hairline between them.
  const secs = cards[0] ? [...cards[0].children].filter((n) => n.classList.contains('net-section')) : [];
  // A device's uptime is on the Status tab, with the link it belongs to.
  check('the card in sections: what, now, managed by, the hotspot, its warnings', secs.length === 5
    && secs.every((n) => n.querySelector('.net-line, .admin-checks')) && !cards[0].querySelector('.net-uptime'), secs.length);
  // The page in tabs by role, with cards per device or connection type within each.
  const tabs = [...d.querySelectorAll('[data-tabs="network"] [role="tab"]')];
  check('four tabs: Status, Hardware, The box\'s access, Hotspot', tabs.map((x) => x.textContent).join('|') === 'Status|Hardware|The box\'s access|Hotspot');
  const panel = (id) => d.getElementById(id);
  check('  each part in its tab: devices on Hardware, the watchdog on access, guests\' security on Hotspot, the log on Status',
    panel('network/hardware').contains(d.getElementById('net-devices')) && panel('network/access').contains(d.getElementById('up-pace'))
    && panel('network/hotspot').contains(d.getElementById('hs-modes')) && panel('network/hotspot').contains(d.getElementById('guest-net-box'))
    && panel('network/status').contains(d.getElementById('up-events')));
  const sc = [...d.querySelectorAll('#net-status-cards .net-card')];
  check('Status: a card per link, with its uptime', sc.length === 1 && /wlan0/.test(sc[0].querySelector('h4').textContent) && !!sc[0].querySelector('.net-uptime')
    && /the box's link/.test(sc[0].textContent), sc.map((c) => c.textContent.slice(0, 80)));
  check('  one line for the whole', /reaches your network through wlan0 \(WiFi\)/.test(t('#net-overview')[0]), t('#net-overview')[0]);
  check('Access: WiFi now, and the networks it knows', /ExampleWiFi/.test(t('#net-wifi-now')[0]) && d.querySelectorAll('#net-saved li').length >= 1
    && /in use/.test(t('#net-saved')[0]), t('#net-saved')[0]);
  w.location.hash = '#network/hotspot';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('an address opens its tab (#network/hotspot)', !panel('network/hotspot').hidden && panel('network/status').hidden
    && d.getElementById('tab-net-hotspot').getAttribute('aria-selected') === 'true');
  w.location.hash = '#up-sens';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  check('  and so does any setting\'s own (#up-sens on access)', !panel('network/access').hidden);
  // A network for the box to join: open hides the password; each add asked first; the form sent and the password cleared.
  const jf = d.getElementById('net-join-form');
  jf.elements.ssid.value = 'Cafe'; jf.elements.security.value = 'open'; jf.elements.security.dispatchEvent(new w.Event('change'));
  check('join: an open network asks no password', d.getElementById('net-join-psk').hidden && !jf.elements.psk.required);
  jf.elements.security.value = 'wpa-psk'; jf.elements.security.dispatchEvent(new w.Event('change'));
  jf.elements.psk.value = 'a-long-secret';
  let asked = '';
  w.confirm = (q) => { asked = q; return true; };
  d.getElementById('net-join-now').click();
  w.confirm = () => true;
  const sentJoin = posted.find((p) => p.action === 'join');
  check('  Add and join it now: warned the page may lose the box, sent, the password cleared from the form', /may lose the box/.test(asked)
    && sentJoin && sentJoin.ssid === 'Cafe' && sentJoin.security === 'wpa-psk' && sentJoin.now === true && sentJoin.hidden === false && jf.elements.psk.value === '');
  check('the hotspot\'s conditions stay in its own section', secs[3] && secs[3].querySelector('.net-conditions') && /hotspot/.test(secs[3].textContent));
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  check('a hairline between sections, and under the heading (style.css)', /\.net-section \+ \.net-section \{ border-top: 1px solid var\(--border\)/.test(css)
    && /\.net-device h4 \{[^}]*border-bottom: 1px solid var\(--border\)/.test(css));
  check('labels in a column, above their text on a phone', /\.net-line \{ display: grid; grid-template-columns: 11rem 1fr/.test(css)
    && /max-width: 560px\) \{ \.net-line \{ grid-template-columns: 1fr/.test(css));
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
  // Staying on the network: two dials, pace and reach, guests, a wedged driver and
  // the sensitivity, a number of missed checks, each choice as radio cards.
  const tiles = (id) => [...d.querySelectorAll(`#${id} .choice-tile`)];
  const vals = (id) => tiles(id).map((r) => r.querySelector('input').value).join(' ');
  const pace = tiles('up-pace'), reach = tiles('up-reach');
  check('pace: four cards, gentle to urgent', vals('up-pace') === 'gentle steady prompt urgent', vals('up-pace'));
  check('reach: five cards, watch to reboot', vals('up-reach') === 'watch reconnect restart radio reboot', vals('up-reach'));
  check('guests and a wedged driver: two cards each', vals('up-guests') === 'protect ignore' && vals('up-wedge') === 'ladder radio');
  check('every pace and reach card shows its description and what it does', [...pace, ...reach].every((r) => r.querySelectorAll('.setting-desc').length === 2
    && r.querySelector('.choice-does').textContent.length > 10));
  check('one chosen in each', ['up-pace', 'up-reach', 'up-guests', 'up-wedge'].every((id) => tiles(id).filter((r) => r.classList.contains('chosen')).length === 1));
  check('the gentle pace\'s line says when each step comes', /reboots after 2 h — each only if the reach goes that far/.test(pace[0].querySelector('.choice-does').textContent),
    pace[0].querySelector('.choice-does').textContent);
  check('watch\'s line says it never acts', /never acts/.test(reach[0].querySelector('.choice-does').textContent));
  const sens = d.getElementById('up-sens');
  check('sensitivity: a number, 1 to 20, 3 to start, said for the pace (30 min for gentle)', sens.type === 'number' && sens.min === '1' && sens.max === '20'
    && sens.value === '3' && /3 missed checks \(or drops of the link\) within 30 min/.test(t('#up-sens-says')[0]), t('#up-sens-says')[0]);
  check('no forgiveness left on the page', !d.getElementById('up-forgive') && !/[Ff]orgiveness/.test(d.getElementById('network').textContent));
  check('"what this will do" is shown', /^What this will do: it checks the link every/.test(t('#up-will')[0]), t('#up-will')[0]);
  const save = d.getElementById('up-save');
  check('Save is off until something changes', save.disabled && save.textContent === 'Save');
  // Roaming: three tiles, roam naturally chosen; a checkbox apart to ignore it.
  check('roaming: three tiles, Roam naturally chosen, the ignore checkbox off', vals('up-roaming') === 'roam no-scan lock'
    && tiles('up-roaming')[0].classList.contains('chosen') && !d.getElementById('up-ignore-roams').checked, vals('up-roaming'));
  check('  the WiFi card says how many access points share the network, and that the hotspot moves with it', /3 access points share .*channel 6, channel 11/.test(t('#net-roaming')[0])
    && /hotspot shares this radio/.test(t('#net-roaming')[0]), t('#net-roaming')[0]);
  check('  Hardware lists the access points and the choices that work here', /aa:00:00:00:00:03 ch 11/.test(t('#net-roam-caps')[0]) && /Lock to one access point: yes/.test(t('#net-roam-caps')[0]));
  tiles('up-roaming')[2].querySelector('input').click();
  const lockAps = d.getElementById('up-lock-aps');
  check('  choosing the lock shows the access points, the strongest preselected', !lockAps.hidden && lockAps.querySelectorAll('input').length === 3
    && lockAps.querySelector('input:checked').value === 'aa:00:00:00:00:01');
  tiles('up-roaming')[0].querySelector('input').click();
  check('  back to roaming: the picker goes', lockAps.hidden);
  pace[3].querySelector('input').click();
  check('choosing urgent marks it, and Save comes on', pace[3].classList.contains('chosen') && !save.disabled && /not saved yet/.test(save.textContent));
  check('the sentence follows: urgent reboots after 30 min', /reboots after 30 min/.test(t('#up-will')[0]), t('#up-will')[0]);
  reach[3].querySelector('input').click();
  check('reach radio: the sentence says it never reboots', /resets the radio after 8 min/.test(t('#up-will')[0]) && /never reboots/.test(t('#up-will')[0]), t('#up-will')[0]);
  sens.value = '2'; sens.dispatchEvent(new w.Event('input'));
  check('sensitivity 2: the sentence follows (2 checks within urgent\'s 2 minutes)', /When 2 checks have failed, or the link has dropped, within 2 min/.test(t('#up-will')[0]), t('#up-will')[0]);
  sens.value = '9'; sens.dispatchEvent(new w.Event('input'));
  check('  a number the checks alone can\'t reach in the window is said so', /Only 5 checks fit that window/.test(t('#up-sens-says')[0]), t('#up-sens-says')[0]);
  sens.value = '2'; sens.dispatchEvent(new w.Event('input'));
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
    check('Save posts both dials, the rest, and custom values', sent && sent.settings.pace === 'urgent' && sent.settings.reach === 'radio'
      && sent.settings.guests === 'protect' && sent.settings.on_wedge === 'ladder' && sent.settings.sensitivity === 2
      && sent.settings.overrides.check === 45 && sent.settings.overrides.steps.reboot === null, JSON.stringify(posted));
    check('a bad custom value is refused on the page', posted.filter((p) => p.action === 'settings').length === 1 && /a number/.test(t('#up-note')[0] || ''), t('#up-note')[0]);
    check('no page errors', errors.length === 0, errors.join(' | '));
    console.log(fails ? `${fails} failure(s)` : 'ok');
    process.exit(fails ? 1 : 0);
  }, 3500);
}, 6000);
