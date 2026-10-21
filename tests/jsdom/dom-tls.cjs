// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// HTTPS in jsdom, static: /admin → Security's HTTPS section (not set up,
// on, a box moved to another subnet; make, switch, a new CA only after a confirm) and the public
// /certificate page (the fingerprint, what it may vouch for, the general step while no platform's
// steps have been tried on a device, the self-check saying whether this device trusts the box).
// Usage: [JSDOM=…/jsdom] node dom-tls.cjs
const { JSDOM, VirtualConsole } = require(process.env.JSDOM || "jsdom");
const WEB = require("path").resolve(__dirname, "../../web");
const fs = require("fs");
// The page's script tags stay: with runScripts 'outside-only' jsdom loads and runs none of them.
const strip = (h) => h;
let fails = 0;
const check = (name, cond, info = "") => {
  console.log(`${cond ? "PASS" : "FAIL"} ${name}${cond ? "" : `  ${info}`}`);
  fails += !cond;
};
const t = (n) => (n ? n.textContent.replace(/\s+/g, " ").trim() : "");
const wait = (ms = 200) => new Promise((r) => setTimeout(r, ms));
const now = Math.floor(Date.now() / 1000);
const ON = {
  set_up: true,
  on: true,
  ports: { main: 443 },
  outside: [],
  pending: 0,
  results: [],
  ca: {
    fingerprint: "AB:CD:EF",
    names: ["irate.home.arpa", ".irate.home.arpa", "lyra.local"],
    networks: ["192.168.4.1/32", "192.168.1.0/24"],
  },
  cert: {
    names: ["irate.home.arpa", "lyra.local"],
    addresses: ["192.168.4.1", "192.168.1.181"],
    expires: now + 300 * 86400,
    own: false,
  },
};
function page(file, url, fetcher, scripts) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => {
    if (!/scrollTo|Not implemented/.test(e.message))
      errors.push("jsdom: " + e.message);
  });
  vc.on("error", (...a) => {
    if (!/HTTP 404/.test(a.join(" "))) errors.push("console: " + a.join(" "));
  });
  const dom = new JSDOM(strip(fs.readFileSync(`${WEB}/${file}`, "utf8")), {
    url,
    runScripts: "outside-only",
    virtualConsole: vc,
    pretendToBeVisual: true,
  });
  dom.window.fetch = fetcher;
  dom.window.confirm = () => page.confirm;
  require("./answer-asks.cjs")(dom.window); // its questions on the page answered by its confirm stub
  scripts.forEach((s) =>
    dom.window.eval(fs.readFileSync(`${WEB}/${s}`, "utf8")),
  );
  return { w: dom.window, d: dom.window.document, errors };
}
const json = (b, status = 200) => new Response(JSON.stringify(b), { status });
(async () => {
  // The admin section.
  let state = { set_up: false, pending: 0, results: [] };
  const posted = [];
  const admin = page(
    "admin.html",
    "http://box.local/admin/#security",
    async (u, o = {}) => {
      if (o.method === "POST" && u === "/admin/tls") {
        posted.push(JSON.parse(o.body));
        state = Object.assign({}, ON, {
          results: [{ id: "r1", ok: true, message: "made the box's CA" }],
        });
        return json({ id: "r1" }, 202);
      }
      if (u === "/admin/tls") return json(state);
      return new Response("{}", { status: 404 });
    },
    ["admin-widgets.js", "admin-layout.js", "admin.js"],
  );
  await wait();
  let d = admin.d;
  check(
    "not set up: said, and Make the box's certificate offered",
    /No certificate yet/.test(t(d.getElementById("tls-state"))) &&
      !d.getElementById("tls-make").hidden &&
      d.querySelector('#tls-transport [data-value="both"]').disabled &&
      d.getElementById("tls-again").hidden,
  );
  d.getElementById("tls-make").click();
  await wait(400);
  check(
    "Make: asked of the hub; then on, with what it covers and until when",
    posted[0] &&
      posted[0].action === "make" &&
      /HTTPS enabled: https:\/\/<this box>\/ \(port 443\) and the HTTPS address of each app\. The certificate covers irate\.home\.arpa, lyra\.local, 192\.168\.4\.1, 192\.168\.1\.181\. Certificate expires .* \(the box renews it before then\)\. Certificate-authority fingerprint: AB:CD:EF\./.test(
        t(d.getElementById("tls-state")),
      ) &&
      /made the box's CA/.test(t(d.getElementById("tls-note"))),
    t(d.getElementById("tls-state")),
  );
  check(
    "  then staged transport choices, and a new CA",
    d.querySelectorAll("#tls-transport button").length === 4 &&
      !d.getElementById("tls-again").hidden &&
      d.getElementById("tls-make").hidden,
  );
  page.confirm = false;
  d.getElementById("tls-again").click();
  await wait();
  check("a new CA: not without saying yes", posted.length === 1);
  page.confirm = true;
  d.getElementById("tls-again").click();
  await wait();
  check(
    "  with a yes: asked as a new one",
    posted[1] && posted[1].action === "make" && posted[1].again === true,
  );
  state = Object.assign({}, ON, { outside: ["10.0.0.5"] });
  admin.w.eval("loadTls()");
  await wait();
  check(
    "moved to another subnet: said, as needing a new CA",
    /now at 10\.0\.0\.5\. These addresses are outside the names that its CA can certify\. Make a new certificate authority/.test(
      t(d.getElementById("tls-state")),
    ) && d.getElementById("tls-state").classList.contains("bad"),
  );
  // /admin over HTTPS only.
  state = Object.assign({}, ON);
  admin.w.eval("loadTls()");
  await wait();
  check(
    "HTTPS-only choices are visible but disabled on HTTP, with a reason",
    ["discovery", "https"].every(
      (value) =>
        d.querySelector(`#tls-transport [data-value="${value}"]`).disabled,
    ) &&
      d.querySelector('#tls-admin-http [data-value="block"]').disabled &&
      /Open this page over HTTPS/.test(t(d.getElementById("tls-session-note"))),
  );
  const beforeDraft = posted.length;
  d.querySelector('#tls-admin-http [data-value="warning"]').click();
  await wait();
  check(
    "selecting a policy only stages it",
    posted.length === beforeDraft && !d.querySelector("#tls-foot .aw-save").disabled,
  );
  d.querySelector("#tls-foot .aw-discard").click();
  check(
    "Discard restores applied settings without a request",
    posted.length === beforeDraft &&
      d.querySelector("#tls-foot .aw-save").disabled &&
      d
        .querySelector('#tls-admin-http [data-value="allow"]')
        .getAttribute("aria-checked") === "true",
  );
  // Bring your own.
  check(
    "own certificate: a warning on plain HTTP (the key would cross the network in clear)",
    !d.getElementById("tls-own-plain").hidden,
  );
  const of = d.getElementById("tls-own-form");
  of.elements.chain.value =
    "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----";
  of.elements.key.value =
    "-----BEGIN PRIVATE KEY-----\nBBB\n-----END PRIVATE KEY-----";
  of.dispatchEvent(new admin.w.Event("submit", { cancelable: true }));
  await wait();
  const imp = posted.find((b) => b.action === "import");
  check(
    "  Use it: the chain and the key sent, the key not left in the page",
    imp &&
      /BEGIN CERTIFICATE/.test(imp.chain) &&
      /BEGIN PRIVATE KEY/.test(imp.key) &&
      of.elements.key.value === "",
  );
  state = Object.assign({}, ON, {
    cert: Object.assign({}, ON.cert, {
      names: ["box.example.org"],
      addresses: [],
      own: true,
    }),
  });
  admin.w.eval("loadTls()");
  await wait();
  check(
    "  in use: said as yours, to renew; and the way back to the box's own",
    /\(your own certificate: renew it before then\)/.test(
      t(d.getElementById("tls-state")),
    ) && !d.getElementById("tls-box").hidden,
  );
  d.getElementById("tls-box").click();
  await wait();
  check(
    "  Go back: asked of the hub",
    posted.some((b) => b.action === "box"),
  );
  check(
    "admin: no page errors",
    !admin.errors.length,
    admin.errors.join(" | "),
  );

  let secureState = {
    ...ON,
    transport: "both",
    admin_http: "allow",
    policy: { mode: "hub" },
  };
  const securePosted = [];
  const secure = page(
    "admin.html",
    "https://box.local/admin/#security",
    async (url, opts = {}) => {
      if (url !== "/admin/tls") return new Response("{}", { status: 404 });
      if (opts.method !== "POST") return json(secureState);
      const body = JSON.parse(opts.body);
      securePosted.push(body);
      secureState = {
        ...secureState,
        results: [{ id: "s1", ok: true, message: "Saved" }],
      };
      if (body.action === "settings")
        secureState = {
          ...secureState,
          transport: body.transport,
          admin_http: body.admin_http,
        };
      if (body.action === "policy") secureState.policy = { mode: body.mode };
      return json({ id: "s1" }, 202);
    },
    ["admin-widgets.js", "admin-layout.js", "admin.js"],
  );
  await wait();
  const sd = secure.d;
  check(
    "both security controls are adjacent cards",
    sd.querySelectorAll("#security-cards > .security-setting").length === 2,
  );
  sd.querySelector('#tls-transport [data-value="discovery"]').click();
  check(
    "HTTPS session enables restrictive selection but sends nothing",
    securePosted.length === 0 && !sd.querySelector("#tls-foot .aw-save").disabled,
  );
  sd.querySelector("#tls-foot .aw-save").click();
  await wait();
  check(
    "Save applies the selected transport once",
    securePosted.length === 1 &&
      securePosted[0].transport === "discovery" &&
      securePosted[0].action === "settings" &&
      sd.querySelector("#tls-foot .aw-save").disabled,
  );
  sd.querySelector('#policy-choices [data-value="console"]').click();
  check(
    "root lock is staged, not immediate",
    securePosted.length === 1 && !sd.querySelector("#policy-foot .aw-save").disabled,
  );
  page.confirm = false;
  sd.querySelector("#policy-foot .aw-save").click();
  await wait();
  check(
    "root lock cannot save without confirmation",
    securePosted.length === 1,
  );
  sd.querySelector("#policy-foot .aw-discard").click();
  check(
    "Discard clears the staged root lock",
    sd.querySelector("#policy-foot .aw-save").disabled,
  );
  sd.querySelector('#policy-choices [data-value="console"]').click();
  page.confirm = true;
  sd.querySelector("#policy-foot .aw-save").click();
  await wait();
  check(
    "confirmed lock is saved, then the hub cannot reverse it",
    securePosted.length === 2 &&
      securePosted[1].confirmed === true &&
      [...sd.querySelectorAll("#policy-choices button")].every(
        (button) => button.disabled,
      ) &&
      /console or SSH/.test(t(sd.getElementById("tls-session-note"))),
  );
  check(
    "secure admin: no page errors",
    !secure.errors.length,
    secure.errors.join(" | "),
  );

  // The public page.
  const pub = (info, trusts, url = "http://box.local/certificate.html") =>
    page(
      "certificate.html",
      url,
      async (u) => {
        if (u === "/certificate.json") return json(info);
        if (u.startsWith("https://")) {
          pub.asked.push(u);
          if (typeof trusts === "function" ? trusts() : trusts)
            return new Response("", { status: 200 });
          throw new TypeError("Failed to fetch");
        }
        return new Response("{}", { status: 404 });
      },
      ["certificate.js"],
    );
  pub.asked = [];
  let p = pub({ set_up: false }, false);
  await wait();
  check(
    "/certificate, none yet: said, nothing to download",
    /no certificate yet/.test(t(p.d.getElementById("cert-state"))) &&
      p.d.getElementById("cert-about").hidden,
  );
  const info = {
    set_up: true,
    on: true,
    port: 443,
    fingerprint: "AB:CD:EF",
    names: ["irate.home.arpa", "box.local"],
    addresses: ["192.168.4.1", "192.168.1.26"],
    expires: now + 300 * 86400,
  };
  p = pub(info, false);
  await wait();
  check(
    "/certificate: the download, its fingerprint, what it may vouch for",
    !p.d.getElementById("cert-about").hidden &&
      t(p.d.getElementById("cert-fingerprint")) === "AB:CD:EF" &&
      t(p.d.getElementById("cert-names")) === "irate.home.arpa, box.local" &&
      p.d.getElementById("cert-download").getAttribute("href") ===
        "/certificate/ca.crt",
  );
  const folds = [...p.d.querySelectorAll("#cert-steps details")];
  check(
    "  the steps tried on real phones (iPhone, Android), each folded",
    folds.map((f) => t(f.querySelector("summary"))).join("|") ===
      "iPhone and iPad|Android" &&
      /Install anyway/.test(t(folds[1])) &&
      /Network may be monitored/.test(t(folds[1])) &&
      /Certificate Trust Settings/.test(t(folds[0])),
  );
  check(
    "  the general step for the rest, named, until tried",
    /On other devices \(Windows, macOS, Linux\): download the certificate/.test(
      t(p.d.getElementById("cert-steps")),
    ),
    t(p.d.getElementById("cert-steps")),
  );
  check(
    "  the self-check: not installed yet, and Check again offered",
    /✗ Not installed yet: this device does not trust the box/.test(
      t(p.d.getElementById("cert-check")),
    ) &&
      !p.d.getElementById("cert-again").hidden &&
      p.d.getElementById("cert-open").hidden,
    t(p.d.getElementById("cert-check")),
  );
  check(
    "  never past the warning: the warning is not to be gone past",
    /do not go past it/.test(t(p.d.getElementById("cert-about"))),
  );
  check(
    "  every HTTPS address the certificate covers, as full links to this page",
    [...p.d.querySelectorAll("#cert-links a")].map((a) => a.href).join(" ") ===
      "https://irate.home.arpa/certificate.html https://box.local/certificate.html " +
        "https://192.168.4.1/certificate.html https://192.168.1.26/certificate.html",
    [...p.d.querySelectorAll("#cert-links a")].map((a) => a.href).join(" "),
  );
  // Installed meanwhile: Check again says so, with the full HTTPS address to open.
  let installed = false;
  p = pub(info, () => installed);
  await wait();
  installed = true;
  p.d.getElementById("cert-recheck").click();
  await wait();
  const opened = p.d.querySelector("#cert-open a");
  check(
    "  Check again after installing: installed, and the full HTTPS link offered",
    /✓ The certificate is installed: this device trusts the box\./.test(
      t(p.d.getElementById("cert-check")),
    ) &&
      p.d.getElementById("cert-again").hidden &&
      opened &&
      opened.href === "https://box.local/" &&
      t(p.d.getElementById("cert-open")) === "Open https://box.local/.",
    t(p.d.getElementById("cert-check")) + " | " + t(p.d.getElementById("cert-open")),
  );
  p = pub(info, true, "http://box.local:8080/certificate.html");
  await wait();
  check(
    "  checked at the address the page was opened at",
    pub.asked.at(-1) === "https://box.local/certificate.json",
    pub.asked.at(-1),
  );
  p = pub(info, false, "https://box.local/certificate.html");
  await wait();
  check(
    "  opened over HTTPS: said, with the link",
    /You are on HTTPS\./.test(t(p.d.getElementById("cert-check"))) &&
      p.d.querySelector("#cert-open a").href === "https://box.local/",
  );
  // Opened at an address the certificate does not cover: every browser would warn, so no check; its addresses.
  const asked = pub.asked.length;
  p = pub(info, true, "http://10.0.0.9/certificate.html");
  await wait();
  check(
    "  an address the certificate does not cover: said, the covered addresses offered, nothing checked",
    /does not cover 10\.0\.0\.9/.test(t(p.d.getElementById("cert-check"))) &&
      !p.d.getElementById("cert-elsewhere").hidden &&
      p.d.getElementById("cert-https").href === "https://irate.home.arpa/" &&
      pub.asked.length === asked,
    t(p.d.getElementById("cert-check")),
  );
  check("public: no page errors", !p.errors.length, p.errors.join(" | "));
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
