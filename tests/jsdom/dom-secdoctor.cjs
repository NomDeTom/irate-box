// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Security doctor page's joint report and scan imports in jsdom: nmap
// XML, OpenVAS XML and OpenVAS CSV read in the browser into only what they found; an import posts
// that and nothing else; the joint report's items, what only one source saw, each source's
// coverage and freshness; the badge counting merged items. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-secdoctor.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8');
const js = ['admin-widgets.js', 'admin-layout.js', 'admin.js'].map((f) => fs.readFileSync(`${WEB}/${f}`, 'utf8')).join(';\n');
const now = Math.floor(Date.now() / 1000);
const NMAP = `<?xml version="1.0"?><nmaprun scanner="nmap" start="1791300000" version="7.95"><host><address addr="192.168.4.1" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="80"><state state="open"/><service name="http" product="nginx" version="1.26.3"/></port>
<port protocol="tcp" portid="23"><state state="closed"/></port><port protocol="udp" portid="53"><state state="open|filtered"/><service name="domain"/></port></ports></host></nmaprun>`;
const OPENVAS = `<report id="r1"><report id="r1"><scan_start>2026-10-05T10:00:00Z</scan_start><results>
<result id="a"><name>nginx old</name><host>192.168.4.1<asset asset_id="x"/></host><port>80/tcp</port><severity>7.5</severity>
 <nvt oid="1.3.6"><name>nginx &lt; 1.27 vulnerability</name><cvss_base>7.5</cvss_base><tags>cvss_base_vector=x|summary=An old nginx.|solution=Update nginx.</tags>
 <refs><ref type="cve" id="CVE-2026-1111"/><ref type="url" id="https://x"/></refs></nvt><description>long text</description></result>
<result id="b"><name>Log only</name><host>192.168.4.1</host><port>general/tcp</port><severity>0.0</severity><nvt oid="1"><name>OS detection</name></nvt></result>
<result id="c"><name>TCP timestamps</name><host>192.168.4.1</host><port>general/tcp</port><severity>2.6</severity><nvt oid="2"><name>TCP timestamps</name><tags>summary=Timestamps on.</tags></nvt></result>
</results></report></report>`;
const CSV = 'IP,Hostname,Port,Port Protocol,CVSS,Severity,QoD,Solution Type,NVT Name,Summary,Specific Result,NVT OID,CVEs,Task ID,Task Name,Timestamp,Result ID,Impact,Solution\n' +
  '192.168.4.1,,80,tcp,7.5,High,80,VendorFix,"nginx < 1.27, ""old""",An old nginx.,,1.3.6,"CVE-2026-1111,CVE-2026-2222",t,task,2026-10-05T10:00:00Z,r,,Update nginx.\n' +
  '192.168.4.1,,,tcp,0.0,Log,80,,OS detection,,,1,,t,task,2026-10-05T10:00:00Z,r2,,\n';
