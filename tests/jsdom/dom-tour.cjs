// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The setup tour on /admin, in jsdom: the setup step lists every
// decision as a link; each lives in a real section, with its part to highlight; the tour opens
// each one's page and highlights it, the clipboard krab beside; Keep and next records it on the hub;
// changing the setting where it lives decides it too; Leave takes the highlight away.
// Usage: [JSDOM=…/jsdom] node dom-tour.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || "jsdom");
const WEB = require("path").resolve(__dirname, "../../web");
const fs = require("fs");
const adminApps = require("./admin-apps-fixture.cjs");
let fails = 0;
const check = (name, cond, info = "") => {
  console.log(`${cond ? "PASS" : "FAIL"} ${name}${cond ? "" : `  ${info}`}`);
  fails += !cond;
};
const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  if (!/scrollTo|Not implemented/.test(e.message))
    errors.push("jsdom: " + e.message);
});
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const html = fs.readFileSync(`${WEB}/admin.html`, "utf8");
const dom = new JSDOM(html, {
  url: "http://box/admin/#welcome",
  runScripts: "outside-only",
  virtualConsole: vc,
  pretendToBeVisual: true,
});
const w = dom.window,
  d = w.document;
let settings = {
  setup_done: false,
  setup_decided: [],
  names_to: "users",
  visitor_counts: true,
};
const posted = [];
// The Security page's settings as security.py makes them (SSH password sign-in, Cockpit on the network,
// the hotspot firewall): the steps that point at them find their parts there.
const REPO = require("path").resolve(__dirname, "../..");
const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO)})
from irate_box.root import security as s
from irate_box.finding import finding
from irate_box.msg import M
cp = [{"proto": "tcp", "port": 9090, "addr": "0.0.0.0", "unit": "cockpit.socket", "pid": 1, "name": s.named("Cockpit")}]
fs = s.ssh_findings({"permitrootlogin": "no", "passwordauthentication": "yes"}, ["pi"], {})
fs += s.cockpit_findings(cp, {})
fs += [finding("firewall", M("firewall.title", "Hotspot firewall"), "warn", M("firewall.off", "The hotspot firewall is off."))]
print(json.dumps(fs))
`;
const security = {
  hub: [],
  pending: 0,
  scan: {
    at: Math.floor(Date.now() / 1000),
    findings: JSON.parse(
      require("child_process").execFileSync("python3", ["-c", py], {
        encoding: "utf8",
      }),
    ),
  },
  results: [],
};
w.fetch = async (u, o = {}) => {
  const json = (x, s = 200) => new Response(JSON.stringify(x), { status: s });
  if (o.method === "POST" && u === "/admin/settings") {
    const b = JSON.parse(o.body);
    posted.push(b);
    settings = { ...settings, ...b };
    return json(settings);
  }
  if (u === "/admin/settings") return json(settings);
  if (u === "/admin/apps") return json({ apps: adminApps() });
  if (u === "/admin/security") return json(security);
  if (u === "/admin/tailscale")
    return json({ state: "stopped", want: { mode: "off" }, max_hours: 24 });
  if (u === "/admin/access")
    return json({
      apps: [
        {
          id: "term",
          title: "Terminal",
          mode: "private",
          login: true,
          kind: "addon",
          unit: true,
          users: false,
          visible: "auto",
        },
      ],
      pages: {},
      results: [],
    });
  return json({}, 404);
};
w.confirm = () => true;
w.sessionStorage.clear();
// Every line the setup page says about what is left, in order, from the first draw on.
const leftSaid = [];
new w.MutationObserver(() => {
  const text = d.getElementById("setup-left").textContent;
  if (leftSaid[leftSaid.length - 1] !== text) leftSaid.push(text);
}).observe(d.getElementById("setup-left"), { childList: true, characterData: true, subtree: true });
["admin-widgets.js", "admin-layout.js", "admin.js", "admin-tour.js"].forEach(
  (f) => w.eval(fs.readFileSync(`${WEB}/${f}`, "utf8")),
);
const T = w.TOUR;
const shownPage = () =>
  [...d.querySelectorAll(".admin-page")].find((p) => !p.hidden);
(async () => {
  await wait(400);
  // While the page's parts are still reporting (here Books never does: its fetch fails), the count waits.
  check(
    "while the parts report: the count waits, says so, and the start button waits too",
    /Checking the steps/.test(d.getElementById("setup-left").textContent) &&
      T.left() === null &&
      d.querySelector("#welcome .tour-start").disabled &&
      /checking/.test(d.querySelector('#setup-steps [data-step="books"]').textContent),
    d.getElementById("setup-left").textContent,
  );
  await wait(4000);
  const counts = leftSaid.filter((text) => /still to do/.test(text));
  check(
    "  then one count, drawn once, after the checking line",
    /^Checking the steps/.test(leftSaid[0] || "") &&
      counts.length === 1 &&
      leftSaid.indexOf(counts[0]) === leftSaid.length - 1 &&
      !d.querySelector("#welcome .tour-start").disabled &&
      typeof T.left() === "number" &&
      /to do/.test(d.querySelector('#setup-steps [data-step="books"]').textContent),
    leftSaid.join(" | "),
  );
  // One list, every step in the same form: the setup's own steps, the decisions, a backup last.
  const rows = [...d.querySelectorAll("#setup-steps > li[data-step]")];
  check(
    "one list: every step and decision, the backup last",
    rows.length === T.STEPS.length &&
      T.STEPS.length === 19 &&
      rows[rows.length - 1].dataset.step === "backup",
    rows.length,
  );
  check(
    "  each the same form: its name, its pill, a line, Open settings",
    rows.every(
      (r) =>
        r.querySelector("strong") &&
        r.querySelector('[class$="-pill"]') &&
        r.querySelector(".step-text") &&
        r.querySelector("a[data-tour]"),
    ),
  );
  const py = fs.readFileSync(
    require("path").resolve(__dirname, "../../irate_box/hub/server.py"),
    "utf8",
  );
  const ids = (py.match(/SETUP_DECISIONS = \(([\s\S]*?)\)/) || [])[1] || "";
  check(
    "the hub knows the same steps",
    T.ALL.every((x) => ids.includes(`"${x.id}"`)) &&
      (ids.match(/"/g) || []).length / 2 === T.ALL.length,
    ids,
  );
  for (const dec of T.STEPS) {
    const sec = d.getElementById(dec.section) || d.getElementById(dec.fallback);
    check(
      `"${dec.label}": its section is on a page`,
      sec && !!sec.closest(".admin-page"),
      dec.section,
    );
    if (typeof dec.target === "string")
      check(
        "  and its part is there",
        !!d.querySelector(dec.target),
        dec.target,
      );
  }
  // The tour, from the start.
  click(d.querySelector("#welcome .tour-start"));
  await wait();
  let p = shownPage();
  check(
    "starting: the first step not done opens, the bar counting all of them",
    !!d.querySelector(".tour-bar") &&
      / of 19: /.test(d.querySelector(".tour-bar").textContent),
    d.querySelector(".tour-bar") &&
      d.querySelector(".tour-bar").textContent.slice(0, 80),
  );
  check(
    "  the bar, with the clipboard krab",
    !!d.querySelector(
      '.tour-bar img.tour-guide[src$="krab-controller-clipboard.webp"]',
    ),
  );
  T.go(T.STEPS.findIndex((x) => x.id === "visitors"));
  await wait(400);
  p = shownPage();
  check(
    "a decision's page opens (Security), its part highlighted",
    p &&
      p.contains(d.getElementById("security")) &&
      !!d.querySelector(".tour-target") &&
      d
        .querySelector(".tour-target")
        .contains(d.getElementById("visitor_counts")),
  );
  click(
    [...d.querySelectorAll(".tour-bar button")].find((b) =>
      /Keep and continue/.test(b.textContent),
    ),
  );
  await wait();
  check(
    "Keep and next: decided on the hub",
    posted.some((b) => (b.setup_decided || []).includes("visitors")),
    JSON.stringify(posted),
  );
  p = shownPage();
  check(
    "  and the next opens (Status tiles: the names)",
    p &&
      p.contains(d.getElementById("status-tiles")) &&
      !!d.querySelector(
        ".tour-target #names-to-box, #names-to-box.tour-target",
      ),
    p && p.getAttribute("aria-label"),
  );
  // Changing it where it lives decides it too.
  d.querySelector(".tour-target").dispatchEvent(
    new w.Event("change", { bubbles: true }),
  );
  await wait();
  check(
    "changing the setting in place decides it",
    posted.some((b) => (b.setup_decided || []).includes("names")),
  );
  T.go(T.STEPS.findIndex((step) => step.id === "root-policy"));
  await wait(400);
  d.getElementById("security-policy").dispatchEvent(
    new w.Event("change", { bubbles: true }),
  );
  await wait();
  check(
    "staging a security choice does not complete its tour decision",
    !T.decided().has("root-policy"),
  );
  d.getElementById("security-policy").dispatchEvent(
    new w.Event("settings-saved"),
  );
  await wait();
  check(
    "a successful security Save completes its tour decision",
    T.decided().has("root-policy"),
  );
  T.go(T.STEPS.findIndex((x) => x.id === "cockpit"));
  await wait(400);
  check(
    "Cockpit's step: its setting on the Security page highlighted",
    !!d.querySelector(".tour-target") &&
      (d.querySelector(".tour-target").id === "sec-cockpit" ||
        !!d.querySelector(".tour-target #sec-cockpit")),
    (d.querySelector(".tour-target") || {}).outerHTML,
  );
  T.go(T.STEPS.findIndex((x) => x.id === "ssh"));
  await wait(400);
  check(
    "  SSH's: its password sign-in setting",
    !!d.querySelector(".tour-target") &&
      (d.querySelector(".tour-target").id === "sec-ssh-password" ||
        !!d.querySelector(".tour-target #sec-ssh-password")),
    (d.querySelector(".tour-target") || {}).outerHTML,
  );
  T.go(T.STEPS.findIndex((x) => x.id === "connection"));
  await wait(400);
  check(
    "How guests reach the box: the hotspot firewall, and the words name guest internet access too",
    !!d.querySelector(".tour-target") &&
      (d.querySelector(".tour-target").id === "sec-firewall" ||
        !!d.querySelector(".tour-target #sec-firewall")) &&
      /Guest internet access/.test(d.querySelector(".tour-bar").textContent),
    (d.querySelector(".tour-target") || {}).outerHTML,
  );
  T.go(T.STEPS.findIndex((x) => x.id === "addons"));
  await wait(400);
  check(
    "Add-ons: the list of every app and add-on, not the few with no page of their own",
    !!d.querySelector("#inv-list.tour-target, .tour-target #inv-list"),
    (d.querySelector(".tour-target") || {}).id,
  );
  // The last step is the backup; past it, the tour ends back at the setup steps.
  T.go(T.STEPS.length - 1);
  await wait(400);
  check(
    "the last step: Take a backup, its download highlighted",
    /Take a backup/.test(d.querySelector(".tour-bar").textContent) &&
      !!d.querySelector(".tour-target[download]"),
  );
  click(
    [...d.querySelectorAll(".tour-bar button")].find((b) =>
      /Skip/.test(b.textContent),
    ),
  );
  await wait();
  p = shownPage();
  check(
    "past the last step: the bar is gone and the setup steps show",
    !d.querySelector(".tour-bar") &&
      p &&
      p.contains(d.getElementById("welcome")) &&
      w.location.hash === "#welcome",
    p && p.getAttribute("aria-label"),
  );
  T.go(T.STEPS.findIndex((x) => x.id === "cockpit"));
  await wait(400);
  click(
    [...d.querySelectorAll(".tour-bar button")].find((b) =>
      /Leave/.test(b.textContent),
    ),
  );
  check(
    "Leave the tour: no bar, no highlight",
    !d.querySelector(".tour-bar") && !d.querySelector(".tour-target"),
  );
  check(
    "the setup page says how many are left, of all 19",
    / of 19 still to do or decide/.test(
      d.getElementById("setup-left").textContent,
    ),
    d.getElementById("setup-left").textContent,
  );
  click(d.querySelector("#backup a[download]"));
  await wait();
  check(
    "downloading a backup is the backup step done",
    posted.some((b) => (b.setup_decided || []).includes("backup")) &&
      /done/.test(
        d.querySelector('#setup-steps [data-step="backup"]').textContent,
      ),
  );
  check(
    "the global override of the locked-tile action: offered, Per app by default",
    /Locked-tile action for every app/.test(
      d.getElementById("sign-in-offer-box").textContent,
    ) && /Per app/.test(d.getElementById("sign-in-offer-box").textContent),
  );
  // Muted: still listed, greyed, but not counted as left to do.
  const before = T.left();
  const row = d.querySelector('#setup-steps [data-step="cockpit"]');
  [...row.querySelectorAll("button.mute-step")][0].click();
  await wait();
  check(
    "mute: asked of the hub, still listed with a muted word, and no longer counted",
    posted.some((b) => (b.setup_muted || []).includes("cockpit")) &&
      /no reminders/.test(
        d.querySelector('#setup-steps [data-step="cockpit"]').textContent,
      ) &&
      T.left() === before - 1,
    `${before} -> ${T.left()}`,
  );
  [
    ...d
      .querySelector('#setup-steps [data-step="cockpit"]')
      .querySelectorAll("button.mute-step"),
  ][0].click();
  await wait();
  check("  Resume reminders counts it again", T.left() === before);
  // The box's own sign-in for scripts: what it has, not "set at first use".
  w.drawPasswordStep({ password_set: false, on: false });
  await wait();
  const pw = d.querySelector('#setup-steps [data-step="password"]');
  check(
    "the password step: no password yet, said so and left to do",
    /No password yet/.test(pw.textContent) && !/done/.test(pw.textContent),
    pw.textContent,
  );
  w.drawPasswordStep({ password_set: true, on: true });
  await wait();
  check(
    "  set: done",
    /done/.test(d.querySelector('#setup-steps [data-step="password"]').textContent),
  );
  // A box without Tailscale or Cockpit (the Pi): neither step is listed or toured.
  T.go(T.STEPS.findIndex((x) => x.id === "terminal"));
  await wait(400);
  T.has("tailscale", false);
  T.has("cockpit", false);
  await wait();
  const listed = [...d.querySelectorAll("#setup-steps > li[data-step]")].map(
    (r) => r.dataset.step,
  );
  check(
    "no Tailscale and no Cockpit: 17 steps, neither of them",
    T.STEPS.length === 17 &&
      listed.length === 17 &&
      !listed.includes("tailscale") &&
      !listed.includes("cockpit") &&
      / of 17 still to do or decide/.test(
        d.getElementById("setup-left").textContent,
      ),
    listed.join(","),
  );
  check(
    "  the tour stays on the step it was on",
    / of 17: Terminal/.test(d.querySelector(".tour-bar").textContent),
    d.querySelector(".tour-bar").textContent.slice(0, 80),
  );
  T.has("cockpit", true);
  await wait();
  check(
    "  Cockpit found later: its step back",
    T.STEPS.length === 18 && T.STEPS.some((x) => x.id === "cockpit"),
  );
  check("no page errors", !errors.length, errors.join("; "));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
function click(el) {
  el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
}
