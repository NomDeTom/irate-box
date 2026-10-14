// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Appearance → Colours in jsdom: light and dark edited apart; presets set both; every pair the page draws as words
// checked at 4.5:1 and the shortfalls named; a palette that falls short saved only once acknowledged; the old single
// accent taken in as both modes' and cleared on Save. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-palette.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|getContext/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#appearance', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
let settings = { hub_title: 'Hub', hub_subtitle: '', accent: '#ffd400', palette: {}, krab_art: true, page_width: 80, admin_width: 80,
  shout_width: 45, board_width: 45, emoji_set: '13.1', card_writes: 'thrifty' };
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/settings' && opts.method === 'POST') {
    const b = JSON.parse(opts.body);
    posted.push(b);
    settings = { ...settings, ...b };
    return new Response(JSON.stringify(settings), { status: 200 });
  }
  if (u === '/admin/settings') return new Response(JSON.stringify(settings), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait(300);
  const d = w.document;
  const ed = () => d.querySelector('.palette-editor');
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const chip = (label) => [...ed().querySelectorAll('.chip')].find((c) => t(c) === label);
  const saveB = () => [...ed().querySelectorAll('.aw-foot button')].find((b) => t(b) === 'Save');
  check('the editor is on Appearance, the old accent row gone', !!ed() && !d.querySelector('#appearance-box input[type=color][aria-label="Accent colour"]'));
  // The old accent (#ffd400) taken in as both modes' accent: nothing looks changed, nothing to save yet.
  const accentPick = () => ed().querySelector('input[type=color][data-role="accent"]');
  check('  the old accent taken in: the light accent shows it, and nothing waits to be saved', accentPick().value === '#ffd400'
    && saveB().disabled && /apply after you select Save/.test(t(ed().querySelector('.aw-foot .note'))), accentPick().value);
  check('  matching no preset: said as Custom palette, marked', chip('Custom palette') && chip('Custom palette').getAttribute('aria-pressed') === 'true'
    && ed().querySelectorAll('.chip-group')[0].querySelectorAll('.selected').length === 1);
  check('  and its shortfall named: a yellow link on a white tile', /Light: Link on a tile, 1\.\d:1 \(minimum 4\.5:1\)/.test(t(ed().querySelector('.palette-report'))), t(ed().querySelector('.palette-report')));
  // Standard: the theme as it comes, its dark pairs that fall short said honestly.
  chip('Standard palette').click();
  const rep = t(ed().querySelector('.palette-report'));
  check('Standard: the dark theme\'s quieter text and link on a tile named, with their ratios', /Dark: Secondary text on a tile, 3\.4:1/.test(rep)
    && /Dark: Link on a tile, 4\.2:1/.test(rep) && /Dark: Button text on the accent colour, 4\.0:1/.test(rep) && !/Light:/.test(rep), rep);
  check('  so Save waits for the acknowledgement', saveB().disabled && !!ed().querySelector('.palette-ack input'));
  ed().querySelector('.palette-ack input').click();
  check('  ticked, Save is offered', !saveB().disabled);
  // A preset that passes: nothing to acknowledge.
  chip('Teal').click();
  check('a preset chosen: Custom palette gone', !chip('Custom palette'));
  check('Teal: every pair passes in both modes, Save offered at once', /4\.5:1 or more in both modes/.test(t(ed().querySelector('.palette-report')))
    && !ed().querySelector('.palette-ack') && !saveB().disabled);
  check('  each mode its own accent: light deeper than dark', accentPick().value === '#0f766e');
  chip('Dark').click();
  check('  the dark one', accentPick().value === '#2dd4bf' && chip('Dark').getAttribute('aria-pressed') === 'true');
  // A colour changed by hand in one mode leaves the other alone; its own button puts the theme's back.
  const bg = ed().querySelector('input[type=color][data-role="bg"]');
  bg.value = '#202020'; bg.dispatchEvent(new w.Event('change'));
  chip('Light').click();
  check('a dark page colour changed: the light palette untouched', ed().querySelector('input[type=color][data-role="bg"]').value === '#f4f5f9');
  check('  the preview drawn in the mode shown', ed().querySelector('.palette-preview').style.getPropertyValue('--pv-accent') === '#0f766e');
  saveB().click();
  await wait();
  const sent = posted[posted.length - 1] || {};
  check('Save sends both modes and clears the old accent', JSON.stringify(sent) === JSON.stringify({ palette: {
    light: { muted: '#5f6380', accent: '#0f766e' }, dark: { muted: '#8a8fa8', accent: '#2dd4bf', bg: '#202020' } }, accent: '' }), JSON.stringify(sent));
  await wait(200);
  check('  after it, the editor shows the saved palette with nothing waiting', !!ed() && saveB().disabled && accentPick().value === '#0f766e');
  check('no stray null or undefined, no page errors', !/\b(null|undefined)\b/.test(t(ed())) && !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
