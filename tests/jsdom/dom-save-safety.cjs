// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Saves on /admin that lose work, or look saved when they failed (the save-patterns plan, phase 0), in jsdom.
// Static: no hub needed; every request answered here, a refusal where a test wants one.
// Usage: [JSDOM=…/jsdom] node dom-save-safety.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const vm = require('vm');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const vc = new VirtualConsole();
const dom = new JSDOM(html, { url: 'http://box.local/admin/#overview', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window, d = w.document;
// POSTs answered by the table (url -> a function of the body: [status, json]); anything else 404, as no hub is here.
const answer = {};
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') {
    const body = JSON.parse(opts.body || '{}');
    posted.push([u, body]);
    const [status, json] = answer[u] ? answer[u](body) : [404, { error: 'not here' }];
    return new Response(JSON.stringify(json), { status });
  }
  return new Response('{}', { status: 404 });
};
let confirms = true;
w.confirm = () => confirms;
// As a script, not eval: the page's own let and const stay reachable to the checks below (ev).
const ctx = dom.getInternalVMContext();
const ev = (code) => new vm.Script(code).runInContext(ctx);
ev(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const button = (root, label) => [...root.querySelectorAll('button')].find((b) => t(b) === label);
const refuse = () => [500, { error: 'Refused for the test.' }];
const type = (input, value) => {
  input.dispatchEvent(new w.Event('focusin', { bubbles: true }));
  input.value = value;
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  input.dispatchEvent(new w.Event('change', { bubbles: true }));
};

(async () => {
  await wait();
  const AW = w.AW;

  // 1. A failed save never looks saved.
  answer['/admin/kits'] = refuse;
  check('a toolkit request the hub refuses reports it', (await ev('kitAct({ action: "settings", kits: {} })')) === false);
  confirms = false;
  check('a package change cancelled at its question reports it', (await ev('pkgAct({ action: "settings", package: "x" }, "Sure?")')) === false);
  confirms = true;
  answer['/admin/packages'] = refuse;
  check('  and one the hub refuses', (await ev('pkgAct({ action: "settings", package: "x" })')) === false);
  const budget = d.getElementById('kits-budget');
  budget.dataset.saved = '500';
  type(budget.elements.budget, '700');
  budget.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check("the toolkits' budget refused: still 700 on the page, not marked saved, Save still on",
    budget.elements.budget.value === '700' && budget.dataset.saved === '500' && !budget.querySelector('[type="submit"]').disabled);
  // The widgets: a save that resolves false keeps its changes.
  const block = AW.settings([{ key: 'k', label: 'K', kind: 'toggle', value: false }], { save: async () => false });
  d.body.append(block);
  block.querySelector('.chip-btn').click();
  button(block, 'Save').click();
  await wait();
  check('a settings block whose save says false keeps its change, Save on', !button(block, 'Save').disabled && block.values().k === true);

  // 2. A draft stays until the request is done and well.
  answer['/admin/security'] = refuse;
  const deb = d.getElementById('debian-pattern');
  type(deb.elements.often, deb.elements.often.options[1].value);
  deb.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check("Debian's pattern refused: still marked changed, Save on again", deb.dataset.dirty === '1'
    && !deb.querySelector('button[type="submit"]').disabled);
  answer['/admin/library'] = refuse;
  const surf = d.getElementById('mirrors-policy');
  const sel = surf.querySelector('select[name]');
  type(sel, [...sel.options].find((o) => o.value !== sel.value).value);
  surf.dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait();
  check("a refresh policy refused: still marked changed, so the page's polling leaves it", surf.dataset.dirty === '1');
  answer['/admin/network'] = refuse;
  ev('defaultsDirty = { powersave: "2" }');
  const dsave = ev('net.defaultsSave');
  dsave.disabled = false;
  dsave.click();
  await wait();
  check('NetworkManager defaults refused: the picks kept', ev('defaultsDirty.powersave') === '2');
  answer['/admin/network'] = () => [202, { id: 'n1' }];
  let cleared = false;
  await ev('netRequest')({ action: 'connection' }, 'connection', () => { cleared = true; });
  check("one network's settings: not cleared when only queued", !cleared && ev('netWaiting && netWaiting.id') === 'n1');
  // The answer, as renderNetwork takes it from the poll: onOk only when the root helper did it.
  ev('netWaiting').onOk();
  check('  cleared once it is done', cleared);
  ev('netWaiting = null');
  dsave.click();
  await wait();
  check('  the NetworkManager defaults the same: kept while queued', ev('defaultsDirty.powersave') === '2');
  ev('netWaiting.onOk(); netWaiting = null');
  check('  and cleared once done', !Object.keys(ev('defaultsDirty')).length);

  // 3. Access: Cancel at the question keeps the draft.
  ev('accessDrafts.set("x", { mode: "off" })');
  const acc = ev('accessBlock')('x', { id: 'x', mode: 'public', unit: 'x.service', title: 'X', visible: 'guests' });
  confirms = false;
  button(acc, 'Save').click();
  confirms = true;
  check("an app's access: Cancel at the question keeps the change", ev('accessDrafts.has("x")') && posted.every(([u]) => u !== '/admin/access'));

  // 4. Opening another card asks before a changed one is closed.
  let mark;
  const grid = AW.cards([{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }], { body: (it, setDirty) => { if (it.id === 'a') mark = setDirty; return AW.h('p', { text: it.title }); }, save: () => {} });
  d.body.append(grid);
  const head = (id) => grid.querySelector(`[data-aw-id="${id}"] .aw-card-head`);
  head('a').click();
  mark(true);
  confirms = false;
  head('b').click();
  check('a changed card stays open when its user keeps it, and the other does not open',
    grid.querySelector('[data-aw-id="a"]').classList.contains('open') && !grid.querySelector('[data-aw-id="b"]').classList.contains('open'));
  confirms = true;
  head('b').click();
  check('  closed and the other opened when they let it go', !grid.querySelector('[data-aw-id="a"]').classList.contains('open')
    && grid.querySelector('[data-aw-id="b"]').classList.contains('open'));

  // 5. The page's polling never overwrites an unsaved change.
  const acctData = { admin_login: { on: true, reach: 'network', password_set: true, https_admins: [], results: [] },
    settings: { signup: 'off', http: 'warning' }, counts: { user: 0, admins: 0, asked: 0, disabled: 0 }, accounts: [] };
  ev('renderAccounts')(acctData);
  const af = d.getElementById('accounts-settings');
  type(af.elements.signup, 'open');
  af.elements.signup.blur();
  ev('renderAccounts')(acctData);
  check("Accounts: a changed sign-up setting survives the page's redraw", af.elements.signup.value === 'open', af.elements.signup.value);
  const fwData = { settings: { enabled: true, configs: true, keep_alpha: 1, keep_beta: 1, boards: ['rak4631'], cache: 'none' }, status: {},
    targets: [{ board: 'rak4631', platform: 'nrf52' }, { board: 'heltec-v3', platform: 'esp32s3' }], running: false };
  w.fwData = fwData;
  ev('fwData = window.fwData; renderFirmware(fwData)');
  const ff = d.getElementById('fw-form');
  type(ff.elements.keep_alpha, '3');
  ff.elements.keep_alpha.blur();
  ev('renderFirmware(fwData)');
  check('Firmware: a changed field survives the poll once focus has left', ff.elements.keep_alpha.value === '3');
  const blocks = (v) => AW.settings([{ key: 'report_threshold', label: 'T', kind: 'choice', value: v, options: [[1, '1'], [2, '2']] }], { id: 'moderation-test', save: () => {} });
  let mod = blocks(1);
  d.body.append(mod);
  mod.querySelectorAll('.chip')[1].click();
  mod.replaceWith((mod = blocks(1)));
  check('a settings block drawn again (a reason chip, another block saved) keeps its unsaved choice',
    mod.values().report_threshold === 2 && !button(mod, 'Save').disabled);
  mod.replaceWith((mod = blocks(2)));
  check('  and lets it go once the saved value is the same', !button(mod, 'Save') || button(mod, 'Save').disabled);

  // 6. The Security bar drops a choice the newest scan no longer offers.
  ev(`secPending.set('ssh-root', { f: { id: 'ssh-root' }, a: { choice: 'ssh-root-off' }, page: 'security' });
    secPending.set('llmnr', { f: { id: 'llmnr' }, a: { choice: 'llmnr-off' }, page: 'security' });
    secPending.set('gone', { f: { id: 'gone' }, a: { choice: 'gone-off' }, page: 'security' });`);
  ev('secPrune')({ findings: [{ id: 'ssh-root', actions: [{ choice: 'ssh-root-off', on: true }] }, { id: 'llmnr', actions: [{ choice: 'llmnr-off', label: 'Disable LLMNR' }] }] });
  check('the Security bar: a choice in effect now, or whose finding has gone, is dropped; one still offered stays',
    ev('[...secPending.keys()].join()') === 'llmnr' && ev('secPending.get("llmnr").a.label') === 'Disable LLMNR');
  ev('secPending.clear()');

  // 7. The doctor's settings: a sequence that fails halfway says what was sent.
  let n = 0;
  answer['/admin/health'] = () => (++n === 1 ? [202, { id: 'h1' }] : [500, { error: 'The queue is full.' }]);
  const groups = [{ key: 'g1', current: 'a', options: [{ value: 'a', choice: 'x-a' }, { value: 'b', choice: 'x-b' }] },
    { key: 'g2', current: 'a', options: [{ value: 'a', choice: 'y-a' }, { value: 'b', choice: 'y-b' }] }];
  ev('hsPick = { g1: "b", g2: "b" }');
  ev('showDoctorSave')(groups);
  await ev('hl.setSave.onclick()');
  check("the doctor's settings: the request sent before a refusal is still waited for", ev('hsSaving && hsSaving.ids.join()') === 'h1');
  ev('renderDoctorSettings')({ results: [{ id: 'h1', ok: true, message: 'Done.' }], queue: {} }, { settings: { groups } });
  const hn = t(ev('hl.setNote'));
  check('  and said: one of two sent, the next refused, one still selected', /1 of 2 changes were sent\. The next change failed: The queue is full\. 1 change is still selected/.test(hn), hn);

  // 8. Git: a cancelled question draws the repository as it is again.
  let redrawn = 0;
  const realGit = w.renderGit;
  w.renderGit = () => { redrawn++; };
  const panel = ev('managePanel')({ name: 'r', area: 'public' }, { presets: {}, repos: [] });
  confirms = false;
  const priv = [...panel.querySelectorAll('input[type="radio"]')].find((r) => r.value === 'private');
  priv.checked = true;
  priv.dispatchEvent(new w.Event('change', { bubbles: true }));
  await wait();
  confirms = true;
  w.renderGit = realGit;
  check("Git: Cancel at a move's question redraws it as it is", redrawn === 1 && posted.every(([u]) => u !== '/admin/git'));

  // 9. A switch that acts at once says when.
  answer['/admin/settings'] = (b) => [200, b];
  const flip = async (id) => { const box = d.getElementById(id); box.checked = !box.checked; box.dispatchEvent(new w.Event('change')); await wait(); return t(ev('settingNote')(box)); };
  check('the factory tile: "Saved.", not "at the next boot"', (await flip('factory_tile')) === 'Saved.');
  check('visitor counts: the same', (await flip('visitor_counts')) === 'Saved.');
  check("the shoutbox's delete at boot: at the next boot", /next boot/.test(await flip('shout_reset_on_boot')));

  // 10. Firmware: the filter changes nothing; Discard puts the boards back.
  ev('fwChosen = new Set(["rak4631"]); renderFirmware(fwData)');
  ff.elements.keep_alpha.value = '1';
  ff._failed && ff.querySelector('.aw-discard').click();
  const filter = d.getElementById('fw-filter');
  type(filter, 'hel');
  check('Firmware: typing in the board filter marks nothing changed', !ff._unsaved() && ff.querySelector('.aw-discard').disabled);
  type(filter, '');
  const tick = [...d.querySelectorAll('#fw-boards input')].find((i) => i.parentElement.textContent.includes('heltec-v3'));
  tick.dispatchEvent(new w.Event('pointerdown', { bubbles: true }));
  tick.click();
  check('  a board ticked: Discard on', ev('fwChosen.has("heltec-v3")') && !ff.querySelector('.aw-discard').disabled);
  ff.querySelector('.aw-discard').click();
  check('  Discard: the boards as saved again', ev('[...fwChosen].join()') === 'rak4631'
    && ![...d.querySelectorAll('#fw-boards input')].find((i) => i.parentElement.textContent.includes('heltec-v3')).checked);

  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
