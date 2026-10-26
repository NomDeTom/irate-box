// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Saves on /admin that lose work, or look saved when they failed, in jsdom.
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
require("./answer-asks.cjs")(w); // its questions on the page answered by its confirm stub
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
  budget.section.fill({ budget: 500, previous: '' });
  type(budget.elements.budget, '700');
  budget.querySelector('.aw-save').click();
  await wait();
  check("the toolkits' budget refused: still 700 on the page, not saved, the reason under Save, Save still on",
    budget.elements.budget.value === '700' && budget.section.savedValue('budget') === '500' && /Refused for the test/.test(t(budget.querySelector('.aw-reason')))
    && !budget.querySelector('.aw-save').disabled);
  // The widgets: a save that resolves false keeps its changes.
  const block = AW.settings([{ key: 'k', label: 'K', kind: 'toggle', value: false }], { save: async () => false });
  d.body.append(block);
  [...block.querySelectorAll('.chip')].find((b) => b.textContent.trim() === 'On').click();
  button(block, 'Save').click();
  await wait();
  check('a settings block whose save says false keeps its change, Save on', !button(block, 'Save').disabled && block.values().k === true);

  // 2. A draft stays until the request is done and well.
  answer['/admin/security'] = refuse;
  const deb = d.getElementById('debian-pattern');
  deb.section.fill({ often: deb.elements.often.options[0].value, act: deb.elements.act.options[0].value });
  type(deb.elements.often, deb.elements.often.options[1].value);
  deb.querySelector('.aw-save').click();
  await wait();
  check("Debian's pattern refused: still changed, Save on again", deb.section.dirty() && !deb.querySelector('.aw-save').disabled);
  answer['/admin/security'] = () => [202, { id: 's1' }];
  deb.querySelector('.aw-save').click();
  await wait();
  check('  sent: "Saving…" until the root helper answers', t(deb.querySelector('.aw-foot .note')) === 'Saving…' && deb.section.dirty());
  ev('secWaiting').onDone(false, 'apt is locked.');
  await wait();
  check('  the root helper failed: still changed, its reason under Save', deb.section.dirty() && /apt is locked/.test(t(deb.querySelector('.aw-reason'))));
  ev('secWaiting = null');
  answer['/admin/library'] = refuse;
  const surf = d.getElementById('mirrors-policy');
  const sel = surf.querySelector('select[name]');
  surf.section.fill(Object.fromEntries([...surf.querySelectorAll('select[name]')].map((x) => [x.name, x.options[0].value])));
  type(sel, [...sel.options].find((o) => o.value !== sel.value).value);
  surf.querySelector('.aw-save').click();
  await wait();
  check("a refresh policy refused: still changed, so the page's polling leaves it", surf.section.dirty());
  // The NetworkManager defaults, a section: refused, then queued, then done.
  answer['/admin/network'] = refuse;
  const def = ev('defSec');
  def.update({ powersave: 'default' });
  def.set('powersave', '2');
  await def.save();
  check('NetworkManager defaults refused: the pick kept, the reason under Save', def.get('powersave') === '2' && def.dirty() && /Refused for the test/.test(t(def.reason)));
  answer['/admin/network'] = () => [202, { id: 'n1' }];
  const queued = def.save();
  await wait();
  check('  sent: kept while queued, "Saving…"', def.dirty() && def.saving() && ev('netWaiting && netWaiting.id') === 'n1');
  // The answer, as renderNetwork takes it from the poll.
  ev('netWaiting').onDone(true, 'Done.');
  await queued;
  check('  saved once the root helper has done it', !def.dirty() && def.savedValue('powersave') === '2');
  ev('netWaiting = null');

  // 3. Access: what Disabled stops, said beside Save; nothing sent until Save.
  const acc = ev('accessBlock')('x', { id: 'x', mode: 'public', unit: 'x.service', title: 'X', visible: 'guests' });
  d.body.append(acc);
  [...acc.querySelectorAll('.chip')].find((c) => t(c) === 'Disabled').click();
  await wait();
  check("an app's access: Disabled says beside Save that its service stops; nothing sent", /Its service stops/.test(t(acc.querySelector('.aw-warn')))
    && AW.unsaved().some((x) => x.id === 'access-x') && posted.every(([u]) => u !== '/admin/access'), t(acc.querySelector('.aw-foot')));
  acc.querySelector('.aw-discard').click();

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

  // 6. A Security choice the newest scan no longer offers is dropped, never sent.
  const realSec = w.renderSecurity;
  w.renderSecurity = () => {}; // the page drawn again: not needed for what is checked here
  const finding = (id, choice, extra = {}) => ({ id, title: id, status: 'warn', actions: [{ choice, label: `Do ${id}`, ...extra }] });
  w.secScan = { findings: [finding('ports-x', 'x-off'), finding('ports-y', 'y-off'), finding('ports-z', 'z-off')] };
  ev('secData = { scan: window.secScan }');
  for (const id of ['ports-x', 'ports-y', 'ports-z']) ev('secSection')(w.secScan.findings.find((f) => f.id === id)).set('choice', `${id.slice(-1)}-off`);
  const newer = { findings: [finding('ports-x', 'x-off', { on: true }), finding('ports-y', 'y-off', { label: 'Switch y off' })] };
  w.secScan = newer;
  ev('secData = { scan: window.secScan }');
  ev('secPrune')(newer);
  const still = ['ports-x', 'ports-y', 'ports-z'].filter((id) => ev('secSecs').get(id).dirty());
  check('Security: a choice in effect now, or whose finding has gone, is dropped; one still offered stays', still.join() === 'ports-y', still.join());
  ev('secSecs').get('ports-y').discard();
  w.renderSecurity = realSec;

  // 7. The doctor's settings: a sequence that fails halfway says what was sent, and keeps what was not.
  let n = 0;
  answer['/admin/health'] = (body) => (body.action === 'scan' ? [202, { id: 'scan' }] : ++n === 1 ? [202, { id: 'h1' }] : [500, { error: 'The queue is full.' }]);
  const groups = [{ key: 'g1', current: 'a', options: [{ value: 'a', choice: 'x-a' }, { value: 'b', choice: 'x-b' }] },
    { key: 'g2', current: 'a', options: [{ value: 'a', choice: 'y-a' }, { value: 'b', choice: 'y-b' }] }];
  ev('doctorOwn').render({ results: [], queue: {} }, { settings: { groups } });
  const doc = ev('doctorOwn').section;
  doc.set('g1', 'b');
  doc.set('g2', 'b');
  const saving = doc.save();
  await wait();
  check("the doctor's settings: the request sent before a refusal is still waited for", ev('doctorOwn').saving() && ev('doctorOwn').saving().ids.join() === 'h1' && doc.saving());
  ev('doctorOwn').render({ results: [{ id: 'h1', ok: true, message: 'Done.' }], queue: {} }, { settings: { groups } });
  await saving;
  const hn = t(ev('hl.setNote'));
  check('  and said: one of two sent, the next refused, one still selected', /1 of 2 changes were sent\. The next change failed: The queue is full\. 1 change is still selected/.test(hn), hn);
  // The rescan: g1 now as chosen; g2 still a change not saved.
  ev('doctorOwn').render({ results: [], queue: {} }, { settings: { groups: [{ ...groups[0], current: 'b' }, groups[1]] } });
  check('  after the rescan: the change sent is saved, the one refused still waits', JSON.stringify(doc.changes()) === '{"g2":"b"}', JSON.stringify(doc.changes()));
  doc.discard();

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
  check("Git: a move acts at once, no question (it can be moved back)", posted.some(([u, b]) => u === '/admin/git' && b.action === 'move'), posted);

  // 9. A switch that acts at once says when.
  answer['/admin/settings'] = (b) => [200, b];
  // Held until Save now (rule 7: a lasting choice is a setting); its footer says when it applies.
  const flip = async (id) => {
    const g = ev('settingGroups').find((x) => x.boxes.some((b) => b.id === id));
    g.sec.update(Object.fromEntries(g.boxes.map((b) => [b.id, false])));
    const box = d.getElementById(id);
    box.checked = true;
    box.dispatchEvent(new w.Event('change'));
    const sentBefore = posted.length;
    const held = posted.length === sentBefore && !g.sec.foot.querySelector('.aw-save').disabled;
    g.sec.foot.querySelector('.aw-save').click();
    await wait();
    return held ? t(g.sec.foot.querySelector('.note')) : 'sent at once';
  };
  check('the factory tile: "Saved.", not "at the next boot"', (await flip('factory_tile')) === 'Saved.');
  check('visitor counts: the same', (await flip('visitor_counts')) === 'Saved.');
  check("the shoutbox's delete at boot: at the next boot", /next boot/.test(await flip('shout_reset_on_boot')));

  // 10. Firmware: the filter changes nothing; Discard puts the boards back.
  ev('fwChosen = new Set(["rak4631"]); renderFirmware(fwData)');
  ff.section.discard();
  const filter = d.getElementById('fw-filter');
  type(filter, 'hel');
  check('Firmware: typing in the board filter marks nothing changed', !ff.section.dirty() && ff.querySelector('.aw-discard').disabled);
  type(filter, '');
  const tick = [...d.querySelectorAll('#fw-boards input')].find((i) => i.parentElement.textContent.includes('heltec-v3'));
  tick.dispatchEvent(new w.Event('pointerdown', { bubbles: true }));
  tick.click();
  check('  a board ticked: Discard on', ev('fwChosen.has("heltec-v3")') && !ff.querySelector('.aw-discard').disabled);
  ff.querySelector('.aw-discard').click();
  check('  Discard: the boards as saved again', ev('[...fwChosen].join()') === 'rak4631'
    && ![...d.querySelectorAll('#fw-boards input')].find((i) => i.parentElement.textContent.includes('heltec-v3')).checked);

  // A section's question before Save, on the page (AW.confirmHere), not the browser's pop-up.
  const askHost = d.createElement('div');
  d.body.append(askHost);
  w.answerAsks = false; // answered by hand below
  let askSaves = 0, popped = 0;
  w.confirm = () => { popped += 1; return true; };
  const asks = w.AW.section({ id: 'ask-test', saved: { v: 1 }, warn: (c) => (c.v === 3 ? 'Three costs something.' : null), save: () => { askSaves += 1; return true; } });
  askHost.append(asks.foot);
  const warnOf = () => askHost.querySelector('.aw-warn');
  check('a section with a warning: none shown while nothing is changed', warnOf().hidden);
  asks.set('v', 2);
  check('  none for a change that costs nothing', warnOf().hidden);
  asks.set('v', 3);
  check('  shown beside Save for one that does', !warnOf().hidden && t(warnOf()) === 'Three costs something.');
  const saved3 = await asks.save();
  check('  Save saves at once: no question, nothing left to save, the warning gone', saved3 === true && askSaves === 1 && !asks.dirty()
    && !askHost.querySelector('.aw-ask') && warnOf().hidden && popped === 0);
  // The on-page question stays for what cannot be undone (AW.confirmHere).
  let answered = w.AW.confirmHere('Delete it? It cannot be restored.', { anchor: askHost });
  await wait();
  const askBox = () => askHost.parentNode.querySelector('.aw-ask');
  check('an irreversible action: asked on the page, Cancel focused', askBox() && /cannot be restored/.test(t(askBox())) && d.activeElement === button(askBox(), 'Cancel'));
  button(askBox(), 'Cancel').click();
  check('  Cancel: No, the question gone', (await answered) === false && !askBox());
  answered = w.AW.confirmHere('Delete it?', { anchor: askHost });
  await wait();
  askBox().dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  check('  Escape cancels too', (await answered) === false && !askBox());
  const first = w.AW.confirmHere('First?', { anchor: askHost });
  const second = w.AW.confirmHere('Second?', { anchor: askHost });
  check('  one question at a time: a new one answers the last one No', (await first) === false && askHost.parentNode.querySelectorAll('.aw-ask').length === 1);
  button(d.querySelector('.aw-ask'), 'Confirm').click();
  check('  and Confirm answers yes', (await second) === true && !d.querySelector('.aw-ask'));

  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