// The box's scan, one of each kind: a cure, two choices, a toggle, an update.
const F = (id, title, status, actions) => ({ id, title, status, detail: 'd', fix: '', actions });
const sec = { hub: [{ id: 'plain-http', title: 'Plain HTTP only', status: 'warn', detail: 'No certificate in use.', fix: 'Make the box\'s certificate.',
    actions: [], do: { go: 'security-https', where: 'Security → HTTPS' } }], accepted: {}, scan: { at: now, listeners: [], findings: [
    F('kernel-links', 'Kernel link protections', 'problem', [{ choice: 'kernel-links-debian', label: "Install Debian's defaults" }]),
    F('ssh-password', 'SSH password login', 'warn', [{ choice: 'ssh-password-off', label: 'Turn password login off' }]),
    F('sudo-nopasswd', 'Passwordless sudo', 'problem', [{ choice: 'sudo-drop:90-lyra', label: 'Remove 90-lyra', confirm: 'Remove it?' }]),
    F('listen-tcp-9090', 'Cockpit', 'problem', [{ choice: 'cockpit-off', label: 'Switch off' }]),
    F('security-updates', 'Security updates', 'problem', [{ choice: 'security-updates', label: 'Install 3 security updates' }])] }, log: [], pending: 0, results: [], deep: { at: now - 3600, took: 438 },
  imports: { nmap: { name: 'scan.xml', ran: now - 86400, imported: now - 3600, results: 3 } },
  audit: { at: now, root: true, counts: { problem: 3, warn: 9, ok: 30 }, not_covered: [],
    steps: [{ id: 'kernel', title: 'Kernel protections', ref: '', findings: [{ id: 'kernel-links', title: 'Link protections off', status: 'problem', detail: 'x', fix: 'y', ref: '' }] }],
    joint: { after: { problem: 2, warn: 5 }, agreed_ok: 3, sources: { doctor: { problem: 1, warn: 1, ok: 27 }, 'security-page': { problem: 1, warn: 2, ok: 10 }, nmap: { problem: 0, warn: 1, ok: 2 } },
      coverage: { doctor: "The box's own design.", 'security-page': 'What listens.', nmap: 'Only which ports answer, from where it ran.' },
      freshness: { doctor: now, 'security-page': now - 600, nmap: now - 86400 },
      items: [{ about: { kind: 'setting', key: 'kernel-links' }, title: 'Kernel link protections', sources: ['doctor', 'security-page'], status: 'problem',
        titles: ['doctor: Link protections off', 'security-page: Kernel link protections'], fix: 'Turn them on', alone: false, could_see: [],
        key: 'setting:kernel-links', area: 'Kernel protections', detail: 'Off.', page: ['kernel-links'], tier: null, do: null,
        lines: [{ source: 'doctor', id: 'kernel-links', title: 'Link protections off', status: 'problem', detail: 'x', fix: 'y' },
          { source: 'security-page', id: 'page-kernel-links', title: 'Kernel link protections', status: 'problem', detail: 'd', fix: '' }] },
      { about: { kind: 'finding', key: 'doctor:image-bluetooth' }, title: 'Bluetooth is on', sources: ['doctor'], status: 'warn', titles: [], fix: 'Switch it off.',
        key: 'finding:doctor:image-bluetooth', area: 'The image', detail: 'bluetoothd runs.', page: [], tier: null, do: { cmd: 'sudo systemctl disable --now bluetooth.service' },
        lines: [{ source: 'doctor', id: 'image-bluetooth', title: 'Bluetooth is on', status: 'warn', detail: 'bluetoothd runs.', fix: 'Switch it off.' }], alone: false, could_see: [] },
      { about: { kind: 'setting', key: 'cis-1.7' }, title: 'AppArmor (CIS 1.7)', sources: ['debian-cis'], status: 'warn', titles: [], fix: 'The vendor kernel lacks it.',
        key: 'setting:cis-1.7', area: 'CIS', detail: 'Not met.', page: [], tier: 'not-here', do: null, lines: [{ source: 'debian-cis', id: 'cis-1.7', title: 'AppArmor', status: 'warn', detail: 'Not met.', fix: '' }], alone: false, could_see: [] },
      { about: { kind: 'service', key: 'tcp/9999' }, title: 'TCP 9999', sources: ['nmap'], status: 'warn', titles: ['nmap: nmap found TCP 9999 open'], fix: '',
        alone: true, could_see: ['security-page'], key: 'service:tcp/9999', area: 'Imported scans', detail: 'Open.', page: [], tier: null, do: null,
        lines: [{ source: 'nmap', id: 'nmap-tcp-9999', title: 'nmap found TCP 9999 open', status: 'warn', detail: 'Open.', fix: '' }] }] } } };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/Error: HTTP 404$/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#secdoctor', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
