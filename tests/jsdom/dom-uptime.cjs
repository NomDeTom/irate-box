// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The links' uptime on /admin → Network → Status (a card per link) in jsdom, static: no hub needed.
// uptime-fixture.json is linkhistory.summarize's own output (made by a one-off script, from real
// record() calls) for a WiFi uplink (nothing for its first two hours, a 15-minute outage in hour
// 10, an hour switched off, the current hour partial) and a wired link down every night; the 72
// days by day mostly empty (only the last ~3 days were recorded). githubstatus.com's format:
// one row, one cell per hour or day. Each card has a folded Uptime part: the last 72 hours by
// hour, 72 days by day, a legend, the summary in words; each cell says what it was; a card with
// no record says how one comes.
// And the services' (from svchistory.summarize): a fold under Overview's table, closed,
// a row per service by hour for the last 72 hours and by day for 72 days, the starts marked, kept
// open across the pane's redraws. And the network's 72 hours on Overview, under the tiles, and
// meshtasticd's where it is installed; a tooltip for any cell, by pointer, tap or keys.
// Usage: [JSDOM=…/jsdom] node dom-uptime.cjs
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
    chosen: { pace: 'gentle', reach: 'reboot', guests: 'protect', on_wedge: 'ladder', sensitivity: 3, iface: 'auto', overrides: {}, steps_off: { wlan0: ['restart'] } } },
  levels: fixture.levels };  // the watchdog's levels as network_snapshot sends them
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/HTTP 404/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#network', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
// The Overview's network rows: net_uptime_72h()'s shape, the uplink first.
const netUp = Object.fromEntries(['wlan0', 'eth0'].filter((i) => fixture.uptime[i]).map((i) => [i,
  (({ kind, hours, hour_cols, hour_full, summary }) => ({ kind, hours, hour_cols, hour_full, summary }))(fixture.uptime[i])]));
// meshtasticd where installed: failed, its crash loop ended by systemd's start limit; sampled with the services.
const svcWithMesh = JSON.parse(JSON.stringify(fixture.services));
svcWithMesh.units['meshtasticd.service'] = svcWithMesh.units['kiwix.service'];
const box = { meshtasticd: { active: 'failed', sub: 'failed', result: 'start-limit-hit', restarts: 5, burst: 5, interval: '3min 20s' },
  system: { boot_uptime: 7500, mem_total: 1024, mem_available: 512, swap_total: 256, swap_free: 128 },
  memory_history: { now, every: 300, samples: [
    { at: now - 2 * 86400, total: 1024, available: 800, swap_total: 256, swap_free: 256 },
    { at: now - 900, total: 1024, available: 700, swap_total: 256, swap_free: 256 },
    { at: now - 300, total: 1024, available: 600, swap_total: 256, swap_free: 200 },
    { at: now, total: 1024, available: 512, swap_total: 256, swap_free: 128 }],
    processes_at: now, processes: [{ pid: 42, name: '<hub>', resident: 300 }] },
  uptime: 11 * 86400 + 5 * 3600, online: 0, joined: 0, version: 'test', results: [], pending: 0, service_uptime: svcWithMesh, net_uptime: netUp,
  services: [{ name: 'Library (Kiwix)', unit: 'kiwix.service', state: 'running', active: true, enabled: true, ops: [] },
    { name: 'MQTT broker', unit: 'mosquitto.service', state: 'running', active: true, enabled: true, ops: [] },
    { name: 'Notes', unit: 'notes.service', state: 'missing', active: false, enabled: false, ops: [] },
    { name: 'Remote access (Tailscale)', unit: 'tailscaled.service', state: 'stopped', active: false, enabled: false, ops: [] }] };
