// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Network pane in jsdom; BASE is a hub whose /admin/network answers without a login (a proxy that adds it).
// Usage: BASE=http://127.0.0.1:PORT [FRONT_SECRET=…] [JSDOM=…/jsdom] node dom-net.cjs; the hub at BASE must run this
// checkout's code (one left running from older code answers with old levels, and the dials' checks fail). FRONT_SECRET:
// the hub's HUB_FRONT_SECRET, sent as the web server in front sends it, so a hub started for the test answers as admin.
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
  if (opts.method === 'POST' && u === '/admin/health') { posted.push({ health: JSON.parse(opts.body) }); return new Response(JSON.stringify({ accepted: {} }), { status: 200 }); }
  if (opts.method === 'POST' && u === '/admin/network') return new Response(JSON.stringify({ id: 'x' }), { status: 202 });
  if (u === '/admin/network') {
    // The root helper's answer to what was posted: done, so the page is not left busy.
    const front = process.env.FRONT_SECRET ? { 'X-Irate-Front': process.env.FRONT_SECRET, 'X-Irate-Admin': '1' } : {};
    const data = await (await fetch(new URL(u, BASE), { ...opts, headers: { ...(opts.headers || {}), ...front } })).json();
    // A real inventory, its network's names taken out.
    data.inventory = JSON.parse(fs.readFileSync(`${__dirname}/netinv-fixture.json`, 'utf8'));
    // The WiFi country: the kernel's, none saved for boot, and two to choose from.
    Object.assign(data.inventory, { country: 'US', country_saved: null, countries: [['GB', 'Britain (UK)'], ['US', 'United States']] });
    // The access point wlan0 joins announces the United States (the Pi's router did, 2026-10-21).
    const linked = data.inventory.radios.find((r) => r.iface === 'wlan0' && r.link);
    if (linked) linked.link.country = 'US';
    // Roaming as the hardware scan reports it: three access points, two channels, the hotspot on the radio.
    const wl = data.inventory.radios.find((r) => r.iface === 'wlan0');
    wl.powersave_from = { where: 'default', value: '2' };
    // What irate-box knows of its chip (hub/known_radios.json).
    wl.known = { chip: 'Broadcom onboard (CYW43455, Raspberry Pi 3B+)', in_kernel: true, hotspot: true,
      notes: ['Power saving hurts it as a hotspot.'], tested_by: ['irate-box'] };
    wl.roaming = { ssid: wl.link.ssid, aps: [{ bssid: 'aa:00:00:00:00:01', channel: 6, freq: 2437, signal: 100 }, { bssid: 'aa:00:00:00:00:02', channel: 6, freq: 2437, signal: 87 },
      { bssid: 'aa:00:00:00:00:03', channel: 11, freq: 2462, signal: 80 }], channels: [6, 11], bgscan: 'simple:30:-65:300', nm_version: '1.52.1',
      choices: ['roam', 'no-scan', 'lock'], why: {}, hotspot_shares: true };
    // The watchdog's report, with a few events, for the event log.
    const now = Math.floor(Date.now() / 1000);
    // The radio's tests (radiotests.py): one run, one not.
    data.inventory.radio_tests = { wlan0: { key: 'k', can: ['radio-reset', 'follows-roam'],
      questions: { 'radio-reset': 'Does it come back after a reset?', 'follows-roam': 'Does the hotspot follow a roam?' },
      found: { 'radio-reset': { at: now - 86400, detail: 'it came back in 40 s' } } } };
    data.uplink = data.uplink || { at: now, state: 'up', iface: 'wlan0', link: {}, gateway: '192.168.1.1', backend: 'networkmanager',
      repairs: ['reconnect'], chosen: { pace: 'gentle', reach: 'reboot', guests: 'protect', on_wedge: 'ladder', sensitivity: 3, iface: 'auto', overrides: {} },
      events: [{ at: now - 600, text: 'wlan0 down: the gateway missed 3 checks' }, { at: now - 540, text: 'Reconnected wlan0 (nmcli device connect)' },
        { at: now - 530, text: 'wlan0 up again after 70 s' }] };
    data.results = [...(data.results || []), { id: 'x', ok: true, message: 'Done.' }];
    data.pending = 0;
    return new Response(JSON.stringify(data), { status: 200 });
  }
  const front = process.env.FRONT_SECRET ? { 'X-Irate-Front': process.env.FRONT_SECRET, 'X-Irate-Admin': '1' } : {};
  return fetch(new URL(u, BASE), { ...opts, headers: { ...(opts.headers || {}), ...front } });
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
  check('what it is doing, with the signal in words', /Current connection:? ?ExampleWiFi\d*, channel \d+ \(2\.4 GHz\), signal -\d+ dBm \((excellent|good|fair|weak|poor)\)/.test(card), card);
  check('Radio tests: each with its result, or Not run', /Radio tests/.test(card) && /Does it come back after a reset\? Tested [^:]+: it came back in 40 s\./.test(card)
    && /Does the hotspot follow a roam\? Not run\./.test(card) && !/Tested on this radio/.test(card), card);
  const ps = cards[0] && [...cards[0].querySelectorAll('.net-line')].find((n) => /^Power saving/.test(n.textContent));
  check('power saving as a setting: its value, where it is set, a link there; not a warning', ps && /Power saving\s*Off, set in Default connection settings\./.test(ps.textContent)
    && ps.querySelector('a').getAttribute('href') === '#net-defaults-card' && !/Power saving is enabled/.test(card), ps && ps.textContent);
  check('managed by, explained', /NetworkManager, the system's own network settings/.test(card));
  check('the hotspot: yes, with conditions', /Yes, with conditions \(through networkmanager\)/.test(card));
  const conds = cards[0] ? [...cards[0].querySelectorAll('.net-conditions li')].map((li) => li.className) : [];
  check('its conditions one per line, untested marked', conds.join(' ') === 'cond-how cond-limit cond-untested', conds.join(' '));
  const own = cards[0] ? [...cards[0].querySelectorAll('.net-facts:not(.radio-tests) li')].length : 0;
  check('its own findings on its card (3): to review, then what is known', own === 3, own);
  // Each labelled part a section, a hairline between them.
  const secs = cards[0] ? [...cards[0].children].filter((n) => n.classList.contains('net-section')) : [];
  // A device's uptime is on the Status tab, with the link it belongs to.
  check('the card in sections: what, the chip, current connection, managed by, power saving, the hotspot, radio tests, to review, known about it', secs.length === 9
    && secs.every((n) => n.querySelector('.net-line, .net-facts')) && !cards[0].querySelector('.net-uptime'), secs.length);
  // The page in tabs by role, with cards per device or connection type within each.
  const tabs = [...d.querySelectorAll('[data-tabs="network"] [role="tab"]')];
  check('four tabs: Status, Hardware, Connection settings, Hotspot', tabs.map((x) => x.textContent.replace(/\s+/g, ' ').trim()).join('|') === 'Status|Hardware|Connection settings|Hotspot');
  const panel = (id) => d.getElementById(id);
  check('  each part in its tab: devices on Hardware, the watchdog on access, guests\' security on Hotspot, the log on Status',
    panel('network/hardware').contains(d.getElementById('net-devices')) && panel('network/access').contains(d.getElementById('up-pace'))
    && panel('network/hotspot').contains(d.getElementById('hs-modes')) && panel('network/hotspot').contains(d.getElementById('guest-net-box'))
    && panel('network/status').contains(d.getElementById('up-events')));
  const sc = [...d.querySelectorAll('#net-status-cards .net-card')];
  check('Status: a card per link, with its uptime', sc.length === 1 && /wlan0/.test(sc[0].querySelector('h4').textContent) && !!sc[0].querySelector('.net-uptime')
    && /The box's link/.test(sc[0].textContent) && sc[0].querySelector('h4').textContent === 'wlan0', sc.map((c) => c.textContent.slice(0, 80)));
  check('  its kind and role as labels beside the name, not in its heading', [...sc[0].querySelectorAll('.fhead > .kind-tag')].map((x) => x.textContent).join('|') === "WiFi|The box's link");
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
  // An action acts at once; what it costs is said under the buttons, not asked.
  let popped = 0;
  w.confirm = () => { popped += 1; return true; };
  d.getElementById('net-join-now').click();
  w.confirm = () => true;
  const sentJoin = posted.find((p) => p.action === 'join');
  const joinSays = d.getElementById('net-join-says').textContent.replace(/\s+/g, ' ');
  check('  Add and connect: the page may lose the box, said under it; sent at once, the password cleared from the form', /this page can lose the box/.test(joinSays) && !popped
    && sentJoin && sentJoin.ssid === 'Cafe' && sentJoin.security === 'wpa-psk' && sentJoin.now === true && sentJoin.hidden === false && jf.elements.psk.value === '');
  const hsec = secs.find((n) => n.querySelector('.net-conditions'));
  check('the hotspot\'s conditions stay in its own section', hsec && /^Hotspot support/.test(hsec.textContent) && hsec.querySelectorAll('.net-line').length === 1);
  // One space wherever the stylesheet has any: the rules are checked by their content, not their layout.
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8').replace(/\s+/g, ' ');
  check('a hairline between sections, and under the heading (style.css)', /\.net-section \+ \.net-section \{ border-top: 1px solid var\(--border\)/.test(css)
    && /\.net-device h4 \{[^}]*border-bottom: 1px solid var\(--border\)/.test(css));
  check('labels in a column, above their text on a phone', /\.net-line \{ display: grid; grid-template-columns: 11rem 1fr/.test(css)
    && /max-width: 560px\) \{ \.net-line \{ grid-template-columns: 1fr/.test(css));
  const general = t('#net-hazards .net-device h4');
  check('the rest in the general list, a card each, not repeated', general.length === 2 && !general.some((g) => /handshake|retries/i.test(g)), general.join(' | '));
  const evs = [...d.querySelectorAll('#up-events li')];
  check('the event log: one line per event, newest first', evs.length === 3 && /up again/.test(evs[0].textContent)
    && evs.every((li) => li.querySelector('.ev-time') && li.querySelector('.ev-text') && !li.querySelector('.setting-desc')), evs.map((li) => li.innerHTML).join(' | '));
  // jsdom loads no stylesheet here, so the rule itself is checked.
  check('…with no bullets (style.css)', /\.net-events \{ list-style: none;/.test(css));
  check('a glossary, folded', d.getElementById('net-glossary') && !d.getElementById('net-glossary').open && d.querySelectorAll('#net-glossary dt').length >= 6);
  console.log('HAZARDS:'); t('#net-hazards li').forEach((r) => console.log('  ', r.slice(0, 160)));
  console.log('STATUS:', t('#up-status')[0]);
  console.log('FIELDS:', [...d.querySelectorAll('#up-fields [data-key]')].map((i) => `${i.dataset.key}=${i.value || '(' + (i.placeholder || i.options?.[0]?.textContent) + ')'}`).join('  '));
  console.log('PROFILE:', t('#up-profile')[0] || '(none)');
  console.log('EVENTS:'); t('#up-events li').slice(0, 6).forEach((r) => console.log('  ', r));
  // Network recovery: two dials, pace and reach, guests, a wedged driver and
  // the sensitivity, a number of missed checks, each choice as radio cards.
  const tiles = (id) => [...d.querySelectorAll(`#${id} .choice-tile`)];
  const vals = (id) => tiles(id).map((r) => r.querySelector('input').value).join(' ');
  const pace = tiles('up-pace'), reach = tiles('up-reach');
  check('pace: four cards, gentle to urgent', vals('up-pace') === 'gentle steady brisk urgent', vals('up-pace'));
  check('reach: five cards, watch to reboot', vals('up-reach') === 'watch reconnect restart radio reboot', vals('up-reach'));
  check('guests: two cards; a failed radio: three (the ladder, a reset at once, and a reboot if it isn\'t back)', vals('up-guests') === 'protect ignore'
    && vals('up-wedge') === 'ladder radio reboot', [vals('up-guests'), vals('up-wedge')]);
  // Pace, reach, guests and the retries: chips, and one line under them for the chosen one (its words said once).
  const saysOf = (id) => { const n = d.getElementById(id).nextElementSibling; return n && n.classList.contains('choice-says') ? n.textContent : null; };
  check('pace, reach, guests and the retries: a row of chips each, one line saying what the chosen one does',
    ['up-pace', 'up-reach', 'up-guests', 'up-retry', 'up-giveup'].every((id) => d.getElementById(id).classList.contains('choice-chips') && (saysOf(id) || '').length > 10),
    ['up-pace', 'up-reach', 'up-guests', 'up-retry', 'up-giveup'].map((id) => `${id}: ${saysOf(id)}`).join(' | '));
  check('  the chips\' words: reach in words, guests Defer or Allow, the period No limit to A week',
    tiles('up-reach').map((r) => r.textContent.trim()).join('|') === 'Watch only|Reconnect|Restart the network service|Reset the radio|Reboot'
    && tiles('up-guests').map((r) => r.textContent.trim()).join('|') === 'Defer|Allow'
    && tiles('up-giveup').map((r) => r.textContent.trim()).join('|') === 'No limit|An hour|A day|A week',
    [tiles('up-reach'), tiles('up-guests'), tiles('up-giveup')].map((x) => x.map((r) => r.textContent.trim()).join('|')).join(' / '));
  check('one chosen in each', ['up-pace', 'up-reach', 'up-guests', 'up-wedge'].every((id) => tiles(id).filter((r) => r.classList.contains('chosen')).length === 1));
  check('the gentle pace\'s line says when each step comes, once', /reboots after 2 h\. Each step runs only if the maximum action includes it/.test(saysOf('up-pace'))
    && !/Waits 5 minutes before the first reconnect/.test(saysOf('up-pace')), saysOf('up-pace'));
  const sens = d.getElementById('up-sens');
  check('sensitivity: a number, 1 to 20, 3 to start, said for the pace (30 min for gentle)', sens.type === 'number' && sens.min === '1' && sens.max === '20'
    && sens.value === '3' && /3 failed checks, or link drops, within 30 min/.test(t('#up-sens-says')[0]), t('#up-sens-says')[0]);
  check('no forgiveness left on the page', !d.getElementById('up-forgive') && !/[Ff]orgiveness/.test(d.getElementById('network').textContent));
  check('"what this will do" is shown', /^What this will do: it checks the link every/.test(t('#up-will')[0]), t('#up-will')[0]);
  const save = d.querySelector('#up-foot .aw-save'), upNote = () => t('#up-foot .aw-foot .note')[0];
  check('Save is off until something changes', save.disabled && save.textContent === 'Save' && /^Changes apply after/.test(upNote()), upNote());
  // Roaming: three tiles, roam normally chosen; a checkbox apart to ignore it.
  check('roaming: three tiles, Roam normally chosen, the ignore checkbox off', vals('up-roaming') === 'roam no-scan lock'
    && tiles('up-roaming')[0].classList.contains('chosen') && !d.getElementById('up-ignore-roams').checked, vals('up-roaming'));
  check('  the WiFi card says how many access points share the network, and that the hotspot moves with it', /3 access points for .*, on channels 6 and 11\./.test(t('#net-roaming')[0])
    && /hotspot shares this radio, so it moves too/.test(t('#net-roaming')[0]), t('#net-roaming')[0]);
  check('  the background scan in words', /Looks for a stronger access point every 30 s while the signal is weaker than -65 dBm, every 5 min while it is stronger/
    .test(t('#net-roaming')[0]), t('#net-roaming')[0]);
  check('  Hardware lists the access points and the choices that work here', /aa:00:00:00:00:03\s*Channel 11/.test(t('#net-roam-caps')[0])
    && /Lock to one access point\s*Available/.test(t('#net-roam-caps')[0]) && !d.querySelector('#net-roam-caps .ok-pill'), t('#net-roam-caps')[0]);
  tiles('up-roaming')[2].querySelector('input').click();
  const lockAps = d.getElementById('up-lock-aps');
  check('  choosing the lock shows the access points, the strongest preselected', !lockAps.hidden && lockAps.querySelectorAll('input').length === 3
    && lockAps.querySelector('input:checked').value === 'aa:00:00:00:00:01');
  tiles('up-roaming')[0].querySelector('input').click();
  check('  back to roaming: the picker goes', lockAps.hidden);
  pace[3].querySelector('input').click();
  check('choosing urgent marks it, and Save comes on, "Unsaved changes." beside it', pace[3].classList.contains('chosen') && !save.disabled && upNote() === 'Unsaved changes.');
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
    check('saved: Save off again', save.disabled && save.textContent === 'Save' && upNote() !== 'Unsaved changes.', upNote());
    chk.value = 'abc'; chk.dispatchEvent(new w.Event('input'));
    save.click();
  }, 1500);
  // The WiFi country: a dropdown, the kernel's country chosen; another one chosen is warned of beside it and saved.
  setTimeout(() => {
    const pick = d.querySelector('#net-country select');
    const warnNow = () => (d.querySelector('#net-country .aw-warn') || {}).textContent || '';
    check('WiFi country: a dropdown with "Not set" and the countries, the kernel\'s chosen, no warning',
      pick && pick.options.length === 3 && pick.value === 'US' && warnNow() === '', pick && `${pick.options.length} ${pick.value} "${warnNow()}"`);
    if (pick) { pick.value = 'GB'; pick.dispatchEvent(new w.Event('change')); }
    check('  another chosen: said beside it that the network the box joins announces another country',
      /wlan0 joins says it is in United States \(US\), so the radios use only the channels both countries allow/.test(warnNow()), warnNow());
    const save = d.querySelector('#net-country .aw-save');
    if (save) save.click();
  }, 2200);
  setTimeout(() => {
    const sent = posted.find((p) => p.action === 'settings');
    check('Save posts both dials, the rest, and custom values', sent && sent.settings.pace === 'urgent' && sent.settings.reach === 'radio'
      && sent.settings.guests === 'protect' && sent.settings.on_wedge === 'ladder' && sent.settings.sensitivity === 2
      && sent.settings.overrides.check === 45 && sent.settings.overrides.steps.reboot === null, JSON.stringify(posted));
    check('a bad custom value is refused on the page, said under Save', posted.filter((p) => p.action === 'settings').length === 1 && /a number/.test(t('#up-foot .aw-reason')[0] || ''), t('#up-foot .aw-reason')[0]);
    // A review item here can be acted on here: another hotspot profile deleted or ignored, any one accepted.
    const apCard = [...d.querySelectorAll('#net-hazards .net-device')].find((c) => /Another hotspot profile/.test(c.textContent));
    const apBtn = (word) => apCard && [...apCard.querySelectorAll('button')].find((b) => b.textContent === word);
    check('another hotspot profile: Delete profile, Ignore profile and Accept on its card', !!(apBtn('Delete profile') && apBtn('Ignore profile') && apBtn('Accept')),
      apCard && apCard.textContent);
    if (apBtn('Ignore profile')) apBtn('Ignore profile').click();
    check('  Ignore profile: asked of the box, for that profile', posted.some((b) => b.action === 'otherap' && b.do === 'leave' && b.uuid === '33564e5b-5cb3-4b45-ac5f-a092c4f7ddba'), JSON.stringify(posted.slice(-2)));
    if (apBtn('Accept')) apBtn('Accept').click();
    check('  Accept: on the Box doctor\'s list, as net:<its id>', posted.some((b) => b.health && b.health.action === 'accept' && b.health.key === 'net:other-ap:33564e5b-5cb3-4b45-ac5f-a092c4f7ddba'),
      JSON.stringify(posted.slice(-2)));
    // One Save and Discard per section: the box defaults' own under their card, none beside the retries.
    const words = [...d.querySelectorAll('#network button, #hotspot button')].map((b) => b.textContent.trim());
    check('Network: no Save named otherwise, none "(unsaved)", no second pair for the box defaults', !words.some((x) => /^Save .|\(unsaved\)/.test(x))
      && d.querySelectorAll('#net-defaults-foot .aw-save').length === 1 && !d.querySelector('#up-retries-group .aw-save'), words.filter((x) => /^Save/.test(x)).join('|'));
    check('  each network\'s own settings: Discard beside its Save', [...d.querySelectorAll('.net-conn-settings')].every((c) => !c.querySelector('.aw-save') || c.querySelector('.aw-discard')));
    check('  saved: the country posted to the box', posted.some((p) => p.action === 'country' && p.country === 'GB'), JSON.stringify(posted.filter((p) => p.action === 'country')));
    // Default connection settings' notes on other files, in words: a match-device list, the settings named by label.
    check('a file\'s section for some devices: its match said in words', w.eval('matchSaid')('driver:eagle_sdio,driver:wl') === 'devices with the driver eagle_sdio or wl'
      && w.eval('matchSaid')('interface-name:wlan0') === 'devices with the interface name wlan0'
      && w.eval('matchSaid')('type:wifi') === 'WiFi devices' && w.eval('matchSaid')('type:wifi,type:ethernet') === 'WiFi or wired devices',
    [w.eval('matchSaid')('driver:eagle_sdio,driver:wl'), w.eval('matchSaid')('type:wifi,type:ethernet')].join(' / '));
    check('  the settings it sets, by label, said whole', w.eval('listSaid')(['MAC address on WiFi', 'MAC address while scanning']) === 'MAC address on WiFi and MAC address while scanning');
    // The recovery limits' titles in words, the cable's reset as the adapter's.
    const reachTitles = [...d.querySelectorAll('#up-cablereach .choice-tile')].map((n) => n.textContent.trim()).join('|');
    check('Wired-link recovery limit: no step named "Radio"; the reset is the network adapter\'s', /Reset the network adapter/.test(reachTitles) && !/(^|\|)Radio\b/.test(reachTitles), reachTitles);
    const chipLine = [...d.querySelectorAll('#net-devices .net-line')].find((p) => /^Chip/.test(p.textContent));
    check('a known chip on its card: its name, its driver in the kernel, the notes, who tested it',
      chipLine && /Broadcom onboard/.test(chipLine.textContent) && /driver is in the kernel/.test(chipLine.textContent)
      && /Power saving hurts it/.test(chipLine.textContent) && /Tested by irate-box/.test(chipLine.textContent), chipLine && chipLine.textContent);
    check('no page errors', errors.length === 0, errors.join(' | '));
    console.log(fails ? `${fails} failure(s)` : 'ok');
    process.exit(fails ? 1 : 0);
  }, 3500);
}, 6000);
