// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hotspot's names on /admin → Network in jsdom: the name field and its note, the second name
// only in two-network mode with the suffixed placeholder, saving the names (and restarting a
// hotspot that is on), and the Appearance links to the box name and the hotspot. Usage:
// [JSDOM=…/jsdom] node dom-hotspot-names.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || "jsdom");
const fs = require("fs");
const path = require("path");
const WEB = path.resolve(__dirname, "../../web");
let fails = 0;
const check = (name, cond, info = "") => {
  console.log(`${cond ? "PASS" : "FAIL"} ${name}${cond ? "" : `  ${info}`}`);
  fails += !cond;
};
const t = (n) => (n ? n.textContent.replace(/\s+/g, " ").trim() : "");
const wait = (ms = 300) => new Promise((r) => setTimeout(r, ms));
const json = (b, status = 200) => new Response(JSON.stringify(b), { status });

const base = JSON.parse(
  fs.readFileSync(path.join(__dirname, "hotspot-fixture.json"), "utf8"),
);
base.settings = { ...base.settings, ssid: "Irate-Box", second_ssid: "" };
base.available = { ...base.available, two: null };
base.capabilities = { ...base.capabilities, ap_slots: 2, ap_radios: 2 };
const running = {
  up: false,
  preview: {
    kind: "own-radio",
    iface: "wlan0",
    channel: 6,
    text: "The hotspot runs on the radio's own interface.",
  },
  plan: {
    kind: "own-radio",
    iface: "wlan0",
    channel: 6,
    to_try: false,
    text: "The hotspot runs on the radio's own interface.",
  },
  radios: [{ iface: "wlan0", channels: [1, 6, 11] }],
  owner: {},
  confirmed: true,
  note: "",
};

function page({ up = false, refuse = null } = {}) {
  const posted = [];
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    if (!/scrollTo|Not implemented/.test(e.message)) errors.push(e.message);
  });
  const html = fs.readFileSync(`${WEB}/admin.html`, "utf8");
  const dom = new JSDOM(html, {
    url: "http://box.local/admin/#network",
    runScripts: "outside-only",
    virtualConsole: vc,
    pretendToBeVisual: true,
  });
  const w = dom.window;
  w.confirm = () => true;
  w.fetch = async (u, o = {}) => {
    if (o.method === "POST" && u === "/admin/hotspot") {
      const body = JSON.parse(o.body);
      posted.push(body);
      if (refuse) return json({ error: refuse }, 400);
      if (body.action) return json({ id: "x" }, 202);
      return json({ settings: body.settings, message: "Saved" });
    }
    if (u === "/admin/hotspot")
      return json({ ...base, running: { ...running, up } });
    return json({}, 404);
  };
  const js = ["admin-widgets.js", "admin-layout.js", "admin.js"]
    .map((f) => fs.readFileSync(`${WEB}/${f}`, "utf8"))
    .join(";\n");
  w.eval(js);
  return { w, d: w.document, posted, errors };
}

(async () => {
  let p = page();
  await wait(500);
  let d = p.d;
  check(
    "the hotspot form has the name, its note, and the second field hidden in one-network mode",
    !!d.getElementById("ap-ssid") &&
      d.getElementById("ap-ssid").value === "Irate-Box" &&
      t(d.getElementById("ap-ssid-label")) === "Hotspot name" &&
      /1 to 32 bytes on one line/.test(t(d.getElementById("ap-ssid-note"))) &&
      d.getElementById("ap-second").hidden &&
      d.getElementById("ap-second-label").hidden,
    t(d.getElementById("ap-ssid-note")),
  );
  check(
    "Appearance links to the box name and the hotspot",
    d.querySelector('#appearance-names a[href="#net-hostname-card"]') &&
      d.querySelector('#appearance-names a[href="#network/hotspot"]'),
  );
  check(
    "  and the box name card and the hotspot link back to Appearance",
    !!d.querySelector('#net-hostname-card a[href="#appearance"]') &&
      !!d.querySelector('#network\\/hotspot a[href="#appearance"]'),
  );

  const two = d.querySelector('#hs-modes input[value="two"]');
  check("two networks can be chosen here", two && !two.disabled);
  two.checked = true;
  two.dispatchEvent(new p.w.Event("change", { bubbles: true }));
  await wait();
  check(
    "two networks: the second field shows, the first is the open network, and the placeholder is the suffixed name",
    !d.getElementById("ap-second").hidden &&
      t(d.getElementById("ap-ssid-label")) === "Open network name" &&
      t(d.getElementById("ap-second-label")) === "Encrypted network name" &&
      d.getElementById("ap-second").placeholder === "Irate-Box (encrypted)",
    d.getElementById("ap-second").placeholder,
  );
  d.getElementById("ap-ssid").value = "Guests";
  d.getElementById("ap-ssid").dispatchEvent(new p.w.Event("input", { bubbles: true }));
  await wait();
  check(
    "  typing the first name moves the placeholder",
    d.getElementById("ap-second").placeholder === "Guests (encrypted)",
    d.getElementById("ap-second").placeholder,
  );
  d.getElementById("ap-second").value = "Guests-Enc";
  d.getElementById("ap-second").dispatchEvent(new p.w.Event("input", { bubbles: true }));
  d.getElementById("ap-save-names").click();
  await wait();
  const saved = p.posted.find((b) => b.settings);
  check(
    "Save name: both names sent with the settings, and the note says devices rejoin",
    saved &&
      saved.settings.ssid === "Guests" &&
      saved.settings.second_ssid === "Guests-Enc" &&
      /Devices on the hotspot rejoin under the new name/.test(
        t(d.getElementById("ap-name-note")),
      ),
    JSON.stringify(p.posted),
  );
  check("no page errors", !p.errors.length, p.errors.join(" | "));

  p = page({ up: true });
  await wait(500);
  d = p.d;
  d.getElementById("ap-ssid").value = "Guests";
  d.getElementById("ap-ssid").dispatchEvent(new p.w.Event("input", { bubbles: true }));
  d.getElementById("ap-save-names").click();
  await wait();
  check(
    "a hotspot that is on: the names saved, then it is restarted with them",
    p.posted.some((b) => b.settings && b.settings.ssid === "Guests") &&
      p.posted.some((b) => b.action === "on"),
    JSON.stringify(p.posted),
  );

  p = page({ refuse: "The two networks need different names, or devices cannot tell them apart." });
  await wait(500);
  d = p.d;
  d.getElementById("ap-ssid").value = "Same";
  d.getElementById("ap-ssid").dispatchEvent(new p.w.Event("input", { bubbles: true }));
  d.getElementById("ap-second").value = "Same";
  d.getElementById("ap-second").dispatchEvent(new p.w.Event("input", { bubbles: true }));
  d.getElementById("ap-save-names").click();
  await wait();
  check(
    "the same name refused: the words beside the field",
    /different names/.test(t(d.getElementById("ap-name-note"))),
    t(d.getElementById("ap-name-note")),
  );

  process.exit(fails ? 1 : 0);
})();