// Tailscale ran most of the week and is switched off now: its record says up, and the page must say off.
box.service_uptime.units['tailscaled.service'] = JSON.parse(JSON.stringify(box.service_uptime.units['mosquitto.service']));
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
  const card = (iface) => [...d.querySelectorAll('#net-status-cards .net-card')].find((c) => c.querySelector('h4').textContent.startsWith(iface));
  const wl = card('wlan0'), up = wl && wl.querySelector('div.net-uptime');
  check('the uplink\'s card: the Uptime part shown, not folded (no expander)', up && !up.closest('details') && !up.querySelector('summary') && up.querySelector('.heatmap'));
  check('  its summary in words: the share up, the drop, the longest outage', /Up 98\.2 % over the last 72 hours \(69\.5 hours recorded\); 1 drop; longest outage 15 min,/.test(t(up && up.querySelector('.net-line'))),
    t(up && up.querySelector('.net-line')));
  const [hours, month] = up ? [...up.querySelectorAll('.heatmap')] : [];
  const rows = (g) => [...g.querySelectorAll('.hm-row:not(.hm-head)')];
  check('the last 72 hours: one row, 72 cells', hours && rows(hours).length === 1 && rows(hours)[0].querySelectorAll('.hm-cell').length === 72);
  const cells = hours && rows(hours)[0].querySelectorAll('.hm-cell');
  check('  before the record began: no data', cells && cells[0].classList.contains('hm-none') && cells[1].classList.contains('hm-none'));
  check('  the outage\'s hour: partly down, a mark for the drop, and it says so', cells && cells[10].classList.contains('hm-part') && cells[10].classList.contains('hm-mark')
    && /Mon 05 03:00–04:00: up 75 %, 1 drop$/.test(cells[10].getAttribute('aria-label')), cells && cells[10].dataset.words);
  check('  the hour switched off: off, not down', cells && cells[20].classList.contains('hm-off') && /switched off/.test(cells[20].dataset.words));
  check('  the current hour, only partly sampled, up so far', cells && cells[71].classList.contains('hm-full') && /up 100 %$/.test(cells[71].dataset.words), cells && cells[71].dataset.words);
  // The tooltip: one for the page, the cell's words, shown on pointing, on a tap, and by the arrow keys.
  const tipOf = () => d.querySelector('body > .hm-tip');
  cells[10].dispatchEvent(new w.MouseEvent('pointerover', { bubbles: true }));
  check('the tooltip: pointing at a cell shows its words, the cell ringed', tipOf() && !tipOf().hidden && tipOf().textContent === cells[10].dataset.words
    && cells[10].classList.contains('hm-on') && !cells[10].hasAttribute('title'), tipOf() && tipOf().textContent);
  hours.dispatchEvent(new w.MouseEvent('pointerleave', { bubbles: false }));
  check('  leaving hides it', tipOf().hidden && !cells[10].classList.contains('hm-on'));
  cells[20].dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  check('  a tap shows it too', !tipOf().hidden && /switched off/.test(tipOf().textContent));
  hours.focus();
  check('  the grid takes focus: the cell kept, else the newest', hours.tabIndex === 0 && !tipOf().hidden && hours.getAttribute('aria-activedescendant') === cells[20].id, tipOf().textContent);
  hours.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  hours.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
  check('  End, then ←: the hour before the newest', tipOf().textContent === cells[70].dataset.words && cells[70].classList.contains('hm-on'), tipOf().textContent);
  hours.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  check('  Escape hides it', tipOf().hidden);
  hours.blur();
  check('  one tooltip for the page', d.querySelectorAll('.hm-tip').length === 1);
  check('72 days, one row', month && rows(month).length === 1 && rows(month)[0].querySelectorAll('.hm-cell').length === 72);
  check('  most days with no record: no data', month && [...month.querySelectorAll('.hm-cell')].filter((c) => c.classList.contains('hm-none')).length >= 60);
  check('a legend, and what up means for the uplink', up && up.querySelectorAll('.hm-legend .hm-key').length === 6 && /the gateway answered/.test(t(up)));
  const eth = card('eth0'), ethUp = eth && eth.querySelector('div.net-uptime');
  check('a wired link: its own heatmap, link only, said', ethUp && ethUp.querySelectorAll('.heatmap').length === 2 && /Only whether the link was up/.test(t(ethUp))
    && /Up 95\.6 % over the last 72 hours.*longest outage 60 min/.test(t(ethUp.querySelector('.net-line'))), t(ethUp && ethUp.querySelector('.net-line')));
  const none = card('eth1'), noneUp = none && none.querySelector('div.net-uptime');
  check('a link with no record: says so, and how one comes', noneUp && /Not recorded yet\./.test(t(noneUp.querySelector('.net-line'))) && !noneUp.querySelector('.heatmap')
    && /once the box's clock is known to be right/.test(t(noneUp)));
  // The services' strips, each in its own row of Overview's table.
  w.location.hash = '#overview'; w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  w.eval('loadBox()');
  setTimeout(() => {
    check('services: the strips not folded, in the table', !d.getElementById('svc-uptime').closest('details') && !d.getElementById('service-table').closest('details')
      && d.querySelectorAll('#service-table .svc-strips').length >= 2);
    const rowOf = (name) => [...d.querySelectorAll('#service-table tbody tr')].find((r) => t(r.querySelector('.svc-name .setting-name')) === name);
    const kiwix = rowOf('Library (Kiwix)');
    const [shours, smonth] = kiwix ? [...kiwix.querySelectorAll('.heatmap')] : [];
    check('  a recorded service\'s row has its 72 hours and its 72 days, bare strips', shours && smonth && shours.classList.contains('hm-bare')
      && shours.querySelectorAll('.hm-cell').length === 72 && smonth.querySelectorAll('.hm-cell').length === 72);
    check('  name and unit on one line (one cell)', kiwix && kiwix.querySelectorAll('.svc-name .setting-desc').length >= 1);
    const k = shours ? [...shours.querySelectorAll('.hm-cell')] : [];
    const marked = k.filter((c) => c.classList.contains('hm-mark'));
    check('  the hour it stopped and started again: partly down, marked, said', marked.length === 1 && marked[0].classList.contains('hm-part')
      && /Library \(Kiwix\), Mon 05 03:00–04:00: up 83 %, 1 restart$/.test(marked[0].dataset.words), marked.map((c) => c.dataset.words).join(' | '));
    check('  72 days by day too, with the hub\'s dates', smonth && /Library \(Kiwix\), Wed 07: up 100 %$/.test([...smonth.querySelectorAll('.hm-cell')].pop().dataset.words));
    check('  a service never recorded: a dash, no strip', [...d.querySelectorAll('#service-table tbody tr')].some((r) => !r.querySelector('.heatmap')));
    const body = d.getElementById('svc-uptime-body');
    const line = t(d.querySelector('#svc-uptime-body > p'));
    check('  one line: all up (Kiwix at 99.x % is up all the time, as the key says), no restart counts (the strips\' dots)',
      /^All up over the last 72 hours\./.test(line) && !/MQTT|Kiwix|started/.test(line), line);
    const worse = JSON.parse(JSON.stringify(box.service_uptime));
    worse.units['kiwix.service'].summary.up = 0.9;
    w.renderServiceUptime(box.services, worse, null);
    check('  a service down part of the time: only it named, with its share', /^Over the last 72 hours, all up but: Library \(Kiwix\) up 90\.0 %\./.test(t(d.querySelector('#svc-uptime-body > p'))),
      t(d.querySelector('#svc-uptime-body > p')));
    w.renderServiceUptime(box.services, box.service_uptime, null);
    // Remote access in words: off; running but not signed in to a Tailscale network (not reachable); connected.
    const rt = (d2) => w.remoteText(d2);
    check('Tailscale off: said off', rt({ state: 'stopped' }) === 'Tailscale is off.');
    check('  running, not signed in: said, with how to sign in or switch it off', /not signed in to a Tailscale network yet.*sudo tailscale up.*Or switch it off here\./.test(rt({ state: 'running', backend: { state: 'needs-login' } })));
    check('  connected: its name, its tailnet and its address', rt({ state: 'running', backend: { state: 'connected', name: 'lyra.tail1.ts.net', tailnet: 'owner@', ip: '100.64.0.5' } })
      === 'Tailscale is on: this box is lyra.tail1.ts.net on owner@ (100.64.0.5).');
    check('  a service switched off now: said as off, not as its past uptime, with where to switch it', /Remote access \(Tailscale\) is switched off\./.test(line)
      && !/Tailscale\) up/.test(line) && d.querySelector('#svc-uptime-body > p a.go-btn') && d.querySelector('#svc-uptime-body > p a.go-btn').getAttribute('href') === '#access', line);
    const key = body.querySelector('details.svc-key');
    check('  the colours\' key folded under the line, closed', key && !key.open && t(key.querySelector('summary')) === 'What the colours mean' && key.querySelectorAll('.hm-legend .hm-key').length === 6);
    check('no page errors', !errors.length, errors.join(' | '));
    const on = d.getElementById('overview-net');
    const tiles = d.getElementById('box-tiles'), table = d.getElementById('service-table');
    check('Overview separates current boot uptime from the recorded total',
      /Since boot.*0 d 2 h 5 min/.test(t(tiles)) && /Total recorded powered-on time.*11 d 5 h across boots/.test(t(tiles)), t(tiles));
    const memory = d.getElementById('memory-graph'), memoryFold = d.getElementById('memory-history');
    check('Overview memory history is collapsed by default', !memoryFold.open && !memory.querySelector('svg'));
    memoryFold.open = true;
    w.renderMemoryHistory(box.memory_history, box.system);
    check('Overview memory: memory and swap paths, current use and a sample readout', memory.querySelectorAll('path').length === 2
      && /Memory used: 50%/.test(t(d.getElementById('memory-current'))) && /50% memory used/.test(t(d.getElementById('memory-readout'))));
    check('Overview memory: 24 hours excludes older samples', /3 samples/.test(t(d.getElementById('memory-history-note'))));
    d.querySelector('[data-memory-hours="168"]').click();
    check('Overview memory: seven-day range includes older samples, preference selected', /4 samples/.test(t(d.getElementById('memory-history-note')))
      && d.querySelector('[data-memory-hours="168"]').getAttribute('aria-pressed') === 'true');
    check('Overview memory: gaps are not joined', (memory.querySelector('path').getAttribute('d').match(/M/g) || []).length === 2);
    {
      // The disk where samples have it: a line of its own, a new stroke where the disk's size changed; none fabricated before.
      const withDisk = { ...box.memory_history, samples: box.memory_history.samples.map((s, i) => (i === 0 ? s
        : { ...s, disk_total: i === 3 ? 16000 : 8000, disk_free: i === 3 ? 12000 : 4000 })) };
      w.renderMemoryHistory(withDisk, { ...box.system, disk_total: 16000, disk_free: 12000 });
      const dp = memory.querySelector('path.memory-disk-line');
      check('Overview disk: its own line from the samples that have it, a new stroke where its size changed', dp
        && (dp.getAttribute('d').match(/M/g) || []).length === 2 && (dp.getAttribute('d').match(/[ML]/g) || []).length === 3, dp && dp.getAttribute('d'));
      check('  the legend and the last sample\'s readout say it', /Disk used: /.test(t(d.getElementById('memory-current')))
        && /disk 25% used/.test(t(d.getElementById('memory-readout'))) && /disk 25% used at the last sample/.test(t(d.getElementById('memory-history-note'))), t(d.getElementById('memory-readout')));
      w.renderMemoryHistory(box.memory_history, box.system);
      check('  samples without it: no disk line, said as not yet', !memory.querySelector('path.memory-disk-line') && /no disk samples yet/.test(t(d.getElementById('memory-history-note'))));
    }
    memory.querySelector('svg').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
    check('Overview memory: keyboard selects the first recorded sample', /22% memory used/.test(t(d.getElementById('memory-readout'))));
    check('Overview memory: largest processes are safely rendered as text', !d.getElementById('memory-processes').hidden
      && /<hub>/.test(t(d.getElementById('memory-processes-body'))) && !d.querySelector('#memory-processes-body hub'));
    w.renderMemoryHistory({ now, samples: [] });
    check('Overview memory: no history is explicit, with no fabricated line', !memory.querySelector('path')
      && /No samples/.test(t(d.getElementById('memory-history-note'))));
    w.renderBox(box);
    check('Overview memory: polling preserves the open state and chosen range', memoryFold.open
      && d.querySelector('[data-memory-hours="168"]').getAttribute('aria-pressed') === 'true');
    memoryFold.open = false;
    w.renderBox(box);
    check('Overview memory: polling also preserves collapsed state', !memoryFold.open);
    check('Overview: the network\'s 72 hours, below the tiles and above the services', on && (tiles.compareDocumentPosition(on) & 4) && (on.compareDocumentPosition(table) & 4));
    check('  a row per link, the uplink first, 72 cells each', [...on.querySelectorAll('.hm-row:not(.hm-head) .hm-label')].map(t).join(',') === Object.keys(netUp).join(',')
      && [...on.querySelectorAll('.hm-row:not(.hm-head)')].every((r) => r.querySelectorAll('.hm-cell').length === 72), [...on.querySelectorAll('.hm-label')].map(t).join(','));
    check('  each cell says its link and hour', /^wlan0, \w{3} \d{2} \d{2}:00–\d{2}:00: /.test(on.querySelector('.hm-row:not(.hm-head) .hm-cell').dataset.words), on.querySelector('.hm-row:not(.hm-head) .hm-cell').dataset.words);
    check('  the summary in words, the legend, a link to Network', /^wlan0: up \d/.test(t(on.querySelector('p'))) && on.querySelectorAll('.hm-legend .hm-key').length === 6
      && on.querySelector('a[href="#network"]'), t(on.querySelector('p')));
    // The watchdog's steps per connection: a row of ticks for each link, restart unticked on wlan0 as saved.
    const rows = [...d.querySelectorAll('#up-steps .up-steps-row')];
    const row = (i) => rows.find((r) => r.dataset.iface === i);
    check('the steps per connection: a row for each WiFi and wired link, four ticks each', row('wlan0') && row('eth0') && row('eth1')
      && rows.every((r) => r.querySelectorAll('input[data-step]').length === 4) && !rows.some((r) => /ap0/.test(r.dataset.iface)), rows.map((r) => r.dataset.iface).join(','));
    const tick = (i, st) => row(i).querySelector(`input[data-step="${st}"]`);
    check('  as saved: restart unticked on wlan0, the rest ticked', !tick('wlan0', 'restart').checked && tick('wlan0', 'radio').checked && tick('eth0', 'restart').checked);
    tick('eth0', 'radio').checked = false; tick('eth0', 'radio').dispatchEvent(new w.Event('change'));
    check('  what Save sends: the unticked steps of each connection', JSON.stringify(w.readUpForm().steps_off) === JSON.stringify({ wlan0: ['restart'], eth0: ['radio'] }),
      JSON.stringify(w.readUpForm().steps_off));
    check('  a change marks the form as not saved yet', /not saved yet/.test(d.getElementById('up-save').textContent));
    const mesh = d.getElementById('overview-mesh');
    check('meshtasticd: its 72 hours under the network\'s, one row', (on.compareDocumentPosition(mesh) & 4) && mesh.querySelectorAll('.hm-row:not(.hm-head)').length === 1
      && /^meshtasticd, /.test(mesh.querySelector('.hm-row:not(.hm-head) .hm-cell').getAttribute('aria-label')));
    check('  the start limit said: the loop ended, down until started again', /systemd's limit \(5 starts within 3min 20s\) ended the loop/.test(t(mesh))
      && /Over the last 72 hours: up 99\.\d %, started 1 time/.test(t(mesh)), t(mesh));
    check('  not in the services\' table', !/meshtasticd/.test(t(d.getElementById('service-table'))));
    check('the words for each state', w.meshWords({ active: 'active', restarts: 2 }) === 'meshtasticd is running; systemd restarted it 2 times since the box started.'
      && /^meshtasticd is failed \(exit-code\)\.$/.test(w.meshWords({ active: 'failed', result: 'exit-code', restarts: 0 })), w.meshWords({ active: 'active', restarts: 2 }));
    w.renderOverviewMesh(null, null);
    check('  not installed: nothing shown', mesh.children.length === 0);
    console.log(`failures: ${fails}`);
    process.exit(fails ? 1 : 0);
  }, 300);
}, 300);
