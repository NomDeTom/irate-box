// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The home page's HTTPS line (home.js, with https-address.js): offered only on plain HTTP and only when the box
// also serves HTTPS (/certificate.json says on). It links first to this same page over HTTPS, at the box's HTTPS
// port rather than this page's, then to /certificate for a device that warns.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-https-offer.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/index.html`, 'utf8');
const address = fs.readFileSync(`${WEB}/https-address.js`, 'utf8');
const home = fs.readFileSync(`${WEB}/home.js`, 'utf8');

let fails = 0;
function check(name, cond, info = '') {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : `  ${info}`));
  fails += !cond;
}
// answer: what /certificate.json says, an Error for no answer, or a number for an HTTP error status.
async function offered(url, answer) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  const w = dom.window;
  let asked = 0;
  w.fetch = async (path) => {
    asked += path === '/certificate.json';
    if (answer instanceof Error) throw answer;
    if (typeof answer === 'number') return new Response('', { status: answer });
    return new Response(JSON.stringify(answer), { status: 200 });
  };
  w.eval(address);
  w.eval(home);
  await new Promise((resolve) => setTimeout(resolve, 50));
  const offer = w.document.getElementById('https-offer');
  const links = [...offer.querySelectorAll('a')].map((link) => link.getAttribute('href'));
  return { shown: !offer.hidden, asked, links, text: offer.textContent.replace(/\s+/g, ' ').trim() };
}

(async () => {
  let seen = await offered('http://box/', { set_up: true, on: true, port: 443 });
  check('plain HTTP, HTTPS on: this page over HTTPS first, then the certificate',
    seen.shown && seen.links[0] === 'https://box/' && seen.links[1] === '/certificate', JSON.stringify(seen));
  check('  the browser warning said in plain words', /If your browser shows a warning, do not go past it/.test(seen.text), seen.text);
  seen = await offered('http://box:8080/?full#board', { set_up: true, on: true, port: 8443 });
  check('  at the box\'s HTTPS port, not this page\'s; the query and fragment kept',
    seen.shown && seen.links[0] === 'https://box:8443/?full#board', JSON.stringify(seen));
  seen = await offered('http://[fd00::1]/', { set_up: true, on: true, port: 443 });
  check('  an IPv6 address in brackets', seen.shown && seen.links[0] === 'https://[fd00::1]/', JSON.stringify(seen));
  seen = await offered('http://box/', { set_up: true, on: false, port: 443 });
  check('plain HTTP, HTTPS off: nothing offered', !seen.shown, JSON.stringify(seen));
  seen = await offered('http://box/', new Error('no answer'));
  check('plain HTTP, no answer: nothing offered', !seen.shown, JSON.stringify(seen));
  seen = await offered('http://box/', 500);
  check('plain HTTP, an error from the hub: nothing offered', !seen.shown, JSON.stringify(seen));
  seen = await offered('https://box/', { set_up: true, on: true, port: 443 });
  check('HTTPS: nothing offered, nothing asked', !seen.shown && !seen.asked, JSON.stringify(seen));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
