// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Books page at hundreds (next-work plan step 14) in jsdom, static: a stand-in hub pages through
// 300 books as /admin/books does. The summary, a page of the table, the language list, search and
// filters asking the hub (not filtering here), paging, selecting a page or every matching book,
// bulk Check and Stop tracking, and a book's card opening from its row.
// Usage: [JSDOM=…/jsdom] node dom-books.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || 'jsdom');
const WEB = require('path').resolve(__dirname, '../../web');
const fs = require('fs');
const html = fs.readFileSync(`${WEB}/admin.html`, 'utf8').replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, '');
const js = fs.readFileSync(`${WEB}/admin.js`, 'utf8');
const LANGS = ['eng', 'fra', 'deu'];
const all = Array.from({ length: 300 }, (_, i) => {
  const n = i + 1;
  const kept = n <= 40;
  return { name: `book-${String(n).padStart(3, '0')}`, title: `Book ${n}`, language: LANGS[n % 3], date: `2026-0${1 + (n % 9)}-01`,
    description: `number ${n}`, size: n * 1000, kept, state: n === 2 ? 'newer' : n === 3 ? 'failed' : 'ok',
    source: kept ? { name: `book-${String(n).padStart(3, '0')}`, type: 'url', url: `https://example.invalid/${n}.zim` } : null, status: {} };
});
const asked = [];
const posted = [];
function books(qs) {
  const p = new URLSearchParams(qs);
  asked.push(Object.fromEntries(p));
  const q = (p.get('q') || '').toLowerCase();
  const hit = all.filter((b) => (!q || b.title.toLowerCase().includes(q) || b.name.includes(q)) && (!p.get('language') || b.language === p.get('language'))
    && (!p.get('state') || b.state === p.get('state')) && (!p.get('kept') || b.kept === (p.get('kept') === 'yes')));
  if (p.get('names') === '1') return { names: hit.map((b) => b.name), matching: hit.length };
  const pages = Math.max(1, Math.ceil(hit.length / 50));
  const page = Math.min(Math.max(1, Number(p.get('page') || 1)), pages);
  return { books: hit.slice((page - 1) * 50, page * 50), page, pages, per_page: 50, matching: hit.length,
    summary: { count: 300, bytes: all.reduce((a, b) => a + b.size, 0), kept: 40, languages: { eng: 100, fra: 100, deu: 100 }, states: { ok: 298, newer: 1, failed: 1 } } };
}
const snap = { policy: { keep_old: 1, check_every_hours: 24, min_free_mb: 500, auto_install: 1 }, sources: [], status: {}, token_set: false,
  running: false, types: [], progress: null, apps: {}, free_mb: 51000, job: {} };
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => { if (!/scrollTo|Not implemented/.test(e.message)) errors.push('jsdom: ' + e.message); });
vc.on('error', (...a) => { if (!/HTTP 404/.test(a.join(' '))) errors.push('console: ' + a.join(' ')); });
const dom = new JSDOM(html, { url: 'http://box.local/admin/#books', runScripts: 'outside-only', virtualConsole: vc, pretendToBeVisual: true });
const w = dom.window;
let libraryAsked = '';
w.fetch = async (u, opts = {}) => {
  const ok = (b) => new Response(JSON.stringify(b), { status: 200 });
  if (opts.method === 'POST' && u === '/admin/library') { posted.push(JSON.parse(opts.body)); return ok(snap); }
  if (u.startsWith('/admin/library')) { libraryAsked = u; return ok(snap); }
  if (u.startsWith('/admin/books?')) return ok(books(u.split('?')[1]));
  return new Response('{}', { status: 404 });
};
w.confirm = () => true;
w.eval(js);
let fails = 0;
const check = (name, cond, info = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${cond ? '' : `  ${info}`}`); fails += !cond; };
const t = (n) => (n ? n.textContent.replace(/\s+/g, ' ').trim() : '');
const wait = (ms = 400) => new Promise((r) => setTimeout(r, ms));
(async () => {
  await wait();
  const d = w.document;
  const rows = () => [...d.querySelectorAll('#books-table tbody tr.book-row')];
  check('the library\'s poll leaves the books out', libraryAsked === '/admin/library?books=0', libraryAsked);
  check('the summary: all of them, their size, the states worth a look', /^300 books, 43\.1 MB; 40 kept current\. 1 newer available\. 1 check failed\.$/.test(t(d.getElementById('books-summary'))),
    t(d.getElementById('books-summary')));
  check('one page of 50, from the hub', rows().length === 50 && /Page 1 of 6 \(300 matching\)/.test(t(d.getElementById('books-page'))));
  const cells = (r) => [...r.cells].map((c) => t(c));
  check('  a row: title, file, language, size, date, kept current, state', cells(rows()[0]).slice(1).join(' | ') === 'Book 1 book-001.zim | fra | 0.1 MB | 2026-02-01 | yes (url) | up to date',
    cells(rows()[0]).join(' | '));
  check('the languages, with their counts', [...d.querySelectorAll('#books-language option')].map((o) => t(o)).join(',') === 'any,deu (100),eng (100),fra (100)');
  d.getElementById('books-next').click(); await wait();
  check('Next: page 2 asked for', asked[asked.length - 1].page === '2' && /Page 2 of 6/.test(t(d.getElementById('books-page'))));
  d.getElementById('books-q').value = 'Book 1'; d.getElementById('books-q').dispatchEvent(new w.Event('input')); await wait();
  check('search: asked of the hub, from page 1', asked[asked.length - 1].q === 'Book 1' && asked[asked.length - 1].page === '1' && /\(111 matching\)/.test(t(d.getElementById('books-page'))),
    JSON.stringify(asked[asked.length - 1]));
  d.getElementById('books-q').value = ''; d.getElementById('books-kept').value = 'yes'; d.getElementById('books-kept').dispatchEvent(new w.Event('change')); await wait();
  check('filter: kept current', asked[asked.length - 1].kept === 'yes' && rows().length === 40);
  d.getElementById('books-page-all').click(); await wait(50);
  check('select this page', rows().every((r) => r.querySelector('input').checked) && /40 selected:/.test(t(d.getElementById('books-bulk'))));
  [...d.querySelectorAll('[data-bulk]')].find((b) => b.dataset.bulk === 'check').click(); await wait();
  const chk = posted.find((b) => b.action === 'check');
  check('bulk Check: every selected book, by name', chk && chk.names.length === 40 && chk.names[0] === 'book-001', JSON.stringify(chk));
  d.getElementById('books-select-none').click(); await wait(50);
  d.getElementById('books-kept').value = ''; d.getElementById('books-kept').dispatchEvent(new w.Event('change')); await wait();
  check('  Clear the selection', !rows().some((r) => r.querySelector('input').checked));
  d.getElementById('books-select-matching').click(); await wait();
  check('select every matching book, across the pages', /300 selected:/.test(t(d.getElementById('books-bulk'))) && asked.some((a) => a.names === '1'));
  d.getElementById('books-select-none').click(); await wait(50);
  rows()[1].querySelector('input').click(); rows()[2].querySelector('input').click();
  [...d.querySelectorAll('[data-bulk]')].find((b) => b.dataset.bulk === 'untrack').click(); await wait();
  check('bulk Stop tracking: one remove each', posted.filter((b) => b.action === 'remove').map((b) => b.name).join() === 'book-002,book-003',
    JSON.stringify(posted.filter((b) => b.action === 'remove')));
  rows()[0].querySelector('.link-button').click(); await wait(50);
  const card = d.querySelector('#books-table tr.book-card');
  check('a kept book\'s card opens under its row, with its buttons', card && /book-001\.zim/.test(t(card)) && [...card.querySelectorAll('button')].some((b) => t(b) === 'Update'));
  const hand = rows().find((r) => /book-050/.test(t(r)));
  hand.querySelector('.link-button').click(); await wait(50);
  check('  one put here by hand says the librarian leaves it alone', /put here by hand or from a USB stick: the librarian leaves it alone/.test(t(d.querySelector('#books-table tr.book-card'))));
  check('no page errors', !errors.length, errors.join(' | '));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
