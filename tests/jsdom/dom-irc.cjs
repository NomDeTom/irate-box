// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The IRC page in jsdom: the server address comes from the address the page was reached by.
// Usage: [JSDOM=/path/to/node_modules/jsdom] node dom-irc.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const fs = require('fs');
const WEB = require('path').resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/irc.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/irc.js`, 'utf8');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : '  ' + info}`); fails += !cond; };

function open(url) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push(e.message));
  const dom = new JSDOM(html, { url, runScripts: 'outside-only', virtualConsole: vc });
  dom.window.eval(js);
  return { d: dom.window.document, errors };
}

for (const [host, shown] of [['192.168.4.1', '192.168.4.1'], ['box.local', 'box.local']]) {
  const { d, errors } = open(`http://${host}/irc.html`);
  check(`${host}: server shown`, d.getElementById('irc-host').textContent === shown, d.getElementById('irc-host').textContent);
  check(`${host}: port shown`, d.getElementById('irc-port').textContent === '6667');
  check(`${host}: irc:// link`, d.getElementById('irc-link').href === `irc://${host}:6667/lobby`, d.getElementById('irc-link').href);
  check(`${host}: link text names the host`, d.getElementById('irc-link').textContent === `irc://${host}:6667/lobby`, d.getElementById('irc-link').textContent);
  check(`${host}: no script errors`, errors.length === 0, errors.join('; '));
}
const { d } = open('http://[fd00::1]/irc.html');
check('IPv6 host keeps its brackets in the link', d.getElementById('irc-link').href === 'irc://[fd00::1]:6667/lobby', d.getElementById('irc-link').href);
const marked = [...d.querySelectorAll('[data-service="/irc.html"]')];
check('the connect items (not the body) carry data-service', marked.length === 2 && !d.body.hasAttribute('data-service'), marked.length);
console.log(fails ? `failures: ${fails}` : 'all passed');
process.exit(fails ? 1 : 0);
