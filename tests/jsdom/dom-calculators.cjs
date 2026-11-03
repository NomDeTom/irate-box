// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Calculators and Tools on the hub: one page with its four sub-headings in Tom's order, every
// entry under its own, the tile that opens it, and no Electronics or RF & LoRa tile beside it.
// Against a running hub (its pages and home page). Usage: BASE=http://127.0.0.1:PORT [JSDOM=…/jsdom] node dom-calculators.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const BASE = process.env.BASE;
let fails = 0;
const check = (n, c, i = '') => { console.log(`${c ? 'PASS' : 'FAIL'} ${n}${c ? '' : '  ' + i}`); fails += !c; };
const text = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
async function open(path) {
  const vc = new VirtualConsole();
  const dom = await JSDOM.fromURL(BASE + path, { runScripts: 'dangerously', resources: 'usable', virtualConsole: vc, pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE + path), o); w.EventSource = class { close() {} }; } });
  await new Promise((r) => setTimeout(r, 700));
  return dom.window.document;
}
const SECTIONS = [
  ['general', 'General calculators & misc tools', ['Unit Converter and Calculator', 'Discord Timestamp Generator', 'URL Cleaner']],
  ['computing-data', 'Computing & Data', ['Computing Units', 'Datatype Byte Converter', 'Protobuf Encoding Demonstrator', 'ASCII and Unicode Converter', 'Unicode Explorer', 'Emoji Version Explorer', 'Integer Arithmetic Calculator']],
  ['electronics', 'Electronics', ['Solar Panel Sense Checker', 'Voltage Divider Calculator', "Ohm's Law Calculator", 'Capacitor Discharge Calculator', 'MLCC Derating Lookup', 'Wire Voltage Drop Calculator', 'Battery Runtime Calculator', 'IC Pinout ASCII Generator', 'Buck and Boost Duty Cycle Calculator']],
  ['rf-lora', 'RF & LoRa', ['Serial terminal and log analyser', 'LoRa Airtime Calculator', 'LoRa Frame Structure Visualiser', 'USB RF Power Meter V3 Reader']],
];
(async () => {
  const d = await open('/calculators.html');
  check('the page is named Calculators and Tools', /Calculators and Tools/.test(text(d.querySelector('h1'))) && /Calculators and Tools/.test(d.title), `${text(d.querySelector('h1'))} / ${d.title}`);
  const groups = [...d.querySelectorAll('.item-section')].map((s) => ({
    id: (s.querySelector('h2') || {}).id || '', title: text(s.querySelector('h2')), names: [...s.querySelectorAll('.item-list .name')].map(text),
  }));
  check('four sub-headings, in the list\'s order', groups.length === 4 && groups.map((g) => g.title).join('|') === SECTIONS.map((x) => x[1]).join('|'), groups.map((g) => g.title).join('|'));
  check('  each has the id a bookmark lands on', groups.every((g, i) => g.id === SECTIONS[i][0]), groups.map((g) => g.id).join('|'));
  check('  every entry under its own heading, in its order', groups.every((g, i) => g.names.join('|') === SECTIONS[i][2].join('|')), groups.map((g) => `${g.title}: ${g.names.join(', ')}`).join(' / '));
  const links = [...d.querySelectorAll('.item-list a')];
  check('every app entry returns to this list (↑)', links.filter((a) => a.getAttribute('href').startsWith('/app.html')).every((a) => a.getAttribute('href').startsWith('/app.html?from=calculators#/')), links.slice(0, 2).map((a) => a.getAttribute('href')).join(' '));

  const home = await open('/');
  const tiles = [...home.querySelectorAll('.service-card')].map((c) => ({ name: text(c.querySelector('.name')), href: c.getAttribute('href') }));
  const cal = tiles.find((t) => t.name === 'Calculators and Tools');
  check('the home page has the Calculators and Tools tile, to the page', cal && cal.href === '/calculators.html', JSON.stringify(cal));
  check('  no Electronics tile beside it', !tiles.some((t) => t.name === 'Electronics'), tiles.map((t) => t.name).join('|'));
  check('  no RF & LoRa tile beside it', !tiles.some((t) => t.name === 'RF & LoRa'), tiles.map((t) => t.name).join('|'));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
