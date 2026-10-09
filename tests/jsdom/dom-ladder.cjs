// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The box doctor's escalation chart (ladder-chart.js; Tom, 2026-10-09) in jsdom, static: the period
// chips, the step line holding each outage's furthest rung, the outages as bands, a marker per step
// shaped by who took it, held and stalls hollow, the summary and key in words, the table view, the
// tooltip from the keyboard, and the empty record. Usage: [JSDOM=…/jsdom] node dom-ladder.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const dom = new JSDOM('<!doctype html><body><div id="c"></div><div id="e"></div></body>', { runScripts: 'outside-only' });
const w = dom.window, d = w.document;
w.eval(fs.readFileSync(`${WEB}/ladder-chart.js`, 'utf8'));
const LC = w.LadderChart;
const now = 1_800_000_000, h = 3600;
// Two days ago a flapping link's repair; yesterday an outage that climbed to a radio reset, a reboot
// held for guests; an hour ago a short one mended by a reconnect; a reset asked on /admin.
const rows = [
  { at: now - 50 * h, k: 'flap', s: 'restart', b: 'flap', i: 'wlan0', t: 'Dropped 3 times in 10 min: treated as a fault, restart the network service.' },
  { at: now - 30 * h, k: 'down', i: 'wlan0', t: 'Link lost.' },
  { at: now - 30 * h + 360, k: 'repair', s: 'reconnect', b: 'auto', i: 'wlan0', t: 'Reconnect (6 min down).' },
  { at: now - 30 * h + 1860, k: 'skip', s: 'restart', i: 'wlan0', t: 'Cannot restart the network service here; skipped.' },
  { at: now - 30 * h + 3660, k: 'repair', s: 'radio', b: 'auto', i: 'wlan0', t: 'Reset the radio (61 min down).' },
  { at: now - 30 * h + 7260, k: 'held', s: 'reboot', i: 'wlan0', t: 'Would reboot, but 1 guest is on the hotspot.' },
  { at: now - 28 * h, k: 'up', i: 'wlan0', t: 'Back after 2 h (tried: reconnect, reset the radio).' },
  { at: now - 1 * h, k: 'down', i: 'wlan0', t: 'Link lost.' },
  { at: now - 1 * h + 360, k: 'repair', s: 'reconnect', b: 'auto', i: 'wlan0', t: 'Reconnect (6 min down).' },
  { at: now - 1 * h + 400, k: 'up', i: 'wlan0', t: 'Back after 7 min (tried: reconnect).' },
  { at: now - 600, k: 'repair', s: 'radio', b: 'hand', i: 'wlan0', t: 'Reset the radio, asked on /admin.' },
];
const box = d.getElementById('c');
LC.render(box, rows, now);
const chips = [...box.querySelectorAll('.ladder-periods .chip')];
check('four periods, 7 days chosen to start', chips.map((c) => c.textContent).join('|') === '24 h|7 days|30 days|72 days'
  && chips[1].classList.contains('selected') && chips[1].getAttribute('aria-checked') === 'true');
const svg = box.querySelector('svg');
check('an image with its summary for a screen reader', svg.getAttribute('role') === 'img' && /2 outages; steps taken: 2 reconnects, 2 radio resets; 1 held/.test(svg.getAttribute('aria-label')),
  svg.getAttribute('aria-label'));
check('the rungs on the axis, up at the bottom', [...svg.querySelectorAll('text.ladder-axis')].map((t) => t.textContent).join(',').includes('up,down,reconnect,restart,radio reset,reboot'));
check('the two outages as bands', svg.querySelectorAll('rect.ladder-band').length === 2);
const m = LC.model(rows.slice().sort((a, b) => a.at - b.at), now - 7 * 86400, now);
check('the line holds each outage\'s furthest rung, then back to up', JSON.stringify(m.pts.map((p) => p[1])) === '[0,1,2,4,0,1,2,0,0]', JSON.stringify(m.pts.map((p) => p[1])));
const circles = svg.querySelectorAll('circle.ladder-mark:not(.hollow)').length, hollow = svg.querySelectorAll('.ladder-mark.hollow').length;
const polys = [...svg.querySelectorAll('polygon.ladder-mark')].map((p) => p.getAttribute('points').split(' ').length);
check('a marker per step: ● the watchdog\'s (3), ◆ by hand (1), ▲ the flap repair (1), the held reboot hollow', circles === 3 && hollow === 1
  && polys.sort().join(',') === '3,4', `${circles} ${hollow} ${polys}`);
