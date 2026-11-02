// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// AW.setBusy and AW.busy: the button's own wait (web/admin-widgets.js). Static, in jsdom.
// Usage: [JSDOM=…/jsdom] node dom-action-spinner.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const dom = new JSDOM('<!doctype html><body><main><button id="one" class="action-btn">Check now</button></main></body>', { runScripts: 'outside-only' });
const w = dom.window, d = w.document;
w.eval(fs.readFileSync(`${WEB}/admin-widgets.js`, 'utf8'));
const AW = w.AW;
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 10) => new Promise((r) => setTimeout(r, ms));
const one = () => d.getElementById('one');

(async () => {
  check('the primitive is exported', typeof AW.setBusy === 'function' && typeof AW.busy === 'function');
  check('an idle action button has neither state', !one().classList.contains('is-busy') && !one().classList.contains('is-helper-busy') && !one().hasAttribute('aria-busy'));
  const off = d.createElement('button');
  off.className = 'action-btn';
  off.disabled = true;
  off.textContent = 'Already installed';
  d.body.append(off);
  check('an ordinary disabled button gets neither state', !off.classList.contains('is-busy') && !off.classList.contains('is-helper-busy') && !off.hasAttribute('aria-busy'));
  const css = fs.readFileSync(`${WEB}/style.css`, 'utf8');
  const disabledRules = [...css.matchAll(/([^{}]*:disabled[^{}]*)\{([^{}]*)\}/g)].map((m) => m[0]);
  check('  and no :disabled rule shows the ring', !disabledRules.some((rule) => /is-(?:helper-)?busy|action-spin|visibility:\s*visible/.test(rule)));
  check('the reduced-motion hourglass has empty alternative text', /content:\s*"⌛"\s*\/\s*""/.test(css));

  const clear = AW.setBusy(one(), true);
  check('clicked: the faster state and aria-busy', one().classList.contains('is-busy') && !one().classList.contains('is-helper-busy') && one().getAttribute('aria-busy') === 'true');
  clear();
  check('  cleared: neither class nor aria-busy', !one().classList.contains('is-busy') && !one().hasAttribute('aria-busy'));

  AW.setBusy(one(), true, 'helper');
  check('waiting on the helper: the slower state only', one().classList.contains('is-helper-busy') && !one().classList.contains('is-busy') && one().getAttribute('aria-busy') === 'true');
  AW.setBusy(one(), false);
  check('  cleared', !one().classList.contains('is-helper-busy') && !one().hasAttribute('aria-busy'));

  let done;
  AW.busy(one(), new Promise((resolve) => { done = resolve; }));
  check('a request in flight: busy until it settles', one().classList.contains('is-busy') && one().getAttribute('aria-busy') === 'true');
  done();
  await wait();
  check('  settled: clear again', !one().classList.contains('is-busy') && !one().hasAttribute('aria-busy'));

  let fail;
  const rejected = new Promise((_, reject) => { fail = reject; });
  AW.busy(one(), rejected);
  check('a request that will fail: busy while it runs', one().classList.contains('is-busy'));
  fail(new Error('no'));
  await wait();
  check('  failed: clear too', !one().classList.contains('is-busy') && !one().hasAttribute('aria-busy'));

  const stop = AW.busy(one());
  check('no promise: the caller owns the wait', one().classList.contains('is-busy'));
  stop();
  check('  and can stop it', !one().classList.contains('is-busy'));

  const job = AW.job();
  AW.busy(one(), job);
  check('a section job: busy while the helper works', one().classList.contains('is-busy'));
  job.done(true);
  await wait();
  check('  and clear when the job settles', !one().classList.contains('is-busy') && !one().hasAttribute('aria-busy'));

  console.log(fails ? `failures: ${fails}` : 'all passed');
  process.exit(fails ? 1 : 0);
})();
