// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hostile SVG against the real /svg/ policy, in headless Chromium: an inline script, an on*=
// handler, a foreignObject and a javascript: link, loaded through SVG-Edit's own loadSvgString
// and as a gallery save. Nothing runs; the policy reports the inline script it refuses. Run by
// tests/browser/hostile-svg.sh; skipped when Playwright, Chromium or SVGEDIT_BUNDLE is absent.
const fs = require('fs');
const http = require('http');
const path = require('path');

const repo = path.join(__dirname, '..', '..');
const bundle = process.env.SVGEDIT_BUNDLE;
const chrome = process.env.CHROME;

const SKIP = (why) => {
  console.log(`skipped: ${why}`);
  process.exit(0);
};

if (!chrome || !fs.existsSync(chrome)) SKIP('no Chromium (set CHROME=…)');
if (!bundle || !fs.existsSync(path.join(bundle, 'index.html'))) SKIP('set SVGEDIT_BUNDLE to a built SVG-Edit tree');
let chromium;
try {
  ({ chromium } = require(process.env.PLAYWRIGHT || 'playwright'));
} catch (_error) {
  SKIP('no Playwright (set PLAYWRIGHT=…/node_modules/playwright)');
}

const HOSTILE =
  '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" onload="window.__svgedit_pwned=\'onload\'">' +
  '<script>window.__svgedit_pwned="script"</script>' +
  '<foreignObject width="120" height="40"><body xmlns="http://www.w3.org/1999/xhtml" ' +
  'onload="window.__svgedit_pwned=\'foreign\'">x</body></foreignObject>' +
  '<a href="javascript:window.__svgedit_pwned=\'href\'"><rect width="20" height="20" fill="red"/></a></svg>';

const policyText = fs.readFileSync(path.join(repo, 'config', 'irate-box.nginx'), 'utf8');
const match = policyText.match(/set \$irate_box_svg_csp "([^"]+)";/);
if (!match) {
  console.error('no set $irate_box_svg_csp in config/irate-box.nginx');
  process.exit(1);
}

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.gif': 'image/gif',
  '.jpg': 'image/jpeg',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

const server = http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
  const origin = `http://127.0.0.1:${server.address().port}`;
  const policy = match[1]
    .replaceAll('$scheme://$host:$irate_box_svg_port', origin)
    .replaceAll('$scheme://$host:$irate_box_hub_port', origin)
    .replaceAll('$scheme://$host', 'http://127.0.0.1');
  const send = (status, body, type) => {
    response.writeHead(status, {
      'Content-Type': type || 'text/plain; charset=utf-8',
      'Content-Security-Policy': policy,
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-cache',
    });
    response.end(body);
  };
  const file = (name, type) => {
    if (!fs.existsSync(name) || !fs.statSync(name).isFile()) return false;
    send(200, fs.readFileSync(name), type || MIME[path.extname(name)] || 'application/octet-stream');
    return true;
  };

  if (pathname === '/api/saves') {
    return send(
      200,
      JSON.stringify({
        now: 1,
        ttl: 0,
        saves: [{ id: 'hostile', kind: 'svgedit', name: 'Hostile', created: 1, size: HOSTILE.length, thumb: true }],
      }),
      MIME['.json'],
    );
  }
  if (pathname === '/api/saves/hostile') {
    return send(200, JSON.stringify({ id: 'hostile', kind: 'svgedit', name: 'Hostile', state: HOSTILE, now: 1 }), MIME['.json']);
  }
  if (pathname === '/api/saves/hostile/thumb') {
    response.writeHead(200, {
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': 'attachment',
      'Content-Security-Policy': policy,
      'X-Content-Type-Options': 'nosniff',
    });
    return response.end(PNG);
  }
  if (pathname === '/svg/lock.js') return file(path.join(repo, 'web', 'lock.js'), MIME['.js']);
  if (pathname === '/svg/hub-return.js') return file(path.join(repo, 'web', 'hub-return.js'), MIME['.js']);
  if (pathname === '/svg' || pathname === '/svg/') return file(path.join(bundle, 'index.html'), MIME['.html']);
  if (pathname.startsWith('/svg/')) {
    const name = path.join(bundle, pathname.slice('/svg/'.length));
    if (!name.startsWith(bundle + path.sep)) return send(404, 'not found');
    if (file(name)) return;
  }
  send(404, 'not found');
});

