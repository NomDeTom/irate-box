// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Music page (web/music.js), in jsdom: the drop's audio listed as a playlist, another file left out, a chosen
// track in the player, and the next and previous buttons moving through it. Static: no hub needed.
// Usage: [JSDOM=…/jsdom] node dom-music.cjs
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const path = require('path');
const fs = require('fs');
const WEB = path.resolve(__dirname, '../../web');
const html = fs.readFileSync(`${WEB}/music.html`, 'utf8');
const js = fs.readFileSync(`${WEB}/music.js`, 'utf8');
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms));
const dom = new JSDOM(html, { url: 'http://box.local/music.html', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
w.HTMLMediaElement.prototype.play = async () => {};
let marked = [];
w.fetch = async (url, opts = {}) => {
  if (url === '/api/drop') {
    return { ok: true, status: 200, json: async () => ({ files: [
      { id: 'one', name: 'first.mp3', size: 1048576 },
      { id: 'two', name: 'second.ogg', size: 2048 },
      { id: 'three', name: 'photo.jpg', size: 10 },
    ] }) };
  }
  if (url === '/api/podcasts') {
    if (opts.method === 'POST') {
      marked.push(JSON.parse(opts.body));
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    return { ok: true, status: 200, json: async () => ({ episodes: [
      { feed: 'casts', feed_title: 'A Podcast', id: 'epone', title: 'Episode One', size: 4096, url: '/podcasts/casts/epone.mp3', starred: false, played: false },
    ] }) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};
w.eval(js);
const t = (n) => n.textContent.replace(/\s+/g, ' ').trim();
(async () => {
  await wait();
  const buttons = [...d.querySelectorAll('#music-list button[data-id]')];
  check(
    'the podcasts kept on the box, then the drop\'s audio, each with its size',
    buttons.length === 3
      && /A Podcast · Episode One/.test(buttons[0].textContent)
      && /first.mp3 \(1.0 MB\)/.test(buttons[1].textContent)
      && /second.ogg \(2 KB\)/.test(buttons[2].textContent),
    buttons.map((b) => b.textContent),
  );
  buttons[1].click();
  check('a chosen track goes into the player', d.getElementById('music-player').src === 'http://box.local/api/drop/one' && /first.mp3/.test(t(d.getElementById('music-now'))), d.getElementById('music-player').src);
  d.getElementById('music-next').click();
  check('  Next moves to the following track', d.getElementById('music-player').src === 'http://box.local/api/drop/two' && /second.ogg/.test(t(d.getElementById('music-now'))));
  d.getElementById('music-prev').click();
  check('  Previous goes back', d.getElementById('music-player').src === 'http://box.local/api/drop/one');
  buttons[0].click();
  check('  a podcast episode plays from its own address', d.getElementById('music-player').src === 'http://box.local/podcasts/casts/epone.mp3');
  const star = [...d.querySelectorAll('#music-list button.small')][0];
  star.click();
  await wait();
  check('  and Keep stars it on the box', marked.some((m) => m.feed === 'casts' && m.id === 'epone' && m.starred === true), marked);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
