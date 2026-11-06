// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The rescue page's preview mode, with no hub: a parent message supplies the status, the page renders it
// (including the living will's current settings), and an action sends nothing. Usage: [JSDOM=…/jsdom] node dom-rescue-page.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/rescue/index.html`, 'utf8');
const script = /<script[^>]*>([\s\S]*?)<\/script>/i.exec(html)[1];
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push(e.message); });
const dom = new JSDOM(html, { url: 'http://box/rescue/index.html?preview=1', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const sent = [];
w.fetch = async (url) => { sent.push(url); throw new Error('preview must not fetch'); };
w.eval(script);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (sel) => (w.document.querySelector(sel) || {}).textContent?.replace(/\s+/g, ' ').trim() || '';
const wait = (ms = 80) => new Promise((r) => setTimeout(r, ms));
const defaults = { auto: { triage: true, repair_gpt: true, repair_fs: true, reflash: false }, restore: { method: 'none', other_slot: 'auto', image: { source: 'usb', name: '', url: '', sha256: '' }, salvage: { source: 'usb', name: '' } }, where: { findings: 'both', backups: 'usb', salvage: 'usb' }, reach: { hotspot: true, ssid: 'irate-rescue', password: '', join_saved: true, ssh: false }, roles: { boot: true, fsck: true, health: true, salvage: true, reflash: true, reachability: true }, consent: 'page', after: { reboot_to_card: 'ask' } };
const will = { valid: true, written: 1762500000, source: 'preview', password_set: true, instructions: defaults };
const status = { ok: true, device: '/dev/mmcblk0', present: true, lent: false, size: 15634268160, partitions: [{ name: '/dev/mmcblk0p1', size: 268435456 }], gpt_broken: false, finding: {}, job: {},
  will, nand: { present: true, env: { available: true, bootcount: 2, bootlimit: 3 } },
  network: { addresses: [{ iface: 'wlan0', address: '192.168.4.1', kind: 'hotspot' }, { iface: 'usb0', address: '192.168.7.1', kind: 'usb' }], hotspot: { on: true, ssid: 'irate-rescue', clients: 1 }, joined: { ssid: 'SKYTB1H1', signal: -61 }, ssh: false } };
(async () => {
  await wait();
  w.dispatchEvent(new w.MessageEvent('message', { data: { type: 'rescue-preview', status, will }, source: w.parent, origin: w.location.origin }));
  await wait();
  check('preview: the banner is shown', !w.document.getElementById('preview').hidden && /Preview: nothing here acts on a card/.test(t('#preview')));
  check('  the living will is followed', /Following the owner's instructions/.test(t('#will')) && /Without asking:.*filesystem repair/.test(t('#will')), t('#will'));
  check('  the card and networks are drawn', /\/dev\/mmcblk0/.test(t('#card')) && /192\.168\.4\.1/.test(t('#net')) && /1 client connected/.test(t('#net')));
  check('  the automatic repair is marked, not offered', /Automatic: Repair filesystem/.test(t('#acts')));
  const before = sent.length;
  w.document.querySelector('[data-a="fscheck"]').click();
  await wait();
  check('  an action sends nothing, and says so', sent.length === before && /Preview: nothing here acts on a card/.test(t('#note')), t('#note'));
  const failed = { ...status, gpt_broken: true, finding: { severity: 'warning', title: 'The primary partition table is damaged.', detail: 'The backup is valid.', action: 'Repair it.' } };
  w.dispatchEvent(new w.MessageEvent('message', { data: { type: 'rescue-preview', status: failed, will }, source: w.parent, origin: w.location.origin }));
  await wait();
  check('  a failed card shows its finding', /primary partition table is damaged/.test(t('#find')), t('#find'));
  const restore = { ...status, job: { id: 'p', verb: 'restore-salvage', state: 'running', stage: 'copying', percent: 40, message: 'Restoring the salvage copy.' } };
  w.dispatchEvent(new w.MessageEvent('message', { data: { type: 'rescue-preview', status: restore, will }, source: w.parent, origin: w.location.origin }));
  await wait();
  check('  restore in progress shows the bar', !w.document.getElementById('jobbox').hidden && /Restoring the salvage copy/.test(t('#jobbox')));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
