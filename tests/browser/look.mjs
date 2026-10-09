// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A look at a box's pages in headless Chromium, checked by script: each page at a phone's width and a
// desktop's, light and dark, with a screenshot of each. Run by tests/browser/run.sh (Chromium and this
// in one container); by hand: node look.mjs BASE_URL OUT_DIR, with HUB_LOGIN=user:password for /admin.
//
// Checked on every view:
//   - an element with `hidden` that is still displayed (a class rule beating the browser's [hidden]);
//   - a page wider than the window (sideways scroll);
//   - the header's buttons overlapping;
//   - script errors and failed requests the page made;
//   - in-page links (#pane, #pane/part) that lead nowhere;
//   - a row of choice chips with no choice, or more than one, selected.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = (process.argv[2] || "").replace(/\/$/, "");
const OUT = process.argv[3] || "out";
if (!BASE) { console.error("usage: node look.mjs BASE_URL OUT_DIR"); process.exit(2); }
const LOGIN = process.env.HUB_LOGIN || "";
const PAGES = (process.env.LOOK_PAGES || [
  "/", "/account.html",
  "/admin/#overview", "/admin/#updates", "/admin/#backup", "/admin/#security", "/admin/#secdoctor", "/admin/#health",
  "/admin/#network/access", "/admin/#network/hotspot", "/admin/#network/hardware", "/admin/#network/interfaces",
  "/admin/#toolkits", "/admin/#books", "/admin/#addons", "/admin/#git", "/admin/#accounts",
].join(" ")).split(/\s+/).filter(Boolean);
const WIDTHS = [390, 1280];
const SCHEMES = ["light", "dark"];
const SETTLE = Number(process.env.LOOK_SETTLE_MS || 3000);
mkdirSync(OUT, { recursive: true });

// --- Chromium over the DevTools protocol --------------------------------------------------------
const chrome = spawn(process.env.CHROMIUM || "chromium", ["--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
  "--remote-debugging-port=9222", "--user-data-dir=/tmp/look-profile", "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
chrome.stderr.on("data", () => {});
async function target() {
  for (let i = 0; i < 100; i++) {
    try {
      const list = await (await fetch("http://127.0.0.1:9222/json/list")).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("Chromium's DevTools never answered");
}
const ws = new WebSocket(await target());
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let seq = 0;
const waiting = new Map();
const events = [];
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && waiting.has(msg.id)) {
    const { ok, bad } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? bad(new Error(msg.error.message)) : ok(msg.result);
  } else if (msg.method) events.push(msg);
};
const send = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; waiting.set(id, { ok, bad }); ws.send(JSON.stringify({ id, method, params })); });

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
await send("Network.enable");
if (LOGIN) await send("Network.setExtraHTTPHeaders", { headers: { Authorization: "Basic " + Buffer.from(LOGIN).toString("base64") } });

// --- what is checked inside the page ----------------------------------------------------------
const CHECKS = `(() => {
  const vis = (e) => { const s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden'; };
  const shown = (e) => { for (let n = e; n && n !== document; n = n.parentElement) if (!vis(n)) return false; return true; };
  const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  const out = {};
  out.hiddenShown = [...document.querySelectorAll('[hidden]')].filter((e) => getComputedStyle(e).display !== 'none').map(name).slice(0, 20);
  out.sideways = document.documentElement.scrollWidth > window.innerWidth + 1 ? document.documentElement.scrollWidth - window.innerWidth : 0;
  if (out.sideways) {
    out.widest = [...document.querySelectorAll('body *')].filter((e) => shown(e) && e.getBoundingClientRect().right > window.innerWidth + 1)
      .sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right).slice(0, 5)
      .map((e) => name(e) + ' ' + Math.round(e.getBoundingClientRect().right) + 'px');
  }
  const btns = [...document.querySelectorAll('header .head-btn, .site-head .head-btn, .head-btn')].filter(shown).map((e) => [name(e), e.getBoundingClientRect()]);
  out.overlap = [];
  for (let i = 0; i < btns.length; i++) for (let j = i + 1; j < btns.length; j++) {
    const [a, r] = btns[i], [b, s] = btns[j];
    if (r.width && s.width && r.left < s.right - 1 && s.left < r.right - 1 && r.top < s.bottom - 1 && s.top < r.bottom - 1) out.overlap.push(a + ' / ' + b);
  }
  out.deadLinks = [...new Set([...document.querySelectorAll('a[href^="#"]')].filter(shown).map((a) => a.getAttribute('href'))
    .filter((h) => h.length > 1 && !document.getElementById(h.slice(1)) && !document.getElementById(h.slice(1).split('/')[0])
      && !document.querySelector('[data-pane="' + h.slice(1) + '"], [data-tab="' + h.slice(1).split('/').pop() + '"]')))].slice(0, 20);
  out.chipRows = [...document.querySelectorAll('.chip-group[role="group"]')].filter(shown).map((g) => {
    const n = g.querySelectorAll('.chip.selected, [aria-pressed="true"]').length;
    return n === 1 ? null : (g.getAttribute('aria-label') || name(g)) + ': ' + n + ' selected';
  }).filter(Boolean).slice(0, 20);
  out.title = document.title;
  return out;
})()`;

// --- each view ---------------------------------------------------------------------------------
const report = [];
let problems = 0;
for (const path of PAGES) {
  for (const width of WIDTHS) {
    for (const scheme of SCHEMES) {
      await send("Emulation.setDeviceMetricsOverride", { width, height: width < 600 ? 844 : 900, deviceScaleFactor: 1, mobile: width < 600 });
      await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
      events.length = 0;
      await send("Page.navigate", { url: "about:blank" });
      await send("Page.navigate", { url: BASE + path });
      await new Promise((r) => setTimeout(r, SETTLE));
      const { result } = await send("Runtime.evaluate", { expression: CHECKS, returnByValue: true });
      const v = result.value || {};
      v.errors = events.filter((e) => e.method === "Runtime.exceptionThrown").map((e) => (e.params.exceptionDetails.exception || {}).description || e.params.exceptionDetails.text)
        .concat(events.filter((e) => e.method === "Runtime.consoleAPICalled" && e.params.type === "error").map((e) => e.params.args.map((a) => a.value ?? a.description).join(" ")))
        .map((s) => String(s).split("\n")[0].slice(0, 200));
      v.failed = events.filter((e) => e.method === "Network.responseReceived" && e.params.response.status >= 500)
        .map((e) => e.params.response.status + " " + e.params.response.url.replace(BASE, "")).slice(0, 10);
      const shot = `${path.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "") || "hub"}-${width}-${scheme}.png`;
      const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      writeFileSync(`${OUT}/${shot}`, Buffer.from(data, "base64"));
      const bad = ["hiddenShown", "overlap", "deadLinks", "chipRows", "errors", "failed"].filter((k) => (v[k] || []).length).concat(v.sideways ? ["sideways"] : []);
      problems += bad.length;
      report.push({ path, width, scheme, shot, bad, ...v });
      console.log(`${bad.length ? "LOOK" : "ok  "} ${path} ${width}px ${scheme}${bad.length ? ": " + bad.map((k) => k === "sideways" ? `sideways ${v.sideways}px (${(v.widest || []).join(", ")})` : `${k} ${JSON.stringify(v[k])}`).join("; ") : ""}`);
    }
  }
}
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));
console.log(`${report.length} views, ${problems} thing${problems === 1 ? "" : "s"} to look at; screenshots and report.json in ${OUT}`);
ws.close();
chrome.kill();
process.exit(problems ? 1 : 0);
