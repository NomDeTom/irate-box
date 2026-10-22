// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The home page's certificate line (home.js): offered only on plain HTTP and only when the box also serves HTTPS
// (/certificate.json says on), linking to /certificate. Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-https-offer.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/index.html`, 'utf8');
const home = fs.readFileSync(`${WEB}/home.js`, 'utf8');

let fails = 0;
function check(name, cond, info = '') {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : `  ${info}`));
  fails += !cond;
}
async function offered(url, answer) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  const w = dom.window;
  let asked = 0;
  w.fetch = async (path) => {
    asked += path === '/certificate.json';
    if (answer instanceof Error) throw answer;
    return new Response(JSON.stringify(answer), { status: 200 });
  };
  w.eval(home);
  await new Promise((resolve) => setTimeout(resolve, 50));
  const offer = w.document.getElementById('https-offer');
  return { shown: !offer.hidden, asked, href: offer.querySelector('a').getAttribute('href') };
}

(async () => {
  let seen = await offered('http://box/', { set_up: true, on: true });
  check('plain HTTP, HTTPS on: the certificate offered', seen.shown && seen.href === '/certificate', JSON.stringify(seen));
  seen = await offered('http://box/', { set_up: true, on: false });
  check('plain HTTP, HTTPS off: nothing offered', !seen.shown, JSON.stringify(seen));
  seen = await offered('http://box/', new Error('no answer'));
  check('plain HTTP, no answer: nothing offered', !seen.shown, JSON.stringify(seen));
  seen = await offered('https://box/', { set_up: true, on: true });
  check('HTTPS: nothing offered, nothing asked', !seen.shown && !seen.asked, JSON.stringify(seen));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
