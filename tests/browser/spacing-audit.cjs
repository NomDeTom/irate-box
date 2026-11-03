// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The vertical-spacing audit (spacing-audit.sh runs it): every /admin page and its sidebar in headless Chromium through
// Playwright on this machine. It reports a card or button that sits on the text above or below it, or a gap outside
// its band, at a desktop width and a phone's. The rules are the ones the browser style audit uses too
// (spacing-rules.js); this runner is the no-Docker way to see the whole menu.
//
// Environment (spacing-audit.sh sets them): BASE, FRONT_SECRET, PLAYWRIGHT (its module), CHROME (optional), SPACING_JSON.
const path = require('path');
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');

const BASE = process.env.BASE;
const WIDTHS = (process.env.SPACING_WIDTHS || '1280 390').split(/\s+/).filter(Boolean).map(Number);
const STANDALONE = [
  ['Setup steps', '/admin/setup.html'],
  ['Factory', '/admin/factory.html'],
  ['Git builds', '/admin/git.html'],
  ['Mesh', '/admin/mesh.html'],
  ['Rockchip boards', '/admin/rockchip.html'],
];

const all = [];
const seen = new Set();
function add(width, page, findings) {
  for (const finding of findings) {
    const key = `${width}|${finding.pane}|${finding.rule}|${finding.what}`;
    if (seen.has(key)) continue;
    seen.add(key);
    all.push({ width, page, ...finding });
  }
}
async function measure(page, width, pageName) {
  const findings = await page.evaluate((label) => {
    const out = [];
    for (const pane of document.querySelectorAll('.admin-pane[id]')) {
      if (!pane.getClientRects().length) continue;
      out.push(...SpacingAudit.measurePane(window, document, pane, label));
    }
    out.push(...SpacingAudit.measureOverflow(window, document, label));
    return out;
  }, pageName);
  add(width, pageName, findings);
}
const SECURITY_ADDRESSES = ['guest-access', 'sign-in', 'process-safety', 'secrets', 'connections'];
// The Security page's five addresses, as a doctor's link, the tour or a bookmark opens them: the selected tab must be
// inside the strip at a phone's width, not left off-screen at its start.
async function securityTabs(page, width) {
  let visited = 0;
  for (const name of SECURITY_ADDRESSES) {
    await page.evaluate((hash) => { location.hash = hash; }, `#security/${name}`);
    await page.waitForTimeout(250);
    const found = await page.evaluate(({ label, wanted }) => {
      const strip = document.querySelector('#security .view-switch');
      const tab = document.querySelector('#security [role="tab"][aria-selected="true"]');
      const panel = [...document.querySelectorAll('#security [role="tabpanel"]')].find((x) => !x.hidden);
      const out = [];
      if (!strip || !tab) {
        out.push({ pane: 'security', page: label, rule: 'a Security tab address did not select a tab', what: label, gap: 0, expected: { min: 0, max: 0 }, pair: 'tab' });
        return out;
      }
      const sr = strip.getBoundingClientRect(), tr = tab.getBoundingClientRect();
      if (tr.left < sr.left - 1 || tr.right > sr.right + 1) {
        out.push({ pane: 'security', page: label, rule: 'the selected tab is outside the strip',
          what: `${(tab.textContent || '').trim()} at ${Math.round(tr.left)}-${Math.round(tr.right)} in ${Math.round(sr.left)}-${Math.round(sr.right)}`,
          gap: 0, expected: { min: 0, max: 0 }, pair: 'tab' });
      }
      if (!panel || panel.id !== wanted) {
        out.push({ pane: 'security', page: label, rule: 'the address did not open its panel', what: panel ? panel.id : 'none', gap: 0, expected: { min: 0, max: 0 }, pair: 'tab' });
      }
      return out;
    }, { label: `admin: Security / ${name}`, wanted: `security/${name}` });
    add(width, `admin: Security / ${name}`, found);
    visited += 1;
  }
  return visited;
}
// A page in tabs shows one panel at a time; each tab is a page view of its own for the audit.
async function measureTabs(page, width, pageName) {
  const tabs = await page.evaluate(() => [...document.querySelectorAll('.admin-page:not([hidden]) [role="tab"]')]
    .map((tab, index) => ({ index, label: (tab.textContent || '').trim().replace(/\s+/g, ' '), selected: tab.getAttribute('aria-selected') === 'true', shown: !!tab.getClientRects().length }))
    .filter((tab) => tab.shown));
  let visited = 0;
  for (const tab of tabs) {
    if (tab.selected) continue;
    await page.evaluate((index) => {
      const found = [...document.querySelectorAll('.admin-page:not([hidden]) [role="tab"]')];
      if (found[index]) found[index].click();
    }, tab.index);
    await page.waitForTimeout(150);
    await measure(page, width, `${pageName} / ${tab.label}`);
    visited += 1;
  }
  return visited;
}
async function openSidebar(page) {
  await page.evaluate(() => {
    const menu = document.querySelector('.admin-menu');
    if (!menu || !menu.getClientRects().length) return;
    if (menu.getAttribute('aria-expanded') !== 'true') menu.click();
  });
  await page.waitForTimeout(250);
  // All groups open: the menu's full height is what a reader sees once they unfold it.
  await page.evaluate(() => {
    document.querySelectorAll('.admin-side-group[aria-expanded="false"]').forEach((head) => head.click());
  });
  await page.waitForTimeout(250);
}
async function sidebar(page, width) {
  await openSidebar(page);
  const findings = await page.evaluate(() => SpacingAudit.measureSidebar(window, document, 'sidebar'));
  add(width, 'sidebar', findings);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });
  let views = 0;
  for (const width of WIDTHS) {
    const context = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 900 },
      extraHTTPHeaders: process.env.FRONT_SECRET ? { 'X-Irate-Front': process.env.FRONT_SECRET, 'X-Irate-Admin': '1' } : {} });
    const page = await context.newPage();
    await page.goto(`${BASE}/admin/`, { waitUntil: 'load' });
    await page.waitForTimeout(2500);
    await page.addScriptTag({ path: path.join(__dirname, 'spacing-rules.js') });
    const adminPages = await page.evaluate(() => (window.AL ? AL.pages().map((p) => [p.title, p.link.getAttribute('href')]) : []));
    for (const [title, href] of adminPages) {
      await page.evaluate((hash) => { location.hash = hash; }, href);
      await page.waitForTimeout(900);
      await measure(page, width, `admin: ${title}`);
      views += 1 + await measureTabs(page, width, `admin: ${title}`);
    }
    views += await securityTabs(page, width);
    await sidebar(page, width);

    for (const [title, url] of STANDALONE) {
      await page.goto(`${BASE}${url}`, { waitUntil: 'load' });
      await page.waitForTimeout(1200);
      await page.addScriptTag({ path: path.join(__dirname, 'spacing-rules.js') });
      await measure(page, width, `admin: ${title}`);
      views += 1 + await measureTabs(page, width, `admin: ${title}`);
    }
    await context.close();
  }
  await browser.close();

  all.sort((a, b) => a.width - b.width || a.page.localeCompare(b.page) || a.pane.localeCompare(b.pane) || a.rule.localeCompare(b.rule));
  let last = '';
  for (const f of all) {
    const where = `${f.width}px ${f.page} #${f.pane}`;
    if (where !== last) {
      console.log(`\n${where}`);
      last = where;
    }
    console.log(`  ${f.rule}`);
    console.log(`    ${f.what}`);
  }
  if (process.env.SPACING_JSON) fs.writeFileSync(process.env.SPACING_JSON, all.map((x) => JSON.stringify(x)).join('\n') + '\n');
  console.log(`\n${views} page views; spacing findings: ${all.length}`);
  process.exit(all.length ? 1 : 0);
})().catch((error) => {
  console.error(`spacing audit failed: ${error.stack || error.message}`);
  process.exit(2);
});