const listen = () => new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));

(async () => {
  const port = await listen();
  const base = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch({
    executablePath: chrome,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));

  await page.addInitScript(() => {
    window.__svgedit_pwned = undefined;
    window.__svgedit_csp = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      window.__svgedit_csp.push({ directive: event.violatedDirective, blocked: event.blockedURI });
    });
  });

  await page.goto(`${base}/svg/`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.svgEditor && window.svgEditor.svgCanvas, { timeout: 15000 });
  await page.waitForSelector('#tool_hub_open', { state: 'attached', timeout: 5000 });

  let loadedError = null;
  try {
    await page.evaluate((svg) => window.svgEditor.loadSvgString(svg), HOSTILE);
  } catch (error) {
    // SVG-Edit's sanitizer may refuse the whole document; that is a safe outcome too.
    loadedError = String(error);
  }
  await page.waitForTimeout(300);
  const link = page.locator('a[href^="javascript:"]').first();
  if (await link.count()) {
    try {
      await link.click({ timeout: 1000 });
    } catch (_error) {
      // a link the editor removed or refuses to click
    }
    await page.waitForTimeout(150);
  }
  const loaded = await page.evaluate(() => ({
    pwned: window.__svgedit_pwned,
    csp: window.__svgedit_csp,
  }));
  loaded.error = loadedError;

  await page.evaluate(() => {
    const injected = document.createElement('script');
    injected.textContent = 'window.__svgedit_pwned = "injected";';
    document.body.append(injected);
  });
  await page.waitForTimeout(200);
  const injected = await page.evaluate(() => ({
    pwned: window.__svgedit_pwned,
    csp: window.__svgedit_csp,
  }));
  const blocked = injected.csp.some((violation) => violation.directive.startsWith('script-src'));

  await page.evaluate(() => {
    window.__svgedit_alerts = [];
    window.seAlert = (text) => window.__svgedit_alerts.push(text);
    document.getElementById('tool_hub_open').click();
  });
  await page.waitForSelector('#svghub-open', { timeout: 5000 });
  await page.locator('#svghub-open button').filter({ hasText: 'Hostile' }).first().click();
  await page.waitForTimeout(500);
  const gallery = await page.evaluate(() => ({
    pwned: window.__svgedit_pwned,
    csp: window.__svgedit_csp,
    title: window.svgEditor.title,
    dialog: !!document.getElementById('svghub-open'),
    alerts: window.__svgedit_alerts,
  }));

  const version = browser.version();
  await browser.close();
  server.close();

  const pwned = [loaded.pwned, injected.pwned, gallery.pwned].filter(Boolean);
  console.log(`Chromium ${version}`);
  console.log(
    JSON.stringify(
      {
        loaded: { pwned: loaded.pwned, error: loaded.error, violations: loaded.csp },
        injected: { pwned: injected.pwned, violations: injected.csp },
        gallery: { pwned: gallery.pwned, title: gallery.title, dialog: gallery.dialog, alerts: gallery.alerts, violations: gallery.csp },
        page_errors: errors,
      },
      null,
      2,
    ),
  );
  if (pwned.length || !blocked) {
    console.error(pwned.length ? 'FAIL: a script ran' : 'FAIL: the inline-script violation was not reported');
    process.exit(1);
  }
  console.log('PASS: nothing ran; the inline script was refused and reported');
})().catch((error) => {
  server.close();
  console.error(error);
  process.exit(1);
});
