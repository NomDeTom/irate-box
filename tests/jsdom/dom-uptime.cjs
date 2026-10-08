// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The links' uptime on /admin → Network (next-work plan step 34) in jsdom, static: no hub needed.
// uptime-fixture.json is linkhistory.summarize's own output for 35 days of a WiFi uplink (a 15-minute
// outage on the 6th, an hour switched off, a day with no record) and a wired link down every night.
// Each card has a folded Uptime part: the week by hour (7 x 24), 35 days by day (5 x 7), a legend,
// the summary in words; each cell says what it was; a card with no record says how one comes.
// And the services' (step 35, from svchistory.summarize): a fold under Overview's table, closed,
// a row per service by hour for the week and by day for 35 days, the starts marked, kept open
// across the pane's redraws. Usage: [JSDOM=…/jsdom] node dom-uptime.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const fixture = JSON.parse(fs.readFileSync(`${__dirname}/uptime-fixture.json`, 'utf8'));
const inv = JSON.parse(fs.readFileSync(`${__dirname}/netinv-fixture.json`, 'utf8'));
inv.wired = [{ iface: 'eth0', bus: 'platform', driver: 'rk_gmac', carrier: true }, { iface: 'eth1', bus: 'usb', driver: 'r8152', carrier: false }];
const now = Math.floor(Date.now() / 1000);
const data = { inventory: inv, uptime: fixture.uptime, pending: 0, results: [],
  uplink: { at: now, state: 'up', iface: 'wlan0', link: {}, gateway: '192.168.1.1', backend: 'networkmanager', repairs: [], events: [],
    chosen: { eagerness: 'patient', forgiveness: 'normal', iface: 'auto', overrides: {} } },
  levels: fixture.levels };  // the watchdog's levels as network_snapshot sends them
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/HTTP 404/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#network', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const box = { system: {}, uptime: 3600, online: 0, joined: 0, version: 'test', results: [], pending: 0, service_uptime: fixture.services,
  services: [{ name: 'Library (Kiwix)', unit: 'kiwix.service', state: 'running', active: true, enabled: true, ops: [] },
    { name: 'MQTT broker', unit: 'mosquitto.service', state: 'running', active: true, enabled: true, ops: [] },
    { name: 'Notes', unit: 'notes.service', state: 'missing', active: false, enabled: false, ops: [] }] };
w.fetch = async (u) => (u === '/admin/network' ? new Response(JSON.stringify(data), { status: 200 })
  : u === '/admin/box' ? new Response(JSON.stringify(box), { status: 200 }) : new Response('{}', { status: 404 }));
