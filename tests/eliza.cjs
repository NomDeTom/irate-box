// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// ELIZA as the box serves it: anthay/ELIZA run through adapt_eliza.py. The engine in eliza.html
// reads every script the list page links to (addons/eliza.json) and answers as each should.
// Usage: node tests/eliza.cjs DIR, where DIR is an adapted copy, e.g.
//   git clone -q https://github.com/anthay/ELIZA /tmp/eliza && git -C /tmp/eliza checkout -q PIN
//   rm -rf /tmp/eliza/.git && ./irate-box adapt_eliza /tmp/eliza && node tests/eliza.cjs /tmp/eliza
const fs = require('fs');
const path = require('path');
const vm = require('vm');
if (!process.argv[2]) { console.error('usage: node tests/eliza.cjs ADAPTED_DIR'); process.exit(2); }
const dir = path.resolve(process.argv[2]);
// The engine is eliza.html's script, run without its page: the load handler is never called.
const page = fs.readFileSync(path.join(dir, 'eliza.html'), 'utf8');
const code = page.match(/<script>([\s\S]*?)<\/script>/)[1];
const ctx = { console, TextEncoder, setTimeout, window: { addEventListener() {} }, document: {} };
vm.runInNewContext(code + '\n;this.E = { Eliza, readScript, nullTracer, join, CACM_1966_01_DOCTOR_SCRIPT };', ctx);
const { Eliza, readScript, nullTracer, join, CACM_1966_01_DOCTOR_SCRIPT } = ctx.E;
const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../addons/eliza.json'), 'utf8'));
for (const e of manifest.entries) e.href = e.path; // a local add-on's entries name paths inside it
let failed = 0;
function check(name, ok, detail) { if (!ok) { failed++; console.log('FAIL', name, detail ?? ''); } }
const script = (n) => fs.readFileSync(path.join(dir, 'scripts', n + '.txt'), 'utf8');
// As the page's ?script=NAME&add=NAME block does.
function load(href) {
  const q = new URLSearchParams(href.split('?')[1] || '');
  if (!q.get('script')) return CACM_1966_01_DOCTOR_SCRIPT;
  let text = script(q.get('script'));
  if (q.get('add')) text = text.replace(/\(\s*\)\s*$/, () => script(q.get('add')) + '\n()');
  return text;
}
// What to say to each, what a reply must match; the two that do not run as printed.
const EXPECT = {
  '': [['Men are all alike.', /IN WHAT WAY/], ['My mother hates me', /FAMILY/]],
  'ELIZA-script-02-000311051-1965-03-06-TAPE-100': [['My mother hates me', /FAMILY/]],
  'ELIZA-script-02-000311051-1965-03-06-TAPE-100+box': [['The mesh is down', /MESH/], ['My battery is flat', /RUN DOWN|EMPTY|CHARGE/],
    ['I flashed the firmware', /UPDATE|FLASH|STABLE/], ['Everybody laughs at me', /EVERYONE/]],
  'ELIZA-script-DOCTOR-French-Jeu-de-Paume': [['Bonjour', /./]],
  'ELIZA-script-YAPYAP-modified-for-1966-CACM-ELIZA': [['I am not sure', /./]],
  'ELIZA-script-equal-number-Turing-machine': [['EQUAL A B B A', /YES/], ['EQUAL A A A B B', /NO/]],
  'ELIZA-script-palindrome-Turing-machine': [['PALP A B B A', /TRUE/], ['PALP A A A B B', /FALSE/]],
  // These two never halt: they run until the transformation limit, as the list page says.
  'ELIZA-script-Turing-example1': [['EX1', /transformation limit reached/]],
  'ELIZA-script-Turing-example2': [['EX2', /transformation limit reached/]],
};
const BROKEN = ['ELIZA-script-02-000311051-1965-03-06-TAPE-102', 'ELIZA-script-YAPYAP-original'];
(async () => {
  const entries = manifest.entries.filter((e) => e.menus.eliza !== undefined);
  // The CACM file is the built-in script's text (the 1966 entry runs the built-in, so *cacm works).
  const CACM = 'ELIZA-script-DOCTOR-original-1966-CACM-appendix';
  for (const f of fs.readdirSync(path.join(dir, 'scripts')).filter((f) => f.startsWith('ELIZA-') && !f.startsWith(CACM)))
    check(`${f} is on the list`, entries.some((e) => e.href.includes('script=' + f.replace(/\.txt$/, ''))));
  {
    const a = readScript(script(CACM))[1], b = readScript(CACM_1966_01_DOCTOR_SCRIPT)[1];
    const ea = new Eliza(a.rules, a.memoryRule, new nullTracer(), 500), eb = new Eliza(b.rules, b.memoryRule, new nullTracer(), 500);
    for (const l of ['Men are all alike.', 'Well, my boyfriend made me come here.', 'My father is afraid of everybody'])
      check(`${CACM}.txt answers as the built-in: ${l}`, (await ea.response(l)) === (await eb.response(l)));
  }
  for (const e of entries) {
    const q = new URLSearchParams(e.href.split('?')[1] || '');
    const key = (q.get('script') || '') + (q.get('add') ? '+' + q.get('add') : '');
    const [status, s] = readScript(load(e.href));
    if (BROKEN.includes(key)) { check(`${key}: does not run as printed`, status !== 'success', status); continue; }
    check(`${key || 'built-in'}: reads`, status === 'success', status);
    if (status !== 'success') continue;
    check(`${key}: says hello`, join(s.helloMessage).length > 0);
    const eliza = new Eliza(s.rules, s.memoryRule, new nullTracer(), 500);
    for (const [said, want] of EXPECT[key] || []) {
      const reply = await eliza.response(said);
      check(`${key || 'built-in'}: ${said}`, want.test(reply), reply);
    }
    check(`${key}: has a test here`, key in EXPECT || key === '');
  }
  console.log(failed ? `${failed} failure(s)` : `ok (${entries.length} entries)`);
  process.exit(failed ? 1 : 0);
})();
