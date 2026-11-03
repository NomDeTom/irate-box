// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Overview's Needs attention heading sits at its card's padding, not under body.admin h3's 1.75rem
// room. Static: style.css in jsdom, in every theme. Usage: [JSDOM=…/jsdom] node dom-attention.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '../../web');
// jsdom's CSS parser does not know @container, and drops the whole sheet over it: those blocks taken out.
const noContainer = (s) => {
  for (let i = s.indexOf('@container'); i >= 0; i = s.indexOf('@container')) {
    let j = s.indexOf('{', i), depth = 0;
    for (; j < s.length; j++) { if (s[j] === '{') depth++; else if (s[j] === '}' && --depth === 0) break; }
    s = s.slice(0, i) + s.slice(j + 1);
  }
  return s;
};
const css = noContainer(fs.readFileSync(`${WEB}/style.css`, 'utf8').replace(/@import[^;]*;/g, ''));
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : '  ' + info}`); fails += !cond; };
for (const theme of ['', 'dark', 'eink']) {
  const dom = new JSDOM(`<!doctype html><html data-theme="${theme}"><head><style>${css}</style></head>
    <body class="admin"><main class="admin-main"><section class="admin-pane" id="overview">
      <h2>Overview</h2><div class="attention" id="attention"><h3>Needs attention</h3><p class="setting-desc">No action required.</p></div>
      <div class="net-card"><h3>A card heading</h3></div><h3>Another admin heading</h3>
    </section></main></body></html>`, { pretendToBeVisual: true });
  const d = dom.window.document, w = dom.window;
  const card = d.getElementById('attention'), heading = card.querySelector('h3');
  const cs = w.getComputedStyle(heading), cardCs = w.getComputedStyle(card);
  check(`${theme || 'light'}: the heading sits at the card's padding (0.2rem under it, not 1.75rem)`,
    cs.marginTop === '0.2rem' && cardCs.paddingTop === '0.5rem', `${cs.marginTop} / ${cardCs.paddingTop}`);
  check('  another admin heading keeps its room', w.getComputedStyle(d.querySelector('#overview > h3')).marginTop === '1.75rem',
    w.getComputedStyle(d.querySelector('#overview > h3')).marginTop);
  check("  a net-card's first heading still sits at its top", w.getComputedStyle(d.querySelector('.net-card h3')).marginTop === '0px',
    w.getComputedStyle(d.querySelector('.net-card h3')).marginTop);
}
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
