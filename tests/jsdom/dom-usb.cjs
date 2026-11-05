// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// USB (/admin/#usb) in jsdom: the gadget, its drive and the drive's writes populate from /admin/hardware
// with no Box doctor scan; a change is sent through the hardware gate, and a port that is not a device
// port says so instead of waiting for a check. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-usb.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['el.js', 'admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|scrollIntoView/.test(e.message)) errors.push('jsdom: ' + e.message); });
const dom = new JSDOM(html, { url: 'http://box/admin/#usb', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
let hw = {
  at: 100,
  usb_gadget: { supported: true, on: true, up: true, drive: true, drive_write: false, address: '192.168.7.1' },
};
let results = [];
const posted = [];
let n = 0;
w.fetch = async (u, opts = {}) => {
  if (u === '/admin/hardware' && opts.method === 'POST') {
    const body = JSON.parse(opts.body);
    posted.push(body);
    const id = `h${++n}`;
    if (body.choice === 'usb-gadget-drive-write:on') {
      hw = { ...hw, at: hw.at + 1, usb_gadget: { ...hw.usb_gadget, drive_write: true } };
      results = [{ id, ok: true, message: 'The USB drive is writable.', msg: { id: 'usb-gadget-drive-write.on', en: 'The USB drive is writable.' } }];
    }
    return new Response(JSON.stringify({ id }), { status: 202 });
  }
  if (u === '/admin/hardware') return new Response(JSON.stringify(hw), { status: 200 });
  if (u === '/admin/health') {
    return new Response(JSON.stringify({ report: null, helper: { waiting: 0, oldest: 0, stuck: false, commands: [] }, queue: {}, results, pending: 0 }), { status: 200 });
  }
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (id) => (w.document.getElementById(id) || {}).textContent || '';
(async () => {
  await wait(150);
  const d = w.document;
  const groups = [...d.querySelectorAll('#usb-gadget-groups .up-group')];
  check(
    'the USB settings populate on load, with no scan: networking, drive and writes',
    groups.length === 3 && /USB networking/.test(text('usb-gadget-groups')) && /USB drive writes/.test(text('usb-gadget-groups')),
    text('usb-gadget-groups'),
  );
  check(
    '  the current state is selected, not the doctor\'s empty note',
    d.querySelector('#usb-usb-gadget input[value="on"]').checked
      && d.querySelector('#usb-usb-gadget-drive input[value="on"]').checked
      && d.querySelector('#usb-usb-gadget-drive-write input[value="off"]').checked
      && !/next check/.test(text('usb-gadget-groups')),
  );
  const on = d.querySelector('#usb-usb-gadget-drive-write input[value="on"]');
  on.checked = true;
  on.dispatchEvent(new w.Event('change', { bubbles: true }));
  const save = d.querySelector('#usb-gadget-foot .aw-save');
  check('a change makes Save live', !save.disabled && save.textContent === 'Save');
  save.click();
  await wait(150);
  check(
    '  Save sends the hardware choice through /admin/hardware',
    posted.length === 1 && posted[0].action === 'set' && posted[0].choice === 'usb-gadget-drive-write:on',
    posted,
  );
  await wait(700);
  check(
    '  the answer is shown and the new state lands on the page',
    /USB drive is writable/.test(text('usb-gadget-note')) && d.querySelector('#usb-usb-gadget-drive-write input[value="on"]').checked,
    [text('usb-gadget-note'), d.querySelector('#usb-usb-gadget-drive-write input[value="on"]')?.checked],
  );
  w.rkRenderUsbHardware({ at: hw.at + 1, usb_gadget: { supported: false } });
  check(
    'a port that is not a device port: said, not left waiting for a check',
    /not a device port/.test(text('usb-gadget-groups')) && !/next check/.test(text('usb-gadget-groups')),
    text('usb-gadget-groups'),
  );
  check('no script errors', errors.length === 0, errors);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
