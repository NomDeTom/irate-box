// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Recovery → Rescue instructions (web/admin-rescue-will.js) in jsdom against a stood-in /admin/rescue-will:
// the saved instructions and the safe defaults, the password only as "set", warnings beside conflicts,
// Save, and Write after the typed WRITE WILL. And the page's words when the root actions are not there yet.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-rescue-will.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['el.js', 'admin-widgets.js', 'admin-rescue-will.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
const defaults = {
  auto: { triage: true, repair_gpt: true, repair_fs: false, reflash: false },
  restore: { method: 'none', other_slot: 'auto', image: { source: 'usb', name: '', url: '', sha256: '' }, salvage: { source: 'usb', name: '' } },
  where: { findings: 'both', backups: 'usb', salvage: 'usb' },
  reach: { hotspot: true, ssid: 'irate-rescue', password: '', join_saved: true, ssh: false },
  roles: { boot: true, fsck: true, health: true, salvage: true, reflash: true, reachability: true },
  consent: 'page', after: { reboot_to_card: 'ask' },
};
const instructions = JSON.parse(JSON.stringify(defaults));
instructions.auto.repair_fs = true;
instructions.restore.method = 'image';
instructions.reach.password = 'secret-password';
const snapshot = {
  will: { valid: true, version: 1, written: 1762500000, password_set: true, copies: { nand: 'match', usb: 'stale' }, instructions },
  defaults,
  restore_candidates: {
    images: [{ name: 'mpwrd-os.img.xz', size: 505290752, sha256: 'a'.repeat(64) }],
    salvage: [{ id: 'usb:2026-11-06T101500', label: 'USB copy' }],
  },
};
const posted = [];
let results = [];
function boot(respond) {
  const dom = new JSDOM(html, { url: 'http://box/admin/#rescue-will', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = async (u, opts = {}) => {
    if (u === '/admin/box') return new Response(JSON.stringify({ results }), { status: 200 });
    if (u !== '/admin/rescue-will') return new Response('{}', { status: 404 });
    if (opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      posted.push(body);
      if (body.action === 'save') {
        snapshot.will.instructions = body.will;
        results = [{ id: 'save-1', ok: true, message: 'The living will is saved. The rescue hotspot password is newpass456. Write it down now. It is not shown again.' }];
        return new Response(JSON.stringify({ id: 'save-1' }), { status: 202 });
      }
      if (body.action === 'push') {
        results = [{ id: 'push-1', ok: true, message: 'The onboard flash copy: written. The USB drive copy: written.' }];
        return new Response(JSON.stringify({ id: 'push-1' }), { status: 202 });
      }
      return new Response(JSON.stringify({ ok: false, error: 'Unknown action.' }), { status: 400 });
    }
    return respond === 'missing'
      ? new Response(JSON.stringify({ ok: false, error: 'There is no such rescue request.' }), { status: 404 })
      : new Response(JSON.stringify(snapshot), { status: 200 });
  };
  w.eval(js);
  return w;
}
const d0 = boot('snapshot');
const d = d0.document;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (node) => (node ? node.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const field = (label) => [...d.querySelectorAll('#rescue-will-form .aw-field')].find((f) => t(f.querySelector('.aw-label')) === label);
const chip = (label, word) => [...field(label).querySelectorAll('.chip')].find((b) => t(b) === word);
const button = (root, label) => [...root.querySelectorAll('button')].find((b) => t(b) === label);
(async () => {
  await wait(120);
  check('the saved values are shown: the restore method and a setting',
    d.querySelector('input[name="rescue-will-method"][value="image"]').checked
      && chip('Filesystem repair', 'On').getAttribute('aria-pressed') === 'true'
      && d.getElementById('rescue-will-form').querySelector('input[value="mpwrd-os.img.xz"]') === null,
    t(d.getElementById('rescue-will-form')).slice(0, 300));
  check('the safe defaults are shown beside the settings',
    /Safe default: Off\./.test(t(field('Filesystem repair'))) && /Safe default: Do not restore\./.test(t(field('Method'))),
    t(field('Filesystem repair')));
  check('the hotspot password is never shown, only that one is set',
    /A hotspot password is set\. It is not shown here;/.test(t(d.getElementById('rescue-will-password-state')))
      && !t(d.body).includes('secret-password')
      && d.getElementById('rescue-will-new-password').type === 'password'
      && d.getElementById('rescue-will-new-password').value === '',
    t(d.getElementById('rescue-will-password-state')));
  check('conflicts get a small warning beside them',
    /repair filesystems without asking/.test(t(d.getElementById('rescue-will-auto-warn')))
      && /image needs its name/.test(t(d.getElementById('rescue-will-restore-warn')))
      && /SHA-256/.test(t(d.getElementById('rescue-will-restore-warn'))),
    [t(d.getElementById('rescue-will-auto-warn')), t(d.getElementById('rescue-will-restore-warn'))]);
  // The rescue preview: the iframe gets the form's current values, before Save, and the state selector
  // switches the card fine, failed and restore in progress.
  const frame = d.getElementById('rescue-will-preview');
  const msgs = [];
  Object.defineProperty(frame, 'contentWindow', { configurable: true, value: { postMessage: (m) => msgs.push(m) } });
  const previewState = d.getElementById('rescue-will-preview-state');
  previewState.value = 'fine';
  previewState.dispatchEvent(new d0.Event('change', { bubbles: true }));
  check('preview: the iframe gets the form\'s current instructions',
    msgs.length && msgs.at(-1).type === 'rescue-preview' && msgs.at(-1).status.will.instructions.auto.repair_fs === true,
    JSON.stringify(msgs.at(-1) && msgs.at(-1).status.will.instructions.auto));
  chip('Filesystem repair', 'Off').click();
  check('  changing an instruction changes the preview at once', msgs.at(-1).status.will.instructions.auto.repair_fs === false,
    JSON.stringify(msgs.at(-1) && msgs.at(-1).status.will.instructions.auto));
  chip('Filesystem repair', 'On').click();
  previewState.value = 'failed';
  previewState.dispatchEvent(new d0.Event('change', { bubbles: true }));
  check('  a failed card: the preview carries its finding', msgs.at(-1).status.gpt_broken === true && /primary partition table is damaged/.test(msgs.at(-1).status.finding.title),
    msgs.at(-1).status.finding);
  previewState.value = 'restore';
  previewState.dispatchEvent(new d0.Event('change', { bubbles: true }));
  check('  restore in progress: the preview carries the job', msgs.at(-1).status.job.state === 'running' && msgs.at(-1).status.job.verb === 'restore-salvage',
    msgs.at(-1).status.job);
  previewState.value = 'fine';
  previewState.dispatchEvent(new d0.Event('change', { bubbles: true }));

  // Turn the filesystem repair off, then save.
  chip('Filesystem repair', 'Off').click();
  await wait(20);
  const save = button(d.getElementById('rescue-will-form'), 'Save');
  check('a change offers Save', save && !save.disabled);
  save.click();
  await wait(700);
  const saved = posted.find((x) => x.action === 'save');
  check('Save sends the instructions, with no password when none was typed',
    saved && saved.will.auto.repair_fs === false && saved.will.restore.method === 'image'
      && saved.will.reach && !('password' in saved.will.reach),
    JSON.stringify(saved));
  check('  the save is said, and the one-time password with it', /living will is saved/.test(t(d.getElementById('rescue-will-note')))
    && /newpass456/.test(t(d.getElementById('rescue-will-note'))) && /not shown again/.test(t(d.getElementById('rescue-will-note'))), t(d.getElementById('rescue-will-note')));
  // Write to both copies, only after the typed words.
  const push = d.getElementById('rescue-will-push');
  check('Write is off until WRITE WILL is typed', push.disabled);
  d.getElementById('rescue-will-confirm').value = 'WRITE WILL';
  d.getElementById('rescue-will-confirm').dispatchEvent(new d0.Event('input', { bubbles: true }));
  check('  typed: Write on', !push.disabled);
  push.click();
  await wait(700);
  check('Write sends push with WRITE WILL', posted.some((x) => x.action === 'push' && x.confirm === 'WRITE WILL'), posted);
  check('  the copies are said', /onboard flash copy: written\. The USB drive copy: written/.test(t(d.getElementById('rescue-will-push-note'))), t(d.getElementById('rescue-will-push-note')));
  // The actions the page waits for when the snapshot is not on the box yet.
  const d1 = boot('missing');
  await wait(120);
  check('without the snapshot: the page names the actions it waits for',
    /will-show, will-save and will-push/.test(t(d1.document.getElementById('rescue-will-state')))
      && d1.document.getElementById('rescue-will-push-card').hidden,
    t(d1.document.getElementById('rescue-will-state')));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