check('a skipped step is not drawn as a step', svg.querySelectorAll('.ladder-mark').length === 6);
check('the key and the summary in words', /◆ one asked for on \/admin/.test(box.querySelector('.ladder-key').textContent)
  && /^Over this time: 2 outages/.test(box.querySelector('.ladder-sum').textContent));
const table = box.querySelector('.ladder-table');
check('a table view, newest first', /The 11 events as a table/.test(table.querySelector('summary').textContent) && table.querySelectorAll('tr').length === 12
  && /asked on \/admin/.test(table.querySelectorAll('tr')[1].textContent));
svg.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
const tip = box.querySelector('.ladder-tip');
check('the keyboard brings the readout: the newest event, the step leading', tip.classList.contains('on') && tip.querySelector('strong').textContent === 'radio reset'
  && /asked on \/admin/.test(tip.textContent), tip.textContent);
svg.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
check('  and steps back through them', /back up/.test(tip.querySelector('strong').textContent), tip.textContent);
chips[0].click();
check('24 h: only the last outage and the reset by hand', box.querySelectorAll('rect.ladder-band').length === 1
  && /1 outage; steps taken: 1 reconnect, 1 radio reset/.test(box.querySelector('.ladder-sum').textContent), box.querySelector('.ladder-sum').textContent);
check('  the choice kept for the next draw', box.dataset.span === '86400');
// The symptoms above the ladder (Tom, 2026-10-09): missed checks within the window, the sensitivity's line.
const withSym = rows.concat([
  { at: now - 30 * h - 300, k: 'm', n: 2, th: 3, i: 'wlan0' }, { at: now - 30 * h, k: 'm', n: 5, th: 3, i: 'wlan0' },
  { at: now - 2 * h, k: 'm', n: 1, th: 3, i: 'wlan0' }, { at: now - 1 * h, k: 'm', n: 3, th: 3, i: 'wlan0' }]);
const sb = d.createElement('div'); d.body.append(sb);
LC.render(sb, withSym, now);
const ssvg = sb.querySelector('svg');
check('symptoms: a column per slot with misses, those at or over the line in colour (2 of 4)', ssvg.querySelectorAll('path.ladder-sym').length === 2
  && (ssvg.querySelector('path.ladder-sym.over').getAttribute('d').match(/M/g) || []).length === 2
  && (ssvg.querySelector('path.ladder-sym:not(.over)').getAttribute('d').match(/M/g) || []).length === 2);
check('  the sensitivity\'s line, labelled', !!ssvg.querySelector('path.ladder-th') && [...ssvg.querySelectorAll('text')].some((x) => x.textContent === 'sensitivity 3'));
check('  said in words: the most misses, the spells at or over the line', /At most 5 missed checks within the pace's window; at or over the line in 2 five-minute spells/.test(sb.querySelector('.ladder-sum').textContent),
  sb.querySelector('.ladder-sum').textContent);
check('  not events: the readout and the table leave them out', /The 11 events as a table/.test(sb.querySelector('.ladder-table summary').textContent));
const e = d.getElementById('e');
LC.render(e, [], now);
check('nothing recorded: said, and no chart', !e.querySelector('svg') && /Nothing recorded yet/.test(e.textContent));
const st = fs.readFileSync(`${WEB}/style.css`, 'utf8');
check('thin marks, recessive grid, colours from the page\'s tokens (style.css)', /\.ladder-line \{[^}]*stroke: var\(--accent\); stroke-width: 2/.test(st)
  && /\.ladder-grid \{ stroke: var\(--border\); stroke-width: 1/.test(st) && /\.ladder-mark \{[^}]*stroke: var\(--surface\); stroke-width: 2/.test(st));
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