// A page's scripts share their top-level consts; separate evals do not, so heatmap.js comes in as a var.
w.eval(fs.readFileSync(`${WEB}/heatmap.js`, 'utf8').replace(/^const Heatmap =/m, 'var Heatmap ='));
w.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));
w.eval(fs.readFileSync(`${WEB}/admin-layout.js`, 'utf8'));
w.eval(fs.readFileSync(`${WEB}/admin.js`, 'utf8'));
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
setTimeout(() => {
  const d = w.document;
  const card = (iface) => [...d.querySelectorAll('#net-devices .net-device')].find((c) => c.querySelector('h4').textContent.startsWith(iface));
  const wl = card('wlan0'), up = wl && wl.querySelector('details.net-uptime');
  check('the uplink\'s card: a folded Uptime part, closed', up && !up.open);
  check('  its summary in words: the share up, the drop, the longest outage', /Up 99\.2 % over the last 7 days \(168 hours recorded\); 1 drop; longest outage 15 min,/.test(t(up && up.querySelector('summary'))),
    t(up && up.querySelector('summary')));
  const [week, month] = up ? [...up.querySelectorAll('.heatmap')] : [];
  const rows = (g) => [...g.querySelectorAll('.hm-row:not(.hm-head)')];
  check('the week: 7 days by 24 hours', week && rows(week).length === 7 && rows(week).every((r) => r.querySelectorAll('.hm-cell').length === 24));
  const day6 = week && rows(week)[5].querySelectorAll('.hm-cell');
  check('  the outage\'s hour: partly down, a mark for the drop, and it says so', day6 && day6[3].classList.contains('hm-part') && day6[3].classList.contains('hm-mark')
    && /03:00–04:00: up 75 %, 1 drop$/.test(day6[3].getAttribute('aria-label')), day6 && day6[3].className + ' ' + day6[3].title);
  check('  the hour switched off: off, not down', day6 && day6[10].classList.contains('hm-off') && /switched off/.test(day6[10].title));
  check('  an hour fully up', day6 && day6[12].classList.contains('hm-full') && /up 100 %$/.test(day6[12].title), day6 && day6[12].title);
  const today = week && rows(week)[6].querySelectorAll('.hm-cell');
  check('  hours to come: no data', today && today[23].classList.contains('hm-none') && /no data$/.test(today[23].title));
  check('35 days: 5 weeks of 7', month && rows(month).length === 5 && rows(month).every((r) => r.querySelectorAll('.hm-cell').length === 7));
  check('  the day with no record: no data', month && [...month.querySelectorAll('.hm-cell')].filter((c) => c.classList.contains('hm-none')).length === 1);
  check('a legend, and what up means for the uplink', up && up.querySelectorAll('.hm-legend .hm-key').length === 6 && /the gateway answered/.test(t(up)));
  const eth = card('eth0'), ethUp = eth && eth.querySelector('details.net-uptime');
  check('a wired link: its own heatmap, link only, said', ethUp && ethUp.querySelectorAll('.heatmap').length === 2 && /Only whether the link was up/.test(t(ethUp))
    && /Up 6\d % over the last 7 days.*longest outage 500 min/.test(t(ethUp.querySelector('summary'))), t(ethUp && ethUp.querySelector('summary')));
  const none = card('eth1'), noneUp = none && none.querySelector('details.net-uptime');
  check('a link with no record: says so, and how one comes', noneUp && /Not recorded yet\./.test(t(noneUp.querySelector('summary'))) && !noneUp.querySelector('.heatmap')
    && /once the box's clock is known to be right/.test(t(noneUp)));
  // The services' fold, under Overview's table.
  w.location.hash = '#overview'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  w.eval('loadBox()');
  setTimeout(() => {
    const fold = d.getElementById('svc-uptime');
    check('services: a fold under the table, closed by default', fold && !fold.open && /Uptime, last 7 days/.test(t(fold.querySelector('summary'))));
    const [sweek, smonth] = fold ? [...fold.querySelectorAll('.heatmap')] : [];
    const srows = sweek ? [...sweek.querySelectorAll('.hm-row:not(.hm-head)')] : [];
    check('  a row per recorded service (not one never recorded), 168 hours each', srows.length === 2 && srows.every((r) => r.querySelectorAll('.hm-cell').length === 168)
      && t(srows[0].querySelector('.hm-label')) === 'Library (Kiwix)', srows.map((r) => t(r.querySelector('.hm-label'))));
    const k = srows[0] ? [...srows[0].querySelectorAll('.hm-cell')] : [];
    const marked = k.filter((c) => c.classList.contains('hm-mark'));
    check('  the hour it stopped and started again: partly down, marked, said', marked.length === 1 && marked[0].classList.contains('hm-part')
      && /Library \(Kiwix\), Tue 06 03:00–04:00: up 50 %, 1 restart$/.test(marked[0].title), marked.map((c) => c.title).join(' | '));
    check('  35 days by day too, with the hub\'s dates', smonth && smonth.querySelectorAll('.hm-row:not(.hm-head)')[0].querySelectorAll('.hm-cell').length === 35
      && /Library \(Kiwix\), Wed 07: up 100 %$/.test([...smonth.querySelectorAll('.hm-row:not(.hm-head)')[0].querySelectorAll('.hm-cell')].pop().title));
    check('  the week in a line', /Library \(Kiwix\): up 99\.\d %, started 1 time; MQTT broker: up 100 %\./.test(t(fold.querySelector('#svc-uptime-body > p'))), t(fold.querySelector('#svc-uptime-body > p')));
    fold.open = true; w.eval('loadBox()');
    setTimeout(() => {
      check('  open stays open as the pane redraws', d.getElementById('svc-uptime').open && d.querySelectorAll('#svc-uptime .heatmap').length === 2);
      check('no page errors', !errors.length, errors.join(' | '));
      console.log(`failures: ${fails}`);
      process.exit(fails ? 1 : 0);
    }, 200);
  }, 300);
}, 300);
