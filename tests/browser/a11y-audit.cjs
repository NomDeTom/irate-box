// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The accessibility audit (a11y-audit.sh runs it): axe-core's WCAG 2.0 and 2.1 rules, levels A and AA, on every hub
// page and every /admin page, in each theme and palette, in headless Chromium. Reports each rule broken by page, with
// the themes it is broken in and a few of its elements; then the contrast failures by colour pair, worst first,
// since one pair of colours is usually behind many elements. Exit 1 when anything is broken.
//
// Environment (a11y-audit.sh sets them): BASE, FRONT_SECRET, AXE (axe.min.js), PLAYWRIGHT (its module), CHROME (an
// executable, optional), A11Y_THEMES ("light dark eink cybercore"), A11Y_PALETTES ("standard contrast"), A11Y_PAGES (a
// part of a page's path or admin title, optional), A11Y_JSON (a file for every failing element, optional).
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');

const BASE = process.env.BASE;
const PUBLIC = ['/', '/help.html', '/about.html', '/account.html', '/certificate.html', '/board.html', '/drop.html', '/esp-flasher.html',
  '/irc.html', '/locks.html', '/mesh.html', '/notebook.html', '/services.html', '/themes.html'];
// The theme menu's choices as themes.js keeps them: Light and Dark in "theme"; E-ink and Cybercore in "hub-theme", over
// their base.
const THEMES = { light: { theme: 'light' }, dark: { theme: 'dark' }, eink: { theme: 'light', 'hub-theme': 'eink' }, cybercore: { theme: 'dark', 'hub-theme': 'cybercore' } };
const themes = (process.env.A11Y_THEMES || 'light dark eink cybercore').split(/\s+/).filter((name) => THEMES[name]);
const palettes = (process.env.A11Y_PALETTES || 'standard contrast').split(/\s+/).filter(Boolean);
const only = process.env.A11Y_PAGES || '';
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

const broken = new Map();   // rule|page → {rule, impact, page, combos: Set, targets: Set}
const pairs = new Map();    // fg|bg|need → {fg, bg, need, worst, count, pages: Set, sample}
const perCombo = {};
const everyElement = [];

async function audit(page, label, combo) {
  await page.addScriptTag({ path: process.env.AXE });
  const result = await page.evaluate(async (tags) => {
    const r = await axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] });
    return r.violations.map((v) => ({ rule: v.id, impact: v.impact, nodes: v.nodes.map((n) => {
      const d = (n.any[0] || {}).data || {};
      const el = document.querySelector(n.target[0]);
      return { target: n.target.join(' '), fg: d.fgColor, bg: d.bgColor, ratio: d.contrastRatio, need: d.expectedContrastRatio,
        text: el ? el.textContent.replace(/\s+/g, ' ').trim().slice(0, 40) : '' };
    }) }));
  }, TAGS);
  const name = `${combo.theme}/${combo.palette}`;
  for (const { rule, impact, nodes } of result) {
    const key = `${rule}|${label}`;
    if (!broken.has(key)) broken.set(key, { rule, impact, page: label, combos: new Set(), targets: new Set() });
    const entry = broken.get(key);
    entry.combos.add(name);
    nodes.forEach((node) => entry.targets.add(node.target));
    perCombo[name] = (perCombo[name] || 0) + nodes.length;
    for (const node of nodes) {
      everyElement.push({ page: label, ...combo, rule, ...node });
      if (rule !== 'color-contrast' || !node.fg) continue;
      const pairKey = `${node.fg}|${node.bg}|${node.need}`;
      if (!pairs.has(pairKey)) pairs.set(pairKey, { fg: node.fg, bg: node.bg, need: node.need, worst: node.ratio, count: 0, pages: new Set(), combos: new Set(), sample: node.text });
      const pair = pairs.get(pairKey);
      pair.count += 1;
      pair.worst = Math.min(pair.worst, node.ratio);
      pair.pages.add(label);
      pair.combos.add(name);
    }
  }
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });
  let pagesSeen = 0;
  for (const theme of themes) for (const palette of palettes) {
    const combo = { theme, palette };
    perCombo[`${theme}/${palette}`] = 0;
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 },
      extraHTTPHeaders: process.env.FRONT_SECRET ? { 'X-Irate-Front': process.env.FRONT_SECRET, 'X-Irate-Admin': '1' } : {} });
    await context.addInitScript(([keys, chosen]) => {
      try {
        localStorage.clear();
        for (const [key, value] of Object.entries(keys)) localStorage.setItem(key, value);
        localStorage.setItem('hub-palette', chosen);
      } catch {
        // No storage: the default theme and palette, which the report then says as the asked-for ones.
      }
    }, [THEMES[theme], palette]);
    const page = await context.newPage();
    for (const path of PUBLIC.filter((x) => x.includes(only))) {
      try {
        await page.goto(BASE + path, { waitUntil: 'load' });
        await page.waitForTimeout(1200);
        await audit(page, path, combo);
        pagesSeen += 1;
      } catch (error) {
        console.log(`SKIP ${path} (${theme}/${palette}): ${error.message.split('\n')[0]}`);
      }
    }
    await page.goto(`${BASE}/admin/`, { waitUntil: 'load' });
    await page.waitForTimeout(2500);
    const adminPages = await page.evaluate(() => AL.pages().map((adminPage) => [adminPage.title, adminPage.link.getAttribute('href')]));
    for (const [title, href] of adminPages.filter(([title]) => `admin: ${title}`.includes(only))) {
      await page.evaluate((hash) => { location.hash = hash; }, href);
      await page.waitForTimeout(1200);
      await audit(page, `admin: ${title}`, combo);
      pagesSeen += 1;
    }
    await context.close();
  }
  await browser.close();

  const RANK = { critical: 0, serious: 1, moderate: 2, minor: 3 };
  const rows = [...broken.values()].sort((left, right) => (RANK[left.impact] - RANK[right.impact]) || left.rule.localeCompare(right.rule) || left.page.localeCompare(right.page));
  for (const row of rows) {
    const every = row.combos.size === themes.length * palettes.length ? 'every theme' : [...row.combos].join(', ');
    console.log(`FAIL ${row.page}: ${row.rule} (${row.impact}), ${row.targets.size} element${row.targets.size === 1 ? '' : 's'}, ${every}`);
    [...row.targets].slice(0, 3).forEach((target) => console.log(`       ${target}`));
  }
  if (pairs.size) {
    console.log('\nContrast by colour pair, most elements first (text colour on background: worst ratio, needed):');
    [...pairs.values()].sort((left, right) => right.count - left.count).slice(0, 25).forEach((pair) => {
      console.log(`  ${pair.fg} on ${pair.bg}: ${pair.worst}:1, needs ${pair.need}; ${pair.count} elements on ${pair.pages.size} page${pair.pages.size === 1 ? '' : 's'}; ${[...pair.combos].join(', ')}; e.g. "${pair.sample}"`);
    });
  }
  console.log(`\n${pagesSeen} page views; failing elements by theme/palette: ${Object.entries(perCombo).map(([name, n]) => `${name} ${n}`).join(', ')}`);
  if (process.env.A11Y_JSON) fs.writeFileSync(process.env.A11Y_JSON, everyElement.map((x) => JSON.stringify(x)).join('\n') + '\n');
  console.log(`failures: ${rows.length}`);
  process.exit(rows.length ? 1 : 0);
})();
