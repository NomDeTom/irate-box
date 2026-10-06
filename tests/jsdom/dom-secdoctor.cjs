// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Security doctor page's joint report and scan imports (next-work plan step 31) in jsdom: nmap
// XML, OpenVAS XML and OpenVAS CSV read in the browser into only what they found; an import posts
// that and nothing else; the joint report's items, what only one source saw, each source's
// coverage and freshness; the badge counting merged items. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-secdoctor.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
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
const sec = { hub: [], scan: { at: now, listeners: [], findings: [] }, log: [], pending: 0, results: [], deep: { at: now - 3600, took: 438 },
  imports: { nmap: { name: 'scan.xml', ran: now - 86400, imported: now - 3600, results: 3 } },
  audit: { at: now, root: true, counts: { problem: 3, warn: 9, ok: 30 }, not_covered: [],
    steps: [{ id: 'kernel', title: 'Kernel protections', ref: '', findings: [{ id: 'kernel-links', title: 'Link protections off', status: 'problem', detail: 'x', fix: 'y', ref: '' }] }],
    joint: { after: { problem: 2, warn: 5 }, agreed_ok: 3, sources: { doctor: { problem: 1, warn: 1, ok: 27 }, 'security-page': { problem: 1, warn: 2, ok: 10 }, nmap: { problem: 0, warn: 1, ok: 2 } },
      coverage: { doctor: "The box's own design.", 'security-page': 'What listens.', nmap: 'Only which ports answer, from where it ran.' },
      freshness: { doctor: now, 'security-page': now - 600, nmap: now - 86400 },
      items: [{ about: { kind: 'setting', key: 'kernel-links' }, title: 'Kernel link protections', sources: ['doctor', 'security-page'], status: 'problem',
        titles: ['doctor: Link protections off', 'security-page: Kernel link protections'], fix: 'Turn them on', alone: false, could_see: [] },
      { about: { kind: 'service', key: 'tcp/9999' }, title: 'TCP 9999', sources: ['nmap'], status: 'warn', titles: ['nmap: nmap found TCP 9999 open'], fix: '',
        alone: true, could_see: ['security-page'] }] } } };
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

  // The joint report on the page.
  check('the summary: counted after merging', /After merging what the sources agree on: 2 to fix, 5 to look at; 3 things several sources agree are fine\./.test(t(d.getElementById('joint-summary'))),
    t(d.getElementById('joint-summary')));
  const cards = [...d.querySelectorAll('#joint-items .joint-item')];
  check('an item per thing: what it is about, the sources that agree, what to do', cards.length === 2 && /Kernel link protections/.test(t(cards[0]))
    && /2 sources agree/.test(t(cards[0])) && /the doctor/.test(t(cards[0])) && /the Security page/.test(t(cards[0])) && /To do: Turn them on/.test(t(cards[0])));
  check('seen by one source only: listed apart, saying who could have seen it', !d.getElementById('joint-alone').hidden
    && /TCP 9999 — only nmap said so; the Security page could have seen it and did not\./.test(t(d.getElementById('joint-alone-list'))), t(d.getElementById('joint-alone-list')));
  check('each source: what it covers and how fresh', d.querySelectorAll('#joint-sources tr').length === 3 && /Only which ports answer/.test(t(d.getElementById('joint-sources'))));
  check('the badge counts merged items (2), not every source\'s (3)', d.querySelector('a[href="#secdoctor"]').dataset.badge === '2', d.querySelector('a[href="#secdoctor"]').dataset.badge);
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
  check('no stray null or undefined on the page', !stray.length, stray.join(','));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
