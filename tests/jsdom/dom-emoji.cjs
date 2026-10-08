// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The emoji picker (web/emoji.js, web/emoji-data.js): Unicode's groups up to Emoji 13.1 and the hub's
// Pirate tab, a search by name, a skin tone kept, and a pick put into the field; the owner's newer sets
// (15.0, 16.0, from /layout.css) offering more. Static: no hub needed.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-emoji.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(e.message));
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };

const pageWith = (set) => new JSDOM(`<!doctype html><html><head>${set ? `<style>:root { --emoji-set: ${set}; }</style>` : ''}</head><body>`
  + '<form><input id="f" data-emoji maxlength="200"></form></body></html>', { url: 'http://box/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const dom = new JSDOM('<!doctype html><html><head></head><body><form><input id="f" data-emoji maxlength="200"></form></body></html>',
  { url: 'http://box/', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window, d = w.document;
// The page loads emoji-data.js itself when a panel first opens; jsdom runs no <script src>, so it is here already.
w.eval(fs.readFileSync(`${WEB}/emoji-data.js`, 'utf8'));
w.eval(fs.readFileSync(`${WEB}/emoji.js`, 'utf8'));
const field = d.getElementById('f');
const panel = d.querySelector('.emoji-panel');
const t = (n) => (n ? n.textContent : '');

(async () => {
  const data = w.EMOJI_DATA.groups;
  const all = data.flatMap((g) => g[2]);
  check('Unicode\'s groups, the Component group left out', data.map((g) => g[0]).join('|')
    === 'Smileys & Emotion|People & Body|Animals & Nature|Food & Drink|Travel & Places|Activities|Objects|Symbols|Flags');
  const upTo = (cap) => all.filter((e) => (e[3] || 0) <= cap);
  check('the list up to 16.0, each newer than 13.1 tagged with its release', upTo(13.1).length > 1800 && upTo(16).length === all.length
    && all.find((e) => e[1] === 'melting face')[3] === 14 && !all.find((e) => e[1] === 'smiling face with tear')[3], `${upTo(13.1).length} ${all.length}`);
  check('no toned forms as entries of their own', !all.some((e) => /skin tone/.test(e[1])));

  d.querySelector('.emoji-toggle').click();
  await new Promise((r) => setTimeout(r, 20));
  const tabs = [...panel.querySelectorAll('.emoji-tabs button')];
  check('opened: a tab per group, and the hub\'s Pirate tab last', !panel.hidden && tabs.length === 10 && tabs[9].title === 'Pirate', tabs.map((b) => b.title).join('|'));
  check('  the first group showing', t(panel.querySelector('.emoji-group')) === 'Smileys & Emotion' && panel.querySelectorAll('.emoji-grid button').length === data[0][2].filter((e) => !e[3]).length);

  tabs[8].click();
  check('a tab shows its group, and the panel stays open', !panel.hidden && t(panel.querySelector('.emoji-group')) === 'Flags'
    && [...panel.querySelectorAll('.emoji-grid button')].some((b) => b.title === 'flag: United Kingdom'));
  panel.querySelectorAll('.emoji-tabs button')[9].click();
  check('  the Pirate tab', [...panel.querySelectorAll('.emoji-grid button')].some((b) => t(b) === '🦜'));

  const q = panel.querySelector('.emoji-search');
  q.value = 'thumbs';
  q.dispatchEvent(new w.Event('input', { bubbles: true }));
  const found = [...panel.querySelectorAll('.emoji-grid button')];
  check('the search finds by name, across every group', found.length === 2 && /2 found/.test(t(panel.querySelector('.emoji-group'))), found.map((b) => b.title).join('|'));

  panel.querySelectorAll('.emoji-tones button')[4].click();
  const up = [...panel.querySelectorAll('.emoji-grid button')].find((b) => b.title === 'thumbs up');
  check('a skin tone: the emoji that take one drawn in it, the panel still open', !panel.hidden && t(up) === '👍🏾', t(up));
  check('  kept for next time', w.localStorage.getItem('irate-emoji-tone') === '4');
  q.value = 'grinning face';
  q.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('  the ones that take none unchanged', [...panel.querySelectorAll('.emoji-grid button')].some((b) => t(b) === '😀'));

  q.value = 'thumbs up';
  q.dispatchEvent(new w.Event('input', { bubbles: true }));
  [...panel.querySelectorAll('.emoji-grid button')].find((b) => b.title === 'thumbs up').click();
  check('a pick goes into the field', field.value === '👍🏾', field.value);
  d.body.click();
  check('a click elsewhere closes it', panel.hidden);
  q.value = 'melting';
  q.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('the default set (13.1): nothing newer offered', panel.querySelectorAll('.emoji-grid button').length === 0);
  q.value = 'handshake';
  q.dispatchEvent(new w.Event('input', { bubbles: true }));
  check('  nor tones that came later (the handshake\'s, 14.0)', [...panel.querySelectorAll('.emoji-grid button')].map(t).join('') === '🤝');

  // The owner's newer set, from /layout.css.
  const d16 = pageWith('16.0');
  d16.window.eval(fs.readFileSync(`${WEB}/emoji-data.js`, 'utf8'));
  d16.window.eval(fs.readFileSync(`${WEB}/emoji.js`, 'utf8'));
  d16.window.document.querySelector('.emoji-toggle').click();
  await new Promise((r) => setTimeout(r, 20));
  const p16 = d16.window.document.querySelector('.emoji-panel');
  const q16 = p16.querySelector('.emoji-search');
  const find = (word) => { q16.value = word; q16.dispatchEvent(new d16.window.Event('input', { bubbles: true })); return [...p16.querySelectorAll('.emoji-grid button')]; };
  check('set to 16.0: 14.0\'s melting face and 16.0\'s face with bags under eyes offered', find('melting').length === 1 && find('bags under').length === 1);
  p16.querySelectorAll('.emoji-tones button')[4].click();
  check('  and the handshake\'s tones', find('handshake').map(t).join('') === '🤝🏾');
  const count = (cap) => upTo(cap).length;
  check('  every emoji of the set, as Appearance says (1,812, 1,870, 1,906)', count(13.1) === 1812 && count(15) === 1870 && count(16) === 1906,
    `${count(13.1)} ${count(15)} ${count(16)}`);
  check('no page errors', !errors.length, errors.join('; '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
