// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// One pattern for every update (how often to look, from off to weekly; then flag, fetch
// or install; and Check, Fetch and Install buttons) in jsdom, static: every surface on /admin drawn from the one widget, with the same choices, chips and words;
// a subset where fetching is the update; the three buttons greyed with why. Usage: [JSDOM=…/jsdom] node dom-update-pattern.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const dom = new JSDOM(html, { runScripts: 'outside-only' });
const w = dom.window, d = w.document;
w.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));
const AW = w.AW;
const pats = [...d.querySelectorAll('.update-pattern')];
check('no placeholder left: each surface drawn from the widget', !d.querySelector('[data-update-pattern]:not([data-filled])') && pats.length >= 3, pats.length);
const chipsOf = (p, i) => [...p.querySelectorAll('.select-chips')][i];
const words = (g) => [...g.querySelectorAll('.chip')].map((c) => c.textContent).join('|');
check('the same two questions everywhere', pats.every((p) => [...p.querySelectorAll('label')].map((l) => l.firstChild.textContent).join('|')
  === 'How often to look|When something newer is found'));
check('how often: Manual, 6 hours, 24 hours, Weekly, as chips', pats.every((p) => words(chipsOf(p, 0)) === 'Manual|6 hours|24 hours|Weekly'),
  pats.map((p) => words(chipsOf(p, 0))));
const actOf = (form) => words(chipsOf(d.getElementById(form).querySelector('.update-pattern'), 1));
check('what to do: Flag, Fetch, Install (apps and books, the hub, Debian)', ['library-policy', 'auto-form', 'debian-pattern'].every((f) => actOf(f) === 'Flag|Fetch|Install'));
check('  Flag or Fetch where fetching is the update (the mirrors, the firmware), with why', actOf('mirrors-policy') === 'Flag|Fetch' && actOf('fw-policy') === 'Flag|Fetch'
  && /nothing to install/.test(d.getElementById('mirrors-policy').textContent));
check('  the toolkits\' cache: Fetch only, said why', actOf('kits-policy') === 'Fetch' && /only answer/.test(d.getElementById('kits-policy').textContent));
check('every update button in the page says Check now / Fetch / Install (or all)', [...d.querySelectorAll('button[data-update]')].every((b) => /^(Check (all )?now|Fetch( all)?( now)?|Install)$/.test(b.textContent)),
  [...d.querySelectorAll('button[data-update]')].map((b) => b.textContent));
for (const [form, often, act] of [['library-policy', 'check_every_hours', 'auto_install'], ['auto-form', 'hub_check_every_hours', 'hub_auto'], ['debian-pattern', 'often', 'act']]) {
  const f = d.getElementById(form);
  check(`${form}: its form reads ${often} and ${act} as before`, f && f.elements[often] && f.elements[act] && f.elements[often].options.length === 4);
}
const deb = d.getElementById('debian-pattern');
deb.elements.often.value = '6';
check('a value set from code moves the chip', chipsOf(deb, 0).querySelector('.selected').textContent === '6 hours');
chipsOf(deb, 1).querySelectorAll('.chip')[2].click();
check('a chip pressed sets the select, and the line under it says what it means', deb.elements.act.value === '2'
  && /unattended-upgrades/.test(deb.querySelectorAll('.chip-says')[1].textContent), deb.querySelectorAll('.chip-says')[1].textContent);
check('  the surface\'s own words where it needs them (the hub restarts)', /restarts/.test(d.getElementById('auto-form').querySelector('[data-pattern=act] option[value="2"]').dataset.says));
// Where fetching is the update (mirrors, the toolkits' cache): Flag or Fetch only, and why.
const sub = AW.updatePattern({ often: 'o', act: 'a', acts: ['0', '1'], why: 'Fetching a mirror is its update.', value: { often: 24, act: 1 } });
d.body.append(sub);
(async () => {
await new Promise((r) => setTimeout(r, 0));
check('a subset: Flag and Fetch only, with the reason said', words(chipsOf(sub, 1)) === 'Flag|Fetch' && /its update/.test(sub.textContent)
  && sub.querySelector('[name=a]').value === '1' && sub.querySelector('[name=o]').value === '24');
// The three buttons.
let pressed = '';
const b = AW.updateButtons({ check: { onclick: () => { pressed += 'c'; } }, fetch: { onclick: () => { pressed += 'f'; } }, install: { onclick: () => { pressed += 'i'; } },
  badge: AW.updatePill(Date.now() / 1000, true) });
d.body.append(b);
check('Check now, Fetch, Install, the badge first', [...b.querySelectorAll('button')].map((x) => x.textContent).join('|') === 'Check now|Fetch|Install'
  && b.querySelector('.update-pill').textContent === 'update available');
b.update({ fetch: { disabled: true, why: 'downloaded already.' }, install: { disabled: false } });
const [bc, bf, bi] = b.querySelectorAll('button');
check('greyed with why: the button, and a line saying so', bf.disabled && bf.title === 'downloaded already.' && !bi.disabled
  && b.querySelector('.update-why').textContent === 'Fetch: downloaded already.' && !b.querySelector('.update-why').hidden);
bc.click(); bi.click();
check('  the others act', pressed === 'ci');
b.update({});
check('  and the line goes when nothing is greyed', b.querySelector('.update-why').hidden);
const nb = AW.updateButtons({ check: { onclick() {} }, fetch: { onclick() {} } });
check('a surface without Install leaves it out', [...nb.querySelectorAll('button')].map((x) => x.textContent).join('|') === 'Check now|Fetch');
})().then(() => {
console.log(`failures: ${fails}`);
process.exit(fails ? 1 : 0);
});
