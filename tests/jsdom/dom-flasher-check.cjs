// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The check the hub puts at the top of the downloaded Meshtastic flasher (hub/flasher.py CHECK): a firmware file
// fetched from the box whose SHA-256 differs from the one the box held when the page was downloaded is refused; the
// same file intact passes; a file with no digest recorded, another address and a failed answer pass as they are.
// The script is read from flasher.py and run with Node's own fetch stand-ins and Web Crypto.
// node tests/jsdom/dom-flasher-check.cjs
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

let fails = 0;
function check(name, cond, info) {
  console.log((cond ? "PASS " : "FAIL ") + name + (cond ? "" : `  ${info ?? ""}`));
  if (!cond) fails++;
}

const src = fs.readFileSync(path.join(__dirname, "../../irate_box/hub/flasher.py"), "utf8");
const template = src.match(/CHECK = """([\s\S]*?)"""/)[1];
const origin = "https://box.example:8443";
const good = Buffer.from("firmware bytes");
const digest = crypto.createHash("sha256").update(good).digest("hex");
const script = template
  .replace("__DIGESTS__", JSON.stringify({ "/flasher/firmware/2.8.2.c39728e/a.bin": digest }))
  .replace("__ORIGIN__", JSON.stringify(origin))
  .replace(/^<script>/, "")
  .replace(/<\/script>\s*$/, "");

// What the box answers: per address, the bytes and the status.
const served = {};
const window = {
  crypto: globalThis.crypto,
  fetch: async (input) => {
    const url = typeof input === "string" ? input : input.url;
    const s = served[url] || { body: Buffer.from(""), status: 404 };
    return new Response(s.body, { status: s.status });
  },
};
new Function("window", "crypto", script)(window, globalThis.crypto);

(async () => {
  const at = `${origin}/flasher/firmware/2.8.2.c39728e/a.bin`;
  served[at] = { body: good, status: 200 };
  const r = await window.fetch(at);
  check("a firmware file as the box held it: passes, its body still readable", r.ok && (await r.text()) === "firmware bytes");
  served[at] = served[at + "?x=1"] = { body: Buffer.from("something else"), status: 200 };
  let said = null;
  try {
    await window.fetch(at + "?x=1");
  } catch (err) {
    said = String(err.message || err);
  }
  check("another file at that address: refused, and said", /Firmware refused: \/flasher\/firmware\/2\.8\.2\.c39728e\/a\.bin/.test(said || ""), said);
  said = null;
  try {
    await window.fetch(new Request(at));
  } catch (err) {
    said = String(err.message || err);
  }
  check("  asked with a Request object too", /Firmware refused/.test(said || ""), said);
  const other = `${origin}/flasher/firmware/2.8.2.c39728e/b.bin`;
  served[other] = { body: Buffer.from("anything"), status: 200 };
  check("a file with no digest recorded: passes as it is", (await window.fetch(other)).ok);
  served["https://elsewhere.example/x"] = { body: Buffer.from("x"), status: 200 };
  check("another address: passes as it is", (await window.fetch("https://elsewhere.example/x")).ok);
  served[at] = { body: Buffer.from("not found"), status: 404 };
  check("a failed answer: passed on unchecked, for the page to say", (await window.fetch(at)).status === 404);
  console.log(`failures: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