const posted = [];
w.fetch = async (u, opts = {}) => {
  if (opts.method === 'POST') { posted.push([u, JSON.parse(opts.body)]); return new Response(JSON.stringify({ id: 'x', message: 'kept' }), { status: 202 }); }
  if (u === '/admin/security') return new Response(JSON.stringify(sec), { status: 200 });
  return new Response('{}', { status: 404 });
};
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 60) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait();
  const d = w.document;
  const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
  const nm = w.eval('parseScanReport')(NMAP, 'scan.xml');
  check('nmap XML: the open ports (open|filtered too), their services, when it ran', nm.kind === 'nmap' && nm.ran === 1791300000
    && JSON.stringify(nm.results.map((r) => [r.proto, r.port, r.service])) === JSON.stringify([['tcp', 80, 'http nginx 1.26.3'], ['udp', 53, 'domain']]), JSON.stringify(nm));
  const ov = w.eval('parseScanReport')(OPENVAS, 'r.xml');
  check('OpenVAS XML: results with a score, their port, CVSS, name, summary, solution and CVEs; log-only left out',
    ov.kind === 'openvas' && ov.results.length === 2 && ov.results[0].port === 80 && ov.results[0].cvss === 7.5 && ov.results[0].name === 'nginx < 1.27 vulnerability'
    && ov.results[0].summary === 'An old nginx.' && ov.results[0].solution === 'Update nginx.' && JSON.stringify(ov.results[0].cves) === '["CVE-2026-1111"]'
    && ov.results[1].port === 'general' && ov.ran === Date.parse('2026-10-05T10:00:00Z') / 1000, JSON.stringify(ov));
  const cv = w.eval('parseScanReport')(CSV, 'r.csv');
  check('OpenVAS CSV: quoted fields, CVEs, log-only left out', cv.kind === 'openvas' && cv.results.length === 1 && cv.results[0].name === 'nginx < 1.27, "old"'
    && JSON.stringify(cv.results[0].cves) === '["CVE-2026-1111","CVE-2026-2222"]' && cv.results[0].solution === 'Update nginx.', JSON.stringify(cv));
  let threw = '';
  try { w.eval('parseScanReport')('<html><body>not a report</body></html>', 'x.xml'); } catch (e) { threw = e.message; }
  check('something else: refused, or read as nothing', threw || w.eval('parseScanReport')('<html/>', 'x').results.length === 0, threw);
  try { w.eval('parseScanReport')('Name,Value\na,b\n', 'x.csv'); threw = ''; } catch (e) { threw = e.message; }
  check('a CSV that is not OpenVAS: refused, and says why', /Not an OpenVAS CSV report/.test(threw), threw);

  // The joint report on the page: one list, each item ending in what to do.
  const rows = () => [...d.querySelectorAll('#joint-items .finding')];
  check('the counters: after merging, less what is not for this box', /^3 all\s*1 to fix\s*2 to look at$/.test(t(d.getElementById('joint-summary'))), t(d.getElementById('joint-summary')));
  check('one list, worst first: what it is about, the sources that agree', rows().length === 3 && /^To fix/.test(t(rows()[0])) && /Kernel link protections/.test(t(rows()[0]))
    && /2 sources agree/.test(t(rows()[0])), rows().map(t).join(' || '));
  const kl = rows()[0];
  check('  a cure is a button on its item (the Security page\'s, from its scan now), not a block of its own', [...kl.querySelectorAll('button')].some((b) => t(b) === "Install Debian's defaults")
    && !d.getElementById('secdoctor-cures'));
  check('  what each source said, folded in the item', /What each source said \(2\)/.test(t(kl.querySelector('.fsaid summary'))));
  const bt = rows().find((r) => /Bluetooth/.test(t(r)));
  check('  a command to type, with Copy', bt && t(bt.querySelector('.fdo-cmd code')) === 'sudo systemctl disable --now bluetooth.service' && /Copy/.test(t(bt.querySelector('.fdo-cmd button'))));
  check('  only one source saw it: said in the item, with who could have', /the Security page could have seen it and did not/.test(t(rows().find((r) => /TCP 9999/.test(t(r))))));
  check('not for this box: folded apart, not counted', !d.getElementById('joint-nothere-fold').hidden && /AppArmor/.test(t(d.getElementById('joint-nothere'))) && /\(1\)/.test(t(d.getElementById('joint-nothere-count'))));
  [...d.querySelectorAll('#joint-summary button')].find((b) => /to fix/.test(t(b))).click();
  check('a counter filters the list (to fix: one)', rows().length === 1 && /Kernel link/.test(t(rows()[0])));
  [...d.querySelectorAll('#joint-summary button')].find((b) => /all/.test(t(b))).click();
  d.getElementById('joint-search').value = 'bluetooth';
  d.getElementById('joint-search').dispatchEvent(new w.Event('input'));
  check('the search finds by what is said', rows().length === 1 && /Bluetooth/.test(t(rows()[0])));
  d.getElementById('joint-search').value = '';
  d.getElementById('joint-search').dispatchEvent(new w.Event('input'));
  [...rows().find((r) => /Bluetooth/.test(t(r))).querySelectorAll('button')].find((b) => /Accept as it is/.test(t(b))).click();
  await wait();
  check('Accept: the hub keeps it, by the item\'s key', posted.some(([, b]) => b.action === 'accept' && b.key === 'finding:doctor:image-bluetooth'));
  w.renderSecurity({ ...sec, accepted: { 'finding:doctor:image-bluetooth': { at: now, title: 'Bluetooth is on' } } });
  check('  accepted: out of the list and the counts, in its own fold with Take back', !rows().some((r) => /Bluetooth/.test(t(r)))
    && /Bluetooth/.test(t(d.getElementById('joint-accepted'))) && /Take back/.test(t(d.getElementById('joint-accepted'))) && /^2 all/.test(t(d.getElementById('joint-summary'))));
  w.renderSecurity(sec);
  const pills = [...d.querySelectorAll('#joint-fresh span')];
  check('each source in one strip at the top: how fresh, what it covers in its title', pills.length === 3
    && pills.some((x) => /^nmap: 1(\.0)? days? ago|^nmap: 24 h ago/.test(t(x)) && /Only which ports answer/.test(x.title)), pills.map(t).join(' | '));
  check('  none stale here (the newest is a day old, under nmap\'s 30)', !pills.some((x) => x.className === 'warn-pill'), pills.map((x) => x.className + ':' + t(x)).join(' | '));
  check('the badge counts what is to fix after merging (1), not every source\'s (3)', d.querySelector('a[href="#secdoctor"]').dataset.badge === '1', d.querySelector('a[href="#secdoctor"]').dataset.badge);
  check('no emoji to decode in the list', !/[✅⚠❌]/.test(t(d.getElementById('joint-items'))));
  check('the imported report is listed, with Remove', /nmap scan\.xml: 3 results/.test(t(d.getElementById('import-list'))) && [...d.querySelectorAll('#import-list button')].some((b) => t(b) === 'Remove'));
  check('the deep audit\'s last run is said', /Last deep audit .*, 7 min\./.test(t(d.getElementById('audit-deep-when'))), t(d.getElementById('audit-deep-when')));

  d.getElementById('audit-deep').click();
  await wait();
  check('Deep audit asks the hub', posted.some(([, b]) => b.action === 'deep'));
  // Importing: the file is read here, only what it found is sent.
  const input = d.querySelector('#import-form input[type=file]');
  Object.defineProperty(input, 'files', { value: [new w.File([OPENVAS], 'report.xml', { type: 'text/xml' })] });
  d.getElementById('import-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await wait(150);
  const imp = posted.find(([, b]) => b.action === 'import');
  check('import: the results only, never the file', imp && imp[1].report.kind === 'openvas' && imp[1].report.results.length === 2 && imp[1].report.name === 'report.xml'
    && !JSON.stringify(imp[1]).includes('long text'), JSON.stringify(imp));
  check('  and says what it read', /OpenVAS: 2 results read\. kept/.test(t(d.getElementById('import-note'))), t(d.getElementById('import-note')));
  [...d.querySelectorAll('#import-list button')].find((b) => t(b) === 'Remove').click();
  await wait();
  check('Remove asks the hub', posted.some(([, b]) => b.action === 'import-remove' && b.kind === 'nmap'));
  const walker = d.createTreeWalker(d.body, w.NodeFilter.SHOW_TEXT);
  const stray = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (/^(null|undefined|NaN|\[object Object\])$/.test(n.textContent.trim())) stray.push(n.textContent);
  // Each line of the scan where it belongs, in the same shape.
  const ids = (sel) => [...d.querySelectorAll(`${sel} .ftitle`)].map(t).join('|');
  check('Security: what waits for a choice, worst first', [...d.querySelectorAll('#security-findings .sec-choice > .setting-name')].map(t).join('|') === 'Passwordless sudo|Cockpit|SSH password login', ids('#security-findings'));
  check('  the cure is not there (it is the doctor\'s)', !/Kernel link/.test(t(d.getElementById('security-findings'))));
  check('  the hub\'s own lines, with where to change them', /Plain HTTP only/.test(t(d.getElementById('security-hub')))
    && d.querySelector('#security-hub a.go-btn').getAttribute('href') === '#security-https' && !!d.getElementById('security-https'));
  check('Debian\'s security updates on Updates', ids('#updates-security') === 'Security updates', ids('#updates-security'));
  check('Security links to where the rest went', ['#secdoctor', '#updates-debian', '#network/hotspot'].every((h) => d.querySelector(`#security-elsewhere a[href="${h}"]`)));
  check('the hotspot\'s WiFi security with the hotspot, on Network', !!d.querySelector('#network #hs-modes') && !d.querySelector('#security #hs-modes'));
  w.renderSecurity({ ...sec, results: [{ id: 'x', ok: true, message: 'done' }] });  // the answer to what was asked: no longer busy
  const sudo = [...d.querySelectorAll('#security-findings .chip-btn')].find((b) => /Passwordless sudo/.test(t(b)));
  check('passwordless sudo a toggle, On while the rule is there', sudo && t(sudo) === 'Passwordless sudo: On' && sudo.getAttribute('aria-pressed') === 'true', sudo && t(sudo));
  w.confirm = () => true;
  sudo.click();
  await wait();
  check('  Off asks root to take the rule out', posted.some(([u, b]) => u === '/admin/security' && b.action === 'fix' && b.choice === 'sudo-drop:90-lyra'));
  check('no stray null or undefined on the page', !stray.length, stray.join(','));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
