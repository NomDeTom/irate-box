// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Every /admin change carries X-Irate-Admin, which the hub requires (server.py _forged): a page
// elsewhere cannot send it with the owner's cached login.
const ADMIN_HEADERS = {
  "Content-Type": "application/json",
  "X-Irate-Admin": "1",
};
// Admin options. The gate is the web server's basic auth on /admin/* -- by the time this page
// loads, the operator has already authenticated. Each toggle saves on change; there is
// no Save button to forget to press.

// --- the menu --------------------------------------------------------------------------
// Built by admin-layout.js (AL) from its table and the apps' manifests : pages
// holding the sections below, the address naming a section. Everything keeps loading in the
// background, so the sidebar's badges stay current whichever page is open.
// A word beside a section's sidebar entry: "new", "working", ... or nothing.
const badge = AL.badge;
// Is this section on the page that's showing? (The loaders that wait to be seen.)
const paneShown = AL.shown;

// Each pane's data is asked for when the page opens; on a small board two dozen answers at once are slow,
// the shown pane's among them. So GETs go out a few at a time, the shown pane's first, then those no pane
// owns (the page's own settings), then the rest; the others go straight out. PANE_OF: an address's panes.
const PANE_OF = {
  "/admin/network": ["network"],
  "/admin/hotspot": ["network"],
  "/admin/health": ["health", "clock"],
  "/admin/update": ["updates", "updoctor"],
  "/admin/packages": ["updates"],
  "/admin/security": ["security", "secdoctor"],
  "/admin/tls": ["security"],
  "/admin/library": ["books", "sources"],
  "/admin/books": ["books"],
  "/admin/moderation": ["moderation", "shoutbox-mod", "board-mod", "drop-mod"],
  "/admin/store": ["saved", "made"],
  "/admin/kit": ["backup"],
  "/admin/backup/plan": ["backup"],
  "/admin/kits": ["toolkits"],
  "/admin/tiles": ["tile-order"],
  "/admin/access": ["apps"],
  "/admin/addons": ["addons"],
  "/admin/local-addons": ["addons"],
  "/admin/usb": ["books", "toolkits", "backup"],
  "/admin/git": ["git"],
  "/admin/ci": ["git"],
  "/admin/firmware": ["firmware", "factory"],
  "/admin/folders": ["mirrors"],
  "/admin/status-tiles": ["status-tiles"],
  "/admin/tailscale": ["access"],
  "/admin/mesh": ["mesh"],
  "/admin/accounts": ["accounts"],
};
const GETS_AT_ONCE = 3;
(() => {
  const send = window.fetch.bind(window);
  const queue = [];
  let out = 0;
  const rank = (url) => {
    const panes = PANE_OF[String(url).split("?")[0]];
    return !panes
      ? 1
      : panes.some((pane) => document.getElementById(pane) && paneShown(pane))
        ? 0
        : 2;
  };
  const next = () => {
    while (out < GETS_AT_ONCE && queue.length) {
      queue.sort((left, right) => rank(left.url) - rank(right.url) || left.n - right.n); // ranked as they leave: the pane shown then
      const job = queue.shift();
      out += 1;
      send(job.url, job.opts)
        .then(job.resolve, job.reject)
        .finally(() => {
          out -= 1;
          next();
        });
    }
  };
  let n = 0;
  window.fetch = (url, opts = {}) => {
    if (
      (opts.method || "GET").toUpperCase() !== "GET" ||
      !String(url).startsWith("/admin/")
    )
      return send(url, opts);
    // Sent after the script that asked has run on: the page's opening burst is all queued, and ranked, first.
    return new Promise((resolve, reject) => {
      queue.push({ url, opts, resolve, reject, n: n++ });
      queueMicrotask(next);
    });
  };
})();

// Every answer goes in the note right under the control that asked for it.
const noteEl = (id) => document.getElementById(id);
function say(text, ok, at) {
  at.textContent = text;
  at.classList.toggle("bad", !ok);
  at.hidden = !text;
}

// The switches in a .settings group (delete at boot, the factory tile, visitor counts): a section per group, held until
// Save; each group names the note its answers go in (data-note). The delete-at-boot ones say when they apply.
const settingGroups = [...document.querySelectorAll(".settings")]
  .filter((g) => g.querySelector('input[type="checkbox"]'))
  .map((g) => {
    const boxes = [...g.querySelectorAll('input[type="checkbox"]')];
    const sec = AW.section({
      id: `switches-${boxes.map((b) => b.id).join("-")}`,
      label: boxes
        .map((b) => {
          const n = b.parentElement.querySelector(".setting-name");
          return n ? n.textContent.trim() : b.id;
        })
        .join(", "),
      saved: null,
      save: async (c) => {
        const data = await postJSON("/admin/settings", c);
        // What the hub stored, not what was sent: they differ if it rejected the value.
        setTimeout(() =>
          sec.update(
            Object.fromEntries(
              boxes
                .filter((b) => typeof data[b.id] === "boolean")
                .map((b) => [b.id, data[b.id]]),
            ),
          ),
        );
        return g.dataset.atBoot !== undefined
          ? "Saved. It takes effect at the next boot."
          : "Saved.";
      },
      onChange: () =>
        boxes.forEach((b) => {
          b.checked = !!sec.get(b.id);
          b.disabled = sec.disabled();
        }),
    });
    boxes.forEach((b) => {
      b.disabled = true;
      b.addEventListener("change", () => sec.set(b.id, b.checked, true));
    });
    g.after(sec.foot, sec.reason);
    return { boxes, sec };
  });
const settingNote = (box) => noteEl(box.closest(".settings").dataset.note);

async function load() {
  try {
    const response = await fetch("/admin/settings");
    if (!response.ok) throw new Error(response.status);
    const data = await response.json();
    for (const { boxes, sec } of settingGroups)
      sec.update(
        Object.fromEntries(
          boxes
            .filter((b) => typeof data[b.id] === "boolean")
            .map((b) => [b.id, data[b.id]]),
        ),
      );
    applySetup(data.setup_done === true);
  } catch (_) {
    if (settingGroups.length)
      say(
        "Could not read the current settings.",
        false,
        settingNote(settingGroups[0].boxes[0]),
      );
  }
}
load();

// --- remote access --------------------------------------------------------------
// The hub only records the choice; a root path unit acts on it a moment later, so the
// state line is re-read a few times after each change rather than assumed.
const remote = document.getElementById("remote-section");
const radios = remote.querySelectorAll('input[name="remote"]');
const hours = document.getElementById("remote-hours");
const remoteState = document.getElementById("remote-state");
const remoteNote = noteEl("remote-note");
const STATE_TEXT = {
  running: "Tailscale is running.",
  stopped: "Tailscale is stopped.",
};
// Running is not the same as reachable: what Tailscale itself says while it runs.
function remoteText(data) {
  const b = data.backend;
  if (data.state !== "running" || !b) return STATE_TEXT[data.state] || "";
  if (b.state === "connected")
    return `Tailscale is running and connected. This box is ${b.name || "on your Tailscale network"}${b.tailnet ? ` on ${b.tailnet}` : ""}${b.ip ? ` (${b.ip})` : ""}.`;
  if (b.state === "needs-login")
    return (
      "Tailscale is running, but this box is not signed in to a Tailscale network. Nothing can reach it " +
      "through Tailscale. To sign in, run sudo tailscale up in Terminal. Open the URL shown by the command. " +
      "Sign in to your Tailscale account. Or disable remote access here."
    );
  return `Tailscale is running. Connection status: ${b.state}.`;
}

function showRemote(data) {
  remote.hidden = data.state === "missing";
  // The setup tour's Tailscale step shows only where the box has it.
  if (window.TOUR) TOUR.has("tailscale", !remote.hidden);
  if (remote.hidden) return;
  radios.forEach((radio) => {
    radio.checked = radio.value === data.want.mode;
  });
  if (data.want.mode === "timed") hours.value = data.want.hours;
  hours.max = data.max_hours;
  remoteState.textContent = remoteText(data);
}

async function loadRemote() {
  try {
    const response = await fetch("/admin/tailscale");
    if (!response.ok) throw new Error(response.status);
    showRemote(await response.json());
  } catch (_) {
    remote.hidden = true;
  }
}

async function saveRemote() {
  const mode = [...radios].find((radio) => radio.checked)?.value;
  if (!mode) return;
  const body = { mode };
  if (mode === "timed") body.hours = Number(hours.value);
  try {
    const response = await fetch("/admin/tailscale", {
      method: "POST",
      headers: ADMIN_HEADERS,
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(response.status);
    showRemote(await response.json());
    say("Saved. Tailscale changes within a few seconds.", true, remoteNote);
    [2000, 5000, 10000].forEach((ms) => setTimeout(loadRemote, ms));
  } catch (_) {
    say("Could not change remote access.", false, remoteNote);
    loadRemote();
  }
}

radios.forEach((radio) => radio.addEventListener("change", saveRemote));
hours.addEventListener("change", () => {
  const timed = [...radios].find((radio) => radio.value === "timed");
  timed.checked = true;
  saveRemote();
});
loadRemote();

// --- library ---------------------------------------------------------------------
// The librarian (librarian.py) does the work; this page shows its snapshot and posts
// actions. Checks and downloads run in the background on the hub, so while one is going
// the page re-reads the snapshot every few seconds.
const lib = {
  states: document.querySelectorAll(".library-state"),
  bars: document.querySelectorAll(".library-bar"),
  allNote: document.getElementById("library-all-note"),
  sources: document.getElementById("library-sources"),
  add: document.getElementById("library-add"),
  policy: document.getElementById("library-policy"),
  token: document.getElementById("library-token"),
  tokenState: document.getElementById("library-token-state"),
  typeHelp: document.getElementById("library-type-help"),
};
const TYPE_HELP = {
  release:
    "The newest release that has a matching asset. No token is needed. Use this when the project publishes its own ZIM file.",
  actions:
    "The artifact of the newest successful run. A GitHub token is required to download Actions artifacts. Artifacts expire after a period set in the repository. The workflow must run more often than that period.",
  "nightly-link":
    "The same artifacts, downloaded through the third-party nightly.link service. No token is needed.",
  url: "A direct link to a .zim file, or to a .zip file that holds one. The book updates when its ETag or Last-Modified header changes.",
};
const PRESETS = {
  release: {
    type: "release",
    name: "project-docs",
    repo: "owner/project",
    pattern: "*.zim",
  },
  actions: {
    type: "actions",
    name: "mermaid-docs",
    repo: "NomDeTom/mermaid",
    workflow: "docs-zim.yml",
    pattern: "mermaid-docs-*",
  },
  "nightly-link": {
    type: "nightly-link",
    name: "meshtastic-docs",
    repo: "NomDeTom/meshtasticDocs",
    workflow: "docs-zim.yml",
    pattern: "meshtastic-docs-*",
  },
};
let libPoll = null;

const el = (tag, props = {}, ...kids) => {
  // A button with no class of its own is an action button (rule 6a: the control vocabulary).
  if (tag === "button" && !props.className)
    props = { ...props, className: "action-btn" };
  const { dataset, ...rest } = props;
  const node = Object.assign(document.createElement(tag), rest);
  if (dataset) Object.assign(node.dataset, dataset);
  node.append(...kids.filter((k) => k !== null && k !== undefined));
  return node;
};
const mb = (bytes) => (bytes ? `${Math.round(bytes / 2 ** 20)} MB` : "");
const where = (source) =>
  source.type === "kiwix"
    ? `Kiwix's catalogue, ${source.kiwix_name}${source.flavour ? ` (${source.flavour})` : ""}`
    : source.type === "url"
      ? source.url
      : `${source.repo}${source.workflow ? ` · ${source.workflow}` : ""}${source.branch ? ` @ ${source.branch}` : ""} · ${source.pattern}`;

// A refusal from the hub as an Error: error is its English; message, when the hub sends one, the same refusal
// as a message (irate_box/hub/refused.py), said in the reader's language.
function hubError(data, status) {
  const m =
    data && data.message && typeof data.message === "object"
      ? data.message
      : null;
  return Object.assign(
    new Error(m ? AW.say(m) : (data && data.error) || `HTTP ${status}`),
    { status },
  );
}

// A result of the root helper in the reader's language: its message (msg.py), or its messages, where it has
// them, else its English.
function resultSaid(result) {
  if (Array.isArray(result.msg)) return result.msg.map((m) => AW.say(m)).join(" ");
  return result.msg ? AW.say(result.msg) : result.message;
}
// A progress step's label: a message from the root helper, said; text as it is.
const said = (v) => (v && typeof v === "object" ? AW.say(v) : v || "");
// A status's failure: error_message, where the hub sends one, said; else error, its English.
const errorSaid = (st) =>
  st.error_message ? AW.say(st.error_message) : st.error;

async function libPost(body) {
  const response = await fetch("/admin/library", {
    method: "POST",
    headers: ADMIN_HEADERS,
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw hubError(data, response.status);
  return data;
}

// What a library button's action answered goes right under that button, not in the banner
// at the top. "Already running" (409) stays until the librarian is free, then goes.
let libNote = null; // { key, text, busy }

async function libAct(key, body) {
  libNote = null;
  try {
    renderLibrary(await libPost(body));
  } catch (error) {
    libNote =
      error.status === 409
        ? {
            key,
            busy: true,
            text: "The librarian is already running. The buttons return when it is free. Try again then.",
          }
        : { key, busy: false, text: error.message };
    loadLibrary();
  }
}

function noteFor(key) {
  if (!libNote || libNote.key !== key) return null;
  return el("span", {
    className: `setting-desc action-note ${libNote.busy ? "busy" : "bad"}`,
    role: "status",
    textContent: libNote.text,
  });
}

function sourceRow(src, st, busy) {
  const cur = st.current || {};
  const latest = st.latest || {};
  const archive = st.archive || [];
  const key = `book:${src.name}`;
  const button = (label, body, opts = {}) =>
    el("button", {
      type: "button",
      className: "action-btn",
      textContent: label,
      disabled: busy || !!opts.off,
      title: opts.title || "",
      onclick: () => {
        libAct(key, body);
      },
    });
  // Check finds a newer version; Download downloads and checks it beside the book in use;
  // Update swaps it in (fetching first if that has not happened).
  const newer = !!latest.version && latest.version !== cur.version;
  const fetched =
    st.fetched && st.fetched.version === latest.version ? st.fetched : null;
  return el(
    "div",
    { className: "setting library-source" },
    el(
      "span",
      {},
      el(
        "span",
        { className: "setting-name" },
        `${src.name}.zim `,
        AW.updatePill(st.last_check, newer, !!st.error),
      ),
      el("span", {
        className: "setting-desc",
        textContent: `${src.type}: ${where(src)}`,
      }),
      el("span", {
        className: "setting-desc",
        textContent: cur.version
          ? `Installed: ${cur.label || cur.version} (${mb(cur.size)}, ${cur.installed})`
          : "The librarian has not installed it yet.",
      }),
      newer
        ? el("span", {
            className: "setting-desc",
            textContent: fetched
              ? `Downloaded: ${fetched.label} (${mb(fetched.size)}), ready to install.`
              : `Available: ${latest.label} (${mb(latest.size)})`,
          })
        : null,
      archive.length
        ? el("span", {
            className: "setting-desc",
            textContent: `Retained versions: ${archive.join(", ")}`,
          })
        : null,
      st.last_check
        ? el("span", {
            className: "setting-desc",
            textContent: `Checked ${st.last_check}: ${st.outcome || ""}`,
          })
        : null,
      st.error
        ? el("span", {
            className: "setting-desc bad",
            textContent: errorSaid(st),
          })
        : null,
      el(
        "span",
        { className: "library-buttons" },
        button("Check now", { action: "check", names: [src.name] }),
        button("Download", { action: "fetch", names: [src.name] }, {
          off: !newer || fetched,
          title: !newer
            ? "Select Check now first. No newer version is known."
            : fetched
              ? "Already downloaded"
              : "",
        }),
        button("Install", { action: "update", names: [src.name] }),
        archive.length
          ? button(
              "Roll back",
              { action: "rollback", name: src.name },
              {
                title: `Restores version ${archive[0]} as ${src.name}.zim. The librarian keeps the current version.`,
              },
            )
          : null,
        button(
          "Stop tracking",
          { action: "remove", name: src.name },
          { title: `The book stays in the library.` },
        ),
      ),
      noteFor(key),
    ),
  );
}

let lastLibrary = null; // drawn again when the menu is rebuilt, for the apps' own pages
AL.onBuild(() => {
  if (lastLibrary) renderApps(lastLibrary, lastLibrary.running);
});
function renderApps(snap, busy) {
  const apps = snap.apps || {};
  const sources = Object.fromEntries(
    snap.sources.filter((source) => source.kind === "app").map((source) => [source.name, source]),
  );
  const post = (key, body) => () => libAct(key, body);
  const row = (name, app, withAccess) => {
    const inst = app.installed;
    const src = sources[name];
    const st = snap.status[name] || {};
    const res = st.install_result;
    // A pinned app (librarian.py, "Latest and pinned") installs the build its source follows.
    const follow = app.pin
      ? (src && src.follow) || st.follow || "pinned"
      : "latest";
    const target = follow === "pinned" ? st.pinned : st.latest;
    const newer =
      !!(target && st.current) && target.version !== st.current.version;
    const fetched =
      newer && st.fetched && st.fetched.version === target.version
        ? st.fetched
        : null;
    const lines = [
      inst === null
        ? "Not installed."
        : inst.commit
          ? `Installed: ${inst.commit.slice(0, 7)} from ${inst.repository} (${inst.ref}), built ${String(inst.built).slice(0, 10)}.`
          : "Installed by hand, with no bundle record. The first update replaces it.",
      src
        ? src.type === "git"
          ? `Source: git, ${src.repo} @ ${src.branch}, adapted for the hub.`
          : `Source: ${src.type}, ${src.repo} · ${src.workflow}${src.branch ? ` @ ${src.branch}` : ""}.`
        : "No update source is set for this app.",
      app.pin
        ? `Pinned version (last known good): ${app.pin.slice(0, 7)}. Newest: ${st.latest ? st.latest.label : "not checked yet"}. ` +
          (follow === "pinned"
            ? "Updates use the pinned version."
            : "Updates use the newest version. If it fails, the app keeps the installed version or the pinned version.")
        : null,
      st.latest_failed && follow === "latest"
        ? `The newest (${st.latest_failed.label}) failed: ${st.latest_failed.error}`
        : null,
      st.last_check ? `Checked ${st.last_check}: ${st.outcome || ""}` : null,
      fetched
        ? `Downloaded: ${fetched.commit} (${fetched.label}), ready to install.`
        : null,
      res
        ? `${res.ok ? "Installed" : "Install failed"}: ${resultSaid(res)}`
        : null,
      st.error ? errorSaid(st) : null,
    ];
    return el(
      "div",
      { className: "setting library-source" },
      el(
        "span",
        {},
        // Only an app with a source is checked for updates; one with none is said so below.
        el(
          "span",
          { className: "setting-name" },
          app.title,
          src ? " " : null,
          src ? AW.updatePill(st.last_check, newer, !!st.error) : null,
        ),
        withAccess ? accessSlot(name) : null,
        ...lines.filter(Boolean).map((t) =>
          el("span", {
            className: `setting-desc${t === (st.error && errorSaid(st)) || (res && !res.ok && t.startsWith("Install failed")) ? " bad" : ""}`,
            textContent: t,
          }),
        ),
        src
          ? el(
              "span",
              { className: "library-buttons" },
              el("button", {
                type: "button",
                className: "action-btn",
                textContent: "Check now",
                disabled: busy,
                onclick: post(`app:${name}`, {
                  action: "check",
                  names: [name],
                }),
              }),
              el("button", {
                type: "button",
                className: "action-btn",
                textContent: "Download",
                disabled: busy || !newer || !!fetched,
                title: !newer
                  ? "Select Check now first. No newer version is known."
                  : fetched
                    ? "Already downloaded"
                    : "",
                onclick: post(`app:${name}`, {
                  action: "fetch",
                  names: [name],
                }),
              }),
              el("button", {
                type: "button",
                className: "action-btn",
                textContent: "Install",
                disabled: busy,
                onclick: post(`app:${name}`, {
                  action: "update",
                  names: [name],
                }),
              }),
              inst && inst.has_previous
                ? el("button", {
                    type: "button",
                    className: "action-btn",
                    textContent: "Roll back",
                    disabled: busy,
                    title: `Restores the previous ${app.title} build.`,
                    onclick: post(`app:${name}`, { action: "rollback", name }),
                  })
                : null,
              app.pin
                ? el("button", {
                    type: "button",
                    className: "action-btn",
                    textContent:
                      follow === "pinned"
                        ? "Use latest version"
                        : "Use pinned version",
                    disabled: busy,
                    title:
                      follow === "pinned"
                        ? `Future ${app.title} updates will use the latest build instead of the reviewed pinned commit. Nobody may have reviewed the latest build.`
                        : "",
                    onclick: post(`app:${name}`, {
                      action: "add",
                      source: {
                        ...src,
                        follow: follow === "pinned" ? "latest" : "pinned",
                      },
                    }),
                  })
                : null,
            )
          : el(
              "span",
              { className: "library-buttons" },
              el("button", {
                type: "button",
                className: "action-btn",
                textContent: "Track app updates",
                disabled: busy,
                title: `Track the published builds of ${app.title}. Then Check now, Download and Install work for it.`,
                onclick: post(`app:${name}`, {
                  action: "add-apps",
                  names: [name],
                }),
              }),
            ),
        noteFor(`app:${name}`),
      ),
    );
  };
  // Each app's row is on its own page; All apps keeps only one that has no page to hold it.
  const paged = new Set(
    [...document.querySelectorAll(".updates-block[data-app]")].map(
      (b) => b.dataset.app,
    ),
  );
  document.getElementById("apps-list").replaceChildren(
    ...Object.entries(apps)
      .filter(([name]) => !paged.has(name))
      .map(([name, app]) => row(name, app, true)),
  );
  // Each app's own page has its row too, its access being at the top of that page already.
  document.querySelectorAll(".updates-block[data-app]").forEach((b) => {
    const app = apps[b.dataset.app];
    b.replaceChildren(
      app
        ? row(b.dataset.app, app, false)
        : el("p", {
            className: "setting-desc",
            textContent: "The librarian has no data about this app yet.",
          }),
    );
  });
  lastLibrary = snap;
  renderInventory();
}

// The other update policies the librarian runs, as saved, each with a link to the one place it is changed.
function drawLibraryOthers(pol) {
  const box = document.getElementById("library-others");
  if (!box) return;
  const hh = (h) => `${String(h).padStart(2, "0")}:00`;
  const fetchOnly = (often, act, what) =>
    Number(often)
      ? `${AW.patternSays(often, 0).replace(/\. .*$/, "")}. ${Number(act) ? `Downloads ${what}` : "Flags newer versions"}.`
      : AW.patternSays(0);
  const rows = [
    [
      "This hub",
      AW.patternSays(pol.hub_check_every_hours, pol.hub_auto).replace(
        /\.$/,
        "",
      ) +
        (Number(pol.hub_check_every_hours) && Number(pol.hub_auto) === 2
          ? `, between ${hh(pol.hub_window_start)} and ${hh(pol.hub_window_end)}.`
          : "."),
      "update-hub-card",
      "Updates",
    ],
    [
      "Git mirrors",
      fetchOnly(
        pol.mirrors_every_hours,
        pol.mirrors_auto,
        "what is newer (downloading is the update)",
      ),
      "mirrors",
      "Mirrors",
    ],
    [
      "Firmware mirror",
      fetchOnly(pol.firmware_every_hours, pol.firmware_auto, "new releases"),
      "firmware",
      "Firmware",
    ],
    [
      "Toolkits' cache",
      Number(pol.kits_every_hours)
        ? `${AW.patternSays(pol.kits_every_hours, 0).replace(/; .*$/, "")}; refreshes the cache (installs nothing).`
        : AW.patternSays(0),
      "toolkits",
      "Toolkits",
    ],
    [
      "Debian's security updates",
      "Set with the box's scan.",
      "updates-debian",
      "Updates",
    ],
  ];
  const key = JSON.stringify(rows);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(
    ...rows.flatMap(([k, v, to, where]) => [
      el("dt", { textContent: k }),
      el(
        "dd",
        {},
        `${v} `,
        el("a", { href: `#${to}`, textContent: `Change on ${where} →` }),
      ),
    ]),
  );
}

// Adapting to GitHub's allowance: attached once, so one press is one request however often the page redraws.
document.getElementById("library-adapt").addEventListener("click", async () => {
  const at = noteEl("library-token-note");
  try {
    renderLibrary(await libPost({ action: "adapt-rate" }));
    say("Adapted. GitHub reported its request limit.", true, at);
  } catch (err) {
    say(err.message, false, at);
  }
});
document
  .getElementById("library-adapt-off")
  .addEventListener("click", async () => {
    const at = noteEl("library-token-note");
    try {
      renderLibrary(await libPost({ action: "adapt-rate-off" }));
      say("No longer adapting.", true, at);
    } catch (err) {
      say(err.message, false, at);
    }
  });

function renderLibrary(snap) {
  const busy = snap.running;
  drawSuggested(snap.sources);
  if (libNote && libNote.busy && !busy) libNote = null;
  renderApps(snap, busy);
  const job = snap.job || {};
  const parts = [];
  const progress = snap.progress;
  if (busy && progress) {
    const rate = progress.seconds > 0 ? progress.done / progress.seconds : 0;
    const left =
      rate && progress.total > progress.done ? Math.round((progress.total - progress.done) / rate) : null;
    parts.push(
      `Downloading ${progress.name}: ${mb(progress.done) || "0 MB"}${progress.total ? ` of ${mb(progress.total)} (${Math.round((100 * progress.done) / progress.total)}%)` : ""}` +
        (rate ? `, ${Math.round(rate / 1024)} KB/s` : "") +
        (left !== null
          ? `, about ${left < 90 ? `${left} s` : `${Math.round(left / 60)} min`} left`
          : "") +
        ".",
    );
  } else if (busy) parts.push(`Working (${job.action || "scheduled check"})…`);
  // The bar: a download's bytes when its size is known, otherwise the indeterminate stripe.
  lib.bars.forEach((bar) => {
    bar.hidden = !busy;
    if (busy && progress && progress.total) {
      bar.max = progress.total;
      bar.value = Math.min(progress.done, progress.total);
    } else bar.removeAttribute("value");
  });
  badge("apps", busy ? "working" : "");
  badge("books", busy ? "working" : "");
  if (!busy) {
    // When the librarian last looked (its sources' newest check), and when it looks next by the schedule.
    const looked = Object.values(snap.status || {})
      .map((status) => Date.parse(status && status.last_check))
      .filter((n) => !Number.isNaN(n));
    const every = Number((snap.policy || {}).check_every_hours) || 0;
    if (looked.length) {
      const last = Math.max(...looked),
        at = (ms) =>
          new Date(ms).toLocaleString([], {
            weekday: "short",
            hour: "2-digit",
            minute: "2-digit",
          });
      parts.push(
        `Idle. Last checked ${at(last)}.${every ? ` Next check about ${at(Math.max(last + every * 3600e3, Date.now()))}.` : " It checks only when you ask."}`,
      );
    } else
      parts.push(
        every ? "Idle. Not checked yet." : "Idle. It checks only when you ask.",
      );
  }
  if (snap.free_mb !== null && snap.free_mb !== undefined)
    parts.push(`${snap.free_mb} MB free on the card.`);
  if (!busy && job.result && job.result.error)
    parts.push(`Last action failed: ${hubError(job.result).message}`);
  lib.states.forEach((n) => {
    n.textContent = parts.join(" ");
  });

  // The books themselves are a page at a time (loadBooks): reread when the librarian finishes.
  if (libWasBusy && !busy) loadBooks();
  libWasBusy = busy;
  libBusy = busy;
  document.querySelectorAll("[data-all], [data-mirrors-all]").forEach((b) => {
    b.disabled = busy;
  });
  document.querySelectorAll("[data-bulk]").forEach((b) => {
    b.disabled = busy;
  });
  const allNote = noteFor("all");
  lib.allNote.hidden = !allNote;
  if (allNote) {
    lib.allNote.textContent = allNote.textContent;
    lib.allNote.className = allNote.className;
  }

  libPolicy.fill(snap.policy);
  fillSurfaces(snap.policy);
  // GitHub's hourly allowance, as the last check saw it (librarian.py: rate): low, the scheduled
  // checks wait for the next hour rather than fail.
  const gh = snap.github || {};
  const resets = gh.reset
    ? new Date(gh.reset * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
  lib.tokenState.textContent =
    (snap.token_set ? "A token is set." : "No token is set.") +
    (gh.limit && gh.reset * 1000 > Date.now()
      ? ` GitHub: ${gh.remaining} of ${gh.limit} requests left this hour, until ${resets}.` +
        (gh.remaining < 10
          ? " Scheduled checks resume at the next hourly cycle."
          : "")
      : "");

  const pc = snap.pace || {};
  const paceEl = document.getElementById("library-pace-state");
  if (paceEl)
    paceEl.textContent = pc.budget
      ? `Adapted. The budget is ${pc.budget} requests an hour (the GitHub limit is ${pc.limit}). ${pc.left} requests are left this hour.${pc.backed_off ? " The librarian has slowed down after GitHub refused requests." : ""}`
      : "Not adapted. The librarian sends requests at its own rate. It stops only when GitHub reports that the limit is nearly reached.";
  document.getElementById("library-adapt-off").hidden = !pc.budget;
  const access = document.getElementById("library-access-says");
  if (access)
    access.textContent = `(${snap.token_set ? "a token is set" : "no token"}, ${pc.budget ? "adapting" : "not adapting"})`;
  drawLibraryOthers(snap.policy || {});

  clearTimeout(libPoll);
  // Also while an app the librarian fetched is still with the root helper.
  const installing = Object.values(snap.status).some(
    (state) => state.pending && !state.install_result,
  );
  if (busy || installing) libPoll = setTimeout(loadLibrary, 3000);
}

async function loadLibrary() {
  try {
    const response = await fetch("/admin/library?books=0");
    if (!response.ok) throw new Error(response.status);
    renderLibrary(await response.json());
  } catch (_) {
    lib.states.forEach((n) => {
      n.textContent = "Could not read the library settings.";
    });
  }
}

function showTypeFields() {
  const type = lib.add.elements.type.value;
  lib.add.querySelectorAll("[data-for]").forEach((label) => {
    label.hidden = !label.dataset.for.split(" ").includes(type);
  });
  lib.typeHelp.textContent = TYPE_HELP[type];
}

lib.add.elements.type.addEventListener("change", showTypeFields);
document.querySelectorAll("[data-preset]").forEach((b) =>
  b.addEventListener("click", () => {
    lib.add.reset();
    for (const [k, v] of Object.entries(PRESETS[b.dataset.preset]))
      lib.add.elements[k].value = v;
    showTypeFields();
  }),
);
lib.add.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fields = lib.add.elements;
  const source = {
    name: fields.name.value.trim(),
    type: fields.type.value,
    prerelease: fields.prerelease.checked,
  };
  for (const k of ["repo", "workflow", "branch", "pattern", "url"]) {
    if (!fields[k].closest("label").hidden && fields[k].value.trim())
      source[k] = fields[k].value.trim();
  }
  try {
    renderLibrary(await libPost({ action: "add", source }));
    say(
      `Added ${source.name}. Use Check now, then Download or Install.`,
      true,
      noteEl("library-add-note"),
    );
    lib.add.reset();
    showTypeFields();
  } catch (err) {
    say(
      `Could not add the source: ${err.message}`,
      false,
      noteEl("library-add-note"),
    );
  }
});
// The librarian's policy, and the mirrors', the firmware mirror's and the toolkits' cache's own pair in it: each
// a section (AW.form). The poll gives the saved policy; a Save sends the changes.
const libPolicySave = async (changes) => {
  const body = { action: "policy" };
  for (const [k, v] of Object.entries(changes)) body[k] = Number(v);
  renderLibrary(await libPost(body));
};
const libPolicy = AW.form(lib.policy, {
  label: "Library policy",
  save: async (c) => {
    await libPolicySave(c);
    return "Saved.";
  },
});
const surfaceForms = [...document.querySelectorAll("form.surface-policy")].map(
  (form) =>
    AW.form(form, {
      label: "Update policy",
      save: async (c) => {
        await libPolicySave(c);
        return "Saved. The librarian applies it at the next hourly cycle.";
      },
    }),
);
function fillSurfaces(policy) {
  for (const sec of surfaceForms) {
    const names = [
      ...sec.foot.closest("form").querySelectorAll("select[name]"),
    ].map((x) => x.name);
    sec.fill(
      Object.fromEntries(
        names.filter((n) => policy[n] != null).map((n) => [n, policy[n]]),
      ),
    );
  }
}
document
  .querySelectorAll("[data-mirrors-all]")
  .forEach((b) =>
    b.addEventListener("click", () =>
      libAct("all", { action: b.dataset.mirrorsAll, names: ["mirrors"] }),
    ),
  );
// The token is never read back: its field starts empty, and empties again once saved.
const libToken = AW.form(lib.token, {
  label: "GitHub token",
  save: async (c) => {
    renderLibrary(await libPost({ action: "token", value: c.value.trim() }));
    setTimeout(() => libToken.fill({ value: "" }));
    return "Token saved.";
  },
});
libToken.fill({ value: "" });
document
  .getElementById("library-token-clear")
  .addEventListener("click", async () => {
    const at = noteEl("library-token-note");
    try {
      renderLibrary(await libPost({ action: "token", value: "" }));
      say("Token cleared.", true, at);
    } catch (err) {
      say(err.message, false, at);
    }
  });
document
  .querySelectorAll("[data-all]")
  .forEach((b) =>
    b.addEventListener("click", () => libAct("all", { action: b.dataset.all })),
  );

// --- the books, a page at a time (/admin/books) -------------------------------------------------
let libWasBusy = false;
let libBusy = false;
const bk = {
  q: document.getElementById("books-q"),
  language: document.getElementById("books-language"),
  state: document.getElementById("books-state"),
  kept: document.getElementById("books-kept"),
  sort: document.getElementById("books-sort"),
  body: document.querySelector("#books-table tbody"),
  pageAll: document.getElementById("books-page-all"),
  summary: document.getElementById("books-summary"),
  page: document.getElementById("books-page"),
  prev: document.getElementById("books-prev"),
  next: document.getElementById("books-next"),
  bulk: document.getElementById("books-bulk"),
  selected: document.getElementById("books-selected"),
  matching: document.getElementById("books-select-matching"),
  none: document.getElementById("books-select-none"),
};
const bookSel = new Set();
let bookPage = 1;
let bookData = null;
let bookOpen = null;
let bookTimer = null;
const STATE_WORDS = {
  ok: "up to date",
  newer: "newer available",
  failed: "check failed",
  "not installed": "not installed yet",
  unreadable: "Kiwix cannot read it",
};
const bookQuery = (extra = {}) =>
  new URLSearchParams({
    q: bk.q.value.trim(),
    language: bk.language.value,
    state: bk.state.value,
    kept: bk.kept.value,
    sort: bk.sort.value,
    page: String(bookPage),
    ...extra,
  }).toString();

async function loadBooks() {
  try {
    renderBooks(await getJSON(`/admin/books?${bookQuery()}`));
  } catch (_) {
    bk.summary.textContent = "Could not read the books.";
  }
}

function renderBooks(data) {
  bookData = data;
  bookPage = data.page;
  const sm = data.summary;
  setupStep(
    "books",
    sm.kept
      ? `${sm.kept} kept current by the librarian.`
      : "No books are tracked yet. Kiwix serves only books that were copied in by hand.",
    sm.kept ? "ok" : "warn",
  );
  const st = sm.states;
  bk.summary.textContent =
    `${sm.count} book${sm.count === 1 ? "" : "s"}, ${size(sm.bytes)}` +
    (sm.budget_mb
      ? ` of a ${size(sm.budget_mb * 2 ** 20)} budget (${Math.round((100 * sm.bytes) / (sm.budget_mb * 2 ** 20))} %)`
      : "") +
    `. ${sm.kept} kept current.` +
    ["newer", "failed", "unreadable", "not installed"]
      .filter((k) => st[k])
      .map((k) => ` ${st[k]} ${STATE_WORDS[k]}.`)
      .join("");
  const langs = Object.keys(sm.languages).sort();
  if (bk.language.dataset.langs !== langs.join(",")) {
    const was = bk.language.value;
    bk.language.dataset.langs = langs.join(",");
    bk.language.replaceChildren(
      el("option", { value: "", textContent: "any" }),
      ...langs.map((l) =>
        el("option", { value: l, textContent: `${l} (${sm.languages[l]})` }),
      ),
    );
    bk.language.value = langs.includes(was) ? was : "";
  }
  const rows = [];
  for (const b of data.books) {
    const box = el("input", {
      type: "checkbox",
      checked: bookSel.has(b.name),
      ariaLabel: `Select ${b.title}`,
      onchange: (event) => {
        if (event.target.checked) bookSel.add(b.name);
        else bookSel.delete(b.name);
        renderBookBulk();
      },
    });
    const open = bookOpen === b.name;
    rows.push(
      el(
        "tr",
        { className: `book-row state-${b.state.replace(" ", "-")}` },
        el("td", {}, box),
        el(
          "td",
          {},
          el("button", {
            type: "button",
            className: "link-button",
            textContent: b.title,
            ariaExpanded: String(open),
            onclick: () => {
              bookOpen = open ? null : b.name;
              renderBooks(bookData);
            },
          }),
          b.title !== b.name
            ? el("span", {
                className: "setting-desc",
                textContent: ` ${b.name}.zim`,
              })
            : null,
        ),
        el("td", { textContent: b.language || "—" }),
        el("td", { textContent: b.size ? size(b.size) : "—" }),
        el("td", { textContent: b.date || "—" }),
        el("td", { textContent: b.kept ? `yes (${b.source.type})` : "no" }),
        el("td", {
          className: b.state === "ok" ? "" : "bad",
          textContent: STATE_WORDS[b.state] || b.state,
        }),
      ),
    );
    if (open) {
      rows.push(
        el(
          "tr",
          { className: "book-card" },
          el(
            "td",
            { colSpan: 7 },
            b.source
              ? sourceRow(b.source, b.status || {}, libBusy)
              : el("p", {
                  className: "setting-desc",
                  textContent:
                    `${b.name}.zim was put here by hand or from a USB drive. The librarian does not change it. ` +
                    (b.description ? `“${b.description}”` : ""),
                }),
          ),
        ),
      );
    }
  }
  bk.body.replaceChildren(
    ...(rows.length
      ? rows
      : [
          el(
            "tr",
            {},
            el("td", {
              colSpan: 7,
              className: "setting-desc",
              textContent: sm.count ? "No book matches." : "No books yet.",
            }),
          ),
        ]),
  );
  bk.pageAll.checked =
    data.books.length > 0 && data.books.every((b) => bookSel.has(b.name));
  bk.page.textContent = `Page ${data.page} of ${data.pages} (${data.matching} matching)`;
  bk.prev.disabled = data.page <= 1;
  bk.next.disabled = data.page >= data.pages;
  renderBookBulk();
}

function renderBookBulk() {
  const n = bookSel.size;
  bk.bulk.hidden =
    !n && !(bookData && bookData.matching > bookData.books.length);
  bk.selected.textContent = n ? `${n} selected:` : "";
  bk.bulk.querySelectorAll("[data-bulk]").forEach((b) => {
    b.hidden = !n;
  });
  bk.matching.textContent = bookData
    ? `Select all ${bookData.matching} matching`
    : "";
  bk.matching.hidden = !bookData || bookData.matching <= n;
  bk.none.hidden = !n;
}

const rebook = () => {
  bookPage = 1;
  clearTimeout(bookTimer);
  bookTimer = setTimeout(loadBooks, 250);
};
bk.q.addEventListener("input", rebook);
[bk.language, bk.state, bk.kept, bk.sort].forEach((filterEl) =>
  filterEl.addEventListener("change", rebook),
);
bk.prev.addEventListener("click", () => {
  bookPage -= 1;
  loadBooks();
});
bk.next.addEventListener("click", () => {
  bookPage += 1;
  loadBooks();
});
bk.pageAll.addEventListener("change", () => {
  (bookData ? bookData.books : []).forEach((b) => {
    if (bk.pageAll.checked) bookSel.add(b.name);
    else bookSel.delete(b.name);
  });
  renderBooks(bookData);
});
bk.matching.addEventListener("click", async () => {
  try {
    (await getJSON(`/admin/books?${bookQuery({ names: "1" })}`)).names.forEach(
      (n) => bookSel.add(n),
    );
    renderBooks(bookData);
  } catch (err) {
    say(err.message, false, lib.allNote);
  }
});
bk.none.addEventListener("click", () => {
  bookSel.clear();
  renderBooks(bookData);
});
document.querySelectorAll("[data-bulk]").forEach((b) =>
  b.addEventListener("click", async () => {
    // Every selected name, on any page: the librarian skips one with no source (put here by hand).
    const names = [...bookSel];
    if (b.dataset.bulk === "untrack") {
      for (const name of names) {
        try {
          await libPost({ action: "remove", name });
        } catch (_) {
          /* not tracked: nothing to stop */
        }
      }
      bookSel.clear();
      loadLibrary();
      loadBooks();
      return;
    }
    libAct("all", { action: b.dataset.bulk, names });
  }),
);

// --- Kiwix's catalogue (/admin/catalogue), online only ------------------------------------------
const cat = {
  form: document.getElementById("catalogue-form"),
  results: document.getElementById("catalogue-results"),
  note: noteEl("catalogue-note"),
  pager: document.getElementById("catalogue-pager"),
  more: document.getElementById("catalogue-more"),
};
let catStart = 0;
async function searchCatalogue(more) {
  const fields = cat.form.elements;
  catStart = more ? catStart + 20 : 0;
  say("Asking Kiwix's catalogue…", true, cat.note);
  try {
    const response = await fetch(
      `/admin/catalogue?${new URLSearchParams({ q: fields.q.value.trim(), language: fields.language.value.trim(), start: String(catStart) })}`,
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw hubError(data, response.status);
    say(
      `${data.total} book${data.total === 1 ? "" : "s"} in the catalogue match.`,
      true,
      cat.note,
    );
    const rows = data.entries.map((book) => {
      const name = book.name.replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 64);
      return el(
        "div",
        { className: "admin-item" },
        el("span", { className: "setting-name", textContent: book.title }),
        el("span", {
          className: "setting-desc",
          textContent: ` ${book.language}, ${size(book.size)}, ${book.updated}${book.flavour ? `, ${book.flavour}` : ""}, ${book.articles} articles. ${book.summary}`,
        }),
        el(
          "span",
          { className: "library-buttons" },
          actionButton("Track releases", async () => {
            try {
              await libPost({
                action: "add",
                source: {
                  name: book.flavour ? `${name}_${book.flavour}`.slice(0, 64) : name,
                  type: "kiwix",
                  kiwix_name: book.name,
                  flavour: book.flavour,
                },
              });
              say(
                `Added ${book.title} (${size(book.size)}). Select Install in the table above to download and install it.`,
                true,
                cat.note,
              );
              loadBooks();
            } catch (err) {
              say(err.message, false, cat.note);
            }
          }),
        ),
      );
    });
    if (more) cat.results.append(...rows);
    else cat.results.replaceChildren(...rows);
    cat.pager.hidden = catStart + 20 >= data.total;
  } catch (err) {
    say(err.message, false, cat.note);
  }
}
// A short list of books worth having offline (from Kiwix's catalogue: name, flavour; sizes as it gave them, 2026-10).
const SUGGESTED_BOOKS = [
  {
    name: "mdwiki_en_all",
    flavour: "maxi",
    title: "MDWiki medical encyclopedia",
    mb: 2200,
    why: "medicine and first aid, written for readers",
  },
  {
    name: "wikivoyage_en_all",
    flavour: "nopic",
    title: "Wikivoyage",
    mb: 260,
    why: "travel guides: places, getting around, staying safe",
  },
  {
    name: "ifixit_en_all",
    flavour: "",
    title: "iFixit repair guides",
    mb: 3400,
    why: "step-by-step repairs of phones, radios, tools",
  },
  {
    name: "ham.stackexchange.com_en_all",
    flavour: "",
    title: "Amateur Radio Q&A",
    mb: 72,
    why: "questions and answers on radio, antennas, licensing",
  },
  {
    name: "electronics.stackexchange.com_en_all",
    flavour: "",
    title: "Electronics Q&A",
    mb: 4000,
    why: "circuits, components, debugging",
  },
  {
    name: "wikibooks_en_all",
    flavour: "nopic",
    title: "Wikibooks",
    mb: 3300,
    why: "open textbooks and how-tos",
  },
];
function drawSuggested(sources) {
  const box = document.getElementById("suggested-books");
  if (!box) return;
  const kept = new Set(
    (sources || [])
      .filter((source) => source.type === "kiwix")
      .map((source) => `${source.kiwix_name}|${source.flavour || ""}`),
  );
  box.replaceChildren(
    ...SUGGESTED_BOOKS.map((b) => {
      const have = kept.has(`${b.name}|${b.flavour}`);
      const name = (b.flavour ? `${b.name}_${b.flavour}` : b.name)
        .replace(/[^A-Za-z0-9_.-]/g, "_")
        .slice(0, 64);
      return el(
        "div",
        { className: "admin-item" },
        el("span", { className: "setting-name", textContent: b.title }),
        el("span", {
          className: "setting-desc",
          textContent: ` About ${size(b.mb * 2 ** 20)}${b.flavour === "nopic" ? ", without pictures" : ""}: ${b.why}.`,
        }),
        el(
          "span",
          { className: "library-buttons" },
          have
            ? el("span", {
                className: "setting-desc",
                textContent: "Already tracked.",
              })
            : actionButton("Track releases", async () => {
                try {
                  await libPost({
                    action: "add",
                    source: {
                      name,
                      type: "kiwix",
                      kiwix_name: b.name,
                      flavour: b.flavour,
                    },
                  });
                  say(
                    `Added ${b.title} (about ${size(b.mb * 2 ** 20)}). Select Install in the table above to download and install it.`,
                    true,
                    cat.note,
                  );
                  loadLibrary();
                  loadBooks();
                } catch (err) {
                  say(err.message, false, cat.note);
                }
              }),
        ),
      );
    }),
  );
}
cat.form.addEventListener("submit", (event) => {
  event.preventDefault();
  searchCatalogue(false);
});
cat.more.addEventListener("click", () => searchCatalogue(true));

showTypeFields();
loadLibrary();

// --- packages from their makers (root/pkgwatch.py: meshtasticd on its channel) ------------------------
// On mPWRD-OS the channel is the one mpwrd-menu keeps, and choosing one here sets it there.
let pkgWaiting = null;
// Each package's last answer, on its own card (kept through redraws); one with no package (Check now with none yet) in
// the list's note. Asked is not done: the root helper takes jobs one at a time.
const pkgAnswer = {};
function pkgSay(id, text, ok) {
  if (!id) {
    say(text, ok, noteEl("pkg-note"));
    return;
  }
  pkgAnswer[id] = { text, ok };
  const card = [...document.querySelectorAll("#pkg-list [data-pkg]")].find(
    (c) => c.dataset.pkg === id,
  );
  const at = card && card.querySelector(".pkg-answer");
  if (at) say(text, ok, at);
}
async function loadPackages() {
  const box = document.getElementById("pkg-list");
  if (!box) return;
  let data;
  try {
    data = await getJSON("/admin/packages");
  } catch (_) {
    box.replaceChildren(
      el("p", {
        className: "setting-desc",
        textContent: "Could not read the watched packages.",
      }),
    );
    return;
  }
  if (pkgWaiting) {
    const done = (data.results || []).find((result) => result.id === pkgWaiting.id);
    if (done) {
      pkgSay(
        pkgWaiting.pkg,
        done.ok || done.msg ? resultSaid(done) : done.error || done.message,
        done.ok,
      );
      pkgWaiting = null;
    } else if (Date.now() - pkgWaiting.at < 600000)
      setTimeout(loadPackages, 3000);
  }
  const pkgs = Object.entries(data.packages || {});
  // A redraw keeps an open policy fold open, and one with changes not saved keeps them (its fold carried over).
  const before = new Map(
    [...box.querySelectorAll("[data-pkg]")].map((c) => [
      c.dataset.pkg,
      c.querySelector(".pkg-policy"),
    ]),
  );
  const carry = (card) => {
    const old = before.get(card.dataset.pkg),
      now = card.querySelector(".pkg-policy");
    if (!old || !now) return card;
    if (old.querySelector(".aw-foot .action-btn.primary:not([disabled])"))
      now.replaceWith(old);
    else now.open = old.open;
    return card;
  };
  box.replaceChildren(
    ...(pkgs.length
      ? pkgs.map(([id, pkg]) => carry(pkgCard(id, pkg)))
      : [
          el("p", {
            className: "setting-desc",
            textContent:
              "No watched packages yet. meshtasticd appears here after it is installed. Select Check now to look again.",
          }),
          el(
            "p",
            { className: "library-buttons" },
            actionButton("Check now", () =>
              pkgAct({ action: "check", package: "" }),
            ),
          ),
        ]),
  );
}
// True once the root helper has the request; false when cancelled or refused (said under the package).
async function pkgAct(body) {
  try {
    pkgWaiting = {
      id: (await postJSON("/admin/packages", body)).id,
      at: Date.now(),
      pkg: body.package || "",
    };
    pkgSay(
      body.package,
      "Queued with the root helper. It runs after the earlier jobs are done.",
      true,
    );
    setTimeout(loadPackages, 2000);
    return true;
  } catch (err) {
    pkgSay(body.package, err.message, false);
    return false;
  }
}
function pkgCard(id, pkg) {
  const pkgSettings = pkg.settings,
    label = (ch) => (pkg.labels || {})[ch] || ch;
  const builds = pkg.builds || [];
  const onChannel = builds.filter((b) => b.channel === pkgSettings.channel);
  const kept = onChannel.length ? onChannel[onChannel.length - 1] : null;
  // A flagged build (mode Flag) is known but not downloaded; it is the newest when there is one.
  const flag =
    pkg.flagged && pkg.flagged.version !== (kept || {}).version
      ? { ...pkg.flagged, channel: pkgSettings.channel, flagged: true }
      : null;
  const newest = flag || kept;
  const ageOf = (b) => ago(Date.now() / 1000 - b.first_seen);
  const looking = pkgSettings.mode === "flag" || pkgSettings.mode === "watch";
  const lines = [
    pkg.installed ? `Installed: ${pkg.installed}.` : "Not installed.",
    pkg.image
      ? pkg.image.channels.length === 1
        ? `${pkg.image.name} lists ${label(pkg.image.channels[0])}.`
        : pkg.image.channels.length
          ? `${pkg.image.name} lists ${pkg.image.channels.map(label).join(" and ")}. apt selects the newest package from either channel. Saving a channel here changes that configuration.`
          : `${pkg.image.name} lists no channel. Saving a channel here sets it there too.`
      : null,
    newest
      ? `Newest detected on ${label(newest.channel)}: ${newest.version}, first detected ${ageOf(newest)}` +
        (newest.flagged
          ? ". It is not downloaded yet. Download keeps it here. Install downloads it first."
          : ".")
      : "Not checked yet.",
    pkg.due
      ? `Due: ${pkg.due}` +
        (looking
          ? ". Nothing installs by itself. Select Install to install it."
          : ". It installs at the next check.")
      : newest && pkg.installed && newest.version !== pkg.installed
        ? `The installed version is newer than the newest version on ${label(pkgSettings.channel)}.` +
          (looking
            ? ""
            : " Nothing installs by itself until that channel passes it.") +
          " Install installs the channel's newest version anyway."
        : null,
    pkgSettings.mode === "aged"
      ? `While it waits, apt upgrade does not change it (${pkg.held ? "held" : "not held yet"}).`
      : null,
    pkg.checked ? `Checked ${ago(Date.now() / 1000 - pkg.checked)}.` : null,
  ];
  const keptList = builds.length
    ? el(
        "details",
        { className: "field-help" },
        el("summary", {
          textContent: `${builds.length} build${builds.length === 1 ? "" : "s"} kept`,
        }),
        el(
          "ul",
          {},
          ...builds
            .slice()
            .reverse()
            .map((b) =>
              el("li", {
                textContent: `${b.version} (${label(b.channel)}), first detected ${ageOf(b)}${b.version === pkg.installed ? ": installed" : ""}`,
              }),
            ),
        ),
      )
    : null;
  // The update pattern's three steps: Flag (mode flag), Download (mode watch: each build kept when first seen),
  // Install at once (auto) or after a wait (aged).
  const act = { flag: 0, watch: 1 }[pkgSettings.mode] ?? 2,
    wait = pkgSettings.mode === "aged" ? pkgSettings.days : 0;
  const pat = (list) => list.map(([v, t]) => [Number(v), t]);
  const settings = AW.settings(
    [
      {
        key: "channel",
        label: "Release channel",
        kind: "choice",
        value: pkgSettings.channel,
        options: pkg.channels.map((c) => [c, label(c)]),
      },
      {
        key: "every",
        label: "How often to look",
        kind: "choice",
        value: pkgSettings.every ?? 24,
        options: AW.UPDATE_OFTEN.map(([v, t]) => [Number(v), t]),
      },
      {
        key: "act",
        label: "When something newer is found",
        kind: "choice",
        value: act,
        options: pat(AW.UPDATE_ACT),
        note: "Flag shows the new version and downloads nothing. Download downloads and keeps each new build, so Roll back works offline. Install also installs the build.",
      },
      {
        key: "wait",
        label: "Install",
        kind: "choice",
        value: wait,
        options: [[0, "at once"]].concat(
          (pkg.days || [1, 3, 7, 14, 30]).map((n) => [
            n,
            `after ${n} day${n === 1 ? "" : "s"}`,
          ]),
        ),
        note: "With Install, a build waits until this long after the box first detected it. The box counts from detection, not from publication. Alpha and nightly builds are less stable than other channels.",
      },
    ],
    {
      id: `pkg-${id}`,
      save: (v) => {
        const all = {
          channel: pkgSettings.channel,
          every: pkgSettings.every ?? 24,
          act,
          wait,
          ...v,
        };
        const mode =
            all.act === 0
              ? "flag"
              : all.act === 1
                ? "watch"
                : all.wait
                  ? "aged"
                  : "auto",
          days = all.wait || pkgSettings.days;
        return pkgAct(
          {
            action: "settings",
            package: id,
            channel: all.channel,
            mode,
            days,
            every: all.every,
          },
        );
      },
    },
  );
  const answer = el("p", {
    className: "setting-desc action-note pkg-answer",
    role: "status",
  });
  if (pkgAnswer[id]) say(pkgAnswer[id].text, pkgAnswer[id].ok, answer);
  else answer.hidden = true;
  // What it does by itself, as saved: the fold's title; the editor inside it.
  const says = `${label(pkgSettings.channel)}: ${AW.patternSays(pkgSettings.every ?? 24, act).replace(/installs it\.$/, wait ? `installs it after ${wait} day${wait === 1 ? "" : "s"}.` : "installs it.")}`;
  const policy = el(
    "details",
    { className: "field-help update-auto pkg-policy" },
    el(
      "summary",
      {},
      "Release channel and automatic updates: ",
      el("span", { className: "pkg-policy-says", textContent: says }),
    ),
    settings,
  );
  return el(
    "div",
    { className: "setting library-source update-card", dataset: { pkg: id } },
    el(
      "span",
      { className: "update-card-body" },
      el(
        "span",
        { className: "setting-name" },
        pkg.title,
        pkg.installed ? " " : null,
        pkg.installed ? AW.updatePill(pkg.checked, !!(pkg.newer || pkg.due)) : null,
      ),
      ...lines
        .filter(Boolean)
        .map((t) => el("span", { className: "setting-desc", textContent: t })),
      el(
        "span",
        { className: "library-buttons" },
        actionButton("Check now", () =>
          pkgAct({ action: "check", package: id }),
        ),
        flag
          ? actionButton(`Download ${flag.version}`, () =>
              pkgAct({ action: "fetch", package: id }),
            )
          : null,
        newest && newest.version !== pkg.installed
          ? actionButton(
              `Install ${newest.version}`,
              () =>
                pkgAct({
                  action: "install",
                  package: id,
                  version: newest.version,
                }),
              { className: "primary" },
            )
          : null,
        pkg.previous
          ? actionButton(
              `Roll back to ${pkg.previous}`,
              () => pkgAct({ action: "rollback", package: id }),
              { title: `This downgrades ${pkg.title} to version ${pkg.previous}.` },
            )
          : null,
      ),
      answer,
      policy,
      keptList,
    ),
  );
}
loadPackages();
loadBooks();

// --- shared helpers for the sections below --------------------------------------
const size = (bytes) => {
  if (Number.isNaN(bytes)) return "size unknown";
  if (!bytes) return "0 MB";
  const gb = bytes / 2 ** 30;
  return gb >= 1
    ? `${gb.toFixed(1)} GB`
    : `${Math.max(0.1, bytes / 2 ** 20).toFixed(1)} MB`;
};
// Ages come from the hub's powered-on clock (seconds), never this browser's.
const ago = (secs) => {
  if (secs < 90) return "just now";
  const m = Math.round(secs / 60);
  if (m < 90) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
};
async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: ADMIN_HEADERS,
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw hubError(data, response.status);
  return data;
}
const actionButton = (label, onclick, extra = {}) =>
  el("button", {
    type: "button",
    textContent: label,
    onclick,
    ...extra,
    className: `action-btn${extra.className ? " " + extra.className : ""}`,
  });

// --- box and services ------------------------------------------------------------
const STATE_LABEL = {
  running: "Running",
  stopped: "Not running",
  missing: "Not installed",
};
const OP_LABEL = {
  start: "Start",
  stop: "Stop",
  restart: "Restart",
  enable: "Enable",
  disable: "Disable",
};
const OP_TITLE = { enable: "Start at boot", disable: "Do not start at boot" };
let boxPoll = null;
let waitingFor = null; // { id, at }: a control request, and the note its answer goes in
const controlNote = noteEl("control-note");

function tile(label, value) {
  return el(
    "div",
    { className: "admin-tile" },
    el("span", { className: "setting-desc", textContent: label }),
    el("span", { className: "setting-name", textContent: value }),
  );
}

// The services' uptime: in each service's own row, a thin strip of the last 72 hours by
// hour and one of 72 days by day (githubstatus.com's format), from the hub's five-minute samples
// (svchistory.py); a dot where it started (a reboot starts them all). Under the table: the legend
// and each service's week in words.
function serviceStrips(service, uptime, pages) {
  // A page the web server serves itself has no unit: its use instead (pagehistory.py), the hours it was loaded.
  if (!service.unit && pages && service.path && (pages.pages || {})[service.path]) {
    uptime = { ...pages, units: { [service.path]: pages.pages[service.path] } };
    service = { ...service, unit: service.path };
  }
  const rec = uptime && service.unit && (uptime.units || {})[service.unit];
  if (!rec) return el("span", { className: "setting-desc", textContent: "—" });
  const dayLabel = (back) => uptime.month_days[71 - back].label;
  return el(
    "div",
    { className: "svc-strips" },
    Heatmap.grid({
      bare: true,
      caption: `${service.name}, the last 72 hours by hour`,
      cols: uptime.hour_cols,
      rows: [
        {
          label: service.name,
          cells: rec.hours,
          where: (i) => `${service.name}, ${uptime.hour_full[i]}`,
        },
      ],
    }),
    Heatmap.grid({
      bare: true,
      caption: `${service.name}, the last 72 days by day`,
      cols: rec.month,
      rows: [
        {
          label: service.name,
          cells: rec.month,
          where: (i) => `${service.name}, ${dayLabel(71 - i)}`,
        },
      ],
    }),
  );
}
function renderServiceUptime(services, uptime, pages) {
  const body = document.getElementById("svc-uptime-body");
  const units = (uptime && uptime.units) || {};
  const mine = services.filter((service) => service.unit && units[service.unit]);
  const used = services.filter(
    (service) => !service.unit && service.path && pages && (pages.pages || {})[service.path],
  );
  if (!mine.length) {
    body.replaceChildren(
      el("p", {
        className: "setting-desc",
        textContent:
          "Uptime: nothing recorded yet. The box checks every service every five minutes after its clock is known to be correct. " +
          "Use network time, or set the clock on Clock. The box keeps 72 days of records.",
      }),
    );
    return;
  }
  // One line: what was not up all the time over the last 72 hours, and what is switched off now (said as off, not as
  // a past uptime); the restarts are the strips' dots. The colours' key and the web pages' strips folded below it.
  const off = (service) => service.state === "stopped" && service.enabled === false;
  const worse = mine.filter(
    (service) =>
      !off(service) &&
      units[service.unit].summary.up != null &&
      units[service.unit].summary.up < 0.995,
  );
  const offNow = services.filter((service) => service.unit && off(service));
  const quiet = mine.filter((service) => units[service.unit].summary.up == null && !off(service));
  const parts = [
    ...worse.map(
      (service) => `${service.name} up ${Heatmap.percent(units[service.unit].summary.up)}`,
    ),
    ...quiet.map((service) => `${service.name} not recorded lately`),
  ];
  const line = el(
    "p",
    { className: "setting-desc" },
    parts.length
      ? `Over the last 72 hours, all up but: ${parts.join(", ")}.`
      : "All up over the last 72 hours.",
  );
  for (const service of offNow) {
    line.append(` ${service.name} is switched off.`);
    if (service.unit === "tailscaled.service")
      line.append(
        " ",
        goLink({
          section: "access",
          where: "System → Access",
          mark: "remote-section",
        }),
      );
  }
  const usedSaid = (service) => {
    const sm = pages.pages[service.path].summary;
    return `${service.name}: ${sm.uses ? `loaded ${sm.uses} time${sm.uses === 1 ? "" : "s"}${sm.errors ? `, ${sm.errors} failed` : ""}` : "not used"} in the last 72 hours`;
  };
  body.replaceChildren(
    line,
    el(
      "details",
      { className: "svc-key" },
      el("summary", { textContent: "What the colours mean" }),
      Heatmap.legend(),
      el("p", {
        className: "setting-desc",
        textContent:
          "A dot marks an hour or a day in which the service started: restarted, or the box rebooted.",
      }),
      ...(used.length
        ? [
            el("p", {
              className: "setting-desc",
              textContent:
                "The pages the web server serves itself have no service to watch: " +
                "their strips show use instead. Blank means not used. Green means every load answered. Red means a load failed. " +
                used.map(usedSaid).join(", ") +
                ".",
            }),
          ]
        : []),
    ),
  );
}

// The network on the Overview: each link's last 72 hours by hour, the box's link to the network
// first, with its summary in words; the days and the details on Network.
function renderOverviewNet(uptime) {
  const box = document.getElementById("overview-net");
  if (!box) return;
  const ifaces = Object.keys(uptime || {});
  if (!ifaces.length) {
    box.replaceChildren();
    return;
  }
  const first = uptime[ifaces[0]],
    sm = first.summary;
  const said =
    sm.up == null
      ? `${ifaces[0]}: no data lately.`
      : `${ifaces[0]}: up ${Heatmap.percent(sm.up)} over the last 72 hours` +
        (sm.drops
          ? `, ${sm.drops} drop${sm.drops === 1 ? "" : "s"}`
          : ", no drops") +
        (sm.longest
          ? `. The longest outage was ${sm.longest.minutes} min.`
          : ".");
  box.replaceChildren(
    el("h3", { textContent: "Network, the last 72 hours" }),
    Heatmap.grid({
      caption: "The network links, the last 72 hours by hour",
      cols: first.hour_cols,
      rows: ifaces.map((i) => ({
        label: i,
        cells: uptime[i].hours,
        where: (k) => `${i}, ${uptime[i].hour_full[k]}`,
      })),
    }),
    Heatmap.legend(),
    el(
      "p",
      { className: "setting-desc" },
      said + " ",
      el("a", {
        href: "#network",
        textContent: "The links, by day too, on Network",
      }),
    ),
  );
}

// meshtasticd on the Overview, where it is installed: its last 72 hours by hour (the hub samples it
// with the services) and its state in words, for its trends: restarts, and a crash loop ended by
// systemd's start limit.
function meshWords(m, sm) {
  const ran = m.restarts
    ? `, and systemd restarted it ${m.restarts} time${m.restarts === 1 ? "" : "s"} since the box started`
    : "";
  const now =
    m.active === "active"
      ? `running${ran}.`
      : m.active === "failed" && m.result === "start-limit-hit"
        ? `stopped. It kept failing. The systemd limit of ${m.burst} starts within ${m.interval} ended the restart loop. It stays stopped until someone starts it again.`
        : m.active === "failed"
          ? `failed (${m.result || "no reason given"})${ran}.`
          : m.active === "activating"
            ? `starting${ran}.`
            : `not running${ran}.`;
  const week =
    sm && sm.up != null
      ? ` Over the last 72 hours: up ${Heatmap.percent(sm.up)}${sm.restarts ? `, started ${sm.restarts} time${sm.restarts === 1 ? "" : "s"}` : ""}.`
      : "";
  return `meshtasticd is ${now}${week}`;
}
function renderOverviewMesh(m, uptime) {
  const box = document.getElementById("overview-mesh");
  if (!box) return;
  if (!m) {
    box.replaceChildren();
    return;
  }
  const rec = uptime && (uptime.units || {})["meshtasticd.service"];
  box.replaceChildren(
    el("h3", { textContent: "Meshtastic (meshtasticd), the last 72 hours" }),
    rec
      ? Heatmap.grid({
          caption: "meshtasticd, the last 72 hours by hour",
          cols: uptime.hour_cols,
          rows: [
            {
              label: "meshtasticd",
              cells: rec.hours,
              where: (k) => `meshtasticd, ${uptime.hour_full[k]}`,
            },
          ],
        })
      : el("p", {
          className: "setting-desc",
          textContent:
            "Not recorded yet. The box checks it every five minutes after its clock is known to be correct.",
        }),
    el("p", {
      className: "setting-desc",
      textContent: meshWords(m, rec && rec.summary),
    }),
  );
}

let memoryHours = 24,
  memoryHistory = null,
  memorySystem = {};
function renderMemoryHistory(history, system = {}) {
  memoryHistory = history || {
    now: Date.now() / 1000,
    every: 300,
    samples: [],
  };
  memorySystem = system;
  const fold = document.getElementById("memory-history");
  if (!fold || !fold.open) return;
  const box = document.getElementById("memory-graph");
  const now = memoryHistory.now,
    start = now - memoryHours * 3600;
  const samples = (memoryHistory.samples || []).filter(
    (sample) =>
      Number.isFinite(sample.at) &&
      sample.at >= start &&
      sample.at <= now &&
      Number.isFinite(sample.total) &&
      sample.total > 0 &&
      Number.isFinite(sample.available),
  );
  const used = (sample) =>
    Math.max(
      0,
      Math.min(100, ((sample.total - sample.available) / sample.total) * 100),
    );
  const swap = (sample) =>
    sample.swap_total > 0
      ? Math.max(
          0,
          Math.min(
            100,
            ((sample.swap_total - sample.swap_free) / sample.swap_total) * 100,
          ),
        )
      : 0;
  // The disk, where a sample has it (older ones do not): its own size each time, so a card swapped or grown is not one line.
  const hasDisk = (sample) =>
    Number.isFinite(sample.disk_total) &&
    sample.disk_total > 0 &&
    Number.isFinite(sample.disk_free);
  const disk = (sample) =>
    Math.max(
      0,
      Math.min(
        100,
        ((sample.disk_total - sample.disk_free) / sample.disk_total) * 100,
      ),
    );
  const latest = samples.at(-1);
  document.getElementById("memory-current").replaceChildren(
    el("span", {
      className: "memory-used-key",
      textContent: system.mem_total
        ? `Memory used: ${Math.round(((system.mem_total - system.mem_available) / system.mem_total) * 100)}%`
        : "Memory used",
    }),
    el("span", {
      className: "memory-swap-key",
      textContent: system.swap_total
        ? `Swap used: ${size(system.swap_total - system.swap_free)} of ${size(system.swap_total)}`
        : "No swap configured",
    }),
    el("span", {
      className: "memory-disk-key",
      textContent: system.disk_total
        ? `Disk used: ${size(system.disk_total - system.disk_free)} of ${size(system.disk_total)}`
        : "Disk data unavailable",
    }),
  );
  document.querySelectorAll("[data-memory-hours]").forEach((button) => {
    const active = Number(button.dataset.memoryHours) === memoryHours;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const note = !latest
    ? "No samples in this range. Recording starts when the box clock is trusted."
    : `${samples.length} samples. Peak memory use ${Math.round(Math.max(...samples.map(used)))}%.` +
      (samples.some(hasDisk)
        ? ` Disk ${Math.round(disk(samples.filter(hasDisk).at(-1)))}% used at the last sample.`
        : " No disk samples yet.") +
      (now - latest.at > (memoryHistory.every || 300) * 2
        ? " No recent sample."
        : "");
  document.getElementById("memory-history-note").textContent = note;
  const width = Math.max(280, box.clientWidth || 800),
    height = 240;
  const left = 42,
    right = width - 12,
    top = 14,
    bottom = height - 34;
  const svgNode = (name, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs).forEach(([key, value]) =>
      node.setAttribute(key, value),
    );
    if (text != null) node.textContent = text;
    return node;
  };
  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "memory-plot",
    role: "img",
    tabindex: "0",
    "aria-label": `Memory, swap and disk use, last ${memoryHours === 24 ? "24 hours" : "7 days"}. ${note}`,
  });
  const x = (at) => left + ((at - start) / (now - start)) * (right - left);
  const y = (value) => bottom - (value / 100) * (bottom - top);
  for (const value of [0, 50, 100]) {
    svg.append(
      svgNode("line", {
        x1: left,
        x2: right,
        y1: y(value),
        y2: y(value),
        class: "memory-grid",
      }),
      svgNode(
        "text",
        {
          x: left - 8,
          y: y(value) + 4,
          "text-anchor": "end",
          class: "memory-axis",
        },
        `${value}%`,
      ),
    );
  }
  for (const [at, label, anchor] of [
    [start, memoryHours === 24 ? "24 h ago" : "7 d ago", "start"],
    [
      start + (now - start) / 2,
      memoryHours === 24 ? "12 h ago" : "3.5 d ago",
      "middle",
    ],
    [now, "Now", "end"],
  ]) {
    svg.append(
      svgNode(
        "text",
        {
          x: x(at),
          y: height - 8,
          "text-anchor": anchor,
          class: "memory-axis",
        },
        label,
      ),
    );
  }
  const gap = (memoryHistory.every || 300) * 2;
  for (const [getValue, className] of [
    [used, "memory-used-line"],
    [swap, "memory-swap-line"],
    [disk, "memory-disk-line"],
  ]) {
    const visible =
      className === "memory-swap-line"
        ? samples.filter((sample) => sample.swap_total > 0)
        : className === "memory-disk-line"
          ? samples.filter(hasDisk)
          : samples;
    // A new stroke after a gap in the samples, and for the disk where its size changed.
    const joins = (sample, prev) =>
      sample.at - prev.at <= gap &&
      (className !== "memory-disk-line" ||
        sample.disk_total === prev.disk_total);
    const path = visible
      .map(
        (sample, index) =>
          `${index && joins(sample, visible[index - 1]) ? "L" : "M"}${x(sample.at).toFixed(2)},${y(getValue(sample)).toFixed(2)}`,
      )
      .join(" ");
    if (path) {
      svg.append(svgNode("path", { d: path, class: className }));
      const last = visible.at(-1);
      svg.append(
        svgNode("circle", {
          cx: x(last.at),
          cy: y(getValue(last)),
          r: 3,
          class: className,
        }),
      );
    }
  }
  const readout = document.getElementById("memory-readout");
  const cursor = svgNode("line", {
    x1: right,
    x2: right,
    y1: top,
    y2: bottom,
    class: "memory-cursor",
    visibility: "hidden",
  });
  svg.append(cursor);
  let selected = samples.length - 1;
  const showSample = (index) => {
    if (!samples.length) {
      readout.textContent = "No recorded samples.";
      return;
    }
    selected = Math.max(0, Math.min(samples.length - 1, index));
    const sample = samples[selected];
    cursor.setAttribute("x1", x(sample.at));
    cursor.setAttribute("x2", x(sample.at));
    cursor.setAttribute("visibility", "visible");
    readout.textContent =
      `${new Date(sample.at * 1000).toLocaleString()}: ${Math.round(used(sample))}% memory used, ${size(sample.available)} available` +
      (sample.swap_total
        ? `, ${Math.round(swap(sample))}% swap used`
        : ", no swap") +
      (hasDisk(sample)
        ? `, disk ${Math.round(disk(sample))}% used, ${size(sample.disk_free)} free.`
        : ", disk not recorded.");
  };
  svg.addEventListener("pointermove", (event) => {
    if (!samples.length) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const at =
      start +
      ((((event.clientX - rect.left) / rect.width) * width - left) /
        (right - left)) *
        (now - start);
    const index = samples.reduce(
      (best, sample, candidate) =>
        Math.abs(sample.at - at) < Math.abs(samples[best].at - at)
          ? candidate
          : best,
      0,
    );
    showSample(index);
  });
  svg.addEventListener("focus", () => showSample(selected));
  svg.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    showSample(
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? samples.length - 1
          : selected + (event.key === "ArrowRight" ? 1 : -1),
    );
  });
  const focused = box.contains(document.activeElement);
  box.replaceChildren(svg);
  showSample(selected);
  if (focused) svg.focus();
  const processes = memoryHistory.processes || [];
  document.getElementById("memory-processes").hidden = !processes.length;
  document.getElementById("memory-processes-at").textContent =
    memoryHistory.processes_at
      ? new Date(memoryHistory.processes_at * 1000).toLocaleString()
      : "";
  document
    .getElementById("memory-processes-body")
    .replaceChildren(
      ...processes.map((process) =>
        el(
          "tr",
          {},
          el("td", { textContent: process.name }),
          el("td", { textContent: String(process.pid) }),
          el("td", { textContent: size(process.resident) }),
        ),
      ),
    );
}
document.querySelectorAll("[data-memory-hours]").forEach((button) =>
  button.addEventListener("click", () => {
    memoryHours = Number(button.dataset.memoryHours);
    renderMemoryHistory(memoryHistory, memorySystem);
  }),
);
document
  .getElementById("memory-history")
  .addEventListener("toggle", () =>
    renderMemoryHistory(memoryHistory, memorySystem),
  );
window.addEventListener("resize", () => {
  if (memoryHistory) renderMemoryHistory(memoryHistory, memorySystem);
});

let lastBox = null; // /admin/box: the services, for each app page's header
function renderBox(data) {
  lastBox = data;
  if (data.hub_port) {
    const changed = !hubPorts || hubPorts.plain !== data.hub_port || hubPorts.tls !== data.hub_tls_port;
    hubPorts = { plain: data.hub_port, tls: data.hub_tls_port };
    if (changed) drawConnectionStep();
  }
  const rate = cardRate(data.system);
  const rateEl = document.getElementById("card-writes-rate");
  if (rateEl)
    rateEl.textContent = rate
      ? `Written to the card: ${rate}`
      : "Card write data is unavailable on this system.";
  if (typeof fillHeadBlocks === "function") fillHeadBlocks();
  const sys = data.system || {};
  const h = Math.floor((data.uptime || 0) / 3600);
  const bootMinutes = Math.floor((sys.boot_uptime || 0) / 60);
  const bootTime =
    sys.boot_uptime == null
      ? "Not available"
      : `${Math.floor(bootMinutes / 1440)} d ${Math.floor(bootMinutes / 60) % 24} h ${bootMinutes % 60} min`;
  document
    .getElementById("box-tiles")
    .replaceChildren(
      tile(
        "Memory",
        sys.mem_total
          ? `${size(sys.mem_available)} free of ${size(sys.mem_total)}`
          : "—",
      ),
      tile(
        "Card",
        sys.disk_total
          ? `${size(sys.disk_free)} free of ${size(sys.disk_total)}`
          : "—",
      ),
      tile("Since boot", bootTime),
      tile(
        "Total recorded powered-on time",
        `${Math.floor(h / 24)} d ${h % 24} h across boots`,
      ),
      tile(
        "Guests",
        `${data.online} hub sessions${data.joined !== null && data.joined !== undefined ? `, ${data.joined} on the WiFi` : ""}`,
      ),
    );
  renderMemoryHistory(data.memory_history, sys);
  renderOverviewNet(data.net_uptime);
  document.getElementById("first-use").hidden = !data.first_use;
  renderOverviewMesh(data.meshtasticd, data.service_uptime);
  document.getElementById("hub-version").textContent = data.version;

  const rows = data.services.map((service) => {
    const ops = service.ops.filter((op) =>
      op === "start"
        ? !service.active
        : op === "stop"
          ? service.active
          : op === "enable"
            ? !service.enabled
            : op === "disable"
              ? service.enabled
              : true,
    );
    return el(
      "tr",
      {},
      el(
        "td",
        { className: "svc-name" },
        el("span", { className: "setting-name", textContent: service.name }),
        el("span", {
          className: "setting-desc",
          textContent: service.unit || service.note || service.path || "",
        }),
        service.why
          ? el("span", { className: "setting-desc bad", textContent: service.why })
          : null,
      ),
      el(
        "td",
        {},
        el("span", {
          className: `state state-${service.state}`,
          textContent: STATE_LABEL[service.state] || service.state,
        }),
      ),
      el("td", {
        textContent:
          service.unit && service.state !== "missing" ? (service.enabled ? "yes" : "no") : "—",
      }),
      el("td", {}, serviceStrips(service, data.service_uptime, data.page_use)),
      el(
        "td",
        {},
        el(
          "span",
          { className: "library-buttons" },
          ...(service.state === "missing"
            ? []
            : ops.map((op) =>
                actionButton(
                  OP_LABEL[op],
                  () => control(service, op),
                  OP_TITLE[op] ? { title: OP_TITLE[op] } : {},
                ),
              )),
        ),
      ),
    );
  });
  document.querySelector("#service-table tbody").replaceChildren(...rows);
  renderServiceUptime(data.services, data.service_uptime, data.page_use);

  const results = data.results || [];
  document.getElementById("control-results").replaceChildren(
    ...results.slice(0, 4).map((result) =>
      el("p", {
        className: `setting-desc${result.ok ? "" : " bad"}`,
        textContent: resultSaid(result),
      }),
    ),
  );
  if (waitingFor) {
    const done = results.find((result) => result.id === waitingFor.id);
    if (done) {
      // A service's answer is already at the top of the list above; the password's is not.
      if (waitingFor.at === controlNote) say("", true, controlNote);
      else say(resultSaid(done), done.ok, waitingFor.at);
      waitingFor = null;
    }
  }
  clearTimeout(boxPoll);
  boxPoll = setTimeout(loadBox, data.pending || waitingFor ? 1500 : 15000);
}

async function loadBox() {
  try {
    renderBox(await getJSON("/admin/box"));
  } catch (_) {
    clearTimeout(boxPoll);
    boxPoll = setTimeout(loadBox, 5000); // the hub may be restarting
  }
}

async function control(service, op) {
  try {
    waitingFor = {
      id: (await postJSON("/admin/control", { unit: service.unit, op })).id,
      at: controlNote,
    };
    say(`${OP_LABEL[op]}: ${service.name}…`, true, controlNote);
    loadBox();
  } catch (err) {
    say(err.message, false, controlNote);
  }
}

// --- moderation ------------------------------------------------------------------
function renderModeration(data) {
  const now = data.now;
  const del = (body, what) => async (event) => {
    if (!await AW.confirmHere(`Delete ${what}?`, { anchor: event.currentTarget })) return;
    try {
      renderModeration(await postJSON("/admin/moderation", body));
    } catch (err) {
      say(err.message, false, noteEl("mod-note"));
    }
  };
  renderReports(data, del);
  // What was reported, marked where the content is listed too, so it is all in one place.
  const reportedN = new Map((data.queue || []).map((report) => [report.key, report.count]));
  const flag = (key) =>
    reportedN.has(key)
      ? AW.pill(`reported ×${reportedN.get(key)}`, "bad")
      : null;
  document.getElementById("mod-messages").replaceChildren(
    ...(data.messages.length
      ? data.messages.map((m) =>
          el(
            "div",
            { className: "admin-item" },
            el(
              "span",
              {},
              el("strong", { textContent: m.name }),
              ` · ${ago(now - m.created)} `,
              flag(`shoutbox:${m.created}:${String(m.name).slice(0, 40)}`),
            ),
            el("span", { className: "admin-text", textContent: m.text }),
            actionButton(
              "Delete message",
              del(
                {
                  action: "delete_message",
                  created: m.created,
                  name: m.name,
                },
                `the message from ${m.name}`,
              ),
              { className: "small" },
            ),
          ),
        )
      : [el("p", { className: "setting-desc", textContent: "No messages." })]),
  );

  document.getElementById("mod-drops").replaceChildren(
    ...((data.drops || []).length
      ? data.drops.map((drop) =>
          el(
            "div",
            { className: "admin-item" },
            el(
              "span",
              {},
              drop.locked
                ? el("span", {
                    title:
                      "Locked by a guest's device. Only that device, or an administrator here, can remove it.",
                    textContent: "🔒 ",
                  })
                : null,
              el("a", {
                href: `/api/drop/${encodeURIComponent(drop.id)}`,
                textContent: drop.name,
              }),
              ` · ${size(drop.size)}${drop.by ? ` · ${drop.by}` : ""} · ${ago(drop.age)}${drop.locked ? " · locked" : ""}`,
            ),
            actionButton(
              "Delete file",
              del({ action: "delete_drop", id: drop.id }, `"${drop.name}"`),
              { className: "small" },
            ),
          ),
        )
      : [el("p", { className: "setting-desc", textContent: "No files." })]),
  );

  const threads = data.board.threads;
  document
    .getElementById("mod-threads")
    .replaceChildren(
      ...(threads.length
        ? threads.map((t) =>
            el(
              "details",
              { className: "admin-item" },
              el(
                "summary",
                {},
                el("strong", { textContent: t.title }),
                ` · ${t.posts.length} post${t.posts.length === 1 ? "" : "s"} · active ${ago(now - t.active)} `,
                t.posts.some((post) => reportedN.has(`board:${t.id}:${post.created}`))
                  ? AW.pill("has reported posts", "bad")
                  : null,
              ),
              ...t.posts.map((post, i) =>
                el(
                  "div",
                  { className: "admin-subitem" },
                  el(
                    "span",
                    {},
                    el("strong", { textContent: post.author }),
                    ` · ${ago(now - post.created)}${i === 0 ? " · opening post" : ""} `,
                    flag(`board:${t.id}:${post.created}`),
                  ),
                  el("span", { className: "admin-text", textContent: post.text }),
                  actionButton(
                    i === 0 ? "Delete thread" : "Delete post",
                    del(
                      i === 0
                        ? { action: "delete_thread", id: t.id }
                        : { action: "delete_post", id: t.id, index: i },
                      i === 0 ? `the whole thread "${t.title}"` : "this post",
                    ),
                    { className: "small" },
                  ),
                ),
              ),
            ),
          )
        : [el("p", { className: "setting-desc", textContent: "No threads." })]),
    );
}

// The queue across the apps: what was reported enough to count, worst first; Keep or Delete.
let lastReports = null;
AL.onBuild(() => {
  if (lastReports) renderReports(...lastReports);
});
function renderReports(data, del) {
  const queue = data.queue || [],
    rs = data.reports;
  badge("moderation", queue.length ? String(queue.length) : "");
  const q = document.getElementById("mod-queue");
  if (q)
    q.replaceChildren(
      queue.length
        ? AW.shortList(
            queue.map((report) => ({
              id: report.key,
              title: report.text.slice(0, 80) || "(empty)",
              summary: `${report.where}${report.by ? ` · by ${report.by}` : ""} · ${report.count} report${report.count === 1 ? "" : "s"}`,
              badges: Object.keys(report.reasons || {}),
              detail: () => [
                AW.h("p", { class: "admin-text", text: report.text }),
                AW.dl(
                  Object.fromEntries(
                    Object.entries(report.reasons || {}).map(([k, n]) => [
                      k,
                      `${n}`,
                    ]),
                  ),
                ),
                AW.btn("Keep content", {
                  onclick: async () => {
                    try {
                      renderModeration(
                        await postJSON("/admin/moderation", {
                          action: "keep",
                          key: report.key,
                        }),
                      );
                    } catch (err) {
                      say(err.message, false, noteEl("mod-note"));
                    }
                  },
                }),
                " ",
                // A notes page is changed or deleted in Notes itself; the rest from here.
                report.link
                  ? AW.h(
                      "a",
                      {
                        href: report.link,
                        class: "action-btn",
                        target: "_blank",
                        rel: "noopener",
                      },
                      "Open notebook",
                    )
                  : AW.btn(
                      report.key.startsWith("shoutbox:")
                        ? "Delete message"
                        : report.drop
                          ? "Delete file"
                          : report.index === 0
                            ? "Delete thread"
                            : "Delete post",
                      {
                        onclick: report.key.startsWith("shoutbox:")
                          ? del(
                              {
                                action: "delete_message",
                                created: Number(report.key.split(":")[1]),
                                name: report.by,
                              },
                              "this message",
                            )
                          : report.drop
                            ? del(
                                { action: "delete_drop", id: report.drop },
                                "this file",
                              )
                            : del(
                                report.index === 0
                                  ? { action: "delete_thread", id: report.thread }
                                  : {
                                      action: "delete_post",
                                      id: report.thread,
                                      index: report.index,
                                    },
                                report.index === 0
                                  ? "the whole thread"
                                  : "this post",
                              ),
                      },
                    ),
              ],
            })),
            { id: "mod-queue-list" },
          )
        : AW.h("p", { class: "setting-desc", text: "No reports." }),
    );
  // Each app's slice of it, on its own page: the same lines, its posts only.
  lastReports = [data, del];
  document.querySelectorAll(".flagged-block[data-app]").forEach((b) => {
    const mine = queue.filter((report) => report.key.startsWith(b.dataset.app + ":"));
    b.replaceChildren(
      mine.length
        ? AW.shortList(
            mine.map((report) => ({
              id: "own-" + report.key,
              title: report.text.slice(0, 80) || "(empty)",
              summary: `${report.where}${report.by ? ` · by ${report.by}` : ""} · ${report.count} report${report.count === 1 ? "" : "s"}`,
              badges: Object.keys(report.reasons || {}),
              detail: () => [
                AW.h("p", { class: "admin-text", text: report.text }),
                AW.h(
                  "a",
                  { href: "#moderation", class: "action-btn" },
                  "Keep or delete content under Moderation →",
                ),
              ],
            })),
            { id: "mod-queue-" + b.dataset.app },
          )
        : AW.h("p", { class: "setting-desc", text: "No reports here." }),
    );
  });
  const box = document.getElementById("report-settings");
  if (!box || !rs) return;
  // The reasons offered (any of them), how many reports count, and whether to hide meanwhile: one section.
  const chosen = new Set(rs.reasons);
  box.replaceChildren(
    AW.settings(
      [
        {
          key: "report_reasons",
          label: "Reasons offered",
          kind: "multi",
          value: rs.all_reasons.filter((reason) => chosen.has(reason)),
          options: rs.all_reasons,
        },
        {
          key: "report_threshold",
          label: "Reports required for queue",
          kind: "choice",
          value: rs.threshold,
          options: [
            [1, "1 report"],
            [2, "2"],
            [3, "3"],
            [5, "5"],
          ],
        },
        {
          key: "report_hide",
          label: "Hide reported content pending review",
          kind: "toggle",
          value: rs.hide,
          note: "Disabled: reported content stays visible while it waits for review. Enabled: content is hidden when it reaches the report limit, and shown again if you keep it.",
        },
      ],
      {
        id: "moderation",
        why: (c, v) =>
          v.report_reasons && !v.report_reasons.length
            ? "Offer at least one reason."
            : null,
        save: async (changed) => {
          const reply = await postJSON("/admin/settings", changed);
          loadModeration();
          return reply;
        },
      },
    ),
  );
}

async function loadModeration() {
  try {
    renderModeration(await getJSON("/admin/moderation"));
  } catch (_) {
    document.getElementById("mod-messages").textContent =
      "Could not read the shoutbox and board.";
  }
}

// --- saved work -------------------------------------------------------------------
const NS_LABEL = {
  scenes: "shared drawings",
  rooms: "collaboration rooms",
  files: "pasted images",
  saves: "gallery",
};

function thumbFor(id) {
  const img = el("img", { className: "admin-thumb", alt: "" });
  // Thumbnails are served as opaque downloads, so they are fetched and re-typed.
  fetch(`/api/saves/${id}/thumb`)
    .then((response) => (response.ok ? response.blob() : null))
    .then((b) => {
      if (b)
        img.src = URL.createObjectURL(new Blob([b], { type: "image/png" }));
    })
    .catch(() => {});
  return img;
}

function renderStore(data) {
  const usage = data.usage;
  const pct = Math.min(100, Math.round((100 * usage.total) / usage.max_total));
  document.getElementById("store-usage").textContent =
    `${size(usage.total)} of ${size(usage.max_total)} used (${pct}%): ` +
    Object.entries(usage.namespaces)
      .map(
        ([ns, v]) =>
          `${v.files} ${NS_LABEL[ns] || ns} file${v.files === 1 ? "" : "s"} (${size(v.bytes)})`,
      )
      .join(", ") +
    ".";
  document.getElementById("store-meter").style.width = `${pct}%`;
  const dr = data.drop;
  if (dr) {
    document.getElementById("drop-usage").textContent =
      `${dr.files} file${dr.files === 1 ? "" : "s"}, ` +
      `${size(dr.bytes)} of ${size(dr.max_total)}. Maximum file size: ${size(dr.max_file)}` +
      (dr.ttl
        ? `. Each file stays ${Math.round(dr.ttl / 3600)} hours of powered-on time.`
        : ". Files stay until the storage limit is reached.");
  }
  document.getElementById("store-clear").replaceChildren(
    ...["scenes", "rooms", "files"].map((ns) =>
      actionButton(
        `Clear ${NS_LABEL[ns]}`,
        async (event) => {
          if (
            !await AW.confirmHere(
              `Delete all ${NS_LABEL[ns]}? This cannot be undone. Links to them stop working.`, { anchor: event.currentTarget })
          )
            return;
          try {
            renderStore(
              await postJSON("/admin/store", {
                action: "clear",
                namespace: ns,
              }),
            );
          } catch (err) {
            say(err.message, false, noteEl("store-note"));
          }
        },
        { disabled: !usage.namespaces[ns].files, className: "small" },
      ),
    ),
  );

  const act = (body) => async () => {
    try {
      renderStore(await postJSON("/admin/store", body));
    } catch (err) {
      say(err.message, false, noteEl("store-note"));
    }
  };
  // Rename: the name becomes a text field in place, with Rename and Cancel (Enter renames, Escape cancels).
  const rename = (saved, title, opener) => {
    const field = el("input", {
      value: saved.name,
      maxLength: 64,
      "aria-label": "New name",
    });
    const done = () => {
      form.replaceWith(title);
      opener.hidden = false;
    };
    const save = () => {
      const name = field.value.trim();
      if (!name || name === saved.name) return done();
      act({ action: "rename", id: saved.id, name })();
    };
    const form = el(
      "span",
      { className: "library-buttons store-rename" },
      field,
      actionButton("Rename", save, { className: "small primary" }),
      actionButton("Cancel", done, { className: "small" }),
    );
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") save();
      else if (event.key === "Escape") done();
    });
    title.replaceWith(form);
    opener.hidden = true;
    field.focus();
    field.select();
  };
  document.getElementById("store-saves").replaceChildren(
    ...(data.saves.length
      ? data.saves.map((save) => {
          const title = el("strong", {
            textContent: `${save.locked ? "🔒 " : ""}${save.name}`,
          });
          return el(
            "div",
            { className: "admin-item admin-save" },
            save.thumb ? thumbFor(save.id) : el("span", { className: "admin-thumb" }),
            el(
              "span",
              {},
              title,
              el("span", {
                className: "setting-desc",
                textContent:
                  `${save.kind} · ${size(save.size)} · ${ago(data.now - save.created)}` +
                  (save.locked
                    ? " · locked by a guest's device. Only that device can change it. You can still rename or delete it here."
                    : ""),
              }),
            ),
            el(
              "span",
              { className: "library-buttons" },
              actionButton("Rename", (event) => rename(save, title, event.currentTarget), {
                className: "small",
              }),
              actionButton(
                "Delete",
                async (event) => {
                  if (await AW.confirmHere(`Delete "${save.name}"?`, { anchor: event.currentTarget }))
                    act({ action: "delete", id: save.id })();
                },
                { className: "small" },
              ),
            ),
          );
        })
      : [
          el("p", {
            className: "setting-desc",
            textContent: "Nothing saved to the gallery yet.",
          }),
        ]),
  );
}

async function loadStore() {
  try {
    renderStore(await getJSON("/admin/store"));
  } catch (_) {
    document.getElementById("store-usage").textContent =
      "Could not read the store.";
  }
}

// --- settings: the store's cap and expiry -----------------------------------------
const storeForm = document.getElementById("store-settings");
async function loadStoreSettings() {
  try {
    const reply = await getJSON("/admin/settings");
    storeSec.fill({
      store_max_total_mb: reply.store_max_total_mb,
      store_save_ttl_hours: reply.store_save_ttl_hours,
      drop_max_total_mb: reply.drop_max_total_mb,
      drop_ttl_hours: reply.drop_ttl_hours,
    });
  } catch (_) {
    /* the landing-page block already reports a failed read */
  }
}
const storeSec = AW.form(storeForm, {
  label: "Storage limits",
  save: async (c) => {
    await postJSON(
      "/admin/settings",
      Object.fromEntries(Object.entries(c).map(([k, v]) => [k, Number(v)])),
    );
    loadStore();
    return "Saved. The new storage limit applies when the box next writes data.";
  },
});

// --- password -----------------------------------------------------------------------
const pwForm = document.getElementById("password-form");
const pwNote = noteEl("password-note");
pwForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pw = pwForm.elements.password.value;
  if (pw !== pwForm.elements.confirm.value) {
    say("The two passwords differ.", false, pwNote);
    return;
  }
  try {
    waitingFor = {
      id: (await postJSON("/admin/password", { password: pw })).id,
      at: pwNote,
    };
    pwForm.reset();
    say(
      "Changing the password. Your browser will ask for the new password.",
      true,
      pwNote,
    );
    loadBox();
  } catch (err) {
    say(err.message, false, pwNote);
  }
});

loadBox();
loadModeration();
loadStore();
loadStoreSettings();

// --- updates ---------------------------------------------------------------------------
// The root helper does all three steps (check into a root-owned cache; fetch: verify it and
// cache its downloads; install: rerun install.sh); this only asks, and shows what it
// reports, with a bar for the step it is on. The hub restarts during an install, so a
// failed read while one is running means "keep waiting", not "broken".
const upd = {
  summary: document.getElementById("update-summary"),
  check: document.getElementById("update-check"),
  fetch: document.getElementById("update-fetch"),
  install: document.getElementById("update-install"),
  progress: document.getElementById("update-progress"),
  bar: document.getElementById("update-bar"),
  step: document.getElementById("update-step"),
  note: document.getElementById("update-note"),
  changes: document.getElementById("update-changes"),
  checks: document.getElementById("update-checks"),
  output: document.getElementById("update-output"),
  log: document.getElementById("update-log"),
  doctor: document.getElementById("update-doctor"),
  clear: document.getElementById("update-clear"),
  doctorNote: document.getElementById("doctor-note"),
  clearNote: document.getElementById("update-clear-note"),
  sourceRepo: document.getElementById("update-source-repo"),
  sourceBranch: document.getElementById("update-source-branch"),
  sourcePoll: document.getElementById("update-source-poll"),
  sourceChoices: document.getElementById("update-source-choices"),
  sourceList: document.getElementById("update-source-branches"),
  sourceWarn: document.getElementById("update-source-warn"),
  sourceNote: document.getElementById("update-source-note"),
  doctorWhen: document.getElementById("doctor-when"),
  findings: document.getElementById("doctor-findings"),
  udSummary: document.getElementById("updoctor-summary"),
  udEmpty: document.getElementById("updoctor-empty"),
  udFineFold: document.getElementById("updoctor-fine-fold"),
  udFineCount: document.getElementById("updoctor-fine-count"),
  udFine: document.getElementById("updoctor-fine"),
};
const MARK = { ok: "✅", warn: "⚠️", problem: "❌" };
// evidence: what a check saw (an error line, a file), as data; note: what the check does not show.
const checkItem = (status, title, detail, fix, evidence = [], note = "") =>
  el(
    "li",
    { className: `check check-${status}` },
    el("span", { textContent: `${MARK[status]} ` }),
    el("strong", { textContent: title }),
    detail ? el("span", { textContent: ` — ${detail}` }) : null,
    fix ? el("span", { className: "setting-desc", textContent: fix }) : null,
    note ? el("span", { className: "setting-desc", textContent: note }) : null,
    evidenceList(evidence),
  );
const UPD_DOING = {
  check: "Checking for updates",
  fetch: "Downloading update",
  install: "Installing the update",
  addon: "Changing an add-on",
  repair: "Running the installer again",
};
let updWaiting = null; // { id, action }
let updPoll = null;
let updCandidate = null;

// Each answer goes under the buttons that asked: a finding's and the doctor's under the doctor, the cache's in its recovery actions.
function updSay(action, text, ok, extra, fromFinding = false) {
  const node = fromFinding
    ? upd.doctorNote
    : action === "set-source" || action === "poll-source"
        ? upd.sourceNote
        : action === "clear-cache"
          ? upd.clearNote
          : action === "doctor" || action === "poll-target"
            ? upd.doctorNote
            : upd.note;
  node.replaceChildren(text || "", ...(extra ? [" ", extra] : []));
  node.classList.toggle("bad", !ok);
  node.hidden = !text;
}

function renderUpdateProgress(progress) {
  upd.progress.hidden = !progress;
  if (!progress) return;
  const steps = Math.max(progress.steps, progress.step, 1);
  const part = progress.total ? Math.min(progress.done / progress.total, 1) : 0;
  upd.bar.value = Math.min((Math.max(progress.step - 1, 0) + part) / steps, 1);
  const bytes = progress.total
    ? `: ${size(progress.done)} of ${size(progress.total)} (${Math.round(100 * part)}%)`
    : progress.done
      ? `: ${size(progress.done)}`
      : "";
  upd.step.textContent =
    `${UPD_DOING[progress.action] || "Working"}, step ${Math.max(progress.step, 1)} of ${progress.estimate ? "about " : ""}${steps}` +
    (progress.label ? ` — ${said(progress.label)}${bytes}` : "") +
    ".";
}

// The update doctor, in the other doctors' shape: the counters (which double as the filter), one row per
// finding with what to do (a command to copy, the place it is changed), and what was fine folded away.
let udShow = "all";
function renderUpdoctor(report, stale) {
  const all = report
    ? [...report.findings].sort((left, right) => RANK[left.status] - RANK[right.status])
    : [];
  const counts = {
    problem: all.filter((finding) => finding.status === "problem").length,
    warn: all.filter((finding) => finding.status === "warn").length,
  };
  upd.doctorWhen.textContent = report
    ? `Last checked ${new Date(report.at * 1000).toLocaleString()}.` +
      (stale
        ? " Out of date: a check for updates or a change of source came after it. Run the doctor again."
        : "")
    : "Not run yet. It runs when you select the button. It also runs when a check for updates fails.";
  upd.doctorWhen.classList.toggle("warn", !!stale);
  upd.udSummary.replaceChildren(
    ...(!report
      ? []
      : [
          [
            "all",
            counts.problem + counts.warn,
            "all",
            counts.problem ? "problem" : counts.warn ? "warn" : "ok",
          ],
          [
            "problem",
            counts.problem,
            "to fix",
            counts.problem ? "problem" : "ok",
          ],
          ["warn", counts.warn, "to review", counts.warn ? "warn" : "ok"],
        ].map(([key, n, word, st]) => {
          const b = el(
            "button",
            {
              type: "button",
              className: `sec-count sec-count-${st}${udShow === key ? " active" : ""}`,
              onclick: () => {
                udShow = key;
                renderUpdoctor(report);
              },
            },
            el("strong", { textContent: String(n) }),
            ` ${word}`,
          );
          b.setAttribute("aria-pressed", String(udShow === key));
          return b;
        })),
  );
  // Each row named by its check, not its place in the list, so a link to it survives a new report.
  const slug = (finding) =>
    `ud-${String(finding.id)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")}`;
  // A finding (finding.py) brings its parts, its commands and its place to go.
  // The doctor's own buttons ask the update helper (requestUpdate), not the box doctor's repairs (hlFix).
  const item = (finding) => {
    const x = healthDo({ ...finding, actions: [] }, false);
    const own = finding.status === "ok" ? [] : (finding.actions || []).map((action) =>
      el("button", {
        type: "button",
        className: "action-btn",
        textContent: action.label,
        onclick: () => {
          requestUpdate(action.choice, null, {}, true);
        },
      }),
    );
    return findingRow({
      id: slug(finding),
      state: finding.status,
      title: finding.title,
      ...findingParts(finding),
      how: x.how,
      commands: finding.status === "ok" ? [] : x.commands || [],
      controls: [...x.controls, ...own],
    });
  };
  const open = all.filter(
    (finding) => finding.status !== "ok" && (udShow === "all" || finding.status === udShow),
  );
  upd.findings.replaceChildren(...open.map(item));
  upd.udEmpty.hidden = !report || open.length > 0;
  upd.udEmpty.textContent =
    counts.problem + counts.warn
      ? "Nothing of that kind."
      : "Nothing to fix or review. Updates have what they need.";
  const fine = all.filter((finding) => finding.status === "ok");
  upd.udFineFold.hidden = !fine.length;
  upd.udFineCount.textContent = `(${fine.length})`;
  upd.udFine.replaceChildren(...fine.map(item));
}
// On a metered network (a phone's hotspot, say): scheduled updates and downloads wait; one started
// by hand begins at once, as the note above the buttons says.
let meteredNow = null;
function renderUpdate(data) {
  drawSigning(data.signing, data.results);
  const m = data.metered || {};
  meteredNow = m.now || null;
  const mEl = document.getElementById("update-metered");
  const w = m.waiting;
  mEl.hidden = !(meteredNow && meteredNow.metered) && !w;
  mEl.textContent = w
    ? `Scheduled updates and downloads have waited since ${new Date(w.since * 1000).toLocaleString()}. The box is on a metered network ` +
      `(${w.connection || w.iface}, ${w.how}). They continue on another network. A download that you start here begins at once and can use a lot of data. To change this, mark the network as not metered in its settings (Network → WiFi).`
    : meteredNow && meteredNow.metered
      ? `The box is on a metered network (${meteredNow.connection || meteredNow.iface}, ${meteredNow.how}). Scheduled updates and downloads wait for another network. A download that you start here begins at once and can use a lot of data.`
      : "";
  const state = AW.localise(data.state); // the checks' and the doctor's messages said once, in the reader's language
  const progress = data.progress;
  const busy = data.pending > 0 || !!updWaiting || !!progress;
  const found = !!state && !state.up_to_date; // a check found a newer version
  const fetched = found && !!(state.checks && state.checks.length);
  const ready = found && state.verified === state.available;
  if (!state) {
    upd.summary.replaceChildren(
      AW.updatePill(null, false),
      " Not checked yet.",
    );
  } else {
    const when = new Date(state.fetched * 1000).toLocaleString();
    upd.summary.replaceChildren(
      AW.updatePill(state.fetched, found),
      " ",
      state.up_to_date
        ? `Up to date with ${state.branch} (${state.available}, ${state.available_date}). Checked ${when}.`
        : `Available: ${state.available} (${state.available_date}) on ${state.branch}` +
            (state.changes_known
              ? `, ${state.changes.length} new commit${state.changes.length === 1 ? "" : "s"}`
              : "") +
            `. Checked ${when}. ` +
            (ready
              ? "Downloaded and verified. Its downloads are cached. Ready to install."
              : fetched
                ? "It did not pass verification (below). It cannot be installed. The update doctor says whether Install anyway can skip the failed checks."
                : "Download it to verify it and download what it needs."),
    );
  }
  const checks = (fetched && state.checks) || [];
  const failedChecks = ready ? [] : checks.filter((c) => !c.ok && !c.warn);
  upd.checks.hidden = !checks.length;
  upd.checks.replaceChildren(
    ...checks.map((c) =>
      checkItem(
        c.ok ? "ok" : c.warn ? "warn" : "problem",
        c.name,
        c.detail,
        c.ok ? "" : c.fix,
        c.ok ? [] : c.evidence || [],
        c.note,
      ),
    ),
  );
  const doctor = AW.localise(data.doctor);
  renderUpdoctor(doctor, data.doctor_stale);
  drawSource(data);
  const doctorProblems = doctor
    ? doctor.findings.filter((finding) => finding.status === "problem").length
    : 0;
  badge(
    "updoctor",
    doctorProblems ? String(doctorProblems) : failedChecks.length ? "!" : "",
  );
  upd.changes.replaceChildren(
    ...((found && state.changes) || [])
      .slice(0, 20)
      .map((c) => el("li", { textContent: c })),
  );
  renderAuto(data.auto);
  upd.output.hidden = !data.log.length || (!busy && state && state.up_to_date);
  if (progress && progress.action === "install") upd.output.open = true;
  upd.log.textContent = data.log.join("\n");
  upd.log.scrollTop = upd.log.scrollHeight;
  renderUpdateProgress(progress);
  badge(
    "updates",
    progress ? "working" : ready ? "ready" : found && !fetched ? "new" : "",
  );

  if (updWaiting) {
    const done = (data.results || []).find((result) => result.id === updWaiting.id);
    if (done) {
      const failed =
        !done.ok ||
        (done.msg
          ? done.msg.id === "update.fetch.failed"
          : /did not pass verification/.test(resultSaid(done)));
      // A failure says itself, with the way to its diagnosis beside it.
      updSay(
        updWaiting.action,
        resultSaid(done),
        !failed,
        failed && !["doctor", "poll-target", "poll-source", "set-source"].includes(
          updWaiting.action,
        )
          ? el("a", {
              className: "action-btn go-btn",
              href: "#updoctor",
              textContent: "Show reason: Updates doctor →",
            })
          : null,
        updWaiting.finding,
      );
      if (updWaiting.job) updWaiting.job.done(!failed, resultSaid(done));
      updWaiting = null;
      renderUpdate(data);
      return;
    }
  }
  updCandidate = found ? state.available_sha || null : null;
  upd.check.textContent = "Check for updates";
  upd.fetch.textContent = found ? "Download" : "Check for updates and download";
  upd.install.textContent = fetched ? "Install" : found ? "Download and install" : "Check for updates, download and install";
  upd.check.disabled = busy || found;
  upd.doctor.disabled = upd.clear.disabled = busy;
  upd.fetch.disabled = busy || fetched;
  upd.install.disabled = busy || (fetched && !ready);
  clearTimeout(updPoll);
  if (busy) updPoll = setTimeout(loadUpdate, progress ? 1000 : 2000);
}

async function loadUpdate() {
  try {
    renderUpdate(await getJSON("/admin/update"));
  } catch (_) {
    clearTimeout(updPoll);
    // Only an install restarts the hub: during one, an unanswered status is that restart; otherwise it is said as
    // not answering, and what was asked keeps its own words.
    if (updWaiting) {
      const installing = /install/.test(updWaiting.action);
      upd.summary.textContent = installing
        ? "Installing. The hub server is restarting."
        : "The hub server is not answering. Reconnecting to check the action status.";
      updPoll = setTimeout(loadUpdate, 3000);
    }
  }
}

// fromFinding: a doctor finding's own button, which asked its question already (Install anyway names the checks it skips) and is answered beside it.
// Install anyway is only a finding's button: the doctor offers it by the same rule the helper installs by.
async function requestUpdate(action, endpoint = null, extra = {}, fromFinding = false) {
  try {
    updWaiting = {
      id: (await postJSON("/admin/update", { action, endpoint, candidate: updCandidate, ...extra })).id,
      action: endpoint === "install" ? "install" : action,
      finding: fromFinding,
    };
    upd.check.disabled =
      upd.fetch.disabled =
      upd.install.disabled =
        true;
    updSay(
      action,
      {
        check: "Checking for updates…",
        fetch:
          "Downloading the update. The box verifies it and caches its downloads…",
        install: "Installing the update…",
        "force-install": "Installing the downloaded version anyway…",
        doctor: "Running the update doctor. It can take up to two minutes…",
        "poll-target": "Asking the repository for its branches…",
        "poll-source": "Asking the repository for its branches…",
        "set-source": "Changing where updates come from…",
        "clear-cache": "Clearing the update cache…",
        reach: endpoint === "install" ? "Preparing and installing the update…" : "Checking and downloading the update…",
      }[action],
      true,
      null,
      fromFinding,
    );
    loadUpdate();
  } catch (err) {
    updSay(action, err.message, false, null, fromFinding);
  }
}

upd.check.addEventListener("click", () => requestUpdate("check"));
upd.fetch.addEventListener("click", () => {
  requestUpdate("reach", "download");
});
upd.doctor.addEventListener("click", () => requestUpdate("doctor"));
upd.clear.addEventListener("click", () => {
  requestUpdate("clear-cache");
});
upd.install.addEventListener("click", () => {
  requestUpdate("reach", "install");
});
// Where updates come from (a fork, say): a section, filled with the recorded source. The repository is asked for its
// branches (the default first, offered as choices) and how the branch this box follows stands against the installed
// version; a change is saved after one question that names it. The root helper checks the address and the branch
// again, and refuses it when policy is console-only.
let updLast = null;
const srcSec = AW.section({
  id: "update-source",
  label: "Update source",
  saved: null,
  why: (c, v) =>
    !v.repo.trim() || !v.branch.trim()
      ? "Give a repository and a branch."
      : null,
  warn: () =>
    'The box runs the code from this branch as root. "Merged on GitHub" accepts a commit from any fork. To trust one person, select "Signed releases only" and add their key.',
  save: async (c, v) => {
    const j = AW.job();
    updWaiting = {
      id: (
        await postJSON("/admin/update", {
          action: "set-source",
          repo: v.repo.trim(),
          branch: v.branch.trim(),
          confirmed: true,
        })
      ).id,
      action: "set-source",
      job: j,
    };
    loadUpdate();
    return j;
  },
  onChange: () => updLast && drawSource(updLast),
});
document
  .getElementById("update-source-foot")
  .append(srcSec.foot, srcSec.reason);
for (const [input, key] of [
  [upd.sourceRepo, "repo"],
  [upd.sourceBranch, "branch"],
])
  // Drawn again as typed (the fields themselves are kept): the branches offered follow the repository.
  input.addEventListener("input", () => srcSec.set(key, input.value));
const STANDING_SAYS = {
  gone: (b) => `The repository no longer has the branch ${b.branch}. The box finds no updates.`,
  behind: (b) =>
    `The branch ${b.branch} is behind the installed version ${b.installed}. The box finds no updates on it, and installing from it would go back.`,
  elsewhere: (b) =>
    `The branch ${b.branch} does not contain the installed version ${b.installed}; ${b.default} does. Every update from ${b.branch} would fail the check that it continues from the installed version.`,
  merged: (b) => `The branch ${b.branch} is already merged into ${b.default}.`,
};
function drawSource(data) {
  updLast = data;
  const src = data.source;
  if (
    src &&
    src.repo &&
    !srcSec.saving() &&
    (!srcSec.loaded() ||
      srcSec.savedValue("repo") !== src.repo ||
      srcSec.savedValue("branch") !== src.branch)
  )
    return srcSec.update({ repo: src.repo, branch: src.branch });
  if (!srcSec.loaded()) return;
  const v = srcSec.values();
  if (upd.sourceRepo.value !== v.repo) upd.sourceRepo.value = v.repo;
  if (upd.sourceBranch.value !== v.branch) upd.sourceBranch.value = v.branch;
  upd.sourceRepo.disabled = upd.sourceBranch.disabled = srcSec.disabled();
  // What the repository said, when it is the one in the form: its branches to pick from, the default first.
  const b = data.branches;
  const known = b && b.repo === v.repo.trim() ? b : null;
  const names = known ? known.branches || [] : [];
  upd.sourceList.replaceChildren(
    ...names.map((n) => el("option", { value: n })),
  );
  upd.sourceChoices.replaceChildren(
    ...(names.length
      ? [
          el(
            "div",
            { className: "chip-group", role: "group" },
            el("span", {
              className: "setting-desc",
              textContent: "Branches: ",
            }),
            ...names.slice(0, 8).map((n) => {
              const chip = el("button", {
                type: "button",
                className: "chip" + (n === v.branch ? " selected" : ""),
                textContent: n === known.default ? `${n} (default)` : n,
                disabled: srcSec.disabled(),
                onclick: () => srcSec.set("branch", n),
              });
              chip.setAttribute("aria-pressed", String(n === v.branch));
              return chip;
            }),
          ),
        ]
      : []),
  );
  // The branch this box follows, as the repository last said it stands: left behind, or fine.
  const says =
    known &&
    known.branch === srcSec.savedValue("branch") &&
    STANDING_SAYS[known.standing];
  upd.sourceWarn.hidden = !says;
  upd.sourceWarn.textContent = says
    ? `${says(known)} Select ${known.default || "main"} and save.`
    : "";
}
upd.sourcePoll.addEventListener("click", () =>
  requestUpdate("poll-source", null, {
    repo:
      upd.sourceRepo.value.trim() === (updLast && updLast.source && updLast.source.repo)
        ? ""
        : upd.sourceRepo.value.trim(),
  }),
);

// Automatic updates (the librarian's selfupdate.py): its policy, saved through /admin/library,
// and what its last step did. The form is filled from the hub only while nobody is editing it.
const auto = {
  form: document.getElementById("auto-form"),
  state: document.getElementById("auto-state"),
  note: noteEl("auto-note"),
  window: document.getElementById("auto-window"),
  says: document.getElementById("update-auto-says"),
};
for (const name of ["hub_window_start", "hub_window_end"]) {
  auto.form.elements[name].replaceChildren(
    ...Array.from({ length: 24 }, (_, h) =>
      el("option", {
        value: String(h),
        textContent: `${String(h).padStart(2, "0")}:00`,
      }),
    ),
  );
}
const AUTO_STEP = {
  check: "checked for an update",
  fetch: "downloaded and verified an update",
  install: "installed an update",
};
function renderAuto(settings) {
  if (!settings) return;
  // The section keeps an edit not saved yet through polling; the fold's title says what is saved.
  autoSec.fill({
    hub_check_every_hours: settings.hub_check_every_hours,
    hub_auto: settings.hub_auto,
    hub_window_start: settings.hub_window_start,
    hub_window_end: settings.hub_window_end,
  });
  const hh = (h) => `${String(h).padStart(2, "0")}:00`;
  auto.says.textContent =
    AW.patternSays(settings.hub_check_every_hours, settings.hub_auto).replace(/\.$/, "") +
    (Number(settings.hub_check_every_hours) && Number(settings.hub_auto) === 2
      ? `, between ${hh(settings.hub_window_start)} and ${hh(settings.hub_window_end)}.`
      : ".") +
    (autoSec.dirty() ? " (Changed below, not saved.)" : "");
  auto.window.hidden = auto.form.elements.hub_auto.value !== "2";
  const st = settings.state || {};
  const parts = [];
  if (st.waiting)
    parts.push(
      `Working. The root helper was asked to ${st.waiting.step} at ${new Date(st.waiting.at * 1000).toLocaleTimeString()}.`,
    );
  if (st.last)
    parts.push(
      `Last automatic step, ${new Date(st.last.at * 1000).toLocaleString()}: ${AUTO_STEP[st.last.step] || st.last.step}. ${st.last.message}`,
    );
  if (st.note && !st.waiting) parts.push(st.note + ".");
  auto.state.textContent =
    parts.join(" ") ||
    (Number(settings.hub_check_every_hours)
      ? "No automatic step yet. The librarian runs the first step at the next hourly cycle."
      : "Automatic checks are disabled.");
}
auto.form.elements.hub_auto.addEventListener("change", () => {
  auto.window.hidden = auto.form.elements.hub_auto.value !== "2";
});
const autoSec = AW.form(auto.form, {
  label: "Automatic updates",
  save: async (c, v) => {
    if (v.hub_auto === "2" && v.hub_window_start === v.hub_window_end)
      throw new Error("The start time and the end time must differ.");
    const body = { action: "policy" };
    for (const [k, x] of Object.entries(c)) body[k] = Number(x);
    await libPost(body);
    loadUpdate();
    return "Saved. The librarian applies it at the next hourly cycle.";
  },
});
loadUpdate();

// --- security --------------------------------------------------------------------------
// The hub's own lines (password, plain HTTP, one origin) and the root helper's scan of the
// box (listeners, SSH, security updates). Each offer is a button on its line; its answer goes
// under it. A scan older than 15 minutes is refreshed when the page opens.
const sec = {
  when: document.getElementById("security-when"),
  scan: document.getElementById("security-scan"),
  note: document.getElementById("security-note"),
  counts: document.getElementById("security-counts"),
  findings: document.getElementById("security-findings"),
  hub: document.getElementById("security-hub"),
  listeners: document.querySelector("#security-listeners tbody"),
  output: document.getElementById("security-output"),
  log: document.getElementById("security-log"),
  auditWhen: document.getElementById("audit-when"),
  auditRun: document.getElementById("audit-run"),
  auditSteps: document.getElementById("audit-steps"),
  auditScope: document.getElementById("audit-scope"),
  auditNot: document.getElementById("audit-not-covered"),
  auditNote: document.getElementById("audit-note"),
  auditDeep: document.getElementById("audit-deep"),
  auditDeepWhen: document.getElementById("audit-deep-when"),
};
const RANK = { problem: 0, warn: 1, ok: 2 };
let secWaiting = null; // { id, fid }: a request, and the line its answer goes under
let secNote = null; // { fid, text, ok }
let secPoll = null;
let secAsked = false;
let secData = null; // the last /admin/security, for redraws (accepting, filtering)

// Which page a line of the box's scan belongs on (decided in the page: the root helper and its
// allow-list are untouched). A cure has one right answer: the kernel's protections, root's own
// login and password, its first-login script, SSH's root login, LLMNR. Debian's security updates
// are system updates. The rest are the owner's real choices.
const CURE_ID = /^(kernel-|root-password|root-firstrun|ssh-root|llmnr)/;
const CURE_CHOICE = /^(kernel-|root-lock|firstrun-|ssh-root-|llmnr-)/;
function secKind(finding) {
  if (finding.id === "security-updates" || finding.id === "unattended") return "update";
  if (
    CURE_ID.test(finding.id) ||
    (finding.actions || []).some((action) => CURE_CHOICE.test(action.choice))
  )
    return "cure";
  // A port is in the Network listeners table, with its buttons there (Stop and disable, Open to the hotspot):
  // never a card of its own. Cockpit has a setting (cockpit_findings).
  if (finding.id.startsWith("port-")) return "port";
  return "choice";
}
// Passwordless sudo as a toggle: On while a
// NOPASSWD rule is there (Off takes it out), Off once taken out from here (On puts it back).
function sudoToggle(finding, busy) {
  return finding.actions.map((action) => {
    const file = action.choice.split(":")[1];
    const on = action.choice.startsWith("sudo-drop:") !== secChosen(finding.id, action); // as it would be after Save
    const b = el("button", {
      type: "button",
      className:
        "chip-btn" +
        (on ? " active" : "") +
        (secChosen(finding.id, action) ? " pending" : ""),
      disabled: busy,
      textContent: `Passwordless sudo${finding.actions.length > 1 ? ` (${file})` : ""}: ${on ? "On" : "Off"}`,
      title: action.label,
      onclick: (event) => secChoose(finding, action, event.currentTarget),
    });
    b.setAttribute("aria-pressed", String(on));
    return b;
  });
}
// A line of the box's scan: its buttons, each acting through the root helper (only what its last scan offered).
function scanButtons(finding, busy) {
  if (finding.id === "sudo-nopasswd") return sudoToggle(finding, busy);
  if ((finding.actions || []).some((action) => action.group))
    return [
      ...secChipRows(finding, busy),
      ...scanButtons(
        { ...finding, actions: finding.actions.filter((action) => !action.group) },
        busy,
      ),
    ];
  return (finding.actions || []).map((action) => {
    const b = el("button", {
      type: "button",
      className: "action-btn" + (secChosen(finding.id, action) ? " pending" : ""),
      textContent: action.label,
      disabled: busy,
      title: secChosen(finding.id, action)
        ? "Selected. Select Save below to apply this change. Select the button again to cancel the selection."
        : "",
      onclick: (event) => secChoose(finding, action, event.currentTarget),
    });
    b.setAttribute("aria-pressed", String(secChosen(finding.id, action)));
    return b;
  });
}
// The Security page's, the Security doctor's and Updates' choices are settings: each finding's is a section of one
// value (the choice picked, '' for none), saved from the footer under it, its question asked once at Save. A cure
// (one right answer) and its Undo are actions: they act at once, after their question.
const secSecs = new Map(); // finding id -> its section
const secOffer = (fid, choice) => {
  const finding = ((secData && secData.scan && secData.scan.findings) || []).find(
    (x) => x.id === fid,
  );
  return finding && (finding.actions || []).find((action) => action.choice === choice);
};
function secSection(finding) {
  if (secSecs.has(finding.id)) return secSecs.get(finding.id);
  const sec = AW.section({
    id: `sec-${finding.id}`,
    label: finding.title,
    saved: { choice: "" },
    warn: (c) => {
      const offer = secOffer(finding.id, c.choice);
      return offer && offer.confirm ? offer.confirm : null;
    },
    save: async (c) => {
      const j = AW.job();
      secRequest({ action: "fix", choice: c.choice }, finding.id, j.done);
      await j;
      // Done: nothing waits any more (the scan says what is in effect now).
      setTimeout(() => sec.update({ choice: "" }));
    },
    busy: () => !!(secWaiting && secWaiting.fid === finding.id),
    onChange: () => secData && renderSecurity(secData),
  });
  secSecs.set(finding.id, sec);
  return sec;
}
const secChosen = (fid, action) => {
  const sec = secSecs.get(fid);
  return !!sec && sec.dirty() && sec.get("choice") === action.choice;
};
function secChoose(finding, action) {
  // A cure acts at once (the one in effect already: nothing to do).
  if (secKind(finding) === "cure") return action.on ? undefined : secFix(finding.id, action);
  // Chosen again, or the choice it has now: nothing to save for it.
  secSection(finding).set("choice", secChosen(finding.id, action) || action.on ? "" : action.choice);
}
// The footer under a finding whose choice is held for Save.
const secFoot = (finding) =>
  secKind(finding) === "cure" || !(finding.actions || []).length
    ? []
    : [secSection(finding).foot, secSection(finding).reason];

// --- one finding, the same shape on every page -------------------------------------------------------
// A status word, not an emoji; the title; what it means; and one thing to do: buttons that do it here, a
// link to where it is chosen, a command to type, or plainly nothing (the hub's own work, a fact).
// A word or two, the counters' own ("to fix", "to review"): a state, never a sentence.
const STATE_WORD = {
  problem: "To fix",
  warn: "To review",
  ok: "Passed",
  suggest: "Suggestion",
  info: "Info",
  "not-here": "Not here",
  accepted: "Accepted",
  fixed: "Repaired",
};
function copyBox(cmd) {
  const b = el("button", {
    type: "button",
    className: "action-btn small",
    textContent: "Copy",
  });
  b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      b.textContent = "Copied";
    } catch (_) {
      b.textContent = "Select it and copy";
    }
    setTimeout(() => {
      b.textContent = "Copy";
    }, 2000);
  });
  return el(
    "span",
    { className: "fdo-cmd" },
    el("code", { textContent: cmd }),
    b,
  );
}
// A finding's parts (irate_box/finding.py: summary, why, notes, evidence, fix, commands).
const findingParts = (finding) => ({
  detail: finding.summary || "",
  why: finding.why || "",
  notes: finding.notes || [],
  evidence: finding.evidence || [],
  commands: finding.commands || [],
});
// What the check saw (names, paths, packages): data, shown as it is.
const evidenceList = (items) =>
  items.length
    ? el(
        "ul",
        { className: "fevidence" },
        ...items.map((item) => el("li", {}, el("code", { textContent: item }))),
      )
    : null;
// r: {id, state, title, detail, why, notes[], evidence[], how, commands[], controls[], meta, said[], checks[], accept: {key, title, on} | null, note}
function findingRow(finding) {
  const state = finding.state || "warn";
  // Accept (or take back): the Security doctor's by default; another doctor gives its own words and request. It sits
  // with the card's other actions, before the folds of detail, not after them.
  const accept = finding.accept
    ? el("button", {
        type: "button",
        className: "action-btn small",
        textContent: finding.accept.on ? "Reopen" : finding.accept.label || "Accept risk",
        title: finding.accept.on
          ? "Return this finding to the unresolved list."
          : finding.accept.hint ||
            "Accepting changes the unresolved count. It does not change the security of the box. The finding moves to Accepted.",
        onclick: () =>
          (finding.accept.act || secAccept)(finding.accept.key, finding.accept.title, !finding.accept.on),
      })
    : null;
  const controls = [...(finding.controls || []), accept].filter(Boolean);
  return el(
    "article",
    { className: `finding finding-${state}`, id: finding.id || "" },
    el(
      "div",
      { className: "fhead" },
      // What it is about first, then how it stands (at the right), then when and where from on its own line.
      el("h4", { className: "ftitle", textContent: finding.title }),
      finding.badge || null,
      el("span", {
        className: `fstate fstate-${state}`,
        textContent: STATE_WORD[state] || state,
      }),
      finding.meta ? el("span", { className: "fmeta", textContent: finding.meta }) : null,
    ),
    finding.detail ? el("p", { className: "fdetail", textContent: finding.detail }) : null,
    finding.why ? el("p", { className: "fdetail fwhy", textContent: finding.why }) : null,
    ...(finding.notes || []).map((n) =>
      el("p", { className: "fdetail fnote", textContent: n }),
    ),
    evidenceList(finding.evidence || []),
    finding.how || controls.length || (finding.commands || []).length
      ? el(
          "div",
          { className: "fdo" },
          finding.how
            ? el(
                "p",
                { className: "fhow" },
                el("span", {
                  className: "fdo-label",
                  textContent:
                    state === "ok" || state === "fixed"
                      ? "Undo action: "
                      : "Recommended action: ",
                }),
                finding.how,
              )
            : null,
          (finding.commands || []).length
            ? el("div", { className: "fdo-cmds" }, ...finding.commands.map(copyBox))
            : null,
          controls.length
            ? el("div", { className: "fdo-controls" }, ...controls)
            : null,
        )
      : null,
    finding.note || null,
    // The deep audit's checks one by one (deepaudit.cis_findings): what each wants, what it found, its own fix.
    (finding.checks || []).length
      ? el(
          "details",
          { className: "fsaid fchecks" },
          el("summary", {
            textContent: `Each check, with its fix (${finding.checks.length})`,
          }),
          el(
            "ul",
            {},
            ...finding.checks.map((c) =>
              el(
                "li",
                {},
                el("strong", {
                  textContent: (c.check || "")
                    .replace(/^[\d.]+_/, "")
                    .replace(/_/g, " "),
                }),
                c.what ? ` — ${c.what}` : "",
                (c.msgs || []).length
                  ? el("span", {
                      className: "setting-desc",
                      textContent: ` Found: ${c.msgs.join(", ")}.`,
                    })
                  : null,
                c.say
                  ? el("span", {
                      className: "setting-desc",
                      textContent: ` ${c.say}`,
                    })
                  : null,
                c.cmd ? el("div", {}, copyBox(c.cmd)) : null,
              ),
            ),
          ),
        )
      : null,
    (finding.said || []).length
      ? el(
          "details",
          { className: "fsaid" },
          el("summary", { textContent: `Source results (${finding.said.length})` }),
          el(
            "ul",
            {},
            ...finding.said.map((l) =>
              el(
                "li",
                {},
                el("span", {
                  className: `fstate fstate-${l.status}`,
                  textContent: STATE_WORD[l.status],
                }),
                el("strong", {
                  textContent: ` ${SOURCE_WORDS[l.source] || l.source}: ${l.title}`,
                }),
                l.summary ? ` — ${l.summary}` : "",
                l.fix
                  ? el("span", {
                      className: "setting-desc",
                      textContent: ` Recommended action: ${l.fix}`,
                    })
                  : null,
              ),
            ),
          ),
        )
      : null,
  );
}
const noteUnder = (fid) =>
  secNote && secNote.fid === fid
    ? el("p", {
        className: `setting-desc action-note${secNote.ok ? "" : " bad"}`,
        role: "status",
        textContent: secNote.text,
      })
    : null;
async function secAccept(key, title, yes) {
  try {
    const reply = await postJSON("/admin/security", {
      action: yes ? "accept" : "unaccept",
      key,
      title,
    });
    if (secData) {
      secData.accepted = reply.accepted;
      renderSecurity(secData);
    }
  } catch (err) {
    say(err.message, false, sec.auditNote);
  }
}
let secRepair = null; // { id, label }: a repair asked from the Security doctor, answered there
async function healthRepair(choice, label) {
  try {
    secRepair = {
      id: (await postJSON("/admin/health", { action: "fix", choice })).id,
      label,
    };
    say(`${label}: waiting for the root helper.`, true, sec.auditNote);
    loadHealth();
  } catch (err) {
    say(err.message, false, sec.auditNote);
  }
}
// Told by each health report: the repair's place in the queue, then its answer, where it was asked.
function secRepairFollow(data) {
  if (!secRepair) return;
  const done = (data.results || []).find((result) => result.id === secRepair.id);
  const q = data.queue || {};
  if (done) {
    say(`${secRepair.label}: ${resultSaid(done)}`, done.ok, sec.auditNote);
    secRepair = null;
    return;
  }
  // Still a request file: queued (oldest first); gone but not answered: the helper has it.
  const ahead = (q.waiting || []).findIndex((x) => x.id === secRepair.id);
  say(
    `${secRepair.label}: ${ahead < 0 ? "running" : ahead ? `queued, ${ahead} ahead of it` : "next in the queue"}. The full queue is on the Box doctor.`,
    true,
    sec.auditNote,
  );
}

// A choice on the Security page, as a setting rather than a finding: its name, what it means, its options as
// chips with how it is now selected (actions with a group), and any other buttons. The status words and
// "what to do" are the doctors' (Security doctor), not this page's.
function secChipRows(finding, busy) {
  const groups = new Map();
  (finding.actions || [])
    .filter((action) => action.group)
    .forEach((action) => groups.set(action.group, [...(groups.get(action.group) || []), action]));
  return [...groups].map(([label, choices]) =>
    el(
      "div",
      { className: "aw-field" },
      el("span", { className: "aw-label", textContent: label }),
      el(
        "div",
        { className: "chip-group", role: "group", "aria-label": label },
        ...choices.map((choice) => {
          const sec = secSecs.get(finding.id);
          // Pending only in the group it was chosen in: the finding's other groups show how they are.
          const pend =
            sec && sec.dirty() && choices.some((c) => c.choice === sec.get("choice"))
              ? { a: { choice: sec.get("choice") } }
              : null;
          const sel = pend ? pend.a.choice === choice.choice : !!choice.on;
          const b = el("button", {
            type: "button",
            className:
              "chip" +
              (sel ? " selected" : "") +
              (pend && sel ? " pending" : ""),
            textContent: choice.label,
            disabled: busy,
            onclick: (event) => secChoose(finding, choice, event.currentTarget),
          });
          b.setAttribute("aria-pressed", String(sel));
          return b;
        }),
      ),
    ),
  );
}
// The Security page's subjects: where each of the box's choices lives.
const SEC_SUBJECTS = [
  [
    "exposure",
    "What guests can reach",
    "Ports that answer, the minimum rules between guests and the box, and what the box shares with other networks.",
  ],
  [
    "login",
    "Sign-in and root",
    "Who can sign in to the box itself, how, and what they can do as root.",
  ],
  [
    "system",
    "The system",
    "How the system under the box software is set up: kernel, package sources, logs and services.",
  ],
];
function secSubject(finding) {
  if (/^(port-|listen-|firewall|guest-net|cockpit|llmnr|share)/.test(finding.id))
    return "exposure";
  if (/^(ssh|sudo|root-|group-)/.test(finding.id)) return "login";
  return "system";
}
// One of the box's settings, not a finding: what it is now, its values with the one in effect marked, and
// Save and Discard. It stays in its place whatever it is set to. What the doctor makes of it is the
// Security doctor's, which links here; this says only that there is one.
function secChoiceCard(finding, busy, note) {
  const rows = secChipRows(finding, busy);
  const rest = { ...finding, actions: (finding.actions || []).filter((action) => !action.group) };
  const buttons = rest.actions.length
    ? el("div", { className: "library-buttons" }, ...scanButtons(rest, busy))
    : null;
  const parts = findingParts(finding);
  return el(
    "div",
    { className: "setting sec-choice", id: `sec-${finding.id}` },
    el("span", { className: "setting-name", textContent: finding.title }),
    el("span", { className: "setting-desc", textContent: parts.detail }),
    ...rows,
    buttons,
    // With nothing to choose here (no key for SSH yet, say), what would make a choice possible.
    !rows.length && !buttons && finding.status !== "ok" && finding.fix
      ? el("span", { className: "setting-desc", textContent: finding.fix })
      : null,
    parts.why
      ? el("span", { className: "setting-desc fwhy", textContent: parts.why })
      : null,
    ...parts.notes.map((n) =>
      el("span", { className: "setting-desc", textContent: n }),
    ),
    evidenceList(parts.evidence),
    // What to type where the page cannot put it in effect itself (the firewall's rules not loaded, say).
    finding.status !== "ok" && parts.commands.length
      ? el("div", { className: "fdo-cmds" }, ...parts.commands.map(copyBox))
      : null,
    finding.status !== "ok"
      ? el("a", {
          className: "setting-desc",
          href: "#secdoctor",
          textContent: "The Security doctor has a finding about this setting.",
        })
      : null,
    ...secFoot(finding),
    note,
  );
}
// A choice waiting for Save that the newest scan no longer offers (in effect now, or its finding gone): dropped,
// never sent.
function secPrune(scan) {
  if (!scan) return;
  for (const [fid, sec] of secSecs) {
    if (!sec.dirty() || sec.saving()) continue;
    const offer = secOffer(fid, sec.get("choice"));
    if (!offer || offer.on) sec.discard();
  }
}
// A program's ports, by protocol, runs of ports as a range: "TCP 443, 8080, 8090–8098".
function portRanges(ls) {
  const by = {};
  for (const l of ls) (by[l.proto] = by[l.proto] || new Set()).add(l.port);
  return Object.keys(by)
    .sort()
    .map((proto) => {
      const ps = [...by[proto]].sort((left, right) => left - right);
      const runs = [];
      for (const port of ps) {
        const run = runs[runs.length - 1];
        if (run && port === run[1] + 1) run[1] = port;
        else runs.push([port, port]);
      }
      return `${proto.toUpperCase()} ${runs.map(([run, b]) => (run === b ? `${run}` : b === run + 1 ? `${run}, ${b}` : `${run}–${b}`)).join(", ")}`;
    })
    .join("; ");
}
function renderSecurity(data) {
  secData = data;
  const scan = data.scan;
  // The setup tour's Cockpit step shows only where the box has it: the page's Cockpit setting.
  if (scan && window.TOUR)
    TOUR.has(
      "cockpit",
      (scan.findings || []).some((finding) => finding.id === "cockpit"),
    );
  const busy = data.pending > 0 || !!secWaiting;
  secPrune(scan);
  if (secWaiting) {
    const done = (data.results || []).find((result) => result.id === secWaiting.id);
    if (done) {
      const all = (secWaiting.ids || [secWaiting.id])
        .map((id) => (data.results || []).find((result) => result.id === id))
        .filter(Boolean);
      secNote = {
        fid: secWaiting.fid,
        text: [
          all.map(resultSaid).join(" "),
          secWaiting.keepNote ? secWaiting.keepNote.text : "",
        ]
          .filter(Boolean)
          .join(" "),
        ok: all.every((result) => result.ok) && !secWaiting.keepNote,
      };
      if (secWaiting.onDone)
        secWaiting.onDone(
          all.every((result) => result.ok),
          all
            .filter((result) => !result.ok)
            .map(resultSaid)
            .join(" "),
        );
      secWaiting = null;
      return renderSecurity(data);
    }
  }
  const lines = scan ? scan.findings : [];
  const shown = new Set(lines.map((finding) => finding.id));
  const by = (k) =>
    lines
      .filter((finding) => secKind(finding) === k)
      .sort((left, right) => RANK[left.status] - RANK[right.status]);
  // The real choices, by subject and in the scan's own order: each stays in its place whether it waits for a
  // choice or is set, so a setting can always be found and changed back.
  const choices = lines.filter((finding) => secKind(finding) === "choice");
  const waiting = choices.filter((finding) => finding.status !== "ok");
  const card = (finding) => secChoiceCard(finding, busy, noteUnder(finding.id));
  sec.findings.replaceChildren(
    ...(!scan
      ? [
          el("p", {
            className: "setting-desc",
            textContent: "Not scanned yet.",
          }),
        ]
      : SEC_SUBJECTS.map(([key, title, words]) => {
          const mine = choices.filter((finding) => secSubject(finding) === key);
          return mine.length
            ? el(
                "div",
                { className: "sec-subject", id: `security-${key}` },
                el("h3", { textContent: title }),
                el("p", { className: "setting-desc", textContent: words }),
                el("div", { className: "sec-settings" }, ...mine.map(card)),
              )
            : null;
        }).filter(Boolean)),
  );
  // The hub's own: what it can protect and can't, said truly (HTTPS on or off), with where it is changed.
  sec.hub.replaceChildren(
    ...data.hub.map((finding) =>
      el(
        "div",
        { className: "setting sec-choice", id: `sec-${finding.id}` },
        el("span", { className: "setting-name", textContent: finding.title }),
        // A limit is a state, set where the link goes: what it is now and why it matters, no recommended action.
        ...((parts) => [
          el("span", { className: "setting-desc", textContent: parts.detail }),
          parts.why ? el("span", { className: "setting-desc fwhy", textContent: parts.why }) : null,
          ...parts.notes.map((n) => el("span", { className: "setting-desc", textContent: n })),
        ])(findingParts(finding)),
        finding.do && finding.do.go
          ? el(
              "div",
              { className: "library-buttons" },
              el("a", {
                className: "action-btn go-btn",
                href: `#${finding.do.go}`,
                textContent: `Go to ${finding.do.where} →`,
              }),
            )
          : null,
      ),
    ),
  );
  // Debian's security updates, on Updates.
  // Automatic security updates: a choice of its own (off, download, install), not the System toolkit's.
  // Its badge (up to date / update available) is as fresh as the package lists it was read from.
  // Lists of unknown age say nothing either way, unless updates are waiting.
  const updBadge = (finding) =>
    finding.id !== "security-updates" ||
    (finding.lists_age_days == null && finding.status === "ok")
      ? null
      : AW.updatePill(
          Date.now() / 1000 - (finding.lists_age_days || 0) * 86400,
          finding.status !== "ok",
        );
  // The choice and the three buttons are the update pattern's, under the two lines.
  // State, not findings: what is waiting and how automatic updates stand. What to do about them is the pattern's
  // card below (its chips and buttons); what the doctor makes of them is the Updates doctor's.
  noteEl("updates-security").replaceChildren(
    ...(by("update").length
      ? by("update").map((finding) => {
          const parts = findingParts(finding);
          return el(
            "div",
            { className: "setting sec-choice", id: `sec-${finding.id}` },
            el(
              "span",
              { className: "setting-name" },
              finding.title,
              updBadge(finding) ? " " : "",
              updBadge(finding),
            ),
            el("span", { className: "setting-desc", textContent: parts.detail }),
            ...parts.notes.map((n) =>
              el("span", { className: "setting-desc", textContent: n }),
            ),
            evidenceList(parts.evidence),
            ...(finding.id === "security-updates"
              ? []
              : [...scanButtons(finding, busy), ...secFoot(finding)]),
            noteUnder(finding.id),
          );
        })
      : [
          el("p", {
            className: "setting-desc",
            textContent: scan ? "Nothing to say yet." : "Not scanned yet.",
          }),
        ]),
  );
  drawDebianPattern(lines, busy);
  // An answer whose line went away with the fix (Cockpit closed, say) shows under Scan again.
  const loose =
    secNote &&
    (secNote.fid === "scan" || !shown.has(secNote.fid)) &&
    secNote.fid !== "audit"
      ? secNote
      : null;
  say(loose ? loose.text : "", loose ? loose.ok : true, sec.note);
  // The security doctor is its own pane (Health), with its answer under its buttons.
  const auditSaid = secNote && secNote.fid === "audit" ? secNote : null;
  say(
    auditSaid ? auditSaid.text : "",
    auditSaid ? auditSaid.ok : true,
    sec.auditNote,
  );

  // Scanning only while a scan asked from here is out; anything else the helper has is said as such.
  const doing =
    secWaiting && secWaiting.fid === "scan" && !secWaiting.ids
      ? "Scanning…"
      : secWaiting
        ? "Saving. Waiting for the root helper…"
        : busy
          ? "The root helper is busy with other work. This report is from before that work."
          : "";
  sec.when.textContent =
    (scan
      ? `Last scanned ${new Date(scan.at * 1000).toLocaleString()}.`
      : "Not scanned yet.") + (doing ? ` ${doing}` : "");
  sec.scan.disabled = busy;
  sec.auditRun.disabled = busy;
  sec.auditDeep.disabled = busy;
  const deep = data.deep || {};
  sec.auditDeepWhen.textContent = deep.progress
    ? `Deep audit running: ${deep.progress.step} (${deep.progress.n} of ${deep.progress.total})…`
    : (deep.at
        ? `Last deep audit ${new Date(deep.at * 1000).toLocaleString()}, ${Math.round((deep.took || 0) / 60)} min. `
        : "No deep audit yet. ") +
      "It runs the CIS benchmark checks from debian-cis and Lynis. This takes about 8 minutes on a small board. It runs weekly while the Security kit is kept current.";
  renderAudit(data.audit, busy, data);
  renderImports(data.imports);
  // Network listeners: one row per program (its unit, else its process), not per port and protocol: its
  // ports, where it listens, what it belongs to, and the scan's word on how far it is exposed, with its
  // buttons. A UDP port in the kernel's range for outgoing sockets (32768 up) is a reply socket of a
  // program that has others (Avahi's queries, Syncthing's connections): not listed.
  const portLine = new Map(
    lines.filter((finding) => finding.id.startsWith("port-")).map((finding) => [finding.id, finding]),
  );
  const lineOf = (l) =>
    portLine.get(`port-${l.proto}-${l.port}`) ||
    portLine.get(`port-${l.proto === "tcp" ? "udp" : "tcp"}-${l.port}`);
  const progs = new Map();
  for (const l of (scan && scan.listeners) || []) {
    const key = l.unit || l.process || `${l.proto}/${l.port}`;
    progs.set(key, [...(progs.get(key) || []), l]);
  }
  sec.listeners.replaceChildren(
    ...[...progs.values()].map((all) => {
      const shown = all.some((l) => !(l.proto === "udp" && l.port >= 32768))
        ? all.filter(
            (l) =>
              !(l.proto === "udp" && l.port >= 32768) ||
              (lineOf(l) && lineOf(l).status !== "ok"),
          )
        : all;
      const fs = [...new Set(shown.map(lineOf).filter(Boolean))].sort(
        (left, right) => RANK[left.status] - RANK[right.status],
      );
      const worst = fs[0];
      const l0 = shown[0];
      const ssh = !worst && shown.some((l) => l.proto === "tcp" && l.port === 22);
      const cockpit = !worst && shown.some((l) => l.port === 9090 && (l.unit || "").startsWith("cockpit"));
      const said = [...new Set(fs.filter((finding) => finding.status === worst.status || fs.length === 1)
        .flatMap((finding) => [finding.summary, finding.why]).filter(Boolean))];
      const acts = fs.filter((finding) => (finding.actions || []).length);
      const owner = l0.owner || {};
      return el(
        "tr",
        {},
        el(
          "td",
          {},
          // The doctor's links go to a port's line: each port's anchor is in its program's row.
          ...fs.filter((finding) => secKind(finding) === "port").map((finding) => el("span", { id: `sec-${finding.id}` })),
          el("span", {
            className: "setting-name",
            textContent: worst ? worst.title : l0.name,
          }),
          el("span", { className: "setting-desc", textContent: l0.unit || l0.process || "" }),
        ),
        el("td", { textContent: portRanges(shown) }),
        el("td", { textContent: [...new Set(shown.flatMap((l) => l.addr.split(", ")))].join(", ") }),
        el("td", {
          className: owner.kind === "unknown" ? "bad" : "",
          textContent:
            owner.kind === "app"
              ? `${owner.name} (an app or add-on)`
              : owner.kind === "hub"
                ? "irate-box"
                : owner.kind === "package"
                  ? `Debian package ${owner.name}`
                  : owner.kind === "unknown"
                    ? `Not known. No package owns ${owner.name || "this program"}. If nothing needs it, stop it.`
                    : "",
        }),
        el(
          "td",
          {},
          worst
            ? el("span", {
                className: `fstate fstate-${worst.status}`,
                textContent: worst.status === "ok" ? "Yes" : STATE_WORD[worst.status],
              })
            : null,
          said.length ? el("span", { className: "setting-desc", textContent: ` ${said.join(" ")}` }) : null,
          ssh
            ? el("a", { href: "#sec-ssh-password", textContent: "Who can sign in, and how: SSH settings" })
            : null,
          cockpit ? el("a", { href: "#sec-cockpit", textContent: "Cockpit setting" }) : null,
          worst && worst.fix && worst.status !== "ok"
            ? el("span", { className: "setting-desc", textContent: ` ${worst.fix}` })
            : null,
          ...acts.map((finding) =>
            el(
              "span",
              { className: "library-buttons" },
              acts.length > 1 ? el("span", { className: "setting-desc", textContent: `${portRanges(shown.filter((l) => lineOf(l) === finding))}: ` }) : null,
              // A program's own buttons once (Stop and disable): a port's stay with its port.
              ...scanButtons(
                { ...finding, actions: finding.actions.filter((action) => finding === acts.find((g) => g.actions.some((b) => b.choice === action.choice))) },
                busy,
              ),
            ),
          ),
          ...fs.flatMap((finding) => [...secFoot(finding), noteUnder(finding.id)]),
        ),
      );
    }),
  );
  sec.output.hidden = !data.log.length;
  sec.log.textContent = data.log.join("\n");
  const problems = waiting.filter((finding) => finding.status === "problem").length;
  const ports = lines.filter(
    (finding) => finding.id.startsWith("port-") && finding.status !== "ok",
  ).length;
  sec.counts.replaceChildren(
    ...[
      [
        ports,
        `port${ports === 1 ? "" : "s"} to review`,
        ports ? "warn" : "ok",
        "security-ports",
      ],
    ].map(([n, word, st, to]) =>
      el(
        "a",
        { className: `sec-count sec-count-${st}`, href: `#${to}` },
        el("strong", { textContent: String(n) }),
        ` ${word}`,
      ),
    ),
  );
  badge("security", problems ? String(problems) : "");
  const updProblems = by("update").filter((finding) => finding.status === "problem").length;
  badge("updates-security", updProblems ? String(updProblems) : ""); // Needs attention words it
  if (scan)
    setupStep(
      "security",
      problems
        ? `${problems} item${problems === 1 ? "" : "s"} to fix or accept.`
        : "Nothing to fix.",
      problems ? "problem" : "ok",
    );

  const stale = !scan || Date.now() / 1000 - scan.at > 15 * 60;
  if (stale && !busy && !secAsked) {
    secAsked = true;
    secRequest({ action: "scan" }, "scan");
    return;
  }
  clearTimeout(secPoll);
  if (busy) secPoll = setTimeout(loadSecurity, 2000);
}

// --- the joint report (secdoctor.joint): what several sources say about one thing, once ------------
const SOURCE_WORDS = {
  doctor: "the doctor",
  "security-page": "the Security page",
  debsecan: "debsecan",
  "debian-cis": "debian-cis",
  lynis: "Lynis",
  openvas: "OpenVAS",
  nmap: "nmap",
};
// How old each source may be before the strip warns (days): the deep audit runs weekly, debsecan's
// tracker data is fetched daily, the Security page scans when opened, imported scans are by hand.
const STALE_DAYS = {
  doctor: 1,
  "security-page": 1,
  debsecan: 3,
  "debian-cis": 8,
  lynis: 8,
  openvas: 30,
  nmap: 30,
};
const srcWords = (list) => list.map((x) => SOURCE_WORDS[x] || x).join(", ");
let jointShow = "all"; // the counters double as the list's filter: all, problem, warn
const jointSearch = document.getElementById("joint-search");
jointSearch.addEventListener(
  "input",
  () => secData && renderJoint(secData.audit && secData.audit.joint, secData),
);

// What to do about one item: the Security page's buttons for its lines (from its scan now), the box
// doctor's repair, the deep audit, a link to where it is chosen, a command; and the words for it.
function jointDo(i, data, busy) {
  const scanBy = new Map(
    ((data.scan || {}).findings || []).map((finding) => [finding.id, finding]),
  );
  const page = (i.page || []).map((id) => scanBy.get(id)).filter(Boolean);
  const seen = new Set(),
    controls = [];
  for (const finding of page) {
    // A repair with one right answer is done here; an ongoing choice is the Security page's (or Updates'),
    // where it can be seen and changed back: the doctor links to it rather than changing it itself.
    if (secKind(finding) !== "cure") {
      if ((finding.actions || []).length || finding.status !== "ok") {
        const [to, where] =
          secKind(finding) === "update"
            ? ["updates-security", "Updates"]
            : [`sec-${finding.id}`, "Security"];
        if (!seen.has(to)) {
          seen.add(to);
          controls.push(
            el("a", {
              className: "action-btn go-btn",
              href: `#${to}`,
              textContent: `Go to ${where} →`,
            }),
          );
        }
      }
      continue;
    }
    for (const action of finding.actions || []) {
      if (seen.has(action.choice)) continue;
      seen.add(action.choice);
      controls.push(
        el("button", {
          type: "button",
          className: "action-btn",
          textContent: action.label,
          disabled: busy,
          onclick: () => secFix(finding.id, action),
        }),
      );
    }
  }
  const todo = i.do || {};
  if (todo.repair === "rerun-install") {
    controls.push(
      el("button", {
        type: "button",
        className: "action-btn",
        textContent: "Run the installer again",
        disabled: busy,
        onclick: () =>
          healthRepair(
            "rerun-install",
            "Running the installer again",
            "Run the installer with this box's recorded options? The installer can repair, upgrade and restart services.",
          ),
      }),
    );
  }
  // A Box doctor action that puts it right (secdoctor_xref: its choice and words), as a button here.
  if (todo.health) {
    controls.push(
      el("button", {
        type: "button",
        className: "action-btn",
        textContent: todo.health.label,
        disabled: busy,
        onclick: () =>
          healthRepair(
            todo.health.choice,
            todo.health.label,
            `${todo.health.label}? ${todo.say || ""}`.trim(),
          ),
      }),
    );
  }
  if (todo.act === "deep")
    controls.push(
      el("button", {
        type: "button",
        className: "action-btn",
        textContent: "Run the deep audit",
        disabled: busy,
        onclick: () => sec.auditDeep.click(),
      }),
    );
  if (todo.go)
    controls.push(
      el("a", {
        className: "action-btn go-btn",
        href: `#${todo.go}`,
        textContent: `Go to ${todo.where} →`,
      }),
    );
  const cmd = todo.cmd || i.cmd;
  if (cmd && !(i.commands || []).includes(cmd)) controls.push(copyBox(cmd));
  // The words: the hub's own work said as such; else the Security page's (it goes with its buttons); else the item's.
  const pageFix = page.map((finding) => finding.fix).find(Boolean);
  const how =
    todo.hub ||
    todo.say ||
    (page.length && page.some((finding) => (finding.actions || []).length)
      ? pageFix || ""
      : "") ||
    i.fix ||
    (controls.length ? "" : "Information only.");
  // Put right since the doctor ran: every Security page line in it is fine now.
  const fixed =
    page.length > 0 &&
    page.length === (i.page || []).length &&
    page.every((finding) => finding.status === "ok");
  return { controls, how, fixed, page };
}

function jointRow(i, data, busy, accepted) {
  const { controls, how, fixed } = jointDo(i, data, busy);
  const isNew = ((data.audit || {}).new || []).some((id) =>
    i.lines.some((l) => l.id === id),
  );
  const meta = [
    i.area,
    i.sources.length > 1
      ? `${i.sources.length} sources agree`
      : `from ${srcWords(i.sources)}`,
    i.alone ? `${srcWords(i.could_see)} could have seen it and did not` : "",
    isNew ? "new since the last run" : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const said =
    i.lines.length > 1 || (i.lines[0] && i.lines[0].summary !== i.summary)
      ? i.lines
      : [];
  // Information only (secdoctor_xref.INFO): said as that, with why, in place of an action there is none of.
  const infoHow =
    i.tier === "info"
      ? `Information only: nothing you can change on this box fixes this. ${i.info || ""}`.trim()
      : "";
  return findingRow({
    id: `find-${i.key.replace(/[^A-Za-z0-9_-]/g, "-")}`,
    state: accepted ? "accepted" : fixed ? "fixed" : i.tier || i.status,
    title: i.title,
    ...findingParts(i),
    how: fixed
      ? "Repaired here since the doctor last ran. It leaves the list at the next run."
      : infoHow || how,
    controls,
    meta,
    said,
    checks: i.checks,
    note: (i.page || []).map(noteUnder).find(Boolean) || null,
    accept: { key: i.key, title: i.title, on: !!accepted },
  });
}

function renderJoint(j, data) {
  const box = (id) => document.getElementById(id);
  const busy = data.pending > 0 || !!secWaiting;
  const accepted = data.accepted || {};
  const all = j ? j.items || [] : [];
  const ok = (i) => !accepted[i.key];
  const fixedNow = (i) => jointDo(i, data, busy).fixed;
  const real = all.filter((i) => !i.tier && ok(i));
  const counts = {
    problem: real.filter((i) => i.status === "problem" && !fixedNow(i)).length,
    warn: real.filter((i) => i.status === "warn" && !fixedNow(i)).length,
  };
  const needle = jointSearch.value.trim().toLowerCase();
  const words = (finding) => [
    finding.title,
    finding.summary,
    finding.why,
    ...(finding.notes || []),
    ...(finding.evidence || []),
    finding.fix,
  ];
  const match = (i) =>
    !needle ||
    [...words(i), i.area, ...i.lines.flatMap(words)]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(needle);
  const pick = (i) =>
    match(i) &&
    (jointShow === "all" || (i.status === jointShow && !fixedNow(i)));
  box("joint-summary").replaceChildren(
    ...(!j
      ? [
          el("p", {
            className: "setting-desc",
            textContent: "Run the doctor to see it.",
          }),
        ]
      : [
          [
            "all",
            `${counts.problem + counts.warn}`,
            "all",
            counts.problem ? "problem" : counts.warn ? "warn" : "ok",
          ],
          [
            "problem",
            `${counts.problem}`,
            "to fix",
            counts.problem ? "problem" : "ok",
          ],
          ["warn", `${counts.warn}`, "to review", counts.warn ? "warn" : "ok"],
        ].map(([key, n, word, st]) => {
          const b = el(
            "button",
            {
              type: "button",
              className: `sec-count sec-count-${st}${jointShow === key ? " active" : ""}`,
              onclick: () => {
                jointShow = key;
                renderJoint(j, data);
              },
            },
            el("strong", { textContent: n }),
            ` ${word}`,
          );
          b.setAttribute("aria-pressed", String(jointShow === key));
          return b;
        })),
  );
  const list = real.filter(pick);
  box("joint-items").replaceChildren(
    ...list.map((i) => jointRow(i, data, busy, null)),
  );
  box("joint-empty").hidden = !j || list.length > 0;
  box("joint-empty").textContent = real.length
    ? "Nothing matches."
    : "Nothing to fix or review.";
  const fold = (id, items, row) => {
    box(`${id}-fold`).hidden = !items.length;
    box(`${id}-count`).textContent = `(${items.length})`;
    box(id).replaceChildren(...items.map(row));
  };
  fold(
    "joint-suggest",
    all.filter((i) => i.tier === "suggest" && ok(i) && match(i)),
    (i) => jointRow(i, data, busy, null),
  );
  fold(
    "joint-info",
    all.filter((i) => i.tier === "info" && ok(i) && match(i)),
    (i) => jointRow(i, data, busy, null),
  );
  fold(
    "joint-nothere",
    all.filter((i) => i.tier === "not-here" && ok(i) && match(i)),
    (i) => jointRow(i, data, busy, null),
  );
  fold(
    "joint-accepted",
    all.filter((i) => !ok(i)),
    (i) => jointRow(i, data, busy, accepted[i.key]),
  );
  // What the box's own scan has put right with a cure (one right answer), each with its Undo.
  const cured = ((data.scan || {}).findings || []).filter(
    (finding) =>
      secKind(finding) === "cure" && finding.status === "ok" && (finding.actions || []).length,
  );
  fold("joint-cured", cured, (finding) =>
    findingRow({
      id: `sec-cure-${finding.id}`,
      state: "ok",
      title: finding.title,
      ...findingParts(finding),
      how: "Done from here. Use the button to restore the previous setting.",
      controls: scanButtons(finding, busy),
      note: noteUnder(finding.id),
    }),
  );
  // One strip at the top: each source, its age, and a warning once it is older than it
  // should be; what it covers and what it said in the pill's title.
  const fresh = (j && j.freshness) || {};
  const now = Date.now() / 1000;
  // Said as a line of text (a badge is for a state, not a sentence); a source too old is marked as a warning.
  const srcs = Object.keys((j && j.sources) || {});
  box("joint-fresh").replaceChildren(
    ...(srcs.length ? ["Sources: "] : []),
    ...srcs.flatMap((src, i) => {
      const c = j.sources[src],
        at = fresh[src],
        old = !at || now - at > (STALE_DAYS[src] || 30) * 86400;
      return [
        i ? ", " : "",
        el("span", {
          className: old ? "fresh-src warn" : "fresh-src",
          title:
            `${(j.coverage || {})[src] || ""} To fix: ${c.problem || 0}. To review: ${c.warn || 0}.`.trim(),
          textContent: `${SOURCE_WORDS[src] || src} ${at ? ago(now - at) : "never"}${old ? " (out of date)" : ""}`,
        }),
      ];
    }),
    ...(srcs.length ? ["."] : []),
  );
  return counts;
}

// Scan reports, read here in the browser: only what they found goes to the box (secimports.py).
function xmlDoc(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length)
    throw new Error(
      "Unsupported XML report. This page cannot read this file as XML.",
    );
  return doc;
}
const kid = (node, name) => {
  const n = node && [...node.children].find((c) => c.tagName === name);
  return n ? n.textContent.trim() : "";
};
function parseNmapXml(text) {
  const doc = xmlDoc(text);
  if (doc.documentElement.tagName !== "nmaprun")
    throw new Error(
      "Unsupported XML report. Select an nmap XML report created with -oX.",
    );
  const results = [];
  for (const host of doc.getElementsByTagName("host")) {
    const addr = (
      host.getElementsByTagName("address")[0] || { getAttribute: () => "" }
    ).getAttribute("addr");
    for (const portEl of host.getElementsByTagName("port")) {
      const st = portEl.getElementsByTagName("state")[0];
      const sv = portEl.getElementsByTagName("service")[0];
      results.push({
        port: Number(portEl.getAttribute("portid")),
        proto: portEl.getAttribute("protocol"),
        host: addr,
        state: st ? st.getAttribute("state") : "",
        service: sv
          ? [
              sv.getAttribute("name"),
              sv.getAttribute("product"),
              sv.getAttribute("version"),
            ]
              .filter(Boolean)
              .join(" ")
          : "",
      });
    }
  }
  const start = Number(doc.documentElement.getAttribute("start")) || null;
  return {
    kind: "nmap",
    ran: start,
    results: results.filter(
      (result) => result.state === "open" || result.state === "open|filtered",
    ),
  };
}
function splitPort(text) {
  const [port, proto] = String(text || "").split("/");
  return {
    port: /^\d+$/.test(port) ? Number(port) : "general",
    proto: (proto || "tcp").toLowerCase() === "udp" ? "udp" : "tcp",
  };
}
function parseOpenvasXml(text) {
  const doc = xmlDoc(text);
  const results = [];
  for (const result of doc.getElementsByTagName("result")) {
    const nvt = [...result.children].find((c) => c.tagName === "nvt");
    if (!nvt) continue;
    const tags = kid(nvt, "tags");
    const tag = (name) =>
      (
        (tags.match(new RegExp(`(?:^|\\|)${name}=([^|]*)`)) || [])[1] || ""
      ).trim();
    const cvss = parseFloat(kid(result, "severity") || kid(nvt, "cvss_base")) || 0;
    if (cvss <= 0) continue;
    const cves = [...nvt.getElementsByTagName("ref")]
      .filter((x) => x.getAttribute("type") === "cve")
      .map((x) => x.getAttribute("id"))
      .concat(kid(nvt, "cve").match(/CVE-\d{4}-\d{4,7}/g) || []);
    results.push({
      ...splitPort(kid(result, "port")),
      host:
        (
          result.getElementsByTagName("host")[0] || { firstChild: null }
        ).firstChild?.textContent?.trim() || "",
      cvss,
      name: kid(nvt, "name"),
      summary: tag("summary") || kid(result, "description").slice(0, 600),
      solution: kid(nvt, "solution") || tag("solution"),
      cves: [...new Set(cves)].slice(0, 50),
    });
  }
  const start = Date.parse(
    (doc.getElementsByTagName("scan_start")[0] || {}).textContent || "",
  );
  return {
    kind: "openvas",
    ran: Number.isFinite(start) ? start / 1000 : null,
    results: results.slice(0, 1000),
  };
}
function csvRows(text) {
  const rows = [];
  let row = [],
    field = "",
    q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else q = false;
      } else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += c;
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((cells) => cells.some((x) => x.trim()));
}
function parseOpenvasCsv(text) {
  const [head, ...rows] = csvRows(text);
  const ix = (name) => head.findIndex((h) => h.trim().toLowerCase() === name);
  const col = {
    ip: ix("ip"),
    port: ix("port"),
    proto: ix("port protocol"),
    cvss: ix("cvss"),
    name: ix("nvt name"),
    summary: ix("summary"),
    solution: ix("solution"),
    cves: ix("cves"),
    time: ix("timestamp"),
  };
  if (col.cvss < 0 || col.name < 0)
    throw new Error("OpenVAS CSV must include CVSS and NVT Name columns.");
  const results = rows
    .map((row) => ({
      port: /^\d+$/.test(row[col.port] || "") ? Number(row[col.port]) : "general",
      proto: (row[col.proto] || "tcp").toLowerCase() === "udp" ? "udp" : "tcp",
      host: row[col.ip] || "",
      cvss: parseFloat(row[col.cvss]) || 0,
      name: row[col.name] || "",
      summary: row[col.summary] || "",
      solution: col.solution >= 0 ? row[col.solution] : "",
      cves: (
        (col.cves >= 0 ? row[col.cves] : "").match(/CVE-\d{4}-\d{4,7}/g) || []
      ).slice(0, 50),
    }))
    .filter((port) => port.cvss > 0);
  const t = col.time >= 0 && rows[0] ? Date.parse(rows[0][col.time]) : NaN;
  return {
    kind: "openvas",
    ran: Number.isFinite(t) ? t / 1000 : null,
    results: results.slice(0, 1000),
  };
}
function parseScanReport(text, name) {
  const t = text.replace(/^\uFEFF/, "").trimStart();
  const report = !t.startsWith("<")
    ? parseOpenvasCsv(t)
    : /<nmaprun[\s>]/.test(t.slice(0, 4000))
      ? parseNmapXml(t)
      : parseOpenvasXml(t);
  return { ...report, name: name || "" };
}

function renderAudit(audit, busy, data) {
  // The badge counts what is to fix after merging, less what is accepted or put right since.
  const counts = renderJoint(audit && audit.joint, data);
  badge(
    "secdoctor",
    audit
      ? String((audit.joint ? counts.problem : audit.counts.problem) || "")
      : "",
  );
  if (!audit) {
    sec.auditWhen.textContent = busy ? "Running…" : "Not run yet.";
    sec.auditSteps.replaceChildren();
    sec.auditScope.hidden = true;
    return;
  }
  sec.auditWhen.textContent =
    `Last run ${new Date(audit.at * 1000).toLocaleString()}` +
    ((audit.new || []).length
      ? `, ${audit.new.length} new since ${new Date(audit.previous_at * 1000).toLocaleDateString()}`
      : "") +
    (audit.root
      ? "."
      : ". Not run as root: some checks could not read what they need.") +
    (busy ? " Running…" : "");
  // The raw report: every step's checks as the doctor wrote them, the fine ones too, all folded.
  sec.auditSteps.replaceChildren(
    ...audit.steps.map((st) => {
      const worst = st.findings.some((finding) => finding.status === "problem")
        ? "problem"
        : st.findings.some((finding) => finding.status === "warn")
          ? "warn"
          : "ok";
      const lines = [...st.findings].sort(
        (left, right) => RANK[left.status] - RANK[right.status],
      );
      return el(
        "details",
        { className: "admin-output" },
        el(
          "summary",
          {},
          el("span", {
            className: `fstate fstate-${worst}`,
            textContent: STATE_WORD[worst],
          }),
          ` ${st.title}${st.ref ? ` (${st.ref})` : ""}`,
        ),
        el(
          "ul",
          { className: "raw-lines" },
          ...lines.map((finding) =>
            el(
              "li",
              {},
              el("span", {
                className: `fstate fstate-${finding.status}`,
                textContent: STATE_WORD[finding.status],
              }),
              " ",
              el("strong", { textContent: finding.title }),
              (audit.new || []).includes(finding.id)
                ? el("span", {
                    className: "badge-push bad",
                    textContent: "new",
                  })
                : null,
              finding.ref
                ? el("span", {
                    className: "setting-desc",
                    textContent: ` [${finding.ref}]`,
                  })
                : null,
              el("span", {
                textContent: ` — ${[finding.summary, finding.why, ...(finding.notes || [])].filter(Boolean).join(" ")}`,
              }),
              (finding.evidence || []).length
                ? el("span", {
                    className: "setting-desc",
                    textContent: ` (${finding.evidence.join(", ")})`,
                  })
                : null,
              finding.fix
                ? el("span", {
                    className: "setting-desc",
                    textContent: ` Recommended action: ${finding.fix}`,
                  })
                : null,
              ...(finding.status !== "ok" ? (finding.commands || []).map(copyBox) : []),
            ),
          ),
        ),
      );
    }),
  );
  sec.auditScope.hidden = !(audit.not_covered || []).length;
  sec.auditNot.replaceChildren(
    ...(audit.not_covered || []).map((t) => el("li", { textContent: t })),
  );
}

async function loadSecurity() {
  try {
    renderSecurity(AW.localise(await getJSON("/admin/security")));
  } catch (err) {
    console.error("security pane:", err);
    sec.when.textContent =
      "Could not read the security report. Reload the page to try again.";
  }
}

// onDone(ok, why): told whether the root helper did it (a draft cleared only then), or false when the hub refused it.
async function secRequest(body, fid, onDone) {
  try {
    secWaiting = {
      id: (await postJSON("/admin/security", body)).id,
      fid,
      onDone,
    };
    secNote = null;
    loadSecurity();
  } catch (err) {
    secNote = { fid, text: err.message, ok: false };
    if (onDone) onDone(false, err.message);
    loadSecurity();
  }
}

// Debian's security updates as the update pattern: how often apt looks, what it does with what
// it finds (apt's periodic work, unattended-upgrades to install), and Check now / Download / Install by hand.
const deb = {
  form: document.getElementById("debian-pattern"),
  note: noteEl("debian-pattern-note"),
  box: document.getElementById("debian-buttons"),
  says: document.getElementById("debian-auto-says"),
};
// The hub card's buttons, by how far Debian's update has got: nothing known (each button checks first), updates
// waiting (download, or download and install), downloaded (install). The labels follow in drawDebianPattern.
const DEB_INSTALL_ASK =
  "Install the waiting security updates now? It can take several minutes on this board.";
deb.buttons = AW.updateButtons({
  check: {
    label: "Check for updates",
    onclick: () => secFix("security-updates", { choice: "security-check" }),
  },
  fetch: {
    label: "Check for updates and download",
    onclick: () =>
      secFix("security-updates", {
        choice: deb.waiting ? "security-fetch" : "security-reach-fetch",
      }),
  },
  install: {
    label: "Check for updates, download and install",
    onclick: () =>
      secFix("security-updates", {
        choice: deb.waiting ? "security-updates" : "security-reach-install",
        confirm: DEB_INSTALL_ASK,
      }),
  },
});
deb.box.replaceChildren(deb.buttons);
function drawDebianPattern(lines, busy) {
  const unattended = lines.find((finding) => finding.id === "unattended"),
    w = lines.find((finding) => finding.id === "security-updates");
  const pat = unattended && unattended.pattern;
  if (pat) debSec.fill({ often: pat.often, act: pat.act });
  deb.says.textContent = pat
    ? AW.patternSays(pat.often, pat.act) +
      (debSec.dirty() ? " (Changed below, not saved.)" : "")
    : "Not known until the box is scanned.";
  const waiting = w ? w.waiting || 0 : 0,
    fetched = w ? w.fetched || 0 : 0;
  deb.waiting = waiting;
  const down = waiting > 0 && fetched >= waiting;
  deb.buttons.update({
    check: {
      label: "Check for updates",
      disabled: busy || waiting > 0,
      why: busy ? "busy" : waiting > 0 ? "updates are already waiting." : "",
    },
    fetch: {
      label: waiting ? "Download" : "Check for updates and download",
      disabled: busy || down,
      why: busy ? "busy" : down ? "already downloaded." : "",
    },
    install: {
      label: down
        ? "Install"
        : waiting
          ? "Download and install"
          : "Check for updates, download and install",
      disabled: busy,
      why: busy ? "busy" : "",
    },
  });
}
// Saved once the root helper has done it: until then "Saving…", and a refusal or a failure keeps the choice.
const debSec = AW.form(deb.form, {
  label: "Debian's security updates",
  save: (c, v) => {
    const j = AW.job();
    secRequest(
      { action: "fix", choice: `autoupdate-set:${v.often}-${v.act}` },
      "unattended",
      j.done,
    );
    return j;
  },
});

function secFix(fid, action) {
  secRequest({ action: "fix", choice: action.choice }, fid);
}

sec.scan.addEventListener("click", () =>
  secRequest({ action: "scan" }, "scan"),
);
sec.auditRun.addEventListener("click", () =>
  secRequest({ action: "audit" }, "audit"),
);
sec.auditDeep.addEventListener("click", () =>
  secRequest({ action: "deep" }, "audit"),
);
const importNote = document.getElementById("import-note");
document.getElementById("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = event.target.elements.file.files[0];
  if (!file) return;
  try {
    if (file.size > 64 * 2 ** 20)
      throw new Error(
        "The file is larger than the maximum report size of 64 MB. Export fewer results.",
      );
    const text = file.text
      ? await file.text()
      : await new Promise((ok, no) => {
          const reader = new FileReader();
          reader.onload = () => ok(reader.result);
          reader.onerror = () => no(reader.error);
          reader.readAsText(file);
        });
    const report = parseScanReport(text, file.name);
    const reply = await postJSON("/admin/security", { action: "import", report });
    say(
      `${report.kind === "nmap" ? "nmap" : "OpenVAS"}: ${report.results.length} results read. ${resultSaid(reply) || ""}`,
      true,
      importNote,
    );
    secWaiting = { id: reply.id, fid: "audit" };
    loadSecurity();
  } catch (err) {
    say(err.message, false, importNote);
  }
});
function renderImports(imports) {
  document.getElementById("import-list").replaceChildren(
    ...Object.entries(imports || {}).map(([kind, i]) =>
      el(
        "li",
        { className: "admin-item" },
        el(
          "span",
          {},
          el("strong", { textContent: kind === "nmap" ? "nmap" : "OpenVAS" }),
          el("span", {
            className: "setting-desc",
            textContent: ` ${i.name || ""}: ${i.results} results. Scan time: ${i.ran ? new Date(i.ran * 1000).toISOString().slice(0, 10) : "?"}. Import time: ${new Date(i.imported * 1000).toISOString().slice(0, 10)}.`,
          }),
        ),
        actionButton(
          "Remove",
          async () => {
            try {
              const reply = await postJSON("/admin/security", {
                action: "import-remove",
                kind,
              });
              secWaiting = { id: reply.id, fid: "audit" };
              loadSecurity();
            } catch (err) {
              say(err.message, false, importNote);
            }
          },
          { className: "small" },
        ),
      ),
    ),
  );
}
loadSecurity();

// A "Go to …" link (the doctor's, the Security page's) names the very line or section: open the folds
// around it, bring it into view and mark it as the setup tour does, so the owner lands on the thing to
// change. A doctor's link may name a setting inside the section that is drawn only once the page's data
// has come (a connection's tick): it is waited for, a few seconds, and marked instead.
let landMark = null;
function landOn() {
  const section =
    location.hash.length > 1 && document.getElementById(location.hash.slice(1));
  const mark = landMark;
  landMark = null;
  if (
    !section ||
    ((section.classList.contains("admin-pane") ||
      section.classList.contains("admin-page")) &&
      !mark)
  )
    return;
  const open = (t) => {
    for (
      let details = t.closest("details");
      details;
      details = details.parentElement && details.parentElement.closest("details")
    )
      details.open = true;
    if (t.tagName === "DETAILS") t.open = true;
  };
  open(section);
  let tries = 0;
  const go = () => {
    const target = (mark && document.getElementById(mark)) || null;
    if (mark && !target && tries++ < 50) {
      setTimeout(go, 100);
      return;
    }
    const t = target && !target.closest("[hidden]") ? target : section;
    open(t);
    if (t.scrollIntoView) t.scrollIntoView({ block: "center" });
    t.classList.add("landed");
    const off = () => {
      t.classList.remove("landed");
      t.removeEventListener("change", off, true);
    };
    t.addEventListener("change", off, true);
    setTimeout(off, 6000);
  };
  setTimeout(go, 50);
}
window.addEventListener("hashchange", landOn);
// A link to a section, marking one setting inside it once there.
function goLink(go) {
  return el("a", {
    className: "action-btn go-btn",
    href: `#${go.section}`,
    textContent: `Go to ${go.where} →`,
    onclick: () => {
      landMark = go.mark || null;
      if (location.hash === `#${go.section}`) setTimeout(landOn, 0);
    },
  });
}

// --- health ----------------------------------------------------------------------------
// The box doctor (health.py, through the root helper): findings with what to do and the safe
// repairs as buttons; the last install's record and output, which the hub reads itself. Polled
// on every pane, slowly, for one more thing the hub can tell on its own: whether the root
// helper is answering at all. If not, a banner on every pane gives the commands to type, since
// no button that needs root (the doctor's included) can do anything until it is back.
const hl = {
  when: document.getElementById("health-when"),
  scan: document.getElementById("health-scan"),
  note: document.getElementById("health-note"),
  progress: document.getElementById("health-progress"),
  bar: document.getElementById("health-bar"),
  step: document.getElementById("health-step"),
  findings: document.getElementById("health-findings"),
  summary: document.getElementById("health-summary"),
  search: document.getElementById("health-search"),
  empty: document.getElementById("health-empty"),
  fine: document.getElementById("health-fine"),
  glance: document.getElementById("health-glance"),
  setGroups: document.getElementById("health-settings-groups"),
  setFoot: document.getElementById("health-settings-foot"),
  setNote: document.getElementById("health-settings-note"),
  fineFold: document.getElementById("health-fine-fold"),
  fineCount: document.getElementById("health-fine-count"),
  install: document.getElementById("health-install"),
  output: document.getElementById("health-output"),
  log: document.getElementById("health-log"),
  clockWhen: document.getElementById("clock-when"),
  clockScan: document.getElementById("clock-scan"),
  clockNote: document.getElementById("clock-note"),
  clockFindings: document.getElementById("clock-findings"),
  banner: document.getElementById("helper-banner"),
  bannerDetail: document.getElementById("helper-banner-detail"),
  bannerCmds: document.getElementById("helper-banner-cmds"),
  busyLine: document.getElementById("helper-busy"),
  busyLog: document.getElementById("helper-busy-log"),
};
let hlWaiting = null; // { id, fid }
let hlNote = null; // { fid, text, ok }
let hlPoll = null;
let hlAsked = false;
// A report's age in words once it is an hour old ("Looked …, 4 hours ago"): what it says may be out of date.
function agoText(at) {
  const h = Math.floor((Date.now() / 1000 - at) / 3600);
  return h < 1
    ? ""
    : h < 48
      ? `, ${h} hour${h === 1 ? "" : "s"} ago`
      : `, ${Math.floor(h / 24)} days ago`;
}

function installText(st) {
  if (!st)
    return "No record yet. Older installations kept none. The next run of the installer keeps one.";
  const when = new Date(st.started * 1000).toLocaleString();
  const n = (st.problems || []).length;
  if (st.running)
    return `Started ${when}${st.step ? `, at "${st.step}"` : ""}. If nothing is installing now, the installation was interrupted there. The doctor also reports this.`;
  if (st.aborted)
    return `Started ${when}. Stopped during "${st.step}"${st.failed_command ? `: ${st.failed_command} failed (exit ${st.exit})` : ""}. The steps after it were not done.`;
  return `Started ${when} (${st.args || "no options"}). Finished${n ? ` with ${n} problem${n === 1 ? "" : "s"}: ${st.problems.join(", ")}` : " with no problems"}.`;
}

// What to do about a finding of the box doctor (and the update doctor): its repair buttons, its setting's choices, the
// place to go and the commands it names, and its words. A fine finding says nothing more.
// A setting's choices (actions with a group): one row of chips each, the current one selected, shown
// whatever the finding's state, since a setting is worth seeing when all is well too.
function healthChips(finding, busy) {
  const groups = new Map();
  (finding.actions || [])
    .filter((action) => action.group)
    .forEach((action) => groups.set(action.group, [...(groups.get(action.group) || []), action]));
  return [...groups].map(([label, choices]) =>
    el(
      "div",
      { className: "aw-field" },
      el("span", { className: "aw-label", textContent: label }),
      el(
        "div",
        { className: "chip-group", role: "group", "aria-label": label },
        ...choices.map((choice) => {
          const b = el("button", {
            type: "button",
            className: "chip" + (choice.on ? " selected" : ""),
            textContent: choice.label,
            disabled: busy,
            onclick: () => {
              if (!choice.on) hlFix(finding.id, choice);
            },
          });
          b.setAttribute("aria-pressed", String(!!choice.on));
          return b;
        }),
      ),
    ),
  );
}
function healthDo(finding, busy) {
  const chips = healthChips(finding, busy);
  if (finding.status === "ok") return { how: "", controls: chips };
  const controls = [
    ...chips,
    ...(finding.actions || [])
      .filter((action) => !action.group)
      .map((action) =>
        el("button", {
          type: "button",
          className: "action-btn",
          textContent: action.label,
          disabled: busy,
          onclick: () => hlFix(finding.id, action),
        }),
      ),
  ];
  // Commands of their own, the place to go named: nothing is found in the words.
  let how = finding.fix || "";
  if (finding.go) controls.push(goLink(finding.go));
  if (!how && !controls.length && !(finding.commands || []).length)
    how = "Information only.";
  return { how, controls, commands: finding.commands || [] };
}
let hlShow = "all"; // the counters double as the list's filter: all, problem, warn
let hlDrawn = ""; // what the findings were last drawn from: the same again leaves them (and a selection in them) alone

// The box at a glance, at the top: the findings' counts, the link and the radio, the last start of the box and why,
// the watchdog's last repair, and what crash watch keeps and what restarts a frozen box.
function renderGlance(data, rep) {
  const find = (id) => rep && rep.findings.find((finding) => finding.id === id);
  const first = (text) => String(text || "").split(/(?<=\.)\s/)[0];
  // A finding in a few words: its own glance (health.py), else the first sentence of what it found.
  const brief = (finding) => finding.glance || first(finding.summary);
  // Why the box last started: the reason only ("Unclean shutdown"), the explanation stays in the findings below.
  const why = (t) => {
    const reason = String(t || "")
      .replace(/^Started: /, "")
      .split(/[:.]/)[0]
      .trim();
    return reason ? reason[0].toUpperCase() + reason.slice(1) : "Started";
  };
  const w = data.watchers || {};
  const st = rep && rep.settings;
  const acc = data.accepted || {};
  const counts = rep
    ? ["problem", "warn"].map(
        (k) =>
          rep.findings.filter(
            (finding) => finding.status === k && !(k === "warn" && acc[finding.id]),
          ).length,
      )
    : null;
  const ago = (at) => {
    const seconds = Date.now() / 1000 - at;
    return seconds < 3600
      ? `${Math.max(1, Math.round(seconds / 60))} min ago`
      : seconds < 172800
        ? `${Math.round(seconds / 3600)} h ago`
        : `${Math.round(seconds / 86400)} days ago`;
  };
  const rows = [
    [
      "Health",
      !rep
        ? "Not checked yet."
        : counts[0] + counts[1]
          ? `${counts[0]} to fix, ${counts[1]} to review (below).`
          : "Nothing to fix or review.",
    ],
    [
      "Network recovery",
      find("uplink-report")
        ? brief(find("uplink-report")) +
          (w.link && w.link.cause ? ` Cause: ${w.link.cause}.` : "")
        : "The watchdog has not reported.",
    ],
    [
      "The WiFi radio",
      find("crash-radio") ? brief(find("crash-radio")) : "No radio watched.",
    ],
    [
      "Last boot",
      w.last_boot
        ? `${ago(w.last_boot.at)}: ${why(w.last_boot.text)}.`
        : "Not recorded yet.",
    ],
    [
      "Last repair",
      w.last_repair
        ? `${ago(w.last_repair.at)}: ${w.last_repair.text}`
        : "None recorded.",
    ],
    [
      "Frozen-system recovery",
      !st
        ? "Not known yet."
        : st.panic || st.watchdog
          ? `Restarts by itself (${[st.panic && "kernel panic", st.watchdog && "watchdog"].filter(Boolean).join(", ")}).`
          : "Stays stopped until someone restarts it.",
    ],
    [
      "Snapshot interval",
      !st ? "Not known yet." : st.snapshots ? "Every 30 s." : "Disabled.",
    ],
    ["Status light", find("led") ? brief(find("led")) : "Not checked yet."],
    [
      "SD card writes",
      (lastBox && cardRate(lastBox.system)) || "Not known yet.",
    ],
  ];
  const key = JSON.stringify(rows);
  if (hl.glance.dataset.key === key) return; // the same again: left alone, a selection in it too
  hl.glance.dataset.key = key;
  hl.glance.replaceChildren(
    ...rows.flatMap(([k, v]) => [
      el("dt", { textContent: k }),
      el("dd", { textContent: v }),
    ]),
  );
}

// The doctor's own settings as a section: each a row of choices; Save sends the changes to the root helper one
// after another, says what waits, then what was done, and looks again so the page shows them as they are. A
// question an option carries is asked once, at Save, with any other.
let hsGroups = [],
  hsFrom = "",
  hsSaving = null;
const docSec = AW.section({
  id: "doctor-settings",
  label: "The Box doctor's settings",
  saved: null,
  warn: (c) => {
    const asks = Object.entries(c)
      .map(([k, v]) => {
        const g = hsGroups.find((x) => x.key === k);
        const o = g && g.options.find((x) => x.value === v);
        return o && o.confirm;
      })
      .filter(Boolean);
    return asks.length ? asks.join(" ") : null;
  },
  save: (c) => doctorSave(c),
  onChange: () => drawDoctorGroups(),
});
hl.setFoot.append(docSec.foot, docSec.reason);
function drawDoctorGroups() {
  if (!hsGroups.length) {
    hl.setGroups.replaceChildren(
      el("p", {
        className: "setting-desc",
        textContent: "Shown after the next check.",
      }),
    );
    return;
  }
  hl.setGroups.replaceChildren(
    ...hsGroups.map((g) => {
      const box = el("div", { id: `hs-${g.key}` });
      AW.choices(
        box,
        `hs-${g.key}`,
        g.options.map((o) => ({ value: o.value, title: o.label })),
        {
          value: docSec.get(g.key),
          onChange: (v) => docSec.set(g.key, v, true),
        },
      );
      box.setAttribute("aria-label", g.label);
      return el(
        "div",
        { className: "up-group" },
        el("span", { className: "setting-name", textContent: g.label }),
        el("p", { className: "setting-desc", textContent: g.desc }),
        box,
      );
    }),
  );
}
// Sent one after another; one the hub refuses stops the rest, and those already sent are still waited for and said.
async function doctorSave(changes) {
  const ids = [];
  let failed = null;
  for (const [k, v] of Object.entries(changes)) {
    const g = hsGroups.find((x) => x.key === k);
    const o = g.options.find((x) => x.value === v);
    try {
      ids.push(
        (await postJSON("/admin/health", { action: "fix", choice: o.choice }))
          .id,
      );
    } catch (err) {
      failed = err.message;
      break;
    }
  }
  if (!ids.length) throw new Error(failed);
  const j = AW.job();
  hsSaving = { ids, failed, of: Object.keys(changes).length, job: j };
  say("Saving. Sent to the root helper.", true, hl.setNote);
  loadHealth();
  return j;
}
function renderDoctorSettings(data, rep) {
  const st = rep && rep.settings;
  hsGroups = (st && st.groups) || [];
  if (hsSaving) {
    const done = hsSaving.ids.map((id) =>
      (data.results || []).find((result) => result.id === id),
    );
    if (done.every(Boolean)) {
      const bad = done.filter((result) => !result.ok);
      const left = hsSaving.of - done.length;
      const ok = !bad.length && !hsSaving.failed;
      const text =
        (bad.length
          ? bad.map(resultSaid).join(" ")
          : `Saved: ${done.map(resultSaid).join(" ")}`) +
        (hsSaving.failed
          ? ` ${done.length} of ${hsSaving.of} changes were sent. The next change failed: ${hsSaving.failed.replace(/\.?$/, ".")} ${left} change${left === 1 ? " is" : "s are"} still selected and not saved.`
          : "");
      const j = hsSaving.job;
      hsSaving = null;
      hsFrom = "";
      say(text, ok, hl.setNote);
      j.done(ok, ok ? "" : text);
      hlRequest({ action: "scan" }, "scan");
    } else {
      const ahead = ((data.queue && data.queue.waiting) || []).length;
      say(
        `Saving: ${done.filter(Boolean).length} of ${done.length} done. Waiting for the root helper` +
          (data.queue && data.queue.running
            ? ` (busy with ${data.queue.running.action}${ahead ? `, ${ahead} waiting` : ""})`
            : "") +
          ".",
        true,
        hl.setNote,
      );
    }
  }
  // A new report: the settings as they are now; a change in progress kept over them.
  const from = JSON.stringify(hsGroups.map((g) => [g.key, g.current]));
  if (from !== hsFrom && !docSec.saving()) {
    hsFrom = from;
    docSec.update(Object.fromEntries(hsGroups.map((g) => [g.key, g.current])));
    if (!hsGroups.length) drawDoctorGroups();
  }
}
document
  .getElementById("health-search")
  .addEventListener("input", () => hlLast && renderHealth(hlLast));

let hlLast = null;
// The root helper's queue in words: its action names, as the pages that ask for them call them.
const HELPER_WORDS = {
  "health-scan": "Box doctor: refresh checks",
  "health-fix": "Box doctor",
  "kit-fetch": "Toolkit: download",
  "kit-install": "Toolkit: install",
  "kit-remove": "Toolkit: remove",
  "kit-rollback": "Toolkit: roll back",
  "kit-drop-previous": "Toolkit: remove older set",
  "kit-drop-all-previous": "Clear old rollback cache",
  "kit-status": "Refresh toolkit cache",
  "kit-keep": "Toolkit: extend installation period",
  "kit-expire": "Toolkits: remove expired installations",
  "kit-define": "Toolkit: save",
  "kit-undefine": "Toolkit: delete",
  "kit-extra": "Toolkit: extras",
  "update-check": "Updates: check",
  "update-fetch": "Updates: download",
  "update-install": "Updates: install",
  "update-force-install": "Updates: install anyway",
  "update-doctor": "Updates: run doctor",
  "update-clear-cache": "Updates: delete the cache",
  "update-poll-target": "Updates: ask for the default branch",
  "update-set-source": "Updates: change the source",
  "update-signing": "Updates: signing",
  "pkg-check": "Packages: check",
  "pkg-fetch": "Packages: download",
  "pkg-install": "Packages: install",
  "pkg-rollback": "Packages: roll back",
  "pkg-settings": "Packages: settings",
  addon: "Add-on",
  "app-install": "Apps: install",
  "app-rollback": "Apps: roll back",
  "security-scan": "Security: scan",
  "security-fix": "Security",
  "security-audit": "Security doctor",
  "security-deep-audit": "Security doctor: deep audit",
  "net-scan": "Network: scan",
  "uplink-set": "Apply watchdog settings",
  "uplink-do": "Network: manual recovery step",
  "uplink-hold": "Network: pause recovery",
  "uplink-profile": "Network: profile",
  "wifi-join": "Network: join",
  "wifi-forget": "Network: remove saved network",
  "ap-on": "Hotspot: enable",
  "ap-off": "Hotspot: disable",
  "ap-try": "Hotspot: trial",
  "ap-confirm": "Hotspot: keep settings",
  "share-set": "Guests' internet",
  "share-allow": "Guests' internet",
  service: "A service",
  password: "The password",
  access: "Who can open an app",
  "admin-gate": "The administrator sign-in",
  "admin-login": "The administrator sign-in",
  "tls-switch": "HTTPS",
  "tls-make": "HTTPS: a certificate",
  "tls-renew": "HTTPS: renew",
  "tls-import": "HTTPS: import",
  "tls-box": "HTTPS",
  "tls-admin-only": "HTTPS",
  "usb-scan": "USB drive: scan",
  "usb-import": "USB drive: import",
  "usb-export": "USB drive: export",
  "usb-export-many": "USB drive: export",
  "usb-kit-import": "Import toolkit from USB drive",
  "usb-kit-export": "Export toolkit to USB drive",
  "offline-kit": "Backup: an offline kit",
  "content-export": "Backup: content export",
  "backup-image": "Backup: image",
};
const helperWords = (result) =>
  `${HELPER_WORDS[result.action] || result.action.replace(/-/g, " ")}${result.detail ? ` (${result.detail})` : ""}`;
const spanOf = (secs) =>
  ago(secs).replace(/ ago$/, "").replace("just now", "under a minute");
function renderHelperQueue(q) {
  const run = document.getElementById("hq-running"),
    wait = document.getElementById("hq-waiting");
  const doneFold = document.getElementById("hq-done-fold"),
    done = document.getElementById("hq-done");
  if (!run || !q) return;
  run.textContent = q.running
    ? `Running: ${helperWords(q.running)}${q.running.for != null ? `, for ${spanOf(q.running.for)}` : ""}.`
    : (q.waiting || []).length
      ? "Starting the next job."
      : "Nothing running.";
  wait.hidden = !(q.waiting || []).length;
  wait.replaceChildren(
    ...(q.waiting || []).map((w) =>
      el("li", {
        textContent: `${helperWords(w)}: waiting ${spanOf(w.waited || 0)}`,
      }),
    ),
  );
  doneFold.hidden = !(q.done || []).length;
  done.replaceChildren(
    ...(q.done || []).map((result) =>
      el("li", {
        className: result.ok ? "" : "bad",
        textContent: `${result.ok ? "✓" : "✗"} ${result.ago != null ? ago(result.ago) : ""}: ${resultSaid(result)}`,
      }),
    ),
  );
}
function renderHealth(data) {
  data = AW.localise(data); // the report's messages said once, in the reader's language
  hlLast = data;
  renderHelperQueue(data.queue);
  secRepairFollow(data);
  const h = data.helper || {};
  // The watchdog's escalations (ladder-chart.js), drawn again only when the record changed.
  const lad = document.getElementById("health-ladder");
  const ladKey =
    JSON.stringify((data.ladder || []).slice(-1)) +
    (data.ladder || []).length +
    JSON.stringify(data.ladder_gaps || []);
  if (lad && typeof LadderChart !== "undefined" && lad.dataset.key !== ladKey) {
    lad.dataset.key = ladKey;
    LadderChart.render(lad, data.ladder || [], { gaps: data.ladder_gaps });
  }
  hl.banner.hidden = !h.stuck;
  if (h.stuck) {
    hl.bannerDetail.textContent = `${h.waiting} request${h.waiting === 1 ? " is" : "s are"} waiting, the oldest for ${minutes(h.oldest)}.`;
    hl.bannerCmds.textContent = h.commands.join("\n");
  }
  // The busy spells of the last week, so that patterns in false alarms show.
  const bl = h.busy_log;
  hl.busyLog.hidden = !bl;
  if (bl) {
    hl.busyLog.textContent =
      `The root helper kept requests waiting ${bl.count} time${bl.count === 1 ? "" : "s"} this week while busy ` +
      `(longest wait: ${minutes(bl.longest_wait)}). Most often it was busy running ${bl.commonest} (${bl.commonest_n}×). ` +
      `The log is helper-busy.json in the state folder of the hub server.`;
  }
  // Busy, not stuck: an earlier job (a toolkit's download, an update) is still running; the rest wait their turn.
  hl.busyLine.hidden = !h.busy || !h.waiting;
  if (h.busy && h.waiting) {
    hl.busyLine.textContent =
      `The root helper is busy with an earlier job (for ${minutes(h.busy)}). ` +
      `${h.waiting} request${h.waiting === 1 ? " waits" : "s wait"} behind it. The helper runs them in turn.`;
  }
  const progress =
    data.progress && data.progress.action === "repair" ? data.progress : null;
  const busy = data.pending > 0 || !!hlWaiting || !!progress;
  if (hlWaiting) {
    const done = (data.results || []).find((result) => result.id === hlWaiting.id);
    if (done) {
      hlNote = { fid: hlWaiting.fid, text: resultSaid(done), ok: done.ok };
      if (hlWaiting.onDone) hlWaiting.onDone(done.ok, resultSaid(done));
      hlWaiting = null;
      return renderHealth(data);
    }
  }
  const rep = data.report;
  renderClockSources(rep && rep.clock, busy || h.stuck);
  // A review finding the owner accepted (data.accepted, by id) leaves the counts and the open list; one that has
  // become a problem shows again whatever was accepted.
  const acc = data.accepted || {};
  const accepted = (finding) => finding.status === "warn" && !!acc[finding.id];
  renderGlance(data, rep);
  renderDoctorSettings(data, rep);
  const all = rep
    ? [...rep.findings].sort((left, right) => RANK[left.status] - RANK[right.status])
    : [];
  const shown = new Set(all.map((finding) => finding.id));
  const noteUnder = (fid) =>
    hlNote && hlNote.fid === fid
      ? el("span", {
          className: `setting-desc action-note${hlNote.ok ? "" : " bad"}`,
          role: "status",
          textContent: hlNote.text,
        })
      : null;
  // The clock and its module have their own pane (System, Clock); the rest is the services doctor.
  const isClock = (finding) => finding.id.startsWith("clock") || finding.id.startsWith("rtc");
  // One shape for every finding, as the security doctor's: a status word,
  // what it is, and one thing to do (its repair buttons, the place it is changed, a command to copy).
  const item = (finding) => {
    const remedy = healthDo(finding, busy || h.stuck);
    const on = accepted(finding);
    return findingRow({
      id: `hl-${finding.id}`,
      state: on ? "accepted" : finding.status,
      title: finding.title,
      ...findingParts(finding),
      how: remedy.how,
      commands: finding.status === "ok" ? [] : remedy.commands,
      controls: remedy.controls,
      note: noteUnder(finding.id),
      accept:
        finding.status === "warn"
          ? {
              key: finding.id,
              title: finding.title,
              on,
              act: hlAccept,
              label: "Accept",
              hint: "Accepting leaves it as it is and takes it out of the review count. It moves to Accepted. The doctor shows it again if it becomes a problem.",
            }
          : null,
    });
  };
  const needle = hl.search.value.trim().toLowerCase();
  const match = (finding) =>
    !needle ||
    [
      finding.title,
      finding.summary,
      finding.why,
      ...(finding.notes || []),
      ...(finding.evidence || []),
      finding.fix,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(needle);
  const services = all.filter((finding) => !isClock(finding));
  const counts = {
    problem: services.filter((service) => service.status === "problem").length,
    warn: services.filter((service) => service.status === "warn" && !accepted(service)).length,
  };
  hl.summary.replaceChildren(
    ...(!rep
      ? []
      : [
          [
            "all",
            counts.problem + counts.warn,
            "all",
            counts.problem ? "problem" : counts.warn ? "warn" : "ok",
          ],
          [
            "problem",
            counts.problem,
            "to fix",
            counts.problem ? "problem" : "ok",
          ],
          ["warn", counts.warn, "to review", counts.warn ? "warn" : "ok"],
        ].map(([key, n, word, st]) => {
          const b = el(
            "button",
            {
              type: "button",
              className: `sec-count sec-count-${st}${hlShow === key ? " active" : ""}`,
              onclick: () => {
                hlShow = key;
                renderHealth(data);
              },
            },
            el("strong", { textContent: String(n) }),
            ` ${word}`,
          );
          b.setAttribute("aria-pressed", String(hlShow === key));
          return b;
        })),
  );
  const open = services.filter(
    (service) =>
      service.status !== "ok" &&
      !accepted(service) &&
      match(service) &&
      (hlShow === "all" || service.status === hlShow),
  );
  // Redrawn only when what they show has changed: a poll that brings the same leaves a selection in them alone.
  const drawn = JSON.stringify([
    rep && rep.at,
    all,
    busy,
    h.stuck,
    hlNote,
    hlShow,
    needle,
    acc,
  ]);
  const redraw = drawn !== hlDrawn;
  hlDrawn = drawn;
  if (redraw) hl.findings.replaceChildren(...open.map(item));
  hl.empty.hidden = !rep || open.length > 0;
  hl.empty.textContent =
    counts.problem + counts.warn
      ? "Nothing matches."
      : "Nothing to fix or review.";
  // Accepted first, then what passed: both folded under one heading.
  const fine = [
    ...services.filter((service) => accepted(service) && match(service)),
    ...services.filter((service) => service.status === "ok" && match(service)),
  ];
  hl.fineFold.hidden = !fine.length;
  hl.fineCount.textContent = `(${fine.length})`;
  if (redraw) {
    hl.fine.replaceChildren(...fine.map(item));
    hl.clockFindings.replaceChildren(...all.filter(isClock).map(item));
  }
  const loose =
    hlNote &&
    (hlNote.fid === "scan" ||
      hlNote.fid === "clock-scan" ||
      !shown.has(hlNote.fid))
      ? hlNote
      : null;
  const inClock =
    loose &&
    (loose.fid === "clock-scan" ||
      (!shown.has(loose.fid) && /^(rtc|clock)/.test(loose.fid)));
  say(loose && !inClock ? loose.text : "", loose ? loose.ok : true, hl.note);
  say(inClock ? loose.text : "", loose ? loose.ok : true, hl.clockNote);
  hl.when.textContent = rep
    ? `Last checked ${new Date(rep.at * 1000).toLocaleString()}${agoText(rep.at)}.` +
      (busy ? " Working…" : "")
    : busy
      ? "Checking…"
      : "Not checked yet.";
  hl.clockWhen.textContent = hl.when.textContent;
  hl.scan.disabled = hl.clockScan.disabled = busy || h.stuck;
  hl.progress.hidden = !progress;
  if (progress) {
    const steps = Math.max(progress.steps, progress.step, 1);
    hl.bar.value = Math.min(Math.max(progress.step - 1, 0) / steps, 1);
    hl.step.textContent = `Running the installer again, step ${Math.max(progress.step, 1)} of ${progress.estimate ? "about " : ""}${steps}${progress.label ? ` — ${said(progress.label)}` : ""}.`;
  }
  hl.install.textContent = installText(data.install);
  // A cut-off or troubled install is no history to fold away: shown open.
  const inst = data.install;
  if (inst && (inst.running || inst.aborted || (inst.problems || []).length))
    document.getElementById("health-install-fold").open = true;
  hl.log.textContent = (data.log || []).join("\n");
  hl.output.hidden = !(data.log || []).length;
  const problems = all.filter(
    (finding) => finding.status === "problem" && !isClock(finding),
  ).length;
  const clockProblems = all.filter(
    (finding) => finding.status === "problem" && isClock(finding),
  ).length;
  badge("health", h.stuck ? "!" : problems ? String(problems) : "");
  badge("clock", clockProblems ? String(clockProblems) : "");

  const stale = !rep || Date.now() / 1000 - rep.at > 15 * 60;
  const here = paneShown("health") || paneShown("clock");
  if (stale && !busy && !hlAsked && !h.stuck && here) {
    hlAsked = true;
    hlRequest({ action: "scan" }, paneShown("clock") ? "clock-scan" : "scan");
    return;
  }
  clearTimeout(hlPoll);
  hlPoll = setTimeout(loadHealth, busy ? 2000 : here ? 15000 : 30000);
}

// Accept a review finding (or take it back): the hub keeps the list; the page draws it again at once.
async function hlAccept(key, title, yes) {
  try {
    const reply = await postJSON("/admin/health", {
      action: yes ? "accept" : "unaccept",
      key,
      title,
    });
    hlNote = {
      fid: key,
      text: yes
        ? "Accepted. It is under Passed and accepted, below."
        : "Reopened. It is in the review list again.",
      ok: true,
    };
    if (hlLast) renderHealth({ ...hlLast, accepted: reply.accepted || {} });
  } catch (error) {
    hlNote = { fid: key, text: error.message, ok: false };
    if (hlLast) renderHealth(hlLast);
  }
}

async function loadHealth() {
  try {
    renderHealth(await getJSON("/admin/health"));
  } catch (err) {
    console.error("health pane:", err);
    hl.when.textContent = "Could not read the health report.";
    clearTimeout(hlPoll);
    hlPoll = setTimeout(loadHealth, 30000);
  }
}

// The clock's sources (clock.py): the time service and what it asks, held as a draft until Save; how each chosen
// source stands. The page's polls redraw the states, never the draft.
const CLOCK_STATE = {
  "in-use": ["ok", "In use"],
  answering: ["ok", "Answering, not used"],
  "not-answering": ["problem", "Not answering"],
  "not-found": ["warn", "Not found"],
  unknown: ["ok", "Not reported"],
};
// The clock's sources as a section: its value the choice (service, pool, GPS, module, serve, local server); the
// page's polls redraw the states, and a draft is kept over a saved choice that changes.
let clkLast = null;
const clkSec = AW.section({
  id: "clock-sources",
  label: "Time sources",
  saved: null,
  why: (c, v) =>
    !v.pool && !v.gps && !v.module && !v.local
      ? "Select at least one source: with none, nothing sets the clock."
      : null,
  warn: (c, v) => clockWarn(v, clkLast.c),
  save: (c, v) => {
    const j = AW.job();
    hlRequest(
      {
        action: "fix",
        choice: `clock-sources:${v.service}:${v.pool ? 1 : 0}:${v.gps ? 1 : 0}:${v.module ? 1 : 0}:${v.serve ? 1 : 0}:${v.local || "-"}`,
      },
      "clock-sources",
      j.done,
    );
    return j;
  },
  onChange: () => clkLast && renderClockSources(clkLast.c, clkLast.busy, true),
});
function renderClockSources(c, busy, own) {
  const box = document.getElementById("clock-sources");
  if (!box) return;
  if (!c) {
    box.replaceChildren(
      el("p", { className: "setting-desc", textContent: "Not checked yet." }),
    );
    return;
  }
  clkLast = { c, busy };
  if (
    !own &&
    clkSec.dirty() &&
    box.contains(document.activeElement) &&
    document.activeElement.tagName === "INPUT"
  )
    return; // typing: left alone
  // What is saved: the choice made here, else the image's own service as it runs (its servers its own).
  const saved = {
    service: c.service || (c.daemon === "chrony" ? "chrony" : "image"),
    pool: c.service ? !!c.pool : true,
    gps: !!c.gps,
    module: !!c.module,
    serve: !!c.serve,
    local: c.local || "",
  };
  // The saved choice, when it changes: the section draws this again (onChange) with a draft kept over it.
  if (
    !clkSec.saving() &&
    (!clkSec.loaded() ||
      JSON.stringify(saved) !==
        JSON.stringify(
          Object.fromEntries(
            Object.keys(saved).map((k) => [k, clkSec.savedValue(k)]),
          ),
        ))
  )
    return clkSec.update(saved);
  const values = clkSec.values();
  const set = (k, v) => {
    if (k === "service" && v === "image") {
      clkSec.set("gps", false, true);
      clkSec.set("module", false, true);
      clkSec.set("serve", false, true);
    }
    clkSec.set(k, v);
  };
  const tick = (key, label, desc, disabled) =>
    el(
      "label",
      { className: "setting inline" },
      el("input", {
        type: "checkbox",
        checked: !!values[key],
        disabled: busy || disabled || clkSec.disabled(),
        onchange: (event) => set(key, event.target.checked),
      }),
      el(
        "span",
        {},
        el("span", { className: "setting-name", textContent: label }),
        desc
          ? el("span", { className: "setting-desc", textContent: ` ${desc}` })
          : null,
      ),
    );
  const addr = el("input", {
    type: "text",
    value: values.local,
    placeholder: "e.g. 192.168.1.1",
    size: 30,
    disabled: busy,
    "aria-label": "A time server on the local network",
  });
  // Restore: an action, at once after its question.
  const footBox = el(
    "div",
    { className: "library-buttons" },
    ...(c.changed
      ? [
          actionButton(
            "Restore the image's settings",
            () => {
              {
                clkSec.discard();
                hlRequest(
                  { action: "fix", choice: "clock-undo" },
                  "clock-sources",
                );
              }
            },
            { disabled: busy },
          ),
        ]
      : []),
  );
  addr.addEventListener("input", () =>
    clkSec.set("local", addr.value.trim(), true),
  );
  const states = (c.sources || []).map((source) => {
    const [st, word] = CLOCK_STATE[source.state] || ["ok", source.state];
    return el(
      "li",
      {},
      el("span", { className: `fstate fstate-${st}`, textContent: word }),
      ` ${source.name[0].toUpperCase()}${source.name.slice(1)}`,
    );
  });
  const imageName =
    c.daemon === "chrony" && !c.service ? "chrony" : "systemd-timesyncd";
  box.replaceChildren(
    chipRow(
      "Time service",
      [
        ["chrony", "chrony (suggested)"],
        ["image", `The image's own (${imageName})`],
      ],
      values.service,
      (v) => set("service", v),
      () => busy,
    ),
    el("p", {
      className: "setting-desc",
      textContent:
        values.service === "chrony"
          ? "chrony asks each source you select and shows how each one stands. It can use GPS. Selecting it installs chrony and removes systemd-timesyncd. Restore puts them back."
          : "The service the image came with, asking the servers you select. It cannot use GPS, and it says only which server it uses now.",
    }),
    tick(
      "pool",
      "The internet's pool",
      "Public time servers (pool.ntp.org). Needs the internet.",
    ),
    el(
      "div",
      { className: "aw-field" },
      el("span", {
        className: "aw-label",
        textContent: "A server on the local network",
      }),
      addr,
      el("span", {
        className: "setting-desc",
        textContent:
          "A router or another box that serves the time. Leave it empty for none.",
      }),
    ),
    tick(
      "gps",
      "GPS",
      values.service === "chrony"
        ? `${c.gps_device ? "A receiver is connected." : "No receiver is connected now: connect one on USB or the UART."} Selecting GPS installs the GPS time toolkit (gpsd).`
        : "Only with chrony.",
      values.service !== "chrony",
    ),
    tick(
      "module",
      "The clock module",
      values.service !== "chrony"
        ? "With the image's own service the module sets the time at boot only."
        : c.module_ready
          ? "Its time, read every 4 s. chrony uses it while GPS and the network are silent."
          : "No clock module is set up: scan for one above. It still sets the time at boot.",
      values.service !== "chrony" || !c.module_ready,
    ),
    tick(
      "serve",
      "Serve the time to the hotspot",
      values.service === "chrony"
        ? "Guests' devices can ask the box for the time (NTP). The hotspot firewall opens UDP port 123 to guests while this is on."
        : "Only with chrony.",
      values.service !== "chrony",
    ),
    states.length
      ? el(
          "div",
          { className: "clock-states" },
          el("span", {
            className: "aw-label",
            textContent: "How each selected source stands",
          }),
          el("ul", { className: "net-facts" }, ...states),
        )
      : null,
    clkSec.foot,
    clkSec.reason,
    footBox,
  );
}
// The one question at Save: what the choice installs, removes or opens.
// What a change of the clock's sources does, beside Save: what is installed or removed, and what opens.
function clockWarn(cur, c) {
  const words = [];
  if (cur.service === "chrony" && !c.chrony_installed)
    words.push("chrony is installed and systemd-timesyncd removed");
  if (cur.service === "image" && c.service === "chrony")
    words.push(
      "chrony is removed if this page installed it, and systemd-timesyncd installed again",
    );
  if (cur.gps && !c.gps) words.push("the GPS time toolkit (gpsd) is installed");
  if (cur.serve && !c.serve)
    words.push(
      "the box answers time requests from the hotspot, and its firewall opens UDP port 123 to guests",
    );
  if (!cur.serve && c.serve)
    words.push("the hotspot firewall closes UDP port 123 again");
  const what = [
    cur.gps ? "GPS" : "",
    cur.module ? "the clock module" : "",
    cur.local ? `the server ${cur.local}` : "",
    cur.pool ? "the internet's pool" : "",
  ]
    .filter(Boolean)
    .join(", ");
  return (
    `${cur.service === "chrony" ? "chrony" : "The image's own time service"} asks ${what || "nothing"}.` +
    `${words.length ? ` On Save, ${words.join(". ")}.` : ""} Restore puts everything back as the image had it.`
  );
}

// onDone(ok, why): told whether the root helper did it, or false when the hub refused it.
async function hlRequest(body, fid, onDone) {
  try {
    hlWaiting = { id: (await postJSON("/admin/health", body)).id, fid, onDone };
    hlNote = null;
  } catch (err) {
    hlNote = { fid, text: err.message, ok: false };
    if (onDone) onDone(false, err.message);
  }
  loadHealth();
}

// A repair acts at once; only setting the clock from this device asks, as only the administrator can
// tell whether this device's clock is right (admin pages plan, phase 5).
async function hlFix(fid, action) {
  let choice = action.choice;
  let ask = null;
  if (choice === "clock-set") {
    // This device's clock, read at the moment of the click: the box has none it can trust.
    const now = new Date();
    choice = `clock-set:${Math.round(now.getTime() / 1000)}`;
    ask = `Set the box clock to ${now.toLocaleString()} (the time of this device)? Do this only if the clock of this device is correct.`;
  }
  if (ask && !(await AW.confirmHere(ask, { label: "Set the clock" }))) return;
  hlRequest({ action: "fix", choice }, fid);
}

hl.scan.addEventListener("click", () => hlRequest({ action: "scan" }, "scan"));
hl.clockScan.addEventListener("click", () =>
  hlRequest({ action: "scan" }, "clock-scan"),
);
window.addEventListener("hashchange", () => {
  if (paneShown("health") || paneShown("clock")) loadHealth();
});
loadHealth();

// --- network ---------------------------------------------------------------------------
// The root helper's inventory of the box's networking (netinv.py), and the uplink watchdog
// (uplink.py): its report, its two levels and any custom values. Answers go under the button
// that asked. The form is filled from the watchdog's report only while nobody is editing it.
const net = {
  when: document.getElementById("net-when"),
  scan: document.getElementById("net-scan"),
  scanNote: document.getElementById("net-scan-note"),
  devices: document.getElementById("net-devices"),
  hazards: document.getElementById("net-hazards"),
  status: document.getElementById("up-status"),
  pace: document.getElementById("up-pace"),
  reach: document.getElementById("up-reach"),
  guests: document.getElementById("up-guests"),
  wedge: document.getElementById("up-wedge"),
  retry: document.getElementById("up-retry"),
  giveUp: document.getElementById("up-giveup"),
  sens: document.getElementById("up-sens"),
  roaming: document.getElementById("up-roaming"),
  lockAps: document.getElementById("up-lock-aps"),
  ignoreRoams: document.getElementById("up-ignore-roams"),
  sensSays: document.getElementById("up-sens-says"),
  will: document.getElementById("up-will"),
  watchRows: document.getElementById("up-watch-rows"),
  watchNew: document.getElementById("up-watchnew"),
  boxSteps: document.getElementById("up-boxsteps"),
  cable: document.getElementById("up-cable"),
  cableReach: document.getElementById("up-cablereach"),
  custom: document.getElementById("up-custom"),
  fields: document.getElementById("up-fields"),
  hold: document.getElementById("up-hold"),
  stall: document.getElementById("up-stall"),
  unhold: document.getElementById("up-unhold"),
  note: document.getElementById("up-note"),
  profile: document.getElementById("up-profile"),
  handover: document.getElementById("net-handover"),
  defaults: document.getElementById("net-defaults"),
  defaultsNote: document.getElementById("net-defaults-note"),
  events: document.getElementById("up-events"),
};
const UP_FIELDS = [
  ["check", "Check interval (s)"],
  ["window", "Failure-count window (s)"],
  ["steps.reconnect", "Reconnect after (s)"],
  ["steps.restart", "Restart network service after (s)"],
  ["steps.radio", "Reset radio after (s)"],
  ["steps.reboot", "Reboot after (s)"],
  ["repeat", "Reconnect again every (s)"],
  ["backoff", "Retry backoff multiplier"],
  ["max_repeat", "Maximum retry interval (s)"],
  ["relapse", "Outage grouping window (s)"],
  ["reboots_per_day", "Maximum reboots per day"],
  ["reboot_gap", "Minimum reboot interval (s)"],
];
const UP_WORDS = {};
let netData = null;
let netWaiting = null; // { id, where: 'scan' | 'up' }
let netNotes = {};
let netPoll = null;
// Network recovery as a section: its one value is the settings object readUpForm() makes from the page (the picks,
// the fields, the steps and the links watched); the page draws it, and draws the saved one again on Discard. Its
// saved value is the hub's, read back through the page the same way, so the two compare like for like.
let upChosenKey = "";
const upSec = AW.section({
  id: "net-recovery",
  label: "Network recovery",
  saved: null,
  warn: (c, v) => upWarn(v.all),
  save: () => upSave(),
  onChange: () => upShow(upSec.get("all")),
});
document.getElementById("up-foot").append(upSec.foot, upSec.reason);
// A pick or a field changed: the draft follows the page; a number that is not one says why under Save.
function upTouched() {
  try {
    upSec.set("all", readUpForm(), true);
    upSec.fail("");
  } catch (err) {
    upSec.fail(err.message);
  }
}
function upShow(all) {
  if (!netData || !all) return;
  fillUpForm(all, netData.levels);
  drawUpScope(netData.inventory);
  renderUpSteps(netData.inventory, all);
  renderUpWatch(netData.inventory, netData.uplink);
}
// The hub's saved settings, as the page reads them; the draft, if any, drawn again over them after.
function upRebase(chosen) {
  const draft = upSec.dirty() ? upSec.get("all") : null;
  upSaved = chosen;
  upChosenKey = JSON.stringify(chosen) + "|" + upScope;
  upShow(chosen);
  let norm = null;
  try {
    norm = readUpForm();
  } catch (_) {
    /* a saved value the page cannot show: left as it is */
  }
  if (draft) upShow(draft);
  if (norm) upSec.update({ all: norm });
}
// What is chosen on the page (each as radio cards), before Save: two dials, pace and reach, guests,
// a wedged driver, and the sensitivity, a number of missed checks within the pace's window.
const upPick = {
  pace: "gentle",
  reach: "reboot",
  guests: "protect",
  on_wedge: "ladder",
  retry: "steady",
  give_up: "never",
  sensitivity: 3,
  roaming: "roam",
  lock_bssid: null,
  ignore_roams: false,
  watch_new: "uplink",
  box_steps: "nothing-works",
  cable_out: "wait",
  cable_reach: "reconnect",
};
const UP_GROUPS = [
  ["pace", "pace"],
  ["reach", "reach"],
  ["guests", "guests"],
  ["wedge", "on_wedge"],
  ["retry", "retry"],
  ["giveUp", "give_up"],
  ["roaming", "roaming"],
  ["watchNew", "watch_new"],
  ["boxSteps", "box_steps"],
  ["cable", "cable_out"],
  ["cableReach", "cable_reach"],
];
// Each link the owner set: watched (true) or not (false); one not set follows watch_new (uplink.py watch).
let upWatch = {};
const WATCHNEW_WORDS = {
  uplink: [
    "Monitor default-route interface",
    "The watchdog monitors the interface that carries the default route of the box. It also monitors an interface that has a problem.",
  ],
  look: [
    "Report all interfaces",
    "The watchdog monitors the default-route interface. It reports problems on every other interface and takes no action on them.",
  ],
  all: [
    "Monitor all interfaces",
    "The watchdog monitors every interface of the box. Each interface has its own watchdog.",
  ],
  none: [
    "Do not monitor",
    "The watchdog monitors only the interfaces that you set above.",
  ],
};
const BOXSTEPS_WORDS = {
  "nothing-works": [
    "Only when no path is left",
    "Only when no link reaches the network. If the WiFi fails and the cable works, the box reconnects and resets the WiFi. It does not reboot.",
  ],
  uplink: [
    "For the default-route interface",
    "For the interface that carries the default route of the box, even when another interface works.",
  ],
  any: [
    "For any monitored interface",
    "For any monitored interface, up to the maximum action, even when another interface works.",
  ],
};
const CABLE_WORDS = {
  wait: [
    "Wait and report",
    "The box cannot repair a missing cable. It waits for the cable and reports this.",
  ],
  reconnect: [
    "Reconnect periodically",
    "The box reconnects at the pace that you set, in case the port is stuck.",
  ],
  ladder: [
    "All recovery steps",
    "Every step up to the maximum action, as for any other failure.",
  ],
};
let netAsked = false;
// Which connection the card shows: '' every connection, else one with its own pace, reach,
// sensitivity, guests and wedge choice (uplink.py per_iface); the rest is the same for all.
let upScope = "";
let upSaved = null;
const UP_PER_IFACE = [
  "pace",
  "reach",
  "sensitivity",
  "guests",
  "on_wedge",
  "retry",
  "give_up",
];
const upScopeBox = document.getElementById("up-scope");
const upScopeSays = document.getElementById("up-scope-says");
function drawUpScope(inv) {
  const links = inv
    ? [
        ...inv.radios
          .filter((radio) => radio.type === "managed")
          .map((radio) => [radio.iface, "WiFi"]),
        ...inv.wired.map((w) => [w.iface, "wired"]),
      ]
    : [];
  for (const name of Object.keys((upSaved && upSaved.per_iface) || {}))
    if (!links.some(([i]) => i === name)) links.push([name, "not here now"]);
  const key = JSON.stringify(links);
  if (upScopeBox.dataset.links !== key) {
    upScopeBox.dataset.links = key;
    upScopeBox.replaceChildren(
      el("option", { value: "", textContent: "Every connection" }),
      ...links.map(([i, kind]) =>
        el("option", { value: i, textContent: `${i} (${kind}) only` }),
      ),
    );
  }
  upScopeBox.value = upScope;
  for (const g of document.querySelectorAll("#net-watchdog-card .up-box-only"))
    g.hidden = !!upScope;
  const own = upScope && ((upSaved && upSaved.per_iface) || {})[upScope];
  upScopeSays.replaceChildren(
    ...(upScope
      ? [
          own
            ? `${upScope} has its own ${Object.keys(own).join(", ")}. The other settings follow every connection. `
            : `${upScope} follows the settings for every connection. A choice here becomes its own setting. `,
          own
            ? actionButton("Use default connection settings", () => {
                const per = { ...upSaved.per_iface };
                delete per[upScope];
                upSec.discard();
                netRequest(
                  {
                    action: "settings",
                    settings: { ...upSaved, per_iface: per },
                  },
                  "up",
                );
              })
            : null,
        ]
      : [
          "You can also set pace, maximum action, sensitivity, guest handling and driver-failure handling for one connection.",
        ]),
  );
}
upScopeBox.addEventListener("change", async (event) => {
  if (upSec.dirty() && !await AW.confirmHere("Discard the unsaved selections?", { anchor: event.currentTarget })) {
    upScopeBox.value = upScope;
    return;
  }
  upScope = upScopeBox.value;
  upSec.discard();
  if (netData && upSaved) upRebase(upSaved);
});

// The numbers a choice runs on, as uplink.effective() makes them: the pace's steps up to the reach.
function upPreset(pick, levels) {
  const presets = levels.presets,
    pace = presets.pace[pick.pace];
  const allowed = levels.steps.slice(0, levels.reach.indexOf(pick.reach));
  const eff = {
    ...presets.common,
    check: pace.check,
    window: pace.window,
    repeat: pace.repeat,
    guests: pick.guests,
    sensitivity: pick.sensitivity,
  };
  eff.steps = Object.fromEntries(
    Object.entries(pace.steps).filter(([step]) => allowed.includes(step)),
  );
  if (levels.retry && pick.retry in levels.retry)
    eff.max_repeat = levels.retry[pick.retry];
  eff.on_wedge = pick.on_wedge;
  eff.give_up = levels.give_up ? levels.give_up[pick.give_up] : null;
  if (!("reconnect" in eff.steps)) eff.repeat = 0;
  return eff;
}
const upGet = (obj, key) =>
  key.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

function buildUpFields() {
  if (net.fields.childElementCount) return;
  for (const [key, label] of UP_FIELDS) {
    let input;
    if (UP_WORDS[key]) {
      input = el(
        "select",
        {},
        el("option", { value: "", textContent: "(use the default)" }),
        ...Object.entries(UP_WORDS[key]).map(([v, t]) =>
          el("option", { value: v, textContent: t }),
        ),
      );
    } else {
      input = el("input", { type: "text", inputMode: "decimal", size: 8 });
    }
    input.dataset.key = key;
    input.addEventListener("input", () => {
      if (netData) showUpPreset(netData.levels);
      upTouched();
    });
    net.fields.append(
      el("label", {}, el("span", { textContent: label }), input),
    );
  }
}

// --- the choices in words ---------------------------------------------------------------
const cap1 = (t) => t[0].toUpperCase() + t.slice(1);
const dur = (sec) =>
  sec < 60
    ? `${sec} s`
    : sec % 3600 === 0
      ? `${sec / 3600} h`
      : `${Math.round(sec / 60)} min`;
const STEP_DOES = {
  reconnect: "reconnects",
  restart: "restarts the network service",
  radio: "resets the radio",
  reboot: "reboots",
};

// What a set of numbers does once the link counts as down ("What this will do").
function stepsInWords(pace) {
  const st = pace.steps || {};
  if (!Object.keys(st).length) return "only records it";
  const parts = [];
  if ("reconnect" in st) {
    let reconnect = st.reconnect
      ? `reconnects after ${dur(st.reconnect)}`
      : "reconnects at once";
    if (pace.repeat)
      reconnect +=
        `, then again every ${dur(pace.repeat)}` +
        (pace.backoff > 1 && pace.max_repeat
          ? ` (the interval grows ×${pace.backoff}, up to ${dur(pace.max_repeat)})`
          : "");
    parts.push(reconnect);
  }
  if ("restart" in st)
    parts.push(`restarts the network service after ${dur(st.restart)}`);
  if ("radio" in st) parts.push(`resets the radio after ${dur(st.radio)}`);
  parts.push(
    "reboot" in st
      ? `reboots after ${dur(st.reboot)}${pace.reboots_per_day ? ` (at most ${pace.reboots_per_day} a day)` : ""}`
      : "never reboots",
  );
  const heavy = "radio" in st || "reboot" in st || "restart" in st;
  return (
    parts.join(", ") +
    (heavy
      ? pace.guests === "ignore"
        ? ", whether or not guests are connected"
        : " (while guests are on the hotspot, it takes no step that disconnects them)"
      : "")
  );
}

// A pace's own line: when each step comes, as far as any reach goes.
function paceLine(name, levels) {
  const pace = levels.presets.pace[name];
  return (
    `First it reconnects ${pace.steps.reconnect ? `after ${dur(pace.steps.reconnect)}` : "at once"}, then again every ${dur(pace.repeat)}. ` +
    `Then it ${["restart", "radio", "reboot"].map((step) => `${STEP_DOES[step].replace("the network service", "the service")} after ${dur(pace.steps[step])}`).join(", ")}. ` +
    `Each step runs only if the maximum action includes it. It checks every ${dur(pace.check)} and counts failed checks over ${dur(pace.window)}.`
  );
}

// The sensitivity said for the pace chosen: how many checks fit its window, so a number that can
// never be reached by failed checks alone says so.
function sensLine(n, eff) {
  const fit = Math.floor(eff.window / eff.check) + 1;
  return (
    `${n} failed check${n === 1 ? "" : "s"}, or link drops, within ${dur(eff.window)} start recovery. The failures can be together or spread out. ` +
    "A lower number is more sensitive." +
    (n > fit
      ? ` Only ${fit} checks fit that window, so only link drops between the checks can reach this number.`
      : "")
  );
}

const GUEST_WORDS = {
  protect: [
    "Defer disruptive recovery while guests are connected",
    "While guests are on the hotspot, the box does not reset the radio or reboot. It does not restart the network service when the hotspot shares the radio. The box reports this and takes the step after the guests leave.",
  ],
  ignore: [
    "Allow disruptive recovery",
    "The box takes every step up to the maximum action, whether or not guests are connected. Guests lose the hotspot for a while.",
  ],
};
const WEDGE_WORDS = {
  ladder: [
    "Use the normal recovery steps",
    "The box shows the evidence and a button to reset the radio by hand. The recovery steps follow the pace and the maximum action.",
  ],
  radio: [
    "Reset the radio at once",
    "Reconnecting or restarting cannot repair a failed radio. The box goes straight to the radio reset, if the maximum action includes it and the box is not deferring for guests.",
  ],
  reboot: [
    "Reset the radio, then reboot",
    "The same as the previous option. The box also reboots if the radio does not return 3 minutes after the reset. This applies only if the maximum action includes a reboot. The reboot limits still apply: a maximum per day, and no reboot during a build or an update.",
  ],
};
// The network at fault rather than the radio: reconnecting is all that helps, as often and for as long as chosen.
const RETRY_WORDS = {
  eager: [
    "Eager",
    "The box retries soon. The interval grows to at most 10 minutes. The box returns fastest when the network returns.",
  ],
  steady: ["Steady", "The interval grows to at most 1 hour."],
  sparing: [
    "Sparing",
    "The interval grows to at most 6 hours. This makes the fewest attempts. Use it for a box on a battery.",
  ],
};
const GIVEUP_WORDS = {
  never: [
    "Retry without limit",
    "The box retries for as long as needed. It reconnects whenever the network returns.",
  ],
  hour: [
    "An hour",
    "Then the box stops and reports this. If the network returns by itself, the box still reconnects.",
  ],
  day: ["A day", "Then the box stops and reports this."],
  week: ["A week", "Then the box stops and reports this."],
};

// Roaming: from the least change to the owner's system to the most,
// each with its cost. The last two change the WiFi profile, by consent, and are undone the same way.
const ROAM_WORDS = {
  roam: [
    "Roam normally",
    "The WiFi moves between your access points when it finds a stronger one (NetworkManager background scans). Nothing on the box changes.",
    "Cost: each move briefly disconnects the box from the network. The hotspot moves with it when it shares the radio.",
  ],
  "no-scan": [
    "No background scans while the hotspot shares the radio",
    "The wpa_supplicant background scan is disabled for the network in use, so the box stays where it is. It still reconnects, perhaps to another access point, if the link is lost. This applies only while the hotspot runs on this radio.",
    "Cost: a box that moves around the house stays on a weak access point until the link drops. This changes the running WiFi settings. You can undo it here.",
  ],
  lock: [
    "Lock to one access point",
    "Your WiFi profile is held to the access point that you select below (NetworkManager BSSID). No roaming and no background scans.",
    "Cost: if that access point goes away, the box does not move to another one. The link stays down until the access point returns or you disable the lock. This changes your WiFi profile. You can undo it here.",
  ],
};
const uplinkRadio = () => {
  const inv = netData && netData.inventory;
  const up = inv && inv.uplink;
  return inv && up ? inv.radios.find((radio) => radio.iface === up.iface) : null;
};
function drawLockAps() {
  const box = net.lockAps,
    radio = uplinkRadio(),
    aps = (radio && radio.roaming && radio.roaming.aps) || [];
  box.hidden = upPick.roaming !== "lock";
  if (box.hidden) return;
  if (!upPick.lock_bssid && aps.length) upPick.lock_bssid = aps[0].bssid; // the strongest, preselected
  box.replaceChildren(
    ...(aps.length
      ? aps.map((accessPoint) => {
          const input = el("input", {
            type: "radio",
            name: "up-lock",
            value: accessPoint.bssid,
          });
          input.checked = accessPoint.bssid === upPick.lock_bssid;
          input.addEventListener("change", () =>
            upChoose({ lock_bssid: accessPoint.bssid }, netData.levels),
          );
          return el(
            "label",
            { className: "inline" },
            input,
            ` ${accessPoint.bssid}: channel ${accessPoint.channel}${accessPoint.freq ? ` (${bandOf(accessPoint.freq)})` : ""}, signal ${accessPoint.signal}%` +
              `${radio.link && radio.link.bssid === accessPoint.bssid ? " (in use now)" : ""}`,
          );
        })
      : [
          el("p", {
            className: "setting-desc",
            textContent:
              "No access points were seen for this network in the last scan. Select Refresh on Hardware, then select an access point.",
          }),
        ]),
  );
}

function willText(eff) {
  return (
    `What this will do: it checks the link every ${dur(eff.check)}. When ${eff.sensitivity} checks have failed, or the link has dropped, ` +
    `within ${dur(eff.window)}, recovery starts. A link that keeps dropping stays in recovery until it settles. Then it ${stepsInWords(eff)}. ` +
    `Outages within ${dur(eff.relapse)} of each other are one episode. A step that did not hold is not repeated while a heavier step remains. ` +
    "A reconnect after the first locks to the strongest access point. " +
    "If it can tell that the network is at fault (gone from the scan, not letting the box join, the gateway silent), it only reconnects. " +
    `In that case it never resets the radio or reboots${eff.give_up ? `, and it stops after ${dur(eff.give_up)}` : ", and it retries for as long as needed"}. ` +
    (eff.on_wedge === "reboot"
      ? "A failed radio is reset at once. The box reboots if the radio is not back 3 minutes later."
      : eff.on_wedge === "radio"
        ? "A failed radio is reset at once."
        : "A failed radio goes through the recovery steps like any outage.")
  );
}

// Every choice the same way: radio cards, each one's description and what it does.
function rungs(box, group, items) {
  AW.choices(box, `up-${group}`, items, {
    onChange: (name) => upChoose({ [group]: name }, netData.levels),
  });
}

function buildUpChoices(levels) {
  if (net.pace.childElementCount) return;
  rungs(
    net.pace,
    "pace",
    levels.pace.map((n) => ({
      value: n,
      title: cap1(n),
      desc: levels.describe[n] || "",
      does: paceLine(n, levels),
    })),
  );
  rungs(
    net.reach,
    "reach",
    levels.reach.map((n) => ({
      value: n,
      title: cap1(n),
      desc: levels.describe[n] || "",
      does:
        n === "watch"
          ? "Checks, and never acts."
          : `Up to: ${n === "reconnect" ? "reconnecting" : STEP_DOES[n]}.`,
    })),
  );
  rungs(
    net.guests,
    "guests",
    levels.guests.map((n) => ({
      value: n,
      title: GUEST_WORDS[n][0],
      desc: GUEST_WORDS[n][1],
    })),
  );
  rungs(
    net.wedge,
    "on_wedge",
    levels.on_wedge.map((n) => ({
      value: n,
      title: WEDGE_WORDS[n][0],
      desc: WEDGE_WORDS[n][1],
    })),
  );
  rungs(
    net.retry,
    "retry",
    Object.keys(levels.retry || RETRY_WORDS).map((n) => ({
      value: n,
      title: RETRY_WORDS[n][0],
      desc: RETRY_WORDS[n][1],
    })),
  );
  rungs(
    net.giveUp,
    "give_up",
    Object.keys(levels.give_up || GIVEUP_WORDS).map((n) => ({
      value: n,
      title: GIVEUP_WORDS[n][0],
      desc: GIVEUP_WORDS[n][1],
    })),
  );
  rungs(
    net.watchNew,
    "watch_new",
    (levels.watch_new || Object.keys(WATCHNEW_WORDS)).map((n) => ({
      value: n,
      title: WATCHNEW_WORDS[n][0],
      desc: WATCHNEW_WORDS[n][1],
    })),
  );
  rungs(
    net.boxSteps,
    "box_steps",
    (levels.box_steps || Object.keys(BOXSTEPS_WORDS)).map((n) => ({
      value: n,
      title: BOXSTEPS_WORDS[n][0],
      desc: BOXSTEPS_WORDS[n][1],
    })),
  );
  rungs(
    net.cable,
    "cable_out",
    (levels.cable_out || Object.keys(CABLE_WORDS)).map((n) => ({
      value: n,
      title: CABLE_WORDS[n][0],
      desc: CABLE_WORDS[n][1],
    })),
  );
  rungs(
    net.cableReach,
    "cable_reach",
    levels.reach.map((n) => ({
      value: n,
      title: cap1(n),
      desc:
        n === "watch"
          ? "Checks and logs."
          : `Up to: ${n === "reconnect" ? "reconnecting" : n === "radio" ? "resetting the network adapter" : STEP_DOES[n]}.`,
    })),
  );
  drawRoamTiles(levels);
  net.ignoreRoams.addEventListener("change", () =>
    upChoose({ ignore_roams: net.ignoreRoams.checked }, netData.levels),
  );
  net.sens.min = levels.sensitivity[0];
  net.sens.max = levels.sensitivity[1];
  net.sens.addEventListener("input", () => {
    const n = Math.round(Number(net.sens.value));
    if (n >= levels.sensitivity[0] && n <= levels.sensitivity[1])
      upChoose({ sensitivity: n }, netData.levels);
  });
}

// The roaming tiles: the ones the hardware scan says can't work here greyed, with why.
function drawRoamTiles(levels) {
  const radio = uplinkRadio(),
    facts = radio && radio.roaming,
    why = (facts && facts.why) || {};
  const key = JSON.stringify(why);
  if (net.roaming.dataset.why === key && net.roaming.childElementCount) return;
  net.roaming.dataset.why = key;
  rungs(
    net.roaming,
    "roaming",
    (levels.roaming || Object.keys(ROAM_WORDS)).map((n) => ({
      value: n,
      title: ROAM_WORDS[n][0],
      desc: ROAM_WORDS[n][1],
      does: ROAM_WORDS[n][2],
      why:
        why[n] ||
        (n === "lock" && facts && !facts.aps.length
          ? "no access point seen for this network yet"
          : ""),
    })),
  );
}

function upChoose(change, levels) {
  Object.assign(upPick, change);
  if (change.roaming && change.roaming !== "lock") upPick.lock_bssid = null;
  showUpPreset(levels);
  upTouched();
}

// The page drawn from a settings object: the saved one, or the draft (upSaved stays the hub's).
function fillUpForm(chosen, levels) {
  buildUpChoices(levels);
  const view = upScope
    ? { ...chosen, ...(chosen.per_iface || {})[upScope] }
    : chosen;
  for (const k of Object.keys(upPick))
    upPick[k] = view[k] || levels.default[k] || null;
  upPick.roaming = chosen.roaming || "roam";
  upPick.ignore_roams = !!chosen.ignore_roams;
  net.ignoreRoams.checked = upPick.ignore_roams;
  for (const k of ["watch_new", "box_steps", "cable_out", "cable_reach"])
    upPick[k] = chosen[k] || levels.default[k];
  upWatch = { ...chosen.watch };
  const over = chosen.overrides || {};
  for (const input of net.fields.querySelectorAll("[data-key]")) {
    const v = upGet(over, input.dataset.key);
    input.value = v === undefined ? "" : v === null ? "off" : String(v);
  }
  if (Object.keys(over).length) net.custom.open = true;
  showUpPreset(levels);
}

function showUpPreset(levels) {
  for (const [boxKey, key] of UP_GROUPS) {
    for (const input of net[boxKey].querySelectorAll("input")) {
      input.checked = input.value === upPick[key];
      input.closest(".choice-tile").classList.toggle("chosen", input.checked);
    }
  }
  drawRoamTiles(levels);
  for (const input of net.roaming.querySelectorAll("input")) {
    input.checked = input.value === upPick.roaming;
    input.closest(".choice-tile").classList.toggle("chosen", input.checked);
  }
  drawLockAps();
  const eff = upPreset(upPick, levels);
  if (document.activeElement !== net.sens)
    net.sens.value = String(upPick.sensitivity);
  net.sensSays.textContent = sensLine(upPick.sensitivity, {
    ...eff,
    ...(() => {
      try {
        return readUpForm().overrides;
      } catch (_) {
        return {};
      }
    })(),
  });
  try {
    const over = readUpForm().overrides;
    const shown = { ...eff, ...over, steps: { ...eff.steps } };
    for (const [k, v] of Object.entries(over.steps || {})) {
      if (v === null) delete shown.steps[k];
      else if (k in eff.steps) shown.steps[k] = v;
    }
    net.will.textContent = willText(shown);
  } catch (_) {
    net.will.textContent = willText(eff);
  }
  for (const input of net.fields.querySelectorAll("input[data-key]")) {
    const v = upGet(eff, input.dataset.key);
    input.placeholder = v === undefined ? "off" : String(v);
  }
}

function readUpForm() {
  const overrides = {};
  for (const input of net.fields.querySelectorAll("[data-key]")) {
    const raw = input.value.trim();
    if (raw === "") continue;
    const key = input.dataset.key;
    let v = raw;
    if (input.tagName === "INPUT") {
      if (key.startsWith("steps.") && raw.toLowerCase() === "off") v = null;
      else if (Number.isNaN(Number(raw)))
        throw new Error(
          `${input.previousSibling.textContent}: enter a number${key.startsWith("steps.") ? " or off" : ""}.`,
        );
      else v = Number(raw);
    }
    if (key.startsWith("steps.")) (overrides.steps ||= {})[key.slice(6)] = v;
    else overrides[key] = v;
  }
  const all = {
    ...upPick,
    lock_bssid: upPick.roaming === "lock" ? upPick.lock_bssid : null,
    iface: "auto",
    watch: { ...upWatch },
    overrides,
    steps_off: readUpSteps(),
  };
  const base = upSaved || {};
  if (!upScope) return { ...all, per_iface: base.per_iface || {} };
  // One connection's own: those five, over what is saved for every connection.
  return {
    ...base,
    steps_off: all.steps_off,
    per_iface: {
      ...base.per_iface,
      [upScope]: Object.fromEntries(UP_PER_IFACE.map((k) => [k, all[k]])),
    },
  };
}

// The steps the watchdog may take, a row of ticks per connection (the box's WiFi and wired links, and any
// connection steps were switched off for before): all ticked unless switched off (uplink.py steps_off).
const UP_STEP_NAMES = {
  reconnect: "Reconnect",
  restart: "Restart the network service",
  radio: "Reset the radio",
  reboot: "Reboot the box",
};
function renderUpSteps(inv, chosen) {
  const box = document.getElementById("up-steps");
  if (!box) return;
  const off = (chosen && chosen.steps_off) || {};
  const links = inv
    ? [
        ...inv.radios
          .filter((radio) => radio.type !== "AP")
          .map((radio) => [radio.iface, "WiFi"]),
        ...inv.wired.map((w) => [w.iface, "wired"]),
      ]
    : [];
  for (const name of Object.keys(off))
    if (!links.some(([i]) => i === name)) links.push([name, "not here now"]);
  if (!links.length) {
    box.replaceChildren(
      el("p", {
        className: "setting-desc",
        textContent: "No connections found yet.",
      }),
    );
    return;
  }
  box.replaceChildren(
    ...links.map(([iface, kind]) => {
      const row = el(
        "fieldset",
        { className: "up-steps-row" },
        el("legend", { textContent: `${iface} (${kind})` }),
        ...Object.entries(UP_STEP_NAMES).map(([step, label]) => {
          const input = el("input", {
            type: "checkbox",
            checked: !(off[iface] || []).includes(step),
          });
          input.dataset.step = step;
          input.addEventListener("change", () => {
            upTouched();
          });
          return el(
            "label",
            { className: "inline", id: `up-step-${iface}-${step}` },
            input,
            ` ${label}`,
          );
        }),
      );
      row.dataset.iface = iface;
      return row;
    }),
  );
}
// The links, a row each: watched, not watched, or as any other link (watch_new); and what it is doing now.
const WATCH_SAY = {
  "": "As any other link",
  on: "Monitored",
  off: "Not monitored",
};
function renderUpWatch(inv, uplink) {
  const box = net.watchRows;
  const known = (uplink && uplink.inventory) || {};
  const links = inv
    ? [
        ...inv.radios
          .filter((radio) => radio.type === "managed")
          .map((radio) => [radio.iface, "WiFi"]),
        ...inv.wired.map((w) => [w.iface, "wired"]),
      ]
    : [];
  for (const [name, l] of Object.entries(known))
    if (!links.some(([i]) => i === name))
      links.push([
        name,
        { wifi: "WiFi", ethernet: "wired" }[l.kind] || "other",
      ]);
  for (const name of Object.keys(upWatch))
    if (!links.some(([i]) => i === name)) links.push([name, "not here now"]);
  if (!links.length) {
    box.replaceChildren(
      el("p", {
        className: "setting-desc",
        textContent: "No links found yet.",
      }),
    );
    return;
  }
  const watching = (uplink && uplink.links) || {};
  box.replaceChildren(
    ...links.map(([iface, kind]) => {
      const sel = el(
        "select",
        { id: `up-watch-${iface}` },
        ...Object.entries(WATCH_SAY).map(([v, t]) =>
          el("option", { value: v, textContent: t }),
        ),
      );
      sel.value =
        upWatch[iface] === true ? "on" : upWatch[iface] === false ? "off" : "";
      sel.setAttribute("aria-label", `${iface}: monitored or not`);
      sel.addEventListener("change", () => {
        if (sel.value === "") delete upWatch[iface];
        else upWatch[iface] = sel.value === "on";
        upTouched();
      });
      const now = watching[iface];
      const l = known[iface] || {};
      const said =
        (now && now.watched
          ? `${now.look ? "Reporting only" : "Monitoring"}: ${now.state}`
          : "Not monitored" +
            (l.up === false ? ", and down" : l.up ? ", up" : "")) +
        (l.as
          ? `. Known before as ${l.as} (the same hardware). It follows the setting of ${l.as}`
          : "");
      return el(
        "div",
        { className: "up-watch-row" },
        el("label", {
          className: "setting-name",
          htmlFor: `up-watch-${iface}`,
          textContent: `${iface} (${kind})`,
        }),
        sel,
        el("span", {
          className: "setting-desc",
          textContent:
            said +
            ((l.autoconnect || []).length
              ? `. Autoconnect: ${l.autoconnect.join(", ")}`
              : "") +
            ".",
        }),
      );
    }),
  );
}
function readUpSteps() {
  const out = {};
  for (const row of document.querySelectorAll("#up-steps .up-steps-row")) {
    const off = [...row.querySelectorAll("input[data-step]")]
      .filter((i) => !i.checked)
      .map((i) => i.dataset.step);
    if (off.length) out[row.dataset.iface] = off;
  }
  return out;
}

const when = (t) =>
  new Date(t * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

function upStatusText(uplink) {
  if (!uplink)
    return "The watchdog has not reported yet. The irate-box-uplink service starts with the box, or when you save here.";
  if (uplink.stale)
    return `The watchdog has not reported since ${new Date(uplink.at * 1000).toLocaleString()}: is irate-box-uplink running?`;
  if (uplink.state === "no-link")
    return uplink.watching
      ? `Monitoring ${uplink.watching}.`
      : "No link to monitor. The box has no route to a network and no WiFi client interface.";
  const others = Object.entries(uplink.links || {}).filter(
    ([i, l]) => i !== uplink.iface && l.watched,
  );
  const one = upLinkText(uplink);
  return others.length
    ? `${one} Also monitoring: ${others.map(([i, l]) => `${i}, ${l.state}${l.state === "stalled" && l.stall ? ` (${l.stall.text})` : ""}`).join(", ")}.`
    : one;
}
function upLinkText(uplink) {
  if (uplink.state === "off")
    return `${uplink.iface} was disconnected manually with nmcli device disconnect. The watchdog does not act on it until it is connected again.`;
  const l = uplink.link || {};
  const on = l.ssid
    ? `${uplink.iface}: ${l.ssid} via ${l.bssid}, channel ${l.channel}, ${l.signal} dBm`
    : uplink.iface;
  const parts = [];
  if (uplink.state === "up") {
    parts.push(`Up on ${on}. The gateway (${uplink.gateway}) answers.`);
  } else if (uplink.state === "checking") {
    parts.push(
      `Checking ${uplink.iface}: ${uplink.misses} failed check${uplink.misses === 1 ? "" : "s"} within ${dur((uplink.settings || {}).window || 0)}. ` +
        `${(uplink.settings || {}).sensitivity} failed checks start recovery.`,
    );
  } else {
    const o = uplink.outage;
    parts.push(
      o
        ? `Down for ${minutes(Math.round(uplink.at - o.since))} on ${uplink.iface}.`
        : `Down on ${uplink.iface}.`,
    );
    const tried = o ? o.done.filter((step) => !(o.skipped || []).includes(step)) : [];
    if (tried.length) parts.push(`Tried: ${tried.join(", ")}.`);
    if (o && (o.skipped || []).length)
      parts.push(`Not possible here: ${o.skipped.join(", ")}.`);
    if (uplink.next)
      parts.push(
        `Next recovery action: ${uplink.next.label} at ${when(uplink.next.at)}.`,
      );
    if (uplink.state === "stalled" && uplink.stall) parts.push(uplink.stall.text);
    else if (uplink.wedged) parts.push(`Possible driver failure: ${uplink.wedged}.`);
    const ep = uplink.episode;
    if (ep && ep.outages > 1)
      parts.push(
        `Outage ${ep.outages} since ${when(ep.since)}` +
          (ep.failed.length ? `. Did not hold: ${ep.failed.join(", ")}.` : "."),
      );
  }
  if (uplink.pinned)
    parts.push(
      `Locked to ${uplink.pinned.bssid} since ${when(uplink.pinned.at)}, until the next drop.`,
    );
  if (uplink.paused_until && uplink.paused_until > Date.now() / 1000)
    parts.push(`Recovery paused until ${when(uplink.paused_until)}.`);
  if (uplink.drops_in_window)
    parts.push(
      `${uplink.drops_in_window} drop${uplink.drops_in_window === 1 ? "" : "s"} lately.`,
    );
  parts.push(
    `Run by ${uplink.backend}. Possible repairs: ${(uplink.repairs || []).join(", ") || "none (monitoring only)"}.`,
  );
  if (uplink.dry_run)
    parts.push("Dry run: it decides and logs. It runs no repair actions.");
  return parts.join(" ");
}

// --- the devices, in plain words -------------------------------------------------------
const MANAGED_BY = {
  networkmanager:
    "NetworkManager, the system's own network settings. irate-box asks it and does not take over.",
  wpa_supplicant:
    "wpa_supplicant, which joins WiFi networks (with something else giving the address).",
  ifupdown: "ifupdown, from /etc/network/interfaces.",
  networkd: "systemd-networkd.",
  dhcpcd: "dhcpcd.",
  iwd: "iwd. irate-box can watch it, not repair it yet.",
  connman: "connman. irate-box can watch it, not repair it yet.",
  none: "Nothing irate-box recognises.",
};
const BUS_WORDS = {
  usb: "USB",
  sdio: "built-in (SDIO)",
  pci: "built-in (PCI)",
  platform: "built-in",
};
const COND_WORD = {
  how: "",
  limit: "Limit: ",
  untested: "Not tested yet: ",
  needs: "Needs: ",
};
// Experiments the box runs on a radio (root/radiotests.py), each said before it runs.
const RADIO_TEST_SAYS = {
  "follows-roam":
    "The WiFi link moves to another access point of your network on another channel for a few seconds. Then it moves back. Guests lose the hotspot for a moment, so the test runs only when no guests are connected.",
  "radio-reset":
    "The box resets the radio as the watchdog would. The WiFi and the hotspot stop for up to 1 minute. The test runs only when no guests are connected.",
  "driver-reset":
    "The box makes one command to the WiFi chip count as timed out. The driver then resets the chip itself. The WiFi and the hotspot stop for a few seconds. The test runs only when no guests are connected.",
};
const TEST_LABEL = {
  "follows-roam": "Test roaming",
  "radio-reset": "Test radio reset",
  "driver-reset": "Test driver timeout recovery",
};
// A radio test's button, what the test does beside it: it runs at once.
function radioTestButton(iface, exp, label) {
  const busy = !!netWaiting || (netData && netData.pending > 0);
  return el(
    "span",
    { className: "radio-test" },
    actionButton(
      label,
      () => netRequest({ action: "radiotest", iface, exp }, "scan"),
      { className: "small", disabled: busy },
    ),
    el("span", { className: "setting-desc", textContent: ` ${RADIO_TEST_SAYS[exp]}` }),
  );
}
function radioTests(inv, iface, section, line) {
  const t = (inv.radio_tests || {})[iface];
  if (!t || !(t.can || []).length) return [];
  return [
    section(
      line("Radio tests", ""),
      el(
        "ul",
        { className: "net-facts radio-tests" },
        ...t.can.map((exp) => {
          const got = (t.found || {})[exp];
          return el(
            "li",
            {},
            el("strong", { textContent: t.questions[exp] }),
            " ",
            got
              ? `Tested ${new Date(got.at * 1000).toLocaleDateString()}: ${got.detail}. `
              : "Not run. ",
            radioTestButton(
              iface,
              exp,
              got ? "Test again" : TEST_LABEL[exp] || "Run test",
            ),
          );
        }),
      ),
    ),
  ];
}

function signalWords(dbm) {
  if (typeof dbm !== "number") return "";
  return dbm >= -50
    ? "excellent"
    : dbm >= -60
      ? "good"
      : dbm >= -70
        ? "fair"
        : dbm >= -80
          ? "weak"
          : "poor";
}

// A link's uptime on its card: the last 72 hours by hour and 72 days by day (githubstatus.com's
// format), from the watchdog's five-minute record (linkhistory.py), drawn by heatmap.js.
// Always shown, not folded: a folded one closed itself on the pane's redraws.
function uptimeSection(iface, data = netData) {
  const uptime = data && data.uptime && data.uptime[iface];
  const fold = el("div", { className: "net-uptime" });
  const sm = uptime && uptime.summary;
  const time = (t) =>
    new Date(t * 1000).toLocaleString([], {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  const said =
    !sm || sm.up == null
      ? "Not recorded yet."
      : `Up ${Heatmap.percent(sm.up)} over the last 72 hours (${sm.hours_seen} hours recorded)` +
        (sm.drops
          ? `, ${sm.drops} drop${sm.drops === 1 ? "" : "s"}`
          : ", no drops") +
        (sm.longest
          ? `, longest outage ${sm.longest.minutes} min, ${time(sm.longest.at)}.`
          : ".");
  fold.append(
    el(
      "p",
      { className: "net-line" },
      el("span", { className: "net-label", textContent: "Uptime" }),
      liveAs(el("span", { textContent: said }), "uptime"),
    ),
  );
  if (!uptime) {
    fold.append(
      el("p", {
        className: "setting-desc",
        textContent:
          "The watchdog (irate-box-uplink) records each link in five-minute slots after the box clock is known to be correct. " +
          "Use network time, or set the clock on Clock. It keeps 72 days of records.",
      }),
    );
    return fold;
  }
  fold.append(
    el("p", {
      className: "setting-desc",
      textContent: "The last 72 hours, by hour:",
    }),
    liveAs(
      Heatmap.grid({
        caption: `${iface}, the last 72 hours by hour`,
        cols: uptime.hour_cols,
        rows: [{ label: iface, cells: uptime.hours, where: (i) => uptime.hour_full[i] }],
      }),
      "hours",
    ),
  );
  const day = (iso) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString([], {
      weekday: "short",
      day: "numeric",
      month: "short",
    });
  fold.append(
    el("p", {
      className: "setting-desc",
      textContent: "The last 72 days, by day:",
    }),
    liveAs(
      Heatmap.grid({
        caption: `${iface}, the last 72 days by day`,
        cols: Array.from({ length: 72 }, (_, i) =>
          i % 12 ? "" : day(uptime.month[i].date),
        ),
        rows: [
          {
            label: iface,
            cells: uptime.month.map((c) => (c.n ? c : null)),
            where: (i) => day(uptime.month[i].date),
          },
        ],
      }),
      "days",
    ),
    Heatmap.legend(),
  );
  fold.append(
    el("p", {
      className: "setting-desc",
      textContent:
        uptime.kind === "uplink"
          ? "Up means the link was up and the gateway answered. A drop is the link going down after being up. A link that you disabled is not counted."
          : uptime.kind === "hotspot"
            ? "Up means the hotspot was running: its interface up and serving."
            : "This shows only whether the link was up: a cable in, or joined to a network. The gateway is checked on the link of the box to your network.",
    }),
  );
  return fold;
}

// The inventory's findings (netinv.py) in the reader's language, as the cards draw them: title, detail (summary,
// why, notes and evidence) and fix (with its commands), as finding.english says them. An inventory from before
// netinv wrote findings has only its English hazards.
function netHazards(inv) {
  if (!inv.findings) return inv.hazards || [];
  return AW.localise(inv.findings).map((finding) => ({
    ...finding,
    detail:
      [finding.summary, finding.why, ...(finding.notes || [])].filter(Boolean).join(" ") +
      ((finding.evidence || []).length ? ` (${finding.evidence.join(", ")})` : ""),
    fix: [finding.fix, ...(finding.commands || []).map((c) => `\`${c}\``)]
      .filter(Boolean)
      .join(" "),
  }));
}

// A network card's header, as every card's: what it is about, then what kind of thing it is
// (quiet tags), then how it stands at the right; the heading holds the name alone.
function netHead(title, kinds = [], state = null) {
  return el(
    "div",
    { className: "fhead net-head" },
    el("h4", { className: "ftitle", textContent: title }),
    ...kinds
      .filter(Boolean)
      .map((k) => el("span", { className: "kind-tag", textContent: k })),
    state
      ? el("span", {
          className: `fstate fstate-${state}`,
          textContent: STATE_WORD[state] || state,
        })
      : null,
  );
}
// A WiFi client's power saving as a setting: on or off as the radio reports it (iw), where that is set
// (netinv.powersave_from), and a link to change it there. On is state, not a warning (item 157 will
// let the radio's tests set it).
const POWER_SAVE_WORD = { on: "On", off: "Off", 3: "On", 2: "Off" };
function powerSaveLine(device) {
  const pf = device.powersave_from || {};
  const state =
    POWER_SAVE_WORD[device.power_save] || POWER_SAVE_WORD[pf.value] || "Not known";
  const [where, to] =
    pf.where === "network"
      ? [`set in the settings of ${pf.network}`, `net-conn-${pf.uuid}`]
      : pf.where === "default"
        ? ["set in Default connection settings", "net-defaults-card"]
        : pf.where === "driver"
          ? ["left to the driver", "net-defaults-card"]
          : ["", ""];
  return el(
    "p",
    { className: "net-line" },
    el("span", { className: "net-label", textContent: "Power saving" }),
    el(
      "span",
      {},
      where ? `${state}, ${where}. ` : `${state}.`,
      to ? el("a", { href: `#${to}`, textContent: "Change →" }) : null,
    ),
  );
}

function deviceCard(inv, device, wifi, hazards) {
  const line = netLine;
  // Each labelled part a section of its own, a hairline between them, so the parts don't run together.
  const section = (...parts) =>
    el("div", { className: "net-section" }, ...parts);
  const kind = wifi ? "WiFi" : "Wired";
  const usb = device.usb || {};
  const what = [
    wifi
      ? `${BUS_WORDS[device.bus] || device.bus || ""} WiFi adapter`.trim()
      : `${BUS_WORDS[device.bus] || ""} Ethernet port`.trim(),
    device.driver ? `driver ${device.driver}` : null,
    usb.product || null,
    usb.id ? `USB id ${usb.id}` : null,
  ]
    .filter(Boolean)
    .join(", ");
  let now;
  if (wifi) {
    const l = device.link || {};
    if (l.ssid) {
      const band = l.freq
        ? l.freq < 3000
          ? "2.4 GHz"
          : l.freq < 5925
            ? "5 GHz"
            : "6 GHz"
        : "";
      // Under "Current connection": the network's name first, not "Connected to" again.
      now = `${l.ssid}, channel ${l.channel}${band ? ` (${band})` : ""}, signal ${l.signal} dBm (${signalWords(l.signal)}).`;
    } else if (device.type === "AP") now = "Running a hotspot.";
    else now = "Not connected to a network.";
  } else {
    now = device.carrier ? "Cable in." : "No cable.";
  }
  if (inv.uplink && inv.uplink.iface === device.iface)
    now += " This is the box's link to your network.";
  const owner = wifi
    ? device.owner
    : inv.uplink && inv.uplink.iface === device.iface
      ? inv.uplink.backend
      : null;
  const managed = owner
    ? (MANAGED_BY[owner] || owner) +
      (device.manager ? ` With ${device.manager} for the address.` : "")
    : "—";
  const kids = [
    netHead(device.iface, [kind]),
    section(line("Device type", what || "—")),
    section(line("Current connection", now, "now")),
    section(line("Connection manager", managed)),
  ];
  // What irate-box knows of the chip (hub/known_radios.json): its name, where its driver comes from, notes from
  // testing, and who tested it, ours and others' credited.
  const chip = wifi && device.known;
  if (chip)
    kids.splice(
      2,
      0,
      section(
        line(
          "Chip",
          [
            `${chip.chip}.`,
            chip.in_kernel
              ? "Its driver is in the kernel."
              : `Its driver is outside the kernel${chip.source && chip.source.name ? `: ${chip.source.name}` : ""}.`,
            chip.hotspot === false ? "It has no hotspot mode." : null,
            ...(chip.notes || []),
            chip.tested_by && chip.tested_by.length
              ? `Tested by ${chip.tested_by.join(" and ")}.`
              : "Not tested yet.",
          ]
            .filter(Boolean)
            .join(" "),
        ),
      ),
    );
  if (wifi && (device.power_save || device.powersave_from))
    kids.push(section(powerSaveLine(device)));
  if (wifi) {
    const hotspot = (inv.ap || []).find((x) => x.phy === device.phy);
    if (hotspot) {
      const conds = hotspot.conditions || [{ kind: "how", text: hotspot.detail }];
      const any = conds.some((c) => c.kind !== "how");
      const verdict = !hotspot.possible
        ? "No."
        : any
          ? `Yes, with conditions (through ${hotspot.backend}).`
          : `Yes (through ${hotspot.backend}).`;
      const tests = (inv.radio_tests || {})[device.iface] || {};
      kids.push(
        section(
          line("Hotspot support", verdict),
          el(
            "ul",
            { className: "net-conditions" },
            ...conds.map((c) => {
              const got = c.test && (tests.found || {})[c.test];
              if (c.kind === "untested" && got)
                return el(
                  "li",
                  { className: "cond-tested" },
                  `Tested ${new Date(got.at * 1000).toLocaleDateString()}: ${got.detail}.`,
                );
              return el(
                "li",
                { className: `cond-${c.kind}` },
                `${COND_WORD[c.kind] || ""}${c.text}`,
                c.kind === "untested" &&
                  c.test &&
                  (tests.can || []).includes(c.test)
                  ? radioTestButton(
                      device.iface,
                      c.test,
                      TEST_LABEL[c.test] || "Run test",
                    )
                  : null,
              );
            }),
          ),
        ),
      );
    }
    kids.push(...radioTests(inv, device.iface, section, line));
  }
  // What the box knows about this device, in words: what to review first, then what is known.
  const mine = hazards.filter((h) => h.iface === device.iface);
  const look = mine.filter(
      (h) => h.status === "problem" || h.status === "warn",
    ),
    known = mine.filter((h) => h.status === "ok" || h.status === "accepted");
  const fact = (h) =>
    el(
      "li",
      {},
      el("strong", { textContent: h.title }),
      h.detail ? `: ${h.detail}` : "",
      h.fix
        ? el("span", { className: "setting-desc", textContent: ` ${h.fix}` })
        : null,
      hazardControls(h),
    );
  if (look.length)
    kids.push(
      section(
        line("To review", ""),
        el("ul", { className: "net-facts" }, ...look.map(fact)),
      ),
    );
  if (known.length)
    kids.push(
      section(
        line("Known limitations", ""),
        el("ul", { className: "net-facts" }, ...known.map(fact)),
      ),
    );
  return el("div", { className: "net-device setting" }, ...kids);
}

// --- the tabs' own parts: a card per connection on Status and on Connection settings ----------------
const netTabs = {
  overview: document.getElementById("net-overview"),
  statusCards: document.getElementById("net-status-cards"),
  ladder: document.getElementById("net-ladder"),
  wifiNow: document.getElementById("net-wifi-now"),
  saved: document.getElementById("net-saved"),
  wiredNow: document.getElementById("net-wired-now"),
  wiredCard: document.getElementById("net-wired-card"),
  apHealth: document.getElementById("ap-health"),
};
const bandOf = (frequency) =>
  frequency ? (frequency < 3000 ? "2.4 GHz" : frequency < 5925 ? "5 GHz" : "6 GHz") : "";
// live: a value that moves at every poll (a signal), put in place on its own (AW.drawKept).
const netLine = (label, text, live) =>
  el(
    "p",
    { className: "net-line" },
    el("span", { className: "net-label", textContent: label }),
    liveAs(el("span", { textContent: text }), live),
  );
// A node marked as live (AW.drawKept): drawn again in place, not counted in what its card shows.
function liveAs(node, name) {
  if (name) node.setAttribute("data-live", name);
  return node;
}
function linkNow(inv, device) {
  if (device.link && device.link.ssid)
    return `On ${device.link.ssid}, channel ${device.link.channel}${device.link.freq ? ` (${bandOf(device.link.freq)})` : ""}, signal ${device.link.signal} dBm (${signalWords(device.link.signal)}).`;
  if (device.type === "AP") return "Running the hotspot.";
  if ("carrier" in device) return device.carrier ? "Cable in." : "No cable.";
  return "Not connected to a network.";
}
// Roaming: on the WiFi card, the access points sharing the network and how often the box
// moved between them; a lock whose access point has gone, with Unlock. On Hardware, which controls work here.
const ROAM_SHORT = {
  roam: "normal",
  "no-scan": "no background scans while the hotspot shares the radio",
  lock: "locked to one access point",
};
// A list in words: "1, 6 and 11".
const andList = (xs) =>
  xs.length > 1
    ? `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`
    : String(xs[0] ?? "");
// wpa_supplicant's background scan ("simple:30:-65:300": short interval, signal threshold, long interval) in words.
function bgscanWords(v) {
  if (!v)
    return "off: it looks for a stronger access point only when the link drops.";
  const m = /^simple:(\d+):(-?\d+):(\d+)$/.exec(v);
  if (!m) return v;
  const t = (sec) =>
    sec % 60 === 0 && sec >= 60 ? `${sec / 60} min` : `${sec} s`;
  return `looks for a stronger access point every ${t(+m[1])} while the signal is weaker than ${m[2]} dBm, every ${t(+m[3])} while it is stronger.`;
}
function drawRoaming(inv, uplink, wifi) {
  const box = document.getElementById("net-roaming"),
    caps = document.getElementById("net-roam-caps");
  const radio = wifi.find((x) => x.roaming),
    roaming = radio && radio.roaming;
  if (!roaming) {
    box.replaceChildren();
    caps.replaceChildren();
    return;
  }
  const chosen = (uplink && uplink.chosen) || {};
  const n = roaming.aps.length,
    hour = uplink && !uplink.stale ? uplink.roams_hour || 0 : null;
  const lines = [
    netLine(
      "Roaming",
      n > 1
        ? `Roaming: ${ROAM_SHORT[chosen.roaming || "roam"]}${chosen.ignore_roams ? " (short moves not counted as drops)" : ""}. ` +
            `${n} access points for ${roaming.ssid}, on channel${roaming.channels.length === 1 ? "" : "s"} ${andList(roaming.channels)}. ` +
            (hour == null
              ? ""
              : hour
                ? `The box moved between them ${hour === 1 ? "once" : `${hour} times`} in the last hour.`
                : "The box has not moved between them in the last hour.") +
            (roaming.hotspot_shares && n > 1
              ? " The hotspot shares this radio, so it moves too."
              : "")
        : `One access point for ${roaming.ssid}: nothing to roam between.`,
    ),
  ];
  if (roaming.bgscan != null)
    lines.push(netLine("Background scan", cap1(bgscanWords(roaming.bgscan))));
  const lockGone =
    chosen.roaming === "lock" &&
    uplink &&
    uplink.state !== "up" &&
    !roaming.aps.some((accessPoint) => accessPoint.bssid === chosen.lock_bssid);
  if (lockGone) {
    lines.push(
      el(
        "p",
        { className: "setting-desc bad" },
        `The locked access point (${chosen.lock_bssid}) is not seen and the link is down. `,
        actionButton(
          "Disable access-point lock",
          () => {
            netRequest(
                {
                  action: "settings",
                  settings: { ...chosen, roaming: "roam", lock_bssid: null },
                },
                "up",
              );
          },
          { className: "primary" },
        ),
      ),
    );
  }
  box.replaceChildren(...lines);
  caps.replaceChildren(
    el(
      "div",
      { className: "net-device setting" },
      el("h4", {}, el("span", { textContent: `${radio.iface}: roaming` })),
      el("p", {
        className: "net-label",
        textContent: `Access points sharing ${roaming.ssid}`,
      }),
      roaming.aps.length
        ? el(
            "ul",
            { className: "net-ap-tiles" },
            ...roaming.aps.map((accessPoint) =>
              el(
                "li",
                {
                  className:
                    "net-ap-tile" +
                    (radio.link && radio.link.bssid === accessPoint.bssid ? " in-use" : ""),
                },
                el("span", { className: "net-ap-bssid", textContent: accessPoint.bssid }),
                el("span", {
                  className: "setting-desc",
                  textContent: `Channel ${accessPoint.channel}${accessPoint.freq ? ` (${bandOf(accessPoint.freq)})` : ""}`,
                }),
                el("meter", {
                  min: 0,
                  max: 100,
                  value: accessPoint.signal,
                  title: `Signal ${accessPoint.signal}%`,
                }),
                el("span", {
                  className: "setting-desc",
                  textContent: `${accessPoint.signal}%${radio.link && radio.link.bssid === accessPoint.bssid ? ", in use" : ""}`,
                }),
              ),
            ),
          )
        : el("p", {
            className: "setting-desc",
            textContent: "None in the last scan.",
          }),
      // What this box can do, as facts beside the version they depend on: no state badges in running text.
      el("p", {
        className: "net-label",
        textContent: "Roaming options on this box",
      }),
      ...["roam", "no-scan", "lock"].map((c) =>
        netLine(
          ROAM_WORDS[c][0],
          roaming.choices.includes(c) ? "Available" : `Not available: ${roaming.why[c]}`,
        ),
      ),
      roaming.nm_version ? netLine("NetworkManager", roaming.nm_version) : null,
    ),
  );
}

function drawNetTabs(data, inv, uplink) {
  const up = inv && inv.uplink;
  // Status: one line for the whole, then a card per link the box could reach a network by.
  netTabs.overview.textContent = !inv
    ? "Not checked yet."
    : !up || !up.iface
      ? "The box has no link to a network right now."
      : `The box reaches your network through ${up.iface} (${up.kind === "wifi" ? "WiFi" : "wired"}). ${upStatusText(uplink)}`;
  const links = inv
    ? [...inv.radios.filter((radio) => radio.type !== "AP"), ...inv.wired]
    : [];
  AW.drawKept(netTabs.statusCards, [
    ...links.map((link) =>
      el(
        "div",
        { className: "net-card setting" },
        netHead(link.iface, [
          "carrier" in link ? "Wired" : "WiFi",
          up && up.iface === link.iface ? "The box's link" : null,
        ]),
        netLine("Current connection", linkNow(inv, link), "now"),
        uptimeSection(link.iface),
      ),
    ),
  ]);
  const netLadKey =
    String((data.ladder || []).length) + JSON.stringify(data.ladder_gaps || []);
  if (
    typeof LadderChart !== "undefined" &&
    netTabs.ladder.dataset.key !== netLadKey
  ) {
    netTabs.ladder.dataset.key = netLadKey;
    LadderChart.render(netTabs.ladder, data.ladder || [], {
      gaps: data.ladder_gaps,
    });
  }
  // Connection settings: its WiFi now, the networks it knows (NetworkManager's saved ones), its wired port.
  const wifi = inv ? inv.radios.filter((radio) => radio.type === "managed") : [];
  AW.drawKept(netTabs.wifiNow, [
    ...(wifi.length
      ? wifi.map((radio) =>
          el(
            "div",
            {},
            netLine(radio.iface, linkNow(inv, radio), "now"),
            radio.link && radio.link.bssid
              ? netLine(
                  "Access point",
                  `${radio.link.bssid}${radio.profile && radio.profile.bssid_lock ? " (locked to it)" : ""}`,
                )
              : null,
            radio.profile
              ? netLine(
                  "Profile",
                  `${radio.profile.name}${radio.profile.autoconnect ? "" : ", autoconnect off"}`,
                )
              : null,
            meteredNow && meteredNow.iface === radio.iface
              ? netLine(
                  "Metered",
                  meteredNow.metered
                    ? `yes (${meteredNow.how}). Scheduled updates and downloads wait for another network.`
                    : `no (${meteredNow.how})`,
                )
              : null,
          ),
        )
      : [
          el("p", {
            className: "setting-desc",
            textContent: inv
              ? "No WiFi client here. The box reaches its network another way."
              : "Not checked yet.",
          }),
        ]),
  ]);
  const nm = inv && inv.stacks && inv.stacks.networkmanager;
  const known = ((nm && nm.wifi_profiles) || []).filter((profile) => profile.mode !== "ap");
  const current = new Set(
    wifi.map((radio) => radio.profile && radio.profile.uuid).filter(Boolean),
  );
  const mine = new Map((data.joined || []).map((j) => [j.uuid, j]));
  const busy = data.pending > 0 || !!netWaiting;
  // Not redrawn while a field in it has focus: the poll would take the field away mid-edit.
  if (!netTabs.saved.contains(document.activeElement))
    netTabs.saved.replaceChildren(
      ...(known.length
        ? [
            el(
              "ul",
              { className: "net-known" },
              ...known.map((profile) =>
                el(
                  "li",
                  {},
                  el("strong", { textContent: profile.ssid || profile.name }),
                  current.has(profile.uuid)
                    ? el("span", {
                        className: "ok-pill",
                        textContent: "in use",
                      })
                    : null,
                  el("span", {
                    className: "setting-desc",
                    textContent:
                      ` ${profile.name}${profile.autoconnect ? `, connects automatically${profile.priority ? ` (priority ${profile.priority})` : ""}` : ", manual connection only"}` +
                      `${profile.bssid_lock ? `, locked to ${profile.bssid_lock}` : ""}${profile.iface ? `, on ${profile.iface} only` : ""}.`,
                  }),
                  mine.has(profile.uuid)
                    ? el("span", {
                        className: "info-pill",
                        textContent: "added here",
                      })
                    : null,
                  connSettings(
                    profile,
                    inv,
                    data,
                    busy,
                    current.has(profile.uuid)
                      ? wifi.find((radio) => radio.profile && radio.profile.uuid === profile.uuid)
                      : null,
                  ),
                  mine.has(profile.uuid)
                    ? actionButton(
                        "Remove saved network",
                        async (event) => {
                          if (
                            await AW.confirmHere(
                              `Remove the saved network ${profile.ssid || profile.name}? The box deletes its profile and does not join it automatically again.`, { anchor: event.currentTarget })
                          )
                            netRequest(
                              { action: "forget", uuid: profile.uuid },
                              "join",
                            );
                        },
                        {
                          className: "small",
                          disabled: busy || current.has(profile.uuid),
                          title: current.has(profile.uuid)
                            ? "In use: the box is on it now"
                            : "",
                        },
                      )
                    : null,
                ),
              ),
            ),
          ]
        : [
            el("p", {
              className: "setting-desc",
              textContent:
                nm && nm.running
                  ? "None saved in NetworkManager."
                  : "NetworkManager does not run this box's WiFi, so its saved networks are not listed here.",
            }),
          ]),
    );
  drawRoaming(inv, uplink, wifi);
  const wired = inv ? inv.wired : [];
  netTabs.wiredCard.hidden = !!inv && !wired.length;
  AW.drawKept(netTabs.wiredNow, [
    ...wired.map((w) =>
      netLine(
        w.iface,
        linkNow(inv, w) +
          (up && up.iface === w.iface
            ? " The box's link to your network."
            : ""),
      ),
    ),
  ]);
  drawApHealth();
}

// The hotspot's status card: switched on or off (the root helper's record, apRun), against what runs (the inventory's
// radios in AP mode), and its up and down from the watchdog's record, as the Overview draws it. Switched on but no
// radio serving it is said as a fault, not as "Not running".
function drawApHealth(data = netData) {
  const inv = data && data.inventory;
  const wifi = inv ? inv.radios.filter((radio) => radio.type !== "AP") : [];
  const aps = inv ? inv.radios.filter((radio) => radio.type === "AP") : [];
  const on = apRun ? !!apRun.up : null;
  const lines = aps.flatMap((accessPoint) => {
    const shared = wifi.find((radio) => radio.phy === accessPoint.phy);
    const n = (accessPoint.stations || []).length;
    return [
      netLine(
        accessPoint.iface,
        `Running${accessPoint.channel ? `, channel ${accessPoint.channel}${accessPoint.freq ? ` (${bandOf(accessPoint.freq)})` : ""}` : ""}. ${n} guest device${n === 1 ? "" : "s"} joined.`,
      ),
      shared
        ? netLine(
            "Its radio",
            `Shared with the WiFi of the box (${shared.iface}). It follows the channel of that network, so a roam or a reconnect there moves guests too.`,
          )
        : null,
    ];
  });
  if (!aps.length) {
    lines.push(
      !inv
        ? el("p", {
            className: "setting-desc",
            textContent: "Not checked yet.",
          })
        : on
          ? el("p", {
              className: "setting-desc bad",
              textContent:
                "Down: the hotspot is switched on, but no radio is serving it. Guests cannot join. " +
                "The Box doctor says why (hostapd, its connection, or dnsmasq). Disable and enable the hotspot below to start it again.",
            })
          : el("p", {
              className: "setting-desc",
              textContent: on === false ? "Switched off." : "Not running.",
            }),
    );
  }
  // Its record, by hour and by day: the hotspot's interface as the watchdog saw it.
  const hist = Object.keys((data && data.uptime) || {}).filter(
    (i) => data.uptime[i].kind === "hotspot",
  );
  AW.drawKept(netTabs.apHealth, [
    ...lines.filter(Boolean),
    ...hist.map((i) => uptimeSection(i, data)),
  ]);
}

function renderNetwork(data) {
  if (data.metered) meteredNow = data.metered.now || null;
  netData = data;
  const busy = data.pending > 0 || !!netWaiting;
  if (netWaiting) {
    const done = (data.results || []).find((result) => result.id === netWaiting.id);
    if (done) {
      netNotes[netWaiting.where] = { text: resultSaid(done), ok: done.ok };
      if (netWaiting.onDone) netWaiting.onDone(done.ok, resultSaid(done));
      netWaiting = null;
      return renderNetwork(data);
    }
  }
  const inv = data.inventory;
  let uplink = data.uplink;
  const levels = data.levels;
  // What the box has
  net.when.textContent = inv
    ? `Last checked ${new Date(inv.at * 1000).toLocaleString()}${agoText(inv.at)}${inv.focus ? ` at ${inv.focus} only` : ""}.` +
      (busy ? " Checking…" : "")
    : busy
      ? "Checking…"
      : "Not checked yet.";
  net.scan.disabled = busy;
  // A card per device, in plain words: what it is, what it's doing, what manages it, whether it
  // can run the hotspot and on what conditions, and the warnings that are about it.
  const shownIfaces = new Set(
    inv
      ? [...inv.radios.map((radio) => radio.iface), ...inv.wired.map((w) => w.iface)]
      : [],
  );
  // A review item accepted (on this page or the Box doctor's, one list: "net:<id>") is said as that and sorts with the passed.
  const accNet = data.accepted || {};
  const hz = inv
    ? netHazards(inv)
        .map((h) =>
          h.status === "warn" && accNet[`net:${h.id}`]
            ? { ...h, status: "accepted" }
            : h,
        )
        .sort((x, y) => (RANK[x.status] ?? 2) - (RANK[y.status] ?? 2))
    : [];
  AW.drawKept(net.devices, [
    ...(inv
      ? [
          ...inv.radios.map((radio) => deviceCard(inv, radio, true, hz)),
          ...inv.wired.map((w) => deviceCard(inv, w, false, hz)),
        ]
      : []),
  ]);
  drawNetTabs(data, inv, uplink);
  AW.drawKept(net.hazards, [
    ...hz
      .filter((h) => !(h.iface && shownIfaces.has(h.iface)))
      .map((h) =>
        el(
          "div",
          { className: "net-device setting" },
          netHead(h.title, [], h.status),
          h.detail
            ? el("p", { className: "setting-desc", textContent: h.detail })
            : null,
          h.fix
            ? el("p", { className: "setting-desc", textContent: h.fix })
            : null,
          hazardControls(h),
        ),
      ),
  ]);
  const sn = netNotes.scan;
  say(sn ? sn.text : "", sn ? sn.ok : true, net.scanNote);
  const jn = netNotes.join;
  say(jn ? jn.text : "", jn ? jn.ok : true, noteEl("net-join-note"));

  // Network recovery
  buildUpFields();
  // The saved settings again only when they changed (or the scope did): a draft is kept over them, said as
  // changed elsewhere if they moved under it.
  const chosen = (uplink && uplink.chosen) || levels.default;
  if (
    !upSec.saving() &&
    JSON.stringify(chosen) + "|" + upScope !== upChosenKey
  )
    upRebase(chosen);
  if (!upSec.dirty()) drawUpScope(inv);
  if (!upSec.dirty() || !net.watchRows.childElementCount) renderUpWatch(inv, uplink);
  net.status.textContent = upStatusText(uplink);
  // Stalled: the step that could help, one press away; the watchdog's
  // guards (guests on the hotspot, the reboot caps) still apply, and its log says what it did.
  // Or a wedged driver, with a radio reset possible: offered by hand whatever the ladder is doing.
  const wedgedRadio =
    uplink &&
    !uplink.stale &&
    uplink.wedged &&
    (uplink.repairs || []).includes("radio") &&
    !((uplink.outage || {}).done || []).includes("radio");
  const need =
    uplink &&
    !uplink.stale &&
    ((uplink.state === "stalled" && uplink.stall && uplink.stall.needs) ||
      (wedgedRadio && "radio"));
  if (need && !(uplink.stall && uplink.stall.needs))
    uplink = { ...uplink, stall: { needs: "radio", label: "reset the radio" } };
  net.stall.hidden = !need;
  net.stall.replaceChildren(
    ...(need
      ? [
          el("button", {
            type: "button",
            className: "action-btn primary",
            disabled: busy,
            textContent: `${uplink.stall.label[0].toUpperCase()}${uplink.stall.label.slice(1)} now`,
            onclick: () => netRequest({ action: "do", step: need }, "up"),
          }),
          // What it costs, beside it: an action acts at once (admin pages plan, phase 5).
          el("span", {
            className: "setting-desc",
            textContent:
              need === "reboot"
                ? " The box restarts. All its services are unavailable for 1 to 2 minutes."
                : " The WiFi and the hotspot stop for a moment.",
          }),
        ]
      : []),
  );
  const held = uplink && uplink.chosen && uplink.chosen.hold_until > Date.now() / 1000;
  net.hold.hidden = !!held;
  net.unhold.hidden = !held;
  net.hold.disabled = net.unhold.disabled = busy;
  const un = netNotes.up;
  say(un ? un.text : "", un ? un.ok : true, net.note);

  renderCountry(inv, busy);
  renderDefaults(inv, busy);
  renderHandover(inv, busy);
  renderVisible(inv, busy);
  renderOtherAp(inv, data, busy);
  renderWifisync(inv, data, busy);
  const change = uplink && uplink.profile_change;
  const prof =
    inv && inv.uplink && inv.uplink.backend === "networkmanager"
      ? inv.uplink.profile
      : null;
  const trap =
    inv &&
    netHazards(inv).find(
      (h) =>
        h.id.startsWith("auth-retries:") ||
        h.id.startsWith("autoconnect-retries:"),
    );
  if (change && trap) {
    net.profile.replaceChildren(
      el(
        "p",
        { className: "setting-desc" },
        `Your WiFi connection ${change.name} was set to retry without limit. netplan wrote the connection again at boot, and the setting was lost. ` +
          "The settings above persist, whichever program writes the connection. ",
        actionButton(
          "Enable automatic retries",
          () => netRequest({ action: "profile", on: true }, "up"),
          { disabled: busy },
        ),
        " ",
        actionButton(
          "Undo",
          () => netRequest({ action: "profile", on: false }, "up"),
          { disabled: busy },
        ),
      ),
    );
  } else if (change) {
    net.profile.replaceChildren(
      el(
        "p",
        { className: "setting-desc" },
        `Your WiFi connection ${change.name} is also set to retry without limit, by a separate setting (since ${new Date(change.at * 1000).toLocaleDateString()}). `,
        actionButton(
          "Undo",
          () => netRequest({ action: "profile", on: false }, "up"),
          { disabled: busy },
        ),
      ),
    );
  } else if (prof && trap) {
    net.profile.replaceChildren(
      el(
        "p",
        { className: "setting-desc" },
        `Your WiFi connection ${prof} stops after a few failures. It then waits for someone to reconnect it manually. A box with no screen cannot ask for this. ` +
          "Enable automatic retries to set the connection to retry without limit. Wrong passwords are also retried. You can undo this here or by uninstalling. ",
        actionButton(
          "Enable automatic retries",
          () => {
            netRequest({ action: "profile", on: true }, "up");
          },
          { disabled: busy },
        ),
      ),
    );
  } else {
    net.profile.replaceChildren();
  }
  document.getElementById("up-retries-group").hidden =
    !document.getElementById("net-retry-defaults").childElementCount &&
    !net.profile.childElementCount;
  const changes = ["net-handover", "net-other-ap", "net-wifisync"].some(
    (id) => document.getElementById(id).childElementCount,
  );
  document.getElementById("net-changes").hidden = !changes;
  applyChangesHidden();
  const lead = document.getElementById("net-changes-lead");
  lead.hidden = !(inv && inv.uplink && inv.uplink.backend === "networkmanager");
  lead.textContent =
    "NetworkManager runs this box's WiFi and network connections, and irate-box makes its changes through it.";
  const evs = (uplink && uplink.events) || [];
  // One line per event: when, then what.
  AW.drawKept(net.events, [
    ...evs
      .slice(-25)
      .reverse()
      .map((entry) =>
        el(
          "li",
          {},
          el("time", {
            className: "ev-time",
            dateTime: new Date(entry.at * 1000).toISOString(),
            textContent: new Date(entry.at * 1000).toLocaleString([], {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            }),
          }),
          el("span", {
            className: "ev-text",
            textContent:
              entry.iface &&
              Object.values((uplink && uplink.links) || {}).filter((l) => l.watched)
                .length > 1
                ? `${entry.iface}: ${entry.text}`
                : entry.text,
          }),
        ),
      ),
  ]);
  const down = uplink && !uplink.stale && uplink.state !== "up";
  badge("network", down ? "!" : "");

  const stale = !inv || Date.now() / 1000 - inv.at > 60 * 60;
  if (stale && !busy && !netAsked && paneShown("network")) {
    netAsked = true;
    netRequest({ action: "scan" }, "scan");
    return;
  }
  clearTimeout(netPoll);
  netPoll = setTimeout(
    loadNetwork,
    busy ? 2000 : paneShown("network") ? 10000 : 60000,
  );
}

async function loadNetwork() {
  try {
    renderNetwork(await getJSON("/admin/network"));
  } catch (err) {
    console.error("network pane:", err);
    net.when.textContent = "Could not read the network report.";
    clearTimeout(netPoll);
    netPoll = setTimeout(loadNetwork, 30000);
  }
}

// NetworkManager's box defaults: each with the value in use and the file it comes from; a change goes
// to irate-box's own drop-in. Choices kept while the page polls, until saved.
let defaultsScope = ""; // '' the whole box, else an interface
const PER_IFACE = ["auth_retries", "cloned_mac", "powersave", "scan_mac"];
const RETRY_DEFAULTS = ["autoconnect_retries", "auth_retries"];
// One section for the box defaults, the retries beside Network recovery included: one Save and one Discard, under
// the defaults' card. Each value is a setting's choice as the page offers it ("default": irate-box sets nothing).
let defaultsSavedKey = "";
const defSec = AW.section({
  id: "net-defaults",
  label: "NetworkManager defaults",
  saved: null,
  warn: () =>
    "NetworkManager reads them at once. Connections use them the next time they connect.",
  save: (changes) => {
    const j = AW.job();
    netRequest(
      {
        action: "defaults",
        changes,
        ...(defaultsScope ? { iface: defaultsScope } : {}),
      },
      "defaults",
      j.done,
    );
    return j;
  },
  onChange: () => renderDefaults(netData && netData.inventory, false),
});
document
  .getElementById("net-defaults-foot")
  .append(defSec.foot, defSec.reason);
document.getElementById("net-retry-go").addEventListener("click", () => {
  const card = document.getElementById("net-defaults-card");
  if (card.scrollIntoView) card.scrollIntoView({ block: "center" });
});
// The WiFi country (hub/wificountry.py): the kernel's now, and the one saved for boot, chosen from tzdata's list.
// A setting: it asks nothing. A warning beside it says where the box and the choice disagree (Tom, 2026-10-21).
// Drawn again only when what it shows changes, so a choice not yet saved isn't lost to the page's polling.
let countryShown = null;
function renderCountry(inv, busy) {
  const box = document.getElementById("net-country");
  if (!box || !inv || !(inv.countries || []).length) return;
  const now = inv.country && inv.country !== "00" ? inv.country : null;
  const saved = inv.country_saved || null;
  const shown = JSON.stringify([now, saved, busy]);
  if (shown === countryShown) return;
  countryShown = shown;
  const names = Object.fromEntries(inv.countries);
  const nameOf = (code) => (names[code] ? `${names[code]} (${code})` : code);
  // Why the radios may not use the country set: an access point announcing another (a client takes it on, and the
  // kernel allows only what both do: its global country reads 98), or a radio that keeps its own (self-managed).
  const announcing = (inv.radios || []).filter((radio) => radio.link && radio.link.country);
  const ownCountry = (inv.radios || []).filter(
    (radio) => ((inv.country_by_phy || {})[radio.phy] || {}).self_managed,
  );
  setupStep(
    "country",
    saved || (now && now !== "98") ? `${nameOf(saved || now)}.` : "No WiFi country is set.",
    saved || now ? "ok" : "warn",
  );
  box.replaceChildren(
    AW.settings(
      [
        {
          key: "country",
          label: "Country",
          kind: "select",
          value: saved || (now && now !== "98" ? now : ""),
          options: [
            ["", "Not set"],
            ...inv.countries.map(([code, name]) => [code, `${name} (${code})`]),
          ],
          warn: (value) => {
            if (!value)
              return now
                ? null
                : "No WiFi country is set. The radios keep to the channels and power allowed everywhere, so the hotspot has fewer channels.";
            const said = [];
            const other = announcing.find((radio) => radio.link.country !== value);
            if (other)
              said.push(
                `The WiFi network that ${other.iface} joins says it is in ${nameOf(other.link.country)}, so the radios use only the channels both countries allow.`,
              );
            if (ownCountry.length)
              said.push(
                `${ownCountry.map((radio) => radio.iface).join(" and ")} ${ownCountry.length === 1 ? "keeps a country of its own" : "keep a country of their own"} and ${ownCountry.length === 1 ? "ignores" : "ignore"} this setting.`,
              );
            if (!said.length && now && now !== value && now !== "98")
              said.push(
                `The radios use ${nameOf(now)} now, not ${nameOf(value)}. The next start applies the saved one.`,
              );
            return said.join(" ") || null;
          },
        },
      ],
      {
        id: "net-country",
        label: "WiFi country",
        why: (changes, values) =>
          !values.country ? "Select a country." : null,
        save: (changes, values) => {
          const j = AW.job();
          netRequest({ action: "country", country: values.country }, "country", j.done);
          return j;
        },
      },
    ),
  );
}

function renderDefaults(inv, busy) {
  const manager =
    inv &&
    inv.stacks &&
    inv.stacks.networkmanager &&
    inv.stacks.networkmanager.defaults;
  const card = document.getElementById("net-defaults-card");
  card.hidden = !manager;
  if (!manager) return;
  const own = (path) => (path || "").split("/").pop();
  const ifaces = Object.keys(manager.ifaces || {});
  if (defaultsScope && !ifaces.includes(defaultsScope)) defaultsScope = "";
  const scope = el(
    "select",
    { "aria-label": "Which connections", disabled: busy },
    el("option", { value: "", textContent: "Every connection on the box" }),
    ...ifaces.map((i) =>
      el("option", { value: i, textContent: `Connections on ${i} only` }),
    ),
  );
  scope.value = defaultsScope;
  scope.addEventListener("change", async (event) => {
    if (
      defSec.dirty() &&
      !await AW.confirmHere("Discard the unsaved changes to the box defaults?", { anchor: event.currentTarget })
    ) {
      scope.value = defaultsScope;
      return;
    }
    defSec.discard();
    defaultsScope = scope.value;
    renderDefaults(netData && netData.inventory, false);
  });
  const savedNow = {};
  const names = defaultsScope ? PER_IFACE : Object.keys(manager.settings);
  const mineIf = (manager.ifaces_mine || {})[defaultsScope] || {};
  const rows = names.map((name) => {
    const setting = manager.settings[name];
    const sel = el("select", {
      "aria-label": setting.label,
      disabled: busy || defSec.disabled(),
    });
    const boxNow =
      setting.value === null
        ? `NetworkManager's own: ${setting.nm_default}`
        : setting.values[setting.value] || setting.value;
    // "default" means irate-box sets nothing here: said as what applies then, not as NetworkManager's
    // own when another file (the image's) sets it.
    const other =
      setting.value !== null && setting.mine === null ? "Use current value" : null;
    const opts = [
      [
        "default",
        defaultsScope
          ? "Use box default"
          : other || "Use NetworkManager default",
      ],
      ...Object.entries(setting.values).map(([v, l]) => [v, cap1(l)]),
    ];
    const cur = defaultsScope ? (mineIf[name] ?? null) : setting.mine;
    if (setting.kind === "count" && cur !== null && !(cur in setting.values))
      opts.push([cur, `${cur} tries`]);
    for (const [v, label] of opts)
      sel.append(el("option", { value: v, textContent: label }));
    const known = (v) => (opts.some(([o]) => o === v) ? v : "default");
    savedNow[name] = known(cur !== null ? cur : "default");
    const chosen = defSec.loaded() ? defSec.get(name) : undefined;
    sel.value = chosen !== undefined ? known(chosen) : savedNow[name];
    sel.addEventListener("change", () => defSec.set(name, sel.value, true));
    let now;
    if (defaultsScope) {
      const effective = manager.ifaces[defaultsScope][name];
      now =
        effective.value === null
          ? `now NetworkManager's own: ${setting.nm_default}`
          : `now ${setting.values[effective.value] || effective.value}, from ${
              effective.section && effective.section.includes("-irate-box-")
                ? `irate-box's setting for ${defaultsScope}`
                : `${own(effective.file)} [${effective.section}]`
            }${effective.unsure ? " (if it matches this device)" : ""}`;
    } else {
      now =
        setting.value === null
          ? `now NetworkManager's own: ${setting.nm_default}`
          : `now ${setting.values[setting.value] || setting.value}, set by ${setting.mine !== null ? "irate-box's file" : own(setting.file)}`;
    }
    // What each word choice means, said once under them: the box default, NetworkManager's own.
    const means = [
      defaultsScope ? `the box default is ${boxNow}` : null,
      setting.value !== null || defaultsScope
        ? `NetworkManager's own is ${setting.nm_default}`
        : null,
    ].filter(Boolean);
    const row = el(
      "label",
      { className: "net-default" },
      el("span", { className: "setting-name", textContent: setting.label }),
      sel,
      el("span", {
        className: "setting-desc",
        textContent:
          cap1(now) + "." + means.map((m) => ` ${cap1(m)}.`).join(""),
      }),
    );
    row.dataset.name = name;
    return row;
  });
  // The retries go beside the watchdog's own (Network recovery), for the whole box.
  const retries = defaultsScope
    ? []
    : rows.filter((row) => RETRY_DEFAULTS.includes(row.dataset.name));
  document.getElementById("net-retry-defaults").replaceChildren(...retries);
  document.getElementById("up-retries-group").hidden =
    !retries.length && !net.profile.childElementCount;
  rows.unshift(
    el(
      "label",
      { className: "net-default net-default-scope" },
      el("span", { className: "setting-name", textContent: "For" }),
      scope,
      el("span", {
        className: "setting-desc",
        textContent: defaultsScope
          ? `Only what NetworkManager takes per interface. The retry setting applies to the whole box.`
          : "A connection's own setting, or one for its interface, comes first.",
      }),
    ),
  );
  const notes = [
    ...manager.legacy.map(
      (l) =>
        `${own(l.file)} also sets ${l.key}=${l.value} (an older name NetworkManager still reads: ${l.means}).`,
    ),
    ...manager.scoped.map(
      (x) =>
        `${own(x.file)} sets ${Object.keys(x.keys).join(", ")} for some devices only (${x.match || x.section}).`,
    ),
    ...manager.broken.map(
      (path) => `${path} could not be read. NetworkManager can also fail to read it.`,
    ),
  ];
  net.defaults.replaceChildren(
    ...rows.filter((row) => !retries.includes(row)),
    ...(notes.length
      ? [
          el(
            "ul",
            { className: "setting-desc" },
            ...notes.map((t) => el("li", { textContent: t })),
          ),
        ]
      : []),
  );
  // The saved values, each time they (or the scope) change: a choice not saved yet is kept over them.
  const key = defaultsScope + "|" + JSON.stringify(savedNow);
  if (key !== defaultsSavedKey && !defSec.saving()) {
    defaultsSavedKey = key;
    defSec.update(savedNow);
  }
  // What the root helper did; a failure is said under Save.
  const dn = netNotes.defaults;
  say(dn && dn.ok ? dn.text : "", true, net.defaultsNote);
}
// mPWRD-OS's wifisync, which switches the WiFi radio off while meshtasticd's WiFi setting is off: a switch, with
// undo back to how the image had it. Shown only where it is installed.
function renderWifisync(inv, data, busy) {
  const box = document.getElementById("net-wifisync");
  const h = inv && netHazards(inv).find((x) => x.id === "wifisync");
  const rec = data.wifisync || {};
  if (!h) {
    box.replaceChildren();
    return;
  }
  const on = h.status !== "ok";
  const rows = [
    el(
      "p",
      { className: "setting-desc", dataset: { change: "wifisync" } },
      `The mPWRD-OS wifisync service is ${on ? "enabled" : "disabled"}. ${
        on
          ? "It switches the WiFi radio off whenever the WiFi setting of meshtasticd is off. " +
            "This disconnects the box from its network and stops the hotspot."
          : "It leaves the WiFi radio alone."
      } `,
      actionButton(
        on ? "Disable wifisync" : "Enable wifisync",
        () =>
          netRequest({ action: "wifisync", do: on ? "off" : "on" }, "wifisync"),
        { className: "small", disabled: busy },
      ),
      rec.was ? " " : null,
      rec.was
        ? actionButton(
            `Undo: ${rec.was === "enabled" ? "enabled" : "disabled"}, as the image had it`,
            () => netRequest({ action: "wifisync", do: "undo" }, "wifisync"),
            { className: "small", disabled: busy },
          )
        : null,
    ),
  ];
  const n = netNotes.wifisync;
  if (n)
    rows.push(
      el("p", {
        className: `setting-desc action-note${n.ok ? "" : " bad"}`,
        role: "status",
        textContent: n.text,
      }),
    );
  box.replaceChildren(...rows);
}

// Hotspot profiles that are not irate-box's (an image's setup hotspot): removed (kept for undo), or left
// and no longer listed. Started, one would take the box off its own network.
function renderOtherAp(inv, data, busy) {
  const box = document.getElementById("net-other-ap");
  const nm = inv && inv.stacks && inv.stacks.networkmanager;
  const chosen = data.other_ap || {};
  const others = ((nm && nm.wifi_profiles) || []).filter(
    (profile) => profile.mode === "ap" && profile.name !== "irate-box-ap",
  );
  const rows = [];
  for (const other of others) {
    const c = chosen[other.uuid];
    const what = `an earlier hotspot profile, ${other.name} (network name ${other.ssid})${/^armbian/i.test(other.ssid || "") ? ", Armbian's setup hotspot" : ", probably the system's own setup hotspot"}`;
    if (c && c.choice === "left") {
      rows.push(
        el(
          "p",
          {
            className: "setting-desc",
            dataset: { change: `otherap:${other.uuid}` },
          },
          `Ignored: ${what}. The profile is not disabled. `,
          actionButton(
            "Stop ignoring profile",
            () =>
              netRequest(
                { action: "otherap", uuid: other.uuid, do: "undo" },
                "otherap",
              ),
            { className: "small", disabled: busy },
          ),
        ),
      );
      continue;
    }
    rows.push(
      el(
        "p",
        { className: "setting-desc", dataset: { change: `otherap:${other.uuid}` } },
        `This box has ${what}. ` +
          (other.active
            ? "It is running now. It holds the WiFi, so the network link of the box and the irate-box hotspot cannot use it. "
            : other.autoconnect
              ? "It is set to connect automatically. At boot it can take over the WiFi and disconnect the box from its network. "
              : "It is disabled and does not connect automatically, so irate-box has the WiFi and runs its own hotspot. ") +
          "Delete the profile (the box keeps a copy that you can restore) or ignore it. Ignoring does not disable the profile. ",
        actionButton(
          "Delete profile",
          () => {
            netRequest(
                { action: "otherap", uuid: other.uuid, do: "remove" },
                "otherap",
              );
          },
          { className: "small", disabled: busy },
        ),
        " ",
        actionButton(
          "Ignore profile",
          () =>
            netRequest(
              { action: "otherap", uuid: other.uuid, do: "leave" },
              "otherap",
            ),
          { className: "small", disabled: busy },
        ),
      ),
    );
  }
  for (const [uuid, c] of Object.entries(chosen)) {
    if (c.choice !== "removed") continue;
    rows.push(
      el(
        "p",
        { className: "setting-desc", dataset: { change: `otherap:${uuid}` } },
        `The earlier hotspot profile ${c.name} (network name ${c.ssid}) was deleted on ${new Date(c.at * 1000).toLocaleDateString()}. The box keeps a copy. `,
        actionButton(
          "Restore profile",
          () => netRequest({ action: "otherap", uuid, do: "undo" }, "otherap"),
          { className: "small", disabled: busy },
        ),
        " ",
        actionButton(
          "Delete retained copy permanently",
          async (event) => {
            if (
              await AW.confirmHere(
                `Delete the retained copy of ${c.name} permanently? You cannot restore it afterwards.`, { anchor: event.currentTarget })
            )
              netRequest({ action: "otherap", uuid, do: "forget" }, "otherap");
          },
          { className: "small", disabled: busy },
        ),
      ),
    );
  }
  const n = netNotes.otherap;
  if (n)
    rows.push(
      el("p", {
        className: `setting-desc action-note${n.ok ? "" : " bad"}`,
        role: "status",
        textContent: n.text,
      }),
    );
  box.replaceChildren(...rows);
}

// The networks NetworkManager has seen, to pick one rather than type its name: the form filled with its
// name and security, the password left to type. A fresh scan on a radio the hotspot shares can move it.
function renderVisible(inv, busy) {
  const box = document.getElementById("net-visible");
  const nm = inv && inv.stacks && inv.stacks.networkmanager;
  const nets = (nm && nm.visible) || [];
  const shared =
    inv &&
    inv.radios.some(
      (radio) =>
        radio.type === "AP" &&
        inv.radios.some((m) => m.type === "managed" && m.phy === radio.phy),
    );
  const scanAgain = actionButton(
    "Run scan",
    () => netRequest({ action: "scan", rescan: true }, "scan"),
    { disabled: busy, className: "small" },
  );
  const fields = joinForm.elements;
  box.replaceChildren(
    el(
      "p",
      { className: "setting-desc" },
      nets.length
        ? `Networks the box can see (from ${inv ? new Date(inv.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "its last look"}). Select one, then enter its password. `
        : "No networks were seen in the last scan. ",
      scanAgain,
      shared
        ? " The hotspot shares this radio, so guests can lose it for a moment."
        : "",
    ),
    ...(nets.length
      ? [
          el(
            "div",
            { className: "table-scroll" },
            el(
              "table",
              { className: "admin-table net-visible-table" },
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  ...[
                    "Network",
                    "Signal",
                    "Band",
                    "Channel",
                    "Access points",
                    "Security",
                    "",
                  ].map((h) => el("th", { textContent: h })),
                ),
              ),
              el(
                "tbody",
                {},
                ...nets.slice(0, 25).map((n) =>
                  el(
                    "tr",
                    {},
                    el(
                      "td",
                      {},
                      el("strong", { textContent: n.ssid }),
                      n.known
                        ? el("span", {
                            className: "info-pill",
                            textContent: "known",
                          })
                        : null,
                    ),
                    el("td", { className: "num", textContent: `${n.signal}%` }),
                    el("td", { textContent: n.bands.join(", ") }),
                    el("td", {
                      className: "num",
                      textContent: n.channels.join(", "),
                    }),
                    el("td", {
                      className: "num",
                      textContent: String(n.aps || 1),
                    }),
                    el("td", { textContent: n.security || "open" }),
                    el(
                      "td",
                      {},
                      n.kind
                        ? actionButton(
                            "Use this network",
                            () => {
                              fields.ssid.value = n.ssid;
                              fields.security.value = n.kind;
                              fields.hidden.checked = false;
                              joinOpen();
                              (n.kind === "open" ? fields.ssid : fields.psk).focus();
                            },
                            {
                              className: "small",
                              disabled: busy || n.known,
                              title: n.known ? "Already known" : "",
                            },
                          )
                        : el("span", {
                            className: "setting-desc",
                            textContent: "needs a sign-in (802.1X)",
                          }),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ]
      : []),
  );
}

// One connection's own settings: each with its value now and the choices, the risky side said; a change
// goes to that connection's profile (where it lasts) and takes effect the next time it connects.
const CONN_FIELDS = [
  [
    "autoconnect",
    "Autoconnect",
    [
      ["yes", "yes"],
      ["no", "no, manual connection only"],
    ],
    (profile) => (profile.autoconnect ? "yes" : "no"),
    "Disabled: the box never joins this network automatically.",
  ],
  [
    "priority",
    "Connection priority",
    "num:-999:999",
    (profile) => String(profile.priority ?? 0),
    "A higher number wins when several known networks are in range. 0 is the NetworkManager default.",
  ],
  [
    "autoconnect_retries",
    "Connection retries",
    [
      ["-1", "use box default"],
      ["0", "without limit"],
      ["1", "once"],
      ["4", "4 times, then 5 minutes blocked"],
    ],
    (profile) => String(profile.autoconnect_retries ?? -1),
    "",
  ],
  [
    "auth_retries",
    "Authentication retries",
    [
      ["-1", "use box default"],
      ["0", "without limit"],
      ["3", "3 times, then waits for manual reconnection"],
    ],
    (profile) => String(profile.auth_retries ?? -1),
    "Without limit also means that a wrong password is retried without limit.",
  ],
  [
    "metered",
    "Metered connection",
    [
      ["unknown", "NetworkManager detects it"],
      ["yes", "yes"],
      ["no", "no"],
    ],
    (profile) => profile.metered || "unknown",
    "For the hotspot of a phone, select yes. Updates and library downloads then wait for another network.",
  ],
  [
    "cloned_mac",
    "Randomised MAC",
    [
      ["", "use box default"],
      ["permanent", "the hardware address of the board"],
      ["preserve", "as the device has it"],
      ["random", "random each time"],
      ["stable", "stable, generated for this connection"],
      ["stable-ssid", "stable, generated for each network"],
    ],
    (profile) => profile.cloned || "",
    "Random: the router sees a new device each time. A reserved address or MAC filter stops working.",
  ],
  [
    "bssid",
    "Access-point lock",
    "bssid",
    (profile) => profile.bssid_lock || "",
    "Locked: the box cannot roam. If that access point goes away, the box stays off the network.",
  ],
  [
    "band",
    "Band",
    [
      ["", "any"],
      ["bg", "2.4 GHz only"],
      ["a", "5 GHz only"],
    ],
    (profile) => profile.band_lock || "",
    "2.4 GHz reaches further and is busier. 5 GHz is faster with less reach. A hotspot on the same radio follows it.",
  ],
  [
    "channel",
    "Channel",
    "num:0:196",
    (profile) => String(profile.channel ?? 0),
    "0 is any. A band must be selected.",
  ],
  [
    "powersave",
    "Power saving",
    [
      ["0", "use box default"],
      ["2", "disabled"],
      ["3", "enabled"],
      ["1", "leave to the driver"],
    ],
    (profile) => String(profile.powersave ?? 0),
    "Enabled: the box answers more slowly, and some drivers disconnect.",
  ],
  [
    "hidden",
    "Hidden network",
    [
      ["no", "no"],
      ["yes", "yes"],
    ],
    (profile) => (profile.hidden ? "yes" : "no"),
    "Yes when the network does not announce its name.",
  ],
  [
    "dhcp_timeout",
    "Address timeout (s)",
    "num:0:3600",
    (profile) => String(profile.dhcp_timeout ?? 0),
    '0 is the NetworkManager default of 45 s. A longer time can hide a dead link as "connecting".',
  ],
];
const connOpen = new Set();
// One network's own settings: a section each (its id the connection's), drawn again with the list and its draft kept.
function connSettings(profile, inv, data, busy, radio) {
  const ho = inv && inv.handover;
  const lasts = !profile.netplan || (ho && ho.integrated);
  const changed = (data.conn_changes || {})[profile.uuid];
  const sec = AW.section({
    id: `net-conn-${profile.uuid}`,
    label: `Settings for ${profile.ssid || profile.name}`,
    saved: Object.fromEntries(CONN_FIELDS.map(([n, , , now]) => [n, now(profile)])),
    warn: () =>
      "They take effect the next time the box connects. Undo restores the previous settings.",
    save: (changes) => {
      const j = AW.job();
      netRequest(
        { action: "connection", uuid: profile.uuid, changes },
        "connection",
        j.done,
      );
      return j;
    },
    onChange: () => netData && renderNetwork(netData),
  });
  const fields = CONN_FIELDS.map(([name, label, kind, now, help]) => {
    let input;
    const cur = now(profile);
    if (Array.isArray(kind)) {
      const opts = kind.some(([v]) => v === cur) ? kind : [...kind, [cur, cur]];
      input = el(
        "select",
        { "aria-label": label, disabled: busy || !lasts },
        ...opts.map(([v, t]) => el("option", { value: v, textContent: t })),
      );
    } else if (kind === "bssid") {
      const aps = new Set(
        [cur, radio && radio.link && radio.link.bssid]
          .filter(Boolean)
          .map((b) => b.toLowerCase()),
      );
      input = el(
        "select",
        { "aria-label": label, disabled: busy || !lasts },
        el("option", { value: "", textContent: "any (roams)" }),
        ...[...aps].map((b) =>
          el("option", {
            value: b,
            textContent: `${b}${radio && radio.link && radio.link.bssid && radio.link.bssid.toLowerCase() === b ? " (on it now)" : ""}`,
          }),
        ),
      );
    } else {
      const [, lo, hi] = kind.split(":");
      input = el("input", {
        type: "number",
        min: lo,
        max: hi,
        step: 1,
        "aria-label": label,
        disabled: busy || !lasts,
      });
    }
    input.value = sec.get(name) ?? cur;
    input.addEventListener("change", () => sec.set(name, input.value, true));
    return el(
      "label",
      { className: "net-default" },
      el("span", { className: "setting-name", textContent: label }),
      input,
      help
        ? el("span", { className: "setting-desc", textContent: help })
        : null,
    );
  });
  const cn = netNotes.connection;
  const box = el(
    "details",
    { className: "net-conn-settings", open: connOpen.has(profile.uuid) },
    el("summary", { textContent: "Settings for this network" }),
    lasts
      ? null
      : el("p", {
          className: "setting-desc",
          textContent:
            "netplan writes this connection again at every boot, so a setting for this connection alone is lost. Select Manage with NetworkManager (above) to change these settings.",
        }),
    changed
      ? el(
          "p",
          { className: "setting-desc" },
          `Changed here: ${changed.changed.join(", ")}. `,
          actionButton(
            "Undo",
            () => {
              netRequest(
                  { action: "connection", uuid: profile.uuid, undo: true },
                  "connection",
                );
            },
            { disabled: busy },
          ),
        )
      : null,
    el("div", { className: "net-defaults" }, ...fields),
    ...(lasts ? [sec.foot, sec.reason] : []),
    cn && cn.ok
      ? el("p", {
          className: `setting-desc action-note${cn.ok ? "" : " bad"}`,
          role: "status",
          textContent: cn.text,
        })
      : null,
  );
  box.addEventListener("toggle", () => {
    if (box.open) connOpen.add(profile.uuid);
    else connOpen.delete(profile.uuid);
  });
  return box;
}

// Connections netplan makes: on a box whose NetworkManager cannot write changes back into netplan,
// offered to be handed over (and back), so a connection's own settings last.
function renderHandover(inv, busy) {
  const ho = inv && inv.handover;
  const rows = [];
  if (ho && ho.integrated) {
    rows.push(
      el("p", {
        className: "setting-desc",
        textContent:
          "NetworkManager here keeps its connections in netplan itself, so a change made to one connection lasts.",
      }),
    );
  } else if (ho && !ho.error) {
    for (const file of ho.files || []) {
      const names = file.connections.map((c) => c.name).join(", ");
      if (file.ok) {
        rows.push(
          el(
            "p",
            {
              className: "setting-desc",
              dataset: { change: `handover:${file.name}` },
            },
            `${names || "Your connection"}: at every boot, netplan writes ${file.connections.length === 1 ? "it" : "them"} again from the file ${file.name}. This undoes any change made here. ` +
              "Select Manage with NetworkManager to keep changes permanently. The connection keeps the same name, password and settings. The box keeps the netplan file aside, so you can undo this. ",
            actionButton(
              "Manage with NetworkManager",
              () => {
                netRequest({ action: "handover", name: file.name }, "handover");
              },
              { disabled: busy },
            ),
          ),
        );
      } else {
        rows.push(
          el("p", {
            className: "setting-desc",
            textContent: `${file.name}: ${file.why}`,
          }),
        );
      }
    }
    for (const handover of ho.done || []) {
      const conns = (handover.connections || [])
        .map((c) =>
          (c.keyfile || "")
            .split("/")
            .pop()
            .replace(/\.nmconnection$/, ""),
        )
        .filter(Boolean)
        .join(", ");
      rows.push(
        el(
          "p",
          {
            className: "setting-desc",
            dataset: { change: `handover:${handover.name}` },
          },
          `${conns ? `Your connection ${conns} is` : "Your connections are"} managed by NetworkManager since ${new Date(handover.at * 1000).toLocaleDateString()}. ` +
            `Before, netplan wrote ${conns ? "it" : "them"} again from the file ${handover.name} at every boot, which undid any change made here. The box keeps that file aside. `,
          actionButton(
            "Restore netplan management",
            () => {
              netRequest(
                  { action: "handover", name: handover.name, undo: true },
                  "handover",
                );
            },
            { disabled: busy },
          ),
        ),
      );
    }
  }
  const hn = netNotes.handover;
  if (hn)
    rows.push(
      el("p", {
        className: `setting-desc action-note${hn.ok ? "" : " bad"}`,
        role: "status",
        textContent: hn.text,
      }),
    );
  net.handover.replaceChildren(...rows);
}

// The changes made here are old news once set up: each can be hidden (a hub setting, the same on every
// device), and one button shows the hidden ones again for a look.
let changesHidden = null,
  changesShowAll = false;
getJSON("/admin/settings")
  .then((st) => {
    changesHidden = st.net_changes_hidden || [];
    applyChangesHidden();
  })
  .catch(() => {});
async function setChangesHidden(list) {
  try {
    changesHidden = (
      await postJSON("/admin/settings", { net_changes_hidden: list })
    ).net_changes_hidden;
  } catch (_) {
    /* kept as it was */
  }
  applyChangesHidden();
}
function applyChangesHidden() {
  const sec = document.getElementById("net-changes");
  if (!sec || changesHidden === null) return;
  const rows = [...sec.querySelectorAll("[data-change]")];
  let hiddenNow = 0;
  for (const row of rows) {
    const id = row.dataset.change,
      isHidden = changesHidden.includes(id);
    row.hidden = isHidden && !changesShowAll;
    hiddenNow += isHidden;
    let b = row.querySelector(":scope > .change-hide");
    if (!b) {
      b = el("button", {
        type: "button",
        className: "link-button change-hide",
      });
      row.append(" ", b);
    }
    b.textContent = isHidden ? "Show this change" : "Hide this change";
    b.title = isHidden
      ? "Show this change here again from now on"
      : "Hide this change here. The change stays as it is. Select Show hidden changes to see it.";
    b.onclick = () =>
      setChangesHidden(
        isHidden
          ? changesHidden.filter((x) => x !== id)
          : [...changesHidden, id],
      );
  }
  // A part whose every change is hidden goes too, box and all.
  for (const id of ["net-handover", "net-other-ap", "net-wifisync"]) {
    const part = document.getElementById(id),
      own = part ? [...part.querySelectorAll("[data-change]")] : [];
    if (part) part.hidden = own.length > 0 && own.every((change) => change.hidden);
  }
  let more = document.getElementById("net-changes-more");
  if (!more) {
    more = el("button", {
      type: "button",
      className: "action-btn small",
      id: "net-changes-more",
    });
    more.addEventListener("click", () => {
      changesShowAll = !changesShowAll;
      applyChangesHidden();
    });
    sec.append(more);
  }
  more.hidden = !hiddenNow;
  more.textContent = changesShowAll
    ? "Hide changes again"
    : `Show hidden changes (${hiddenNow})`;
  // All hidden and folded: the section's heading alone, with its button.
  const lead = document.getElementById("net-changes-lead");
  if (lead)
    lead.dataset.allHidden = String(
      hiddenNow === rows.length && !changesShowAll,
    );
}

// What a review item on this page can do here: another hotspot profile, delete or ignore it (as under Changes made
// here); any review item, accept it as it is (or take that back), on the Box doctor's list as "net:<id>".
function hazardControls(h) {
  const btns = [];
  const ap = /^other-ap:(.+)$/.exec(h.id || "");
  if (ap && h.status === "warn") {
    btns.push(
      actionButton(
        "Delete profile",
        () => {
          netRequest(
              { action: "otherap", uuid: ap[1], do: "remove" },
              "otherap",
            );
        },
        { className: "small" },
      ),
      " ",
      actionButton(
        "Ignore profile",
        () =>
          netRequest(
            { action: "otherap", uuid: ap[1], do: "leave" },
            "otherap",
          ),
        { className: "small" },
      ),
    );
  }
  if (h.status === "warn" || h.status === "accepted") {
    const on = h.status === "accepted";
    btns.push(
      " ",
      el("button", {
        type: "button",
        className: "action-btn small",
        textContent: on ? "Reopen" : "Accept",
        title: on
          ? "Return this item to the review list."
          : "Leave it as it is and take it out of the review count. The Box doctor shows it as accepted too.",
        onclick: async () => {
          try {
            await postJSON("/admin/health", {
              action: on ? "unaccept" : "accept",
              key: `net:${h.id}`,
              title: h.title,
            });
            loadNetwork();
          } catch (error) {
            say(error.message, false, net.scanNote);
          }
        },
      }),
    );
  }
  return btns.length
    ? el("p", { className: "library-buttons" }, ...btns)
    : null;
}

// onDone(ok, why): told whether the root helper did it (a draft cleared only then), or false when the hub refused it.
async function netRequest(body, where, onDone) {
  try {
    netWaiting = {
      id: (await postJSON("/admin/network", body)).id,
      where,
      onDone,
    };
    delete netNotes[where];
  } catch (err) {
    netNotes[where] = { text: err.message, ok: false };
    if (onDone) onDone(false, err.message);
  }
  loadNetwork();
}

net.scan.addEventListener("click", () =>
  netRequest({ action: "scan" }, "scan"),
);
// A network for the box to join: added with consent, at once only when asked (and then said what it means).
const joinForm = document.getElementById("net-join-form");
const joinPsk = document.getElementById("net-join-psk");
const joinOpen = () => {
  joinPsk.hidden = joinForm.elements.security.value === "open";
  joinForm.elements.psk.required = !joinPsk.hidden;
};
joinForm.elements.security.addEventListener("change", joinOpen);
joinOpen();
function joinAsk(now) {
  const fields = joinForm.elements;
  if (!joinForm.reportValidity()) return;
  const ssid = fields.ssid.value;
  // What each button does is said under them (#net-join-says): it acts at once.
  netRequest(
    {
      action: "join",
      ssid,
      security: fields.security.value,
      psk: fields.security.value === "open" ? "" : fields.psk.value,
      hidden: fields.hidden.checked,
      now,
    },
    "join",
  );
  fields.psk.value = "";
}
joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  joinAsk(false);
});
document
  .getElementById("net-join-now")
  .addEventListener("click", () => joinAsk(true));
// One question at Save for what needs saying, from the settings as they would be saved.
// What a change of the watchdog's levels does, beside Save.
function upWarn(all) {
  let settings = all;
  try {
    settings = readUpForm();
  } catch (_) {
    /* said under Save already; save() says it again */
  }
  const was = (netData && netData.uplink && netData.uplink.chosen) || {};
  const asks = [];
  if (
    settings.roaming === "lock" &&
    (was.roaming !== "lock" || was.lock_bssid !== settings.lock_bssid)
  )
    asks.push(
      `The box reconnects to ${settings.lock_bssid} and stays with it, briefly off the network while it does. If that access point does not answer, the box removes the lock.`,
    );
  if (settings.roaming === "no-scan" && was.roaming !== "no-scan")
    asks.push(
      "No background scans while the hotspot shares the radio. This changes the running WiFi settings, not the WiFi profile.",
    );
  if (
    settings.guests === "ignore" &&
    ["restart", "radio", "reboot"].includes(settings.reach)
  )
    asks.push(
      `The box can ${settings.reach === "restart" ? "restart the network service" : settings.reach === "radio" ? "reset the radio" : "reset the radio and reboot the box"} while guests are on the hotspot.`,
    );
  return asks.length ? asks.join(" ") : null;
}
function upSave() {
  const settings = readUpForm(); // a number that is not one: its reason under Save
  const j = AW.job();
  netRequest({ action: "settings", settings }, "up", j.done);
  return j;
}
net.hold.addEventListener("click", () =>
  netRequest({ action: "hold", minutes: 60 }, "up"),
);
net.unhold.addEventListener("click", () =>
  netRequest({ action: "hold", minutes: 0 }, "up"),
);
window.addEventListener("hashchange", () => {
  if (paneShown("network")) loadNetwork();
});

// --- the hotspot itself (root/ap.py, the plan from hub/apmode.py) -------------------------------------
const apEl = {
  state: document.getElementById("ap-state"),
  form: document.getElementById("ap-form"),
  radio: document.getElementById("ap-radio"),
  channel: document.getElementById("ap-channel"),
  take: document.getElementById("ap-take"),
  takeLabel: document.getElementById("ap-take-label"),
  sw: document.getElementById("ap-switch"),
  tryB: document.getElementById("ap-try"),
  confirmB: document.getElementById("ap-confirm"),
  note: noteEl("ap-note"),
};
let apRun = null;
let apWait = null; // {id, until}: a request to the root helper, and when to stop waiting for it
function apFill(sel, values, keep) {
  const want = keep !== undefined ? keep : sel.value;
  sel.replaceChildren(
    el("option", { value: "", textContent: "Automatic" }),
    ...values.map((v) =>
      el("option", { value: String(v), textContent: String(v) }),
    ),
  );
  sel.value = values.map(String).includes(String(want)) ? String(want) : "";
}
function renderAp(run) {
  apRun = run;
  if (netData) drawApHealth();
  const plan = run.up ? run.plan : run.preview;
  const where = (q) =>
    q && q.channel
      ? ` on ${q.iface === "ap0" || q.kind === "own-channel" || q.kind === "follow" ? "a second interface beside " + q.iface : q.iface}, channel ${q.channel}`
      : "";
  apEl.state.textContent = run.up
    ? `On${where(plan)}. ${plan.text}${run.confirmed === false ? " Your WiFi link is disabled. Join the hotspot network and open the hub. Then select Keep hotspot settings. If you do not, the WiFi link returns automatically." : ""}`
    : plan
      ? `Off. Switched on, it would run${where(plan)}. ${plan.text}${plan.to_try ? " Whether it can keep a channel of its own here is still to be tried." : ""}${plan.why ? ` (${plan.why})` : ""}`
      : "Off. The radios have not been checked yet. Select Refresh, above.";
  if (run.note) say(run.note, true, apEl.note);
  const radio = apEl.radio.value;
  apFill(
    apEl.radio,
    (run.radios || []).map((runRadio) => runRadio.iface),
    (run.owner || {}).radio || radio,
  );
  const chans = (
    (run.radios || []).find(
      (runRadio) => runRadio.iface === (apEl.radio.value || (plan && plan.iface)),
    ) ||
    (run.radios || [])[0] || { channels: [] }
  ).channels;
  apFill(apEl.channel, chans, (run.owner || {}).channel);
  apEl.sw.textContent = run.up ? "Disable hotspot" : "Enable hotspot";
  apEl.takeLabel.hidden = run.up || !(plan && plan.needs_choice);
  apEl.tryB.hidden = run.up || !(plan && plan.to_try);
  apEl.confirmB.hidden = !(run.up && run.confirmed === false);
  [apEl.sw, apEl.tryB, apEl.confirmB].forEach((b) => {
    b.disabled = !!apWait;
  });
}
async function loadAp() {
  try {
    const reply = await getJSON("/admin/hotspot");
    renderAp(reply.running || {});
    if (apWait) {
      const done = ((reply.running || {}).at || 0) * 1000 > apWait.since;
      if (done || Date.now() > apWait.until) {
        apWait = null;
        renderAp(reply.running || {});
      } else setTimeout(loadAp, 2000);
    }
  } catch (_) {
    apEl.state.textContent = "Could not read the hotspot.";
  }
}
async function apAct(body) {
  try {
    await postJSON("/admin/hotspot", body);
    apWait = { since: Date.now() - 1000, until: Date.now() + 90000 };
    say("Request sent. The box is applying it…", true, apEl.note);
    renderAp(apRun || {});
    setTimeout(loadAp, 2000);
  } catch (err) {
    say(err.message, false, apEl.note);
  }
}
apEl.form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (apRun && apRun.up) {
    apAct({ action: "off" });
    return;
  }
  const body = { action: "on" };
  if (apEl.radio.value) body.radio = apEl.radio.value;
  if (apEl.channel.value) body.channel = Number(apEl.channel.value);
  if (!apEl.takeLabel.hidden) {
    if (!apEl.take.checked) {
      say(
        "This radio can do only one thing at a time. Select the checkbox to use it for the hotspot, or leave the hotspot disabled.",
        false,
        apEl.note,
      );
      return;
    }
    body.take_radio = true;
  }
  apAct(body);
});
apEl.radio.addEventListener("change", () => renderAp(apRun || {}));
apEl.tryB.addEventListener("click", () => apAct({ action: "try" }));
apEl.confirmB.addEventListener("click", () => apAct({ action: "confirm" }));
window.addEventListener("hashchange", () => {
  if (paneShown("network")) loadAp();
});
if (paneShown("network")) loadAp();
loadNetwork();

// --- the hotspot's own WiFi ----------------------------------------------------------------
// A choice kept in the hub's state (hotspot.py) for the hotspot add-on to apply: open, OWE,
// WPA3 with a published password, or two networks. Modes the radios cannot do are shown, greyed,
// with the reason; the chosen mode's warnings are listed under the choices.
const hs = {
  modes: document.getElementById("hs-modes"),
  fields: document.getElementById("hs-fields"),
  second: document.getElementById("hs-second"),
  secondLabel: document.getElementById("hs-second-label"),
  secondBox: document.getElementById("hs-second-box"),
  password: document.getElementById("hs-password"),
  passwordLabel: document.getElementById("hs-password-label"),
  generate: document.getElementById("hs-generate"),
  wpa2: document.getElementById("hs-wpa2"),
  wpa2Label: document.getElementById("hs-wpa2-label"),
  wpa2Desc: document.getElementById("hs-wpa2-desc"),
  warnings: document.getElementById("hs-warnings"),
  note: document.getElementById("hs-note"),
};
let hsData = null,
  hsSavedKey = "";
// The hotspot's own WiFi as a section: the mode, the second network, the password and WPA2; a reload of the page's
// data keeps a change in progress (and says if the saved settings moved under it).
const hsSec = AW.section({
  id: "hotspot-security",
  label: "Hotspot security",
  saved: null,
  warn: (c, v) =>
    v.mode !== "open" ? "Read the warnings under the choices." : null,
  save: async (c, v) => {
    const reply = await postJSON("/admin/hotspot", { settings: v });
    loadHotspot();
    return resultSaid(reply);
  },
  onChange: () => hsDraw(hsSec.values()),
});
document.getElementById("hs-foot").append(hsSec.foot, hsSec.reason);
// What the page shows, into the draft (the controls drew themselves).
function hsTouched() {
  for (const [k, v] of Object.entries({
    mode: hsChosen(),
    second: hs.second.value,
    password: hs.password.value,
    allow_wpa2: hs.wpa2.checked,
  }))
    hsSec.set(k, v, true);
}
// The draft (or the saved settings) drawn.
function hsDraw(v) {
  if (!hsData || !v) return;
  const data = hsData;
  AW.choices(
    hs.modes,
    "hs-mode",
    data.modes.map((m) => ({
      value: m,
      title: data.label[m],
      desc: data.what[m],
      why: data.available[m],
    })),
    {
      value: v.mode,
      onChange: () => {
        hsTouched();
        hsShowFields();
      },
    },
  );
  hs.second.value = v.second;
  hs.password.value = v.password || "";
  hs.wpa2.checked = !!v.allow_wpa2;
  hsShowFields();
}

function hsChosen() {
  const checked = hs.modes.querySelector('input[name="hs-mode"]:checked');
  return checked ? checked.value : "open";
}

function hsShowFields() {
  if (!hsData) return;
  const mode = hsChosen();
  const needsPw =
    mode === "sae" || (mode === "two" && hs.second.value === "sae");
  // The whole group: the drop-down shows as chips, which are not the <select> and would stay.
  hs.secondBox.hidden = mode !== "two";
  hs.password.parentElement.hidden = hs.passwordLabel.hidden = !needsPw;
  hs.wpa2Label.hidden = !needsPw;
  hs.fields.hidden = mode !== "two" && !needsPw;
  const warn = [...(hsData.warnings[mode] || [])];
  if (mode === "two")
    warn.push(
      ...(hsData.warnings[hs.second.value] || []).filter(
        (w) => !/^Someone can still|^Anyone who knows/.test(w),
      ),
    );
  if (needsPw && hs.wpa2.checked) warn.push(hsData.wpa2_warning);
  hs.warnings.replaceChildren(
    ...warn.map((w) => checkItem("warn", w, "", "")),
    checkItem("warn", hsData.always, "", ""),
  );
}

function renderHotspot(data) {
  const first = !hsData;
  hsData = data;
  hs.wpa2Desc.textContent =
    "Let older WPA2 devices join too (WPA3 transition mode).";
  const settings = data.settings;
  const saved = {
    mode: settings.mode,
    second: settings.second,
    password: settings.password || "",
    allow_wpa2: !!settings.allow_wpa2,
  };
  // Drawn again only when the saved settings change: a field being typed in is not taken away.
  const key = JSON.stringify(saved);
  if (key !== hsSavedKey && !hsSec.saving()) {
    hsSavedKey = key;
    hsSec.update(saved);
  } else if (first) hsDraw(hsSec.values());
  else hsShowFields();
  drawGuestNet(data.share || "off", data.results);
}

// Guests' onward internet (root/share.py): five stops from nobody to everyone, the safest the
// default; held until Save. The "welcome page" is the page a phone is shown when it joins the hotspot.
const GUEST_NET = [
  ["off", "Disabled", "Guests reach the box and nothing else."],
  [
    "users-web",
    "Signed-in devices: web only",
    "A device that is signed in to an account on the hub reaches the web (ports 80 and 443).",
  ],
  [
    "sheet-web",
    "After portal consent: web only",
    "A phone that joins the hotspot is shown the captive portal page of the box. Any device that accepts it reaches the web (ports 80 and 443).",
  ],
  [
    "sheet-all",
    "After portal consent: all permitted traffic",
    "A device that accepts the captive portal page reaches the internet. The containment rules still apply. The box does not forward guest traffic into your local network, into Tailscale or into tunnels, unless an administrator disables them.",
  ],
  [
    "open",
    "All devices: no portal consent",
    "Every device on the hotspot reaches the internet at once, with the same containment rules. The captive portal page stops appearing.",
  ],
];
// Guests' internet as a section of one value. Saved when the box reports the level asked for (the root helper
// applies it); after 40 s without that, the Save is said to have failed and the choice kept.
let guestNetWaiting = null;
const guestSec = AW.section({
  id: "guest-internet",
  label: "Guest internet access",
  saved: null,
  warn: (c) =>
    c.level !== "off"
      ? "Guests' online activity shows as coming from your internet connection."
      : null,
  save: async (c) => {
    await postJSON("/admin/hotspot", { action: "share", level: c.level });
    const j = AW.job();
    guestNetWaiting = { level: c.level, at: Date.now(), job: j };
    setTimeout(loadHotspot, 1500);
    return j;
  },
  onChange: () => drawGuestNet(),
});
document.getElementById("guest-net-foot").append(guestSec.foot, guestSec.reason);
function drawGuestNet(level) {
  const box = document.getElementById("guest-net-chips");
  if (!box) return;
  if (level !== undefined) {
    // Asked: done when the box says the level it was asked for (or, after 40 s, said to look again).
    const w = guestNetWaiting;
    if (w && level === w.level) {
      guestNetWaiting = null;
      w.job.done(true, `Saved: ${GUEST_NET.find((x) => x[0] === level)[2]}`);
    } else if (w && Date.now() - w.at > 40000) {
      guestNetWaiting = null;
      w.job.done(
        false,
        "The box has not applied the change yet. Check the Security doctor, or try again.",
      );
    } else if (w) setTimeout(loadHotspot, 2000);
    if (!guestNetWaiting && guestSec.savedValue("level") !== level)
      return guestSec.update({ level });
  }
  const cur = guestSec.get("level");
  if (!cur) return;
  box.replaceChildren(
    ...GUEST_NET.map(([v, label]) => {
      const b = el("button", {
        type: "button",
        className: "chip" + (v === cur ? " selected" : ""),
        textContent: label,
        disabled: guestSec.disabled(),
        onclick: () => guestSec.set("level", v),
      });
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(v === cur));
      return b;
    }),
  );
  document.getElementById("guest-net-said").textContent =
    GUEST_NET.find((x) => x[0] === cur)[2] +
    (["users-web", "sheet-web", "sheet-all"].includes(cur)
      ? " The box authorises each device for 12 hours at a time."
      : "");
}

async function loadHotspot() {
  try {
    renderHotspot(await getJSON("/admin/hotspot"));
  } catch (err) {
    console.error("hotspot section:", err);
    say("Could not read the hotspot settings.", false, hs.note);
  }
}

hs.second.addEventListener("change", () => {
  hsTouched();
  hsShowFields();
});
hs.wpa2.addEventListener("change", () => {
  hsTouched();
  hsShowFields();
});
hs.password.addEventListener("input", hsTouched);
hs.generate.addEventListener("click", () => {
  // Easy to read out and type on a phone: no 0/O, 1/l/I. getRandomValues works on plain HTTP.
  const abc = "abcdefghjkmnpqrstuvwxyz23456789";
  // Bytes past the last whole multiple of the alphabet are dropped, so every letter is as likely.
  const chars = [];
  const limit = 256 - (256 % abc.length);
  while (chars.length < 12) {
    for (const v of crypto.getRandomValues(new Uint8Array(16))) {
      if (v < limit && chars.length < 12) chars.push(abc[v % abc.length]);
    }
  }
  hs.password.value = [0, 4, 8]
    .map((i) => chars.slice(i, i + 4).join(""))
    .join("-");
  hsTouched();
});
loadHotspot();

// --- Backup and a new box: offers with sizes -------------------------------------------------
// A backup at a chosen level (settings, settings and data, full image) and a new box or an offline box's
// update (books, toolkits, git repositories), each with its estimated size against a budget.
// The sizes are the hub's (/admin/backup/plan); the kit, the image and the stick are the root helper's.
const bku = {
  level: document.getElementById("backup-level"),
  keys: document.getElementById("backup-syncthing"),
  keysLabel: document.getElementById("backup-keys-label"),
  leftOut: document.getElementById("backup-left-out"),
  goBox: document.getElementById("backup-go-box"),
  go: document.getElementById("backup-go"),
  imageBox: document.getElementById("backup-image-box"),
  stick: document.getElementById("backup-image-stick"),
  imageGo: document.getElementById("backup-image-go"),
  note: noteEl("backup-note"),
  progress: document.getElementById("backup-progress"),
  bar: document.getElementById("backup-bar"),
  step: document.getElementById("backup-step"),
};
let bkPlan = null,
  bkLevel = "data",
  bkWaiting = null,
  bkPoll = null,
  usbSticks = [];
const BK_WORDS = {
  settings: [
    "Settings archive",
    "The settings of the hub, the accounts, the settings of apps and add-ons, and the sources of the library and the mirrors. It does not include notes, saved work, the board, the shoutbox, dropped files or git repositories.",
  ],
  data: [
    "Settings and shared-data archive",
    "The settings archive, plus the data made on the box: notes, saved work, the board, the shoutbox, dropped files and the git repositories of the box.",
  ],
  image: [
    "Full storage image",
    "The whole card, written to a USB drive: the system, the hub, books and all. The box creates the image while it runs, so the files might not match one point in time. Write it back with any image writer.",
  ],
};
function drawBackup() {
  const plan = bkPlan;
  if (!plan) return;
  const img = plan.image || {};
  AW.choices(
    bku.level,
    "backup-level",
    ["settings", "data", "image"].map((v) => ({
      value: v,
      title: BK_WORDS[v][0],
      desc: BK_WORDS[v][1],
      does:
        v === "image"
          ? `About ${size(img.used)} (what the card holds)${img.card ? `, at most ${size(img.card)} (the card)` : ""}, compressed.`
          : `About ${size(plan.levels[v] + (bku.keys.checked ? plan.syncthing : 0))}.`,
    })),
    {
      value: bkLevel,
      onChange: (v) => {
        bkLevel = v;
        drawBackupGo();
      },
    },
  );
  const lo = plan.left_out || {};
  bku.leftOut.textContent =
    `Omitted from every download: books (${size(lo.books)}), the firmware mirror (${size(lo.firmware)}), ` +
    "the git mirrors, builds and their caches, crash evidence and the archive of the library. The box can download them again.";
  drawBackupGo();
}
function drawBackupGo() {
  const image = bkLevel === "image";
  bku.goBox.hidden = image;
  bku.keysLabel.hidden = image;
  bku.imageBox.hidden = !image;
  if (!bkPlan) return;
  if (!image) {
    // the image has no download, so no link and no size of its own here
    const q = `level=${bkLevel}${bku.keys.checked ? "&syncthing=1" : ""}`;
    bku.go.href = `/admin/backup?${q}`;
    bku.go.textContent = `Download the backup (about ${size(bkPlan.levels[bkLevel] + (bku.keys.checked ? bkPlan.syncthing : 0))})`;
  }
  bku.stick.replaceChildren(
    ...(usbSticks.length
      ? usbSticks.map((usbStick) =>
          el("option", {
            value: usbStick.name,
            textContent: `${usbStick.label || usbStick.name} (${usbStick.fstype}, ${size(Number(usbStick.size))})`,
          }),
        )
      : [el("option", { value: "", textContent: "No drive found yet" })]),
  );
  bku.imageGo.disabled = !usbSticks.length || !!bkWaiting;
  for (const select of [document.getElementById("offline-stick")]) {
    const keep = select.value;
    select.replaceChildren(...bku.stick.cloneNode(true).children);
    if ([...select.options].some((o) => o.value === keep)) select.value = keep;
  }
  drawKitTotal();
}
// The tick is the choice: its label says what the file then holds, no question.
bku.keys.addEventListener("change", () => drawBackup());
// The kit's and the offline stick's offers: a checkbox per book, toolkit and repository, with its size.
const pickList = (box, items, name, label) => {
  const keep = new Set(
    [...box.querySelectorAll("input:checked")].map((i) => i.value),
  );
  box.replaceChildren(
    ...(items.length
      ? items.map((it) =>
          el(
            "label",
            { className: "inline" },
            el("input", {
              type: "checkbox",
              name,
              value: it.value,
              checked: keep.has(it.value),
            }),
            ` ${label(it)}`,
          ),
        )
      : [
          el("span", {
            className: "setting-desc",
            textContent: "None on this box.",
          }),
        ]),
  );
};
const picked = (box) =>
  [...box.querySelectorAll("input:checked")].map((i) => i.value);
const kitEl = {
  form: document.getElementById("kit-form"),
  make: document.getElementById("kit-make"),
  refresh: document.getElementById("kit-refresh"),
  when: document.getElementById("kit-when"),
  books: document.getElementById("kit-books"),
  kits: document.getElementById("kit-kits"),
  repos: document.getElementById("kit-repos"),
  total: document.getElementById("kit-total"),
  progress: document.getElementById("kit-progress"),
  bar: document.getElementById("kit-bar"),
  step: document.getElementById("kit-step"),
  note: noteEl("kit-note"),
  state: document.getElementById("kit-state"),
  details: document.getElementById("kit-details"),
  contents: document.getElementById("kit-contents"),
};
const stickEl = {
  box: document.getElementById("offline-stick-box"),
  stick: document.getElementById("offline-stick"),
  hubSize: document.getElementById("kit-hub-size"),
  presets: document.getElementById("export-presets"),
  presetSays: document.getElementById("export-preset-says"),
};
// Presets set the hub program, the state and the repositories; the books and toolkits stay as picked.
const EXPORT_PRESETS = {
  update: [
    "Books and toolkits for a box that has irate-box. The hub program, the repositories and the state are omitted.",
    { hub: false, state: "none", repos: false },
  ],
  level: [
    "For a box that has irate-box but is out of date. It includes every repository and the settings of this box.",
    { hub: false, state: "settings", repos: true },
  ],
  new: [
    "For a box without irate-box. It includes the hub program and what you select below. Unpack it on that box and run setup.sh.",
    { hub: true },
  ],
};
let exportPreset = "update",
  kitBusy = false;
function applyPreset(key) {
  const fields = kitEl.form.elements,
    [, set] = EXPORT_PRESETS[key];
  exportPreset = key;
  if ("hub" in set) fields.hub.checked = set.hub;
  if ("state" in set) fields.state.value = set.state;
  if ("repos" in set)
    kitEl.repos.querySelectorAll("input").forEach((i) => {
      i.checked = set.repos;
    });
  drawPresets();
  drawKitTotal();
}
function drawPresets() {
  stickEl.presets.querySelectorAll("[data-export]").forEach((b) => {
    b.classList.toggle("selected", b.dataset.export === exportPreset);
    b.setAttribute("aria-pressed", String(b.dataset.export === exportPreset));
  });
  stickEl.presetSays.textContent = EXPORT_PRESETS[exportPreset][0];
}
stickEl.presets.addEventListener("click", (event) => {
  const b = event.target.closest("[data-export]");
  if (b) applyPreset(b.dataset.export);
});
drawPresets(); // the default preset shown chosen before any is pressed
function drawPicks() {
  const plan = bkPlan;
  const book = (b) => `${b.name} (${size(b.size)})`,
    kit = (k) => `${k.title} (${size(k.size)})`;
  pickList(
    kitEl.books,
    plan.books.map((b) => ({ ...b, value: b.name })),
    "book",
    book,
  );
  pickList(
    kitEl.kits,
    plan.kits.map((k) => ({ ...k, value: k.id })),
    "kit",
    kit,
  );
  pickList(
    kitEl.repos,
    plan.repos.map((repo) => ({ ...repo, value: `${repo.area}/${repo.name}` })),
    "repo",
    (repo) =>
      `${repo.name}${repo.area === "private" ? " (administrators only)" : ""}${repo.mirror ? ", a mirror" : ""} (${size(repo.size)})`,
  );
  stickEl.hubSize.textContent = `about ${size(plan.hub)} (its code, apps and release files)`;
  drawPresets();
  drawKitTotal();
}
function kitBytes() {
  const plan = bkPlan,
    fields = kitEl.form.elements;
  const sum = (box, list, key) =>
    picked(box).reduce(
      (n, v) => n + ((list.find((x) => x[key] === v) || {}).size || 0),
      0,
    );
  return (
    (fields.hub.checked ? plan.hub : 0) +
    sum(kitEl.books, plan.books, "name") +
    sum(kitEl.kits, plan.kits, "id") +
    picked(kitEl.repos).reduce(
      (n, v) =>
        n +
        ((plan.repos.find((repo) => `${repo.area}/${repo.name}` === v) || {}).size || 0),
      0,
    ) +
    (fields.state.value === "none" ? 0 : plan.levels[fields.state.value])
  );
}
function drawKitTotal() {
  if (!bkPlan) return;
  const fields = kitEl.form.elements;
  // The hub program goes only as a download (one file that setup.sh runs from).
  let moved = false;
  if (fields.hub.checked && fields.dest.value === "stick") {
    fields.dest.value = "download";
    moved = true;
  }
  const stick = fields.dest.value === "stick";
  stickEl.box.hidden = !stick;
  const total = kitBytes(),
    budget = Number(fields.budget.value) || 0;
  const over = budget && total > budget * 2 ** 20;
  kitEl.total.textContent =
    (total ? `About ${size(total)}` : "Nothing selected yet") +
    (budget
      ? ` of a ${size(budget * 2 ** 20)} budget${over ? ". This is over the budget. Remove something from the export." : "."}`
      : " (no budget set).") +
    (fields.hub.checked ? " The hub program is in it." : "") +
    (moved ? " With the hub program, the export is a download." : "");
  kitEl.total.classList.toggle("bad", !!over);
  kitEl.make.dataset.over = over ? "1" : "";
  kitEl.make.textContent = stick ? "Copy export to USB drive" : "Create export";
  kitEl.make.disabled =
    kitBusy || !total || (stick && (!usbSticks.length || !!bkWaiting));
}
kitEl.form.addEventListener("change", drawKitTotal);
kitEl.form.addEventListener("input", drawKitTotal);
async function loadBackupPlan() {
  try {
    bkPlan = await getJSON("/admin/backup/plan");
    drawBackup();
    drawPicks();
  } catch (err) {
    console.error("backup plan:", err);
  }
}
// The sticks, the image and the offline copy: the root helper, through /admin/usb, as the Books page's exports.
async function bkSticks(scan) {
  try {
    if (scan) {
      bkWaiting = {
        id: (await postJSON("/admin/usb", { action: "scan" })).id,
        note: bku.note,
      };
    }
    const reply = await getJSON("/admin/usb");
    usbSticks = (reply.scan && reply.scan.devices) || [];
    if (bkWaiting) {
      const done = (reply.results || []).find((result) => result.id === bkWaiting.id);
      if (done) {
        say(resultSaid(done), done.ok, bkWaiting.note);
        bkWaiting = null;
      }
    }
    const progress = reply.progress;
    bku.progress.hidden = !progress;
    if (progress) {
      bku.bar.value = progress.total ? Math.min(progress.done / progress.total, 1) : 0;
      bku.step.textContent = `${said(progress.label)}${progress.total ? `: ${size(progress.done)} of ${size(progress.total)} (${Math.round((100 * progress.done) / progress.total)}%)` : "…"}`;
    }
    drawBackupGo();
    clearTimeout(bkPoll);
    if (bkWaiting || progress || reply.pending > 0)
      bkPoll = setTimeout(() => bkSticks(false), 1500);
  } catch (err) {
    say(err.message, false, bku.note);
  }
}
document
  .getElementById("backup-image-scan")
  .addEventListener("click", () => bkSticks(true));
document
  .getElementById("offline-scan")
  .addEventListener("click", () => bkSticks(true));
bku.imageGo.addEventListener("click", async (event) => {
  const usbStick = usbSticks.find((x) => x.name === bku.stick.value);
  if (
    !usbStick ||
    !await AW.confirmHere(
      `Write an image of the whole card to ${usbStick.label || usbStick.name}? The image is about ${size((bkPlan.image || {}).used)} or more. ` +
        "On this board it can take an hour or more. The box stays in use meanwhile.", { anchor: event.currentTarget })
  )
    return;
  try {
    bkWaiting = {
      id: (await postJSON("/admin/usb", { action: "image", device: usbStick.name }))
        .id,
      note: bku.note,
    };
    say("Writing the image. The progress shows here.", true, bku.note);
    bkSticks(false);
  } catch (err) {
    say(err.message, false, bku.note);
  }
});

// The kit: the root helper makes it (hub_control.py offline_kit); the hub streams the download.
let kitWaiting = null;
let kitPoll = null;
function renderKit(data) {
  if (kitWaiting) {
    const done = (data.results || []).find((result) => result.id === kitWaiting);
    if (done) {
      say(resultSaid(done), done.ok, kitEl.note);
      kitWaiting = null;
    }
  }
  const busy = !!kitWaiting || data.pending > 0 || !!data.progress;
  kitBusy = busy;
  drawKitTotal();
  kitEl.progress.hidden = !data.progress;
  if (data.progress) {
    const progress = data.progress;
    kitEl.bar.value = Math.min(
      Math.max(progress.step - 1, 0) / Math.max(progress.steps, 1),
      1,
    );
    kitEl.step.textContent = `Step ${Math.max(progress.step, 1)} of ${progress.steps}${progress.label ? ` — ${said(progress.label)}` : ""}.`;
  }
  const k = data.kit;
  const held = k
    ? [
        k.books.length &&
          `${k.books.length} book${k.books.length === 1 ? "" : "s"}`,
        (k.kits || []).length &&
          `${k.kits.length} toolkit${k.kits.length === 1 ? "" : "s"}`,
        (k.repos || []).length &&
          `${k.repos.length} repositor${k.repos.length === 1 ? "y" : "ies"}`,
        k.state &&
          k.state !== "none" &&
          (k.state === "data" ? "settings and data" : "settings"),
      ].filter(Boolean)
    : [];
  kitEl.state.replaceChildren(
    ...(k
      ? [
          el("a", {
            href: "/admin/kit/download",
            download: k.name,
            textContent: `Download ${k.name}`,
          }),
          document.createTextNode(
            ` — ${size(k.size)}, made ${new Date(k.at * 1000).toLocaleString()}, with ${held.length ? held.join(", ") : "the hub alone"}${k.kind === "content" ? ", without the hub program" : ""}.`,
          ),
        ]
      : [
          document.createTextNode(
            busy ? "Creating the export…" : "No export created yet.",
          ),
        ]),
  );
  const auto = data.auto || {};
  kitEl.refresh.disabled = busy || !auto.can_refresh;
  if (document.activeElement !== kitEl.when)
    kitEl.when.value = auto.when || "manual";
  kitEl.details.hidden = !(k && k.contents && k.contents.length);
  if (k)
    kitEl.contents.replaceChildren(
      ...(k.contents || []).map((t) => el("li", { textContent: t })),
    );
  clearTimeout(kitPoll);
  if (busy) kitPoll = setTimeout(loadKit, 2000);
}
// Refresh export: the last export's choices again; Make the export again: when the librarian does it by itself.
kitEl.refresh.addEventListener("click", async () => {
  try {
    kitWaiting = (await postJSON("/admin/kit", { action: "refresh" })).id;
    loadKit();
  } catch (err) {
    say(err.message, false, kitEl.note);
  }
});
kitEl.when.addEventListener("change", async () => {
  try {
    renderKit(
      await postJSON("/admin/kit", {
        action: "schedule",
        when: kitEl.when.value,
      }),
    );
    say("Saved.", true, kitEl.note);
  } catch (err) {
    say(err.message, false, kitEl.note);
  }
});
async function loadKit() {
  try {
    renderKit(await getJSON("/admin/kit"));
  } catch (err) {
    console.error("kit:", err);
  }
}
kitEl.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (kitEl.make.dataset.over) {
    say(
      "The export is over the budget. Remove something from it, or raise the budget.",
      false,
      kitEl.note,
    );
    return;
  }
  const fields = kitEl.form.elements,
    budget = Number(fields.budget.value) || null;
  const books = picked(kitEl.books),
    kits = picked(kitEl.kits),
    repos = picked(kitEl.repos),
    state = fields.state.value;
  try {
    if (fields.dest.value === "stick") {
      const usbStick = usbSticks.find((x) => x.name === stickEl.stick.value);
      if (!usbStick) return;
      bkWaiting = {
        id: (
          await postJSON("/admin/usb", {
            action: "export-many",
            device: usbStick.name,
            books,
            kits,
            repos,
            state,
          })
        ).id,
        note: kitEl.note,
      };
      say(
        "Copying. The progress shows above, under Back up this box.",
        true,
        kitEl.note,
      );
      bkSticks(false);
      return;
    }
    kitWaiting = (
      await postJSON("/admin/kit", {
        action: "make",
        hub: fields.hub.checked,
        books,
        kits,
        repos,
        state,
        budget_mb: budget,
      })
    ).id;
    say(
      "Creating the export. This takes a minute or two, and longer with books.",
      true,
      kitEl.note,
    );
    loadKit();
  } catch (err) {
    say(err.message, false, kitEl.note);
  }
});
window.addEventListener("hashchange", () => {
  if (paneShown("backup")) {
    loadKit();
    loadBackupPlan();
    bkSticks(false);
  }
});
loadKit();
loadBackupPlan();

// --- who can open each app --------------------------------------------------------------
// Public, private or off (access.py): a three-way switch on each app (Apps), each add-on
// (Add-ons) and the hub's own parts (Apps, Built into the hub). The root helper rewrites the
// web server's part and answers; each switch keeps its node, so a list that re-renders moves
// it rather than losing what it shows.
const accessNodes = new Map(); // id -> { node, app }
let accessWaiting = null; // { id, app }
let accessNote = null; // { app, text, ok }
let accessPoll = null;

const SEEN_LABEL = {
  auto: "as its access",
  guests: "guests",
  users: "signed-in users",
  hidden: "nobody",
};
function accessDesc(app) {
  if (app.mode === "public")
    return app.login
      ? "Guests: the hub shows the tile. The app still asks for the administrator sign-in."
      : "Guests: the hub shows the tile. Every device on the box's network can open the app.";
  if (app.mode === "users")
    return "Users: the hub shows the tile to signed-in users. A guest must sign in to an account first (Accounts).";
  if (app.mode === "private")
    return "Administrators: the hub does not show the tile. The app address asks for the administrator sign-in.";
  return `Disabled: the hub does not show the tile. The app address answers "Not found"${app.unit ? ". The service is stopped" : ""}.`;
}

// A list may render before the choices arrive: its slot is made empty and filled in later.
function accessSlot(id) {
  if (!accessNodes.has(id))
    accessNodes.set(id, { node: el("span", { className: "access" }) });
  return accessNodes.get(id).node;
}

// What a change of access needs saying, asked once at Save.
// What a change of an app's access does, beside Save.
function accessWarn(app, mode) {
  if (mode === "off" && app.unit)
    return "Its service stops until you enable it again.";
  if (mode === "public" && app.mode !== "public" && !app.login)
    return "Every device on the box's network can open it without a sign-in.";
  return null;
}
// The change, as a job: done when the root helper's answer comes back with the poll (renderAccess).
async function accessSet(app, mode) {
  const j = AW.job();
  const reply = await postJSON("/admin/access", { app: app.id, mode });
  accessWaiting = { id: reply.id, app: app.id, job: j };
  accessNote = null;
  loadAccess();
  return j;
}

function renderAccess(data) {
  if (accessWaiting) {
    const done = (data.results || []).find((result) => result.id === accessWaiting.id);
    if (done) {
      accessNote = {
        app: accessWaiting.app,
        text: resultSaid(done),
        ok: done.ok,
      };
      const j = accessWaiting.job;
      accessWaiting = null;
      if (j) j.done(done.ok, resultSaid(done));
      return loadAccess();
    }
  }
  const builtin = [];
  lastAccess = data;
  for (const app of data.apps) {
    accessSlot(app.id).replaceChildren(...accessControls(app));
    if (app.kind === "builtin") builtin.push(app);
  }
  fillAccessBlocks();
  const paged = new Set(
    [...document.querySelectorAll(".access-block[data-app]")].map(
      (b) => b.dataset.app,
    ),
  );
  const unpaged = builtin.filter((app) => !paged.has(app.id));
  document
    .getElementById("builtin-list")
    .replaceChildren(
      ...unpaged.map((app) =>
        el(
          "div",
          { className: "setting library-source" },
          el(
            "span",
            {},
            el("span", { className: "setting-name", textContent: app.title }),
            accessSlot(app.id),
          ),
        ),
      ),
    );
  document.getElementById("builtin-more").hidden = !unpaged.length;
  renderInventory();
  clearTimeout(accessPoll);
  if (accessWaiting) accessPoll = setTimeout(loadAccess, 1000);
}

// Each app's own page starts with its access: the same controls, drawn again there.
let lastAccess = null;
const TAB_SEEN = {
  guests: "guests",
  users: "signed-in users",
  hidden: "nobody",
};
// Its tab's visibility on the hub page: a section per app (the same setting as on Home tiles), held until Save.
function fillSeenBlocks() {
  if (!lastAccess) return;
  document.querySelectorAll(".seen-block[data-app]").forEach((block) => {
    const app = block.dataset.app,
      cur = ((lastAccess.pages || {})[app] || "auto").replace("auto", "guests");
    const sec = AW.section({
      id: `tab-seen-${app}`,
      label: "Visibility of its tab",
      saved: { visible: cur },
      save: async (c) => {
        await postJSON("/admin/visibility", { app, visible: c.visible });
        setTimeout(loadAccess);
      },
      onChange: () => fillSeenBlocks(),
    });
    const v = sec.get("visible");
    block.replaceChildren(
      el(
        "span",
        { className: "chip-group access-seen" },
        el("span", {
          className: "setting-desc",
          textContent: "Visibility of its tab on the hub page:",
        }),
        ...Object.entries(TAB_SEEN).map(([k, label]) => {
          const b = el("button", {
            type: "button",
            className: "chip" + (v === k ? " selected" : ""),
            textContent: label,
            disabled: sec.disabled(),
            onclick: () => sec.set("visible", k),
          });
          b.setAttribute("aria-pressed", String(v === k));
          return b;
        }),
      ),
      sec.foot,
      sec.reason,
    );
  });
}
function fillAccessBlocks() {
  if (!lastAccess) return;
  fillSeenBlocks();
  // The rows in the lists (Apps, the built-in parts, add-ons) carry the same block: redrawn too.
  for (const app of lastAccess.apps)
    if (accessNodes.has(app.id))
      accessSlot(app.id).replaceChildren(...accessControls(app));
  document.querySelectorAll(".access-block[data-app]").forEach((block) => {
    const app = lastAccess.apps.find((x) => x.id === block.dataset.app);
    block.replaceChildren(
      ...(app
        ? accessControls(app)
        : [
            el("p", {
              className: "setting-desc",
              textContent: "Always on the hub: no switch.",
            }),
          ]),
    );
  });
  drawTileOrder();
  fillTileBlocks();
}

// An app's or folder's tile on its own page: what Home tiles has for it, and the way there.
let tileFocus = null; // the row Home tiles opens next
function fillTileBlocks() {
  document.querySelectorAll(".tile-block[data-app]").forEach((block) => {
    const id = block.dataset.app;
    const t = tilesData && tilesData.tiles.find((x) => x.id === id);
    const app = lastAccess && lastAccess.apps.find((x) => x.id === id);
    if (!tilesData) {
      block.replaceChildren(
        el("p", {
          className: "setting-desc",
          textContent: "Loading the tiles.",
        }),
      );
      return;
    }
    if (!t) {
      block.replaceChildren(
        el("p", {
          className: "setting-desc",
          textContent:
            app && app.mode === "off"
              ? "No tile while it is disabled."
              : "No tile on the home page.",
        }),
      );
      return;
    }
    const order = tilesData.tiles.map((x) => x.id);
    const mode = app ? app.mode : "public";
    const seen = app
      ? seenOf(app.mode, app.visible)
      : seenOf("public", ((lastAccess && lastAccess.seen) || {})[id]);
    const lock = ((lastAccess && lastAccess.locked_as) || {})[id] || "signin";
    const go = el("a", {
      className: "action-btn",
      href: "#tile-order",
      textContent: "Edit Home tiles →",
    });
    go.addEventListener("click", () => {
      tileFocus = id;
    });
    block.replaceChildren(
      el("p", {
        className: "setting-desc",
        textContent:
          `${seenWords(seen, mode)} Place ${order.indexOf(id) + 1} of ${order.length}. ` +
          `${t.size || "single"} size. Icon: ${t.own_icon ? `its own (${t.own_icon})` : "the icon of the app"}.` +
          (app && mode !== "public" && seen !== "hidden"
            ? ` Locked-tile action: ${LOCK_WORDS[lock]}.`
            : ""),
      }),
      el("div", { className: "library-buttons" }, go),
    );
  });
}
window.addEventListener("hashchange", () => {
  if (location.hash === "#tile-order" && tileFocus) {
    const id = tileFocus;
    tileFocus = null;
    tileFocusScroll = true;
    drawTileOrder(id);
    tileFocusScroll = false;
  }
});

// The top of an app's page: what it is and what state it is in, and what can be done now: open
// it, add it, start or stop its service. The facts are the All apps line's.
function fillHeadBlocks() {
  const rows = AL.apps() ? inventoryRows() : [];
  const manifests = AL.apps() || [];
  document.querySelectorAll(".head-block[data-app]").forEach((block) => {
    const id = block.dataset.app;
    const row = rows.find((x) => x.id === id);
    const m = manifests.find((x) => x.id === id) || {};
    const app = lastAccess && lastAccess.apps.find((x) => x.id === id);
    const svc = lastBox && (lastBox.services || []).find((x) => x.app === id);
    const buttons = [];
    if (
      m.href &&
      row &&
      ["installed", "builtin"].includes(row.state) &&
      !(app && app.mode === "off")
    ) {
      buttons.push(
        el("a", {
          className: "action-btn primary",
          href: m.href,
          target: "_blank",
          rel: "noopener",
          textContent: "Open",
        }),
      );
    }
    const addon =
      lastAddons && (lastAddons.addons || []).find((x) => x.id === id);
    // Its install card on this page says what adding it means first (its consent text, addonButtons).
    if (row && row.kind === "addon" && addon && addon.added === false)
      buttons.push(
        el("a", {
          className: "action-btn primary",
          href: `#app-install-${id}`,
          textContent: "Add…",
        }),
      );
    const held =
      row &&
      row.kind === "local" &&
      lastLocal &&
      (lastLocal.added || []).find(
        (x) => x.id === id && x.catalogue && x.catalogue.status === "held",
      );
    if (held)
      buttons.push(approveButton(held, lastLocal, "action-btn primary"));
    if (row && row.kind === "local" && row.state !== "installed")
      buttons.push(
        el("a", {
          className: "action-btn",
          href: row.offered ? "#local-catalogue" : "#local-added",
          textContent: "Add from the catalogue",
        }),
      );
    if (row && row.kind === "app" && row.state === "available")
      buttons.push(
        el("a", {
          className: "action-btn",
          href: `#app-updates-${id}`,
          textContent: "Install…",
        }),
      );
    if (svc && svc.unit && svc.state !== "missing") {
      const ops = (svc.ops || []).filter((op) =>
        op === "start"
          ? !svc.active
          : op === "stop"
            ? svc.active
            : op === "enable"
              ? !svc.enabled
              : op === "disable"
                ? svc.enabled
                : true,
      );
      buttons.push(
        ...ops.map((op) =>
          actionButton(
            OP_LABEL[op],
            () => control(svc, op),
            OP_TITLE[op] ? { title: OP_TITLE[op] } : {},
          ),
        ),
      );
    }
    const facts = row ? [INV_KIND[row.kind] || row.kind, ...row.facts] : ["Loading."];
    if (svc && svc.unit)
      facts.push(`Service: ${STATE_LABEL[svc.state] || svc.state}`);
    block.replaceChildren(
      ...[
        el(
          "p",
          { className: "inv-facts" },
          ...facts.map((t) =>
            el("span", { className: "inv-fact", textContent: t }),
          ),
          ...(row ? row.attention : []).map((t) =>
            el("span", { className: "inv-fact bad", textContent: t }),
          ),
        ),
        buttons.length
          ? el("p", { className: "library-buttons" }, ...buttons)
          : null,
      ].filter(Boolean),
    );
  });
}
AL.onBuild(fillHeadBlocks);
AL.onBuild(fillTileBlocks);
AL.onBuild(fillAccessBlocks);

// A tile with no switch of its own (a folder, About): anyone may open it, so only who sees
// its tile is to choose. Hidden, its address still works.
const SEEN_ONLY = {
  auto: "guests",
  guests: "guests",
  users: "signed-in users",
  admin: "administrators",
  hidden: "nobody",
};

// The box-wide sign-in offer (the setup decision "sign-in-offer"): an app for users, left at
// "as its access", shows its tile to guests too, locked, leading to sign-in.
let lockAll = ""; // the box-wide way a seen-but-unopenable tile behaves ('' = each app's own)
async function loadSignInOffer() {
  const box = document.getElementById("sign-in-offer-box");
  if (!box) return;
  let st;
  try {
    st = await getJSON("/admin/settings");
  } catch {
    return;
  }
  lockAll = st.locked_all || "";
  box.replaceChildren(
    AW.settings(
      [
        {
          key: "sign_in_offer",
          label:
            "Show guests the tiles of apps for users, locked, with sign-in",
          kind: "toggle",
          value: !!st.sign_in_offer,
          decision: "sign-in-offer",
          note: "Disabled: an app for users shows its tile only to signed-in users. Enabled: guests also see the tile, with a lock that leads to sign-in. The Visibility setting of each app overrides this setting.",
        },
        {
          key: "locked_all",
          label: "Locked-tile action for every app",
          kind: "choice",
          value: lockAll,
          options: [
            ["", "Per app"],
            ["signin", "Sign in"],
            ["signup", "Sign up"],
            ["padlock", "Padlock"],
            ["grey", "Greyed"],
          ],
          note: "Per app (the default): the page of each app sets the action. Any other option: every app uses that action and the setting of the app is ignored. Sign up applies only where accounts are open or by application.",
        },
      ],
      {
        id: "sign-in-offer",
        save: async (changed) => {
          await postJSON("/admin/settings", changed);
          await loadSignInOffer();
          loadAccess();
        },
      },
    ),
  );
}
loadSignInOffer();

// The apps row's order, on "All apps": each tile with who opens it and who sees it; moved
// with ↑/↓, held until Save, kept by the hub (/admin/tiles).
let tilesData = null;
let tilesDraft = null; // the section's draft, as the list's controls change it
// Home tiles as one section: its value the whole arrangement (freshTilesDraft's shape, visibility and the locked
// action as changes only), so a change undone turns Save off; reloaded, a draft is kept over the saved one.
const tilesSec = AW.section({
  id: "home-tiles",
  label: "Home tiles",
  saved: null,
  why: (c, v) =>
    Object.values(v.all.icon || {}).some((x) => x && !ICON_OK(x))
      ? "A tile icon is not valid: use emoji, or up to four letters and digits."
      : null,
  save: (c, v) => saveTiles(v.all),
  onChange: () => {
    tilesDraft = JSON.parse(JSON.stringify(tilesSec.get("all")));
    drawTileOrder();
  },
});
const tilesTouched = () =>
  tilesSec.set("all", JSON.parse(JSON.stringify(tilesDraft)), true);
async function loadTileOrder() {
  try {
    tilesData = await getJSON("/admin/tiles");
  } catch {
    return;
  }
  tilesSec.update({ all: freshTilesDraft() });
  renderInventory();
}
async function saveTiles(tiles) {
  const appOf = (id) => lastAccess && lastAccess.apps.find((x) => x.id === id);
  tilesData = await postJSON("/admin/tiles", {
    state: { order: tiles.order, size: tiles.size || {}, icon: tiles.icon || {} },
  });
  for (const [id, v] of Object.entries(tiles.seen)) {
    const app = appOf(id),
      auto = app ? seenOf(app.mode, "auto") : "guests";
    await postJSON("/admin/visibility", {
      app: id,
      visible: v === auto ? "auto" : v,
    });
  }
  for (const [id, v] of Object.entries(tiles.lock))
    await postJSON("/admin/visibility", { app: id, locked: v });
  // Saved: the arrangement read back (visibility and the locked action as they are now, no changes left).
  setTimeout(() => {
    tilesSec.update({ all: freshTilesDraft() });
    loadAccess();
  });
}
// Home tiles' draft: the order, sizes and icons (/admin/tiles), and who sees each tile and what a
// locked one does (/admin/visibility), changed ones only. All held until Save.
function freshTilesDraft() {
  return {
    order: tilesData.tiles.map((x) => x.id),
    size: { ...tilesData.state.size },
    icon: { ...tilesData.state.icon },
    seen: {},
    lock: {},
  };
}
const OPENS = {
  public: "guests",
  users: "users",
  private: "administrators",
  off: "disabled",
};
function tileWords(id) {
  const app = lastAccess && lastAccess.apps.find((x) => x.id === id);
  if (app) {
    const seen =
      app.mode === "off"
        ? "nobody"
        : app.visible === "auto" || !app.visible
          ? "as its access"
          : SEEN_LABEL[app.visible];
    return {
      summary: `Access: ${OPENS[app.mode] || app.mode} · Visibility: ${seen}`,
      badges: [app.mode === "off" ? "off" : app.mode],
    };
  }
  const seen = lastAccess && (lastAccess.seen || {})[id];
  return {
    summary: `Access: guests · Visibility: ${SEEN_ONLY[seen || "auto"]}`,
    badges: [seen === "hidden" ? "hidden" : "shown"],
  };
}
function drawTileOrder(openId) {
  const box = document.getElementById("tile-order-box");
  if (!box || !tilesData || !tilesDraft) return;
  const asked = openId; // a row to open, asked for by a control (not the poll)
  // The row open before a redraw stays open (the poll and the other controls draw the list again).
  if (!openId) {
    const was = box.querySelector('.aw-row-head[aria-expanded="true"]');
    openId = was ? was.closest(".aw-row").dataset.awId : null;
  }
  const byId = new Map(tilesData.tiles.map((x) => [x.id, x]));
  const order = tilesDraft.order.filter((i) => byId.has(i));
  const redraw = (id) => {
    tilesTouched();
    drawTileOrder(id);
  };
  const move = (id, step) => {
    const i = order.indexOf(id),
      k = i + step;
    if (k < 0 || k >= order.length) return;
    [order[i], order[k]] = [order[k], order[i]];
    tilesDraft.order = order;
    redraw(id);
  };
  const appOf = (id) => lastAccess && lastAccess.apps.find((x) => x.id === id);
  const folderIds = new Set(
    (AL.apps() || []).filter((x) => x.folder).map((x) => x.id),
  );
  const seenNow = (id) => {
    const app = appOf(id);
    return app
      ? seenOf(app.mode, app.visible)
      : seenOf("public", ((lastAccess && lastAccess.seen) || {})[id]);
  };
  const lockNow = (id) =>
    ((lastAccess && lastAccess.locked_as) || {})[id] || "signin";
  const items = order.map((id) => {
    const t = byId.get(id),
      app = appOf(id),
      w = tileWords(id);
    const sz = (tilesDraft.size || {})[id];
    const seen = tilesDraft.seen[id] || seenNow(id),
      lock = tilesDraft.lock[id] || lockNow(id);
    // The icon field's own draft shape: undefined while unchanged.
    const iconNow = (tilesData.state.icon || {})[id] || "";
    const iconDraft = {
      icon:
        (tilesDraft.icon || {})[id] !== iconNow
          ? (tilesDraft.icon || {})[id] || ""
          : undefined,
    };
    const setIcon = () => {
      const v = iconDraft.icon;
      tilesDraft.icon = { ...tilesDraft.icon };
      if (v) tilesDraft.icon[id] = v;
      else delete tilesDraft.icon[id];
      redraw(id);
    };
    const setSeen = (v) => {
      if (v === seenNow(id)) delete tilesDraft.seen[id];
      else tilesDraft.seen[id] = v;
      redraw(id);
    };
    const setLock = (v) => {
      if (v === lockNow(id)) delete tilesDraft.lock[id];
      else tilesDraft.lock[id] = v;
      redraw(id);
    };
    return {
      id,
      title: `${tilesDraft.icon[id] || t.icon} ${t.name}`.trim(),
      summary:
        w.summary +
        (sz
          ? ` · ${(TILE_SIZES.find((x) => x[0] === sz) || [])[1] || sz}`
          : ""),
      badges: [folderIds.has(id) ? "folder" : "app", ...w.badges],
      detail: () =>
        [
          AW.h(
            "div",
            { class: "aw-field" },
            AW.h("span", { class: "aw-label", text: "Place" }),
            AW.btn("↑ Earlier", { onclick: () => move(id, -1) }),
            AW.btn("↓ Later", { onclick: () => move(id, 1) }),
          ),
          AW.h(
            "div",
            { class: "aw-field" },
            AW.h("span", { class: "aw-label", text: "Size" }),
            sizeChips((tilesDraft.size || {})[id] || "single", (v) => {
              setSize(tilesDraft, id, v);
              redraw(id);
            }),
          ),
          chipRow(
            "Visibility",
            SEEN_CHIPS,
            seen,
            setSeen,
            () => !!(app && app.mode === "off"),
          ),
          el("p", {
            className: "setting-desc",
            textContent:
              seenWords(seen, app ? app.mode : "public") +
              " If hidden, the address still works for everyone who can open the app.",
          }),
          app && app.mode !== "public"
            ? chipRow(
                "Locked-tile action",
                LOCK_CHIPS,
                lock,
                setLock,
                (v) => !!lockAll || (v === "signup" && app.mode !== "users"),
              )
            : null,
          app && app.mode !== "public"
            ? el("p", {
                className: "setting-desc",
                textContent: lockAll
                  ? `The global locked-tile action for every app is "${LOCK_CHIPS.find((c) => c[0] === lockAll)[1]}". It overrides the setting of this app (All apps, under "App access").`
                  : `Locked-tile action: ${LOCK_WORDS[lock]}.`,
              })
            : null,
          iconField(iconDraft, t, iconNow, setIcon),
          document.getElementById("page-app-" + id)
            ? AW.h(
                "a",
                { href: "#page-app-" + id, class: "action-btn" },
                folderIds.has(id)
                  ? "Open folder page →"
                  : "Open app settings →",
              )
            : null,
        ].filter(Boolean),
    };
  });
  // Drawn again only when what it shows changed (a poll of access that brings nothing new leaves it, a filter
  // typed and a selection in it too).
  const key = JSON.stringify([
    items.map(({ detail, ...x }) => x),
    tilesDraft,
    lockAll,
  ]);
  if (key === box.dataset.key && !asked && box.childElementCount) {
    fillTileBlocks();
    return;
  }
  box.dataset.key = key;
  box.replaceChildren(
    AW.shortList(items, { id: "tile-order-list" }),
    tilesSec.foot,
    tilesSec.reason,
  );
  const open =
    openId &&
    [...box.querySelectorAll(".aw-row")].find((row) => row.dataset.awId === openId);
  if (open) {
    AW.foldRow(open.querySelector(".aw-row-head"), true);
    if (tileFocusScroll && open.scrollIntoView)
      open.scrollIntoView({ block: "center" });
  }
  fillTileBlocks();
}
let tileFocusScroll = false;
loadTileOrder();

// Who opens it, who sees it, and its place on the hub: a set of four chips each, and two buttons
// for moving it: guests, users, admin, off; guests, users, admin, hidden; ↑ Earlier, ↓ Later. Held until Save, as every
// setting is; drafts kept per app, so a poll redrawing the page doesn't lose one.
const OPEN_CHIPS = [
  ["public", "Guests"],
  ["users", "Users"],
  ["private", "Administrators"],
  ["off", "Disabled"],
];
const SEEN_CHIPS = [
  ["guests", "Guests"],
  ["users", "Users"],
  ["admin", "Administrators"],
  ["hidden", "Hidden"],
];
// What the tile does for one who sees it but may not open it.
const LOCK_CHIPS = [
  ["signin", "sign in"],
  ["signup", "sign up"],
  ["padlock", "padlock"],
  ["grey", "greyed"],
];
const LOCK_WORDS = {
  signin: "it leads to the sign-in page",
  signup:
    "it leads to sign-up while accounts are open or by application, otherwise to sign-in",
  padlock: "it shows a padlock and does nothing",
  grey: "it is greyed out and does nothing",
};
// "As its access" (auto), shown as the chip it amounts to.
const seenOf = (mode, visible) =>
  visible && visible !== "auto"
    ? visible
    : { public: "guests", users: "users", private: "admin", off: "hidden" }[
        mode
      ] || "guests";
function chipRow(label, opts, cur, set, disabledOf = () => false) {
  const g = el(
    "div",
    { className: "chip-group" },
    ...opts.map(([v, word]) => {
      const b = el("button", {
        type: "button",
        className: "chip" + (cur === v ? " selected" : ""),
        textContent: word,
        disabled: disabledOf(v),
        onclick: () => set(v),
      });
      b.setAttribute("aria-pressed", String(cur === v));
      return b;
    }),
  );
  g.setAttribute("role", "group");
  g.setAttribute("aria-label", label);
  return el(
    "div",
    { className: "aw-field access-field" },
    el("span", { className: "aw-label", textContent: label }),
    g,
  );
}
// The block: who can open an app. Its tile (who sees it, what a locked one does, its place,
// size and icon) is arranged on Home tiles; the app's page shows that as a summary.
function accessBlock(id, app) {
  // A section per app (drawn in its row and on its page alike, the draft shared by id); root's, answered by poll:
  // the draft stays until it is done (renderAccess), so Cancel or a failure keeps it.
  const sec = AW.section({
    id: `access-${id}`,
    label: `Access to ${app.title}`,
    saved: { mode: app.mode },
    warn: (c) => accessWarn(app, c.mode),
    save: (c) => accessSet(app, c.mode),
    busy: () => !!(accessWaiting && accessWaiting.app === id),
    onChange: () => fillAccessBlocks(),
  });
  const mode = sec.get("mode");
  const waiting = sec.saving();
  const note =
    accessNote && accessNote.app === id && accessNote.ok
      ? el("span", {
          className: "setting-desc action-note",
          role: "status",
          textContent: accessNote.text,
        })
      : null;
  const seen =
    mode === "off"
      ? "Disabled: no tile."
      : `Visibility: ${seenWords(seenOf(mode, app.visible), mode)}`;
  return el(
    "div",
    { className: "access-set" },
    chipRow(
      "Access",
      OPEN_CHIPS,
      mode,
      (v) => sec.set("mode", v),
      (v) => waiting || (v === "users" && !app.users),
    ),
    el("p", {
      className: "setting-desc",
      textContent: waiting
        ? "Changing…"
        : `${accessDesc({ ...app, mode })} ${seen}`,
    }),
    sec.foot,
    sec.reason,
    note,
  );
}
const SEEN_WORDS = {
  guests: "Every visitor sees the tile",
  users: "Signed-in users see the tile",
  admin: "Only a signed-in administrator sees the tile",
  hidden: "No tile",
};
function seenWords(seen, mode) {
  if (mode === "off") return "Disabled: no tile.";
  return `${SEEN_WORDS[seen]}${seen !== "hidden" && ((mode === "users" && seen === "guests") || (mode === "private" && seen !== "admin")) ? ". A lock shows for visitors who cannot open the app" : ""}.`;
}
function accessControls(app) {
  return [accessBlock(app.id, app)];
}
// The tile's own icon: emoji, or up to four letters and digits drawn as text;
// blank goes back to the app's own. Checked here as the hub checks it (server.valid_icon).
const ICON_OK = (icon) =>
  /^[A-Za-z0-9]{1,4}$/.test(icon) ||
  (icon.length > 0 &&
    [...icon].length <= 16 &&
    [...icon].every((c) => c.codePointAt(0) > 0x7f));
function iconField(draft, tile, iconNow, redraw) {
  const val = draft.icon !== undefined ? draft.icon : iconNow;
  const input = el("input", {
    type: "text",
    value: val,
    placeholder: tile.icon,
    maxLength: 16,
    size: 8,
    spellcheck: false,
  });
  input.setAttribute(
    "aria-label",
    "Tile icon: emoji, or up to four letters and digits",
  );
  const bad = val && !ICON_OK(val);
  const hint = el("span", {
    className: "setting-desc" + (bad ? " bad" : ""),
    textContent: bad
      ? "Emoji, or up to four letters and digits (no spaces)."
      : val
        ? "Its own icon. A blank field restores the icon of the app."
        : `Icon of the app: ${tile.icon}`,
  });
  input.addEventListener("change", () => {
    draft.icon = input.value.trim();
    redraw();
  });
  // A pick from the emoji picker arrives as an input event of the page's own.
  input.addEventListener("input", (event) => {
    if (!event.isTrusted) {
      draft.icon = input.value.trim();
      redraw();
    }
  });
  const field = el(
    "div",
    { className: "aw-field access-field" },
    el("span", { className: "aw-label", textContent: "Tile icon" }),
    input,
    hint,
  );
  if (window.EMOJI) window.EMOJI.attach(input);
  return field;
}

async function loadAccess() {
  try {
    renderAccess(await getJSON("/admin/access"));
  } catch (err) {
    console.error("access:", err);
    clearTimeout(accessPoll);
    if (accessWaiting) accessPoll = setTimeout(loadAccess, 3000);
  }
}
loadAccess();

// --- add-ons ---------------------------------------------------------------------------
// Each add-on from apps.d: Add (after its consent text) or Remove, through the root helper,
// which reruns install.sh. The run's bar and output are the update's own (one installer at a
// time), so the Updates pane shows it too.
const ado = {
  list: document.getElementById("addons-list"),
  progress: document.getElementById("addons-progress"),
  bar: document.getElementById("addons-bar"),
  step: document.getElementById("addons-step"),
  output: document.getElementById("addons-output"),
  log: document.getElementById("addons-log"),
};
let adoWaiting = null; // { id, addon }
let adoNote = null; // { addon, text, ok }
let adoPoll = null;
let lastAddons = null;
AL.onBuild(() => {
  if (lastAddons) renderAddons(lastAddons);
});

// What installing or removing an add-on means, on its card before the button, not in a pop-up: its
// consent text with a box to tick (the button waits for it), or what the installer does; Uninstall…
// shows what removing does, with Uninstall and Cancel. Kept across the page's redraws.
const agreedTo = new Set(); // "kind:id" of the consent texts ticked, kept across redraws
let adoAsking = null; // the add-on whose Uninstall… is open
// A text to agree to on the page: the text, a box to tick, and the button, which waits for the tick.
function agreeRow(key, text, label, disabled, go) {
  const button = el("button", {
    type: "button",
    className: "action-btn",
    textContent: label,
    disabled: disabled || !agreedTo.has(key),
    onclick: go,
  });
  return el(
    "span",
    { className: "agree-row" },
    el("span", { className: "setting-desc agree-text", textContent: text }),
    el(
      "label",
      { className: "setting-desc" },
      el("input", {
        type: "checkbox",
        checked: agreedTo.has(key),
        onchange: (event) => {
          if (event.target.checked) agreedTo.add(key);
          else agreedTo.delete(key);
          button.disabled = disabled || !event.target.checked;
        },
      }),
      " I have read this and I agree.",
    ),
    el("span", { className: "library-buttons" }, button),
  );
}
// A change asked for on the page: what it does, then the button and Cancel.
function askRow(text, label, go, cancel, disabled) {
  return el(
    "span",
    { className: "agree-row" },
    el("span", { className: "setting-desc", textContent: text }),
    el(
      "span",
      { className: "library-buttons" },
      actionButton(label, go, { disabled: !!disabled }),
      actionButton("Cancel", cancel),
    ),
  );
}
const ADDON_INSTALL_SAYS =
  "The installer runs for a few minutes. The hub server can restart during the installation.";
const ADDON_REMOVE_SAYS =
  "Its service stops. Its data stays, so installing it again finds the data.";
function addonButtons(addon, busy) {
  const redraw = () => lastAddons && renderAddons(lastAddons);
  if (addon.added) {
    if (adoAsking !== addon.id)
      return el(
        "span",
        { className: "library-buttons" },
        actionButton(
          "Uninstall add-on…",
          () => {
            adoAsking = addon.id;
            redraw();
          },
          { disabled: busy },
        ),
      );
    return askRow(
      `Uninstall ${addon.title}? ${ADDON_REMOVE_SAYS}`,
      "Uninstall add-on",
      () => {
        adoAsking = null;
        addonSet(addon, false);
      },
      () => {
        adoAsking = null;
        redraw();
      },
      busy,
    );
  }
  const install = () => addonSet(addon, true);
  if (addon.consent)
    return agreeRow(`addon:${addon.id}`, `${addon.consent} ${ADDON_INSTALL_SAYS}`, "Install add-on", busy, install);
  return el(
    "span",
    { className: "agree-row" },
    el("span", { className: "setting-desc", textContent: ADDON_INSTALL_SAYS }),
    el(
      "span",
      { className: "library-buttons" },
      actionButton("Install add-on", install, { disabled: busy }),
    ),
  );
}

function addonCard(addon, busy, withAccess, progressText) {
  const state =
    addon.added === null
      ? "Installation status unavailable. This box records no install options."
      : addon.added
        ? addon.active
          ? "Installed. Service running."
          : "Installed. Service not running (see Overview)."
        : "Not installed.";
  const note =
    adoNote && adoNote.addon === addon.id
      ? el("span", {
          className: `setting-desc action-note${adoNote.ok ? "" : " bad"}`,
          role: "status",
          textContent: adoNote.text,
        })
      : null;
  return el(
    "div",
    { className: "setting library-source" },
    el(
      "span",
      {},
      el("span", { className: "setting-name", textContent: addon.title }),
      withAccess ? accessSlot(addon.id) : null,
      el("span", { className: "setting-desc", textContent: addon.summary }),
      el("span", {
        className: `setting-desc${addon.added && !addon.active ? " bad" : ""}`,
        textContent: state,
      }),
      !addon.added && addon.needs
        ? el("span", {
            className: "setting-desc",
            textContent: `Needs: ${addon.needs}`,
          })
        : null,
      addon.added === null ? null : addonButtons(addon, busy),
      note,
      !withAccess && progressText
        ? el("span", {
            className: "setting-desc",
            role: "status",
            textContent: progressText,
          })
        : null,
      !withAccess && (progressText || note)
        ? el("a", { href: "#addons", textContent: "Installation output" })
        : null,
    ),
  );
}

function renderAddons(data) {
  lastAddons = data;
  const progress = data.progress;
  const busy = data.pending > 0 || !!adoWaiting || !!progress;
  if (adoWaiting) {
    const done = (data.results || []).find((result) => result.id === adoWaiting.id);
    if (done) {
      adoNote = {
        addon: adoWaiting.addon,
        text: resultSaid(done),
        ok: done.ok,
      };
      adoWaiting = null;
      return renderAddons(data);
    }
  }
  const steps = progress ? Math.max(progress.steps, progress.step, 1) : 0;
  const progressText = progress
    ? `${UPD_DOING[progress.action] || "Working"}, step ${Math.max(progress.step, 1)} of ${progress.estimate ? "about " : ""}${steps}` +
      (progress.label ? ` — ${said(progress.label)}` : "") +
      "."
    : "";
  // Each add-on's card is on its own page; All apps keeps one only where no page holds it.
  const paged = new Set(
    [...document.querySelectorAll(".addon-block[data-addon]")].map(
      (b) => b.dataset.addon,
    ),
  );
  ado.list.replaceChildren(
    ...data.addons
      .filter((addon) => !paged.has(addon.id))
      .map((addon) => addonCard(addon, busy, true, progressText)),
  );
  document.querySelectorAll(".addon-block").forEach((block) => {
    const addon = data.addons.find((entry) => entry.id === block.dataset.addon);
    block.replaceChildren(
      addon
        ? addonCard(addon, busy, false, progressText)
        : el("p", {
            className: "setting-desc",
            textContent: "Installation status is unavailable.",
          }),
    );
  });
  // The run in flight: the same bar as Updates, for an add-on's run or an update's.
  ado.progress.hidden = !progress;
  if (progress) {
    ado.bar.value = Math.min(Math.max(progress.step - 1, 0) / steps, 1);
    ado.step.textContent = progressText;
  }
  renderInventory();
  ado.output.hidden = !(data.log.length && (busy || adoNote));
  ado.log.textContent = data.log.join("\n");
  ado.log.scrollTop = ado.log.scrollHeight;
  if (progress && progress.action === "addon") ado.output.open = true;
  badge("addons", progress && progress.action === "addon" ? "working" : "");
  const added = data.addons.filter((addon) => addon.added).map((addon) => addon.title);
  setupStep(
    "addons",
    added.length
      ? `Installed: ${added.join(", ")}.`
      : "None installed. The hub works without them.",
    "ok",
  );
  clearTimeout(adoPoll);
  if (busy) adoPoll = setTimeout(loadAddons, 1500);
}

async function loadAddons() {
  try {
    renderAddons(await getJSON("/admin/addons"));
  } catch (_) {
    clearTimeout(adoPoll);
    if (adoWaiting) adoPoll = setTimeout(loadAddons, 3000); // the hub restarts during the run
  }
}

// Asked on the card first (addonButtons): its consent ticked, or Uninstall… then Uninstall.
async function addonSet(addon, on) {
  try {
    adoWaiting = {
      id: (await postJSON("/admin/addons", { addon: addon.id, on })).id,
      addon: addon.id,
    };
    adoNote = {
      addon: addon.id,
      text: on ? `Installing ${addon.title}…` : `Uninstalling ${addon.title}…`,
      ok: true,
    };
    loadAddons();
  } catch (err) {
    adoNote = { addon: addon.id, text: err.message, ok: false };
    loadAddons();
  }
}

loadAddons();

// --- web add-ons ---------------------------------------------------------------------------
// Added, kept current and removed by the hub itself, no installer; switched like any app
// (accessSlot). Adding one asks its consent text first; a pasted one needs the warning read.
const loc = {
  added: document.getElementById("local-added"),
  cat: document.getElementById("local-catalogue"),
  errors: document.getElementById("local-errors"),
  port: document.getElementById("local-port"),
  paste: document.getElementById("local-paste-text"),
  pasteOk: document.getElementById("local-paste-ok"),
  pasteGo: document.getElementById("local-paste-go"),
  pasteNote: document.getElementById("local-paste-note"),
};
let locNote = null; // { id, text, ok }
let locPoll = null;
const LOC_UNDERSTOOD =
  "I understand this runs someone else's code on this box's address";

function capsText(c) {
  const connect = (c && c.connect) || [];
  return (
    (connect.length
      ? `Allowed connections: ${connect.join(", ")} ({box} is this hub).`
      : "Allowed connections: its own address only.") +
    (c && c.storage ? " Stores data in this browser." : "")
  );
}

let lastLocal = null;
function renderLocal(data) {
  lastLocal = data;
  renderInventory();
  loc.port.textContent = data.addon_port;
  const note = (id) =>
    locNote && locNote.id === id
      ? el("span", {
          className: `setting-desc action-note${locNote.ok ? "" : " bad"}`,
          role: "status",
          textContent: locNote.text,
        })
      : null;
  const fetching =
    data.running || !!(data.job && data.job.action && !data.job.result);
  loc.added.replaceChildren(
    ...(data.added.length
      ? data.added.map((addon) => {
          const inst = addon.installed;
          const st = addon.status || {};
          const state =
            inst && inst.commit
              ? `Installed: ${inst.commit.slice(0, 7)} (${inst.ref === "pinned" ? "the pinned commit" : inst.ref}), from ${addon.repo}.`
              : st.error
                ? `Not installed: ${errorSaid(st)}`
                : fetching
                  ? "Downloading…"
                  : "Not installed yet.";
          return el(
            "div",
            { className: "setting library-source" },
            el(
              "span",
              {},
              el("span", { className: "setting-name", textContent: addon.title }),
              accessSlot(addon.id),
              el("span", { className: "setting-desc", textContent: addon.summary }),
              el("span", {
                className: `setting-desc${st.error && !(inst && inst.commit) ? " bad" : ""}`,
                textContent: state,
              }),
              el("span", {
                className: "setting-desc",
                textContent:
                  capsText(addon.capabilities) +
                  (addon.from_catalogue ? "" : " Added by pasting its manifest."),
              }),
              catalogueNote(addon),
              el(
                "span",
                { className: "library-buttons" },
                inst && inst.commit
                  ? el("a", {
                      className: "action-btn",
                      href: addon.href,
                      target: "_blank",
                      rel: "noopener",
                      textContent: "Open",
                    })
                  : null,
                addon.catalogue && addon.catalogue.status === "held" && locAsking !== `approve:${addon.id}`
                  ? approveButton(addon, data)
                  : null,
                locAsking === `remove:${addon.id}`
                  ? null
                  : actionButton("Remove web add-on…", () => {
                      locAsking = `remove:${addon.id}`;
                      renderLocal(data);
                    }),
              ),
              locAsking === `approve:${addon.id}` ? approveRow(addon, data) : null,
              locAsking === `remove:${addon.id}`
                ? askRow(
                    `Remove ${addon.title}? The box deletes its files. Adding it again downloads it again.`,
                    "Remove web add-on",
                    () => {
                      locAsking = null;
                      localRemove(addon);
                    },
                    () => {
                      locAsking = null;
                      renderLocal(data);
                    },
                  )
                : null,
              note(addon.id),
            ),
          );
        })
      : [
          el("p", {
            className: "setting-desc",
            textContent: "None added yet.",
          }),
        ]),
  );
  const offered = data.catalogue.filter((c) => !c.added);
  // Drawn again only when it would show something else: its cards hold a box to tick, so the
  // guard for a selection (AW keepSelections) cannot tell an unchanged block by its HTML.
  const catKey = JSON.stringify([
    data.catalogue_error || "",
    offered,
    fetching,
    offered.filter((c) => agreedTo.has(`local:${c.id}`)).map((c) => c.id),
    locNote,
  ]);
  if (loc.cat.dataset.key !== catKey) {
    loc.cat.dataset.key = catKey;
    loc.cat.replaceChildren(
    ...(data.catalogue_error
      ? [
          el("p", {
            className: "setting-desc bad",
            textContent: `The catalogue has a fault: ${data.catalogue_error}`,
          }),
        ]
      : []),
    ...(offered.length
      ? offered.map((c) =>
          el(
            "div",
            { className: "setting library-source" },
            el(
              "span",
              {},
              el("span", { className: "setting-name", textContent: c.title }),
              el("span", { className: "setting-desc", textContent: c.summary }),
              el("span", {
                className: "setting-desc",
                textContent: `${capsText(c.capabilities)} From ${c.repo} at ${String(c.pin).slice(0, 7)}.`,
              }),
              agreeRow(
                `local:${c.id}`,
                `${c.consent || ""} It starts disabled. Enable it here after the installation.`.trim(),
                "Add web add-on",
                fetching,
                () => localAdd(c),
              ),
              note(c.id),
            ),
          ),
        )
      : [
          el("p", {
            className: "setting-desc",
            textContent: "Everything in the catalogue is added.",
          }),
        ]),
  );
  }
  loc.errors.replaceChildren(
    ...Object.values(data.errors || {}).map((why) =>
      el("p", {
        className: "setting-desc bad",
        textContent: `Omitted: ${why}`,
      }),
    ),
  );
  clearTimeout(locPoll);
  if (fetching)
    locPoll = setTimeout(() => {
      loadLocal();
      loadAccess();
    }, 1500);
}

// A held catalogue update: what changed in what the administrator agreed to (server.py _agreed_changes). Only its
// consent text: the catalogue reworded it, nothing more is allowed than before. Anything else: the permissions changed.
const CONSENT_TEXT = "its consent text";
const heldTextOnly = (addon) => {
  const h = (addon.catalogue || {}).held || [];
  return h.length > 0 && h.every((x) => x === CONSENT_TEXT);
};
const approveLabel = (addon) =>
  heldTextOnly(addon) ? "Review and approve" : "Approve changed permissions";
let locAsking = null; // "approve:id" or "remove:id": the web add-on whose step is open on its card
const approveButton = (addon, data, cls = "action-btn") =>
  el("button", {
    type: "button",
    className: cls,
    textContent: approveLabel(addon),
    onclick: (event) => {
      locAsking = `approve:${addon.id}`;
      renderLocal(data);
      // From the app's own page: to its card, where the new text is.
      if (!event.target.closest("#local-added")) location.hash = "#local-added";
    },
  });
// A held catalogue update, approved on its card: what changed, the new text and permissions, a box to tick.
function approveRow(addon, data) {
  const c = (data.catalogue || []).find((x) => x.id === addon.id) || {};
  const changed = heldTextOnly(addon)
    ? "The catalogue reworded the text you agreed to. Permissions, connections and source are the same."
    : `What changed: ${((addon.catalogue || {}).held || []).join(", ")}.`;
  return el(
    "span",
    {},
    agreeRow(
      `approve:${addon.id}:${c.consent || ""}`,
      `${changed} The new text: ${c.consent || ""} ${capsText(c.capabilities)}`,
      "Approve",
      false,
      () => {
        locAsking = null;
        localAccept(addon);
      },
    ),
    actionButton("Cancel", () => {
      locAsking = null;
      renderLocal(data);
    }),
  );
}

// Where an added add-on stands against the catalogue (server.py catalogue_sync).
function catalogueNote(addon) {
  const c = addon.catalogue;
  if (!c) return null;
  const day = new Date(c.at * 1000).toLocaleDateString();
  if (c.status === "updated") {
    return el("span", {
      className: "setting-desc",
      textContent: `Updated from the catalogue on ${day}. Changes: ${(c.changed || []).join(", ") || "none to permissions, trust or source"}.`,
    });
  }
  if (c.status === "held") {
    return el("span", {
      className: "setting-desc bad",
      textContent:
        (heldTextOnly(addon)
          ? "The catalogue reworded the text you agreed to. Permissions, connections and source are the same. "
          : `The catalogue has a newer version that changes permissions, trust or source: ${(c.held || []).join(", ")}. `) +
        `The version that you approved keeps running until you select ${approveLabel(addon)}.`,
    });
  }
  if (c.status === "gone")
    return el("span", {
      className: "setting-desc",
      textContent:
        "No longer in the catalogue. It keeps working, but the catalogue no longer updates it.",
    });
  return null;
}

// Agreed on its card first (approveRow).
function localAccept(addon) {
  localPost(
    { action: "accept", id: addon.id, agree: true },
    addon.id,
    `${addon.title}: the changed permissions are approved. Downloading the new version…`,
  );
}

async function loadLocal() {
  try {
    renderLocal(await getJSON("/admin/local-addons"));
  } catch (err) {
    console.error("local add-ons:", err);
  }
}

async function localPost(body, id, doing) {
  try {
    await postJSON("/admin/local-addons", body);
    locNote = { id, text: doing, ok: true };
  } catch (err) {
    locNote = { id, text: err.message, ok: false };
  }
  loadLocal();
  loadAccess();
}

// Agreed on its card first (agreeRow); its permissions are on the card.
function localAdd(c) {
  localPost(
    { action: "add", id: c.id, agree: true },
    c.id,
    `Adding ${c.title}. Downloading it…`,
  );
}

// Asked on its card first (Remove web add-on…).
function localRemove(addon) {
  localPost({ action: "remove", id: addon.id }, addon.id, `${addon.title} removed.`);
}

// A pasted manifest: read, then its consent text and permissions shown under the box, with Add and Cancel.
loc.pasteGo.addEventListener("click", () => {
  const bad = (text) => {
    loc.pasteNote.textContent = text;
    loc.pasteNote.className = "setting-desc bad";
  };
  let manifest;
  try {
    manifest = JSON.parse(loc.paste.value);
  } catch (err) {
    bad(`Invalid manifest JSON: ${err.message}`);
    return;
  }
  if (!loc.pasteOk.checked) {
    bad("Read the warning and select the checkbox first.");
    return;
  }
  const addon = (manifest && manifest.addon) || {};
  loc.pasteNote.className = "setting-desc";
  loc.pasteNote.replaceChildren(
    askRow(
      `Add "${addon.title}" from this manifest? ${addon.consent || ""} ${capsText(manifest.capabilities)}`,
      "Add web add-on",
      () => pasteAdd(manifest, bad),
      () => loc.pasteNote.replaceChildren(),
    ),
  );
});
async function pasteAdd(manifest, bad) {
  try {
    await postJSON("/admin/local-addons", {
      action: "paste",
      manifest,
      understood: LOC_UNDERSTOOD,
    });
    loc.pasteNote.textContent = "Added. Downloading it.";
    loc.pasteNote.className = "setting-desc";
    loc.paste.value = "";
    loc.pasteOk.checked = false;
  } catch (err) {
    bad(err.message);
  }
  loadLocal();
  loadAccess();
}

loadLocal();

// --- setup steps -----------------------------------------------------------------------
// The rest of the first-use setup (the password is step 1, on admin-setup.html). Each step's
// line is filled in by the part of this page that already loads its data; the pane shows
// until the owner finishes it, and Overview's link brings it back.

// Each setup step's state, as the part of the page that knows it says; the tour draws the list.
const SETUP_STATE = {};
window.SETUP_STATE = SETUP_STATE; // the tour reads it there too (scripts evaluated one by one, as in the page tests)
function setupStep(step, text, status) {
  SETUP_STATE[step] = { text, status };
  if (window.TOUR) TOUR.drawList();
}

function applySetup(done) {
  AL.hide("welcome", done);
  if (!done && !location.hash) location.hash = "#welcome";
}

async function setSetupDone(done) {
  try {
    await postJSON("/admin/settings", { setup_done: done });
    applySetup(done);
    location.hash = done ? "#overview" : "#welcome";
  } catch (err) {
    say(err.message, false, noteEl("setup-note"));
  }
}

// The hub's own address, which these pages (on a port of their own) name in what they show and copy: this
// page's host with the hub's port, which the box tells (/admin/box).
var hubPorts = null;
function hubHost() {
  const secure = location.protocol === "https:";
  const port = hubPorts ? (secure ? hubPorts.tls : hubPorts.plain) : null;
  return port && port !== (secure ? 443 : 80) ? `${location.hostname}:${port}` : location.hostname;
}
function hubOrigin() {
  return `${location.protocol}//${hubHost()}`;
}
// How guests reach the box: the address of the hub's page, and whether it is on port 80.
function drawConnectionStep() {
  const host = hubHost();
  const port = host.includes(":") ? host.split(":").pop() : "";
  setupStep(
    "connection",
    port
      ? `At http://${host}/ on port ${port}. Something else uses port 80, so a phone that joins a hotspot does not get the captive portal page.`
      : `At http://${host}/ on the network that the box is already on.`,
    port ? "warn" : "ok",
  );
}
// The box's own login (the user admin, for scripts), as the accounts data has it: no password after
// the first run made an account, until one is set on Access.
function drawPasswordStep(al) {
  setupStep(
    "password",
    !al.password_set
      ? "No password yet. Scripts cannot sign in as admin."
      : al.on
        ? "Set. Scripts can sign in as admin."
        : "Set. Script sign-in is off (Accounts).",
    al.password_set ? "ok" : "",
  );
}
drawConnectionStep();
document
  .getElementById("setup-finish")
  .addEventListener("click", () => setSetupDone(true));
document.getElementById("setup-again").addEventListener("click", (event) => {
  event.preventDefault();
  setSetupDone(false);
});

// --- books on a USB drive ---------------------------------------------------------------
// The root helper mounts a stick only for a scan or a copy (usbstick.py); this lists what the
// last scan found, offers Import for each book and Export of the hub's books, and shows the
// copy's bar.
const usb = {
  scan: document.getElementById("usb-scan"),
  note: document.getElementById("usb-note"),
  progress: document.getElementById("usb-progress"),
  bar: document.getElementById("usb-bar"),
  step: document.getElementById("usb-step"),
  devices: document.getElementById("usb-devices"),
};
let usbWaiting = null; // { id, at }
let usbNote = null; // { at, text, ok }
let usbPoll = null;

function renderUsb(data) {
  const progress = data.progress;
  const busy = data.pending > 0 || !!usbWaiting || !!progress;
  if (usbWaiting) {
    const done = (data.results || []).find((result) => result.id === usbWaiting.id);
    if (done) {
      usbNote = { at: usbWaiting.at, text: resultSaid(done), ok: done.ok };
      usbWaiting = null;
      if (done.ok) loadLibrary();
      return renderUsb(data);
    }
  }
  const noteAt = (at) =>
    usbNote && usbNote.at === at
      ? el("span", {
          className: `setting-desc action-note${usbNote.ok ? "" : " bad"}`,
          role: "status",
          textContent: usbNote.text,
        })
      : null;
  say(
    usbNote && usbNote.at === "scan" ? usbNote.text : "",
    usbNote ? usbNote.ok : true,
    usb.note,
  );
  usb.scan.disabled = busy;
  const devs = (data.scan && data.scan.devices) || [];
  usb.devices.replaceChildren(
    ...devs.map((device) => {
      const pick = el(
        "select",
        { disabled: busy || !data.books.length },
        ...data.books.map((b) =>
          el("option", { value: b, textContent: `${b}.zim` }),
        ),
      );
      return el(
        "div",
        { className: "setting library-source" },
        el(
          "span",
          {},
          el("span", {
            className: "setting-name",
            textContent: `${device.label || device.name} (${device.fstype}, ${size(Number(device.size))})`,
          }),
          device.error
            ? el("span", {
                className: "setting-desc bad",
                textContent: errorSaid(device),
              })
            : null,
          ...(device.zims.length
            ? device.zims.map((z) =>
                el(
                  "span",
                  { className: "usb-book" },
                  el("span", {
                    className: "setting-desc",
                    textContent: `${z.file} · ${size(z.size)}${z.zim ? "" : ` · Cannot import: ${z.problem || "not a ZIM file"}`}`,
                  }),
                  z.zim
                    ? el("button", {
                        type: "button",
                        className: "action-btn small",
                        textContent: "Copy book to library",
                        disabled: busy,
                        onclick: () =>
                          usbRequest(
                            { action: "import", device: device.name, file: z.file },
                            `${device.name}:${z.file}`,
                          ),
                      })
                    : null,
                  noteAt(`${device.name}:${z.file}`),
                ),
              )
            : [
                el("span", {
                  className: "setting-desc",
                  textContent: device.error ? "" : "No books on this drive.",
                }),
              ]),
          data.books.length
            ? el(
                "span",
                { className: "library-buttons" },
                pick,
                el("button", {
                  type: "button",
                  className: "action-btn",
                  textContent: "Export book to USB drive",
                  disabled: busy,
                  onclick: () =>
                    usbRequest(
                      { action: "export", device: device.name, book: pick.value },
                      `export:${device.name}`,
                    ),
                }),
              )
            : null,
          noteAt(`export:${device.name}`),
        ),
      );
    }),
  );
  if (data.scan && !devs.length)
    usb.devices.replaceChildren(
      el("p", {
        className: "setting-desc",
        textContent:
          "No USB drive found. Connect a USB drive. Select Scan USB drives.",
      }),
    );
  usb.progress.hidden = !progress;
  if (progress) {
    usb.bar.value = progress.total ? Math.min(progress.done / progress.total, 1) : 0;
    usb.step.textContent = `${said(progress.label)}${progress.total ? `: ${size(progress.done)} of ${size(progress.total)} (${Math.round((100 * progress.done) / progress.total)}%)` : "…"}`;
  }
  clearTimeout(usbPoll);
  if (busy) usbPoll = setTimeout(loadUsb, 1000);
}

async function loadUsb() {
  try {
    renderUsb(await getJSON("/admin/usb"));
  } catch (_) {
    /* shown on the next poll */
  }
}

async function usbRequest(body, at) {
  try {
    usbWaiting = { id: (await postJSON("/admin/usb", body)).id, at };
    usbNote = null;
    loadUsb();
  } catch (err) {
    usbNote = { at, text: err.message, ok: false };
    loadUsb();
  }
}

usb.scan.addEventListener("click", () =>
  usbRequest({ action: "scan" }, "scan"),
);
loadUsb();

// --- git: the two servers' repositories (gitrepos.py) ----------------------------------
// The hub owns the repositories, so these changes need no root helper: each answer is the
// new list.
const git = {
  usage: noteEl("git-usage"),
  note: noteEl("git-note"),
  form: document.getElementById("git-create"),
  createNote: noteEl("git-create-note"),
  grid: noteEl("git-grid"),
  search: document.getElementById("git-search"),
  chipsKind: noteEl("git-chips-kind"),
  chipsArea: noteEl("git-chips-area"),
  mirrors: noteEl("git-mirrors"),
  mirrorForm: document.getElementById("git-mirror-add"),
  mirrorNote: noteEl("git-mirror-note"),
};
const commitDate = (unix) => new Date(unix * 1000).toISOString().slice(0, 10);
// The Git page as cards (Kiwix's library as the model): a filter bar, a card per
// repository with its badges in words, and Manage, opening under its card.
let gitData = null;
const gitView = { kind: "all", area: "all", q: "", open: null };
const PUSH_WORDS = {
  everyone: "anyone pushes",
  admin: "administrators push",
  nobody: "read-only",
};
const builds = (repo) => repo.can_build && repo.build && repo.has_script;
const repoKey = (repo) => `${repo.area}/${repo.name}`;
const GIT_KINDS = [
  ["all", "All", () => true],
  ["mine", "Local repositories", (repo) => !repo.mirror_of],
  ["mirrors", "Mirrors", (repo) => !!repo.mirror_of],
  ["builds", "Builds", builds],
];
const GIT_AREAS = [
  ["all", "All access", () => true],
  ["public", "Guests", (repo) => repo.area === "public"],
  ["private", "Administrators", (repo) => repo.area === "private"],
];

// One sentence for the card: who sees it, who pushes, whether pushes are built.
function accessSentence(repo) {
  const see =
    repo.area === "public"
      ? "Anyone on the network can see and clone it."
      : "Only administrators can see and clone it.";
  const push = repo.mirror_of
    ? "Nobody can push to it. The librarian updates it."
    : {
        everyone: "Anyone can push to it.",
        admin: "Only administrators can push to it.",
        nobody: "Nobody can push to it.",
      }[repo.write];
  const build = builds(repo)
    ? " The box builds each push."
    : repo.can_build && repo.has_script && !repo.build
      ? " Builds are disabled."
      : "";
  return `${see} ${push}${build}`;
}

function renderGit(data) {
  gitData = data;
  if (!data.installed) {
    git.usage.textContent =
      "The git servers are not set up on this box. Run install.sh again.";
    git.grid.replaceChildren();
    return;
  }
  renderMirrors(data);
  const total = data.repos.reduce((n, repo) => n + repo.size, 0);
  git.usage.textContent =
    `${data.repos.length} repositor${data.repos.length === 1 ? "y" : "ies"}, ${size(total)}` +
    (data.free != null ? `, ${size(data.free)} free on the card` : "") +
    `. A single push can be up to ${size(data.max_push)}.`;
  // Submodule mirrors have no card of their own: Git → Mirrors lists them under their parent.
  const shown = data.repos.filter((repo) => !repo.submodule_of);
  const chips = (box, list, key) =>
    box.replaceChildren(
      ...list.map(([id, label, test]) =>
        el("button", {
          type: "button",
          className: `chip${gitView[key] === id ? " on" : ""}`,
          textContent: `${label} ${shown.filter(test).length}`,
          onclick: () => {
            gitView[key] = id;
            renderGit(gitData);
          },
        }),
      ),
    );
  chips(git.chipsKind, GIT_KINDS, "kind");
  chips(git.chipsArea, GIT_AREAS, "area");
  [...git.chipsKind.children, ...git.chipsArea.children].forEach((b) =>
    b.setAttribute("aria-pressed", b.classList.contains("on")),
  );
  const q = gitView.q.toLowerCase();
  const kindTest = GIT_KINDS.find((k) => k[0] === gitView.kind)[2];
  const areaTest = GIT_AREAS.find((k) => k[0] === gitView.area)[2];
  const list = shown.filter(
    (repo) =>
      kindTest(repo) &&
      areaTest(repo) &&
      (!q ||
        repo.name.toLowerCase().includes(q) ||
        (repo.description || "").toLowerCase().includes(q)),
  );
  const cards = [];
  for (const repo of list) {
    cards.push(repoCard(repo, data));
    if (gitView.open === repoKey(repo))
      cards.push(managePanel(repo, data, "git-drawer"));
  }
  if (!list.length)
    cards.push(
      el("p", {
        className: "setting-desc",
        textContent: shown.length ? "Nothing matches." : "No repositories yet.",
      }),
    );
  // The New card last, after the repositories, its drawer after it.
  cards.push(newCard());
  if (gitView.open === "new") cards.push(newPanel("git-drawer"));
  git.grid.replaceChildren(...cards);
}

function newCard() {
  return withAttrs(
    el(
      "button",
      {
        type: "button",
        className: "git-card git-new",
        onclick: () => {
          gitView.open = gitView.open === "new" ? null : "new";
          renderGit(gitData);
        },
      },
      el("span", {
        className: "git-new-plus",
        textContent: "＋",
        ariaHidden: "true",
      }),
      el("strong", { textContent: "New" }),
      el("span", {
        className: "setting-desc",
        textContent: "An empty repository, or a mirror of one on the internet",
      }),
    ),
    { "aria-expanded": String(gitView.open === "new") },
  );
}
const withAttrs = (node, attrs) => {
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
};

function newPanel(cls) {
  git.form.hidden = false;
  return el(
    "div",
    { className: `git-manage ${cls}` },
    el(
      "div",
      { className: "git-manage-head" },
      el("strong", { textContent: "New" }),
      closeButton(),
    ),
    el(
      "section",
      {},
      el("h4", { textContent: "An empty repository" }),
      el("p", {
        className: "setting-desc",
        textContent:
          "Push to it from a computer. Guests: anyone on the network can browse and clone it. Until you change the push setting, only administrators can push. Administrators: only administrators can browse, clone or push.",
      }),
      git.form,
      git.createNote,
    ),
    el(
      "section",
      {},
      el("h4", { textContent: "A mirror of a repository on the internet" }),
      el("p", {
        className: "setting-desc",
        textContent:
          "Kept by the librarian to a policy (branches, releases, submodules), read-only here. Mirrors are set up and managed under Mirrors, below.",
      }),
      el("a", {
        href: "#mirrors",
        className: "button-link",
        textContent: "Add a mirror",
      }),
    ),
  );
}

const closeButton = () =>
  actionButton(
    "Close",
    () => {
      gitView.open = null;
      renderGit(gitData);
    },
    { className: "small" },
  );
const copyUrl = (repo) =>
  actionButton(
    "Copy clone URL",
    () => {
      const url = `${hubOrigin()}${repo.url.replace(/\/$/, "")}`;
      navigator.clipboard?.writeText(url).then(
        () => say(`Copied ${url}`, true, git.note),
        () => say(url, true, git.note),
      );
    },
    { className: "small" },
  );

function repoCard(repo, data) {
  const m = repo.mirror_of
    ? (data.mirrors || []).find((x) => x.name === repo.name && x.area === repo.area)
    : null;
  const ms = (m && m.status) || {};
  const b = repo.last_build;
  const badges = [
    [repo.area === "public" ? "Guests" : "Administrators", `badge-${repo.area}`],
    [repo.mirror_of ? "read-only" : PUSH_WORDS[repo.write], "badge-push"],
    repo.mirror_of ? ["Mirror", "badge-mirror"] : null,
    builds(repo)
      ? [
          b
            ? `${STATE_ICON[b.state] || ""} build ${b.state}${b.started ? `, ${commitDate(b.started)}` : ""}`
            : "builds on push",
          `badge-build${b && b.state === "failed" ? " bad" : ""}`,
        ]
      : null,
  ].filter(Boolean);
  const facts = repo.mirror_of
    ? [
        `from ${repo.mirror_of.replace(/^https:\/\//, "")}`,
        ms.updated ? `updated ${commitDate(ms.updated)}` : "not downloaded yet",
        size(repo.size),
      ]
    : [
        repo.last_commit ? `Last commit ${commitDate(repo.last_commit)}` : "Empty",
        repo.branches
          ? `${repo.branches} branch${repo.branches === 1 ? "" : "es"}`
          : null,
        size(repo.size),
      ];
  const open = gitView.open === repoKey(repo);
  return el(
    "div",
    { className: `git-card${open ? " open" : ""}` },
    el("h4", {}, el("a", { href: repo.url, textContent: `${repo.name}.git` })),
    el(
      "p",
      { className: "badges" },
      ...badges.map(([text, cls]) =>
        el("span", { className: `badge ${cls}`, textContent: text }),
      ),
    ),
    repo.description
      ? el(
          "p",
          { className: "git-about" },
          el("span", { className: "git-label", textContent: "About " }),
          repo.description,
        )
      : null,
    el("p", {
      className: "setting-desc git-facts",
      textContent: facts.filter(Boolean).join(" · "),
    }),
    el("p", {
      className: "setting-desc git-access",
      textContent: accessSentence(repo),
    }),
    el(
      "p",
      { className: "library-buttons" },
      copyUrl(repo),
      repo.mirror_of
        ? el("a", {
            href: "#mirrors",
            className: "button-link small",
            textContent: "Manage the mirror",
          })
        : withAttrs(
            actionButton(
              "Manage",
              () => {
                gitView.open = open ? null : repoKey(repo);
                renderGit(gitData);
              },
              { className: "small" },
            ),
            { "aria-expanded": String(open) },
          ),
    ),
  );
}

// Manage: each part with a sentence of what it does; Delete at the bottom, apart.
function managePanel(repo, data, cls) {
  // Only Delete asks, on the page (confirmText): a move can be moved back (admin pages plan, phase 5).
  const act = (body, confirmText, done) => async (event) => {
    // Cancelled: drawn again as it is, so a radio clicked does not stay showing a choice not made.
    if (
      body.action === "delete" &&
      !(await AW.confirmHere(confirmText, { anchor: event && event.currentTarget, label: "Delete" }))
    ) {
      renderGit(gitData);
      return false;
    }
    try {
      const out = await postJSON("/admin/git", body);
      if (body.action === "move") gitView.open = `${body.to}/${repo.name}`;
      if (body.action === "delete") gitView.open = null;
      renderGit(out);
      say(done || "", true, git.note);
      return true;
    } catch (err) {
      say(err.message, false, git.note);
      renderGit(gitData);
      return false;
    }
  };
  const radio = (group, value, label, checked, onpick) =>
    el(
      "label",
      { className: "inline" },
      el("input", {
        type: "radio",
        name: `${group}-${repoKey(repo)}`,
        value,
        checked,
        onchange: onpick,
      }),
      ` ${label}`,
    );
  // Who can see it: the area. Moving changes the clone URL, said under the choices.
  const see = el(
    "fieldset",
    { className: "git-q" },
    el("legend", { textContent: "Who can see it?" }),
    radio(
      "see",
      "public",
      "Everyone on the network",
      repo.area === "public",
      act(
        { action: "move", area: repo.area, name: repo.name, to: "public" },
        null,
        `${repo.name}.git is now visible to guests.`,
      ),
    ),
    radio(
      "see",
      "private",
      "Only administrators",
      repo.area === "private",
      act(
        { action: "move", area: repo.area, name: repo.name, to: "private" },
        null,
        `${repo.name}.git is now visible to administrators only.`,
      ),
    ),
    el("p", {
      className: "setting-desc",
      textContent:
        "Moving it changes its clone URL. Clones elsewhere need git remote set-url.",
    }),
  );
  const presets = (data.presets || {})[repo.area] || [];
  const WHO = {
    everyone: "Anyone",
    users: "Users (account name and password)",
    admin: "Only administrators",
    nobody: "Nobody (read-only)",
  };
  const push = el(
    "fieldset",
    { className: "git-q" },
    el("legend", { textContent: "Who can push?" }),
    ...presets.map((preset) =>
      radio(
        "push",
        preset.name,
        WHO[preset.write],
        preset.name === repo.preset,
        act(
          { action: "preset", area: repo.area, name: repo.name, preset: preset.name },
          null,
          `${repo.name}.git: ${preset.text}.`,
        ),
      ),
    ),
    el("p", {
      className: "setting-desc",
      textContent:
        "The hub server decides each push. Nobody can rewrite or delete history in a repository that guests can read.",
    }),
    presets.some((preset) => preset.write === "everyone")
      ? el("p", {
          className: "setting-desc",
          textContent:
            "With Anyone, people on the network push without signing in. Repositories that guests can read have a storage limit. The box serves anything they push.",
        })
      : null,
  );
  const buildNow =
    repo.can_build && repo.build && repo.has_script
      ? actionButton(
          "Build now",
          async () => {
            try {
              const out = await postJSON("/admin/ci", {
                action: "build",
                repo: repo.name,
              });
              say(
                `Queued: ${repo.name}.git ${out.branch} at ${out.commit.slice(0, 7)}. Its run shows under Builds.`,
                true,
                git.note,
              );
              renderCi(out.snapshot);
            } catch (err) {
              say(err.message, false, git.note);
            }
          },
          { className: "small" },
        )
      : null;
  const build = el(
    "fieldset",
    { className: "git-q" },
    el("legend", { textContent: "Build on push?" }),
    repo.can_build
      ? el(
          "label",
          { className: "inline" },
          el("input", {
            type: "checkbox",
            className: "git-build",
            checked: repo.build,
            onchange: (event) =>
              act(
                {
                  action: "build",
                  area: repo.area,
                  name: repo.name,
                  on: event.target.checked,
                },
                null,
                `${repo.name}.git: builds ${event.target.checked ? "enabled" : "disabled"}.`,
              )(),
          }),
          " Build each push that has a .irate-ci.sh",
        )
      : null,
    el("p", {
      className: "setting-desc",
      textContent: repo.can_build
        ? repo.has_script
          ? "Its newest commit has a .irate-ci.sh. A build runs as a dedicated user in a sandbox at the lowest priority (Builds, below)."
          : "Its newest commit has no .irate-ci.sh yet. Add one at the top of the repository to build."
        : repo.area === "public"
          ? "Repositories that guests can push to never build, because anyone could push code that runs."
          : "Only for repositories that only administrators can push to.",
    }),
    buildNow,
    repo.can_build ? buildSetup() : null,
  );
  // Its description: a section of one value (a redraw of the list keeps what is being typed).
  const desc = el("textarea", {
    rows: 2,
    maxLength: 200,
    className: "git-desc",
  });
  const descSec = AW.section({
    id: `git-desc-${repoKey(repo)}`,
    label: `Description of ${repo.name}.git`,
    saved: { description: repo.description || "" },
    save: async (c) =>
      (await act(
        {
          action: "describe",
          area: repo.area,
          name: repo.name,
          description: c.description,
        },
        null,
        "Saved.",
      )())
        ? "Saved."
        : false,
    onChange: () => {
      desc.value = descSec.get("description");
    },
  });
  desc.value = descSec.get("description");
  desc.addEventListener("input", () =>
    descSec.set("description", desc.value, true),
  );
  const about = el(
    "section",
    {},
    el("h4", { textContent: "About" }),
    el("p", {
      className: "setting-desc",
      textContent: "A line on what it is, shown on its card and in cgit.",
    }),
    desc,
    descSec.foot,
    descSec.reason,
  );
  const others = data.repos.filter(
    (o) => !(o.area === repo.area && o.name === repo.name) && !o.mirror_of,
  );
  const target = el(
    "select",
    { className: "git-publish-to" },
    ...others.map((o) =>
      el("option", {
        value: repoKey(o),
        textContent: `${o.area}/${o.name}.git`,
      }),
    ),
  );
  const refs = withAttrs(
    el("input", { value: "main", className: "git-publish-refs" }),
    { "aria-label": "Branches or tags, separated by spaces" },
  );
  const publish = el(
    "section",
    {},
    el("h4", { textContent: "Publish to…" }),
    el("p", {
      className: "setting-desc",
      textContent:
        "Copy branches or tags into another repository on the box. They replace branches or tags of the same name there. Example: copy a draft from an administrator-only repository to a repository that guests can read.",
    }),
    others.length
      ? el(
          "p",
          { className: "library-buttons" },
          target,
          refs,
          actionButton(
            "Publish branches or tags",
            () => {
              const [toArea, toName] = target.value.split("/");
              act(
                {
                  action: "publish",
                  from: { area: repo.area, name: repo.name },
                  to: { area: toArea, name: toName },
                  refs: refs.value.trim().split(/\s+/),
                },
                null,
                "Published.",
              )();
            },
            { className: "small" },
          ),
        )
      : el("p", {
          className: "setting-desc",
          textContent: "There is no other repository to publish to yet.",
        }),
  );
  return el(
    "div",
    { className: `git-manage ${cls}` },
    el(
      "div",
      { className: "git-manage-head" },
      el("strong", { textContent: `Manage ${repo.name}.git` }),
      closeButton(),
    ),
    el(
      "section",
      {},
      el("h4", { textContent: "Access" }),
      el("p", { className: "setting-desc", textContent: accessSentence(repo) }),
      see,
      push,
      build,
    ),
    about,
    publish,
    el(
      "section",
      { className: "git-danger" },
      el("h4", { textContent: "Delete" }),
      el("p", {
        className: "setting-desc",
        textContent:
          "Deleting it removes its history. Clones elsewhere keep their own history. You cannot restore this repository.",
      }),
      actionButton(
        `Delete repository ${repo.name}.git`,
        act(
          { action: "delete", area: repo.area, name: repo.name },
          `Delete the repository ${repo.name}.git and all its history? Clones elsewhere keep their own history. You cannot restore this repository.`,
          "Deleted.",
        ),
        { className: "small danger" },
      ),
    ),
  );
}

git.search.addEventListener("input", () => {
  gitView.q = git.search.value.trim();
  if (gitData) renderGit(gitData);
});

// Mirrors (mirrors.py): what each keeps, its size and last outcome, and check / update / remove.
function renderMirrors(data) {
  // A mirror removed can be mirrored again from upstream: it acts at once.
  const act = (body) => async () => {
    try {
      renderGit(await postJSON("/admin/git", body));
      say(
        body.action === "mirror-remove"
          ? "Removed."
          : "Started. The outcome shows here when it is done.",
        true,
        git.mirrorNote,
      );
    } catch (err) {
      say(err.message, false, git.mirrorNote);
    }
  };
  const list = data.mirrors || [];
  git.mirrors.replaceChildren(
    ...(list.length
      ? list.map((m) => {
          const st = m.status || {};
          const keeps = [
            m.branches.join(", "),
            ...m.groups.map(
              (g) =>
                `${g.keep} ${g.kind === "tags" ? `tag${g.keep === 1 ? "" : "s"} ${g.pattern}` : g.kind + (g.keep === 1 ? "" : "s")}`,
            ),
            m.history,
            m.submodules ? "with submodules" : null,
            `budget ${m.budget_mb} MB`,
          ]
            .filter(Boolean)
            .join(" · ");
          const subs = Object.entries(st.submodules || {}).map(
            ([path, submodule]) =>
              `${path} (${submodule.kept.length} commit${submodule.kept.length === 1 ? "" : "s"}, ${size(submodule.size)})`,
          );
          const state = st.error
            ? `error: ${errorSaid(st)}`
            : st.outcome
              ? `${st.outcome}${st.size != null ? ` · ${size(st.size)}` : ""}${st.checked ? ` · checked ${commitDate(st.checked)}` : ""}`
              : "not downloaded yet";
          // Release groups: revoked releases (named so by their project) are left out unless switched off.
          const relGroups = m.groups.filter((g) => g.kind !== "tags");
          const skipping = relGroups.some((g) => g.skip_revoked !== false);
          const revoked = ((st.releases || {}).list || [])
            .filter((release) => release[2])
            .map((release) => release[0]);
          const relNote = !relGroups.length
            ? null
            : [
                skipping
                  ? revoked.length
                    ? `Revoked releases are excluded: ${revoked.join(", ")}`
                    : "Revoked releases are excluded"
                  : "Revoked releases are included",
                st.releases_cached
                  ? `release list from the cache of ${st.releases_cached.since ? commitDate(st.releases_cached.since) : "earlier"} (${st.releases_cached.why})`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ");
          return el(
            "div",
            { className: "admin-item" },
            el(
              "span",
              {},
              el(
                "strong",
                {},
                el("a", {
                  href: `${m.area === "public" ? "/git/" : "/git-private/"}${m.name}.git/`,
                  textContent: `${m.name}.git`,
                }),
              ),
              el("span", {
                className: "setting-desc",
                textContent: `from ${m.upstream}`,
              }),
              el("span", {
                className: "setting-desc",
                textContent: `keeps ${keeps}`,
              }),
              subs.length
                ? el("span", {
                    className: "setting-desc",
                    textContent: `submodules: ${subs.join(", ")}`,
                  })
                : null,
              relNote
                ? el("span", {
                    className: `setting-desc${st.releases_cached ? " warn" : ""}`,
                    textContent: relNote,
                  })
                : null,
              el("span", {
                className: `setting-desc${st.error || st.over_budget ? " bad" : ""}`,
                textContent: state,
              }),
              // Every upstream tag, held apart for checking (mirrors.py _sync_tags); one moved upstream said plainly.
              st.tags
                ? el("span", {
                    className: `setting-desc${(st.tags.moved || []).length ? " bad" : ""}`,
                    textContent:
                      `Holds all ${st.tags.count} tags of its upstream repository (${st.tags.signed} carry a signature that the box does not verify), without their files.` +
                      ((st.tags.moved || []).length
                        ? ` Moved upstream since first detected: ${st.tags.moved.join(", ")}.`
                        : ""),
                  })
                : null,
              st.tags_error
                ? el("span", {
                    className: "setting-desc bad",
                    textContent: `The tags: ${st.tags_error}`,
                  })
                : null,
            ),
            el(
              "span",
              { className: "library-buttons" },
              actionButton(
                "Check now",
                act({ action: "mirror-check", name: m.name }),
                { className: "small", disabled: data.running },
              ),
              actionButton(
                "Update mirror",
                act({ action: "mirror-update", name: m.name }),
                {
                  className: "small",
                  disabled: data.running,
                  title:
                    "Updating a mirror downloads new data from the upstream repository",
                },
              ),
              actionButton(
                m.submodules ? "Without submodules" : "With submodules",
                act({
                  action: "mirror-change",
                  mirror: Object.assign({}, m, {
                    status: undefined,
                    submodules: !m.submodules,
                  }),
                }),
                { className: "small" },
              ),
              relGroups.length
                ? actionButton(
                    skipping
                      ? "Include revoked releases"
                      : "Exclude revoked releases",
                    act(
                      {
                        action: "mirror-change",
                        mirror: Object.assign({}, m, {
                          status: undefined,
                          groups: m.groups.map((g) =>
                            g.kind === "tags"
                              ? g
                              : Object.assign({}, g, {
                                  skip_revoked: !skipping,
                                }),
                          ),
                        }),
                      },
                      skipping
                        ? "Include releases that their project has revoked? The mirror excludes them by default. Include them only to investigate them."
                        : null,
                    ),
                    { className: "small" },
                  )
                : null,
              actionButton(
                "Remove local mirror",
                act(
                  { action: "mirror-remove", name: m.name },
                  `Remove the local mirror ${m.name}.git and its copy on the box? The upstream repository is not changed.`,
                ),
                { className: "small" },
              ),
            ),
          );
        })
      : [el("p", { className: "setting-desc", textContent: "None yet." })]),
  );
}

git.mirrorForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fields = git.mirrorForm.elements;
  const groups = [];
  if (fields.pattern.value.trim())
    groups.push({
      kind: "tags",
      pattern: fields.pattern.value.trim(),
      keep: Number(fields.releases.value) || 0,
    });
  else {
    if (Number(fields.releases.value))
      groups.push({ kind: "release", keep: Number(fields.releases.value) });
    if (Number(fields.prereleases.value))
      groups.push({ kind: "prerelease", keep: Number(fields.prereleases.value) });
  }
  const mirror = {
    upstream: fields.upstream.value.trim(),
    name: fields.name.value.trim(),
    area: fields.area.value,
    branches: fields.branches.value.trim().split(/\s+/).filter(Boolean),
    groups,
    history: fields.history.value,
    budget_mb: Number(fields.budget.value) || 2048,
    submodules: fields.submodules.checked,
  };
  try {
    renderGit(await postJSON("/admin/git", { action: "mirror-add", mirror }));
    say(
      `Added ${mirror.name}.git. Select Update mirror to download it now, or the librarian downloads it on its schedule.`,
      true,
      git.mirrorNote,
    );
  } catch (err) {
    say(err.message, false, git.mirrorNote);
  }
});

async function loadGit() {
  try {
    renderGit(await getJSON("/admin/git"));
  } catch (_) {
    git.usage.textContent = "Could not read the repositories.";
  }
}

git.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fields = git.form.elements;
  try {
    renderGit(
      await postJSON("/admin/git", {
        action: "create",
        area: fields.area.value,
        name: fields.name.value,
        description: fields.description.value,
      }),
    );
    say(
      `Created ${fields.name.value.replace(/\.git$/, "")}.git. Push to it with: git push http://${hubHost()}${fields.area.value === "public" ? "/git/" : "/git-private/"}${fields.name.value.replace(/\.git$/, "")}.git main`,
      true,
      git.createNote,
    );
    git.form.reset();
  } catch (err) {
    say(err.message, false, git.createNote);
  }
});
loadGit();

// --- builds on push (ci.py): the newest runs, refreshed while one is going -----------------
const ciAbout = noteEl("ci-about");
const ciRuns = noteEl("ci-runs");
let ciPoll = null;
const STATE_ICON = {
  passed: "✅",
  failed: "❌",
  "timed out": "⏱",
  interrupted: "⏹",
  running: "⏳",
};
const minutes = (seconds) => (seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`);

let ciData = null;
let ciViewing = null; // { run, next, log } while a run's view is open
function renderCi(data) {
  ciData = data;
  if (!data.installed) {
    ciAbout.textContent =
      "Builds are not set up on this box. Run install.sh again.";
    return;
  }
  ciAbout.textContent =
    `A push to an administrator-only repository builds here when its commit has a ${data.script} at the top. ` +
    `The box makes a fresh clone and runs bash ${data.script} as a dedicated user at the lowest priority. Repositories that guests can push to never build. ` +
    "Set up builds, or select Build now, under Manage on the card of a repository." +
    (data.queued ? ` ${data.queued} waiting.` : "");
  const mem = data.memory || {};
  const memText = /^\d+$/.test(mem.max || "")
    ? `memory limit ${size(Number(mem.max))}`
    : "no memory limit";
  noteEl("ci-limits").textContent =
    `Each build: up to ${Math.round(data.time_limit / 3600)} hours, ${memText}, ` +
    `the lowest CPU priority. The runs use ${size(data.runs_bytes || 0)}. The box retains the newest ${data.keep_runs} runs for each repository, and every run marked Retain run.`;
  ciKeep.fill({ keep: data.keep_runs });
  noteEl("ci-offline").textContent =
    "Without internet access: " +
    ((data.mirrored || []).length
      ? `git downloads of ${data.mirrored.length} URL${data.mirrored.length === 1 ? "" : "s"} in a build use the mirrors of this box (${data.mirrored
          .slice(0, 3)
          .map((url) => url.replace(/^https:\/\//, ""))
          .join(", ")}${data.mirrored.length > 3 ? "…" : ""}).`
      : "no mirrors exist yet, so a build that downloads from the internet needs internet access (Git → Mirrors).") +
    (data.pio_deps
      ? " PlatformIO packages come from the cache of the library (CI_PIO_DEPS)."
      : " No PlatformIO cache is kept (Builds above, the firmware build cache).") +
    (data.wheelhouse
      ? " pip installs (platformio itself) use the wheelhouse of the Building toolkit (PIP_NO_INDEX, PIP_FIND_LINKS)."
      : " The Building toolkit has no wheelhouse yet, so a pip install in a build still needs internet access.");
  ciRuns.replaceChildren(
    ...(data.runs.length
      ? data.runs.map((run) =>
          el(
            "div",
            { className: "admin-item" },
            el(
              "span",
              {},
              el("strong", {
                textContent:
                  `${STATE_ICON[run.state] || ""} ${run.repo} · ${run.branch} · ${run.commit.slice(0, 7)}` +
                  (run.keep ? " · retained" : ""),
              }),
              el("span", {
                className: "setting-desc",
                textContent: [
                  run.state,
                  run.by === "build-now" ? "built manually" : null,
                  run.duration != null ? minutes(run.duration) : null,
                  run.started ? commitDate(run.started) : null,
                ]
                  .filter(Boolean)
                  .join(" · "),
              }),
            ),
            el(
              "span",
              { className: "library-buttons" },
              actionButton("View", () => openRun(run.run), {
                className: "small",
              }),
            ),
          ),
        )
      : [
          el("p", { className: "setting-desc", textContent: "No builds yet." }),
        ]),
  );
  // A build that failed since this page last showed the Git pane: a mark on its side-bar link.
  let seen = 0;
  try {
    seen = Number(localStorage.getItem("irate-ci-seen")) || 0;
  } catch (_) {
    /* no storage */
  }
  const newBad = data.runs.filter(
    (run) => run.finished && run.finished > seen && run.state !== "passed",
  ).length;
  if (paneShown("git")) {
    try {
      localStorage.setItem("irate-ci-seen", String(Date.now() / 1000));
    } catch (_) {
      /* no storage */
    }
  }
  badge("git", !paneShown("git") && newBad ? String(newBad) : "");
  clearTimeout(ciPoll);
  if (data.queued || data.runs.some((run) => run.state === "running"))
    ciPoll = setTimeout(loadCi, 5000);
}

const ciFile = (run, name) =>
  `/admin/ci/file?run=${encodeURIComponent(run)}&name=${encodeURIComponent(name)}`;
async function openRun(run) {
  ciViewing = { run, next: 0, log: "" };
  await followRun();
}
async function followRun() {
  if (!ciViewing) return;
  const box = noteEl("ci-run-view");
  let v;
  try {
    v = await getJSON(
      `/admin/ci/run?run=${encodeURIComponent(ciViewing.run)}&from=${ciViewing.next}`,
    );
  } catch (err) {
    box.hidden = false;
    box.replaceChildren(
      el("p", { className: "setting-desc bad", textContent: err.message }),
    );
    return;
  }
  ciViewing.log += v.log;
  ciViewing.next = v.next;
  const st = v.status;
  const running = st.state === "running";
  const pre = el("pre", {
    className: "admin-log ci-log",
    textContent: ciViewing.log.slice(-200000),
  });
  box.hidden = false;
  box.replaceChildren(
    el(
      "div",
      { className: "git-manage-head" },
      el("strong", {
        textContent: `${STATE_ICON[st.state] || ""} ${st.repo} · ${st.branch} · ${st.commit.slice(0, 7)}`,
      }),
      actionButton(
        "Close",
        () => {
          ciViewing = null;
          box.hidden = true;
        },
        { className: "small" },
      ),
    ),
    el("p", {
      className: "setting-desc",
      textContent: [
        st.state,
        st.duration != null
          ? minutes(st.duration)
          : running
            ? `running for ${minutes(Math.round(Date.now() / 1000 - st.started))}`
            : null,
        st.keep ? "retained" : null,
      ]
        .filter(Boolean)
        .join(" · "),
    }),
    v.steps.length
      ? el(
          "ol",
          { className: "ci-steps" },
          ...v.steps.map((x) => el("li", { textContent: x })),
        )
      : null,
    pre,
    v.artifacts.length
      ? el(
          "p",
          { className: "library-buttons" },
          el("span", { className: "setting-desc", textContent: "Artifacts: " }),
          ...v.artifacts.map((artifact) =>
            el("a", {
              href: ciFile(v.run, artifact.name),
              className: "button-link small",
              textContent: `${artifact.name} (${size(artifact.size)})`,
            }),
          ),
        )
      : null,
    el(
      "p",
      { className: "library-buttons" },
      el("a", {
        href: ciFile(v.run, "log.txt"),
        target: "_blank",
        className: "button-link small",
        textContent: "Download full log",
      }),
      running
        ? null
        : actionButton(
            st.keep ? "Stop retaining run" : "Retain run",
            () => ciChange(v.run, st.keep ? "unkeep" : "keep"),
            { className: "small" },
          ),
      running
        ? null
        : actionButton(
            "Delete run",
            async (event) => {
              if (await AW.confirmHere("Delete this run, its log and its artifacts?", { anchor: event.currentTarget }))
                ciChange(v.run, "delete");
            },
            { className: "small danger" },
          ),
    ),
  );
  pre.scrollTop = pre.scrollHeight;
  if (running) setTimeout(followRun, 2000);
}
async function ciChange(run, action) {
  try {
    await postJSON("/admin/ci", { action, run });
    say(
      action === "delete"
        ? "Queued. The builder deletes the run after any build in progress."
        : "Queued. The builder applies the change when it is free.",
      true,
      git.note,
    );
    if (action === "delete") {
      ciViewing = null;
      noteEl("ci-run-view").hidden = true;
    }
    setTimeout(loadCi, 1500);
  } catch (err) {
    say(err.message, false, git.note);
  }
}
const ciKeep = AW.form(document.getElementById("ci-keep-form"), {
  label: "Runs kept",
  save: async (c) => {
    const out = await postJSON("/admin/ci", {
      action: "settings",
      keep_runs: Number(c.keep),
    });
    setTimeout(() => renderCi(out));
    return "Saved.";
  },
});

// "Set up builds", in a repository's Manage: what a script gets, and templates to start from.
function buildSetup() {
  if (!ciData || !ciData.templates) return null;
  return el(
    "details",
    { className: "ci-setup" },
    el("summary", {
      textContent: "Set up builds: script environment and templates",
    }),
    el("p", {
      className: "setting-desc",
      textContent: `Put a ${ciData.script} at the top of the repository. Each push to a branch runs it with bash in a fresh clone of that commit.`,
    }),
    el(
      "dl",
      { className: "ci-env" },
      ...Object.entries(ciData.env || {}).flatMap(([k, v]) => [
        el("dt", {}, el("code", { textContent: k })),
        el("dd", { textContent: v }),
      ]),
    ),
    ...Object.values(ciData.templates).map((t) => {
      const pre = el("pre", { className: "admin-log", textContent: t.script });
      return el(
        "div",
        { className: "ci-template" },
        el("strong", { textContent: t.title }),
        actionButton(
          "Copy",
          () =>
            navigator.clipboard?.writeText(t.script).then(
              () =>
                say(
                  `Copied the ${t.title} template: save it as ${ciData.script}.`,
                  true,
                  git.note,
                ),
              () => say("Select the text and copy it.", false, git.note),
            ),
          { className: "small" },
        ),
        pre,
      );
    }),
  );
}

async function loadCi() {
  try {
    renderCi(await getJSON("/admin/ci"));
  } catch (_) {
    ciAbout.textContent = "Could not read the builds.";
  }
}
loadCi();

// --- firmware for the web flasher (firmware.py) ---------------------------------------------
// The board picker lists what the newest kept release offers; until a first check has run,
// there is nothing to pick yet, and Check now fetches the list.
const fw = {
  form: document.getElementById("fw-form"),
  status: noteEl("fw-status"),
  note: noteEl("fw-note"),
  boards: noteEl("fw-boards"),
  filter: noteEl("fw-filter"),
  kept: noteEl("fw-kept"),
  source: noteEl("fw-source"),
  cacheForm: document.getElementById("ci-cache-form"),
  cacheStatus: noteEl("ci-cache-status"),
  cacheNote: noteEl("ci-cache-note"),
};
// The firmware's source, as a mirror: what the Mirror the source button adds.
const FW_SOURCE_MIRROR = {
  upstream: "https://github.com/meshtastic/firmware",
  name: "meshtastic-firmware",
  area: "public",
  branches: ["master"],
  groups: [
    { kind: "release", keep: 2 },
    { kind: "prerelease", keep: 2 },
  ],
  history: "shallow",
  budget_mb: 2048,
  submodules: true,
};
let fwPoll = null;
let fwChosen = new Set();

function renderFirmware(data) {
  const cfg = data.settings;
  const st = data.status || {};
  const fields = fw.form.elements;
  fwSec.fill({
    enabled: cfg.enabled,
    configs: cfg.configs !== false,
    keep_alpha: cfg.keep_alpha,
    keep_beta: cfg.keep_beta,
    all_boards: cfg.boards === "all",
    boards: cfg.boards === "all" ? [] : [...cfg.boards].sort(),
  });
  const running = data.running
    ? data.progress
      ? ` Working: ${data.progress.name} (${size(data.progress.done)}${data.progress.total ? ` of ${size(data.progress.total)}` : ""}).`
      : " Working…"
    : "";
  fw.status.textContent =
    (st.last_check
      ? `Last checked ${st.last_check.replace("T", " ").replace("Z", " UTC")}: ${st.outcome || ""}.`
      : "Not checked yet.") +
    (st.error ? ` Error: ${errorSaid(st)}` : "") +
    running +
    (data.free_mb != null ? ` ${size(data.free_mb * 2 ** 20)} free.` : "");
  const q = fw.filter.value.trim().toLowerCase();
  const targets = (data.targets || []).filter(
    (t) => !q || t.board.includes(q) || t.platform.includes(q),
  );
  fwCache.fill({ cache: cfg.cache });
  fw.cacheStatus.replaceChildren(
    ...(st.cache
      ? [
          `Stored firmware: ${st.cache.version}, ${st.cache.mode === "native" ? "headless" : "complete"}, ${size(st.cache.bytes)}. `,
          actionButton(
            "Remove cache",
            async () => {
              try {
                fwData = await postJSON("/admin/firmware", {
                  action: "flush-cache",
                });
                renderFirmware(fwData);
                say(
                  "Cache removed. The next check or update downloads it again if the policy requires a cache.",
                  true,
                  fw.cacheNote,
                );
              } catch (err) {
                say(err.message, false, fw.cacheNote);
              }
            },
            { className: "small" },
          ),
        ]
      : [
          cfg.cache === "discard"
            ? ""
            : "No cache stored yet. The library downloads it on its schedule, or select Update now on the Firmware page.",
        ]),
  );
  fw.source.replaceChildren(
    ...(data.source
      ? [
          `Source: mirrored as `,
          el("a", {
            href: `${data.source.area === "public" ? "/git/" : "/git-private/"}${data.source.name}.git/`,
            textContent: `${data.source.name}.git`,
          }),
          " (Git → Mirrors). The pinout map reads the board configs from this mirror, without internet access.",
        ]
      : [
          "Source: not mirrored on this box. ",
          actionButton(
            "Mirror source code",
            async () => {
              try {
                await postJSON("/admin/git", {
                  action: "mirror-add",
                  mirror: FW_SOURCE_MIRROR,
                });
                say(
                  "Added meshtastic-firmware.git (master, 2 releases, 2 pre-releases, shallow, with submodules). The library downloads it on its schedule, or select Update mirror on Git → Mirrors.",
                  true,
                  fw.note,
                );
                loadFirmware();
              } catch (err) {
                say(err.message, false, fw.note);
              }
            },
            { className: "small" },
          ),
        ]),
  );
  fw.boards.hidden = fields.all_boards.checked;
  fw.boards.replaceChildren(
    ...(data.targets && data.targets.length
      ? targets.map((t) =>
          el(
            "label",
            { className: "inline" },
            el("input", {
              type: "checkbox",
              dataset: { board: t.board },
              checked: fwChosen.has(t.board),
              onchange: (event) => {
                if (event.target.checked) fwChosen.add(t.board);
                else fwChosen.delete(t.board);
                fwSec.set("boards", [...fwChosen].sort());
              },
            }),
            ` ${t.board} `,
            el("span", { className: "setting-desc", textContent: t.platform }),
          ),
        )
      : [
          el("p", {
            className: "setting-desc",
            textContent:
              "No board list available. Enable firmware downloads, select Save, then select Check now.",
          }),
        ]),
  );
  const versions = Object.entries(st.versions || {});
  fw.kept.replaceChildren(
    ...(versions.length
      ? versions.map(([v, info]) =>
          el(
            "div",
            { className: "admin-item" },
            el(
              "span",
              {},
              el("strong", { textContent: `${v} (${info.channel})` }),
              el("span", {
                className: "setting-desc",
                textContent:
                  `${info.boards.length} board${info.boards.length === 1 ? "" : "s"}, ` +
                  `${info.files} files, ${size(info.bytes)}` +
                  (info.missing && info.missing.length
                    ? `. Boards not in this release: ${info.missing.join(", ")}`
                    : "") +
                  (info.unavailable && info.unavailable.length
                    ? `. Omitted because a file is missing from the release: ${info.unavailable.map((entry) => entry.board).join(", ")}`
                    : "") +
                  (st.cache && st.cache.version === v
                    ? `. Build cache (${st.cache.mode}): ${size(st.cache.bytes)}`
                    : "") +
                  ".",
              }),
            ),
          ),
        )
      : [
          el("p", {
            className: "setting-desc",
            textContent: "No stored firmware yet.",
          }),
        ]),
  );
  clearTimeout(fwPoll);
  if (data.running) fwPoll = setTimeout(loadFirmware, 2000);
}

let fwData = null;
async function loadFirmware() {
  try {
    fwData = await getJSON("/admin/firmware");
    renderFirmware(fwData);
  } catch (_) {
    fw.status.textContent = "Could not read the firmware settings.";
  }
}
fw.filter.addEventListener("input", () => fwData && renderFirmware(fwData));
// The boards ticked are kept in fwChosen (the list is drawn again as the filter changes), not in a field: the
// section's extra value, drawn back into the list on Discard.
const fwSec = AW.form(fw.form, {
  label: "Firmware",
  extra: {
    boards: {
      set: (v) => {
        fwChosen = new Set(v || []);
        fw.boards
          .querySelectorAll("input[type=checkbox]")
          .forEach((i) => {
            i.checked = fwChosen.has(i.dataset.board);
          });
      },
    },
  },
  save: async (c, v) => {
    fwData = await postJSON("/admin/firmware", {
      action: "settings",
      enabled: v.enabled,
      configs: v.configs,
      keep_alpha: Number(v.keep_alpha),
      keep_beta: Number(v.keep_beta),
      boards: v.all_boards ? "all" : v.boards,
    });
    setTimeout(() => renderFirmware(fwData));
    return "Saved. Select Update now to download the missing files. The schedule handles the rest.";
  },
});
const fwCache = AW.form(fw.cacheForm, {
  label: "Build cache",
  save: async (c) => {
    fwData = await postJSON("/admin/firmware", {
      action: "settings",
      cache: c.cache,
    });
    setTimeout(() => renderFirmware(fwData));
    return "Saved. The library downloads or removes the cache on its schedule. Update now on the Firmware page does it now.";
  },
});
for (const [id, action] of [
  ["fw-check", "check"],
  ["fw-update", "update"],
]) {
  document.getElementById(id).addEventListener("click", async () => {
    try {
      fwData = await postJSON("/admin/firmware", { action });
      renderFirmware(fwData);
      say("", true, fw.note);
    } catch (err) {
      say(err.message, false, fw.note);
    }
  });
}
loadFirmware();

// --- toolkits (toolkits.py, root's kits.py): a card per kit, Kiwix's library as the model --------
// Each action is a request for the root helper; the page shows "Working…" until its answer is in.
const kitsEl = {
  summary: noteEl("kits-summary"),
  note: noteEl("kits-note"),
  grid: noteEl("kits-grid"),
  problems: noteEl("kits-problems"),
  budget: document.getElementById("kits-budget"),
  add: document.getElementById("kits-add"),
  addTitle: noteEl("kits-form-title"),
  addCancel: document.getElementById("kits-add-cancel"),
  custom: document.getElementById("kits-custom"),
};
const kitsHome = document.getElementById("kits-custom"); // where the form waits while its card is closed
const kitsFormNodes = [...kitsHome.childNodes];
const kitsFormHome = () => {
  if (
    !kitsEl.add.isConnected ||
    (!kitsHome.contains(kitsEl.add) && !kitsEl.grid.contains(kitsEl.add))
  )
    kitsHome.append(...kitsFormNodes);
};
// Its card closed (✕, Esc, another card): the form goes back to wait.
new MutationObserver(kitsFormHome).observe(kitsEl.grid, {
  childList: true,
  subtree: true,
});
const REMOVE_AFTER = [
  [1, "an hour"],
  [4, "4 hours"],
  [24, "a day"],
  [168, "a week"],
  [720, "a month"],
  [null, "never"],
];
const dayOf = (unix) =>
  unix ? new Date(unix * 1000).toISOString().slice(0, 10) : "";
const kitsWaiting = new Set();
let kitsData = null;
let kitsPoll = null;

function hoursPicker(value) {
  return el(
    "select",
    { className: "kit-hours" },
    ...REMOVE_AFTER.map(([v, label]) =>
      el("option", {
        value: v == null ? "never" : String(v),
        textContent: label,
        selected: v === value,
      }),
    ),
  );
}
const pickedHours = (sel) => (sel.value === "never" ? null : Number(sel.value));

// True once the hub or the root helper has it; false when cancelled or refused (said in the note).
async function kitAct(body, quiet) {
  try {
    const data = await postJSON("/admin/kits", body);
    if (data.id) kitsWaiting.add(data.id);
    if (!quiet)
      say(
        data.id
          ? "Queued. The root helper works on it now. A download takes a few minutes."
          : "Saved.",
        true,
        kitsEl.note,
      );
    renderKits(data);
    return true;
  } catch (err) {
    say(err.message, false, kitsEl.note);
    return false;
  }
}

function renderKits(data) {
  data = AW.localise(data); // the Security doctor's flags (flag_details) are messages
  kitsData = data;
  const st = data.status || {};
  const cfg = data.settings;
  const kits = Object.values(data.kits || {});
  // Answers to what this page asked: shown, and the waiting ends.
  for (const result of data.results || []) {
    if (kitsWaiting.has(result.id)) {
      kitsWaiting.delete(result.id);
      say(resultSaid(result), result.ok, kitsEl.note);
    }
  }
  const cached = kits.filter((k) => ((st.kits || {})[k.id] || {}).cached);
  const newest = Math.max(
    0,
    ...cached.map((k) => st.kits[k.id].cached.fetched),
  );
  const feed = (data.feeds || {}).debsecan;
  kitsEl.summary.textContent =
    (st.at
      ? `${cached.length} of ${kits.length} toolkits cached, ${size(st.pool_bytes || 0)} of ${cfg.budget_mb} MB` +
        (newest ? `. Newest download: ${dayOf(newest)}` : "") +
        ". Cached toolkits install without internet access."
      : "Not checked yet.") +
    (feed && feed.fetched ? ` debsecan's data: ${dayOf(feed.fetched)}.` : "") +
    (kitsWaiting.size ? " Working…" : "");
  kitsBudget.fill({
    budget: cfg.budget_mb,
    previous: cfg.previous_days == null ? "" : cfg.previous_days,
  });
  kitsEl.problems.replaceChildren(
    ...(st.problems || []).map((problem) =>
      checkItem(
        "problem",
        "Cache integrity check failed",
        problem,
        "Refresh the toolkit cache while online. The box does not install from a cache that fails its integrity check.",
      ),
    ),
  );
  // A card per kit, its details in a drawer inside the card; not redrawn
  // under an open drawer with changes not saved.
  const open = kitsEl.grid.querySelector(".aw-card.open");
  if (!(open && open._dirty && open._dirty())) {
    const openId = open && open.dataset.awId;
    kitsHome.append(...kitsFormNodes);
    kitsEl.grid.replaceChildren(
      AW.cards(
        [
          ...kits.map((k) => kitItem(k, st)),
          {
            id: "kit-custom",
            title: "+ Custom toolkit",
            summary: "Your own packages, managed by the librarian",
            badges: [],
          },
        ],
        {
          id: "kits-cards",
          body: (it, setDirty) =>
            it.id === "kit-custom"
              ? customBody()
              : kitBody(it.kit, st, cfg, setDirty),
          save: (it) => kitSave(it),
        },
      ),
    );
    const again =
      openId &&
      kitsEl.grid.querySelector(
        `.aw-card[data-aw-id="${openId}"] .aw-card-head`,
      );
    if (again) again.click();
  }
  clearTimeout(kitsPoll);
  if (kitsWaiting.size) kitsPoll = setTimeout(loadKits, 3000);
}

// A kit's card: its name, what it is for and its state in words.
function kitItem(k, st) {
  const kitState = (st.kits || {})[k.id] || {},
    c = kitState.cached,
    inst = (st.installed || {})[k.id];
  const left =
    inst && inst.remove_at
      ? Math.max(0, Math.round((inst.remove_at - Date.now() / 1000) / 3600))
      : null;
  const badges = [
    c ? `cached ${dayOf(c.fetched)}, ${size(c.bytes)}` : "not cached",
    kitState.previous
      ? `older set kept for rollback${kitState.previous.extra_bytes ? ` (${size(kitState.previous.extra_bytes)})` : ""}`
      : null,
    k.owner ? "yours" : null,
    ((kitsData || {}).flag_details || {})[k.id]
      ? "flagged by the doctor"
      : null,
    (k.extra || []).length ? `+${k.extra.length} extra` : null,
    inst
      ? inst.remove_at
        ? `installed: removed in ${left < 1 ? "under an hour" : `${left} h`}`
        : "installed, kept"
      : null,
  ].filter(Boolean);
  return {
    id: "kit-" + k.id,
    title: k.title,
    summary: k.summary || "",
    badges,
    kit: k,
  };
}
// Its drawer (steps 2, 3): settings held until Save (keep current, removed after by default, extra
// tools); jobs at once (install, remove, keep longer, refresh, roll back, edit, delete), said so.
const kitDrafts = new Map();
function kitBody(k, st, cfg, setDirty) {
  const kitState = (st.kits || {})[k.id] || {},
    c = kitState.cached,
    inst = (st.installed || {})[k.id],
    mine = cfg.kits[k.id];
  const draft = {
    keep_current: mine.keep_current,
    remove_after: mine.remove_after,
    extra: (k.extra || []).join(" "),
  };
  kitDrafts.set(k.id, draft);
  const jobs = el("div", { className: "kit-jobs" });
  const jobRow = (...kids) =>
    el("p", { className: "library-buttons" }, ...kids);
  if (inst) {
    const keep = hoursPicker(mine.remove_after);
    jobs.append(
      jobRow(
        actionButton(
          "Remove toolkit",
          () => kitAct({ action: "remove", kit: k.id }),
          { className: "small" },
        ),
        el("span", {
          className: "setting-desc",
          textContent: "Keep installed for ",
        }),
        keep,
        actionButton(
          "Extend installation period",
          () => kitAct({ action: "keep", kit: k.id, hours: pickedHours(keep) }),
          { className: "small" },
        ),
      ),
    );
    if (inst.upgraded && inst.upgraded.length)
      jobs.append(
        el("p", {
          className: "setting-desc",
          textContent: `The installation also upgraded these packages. They remain upgraded after removal: ${inst.upgraded.join(", ")}.`,
        }),
      );
  } else {
    // Install… first shows the consent step: what the kit can do, and when it goes again.
    const consent = () => {
      const hours = hoursPicker(mine.remove_after);
      const flag = ((kitsData || {}).flag_details || {})[k.id];
      return el(
        "div",
        { className: "kit-consent" },
        el("p", { textContent: k.consent }),
        flag
          ? el("p", {
              className: "kit-flag",
              textContent: `The Security doctor flags the cache of this toolkit: ${flag} Refresh the cache first if the box has internet access.`,
            })
          : null,
        jobRow(
          el("span", {
            className: "setting-desc",
            textContent: "Remove after ",
          }),
          hours,
          actionButton(
            "Install toolkit",
            () =>
              kitAct({
                action: "install",
                kit: k.id,
                hours: pickedHours(hours),
              }),
            { className: "small" },
          ),
          actionButton(
            "Cancel",
            (event) => event.target.closest(".kit-consent").replaceWith(installRow()),
            { className: "small" },
          ),
        ),
      );
    };
    const installRow = () =>
      jobRow(
        actionButton(
          "Install toolkit…",
          (event) => event.target.closest("p").replaceWith(consent()),
          {
            className: "small",
            disabled: !c,
            title: c
              ? ""
              : "Not cached yet. Refresh the cache while the box has internet access.",
          },
        ),
        c
          ? null
          : el("span", {
              className: "setting-desc",
              textContent:
                "Refresh the cache while the box has internet access.",
            }),
      );
    jobs.append(installRow());
  }
  // The set before this one, kept for Roll back: what it holds beyond the current set. The Security doctor judges the
  // cache by its newest versions only, so these older copies are not flagged.
  if (kitState.previous) {
    const older = kitState.previous.older || [];
    jobs.append(
      el("p", {
        className: "setting-desc",
        textContent:
          `Kept for rollback: the set downloaded ${dayOf(kitState.previous.fetched)}` +
          (older.length
            ? `, with older versions of ${older.length} package${older.length === 1 ? "" : "s"} (${older.slice(0, 6).join(", ")}${older.length > 6 ? ", …" : ""})`
            : ", the same versions as now") +
          (kitState.previous.extra_bytes
            ? `, ${size(kitState.previous.extra_bytes)} more in the cache`
            : "") +
          ". The Security doctor checks only the newest version of each package." +
          (cfg.previous_days && c && c.fetched
            ? ` The box removes it on ${dayOf(c.fetched + cfg.previous_days * 86400)} (Older sets kept for Roll back).`
            : ""),
      }),
    );
  }
  jobs.append(
    jobRow(
      actionButton(
        "Refresh cache",
        () => kitAct({ action: "fetch", kit: k.id }),
        { className: "small" },
      ),
      kitState.previous
        ? actionButton(
            `Roll back to ${dayOf(kitState.previous.fetched)}`,
            () => {
              kitAct({ action: "rollback", kit: k.id });
            },
            { className: "small" },
          )
        : null,
      kitState.previous
        ? actionButton(
            `Remove older set${kitState.previous.extra_bytes ? ` (frees ${size(kitState.previous.extra_bytes)})` : ""}`,
            async (event) => {
              if (
                await AW.confirmHere(
                  `Remove the older set of ${k.title}, downloaded ${dayOf(kitState.previous.fetched)}? This frees ${size(kitState.previous.extra_bytes || 0)}. Roll back is not possible until a refresh brings new versions.`, { anchor: event.currentTarget })
              )
                kitAct({ action: "drop-previous", kit: k.id });
            },
            { className: "small" },
          )
        : null,
      k.owner
        ? actionButton("Edit", () => editKit(k, mine), { className: "small" })
        : null,
      k.owner
        ? actionButton(
            "Delete toolkit",
            async (event) => {
              if (await AW.confirmHere(`Delete ${k.title} and its cache?`, { anchor: event.currentTarget }))
                kitAct({ action: "undefine", kit: k.id });
            },
            {
              className: "small",
              disabled: !!inst,
              title: inst ? "Remove the toolkit first" : "",
            },
          )
        : null,
    ),
  );
  // Save is on only while the drawer differs from what is saved: changing a setting back turns it off again.
  const saved = JSON.stringify(draft);
  const changed = () => setDirty(JSON.stringify(draft) !== saved);
  const keepBtn = el("button", {
    type: "button",
    className: "chip-btn" + (draft.keep_current ? " active" : ""),
    textContent: `Automatic cache refresh: ${draft.keep_current ? "On" : "Off"}`,
  });
  keepBtn.setAttribute("aria-pressed", String(draft.keep_current));
  keepBtn.addEventListener("click", () => {
    draft.keep_current = !draft.keep_current;
    keepBtn.classList.toggle("active", draft.keep_current);
    keepBtn.setAttribute("aria-pressed", String(draft.keep_current));
    keepBtn.textContent = `Automatic cache refresh: ${draft.keep_current ? "On" : "Off"}`;
    changed();
  });
  const def = hoursPicker(mine.remove_after);
  def.addEventListener("change", () => {
    draft.remove_after = pickedHours(def);
    changed();
  });
  const extra = k.owner
    ? null
    : el("input", {
        className: "kit-extra",
        value: draft.extra,
        placeholder: "e.g. ltrace gdbserver",
      });
  if (extra)
    extra.addEventListener("input", () => {
      draft.extra = extra.value;
      changed();
    });
  const field = (label, ...kids) =>
    el(
      "div",
      { className: "aw-field" },
      el("span", { className: "aw-label", textContent: label }),
      ...kids,
    );
  return [
    el("p", {
      className: "setting-desc",
      textContent: "These actions run immediately:",
    }),
    jobs,
    field(
      "Automatic cache refresh",
      keepBtn,
      el("span", {
        className: "setting-desc",
        textContent: "The library refreshes the cache on its schedule.",
      }),
    ),
    field("Default removal period", def),
    extra ? field("Additional packages", extra) : null,
    el(
      "ul",
      { className: "kit-notes" },
      ...(k.notes || []).map((n) => el("li", { textContent: n })),
    ),
    c
      ? el("p", {
          className: "setting-desc",
          textContent:
            `${c.packages} packages: ` +
            Object.entries(c.versions || {})
              .map(([n, v]) => `${n} ${v}`)
              .join(", ") +
            (c.on_box && c.on_box.length
              ? `. Already on the box: ${c.on_box.join(", ")}.`
              : "."),
        })
      : null,
    c && c.left_out && c.left_out.length
      ? el("p", {
          className: "setting-desc",
          textContent: `Omitted on this ${c.arch || ""} board because they need a 64-bit board: ${c.left_out.join(", ")}.`,
        })
      : null,
    (k.git || []).length
      ? el("p", {
          className: "setting-desc",
          textContent: `From git: ${k.git.map((g) => `${g.name} (${g.upstream.replace(/^https:\/\//, "")}, a mirror)`).join(", ")}.`,
        })
      : null,
  ];
}
async function kitSave(it) {
  const k = it.kit,
    draft = kitDrafts.get(k.id),
    mine = kitsData.settings.kits[k.id];
  const ch = {};
  if (draft.keep_current !== mine.keep_current) ch.keep_current = draft.keep_current;
  if (draft.remove_after !== mine.remove_after) ch.remove_after = draft.remove_after;
  const extra = draft.extra
    .trim()
    .split(/[\s,]+/)
    .filter(Boolean);
  if (
    Object.keys(ch).length &&
    !(await kitAct({ action: "settings", kits: { [k.id]: ch } }))
  )
    return false;
  if (
    !k.owner &&
    extra.join(" ") !== (k.extra || []).join(" ") &&
    !(await kitAct({ action: "extra", kit: k.id, packages: extra }))
  )
    return false;
  return true;
}
// The "+ Custom toolkit" card: the form, moved into its drawer while it is open.
function customBody() {
  const box = el("div", { className: "kit-custom" }, ...kitsFormNodes);
  return [box];
}

function editKit(k, mine) {
  const fields = kitsEl.add.elements;
  fields.id.value = k.id;
  fields.title.value = k.title;
  fields.summary.value = k.summary || "";
  fields.packages.value = k.packages.join(" ");
  fields.hours.value =
    mine.remove_after == null ? "never" : String(mine.remove_after);
  kitsEl.addTitle.textContent = `Change ${k.title}`;
  kitsEl.addCancel.hidden = false;
  kitsEl.add.querySelector("button[type=submit]").textContent = "Update toolkit";
  // In the custom card's drawer: open it if it isn't.
  const card = kitsEl.grid.querySelector('.aw-card[data-aw-id="kit-custom"]');
  if (card && !card.classList.contains("open"))
    card.querySelector(".aw-card-head").click();
  kitsEl.add.scrollIntoView?.({ block: "center" });
}
function resetKitForm() {
  kitsEl.add.reset();
  kitsEl.add.elements.id.value = "";
  kitsEl.addTitle.textContent = "Add a toolkit of your own";
  kitsEl.addCancel.hidden = true;
  kitsEl.add.querySelector("button[type=submit]").textContent = "Add toolkit";
}
kitsEl.add.addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = kitsEl.add.elements;
  const kit = {
    title: fields.title.value.trim(),
    summary: fields.summary.value.trim(),
    packages: fields.packages.value
      .trim()
      .split(/[\s,]+/)
      .filter(Boolean),
    remove_after_hours:
      fields.hours.value === "never" ? null : Number(fields.hours.value),
  };
  if (fields.id.value) kit.id = fields.id.value;
  kitAct({ action: "define", kit });
  resetKitForm();
});
kitsEl.addCancel.addEventListener("click", resetKitForm);

// Kits by USB drive (usbstick.py, kits.py): the root helper's last stick scan, Import for each kit
// found for this box's architecture, and a cached kit to copy onto each drive.
let kitsUsbWaiting = null;
let kitsUsbAsked = null; // what the waiting request asked: an import refused for the clock is offered again below
async function loadKitsUsb() {
  let data;
  try {
    data = await getJSON("/admin/usb");
  } catch (_) {
    return;
  }
  const note = noteEl("kits-usb-note");
  if (kitsUsbWaiting) {
    const done = (data.results || []).find((result) => result.id === kitsUsbWaiting);
    if (done) {
      kitsUsbWaiting = null;
      say(resultSaid(done), done.ok, note);
      // The clock cannot date the release: the owner may import it anyway, knowing its freshness goes unchecked.
      // Root decides whether the hub may give that approval (the security policy) and says so if not.
      const asked = kitsUsbAsked;
      if (!done.ok && done.msg && done.msg.id === "kits.usb.time-untrusted" && asked && asked.action === "kit-import")
        note.append(
          " ",
          el("button", {
            type: "button",
            className: "action-btn small",
            textContent: "Import without a freshness check",
            onclick: () =>
              kitsUsbAsk(
                { ...asked, freshness: "unknown-time" },
                "Import this toolkit without checking that its packages are current? Their signatures and hashes " +
                  "are still checked, but a USB drive could carry an older signed set with problems fixed since. " +
                  "Set the clock on the Clock page first if you can.",
              ),
          }),
        );
      loadKits();
    }
  }
  const progress = data.progress;
  const busy = !!kitsUsbWaiting || !!progress || data.pending > 0;
  document.getElementById("kits-usb-scan").disabled = busy;
  const st = (kitsData || {}).status || {};
  const cached = Object.entries(st.kits || {}).filter(([, v]) => v.cached);
  const here = (cached[0] && cached[0][1].cached.arch) || "";
  const devs = (data.scan && data.scan.devices) || [];
  noteEl("kits-usb").replaceChildren(
    ...(data.scan && !devs.length
      ? [
          el("p", {
            className: "setting-desc",
            textContent:
              "No USB drive found. Connect a USB drive. Select Scan USB drives.",
          }),
        ]
      : []),
    ...devs.map((device) => {
      const pick = el(
        "select",
        { disabled: busy || !cached.length },
        ...cached.map(([kid]) =>
          el("option", {
            value: kid,
            textContent: ((kitsData.kits || {})[kid] || {}).title || kid,
          }),
        ),
      );
      return el(
        "div",
        { className: "setting library-source" },
        el(
          "span",
          {},
          el("span", {
            className: "setting-name",
            textContent: `${device.label || device.name} (${device.fstype}, ${size(Number(device.size))})`,
          }),
          device.error
            ? el("span", {
                className: "setting-desc bad",
                textContent: errorSaid(device),
              })
            : null,
          ...((device.kits || []).length
            ? device.kits.map((k) =>
                el(
                  "span",
                  { className: "usb-book" },
                  el("span", {
                    className: "setting-desc",
                    textContent:
                      `${k.title}: ${k.packages} packages, ${size(k.bytes)}, ${k.arch}` +
                      (here && k.arch !== here
                        ? ` (Architecture mismatch: this board is ${here})`
                        : ""),
                  }),
                  !here || k.arch === here
                    ? el("button", {
                        type: "button",
                        className: "action-btn small",
                        textContent: "Import toolkit",
                        disabled: busy,
                        onclick: () =>
                          kitsUsbAsk(
                            {
                              action: "kit-import",
                              device: device.name,
                              kit: k.kit,
                            },
                          ),
                      })
                    : null,
                ),
              )
            : [
                el("span", {
                  className: "setting-desc",
                  textContent: "No toolkits on this drive.",
                }),
              ]),
          cached.length
            ? el(
                "span",
                { className: "library-buttons" },
                pick,
                el("button", {
                  type: "button",
                  className: "action-btn",
                  textContent: "Export toolkit to USB drive",
                  disabled: busy,
                  onclick: () =>
                    kitsUsbAsk(
                      { action: "kit-export", device: device.name, kit: pick.value },
                    ),
                }),
              )
            : null,
        ),
      );
    }),
    // Nothing in progress: nothing (a null here showed as the word "null" under a listed stick).
    ...(progress
      ? [
          el("p", {
            className: "setting-desc",
            textContent: `${said(progress.label)}${progress.total ? `: ${size(progress.done)} of ${size(progress.total)}` : "…"}`,
          }),
        ]
      : []),
  );
  if (busy) setTimeout(loadKitsUsb, 1500);
}
// confirmText: only where the box cannot go on without a decision (an import with the clock unknown).
async function kitsUsbAsk(body, confirmText) {
  if (confirmText && !(await AW.confirmHere(confirmText))) return;
  kitsUsbAsked = body;
  try {
    kitsUsbWaiting = (await postJSON("/admin/usb", body)).id;
    say("Working…", true, noteEl("kits-usb-note"));
    loadKitsUsb();
  } catch (err) {
    say(err.message, false, noteEl("kits-usb-note"));
  }
}
document
  .getElementById("kits-usb-scan")
  .addEventListener("click", () => kitsUsbAsk({ action: "scan" }));

async function loadKits() {
  try {
    const data = await getJSON("/admin/kits");
    // Root writes what is cached and installed; on a first visit, ask it to.
    if (!(data.status || {}).at && !kitsWaiting.size) {
      kitAct({ action: "status" }, true);
      return;
    }
    renderKits(data);
  } catch (_) {
    kitsEl.summary.textContent = "Could not read the toolkits.";
  }
}
// The budget waits for Save; Discard puts back the saved one.
// The cache's budget and how long an older set is kept: a section of the toolkits' page.
const kitsBudget = AW.form(kitsEl.budget, {
  label: "Toolkits' cache budget",
  save: async (c, v) => {
    renderKits(
      await postJSON("/admin/kits", {
        action: "settings",
        budget_mb: Number(v.budget),
        previous_days: v.previous === "" ? null : Number(v.previous),
      }),
    );
  },
});
document
  .getElementById("kits-refresh-all")
  .addEventListener("click", async () => {
    for (const k of Object.keys((kitsData || {}).kits || {}))
      await kitAct({ action: "fetch", kit: k }, true);
    say(
      "Queued a refresh for each toolkit. The root helper refreshes them one after another. Each takes several minutes.",
      true,
      kitsEl.note,
    );
  });
// Clear old rollback cache: every toolkit's older set at once (each kit's own Remove older set, for all).
document.getElementById("kits-drop-all").addEventListener("click", async (event) => {
  if (
    await AW.confirmHere(
      "Remove the older set of every toolkit? This frees their cache space. Roll back is not possible until a refresh brings new versions.", { anchor: event.currentTarget })
  ) {
    kitAct({ action: "drop-all-previous" });
  }
});
window.addEventListener("hashchange", () => {
  if (paneShown("toolkits")) {
    loadKits();
    loadKitsUsb();
  }
});
if (paneShown("toolkits")) {
  loadKits();
  loadKitsUsb();
}

// --- HTTPS (root/tls.py, /admin/tls) ----------------------------------------------------------------
document
  .getElementById("security-cards")
  .append(document.getElementById("security-https"));
const tlsEl = {
  state: document.getElementById("tls-state"),
  make: document.getElementById("tls-make"),
  again: document.getElementById("tls-again"),
  note: noteEl("tls-note"),
  box: document.getElementById("tls-box"),
  own: document.getElementById("tls-own-form"),
  plain: document.getElementById("tls-own-plain"),
  transport: document.getElementById("tls-transport"),
  adminHttp: document.getElementById("tls-admin-http"),
};
let tlsWaiting = null;
let tlsData = null;
let tlsOperation = null,
  tlsJob = null; // the section's save waiting for the root helper (AW.job)
// HTTPS's two choices, and the security policy: a section each, saved by the root helper (renderTls says when).
const tlsLocked = () =>
  !!tlsData && (tlsData.policy || {}).mode === "console";
const tlsSave = (body) => {
  const j = AW.job();
  tlsJob = j;
  tlsAct(body);
  return j;
};
const tlsSec = AW.section({
  id: "https-settings",
  label: "HTTPS",
  saved: null,
  why: (c, v) =>
    tlsLocked()
      ? "Security policy is locked. Changes require the console or SSH."
      : v.transport === "http" && v.admin_http === "block"
        ? "Closing the administration pages over HTTP needs HTTPS on."
        : null,
  warn: (c, v) =>
    v.transport === "https"
      ? "Captive-portal discovery over HTTP stops."
      : null,
  save: (c, v) => tlsSave({ action: "settings", ...v }),
  onChange: () => drawTlsChoices(),
});
const policySec = AW.section({
  id: "security-policy",
  label: "Security policy",
  saved: null,
  warn: (c) =>
    c.mode === "console"
      ? "After Save, only a root-capable SSH session or the console can change security policy. The hub cannot undo it."
      : null,
  why: () =>
    tlsLocked()
      ? "Security policy is locked. Changes require the console or SSH."
      : null,
  save: (c) => tlsSave({ action: "policy", mode: c.mode, confirmed: true }),
  onChange: () => drawTlsChoices(),
});
document.getElementById("tls-foot").append(tlsSec.foot, tlsSec.reason);
document.getElementById("policy-foot").append(policySec.foot, policySec.reason);
const TLS_TRANSPORT = [
  ["http", "HTTPS disabled"],
  ["both", "HTTP and HTTPS"],
  ["discovery", "HTTPS with HTTP discovery/redirect only"],
  ["https", "HTTP disabled entirely"],
];
const TLS_ADMIN_HTTP = [
  ["allow", "Fully open"],
  ["warning", "Open with warning"],
  ["block", "Closed"],
];
const ROOT_POLICY = [
  ["hub", "Allow changes from hub admin"],
  ["console", "Require console/SSH for changes"],
];
function tlsChoices(box, choices, selected, disabled, choose) {
  box.replaceChildren(
    ...choices.map(([value, label]) => {
      const button = el("button", {
        type: "button",
        className: "chip" + (selected === value ? " selected" : ""),
        textContent: label,
        disabled: disabled(value),
        role: "radio",
        dataset: { value },
        onclick: () => choose(value),
      });
      button.setAttribute("aria-checked", String(selected === value));
      if (button.disabled)
        button.title =
          "This choice requires HTTPS, a certificate, or console/SSH policy access.";
      return button;
    }),
  );
}
function drawTlsChoices() {
  if (!tlsData) return;
  const busy = !!tlsWaiting || tlsData.pending > 0,
    locked = (tlsData.policy || {}).mode === "console";
  const secure = location.protocol === "https:";
  const applied = {
    transport: tlsData.transport || (tlsData.on ? "both" : "http"),
    admin_http: tlsData.admin_http || (tlsData.admin_only ? "block" : "allow"),
  };
  // The applied settings, when they change: a choice not saved yet kept over them.
  const appliedMode = (tlsData.policy || {}).mode || "hub";
  if (
    !tlsSec.saving() &&
    (!tlsSec.loaded() ||
      tlsSec.savedValue("transport") !== applied.transport ||
      tlsSec.savedValue("admin_http") !== applied.admin_http)
  )
    return tlsSec.update(applied);
  if (
    !policySec.saving() &&
    (!policySec.loaded() || policySec.savedValue("mode") !== appliedMode)
  )
    return policySec.update({ mode: appliedMode });
  const draft = tlsSec.values(),
    mode = policySec.get("mode");
  tlsChoices(
    tlsEl.transport,
    TLS_TRANSPORT,
    draft.transport,
    (value) =>
      busy ||
      locked ||
      (value !== "http" && !tlsData.set_up) ||
      (!secure && ["discovery", "https"].includes(value)),
    (value) => tlsSec.set("transport", value),
  );
  tlsChoices(
    tlsEl.adminHttp,
    TLS_ADMIN_HTTP,
    draft.admin_http,
    (value) =>
      busy ||
      locked ||
      (value === "block" && (!secure || draft.transport === "http")) ||
      ["discovery", "https"].includes(draft.transport),
    (value) => tlsSec.set("admin_http", value),
  );
  tlsChoices(
    document.getElementById("policy-choices"),
    ROOT_POLICY,
    mode,
    () => busy || locked,
    (value) => policySec.set("mode", value),
  );
  const changed = tlsSec.dirty();
  document.getElementById("policy-state").textContent = locked
    ? "Applied: console/SSH only. The hub cannot change security policy."
    : "Applied: hub administrators may change policy. This trusts the hub to convey administrator approval.";
  document.getElementById("tls-session-note").textContent = locked
    ? "Security policy is locked. Changes require the console or SSH."
    : !secure
      ? "Open this page over HTTPS to enable either HTTPS-only transport choice or close administrator access over HTTP."
      : "Disabling HTTP entirely also disables HTTP captive-portal discovery.";
  document.getElementById("tls-draft-note").textContent = changed
    ? "Changed, not saved."
    : "The applied settings are shown.";
}
async function loadTls() {
  try {
    renderTls(await getJSON("/admin/tls"));
  } catch (_) {
    tlsEl.state.textContent = "Could not read the HTTPS state.";
  }
}
function renderTls(data) {
  tlsData = data;
  if (tlsWaiting) {
    const done = (data.results || []).find((result) => result.id === tlsWaiting);
    if (done) {
      say(
        resultSaid(done),
        done.ok,
        tlsOperation === "policy" ? noteEl("policy-note") : tlsEl.note,
      );
      if (tlsJob) tlsJob.done(done.ok, resultSaid(done));
      tlsJob = null;
      if (done.ok) {
        document
          .getElementById(
            tlsOperation === "policy" ? "security-policy" : "security-https",
          )
          .dispatchEvent(new Event("settings-saved"));
      }
      tlsWaiting = null;
    }
  }
  const busy = !!tlsWaiting || data.pending > 0;
  const c = data.cert || {};
  const day = (t) => new Date(t * 1000).toLocaleDateString();
  tlsEl.state.textContent = !data.set_up
    ? "No certificate yet. The box serves pages over plain HTTP only."
    : `${data.on ? `HTTPS enabled: https://<this box>/ (port ${data.ports.main}) and the HTTPS address of each app.` : "HTTPS disabled. The box keeps the certificate. Pages are plain HTTP only."}` +
      ` The certificate covers ${[...(c.names || []), ...(c.addresses || [])].join(", ")}. Certificate expires ${c.expires ? day(c.expires) : "?"}` +
      `${c.own ? " (your own certificate: renew it before then)" : " (the box renews it before then)"}.${data.ca ? ` Certificate-authority fingerprint: ${data.ca.fingerprint}.` : ""}` +
      (data.outside && data.outside.length
        ? ` The box is now at ${data.outside.join(", ")}. These addresses are outside the names that its CA can certify. Make a new certificate authority for that network.`
        : "");
  tlsEl.state.classList.toggle("bad", !!(data.outside && data.outside.length));
  tlsEl.make.hidden = !!data.set_up;
  tlsEl.again.hidden = !data.set_up;
  tlsEl.box.hidden = !(c.own && data.ca);
  tlsEl.make.hidden = !!(data.set_up && data.ca);
  tlsEl.plain.hidden = location.protocol === "https:";
  // /admin over HTTPS only: offered while HTTPS is on, and turned on only from a page that came
  // over HTTPS (this device trusts the box, so it can't lock its owner out).
  [
    tlsEl.make,
    tlsEl.again,
    tlsEl.box,
    ...tlsEl.own.querySelectorAll("input, textarea, button"),
  ].forEach((button) => {
    button.disabled = busy || (data.policy || {}).mode === "console";
  });
  drawTlsChoices();
  if (busy) setTimeout(loadTls, 1500);
}
async function tlsAct(body) {
  try {
    const reply = await postJSON("/admin/tls", body);
    tlsWaiting = reply.id;
    tlsOperation = body.action;
    say(
      "Working…",
      true,
      body.action === "policy" ? noteEl("policy-note") : tlsEl.note,
    );
    loadTls();
  } catch (err) {
    if (tlsJob) {
      tlsJob.done(false, err.message);
      tlsJob = null;
    } else say(err.message, false, tlsEl.note);
  }
}
tlsEl.make.addEventListener("click", () => tlsAct({ action: "make" }));
tlsEl.box.addEventListener("click", () => tlsAct({ action: "box" }));
tlsEl.own.addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = tlsEl.own.elements;
  tlsAct({ action: "import", chain: fields.chain.value, key: fields.key.value });
  fields.key.value = ""; // not left in the page
});
tlsEl.again.addEventListener("click", async (event) => {
  if (
    await AW.confirmHere(
      "Make a new certificate authority? Every phone and computer that installed the old one must install the new one, or it shows a warning about the box.", { anchor: event.currentTarget })
  ) {
    tlsAct({ action: "make", again: true });
  }
});
window.addEventListener("hashchange", () => {
  if (paneShown("security")) loadTls();
});
if (paneShown("security")) loadTls();

// --- accounts (accounts.py, /admin/accounts) ---------------------------------------------------------
const acctEl = {
  settings: document.getElementById("accounts-settings"),
  note: noteEl("accounts-note"),
  counts: document.getElementById("accounts-counts"),
  make: document.getElementById("accounts-make"),
  code: document.getElementById("accounts-code"),
  list: document.getElementById("accounts-list"),
  login: document.getElementById("accounts-admin-login"),
  loginChoices: document.getElementById("accounts-admin-login-choices"),
};
let acctWaiting = null; // the root helper's request for the box's own login (an AW.job, with its id)
async function loadAccounts() {
  try {
    renderAccounts(await getJSON("/admin/accounts"));
  } catch (_) {
    acctEl.counts.textContent = "Could not read the accounts.";
  }
}
// The box's own login (for scripts): its reach, from the safest. A chip only selects; Save applies it,
// after the page has said what the change stops. The password only at the console.
const LOGIN_REACH = [
  [
    "off",
    "Script sign-in disabled",
    "Scripts cannot sign in with the box password. The administration pages open only for a signed-in administrator account.",
  ],
  [
    "local",
    "Local scripts only",
    "Scripts that run on the box (over SSH, or on its own timers) can use the box password. From any other device, the password does not work.",
  ],
  [
    "network",
    "Network scripts permitted",
    "Any device that has the password can use it. The password goes with every request. Over plain HTTP, anyone on the WiFi can read it, so use it over HTTPS.",
  ],
];
// The reach as a section: a chip only selects; Save applies it, after one question saying what the change stops.
let acctLast = null,
  acctAdmins = "";
const reachSec = AW.section({
  id: "script-sign-in",
  label: "Script sign-in",
  saved: null,
  // What the change stops is said beside the choices (reach-change).
  why: (c) =>
    reachChange(c.reach, acctAdmins).lockout
      ? "Create an administrator account first."
      : null,
  save: async (c) => {
    const j = AW.job();
    j.id = (
      await postJSON("/admin/accounts", {
        action: "admin-login",
        reach: c.reach,
      })
    ).id;
    acctWaiting = j;
    loadAccounts();
    return j;
  },
  onChange: () => acctLast && renderAccounts(acctLast),
});
// What saving a reach stops, said before it is saved; and whether it would leave no way into /admin from another device.
function reachChange(v, admins) {
  const accounts = admins
    ? ` The administrator accounts (${admins}) can still sign in from any device.`
    : "";
  const lockout = !admins;
  if (v === "off") {
    return {
      lockout,
      says:
        "Saving this disables the box password. Scripts that use it (for example curl -u admin) stop working, and the browser sign-in window for the administration pages stops accepting it." +
        (lockout
          ? " This box has no administrator account that has signed in over HTTPS, so nobody could open the administration pages. Create an administrator account and sign in with it over HTTPS first."
          : accounts),
    };
  }
  if (v === "local") {
    return {
      lockout,
      says:
        "Saving this limits the box password to the box itself. On every other device, including the one you are using now, scripts and the browser sign-in window stop accepting it." +
        (lockout
          ? " This box has no administrator account that has signed in over HTTPS, so no other device could open the administration pages. Create an administrator account and sign in with it over HTTPS first."
          : accounts),
    };
  }
  return {
    lockout: false,
    says: "Saving this lets the box password work from every device on the box's network. The password goes with every request. Over plain HTTP, anyone on the WiFi can read it, so use it over HTTPS.",
  };
}
function renderAccounts(data) {
  const al = data.admin_login || {
    on: true,
    reach: "network",
    password_set: true,
    https_admins: [],
    results: [],
  };
  acctLast = data;
  if (data.admin_login) drawPasswordStep(data.admin_login);
  if (acctWaiting) {
    const done = (al.results || []).find((result) => result.id === acctWaiting.id);
    if (done) {
      const j = acctWaiting;
      acctWaiting = null;
      j.done(done.ok, resultSaid(done));
    } else setTimeout(loadAccounts, 1000);
  }
  const admins = al.https_admins.join(", ");
  acctAdmins = admins;
  // The saved reach, when it changes: the section draws the page again (onChange) over it.
  if (
    !reachSec.saving() &&
    (!reachSec.loaded() || reachSec.savedValue("reach") !== al.reach)
  )
    return reachSec.update({ reach: al.reach });
  const draft = reachSec.get("reach");
  // A reach other than off needs a password, set at the console.
  const blocked = (v) =>
    reachSec.disabled() || (v !== "off" && !al.password_set);
  const change = reachSec.dirty() && reachChange(draft, admins);
  acctEl.loginChoices.replaceChildren(
    chipRow(
      "Script sign-in",
      LOGIN_REACH.map(([v, w]) => [v, w]),
      draft,
      (v) => reachSec.set("reach", v),
      blocked,
    ),
    ...(change
      ? [
          el("p", {
            className: "setting-desc reach-change",
            textContent: `Selected, not saved. ${change.says}`,
          }),
        ]
      : []),
    reachSec.foot,
    reachSec.reason,
  );
  const said = (LOGIN_REACH.find(([v]) => v === al.reach) || LOGIN_REACH[0])[2];
  acctEl.login.textContent =
    `Now: ${said}${!al.password_set ? " It has no password yet: set one at the box's console first (above)." : ""}` +
    (al.reach !== "off" && !admins
      ? " To disable it or limit it to the box itself, first create an administrator account and sign in with it over HTTPS."
      : "");
  acctSettings.fill({ signup: data.settings.signup, http: data.settings.http });
  const c = data.counts;
  acctEl.counts.textContent = `${c.user} user${c.user === 1 ? "" : "s"} (${c.admins} administrator${c.admins === 1 ? "" : "s"}), ${c.asked} waiting for approval, ${c.disabled} disabled.`;
  const when = (t) => (t ? ago(Date.now() / 1000 - t) : "never");
  acctEl.list.replaceChildren(
    ...(data.accounts.length
      ? data.accounts.map((account) => {
          const act = (action, label) =>
            actionButton(label, () => acctAct({ action, name: account.name }));
          const state =
            account.state === "asked"
              ? "Awaiting approval"
              : account.state === "disabled"
                ? "Disabled"
                : account.role === "admin"
                  ? "Administrator"
                  : "User";
          return el(
            "div",
            { className: "admin-item" },
            el("span", {
              className: `state state-${account.state === "user" ? "running" : "stopped"}`,
              textContent: state,
            }),
            el("span", {
              className: "setting-name",
              textContent: ` ${account.name}`,
            }),
            el("span", {
              className: "setting-desc",
              textContent: ` ${account.by}, ${when(account.created)}. Last activity ${when(account.seen)}.${account.password_set ? "" : " Password not set."} ${account.shown_online ? "Name shown online" : "Name not shown"} (their own choice).`,
            }),
            el(
              "span",
              { className: "library-buttons" },
              account.state === "asked" ? act("accept", "Approve account") : null,
              account.state === "user"
                ? act("disable", "Disable account")
                : account.state === "disabled"
                  ? act("enable", "Enable account")
                  : null,
              account.state === "user"
                ? act(
                    account.role === "admin" ? "user" : "admin",
                    account.role === "admin"
                      ? "Set user role"
                      : "Set administrator role",
                  )
                : null,
              account.state !== "asked" ? act("reset", "Reset password") : null,
              acctCodes[account.name.toLowerCase()]
                ? codeBox(account.name, acctCodes[account.name.toLowerCase()])
                : null,
              act(
                "delete",
                account.state === "asked" ? "Refuse request" : "Delete account",
              ),
            ),
          );
        })
      : [el("p", { className: "setting-desc", textContent: "None yet." })]),
  );
}
// Notebooks per account (notebooks.py): private or communal, and how many can run at once.
async function loadNotebooksAdmin() {
  const box = document.getElementById("notebooks-box");
  let st, nb;
  try {
    [st, nb] = await Promise.all([
      getJSON("/admin/settings"),
      getJSON("/admin/notebooks"),
    ]);
  } catch (_) {
    return;
  }
  box.replaceChildren(
    AW.settings(
      [
        {
          key: "notebooks",
          label: "Notebooks",
          kind: "choice",
          value: st.notebooks || "",
          options: [
            ["communal", "Shared notebook only"],
            ["", "Private notebooks when registration is enabled"],
            ["private", "Private notebooks for all accounts"],
          ],
          note: "Shared notebook only: no account has its own notebook. Private notebooks when registration is enabled (the default): each account has its own notebook where people can create accounts. Private notebooks for all accounts: every account has its own notebook.",
        },
        {
          key: "notebooks_max",
          label: "Maximum active notebooks",
          kind: "choice",
          value: st.notebooks_max,
          options: [
            [1, "1"],
            [2, "2"],
            [3, "3"],
            [4, "4"],
            [5, "5"],
            [6, "6"],
            [8, "8"],
            [10, "10"],
          ],
          note: "An extra notebook waits until a running notebook stops. Five is an estimate for a board with 512 MB of memory.",
        },
        {
          key: "notebooks_keep_free_mb",
          label: "Minimum free memory (MB)",
          kind: "choice",
          value: st.notebooks_keep_free_mb,
          options: [
            [48, "48 MB"],
            [64, "64 MB"],
            [96, "96 MB"],
            [128, "128 MB"],
            [192, "192 MB"],
          ],
          note: "No notebook starts while the box has less free memory than this. Running notebooks continue.",
        },
      ],
      {
        id: "notebooks",
        save: async (changed) => {
          await postJSON("/admin/settings", changed);
          loadNotebooksAdmin();
        },
      },
    ),
  );
  const words = !nb.supported
    ? "The web server of this box (Caddy) cannot serve separate notebooks. Only the shared notebook is available."
    : nb.mode !== "private"
      ? "Current mode: shared notebook only."
      : `Current mode: private notebooks. ${nb.notebooks.length} created. ${nb.running.length ? `Running: ${nb.running.join(", ")}.` : "None running."}`;
  document.getElementById("notebooks-state").textContent =
    words + (nb.free_mb != null ? ` ${nb.free_mb} MB of memory free now.` : "");
}
loadNotebooksAdmin();

// A one-time code shows beside the account it is for, in a box to copy, until the
// page is reloaded: the hub keeps only its hash.
const acctCodes = {};
function showCode(name, code) {
  acctCodes[name.toLowerCase()] = code;
  acctEl.code.hidden = false;
  acctEl.code.replaceChildren(
    el("span", {
      textContent: `The one-time code of ${name} is beside the name below. Give it to this person now. The box does not show it again.`,
    }),
  );
}
function codeBox(name, code) {
  const box = el("input", {
    type: "text",
    readOnly: true,
    value: code,
    className: "code-box",
    size: code.length + 1,
  });
  box.setAttribute("aria-label", `${name}'s one-time code`);
  const note = el("span", { className: "setting-desc" });
  const copy = async () => {
    box.select();
    try {
      await navigator.clipboard.writeText(code);
      note.textContent = " Copied.";
      return;
    } catch (_) {
      /* plain HTTP: no clipboard API */
    }
    try {
      note.textContent = document.execCommand("copy")
        ? " Copied."
        : " Select and copy.";
    } catch (_) {
      note.textContent = " Select and copy.";
    }
  };
  return el(
    "span",
    { className: "code-copy" },
    box,
    actionButton("Copy", copy, { className: "small" }),
    note,
  );
}
// True when the hub did it; false when cancelled or refused (said in the note).
async function acctAct(body) {
  if (
    body.action === "delete" &&
    !await AW.confirmHere(
      `Delete the account ${body.name}? This person can no longer sign in. You cannot undo this.`)
  )
    return false;
  try {
    const reply = await postJSON("/admin/accounts", body);
    if (reply.code) showCode(body.name, reply.code);
    say("Done.", true, acctEl.note);
    renderAccounts(reply);
    return true;
  } catch (err) {
    say(err.message, false, acctEl.note);
    return false;
  }
}
const acctSettings = AW.form(acctEl.settings, {
  label: "Account settings",
  save: async (c, v) => {
    const reply = await postJSON("/admin/accounts", {
      action: "settings",
      signup: v.signup,
      http: v.http,
    });
    setTimeout(() => renderAccounts(reply));
    return "Saved.";
  },
});
acctEl.make.addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = acctEl.make.elements;
  acctAct({
    action: "make",
    name: fields.name.value.trim(),
    role: (event.submitter && event.submitter.value) || "user",
  });
  fields.name.value = "";
});
// Who may post on the shoutbox and the forum, and the users' marks (the hub's settings): a form on
// each app's own page.
// Who may post, and the users' marks: a section per form (AW.form), held until Save.
const postingForms = [...document.querySelectorAll(".posting-form")].map(
  (form) =>
    AW.form(form, {
      label: "Posting rules",
      save: async (c) => {
        const out = {};
        for (const [k, v] of Object.entries(c)) out[k] = v;
        fillPosting(await postJSON("/admin/settings", out));
        return "Saved.";
      },
    }),
);
const fillPosting = (st) =>
  postingForms.forEach((sec) => {
    const form = sec.foot.closest("form");
    sec.fill(
      Object.fromEntries(
        [...form.elements]
          .filter((field) => field.name && field.name in st)
          .map((field) => [field.name, st[field.name]]),
      ),
    );
  });
async function loadPosting() {
  try {
    fillPosting(await getJSON("/admin/settings"));
  } catch (_) {
    /* the page says if the hub can't be read */
  }
}
const postingShown = () =>
  paneShown("shoutbox-settings") || paneShown("board-settings");
window.addEventListener("hashchange", () => {
  if (postingShown()) loadPosting();
});
if (postingShown()) loadPosting();
window.addEventListener("hashchange", () => {
  if (paneShown("accounts")) loadAccounts();
});
loadAccounts(); // once on any page: the setup's password step says what the box's own login has

// --- the mesh (meshbridge.py, /admin/mesh) ----------------------------------------------------------
const meshEl = {
  state: document.getElementById("mesh-admin-state"),
  channels: document.getElementById("mesh-channels"),
  form: document.getElementById("mesh-channel-form"),
  longfast: document.getElementById("mesh-longfast"),
  note: noteEl("mesh-note"),
};
let meshPoll = null;
async function loadMesh() {
  try {
    renderMesh(await getJSON("/admin/mesh"));
  } catch (_) {
    meshEl.state.textContent = "Could not read the mesh.";
  }
}
function renderMesh(data) {
  meshEl.state.textContent =
    data.state === "listening"
      ? `Connected to MQTT broker: ${data.nodes.length} node${data.nodes.length === 1 ? "" : "s"} heard in the last 7 days.`
      : `Reconnecting to broker${data.error ? ` (${errorSaid(data)})` : ""}. Check that the MQTT broker is installed (Add-ons, MQTT broker) and running. The box tries again automatically.`;
  meshEl.channels.replaceChildren(
    ...(data.channels.length
      ? data.channels.map((c) =>
          el(
            "div",
            { className: "admin-item" },
            el("span", { className: "setting-name", textContent: c.name }),
            el("span", {
              className: "setting-desc",
              textContent: c.public_key
                ? " Default public key. Anyone can read this channel."
                : c.no_key
                  ? " Unencrypted channel."
                  : " Custom key (not shown).",
            }),
            el(
              "span",
              { className: "library-buttons" },
              actionButton("Remove channel", () =>
                meshAct({ action: "remove-channel", name: c.name }),
              ),
            ),
          ),
        )
      : [
          el("p", {
            className: "setting-desc",
            textContent:
              "No channels configured. The box cannot decode channel-encrypted messages without a configured key. Add a channel and its key as your Meshtastic app shows them.",
          }),
        ]),
  );
  clearTimeout(meshPoll);
  if (paneShown("mesh")) meshPoll = setTimeout(loadMesh, 15000);
}
async function meshAct(body) {
  try {
    await postJSON("/admin/mesh", body);
    say(
      body.action === "add-channel"
        ? `Added ${body.name}.`
        : `Removed ${body.name}.`,
      true,
      meshEl.note,
    );
    loadMesh();
  } catch (err) {
    say(err.message, false, meshEl.note);
  }
}
meshEl.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = meshEl.form.elements;
  meshAct({
    action: "add-channel",
    name: fields.name.value.trim(),
    key: fields.key.value.trim(),
  });
  fields.key.value = "";
});
meshEl.longfast.addEventListener("click", () =>
  meshAct({ action: "add-channel", name: "LongFast", key: "AQ==" }),
);
window.addEventListener("hashchange", () => {
  if (paneShown("mesh")) loadMesh();
});
if (paneShown("mesh")) loadMesh();

// ---- Appearance: the page widths, for everyone, read by every page from
// /layout.css. A choice shows on this page at once; Save keeps it.
const WIDTHS = [45, 60, 80, 90, 100];
// A width as pixels, at the browsers' default text size (16 px a rem); stored in rem.
const widthOption = (w) => [w, `${w * 16} px`];
let appearanceSettings = null;
// WCAG's relative luminance and contrast ratio, for the palettes' checks.
function luminance(hex) {
  const ch = (i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5);
}
function contrast(first, second) {
  const [x, y] = [luminance(first), luminance(second)].sort((larger, smaller) => smaller - larger);
  return (x + 0.05) / (y + 0.05);
}
// ---- The light and dark palettes (Appearance → Colours): each mode its own, the standard theme's colours under
// whatever is left out. Every pair a page draws as text is checked against WCAG's 4.5:1; a palette that falls short
// is saved only once the administrator says so, the pair, its ratio and the target said beside it.
const PALETTE_ROLES = [
  ["bg", "Page"],
  ["surface", "Tiles and panels"],
  ["border", "Edges"],
  ["text", "Text colour"],
  ["muted", "Secondary text"],
  ["accent", "Accent (links, buttons)"],
];
const PALETTE_STANDARD = {
  light: {
    bg: "#f4f5f9",
    surface: "#ffffff",
    border: "#d9dbe6",
    text: "#1b1d27",
    muted: "#6b6f8a",
    accent: "#5b4bd6",
  },
  dark: {
    bg: "#0f1117",
    surface: "#1a1d27",
    border: "#2a2d3a",
    text: "#e2e4ef",
    muted: "#6b6f8a",
    accent: "#7c6af7",
  },
};
// Paired presets: a light accent a shade deeper than its dark one, so each reads on its own ground.
const PALETTE_PRESETS = [
  ["standard", "Standard palette", {}],
  [
    "contrast",
    "Standard, more contrast",
    {
      light: { muted: "#5f6380" },
      dark: { muted: "#8a8fa8", accent: "#9d8ff9" },
    },
  ],
  [
    "teal",
    "Teal",
    {
      light: { muted: "#5f6380", accent: "#0f766e" },
      dark: { muted: "#8a8fa8", accent: "#2dd4bf" },
    },
  ],
  [
    "rose",
    "Rose",
    {
      light: { muted: "#5f6380", accent: "#be123c" },
      dark: { muted: "#8a8fa8", accent: "#fb7185" },
    },
  ],
  [
    "green",
    "Green",
    {
      light: { muted: "#5f6380", accent: "#15803d" },
      dark: { muted: "#8a8fa8", accent: "#4ade80" },
    },
  ],
];
const PALETTE_PAIRS = [
  ["text", "bg", "Text on the page"],
  ["text", "surface", "Text on a tile"],
  ["muted", "bg", "Secondary text on the page"],
  ["muted", "surface", "Secondary text on a tile"],
  ["accent", "bg", "Link on the page"],
  ["accent", "surface", "Link on a tile"],
  ["ink", "accent", "Button text on the accent colour"],
];
const accentInk = (hex) =>
  contrast("#ffffff", hex) >= contrast("#0f1117", hex) ? "#ffffff" : "#0f1117";
// What a mode shows: the standard theme's, then the mode's own. The words on the accent are the theme's own (white)
// until the accent is changed; then white or near-black, whichever stands out more, as /layout.css writes them.
const PALETTE_INK = { light: "#ffffff", dark: "#ffffff" };
function paletteShown(mode, pal) {
  const own = (pal || {})[mode] || {};
  const c = { ...PALETTE_STANDARD[mode], ...own };
  return { ...c, ink: own.accent ? accentInk(c.accent) : PALETTE_INK[mode] };
}
function paletteShort(mode, pal) {
  const c = paletteShown(mode, pal);
  return PALETTE_PAIRS.map(([pair, b, words]) => ({
    mode,
    words,
    ratio: contrast(c[pair], c[b]),
  })).filter((x) => x.ratio < 4.5);
}
function paletteEditor(st, onSaved) {
  // The one accent of before, where a mode has none of its own, taken in as that mode's: the page looks the same,
  // and a Save keeps it in the palette and clears the old setting.
  const saved = JSON.parse(JSON.stringify(st.palette || {}));
  if (st.accent)
    for (const m of ["light", "dark"])
      if (!(saved[m] || {}).accent)
        saved[m] = { ...saved[m], accent: st.accent };
  let mode = "light"; // the mode shown: a view, not a setting
  const box = el("div", { className: "aw-settings palette-editor" });
  // A section of one value, the whole palette; colours that fail the contrast check are asked about once, at Save.
  const pal = AW.section({
    id: "appearance-palette",
    label: "Colours",
    saved: { palette: saved },
    warn: (c) => {
      const short = [
        ...paletteShort("light", c.palette),
        ...paletteShort("dark", c.palette),
      ];
      return short.length
        ? `${short.length} colour pair${short.length === 1 ? " fails" : "s fail"} the 4.5:1 ratio. Some people will find that text hard to read.`
        : null;
    },
    save: async (c) => {
      await postJSON("/admin/settings", { palette: c.palette, accent: "" });
      reloadLayoutCss();
      setTimeout(onSaved);
    },
    onChange: () => draw(),
  });
  const put = (next) => pal.set("palette", next);
  const draw = () => {
    const draft = pal.get("palette");
    const c = paletteShown(mode, draft);
    const modeChips = el(
      "div",
      {
        className: "chip-group",
        role: "group",
        "aria-label": "The palette shown and changed",
      },
      ...["light", "dark"].map((m) => {
        const b = el("button", {
          type: "button",
          className: "chip" + (m === mode ? " selected" : ""),
          textContent: m === "light" ? "Light" : "Dark",
          onclick: () => {
            mode = m;
            draw();
          },
        });
        b.setAttribute("aria-pressed", String(m === mode));
        return b;
      }),
    );
    const presets = el(
      "div",
      { className: "chip-group", role: "group", "aria-label": "Presets" },
      ...PALETTE_PRESETS.map(([id, label, preset]) => {
        const on = JSON.stringify(preset) === JSON.stringify(draft);
        const b = el("button", {
          type: "button",
          className: "chip" + (on ? " selected" : ""),
          textContent: label,
          onclick: () => put(preset),
        });
        b.dataset.preset = id;
        b.setAttribute("aria-pressed", String(on));
        return b;
      }),
    );
    // Colours that match no preset: said as the owner's own, so the row always shows where it stands.
    if (
      !PALETTE_PRESETS.some(
        ([, , pal]) => JSON.stringify(pal) === JSON.stringify(draft),
      )
    ) {
      const own = el("button", {
        type: "button",
        className: "chip selected",
        textContent: "Custom palette",
        title: "Custom colours. Change them below.",
      });
      own.setAttribute("aria-pressed", "true");
      presets.append(own);
    }
    const roles = PALETTE_ROLES.map(([role, label]) => {
      const own = (draft[mode] || {})[role];
      const pick = el("input", { type: "color", value: c[role] });
      pick.setAttribute("aria-label", `${label}, ${mode}`);
      pick.dataset.role = role;
      pick.addEventListener("change", () =>
        put({
          ...draft,
          [mode]: { ...draft[mode], [role]: pick.value.toLowerCase() },
        }),
      );
      const back = el("button", {
        type: "button",
        className: "action-btn small",
        textContent: "Reset to default theme",
        disabled: !own,
        onclick: () => {
          const m = { ...draft[mode] };
          delete m[role];
          const next = { ...draft };
          if (Object.keys(m).length) next[mode] = m;
          else delete next[mode];
          put(next);
        },
      });
      return el(
        "div",
        { className: "aw-field" },
        el("span", { className: "aw-label", textContent: label }),
        el("span", { className: "aw-colour" }, pick, back),
      );
    });
    // The draft drawn as the page draws it: a page, a tile on it, its words, a link and a button.
    const preview = el(
      "div",
      { className: "palette-preview" },
      el(
        "div",
        { className: "palette-tile" },
        el("strong", { textContent: "A tile" }),
        el("span", {
          className: "palette-muted",
          textContent: "Secondary text",
        }),
        el("a", { href: "#appearance", textContent: "A link" }),
        el("span", { className: "palette-button", textContent: "A button" }),
      ),
      el("span", { textContent: "Words on the page" }),
    );
    for (const [k, v] of Object.entries({
      "--pv-bg": c.bg,
      "--pv-surface": c.surface,
      "--pv-border": c.border,
      "--pv-text": c.text,
      "--pv-muted": c.muted,
      "--pv-accent": c.accent,
      "--pv-ink": c.ink,
    }))
      preview.style.setProperty(k, v);
    const short = [
      ...paletteShort("light", draft),
      ...paletteShort("dark", draft),
    ];
    const report = el(
      "div",
      { className: "palette-report", role: "status" },
      short.length
        ? el(
            "ul",
            {},
            ...short.map((x) =>
              el("li", {
                className: "bad",
                textContent: `${x.mode === "light" ? "Light" : "Dark"}: ${x.words}, ${x.ratio.toFixed(1)}:1 (minimum 4.5:1)`,
              }),
            ),
          )
        : el("p", {
            className: "setting-desc",
            textContent:
              "These text pairs meet 4.5:1 or more in both modes (WCAG AA for text). This check does not cover focus, layout or the other themes.",
          }),
    );
    box.replaceChildren(
      el(
        "div",
        { className: "aw-field" },
        el("span", { className: "aw-label", textContent: "Presets" }),
        presets,
        el("small", {
          className: "setting-desc",
          textContent:
            "Each preset sets both modes. Standard is the theme as supplied.",
        }),
      ),
      el(
        "div",
        { className: "aw-field" },
        el("span", { className: "aw-label", textContent: "Mode" }),
        modeChips,
        el("small", {
          className: "setting-desc",
          textContent:
            "Each mode keeps its own colours. Auto follows the device of the visitor. Visitors see these colours as the Box palette, unless they select Standard, Increased contrast or another theme in the 🎨 menu.",
        }),
      ),
      ...roles,
      preview,
      report,
      pal.foot,
      pal.reason,
    );
  };
  draw();
  return box;
}
// The pictures of the administrator: the favicon (the browser tab's icon on every page) and the hub page's header picture.
// An upload acts at once (not a setting held for Save); Remove takes it away again.
const PICTURE_ROWS = {
  favicon: {
    label: "Browser icon (favicon)",
    url: "/favicon.ico",
    max: 64,
    accept: "image/png,image/x-icon,.ico,.png",
    size: 32,
    none: "No custom favicon.",
    note: "The icon of the browser tab on every page. Use a PNG or .ico file, up to 64 KB. A square file of 32 px or 64 px is enough.",
  },
  "hub-art": {
    label: "Header image",
    url: "/hub-art",
    max: 180,
    accept: "image/png,image/jpeg,image/webp",
    size: 64,
    none: "No header image. The hub page starts with its name.",
    note: "Shown above the name of the hub page. Use a PNG, JPEG or WebP image, up to 180 KB. A WebP banner about 1200 px wide fits.",
  },
};
function pictureRow(which) {
  const row = PICTURE_ROWS[which];
  const img = el("img", {
    className: "picture-preview",
    alt: `${row.label} now`,
    height: row.size,
  });
  const note = el("small", { className: "setting-desc", role: "status" });
  const remove = el("button", {
    type: "button",
    className: "action-btn",
    textContent: "Remove",
  });
  const file = el("input", { type: "file", accept: row.accept });
  file.setAttribute("aria-label", `Upload a ${row.label.toLowerCase()}`);
  const show = () => {
    img.hidden = true;
    img.onload = () => {
      img.hidden = false;
      remove.disabled = false;
      note.textContent = "";
    };
    img.onerror = () => {
      remove.disabled = true;
      note.textContent = row.none;
    };
    img.src = `${row.url}?${Date.now()}`;
  };
  // This tab's icon follows at once: the new favicon, or once removed, the page's own emoji (themes.js keeps it in data-own).
  const refreshTab = (removed) => {
    if (which !== "favicon") return;
    let link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = el("link", { rel: "icon" });
      document.head.append(link);
    }
    if (link.dataset.own === undefined)
      link.dataset.own = link.getAttribute("href") || "";
    if (removed && link.dataset.own) link.href = link.dataset.own;
    else link.href = `/favicon.ico?${Date.now()}`;
  };
  const send = async (body) => {
    try {
      await postJSON("/admin/picture", { which, ...body });
      show();
      refreshTab(!!body.remove);
    } catch (error) {
      note.textContent = error.message;
    }
  };
  file.addEventListener("change", async () => {
    const upload = file.files[0];
    file.value = "";
    if (!upload) return;
    if (upload.size > row.max * 1024) {
      note.textContent = `Maximum size: ${row.max} KB. This file is ${Math.ceil(upload.size / 1024)} KB.`;
      return;
    }
    const data = await new Promise((ok, fail) => {
      const reader = new FileReader();
      reader.onload = () => ok(String(reader.result).split(",")[1] || "");
      reader.onerror = fail;
      reader.readAsDataURL(upload);
    });
    send({ data });
  });
  remove.addEventListener("click", () => send({ remove: true }));
  show();
  return el(
    "div",
    { className: "aw-field picture-row" },
    el("span", { className: "aw-label", textContent: row.label }),
    el("span", { className: "aw-colour" }, img, file, remove),
    note,
    el("small", { className: "setting-desc", textContent: row.note }),
  );
}
const faviconRow = () => pictureRow("favicon");
// The page's own /layout.css again, so a saved colour shows here at once.
function reloadLayoutCss() {
  const link = document.querySelector('link[href^="/layout.css"]');
  if (link) link.href = "/layout.css?" + Date.now();
}
async function loadAppearance() {
  const box = document.getElementById("appearance-box");
  if (!box) return;
  let st;
  try {
    st = await getJSON("/admin/settings");
  } catch {
    return;
  }
  appearanceSettings = st;
  fillWidthBlocks();
  const hub = AW.settings(
    [
      {
        key: "hub_title",
        label: "Name",
        kind: "text",
        value: st.hub_title,
        max: 40,
        note: "the hub page's heading and its browser tab",
      },
      {
        key: "hub_subtitle",
        label: "Subtitle",
        kind: "text",
        value: st.hub_subtitle,
        max: 120,
        note: "Leave empty for no subtitle.",
      },
      {
        key: "krab_art",
        label: "Krab art",
        kind: "toggle",
        value: st.krab_art,
        note: "the pictures at the foot of the administration pages and a few others",
      },
    ],
    {
      id: "appearance-hub",
      save: async (changed) => {
        if ("hub_title" in changed && !changed.hub_title.trim())
          throw new Error("The hub needs a name.");
        const now = await postJSON("/admin/settings", changed);
        reloadLayoutCss();
        return now;
      },
    },
  );
  const row = (key, label, note) => ({
    key,
    label,
    kind: "choice",
    value: st[key],
    note,
    options: WIDTHS.map(widthOption),
  });
  box.replaceChildren(
    el("h3", { textContent: "The hub" }),
    hub,
    el("h3", { textContent: "Colours" }),
    el("p", {
      className: "setting-desc",
      textContent:
        "These colours apply to the light and dark themes for everyone. E-ink and Cybercore keep their own colours.",
    }),
    paletteEditor(st, loadAppearance),
    el("h3", { textContent: "Pictures" }),
    el(
      "div",
      { className: "aw-settings" },
      faviconRow(),
      pictureRow("hub-art"),
    ),
    el("h3", { textContent: "Widths" }),
    AW.settings(
      [
        row("page_width", "Hub", "the tiles, the menu and its pages"),
        row("admin_width", "Admin", "these pages"),
        row("shout_width", "Shoutbox", "its tab on the hub page"),
        row("board_width", "Forum", "its tab on the hub page"),
      ],
      {
        id: "appearance-widths",
        save: async (changed) => {
          const now = await postJSON("/admin/settings", changed);
          document.body.style.setProperty(
            "--page-width",
            now.admin_width + "rem",
          ); // this page is an admin one
          return now;
        },
      },
    ),
    // The emoji pickers' set: newer sets have more, but a phone older than a set's
    // release draws its newest emoji as empty boxes. Chosen by the iPhones each reaches.
    el("h3", { textContent: "Emoji" }),
    el("p", {
      className: "setting-desc",
      textContent:
        "The emoji pickers offer this set to everyone. A newer set has more emoji. " +
        "A device that is older than the set can show the newest emoji as empty boxes. Support depends on the font and the browser, as well as the operating system. " +
        "Emoji 13.1 (1,812 emoji): iOS 14.5 and Android 12 and later. " +
        "Emoji 15.0 (1,870): iOS 16.4 (iPhone 8 and later) and Android 14 and later. Emoji 16.0 (1,906): iOS 18.4 (iPhone XS and later) and Android 16 and later.",
    }),
    AW.settings(
      [
        {
          key: "emoji_set",
          label: "Emoji compatibility",
          kind: "choice",
          value: st.emoji_set,
          note: "Select the newest set that your oldest expected device can show.",
          options: [
            ["13.1", "Emoji 13.1"],
            ["15.0", "Emoji 15.0"],
            ["16.0", "Emoji 16.0"],
          ],
        },
      ],
      { id: "appearance-emoji", save: (changed) => postJSON("/admin/settings", changed) },
    ),
  );
}
loadAppearance();

// The write-frequency levels (Recovery and monitoring), from the most kept to the least written.
const CARD_STOPS = [
  [
    "everything",
    "Frequent writes",
    "The box writes and syncs the crash-watch snapshots every 30 s. It saves the clock every minute.",
  ],
  [
    "thrifty",
    "Reduced clock writes",
    "The box writes and syncs the snapshots every 30 s. It saves the clock every 10 minutes and when the hub server stops. After a crash, the clock can resume up to 10 minutes behind.",
  ],
  [
    "gentle",
    "Buffered writes",
    "The box writes the snapshots without a sync. A hang can lose the last two minutes of snapshots. The box saves the clock every 30 minutes.",
  ],
  [
    "saver",
    "In-memory snapshots",
    "The box keeps the snapshots in memory and writes them every 15 minutes, or at once when crash watch sees trouble. It saves the clock every hour. A hang can lose up to 15 minutes of snapshots.",
  ],
];
async function loadCardWrites() {
  const box = document.getElementById("card-writes-box");
  if (!box) return;
  let st;
  try {
    st = await getJSON("/admin/settings");
  } catch (_) {
    return;
  }
  const stop = st.card_writes || "thrifty";
  box.replaceChildren(
    AW.settings(
      [
        {
          key: "card_writes",
          label: "Card write level",
          kind: "choice",
          value: stop,
          options: CARD_STOPS.map(([v, label]) => [v, label]),
          note:
            (CARD_STOPS.find((c) => c[0] === stop) || CARD_STOPS[1])[2] +
            " While the box still hangs, use Frequent writes or Reduced clock writes. The lower levels lose the data that crash watch collects.",
        },
      ],
      {
        id: "card-writes",
        save: async (changed) => {
          await postJSON("/admin/settings", changed);
          loadCardWrites();
        },
      },
    ),
  );
}
loadCardWrites();
// The card's writes since boot, and per day at that rate: the evidence for the ladder.
function cardRate(sys) {
  if (!sys || sys.card_written == null || !sys.boot_uptime) return null;
  const perDay = (sys.card_written / sys.boot_uptime) * 86400;
  return `${size(sys.card_written)} since boot, about ${size(perDay)} a day at that rate.`;
}

// ---- Folders: each folder's page arranges its entries. The
// administrator can hide an entry from a folder, order a folder's entries, and put an entry in other
// folders as well. Each folder is a section with its own Save and Discard, on its own page; "Also included in"
// changes the other folder's section, saved from that folder's page.
let folders = null,
  folderDraft = null;
const folderSecs = new Map(); // folder -> its section
const folderFull = (v) => ({
  hidden: [...((v || {}).hidden || [])],
  order: [...((v || {}).order || [])],
  extra: [...((v || {}).extra || [])],
});
const folderOne = (folderId, state) => folderNorm({ [folderId]: (state || {})[folderId] || {} })[folderId] || {};
function folderSec(folderId) {
  if (folderSecs.has(folderId)) return folderSecs.get(folderId);
  const fol = (folders.folders || []).find((x) => x.id === folderId);
  const sec = AW.section({
    id: `folder-${folderId}`,
    label: `Folder: ${fol ? fol.title : folderId}`,
    saved: null,
    save: async (c, v) => {
      const state = { ...folders.state, [folderId]: folderFull(v.all) };
      folders = await postJSON("/admin/folders", { state });
      setTimeout(folderSaved);
    },
    onChange: () => {
      folderDraft[folderId] = folderFull(sec.get("all"));
      fillFolderBlocks();
    },
  });
  folderSecs.set(folderId, sec);
  return sec;
}
// The saved arrangement into every folder's section (a draft kept over it).
function folderSaved() {
  for (const fol of folders.folders)
    folderSec(fol.id).update({ all: folderOne(fol.id, folders.state) });
}
// After a change on the page: each folder's section told its draft.
function folderTouched() {
  for (const fol of folders.folders)
    folderSec(fol.id).set("all", folderOne(fol.id, folderDraft), true);
}
const folderOpen = {}; // folder -> the entry left open, so a change keeps it in view
const KIND_WORD = {
  switches: "starting switches",
  download: "a download",
  page: "a page",
};
async function loadFolders() {
  try {
    folders = await getJSON("/admin/folders");
  } catch {
    return;
  }
  if (!folderDraft)
    folderDraft = Object.fromEntries(
      folders.folders.map((x) => [x.id, folderFull((folders.state || {})[x.id])]),
    );
  folderSaved();
  fillFolderBlocks();
}
function fillFolderBlocks() {
  if (!folders) return;
  document
    .querySelectorAll(".folder-block[data-folder]")
    .forEach((b) => drawFolder(b, b.dataset.folder));
  drawFolderIndex();
}
// The managed app an entry opens, if any (an entry may be a download or a page instead).
function appFor(href) {
  const norm = (h) => String(h || "").replace(/\/$/, "");
  return (
    (AL.apps() || []).find(
      (app) =>
        !app.folder &&
        app.href &&
        norm(app.href) === norm(href) &&
        document.getElementById("page-app-" + app.id),
    ) || null
  );
}
// The Folders page: a line per folder, with how many entries it shows, leading to its own page.
function drawFolderIndex() {
  const list = document.getElementById("folder-index-list");
  const apps = AL.apps();
  if (!list || !apps) return;
  const changed = folders
    ? folders.folders.filter((x) => folderSec(x.id).dirty())
    : [];
  const rows = apps
    .filter((app) => app.folder)
    .map((app) => {
      const fol = folders && folders.folders.find((x) => x.id === app.id);
      let count = "";
      if (fol) {
        const hrefs = new Set([
          ...fol.entries.map((entry) => entry.href),
          ...((folderDraft[app.id] || {}).extra || []),
        ]);
        const n = [...hrefs].filter((h) => inFolder(app.id, h)).length;
        count = `${n} ${n === 1 ? "entry" : "entries"} visible`;
      }
      return el(
        "li",
        { className: "inv-row" },
        el(
          "a",
          { className: "inv-name", href: `#page-app-${app.id}` },
          app.icon ? `${app.icon} ${app.name}` : app.name,
        ),
        el(
          "span",
          { className: "inv-facts" },
          count
            ? el("span", { className: "inv-fact", textContent: count })
            : null,
        ),
      );
    });
  list.replaceChildren(
    ...(rows.length
      ? rows
      : [
          el("li", {
            className: "setting-desc",
            textContent: "This hub has no folders.",
          }),
        ]),
  );
  const note =
    document.getElementById("folder-index-note") ||
    el("p", {
      className: "setting-desc action-note",
      id: "folder-index-note",
      role: "status",
    });
  note.textContent = changed.length
    ? `Unsaved changes in ${changed.map((x) => x.title).join(", ")}. Each folder's page has its own Save and Discard.`
    : "";
  if (!note.isConnected) list.after(note);
}
AL.onBuild(drawFolderIndex);
drawFolderIndex(); // the menu may have been built before this script ran
AL.onBuild(fillFolderBlocks);
// The arrangement without its empty parts, sorted by folder: what changed, and nothing else.
const folderNorm = (byFolder) =>
  Object.fromEntries(
    Object.entries(byFolder || {})
      .sort()
      .map(([folderId, v]) => [
        folderId,
        Object.fromEntries(
          ["hidden", "order", "extra"]
            .filter((k) => (v[k] || []).length)
            .map((k) => [k, v[k]]),
        ),
      ])
      .filter(([, v]) => Object.keys(v).length),
  );
const fpart = (folderId) => {
  folderDraft[folderId] = folderDraft[folderId] || { hidden: [], order: [], extra: [] };
  return folderDraft[folderId];
};
// Is an entry in a folder: one of the folder's own and not hidden there, or put in from another.
function inFolder(folderId, href) {
  const own = (
    folders.folders.find((x) => x.id === folderId) || { entries: [] }
  ).entries.some((entry) => entry.href === href && !entry.extra);
  const st = folderDraft[folderId] || {};
  return own
    ? !(st.hidden || []).includes(href)
    : (st.extra || []).includes(href);
}
function setInFolder(folderId, href, yes) {
  const own = (
    folders.folders.find((x) => x.id === folderId) || { entries: [] }
  ).entries.some((entry) => entry.href === href && !entry.extra);
  const st = fpart(folderId);
  const without = (list) => list.filter((h) => h !== href);
  if (own) st.hidden = yes ? without(st.hidden) : [...without(st.hidden), href];
  else st.extra = yes ? [...without(st.extra), href] : without(st.extra);
}
function drawFolder(block, folderId) {
  const fol = folders.folders.find((x) => x.id === folderId);
  if (!fol) {
    block.replaceChildren();
    return;
  }
  const st = fpart(folderId);
  const pos = (h) => {
    const i = st.order.indexOf(h);
    return i < 0 ? 1e6 : i;
  };
  // The folder's entries, plus any put in since the last save, in the draft's order.
  const pool = new Map(
    folders.folders.flatMap((x) => x.entries).map((entry) => [entry.href, entry]),
  );
  const hrefs = [
    ...new Set([...fol.entries.map((entry) => entry.href), ...st.extra]),
  ].filter((h) => pool.has(h));
  const list = hrefs
    .map((h, i) => ({ h, i }))
    .sort((left, right) => pos(left.h) - pos(right.h) || left.i - right.i)
    .map((x) => pool.get(x.h));
  const move = (entry, step) => {
    const order = list.map((x) => x.href),
      i = order.indexOf(entry.href),
      j = i + step;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    st.order = order;
    folderOpen[folderId] = entry.href;
    folderTouched();
    fillFolderBlocks();
  };
  const items = list.map((entry) => {
    const shown = inFolder(folderId, entry.href);
    return {
      id: entry.href,
      title: entry.name,
      summary: entry.desc || entry.href,
      badges: [shown ? "shown" : "hidden", KIND_WORD[entry.kind] || entry.kind].concat(
        entry.from !== folderId ? ["from " + entry.from] : [],
      ),
      detail: () =>
        [
          AW.h(
            "div",
            { class: "aw-field" },
            AW.h("span", { class: "aw-label", text: "Visible in this folder" }),
            AW.h(
              "button",
              {
                type: "button",
                class: "chip-btn" + (shown ? " active" : ""),
                "aria-pressed": String(shown),
                onclick: () => {
                  setInFolder(folderId, entry.href, !shown);
                  folderOpen[folderId] = entry.href;
                  folderTouched();
                  fillFolderBlocks();
                },
              },
              shown
                ? "Visible in this folder: On"
                : "Visible in this folder: Off",
            ),
            AW.btn("↑ Earlier", { onclick: () => move(entry, -1) }),
            AW.btn("↓ Later", { onclick: () => move(entry, 1) }),
          ),
          AW.h(
            "div",
            { class: "aw-field" },
            AW.h("span", { class: "aw-label", text: "Also included in" }),
            AW.h(
              "div",
              { class: "filter-chips" },
              folders.folders
                .filter((x) => x.id !== folderId)
                .map((x) => {
                  const on = inFolder(x.id, entry.href);
                  return AW.h(
                    "button",
                    {
                      type: "button",
                      class: "filter-chip" + (on ? " active" : ""),
                      "aria-pressed": String(on),
                      onclick: () => {
                        setInFolder(x.id, entry.href, !on);
                        folderOpen[folderId] = entry.href;
                        folderTouched();
                        fillFolderBlocks();
                      },
                    },
                    x.title,
                  );
                }),
            ),
          ),
          AW.dl({ Opens: entry.href }),
          // An entry that is a managed app: its access and settings are on the app's own page;
          // hiding it here only takes it out of this folder.
          appFor(entry.href)
            ? AW.h(
                "a",
                { href: "#page-app-" + appFor(entry.href).id, class: "action-btn" },
                `Open app settings: ${appFor(entry.href).name} →`,
              )
            : null,
        ].filter(Boolean),
    };
  });
  const shownCount = list.filter((entry) => inFolder(folderId, entry.href)).length;
  // A change made here to another folder (Also included in) is saved from that folder's page: said here.
  const others = folders.folders.filter(
    (x) => x.id !== folderId && folderSec(x.id).dirty(),
  );
  const sec = folderSec(folderId);
  block.replaceChildren(
    AW.h("p", {
      class: "setting-desc",
      text: `${shownCount} of ${list.length} visible on its page. The entries come from the manifests of apps, from the index of an app and from start switches on one page.`,
    }),
    AW.shortList(items, { id: "folder-list-" + folderId }),
    sec.foot,
    sec.reason,
    ...(others.length
      ? [
          AW.h("p", {
            class: "setting-desc",
            text: `Also changed, and saved from their own pages: ${others.map((x) => x.title).join(", ")}.`,
          }),
        ]
      : []),
  );
  const open =
    folderOpen[folderId] &&
    [...block.querySelectorAll(".aw-row")].find(
      (row) => row.dataset.awId === folderOpen[folderId],
    );
  if (open) AW.foldRow(open.querySelector(".aw-row-head"), true);
}
loadFolders();

// An app drawn on the hub page has its width on its own page too, as on Appearance: one row of
// the same setting.
const WIDTH_LABEL = {
  shout_width: "Width on hub page",
  board_width: "Width on hub page",
};
function fillWidthBlocks() {
  if (!appearanceSettings) return;
  document.querySelectorAll(".width-block[data-key]").forEach((block) => {
    const key = block.dataset.key;
    block.replaceChildren(
      AW.settings(
        [
          {
            key,
            label: WIDTH_LABEL[key] || "Width",
            kind: "choice",
            value: appearanceSettings[key],
            note: "also on System → Appearance",
            options: WIDTHS.map(widthOption),
          },
        ],
        {
          id: `width-${key}`,
          save: async (changed) => {
            appearanceSettings = await postJSON("/admin/settings", changed);
            loadAppearance();
            return appearanceSettings;
          },
        },
      ),
    );
  });
}
AL.onBuild(fillWidthBlocks);

// ---- Status tiles: the box row arranged, held until Save.
let stTiles = null,
  stDraft = null;
// A tile's size, for all tiles: one cell, wide (two across) or large (two by two).
const TILE_SIZES = [
  ["single", "Single cell"],
  ["wide", "Two columns"],
  ["large", "Two-by-two"],
];
function sizeChips(cur, set) {
  return AW.h(
    "div",
    { class: "chip-group", role: "group", "aria-label": "Size" },
    TILE_SIZES.map(([v, label]) =>
      AW.h(
        "button",
        {
          type: "button",
          class: "chip" + (cur === v ? " selected" : ""),
          "aria-pressed": String(cur === v),
          title: label,
          onclick: () => set(v),
        },
        label,
      ),
    ),
  );
}
function setSize(draft, id, v) {
  draft.size = { ...draft.size };
  if (v === "single") delete draft.size[id];
  else draft.size[id] = v;
}
// The arrangement in one shape, so the same arrangement compares the same: every tile in order, the hidden ones
// and the sizes sorted.
function stNorm(state) {
  const pos = (i) => {
    const n = (state.order || []).indexOf(i);
    return n < 0 ? 1e6 : n;
  };
  return {
    order: stTiles.tiles
      .map((x, i) => ({ id: x.id, i }))
      .sort((left, right) => pos(left.id) - pos(right.id) || left.i - right.i)
      .map((x) => x.id),
    hidden: [...(state.hidden || [])].sort(),
    size: Object.fromEntries(Object.entries(state.size || {}).sort()),
  };
}
// Status tiles as one section: its value the arrangement.
const stSec = AW.section({
  id: "status-tiles",
  label: "Status tiles",
  saved: null,
  save: async (c, v) => {
    stTiles = await postJSON("/admin/status-tiles", { state: v.all });
    setTimeout(() => stSec.update({ all: stNorm(stTiles.state) }));
  },
  onChange: () => {
    stDraft = JSON.parse(JSON.stringify(stSec.get("all")));
    drawStatusTiles();
  },
});
async function loadStatusTiles() {
  try {
    stTiles = await getJSON("/admin/status-tiles");
  } catch {
    return;
  }
  stSec.update({ all: stNorm(stTiles.state) });
}
function drawStatusTiles(openId) {
  const box = document.getElementById("status-tiles-box");
  if (!box || !stTiles || !stDraft) return;
  if (!openId) {
    const was = box.querySelector('.aw-row-head[aria-expanded="true"]');
    openId = was ? was.closest(".aw-row").dataset.awId : null;
  }
  const pos = (i) => {
    const n = stDraft.order.indexOf(i);
    return n < 0 ? 1e6 : n;
  };
  const list = stTiles.tiles
    .map((x, i) => ({ x, i }))
    .sort((left, right) => pos(left.x.id) - pos(right.x.id) || left.i - right.i)
    .map((pair) => pair.x);
  const toggle = (k, id, on) => {
    stDraft[k] = on
      ? [...new Set([...stDraft[k], id])]
      : stDraft[k].filter((x) => x !== id);
  };
  const redraw = (id) => {
    stDraft = stNorm(stDraft);
    stSec.set("all", JSON.parse(JSON.stringify(stDraft)), true);
    drawStatusTiles(id);
  };
  const move = (id, step) => {
    const order = list.map((x) => x.id),
      i = order.indexOf(id),
      k = i + step;
    if (k < 0 || k >= order.length) return;
    [order[i], order[k]] = [order[k], order[i]];
    stDraft.order = order;
    redraw(id);
  };
  const items = list.map((t) => {
    const shown = !stDraft.hidden.includes(t.id),
      size = (stDraft.size || {})[t.id] || "single";
    return {
      id: t.id,
      title: t.name,
      summary: {
        single: "Single cell",
        wide: "Two columns",
        large: "Two-by-two",
      }[size],
      badges: [shown ? "shown" : "hidden"],
      detail: () => [
        AW.h(
          "div",
          { class: "aw-field" },
          AW.h("span", { class: "aw-label", text: "On the hub page" }),
          AW.h(
            "button",
            {
              type: "button",
              class: "chip-btn" + (shown ? " active" : ""),
              "aria-pressed": String(shown),
              onclick: () => {
                toggle("hidden", t.id, shown);
                redraw(t.id);
              },
            },
            shown ? "Shown: On" : "Shown: Off",
          ),
          AW.btn("↑ Earlier", { onclick: () => move(t.id, -1) }),
          AW.btn("↓ Later", { onclick: () => move(t.id, 1) }),
        ),
        AW.h(
          "div",
          { class: "aw-field" },
          AW.h("span", { class: "aw-label", text: "Size" }),
          sizeChips(size, (v) => {
            setSize(stDraft, t.id, v);
            redraw(t.id);
          }),
          t.id === "box-people"
            ? AW.h("small", {
                class: "setting-desc",
                text: "Two columns or Two-by-two shows the estimated number of distinct devices seen today and this week, if counting is enabled (Security).",
              })
            : null,
        ),
      ],
    };
  });
  box.replaceChildren(
    AW.shortList(items, { id: "status-tiles-list" }),
    stSec.foot,
    stSec.reason,
  );
  const open =
    openId &&
    [...box.querySelectorAll(".aw-row")].find((row) => row.dataset.awId === openId);
  if (open) AW.foldRow(open.querySelector(".aw-row-head"), true);
}
loadStatusTiles();

// Who sees the names of those signed in (a setup decision): users or administrators only.
async function loadNamesTo() {
  const box = document.getElementById("names-to-box");
  if (!box) return;
  let st;
  try {
    st = await getJSON("/admin/settings");
  } catch {
    return;
  }
  box.replaceChildren(
    AW.settings(
      [
        {
          key: "names_to",
          label: "Visible to",
          kind: "choice",
          value: st.names_to,
          decision: "names",
          options: [
            ["users", "signed-in users"],
            ["admin", "administrators only"],
          ],
          note: "Names appear only for users who chose to be shown on their own account page. Guests see only the number of users.",
        },
      ],
      { id: "names", save: (changed) => postJSON("/admin/settings", changed) },
    ),
  );
}
loadNamesTo();

// Saves tied to accounts: disabled by default, so a device lock is
// the only thing that guards a save unless an administrator enables this.
async function loadSavesCrossDevice() {
  const box = document.getElementById("saves-cross-device-box");
  if (!box) return;
  let st;
  try {
    st = await getJSON("/admin/settings");
  } catch {
    return;
  }
  box.replaceChildren(
    AW.settings(
      [
        {
          key: "saves_cross_device",
          label: "Let users edit their saved items from any device",
          kind: "toggle",
          value: st.saves_cross_device,
          note: "Disabled: only the device that made or locked a save can change it. Enabled: a signed-in user can change or remove any save of their own account on any device. This overrides the device lock for saves that belong to an account. Saves of guests do not change.",
        },
      ],
      { id: "saves", save: (changed) => postJSON("/admin/settings", changed) },
    ),
  );
}
loadSavesCrossDevice();

// --- Needs attention --------------------------------------------------------------------------
// One list at the top of Overview of what is waiting on an administrator, each a link to where it is dealt
// with: the words the sections already put beside their sidebar entries, and the setup decisions
// not yet made. Work in progress ("working") isn't waiting on anyone, so it is left out.
const ATTENTION = {
  health: (w) =>
    w === "!"
      ? "The root helper is stuck. Actions that need root cannot run"
      : `${w} unresolved finding${w === "1" ? "" : "s"} in Box doctor`,
  secdoctor: (w) =>
    `${w} unresolved finding${w === "1" ? "" : "s"} in Security doctor`,
  security: (w) =>
    `${w} security choice${w === "1" ? "" : "s"} waiting for a decision`,
  updoctor: (w) =>
    w === "!"
      ? "An update failed its checks"
      : `${w} problem${w === "1" ? "" : "s"} with updates`,
  updates: (w) =>
    ({ ready: "Update ready to install", new: "An update is available" })[w] ||
    null,
  clock: (w) => `${w} clock problem${w === "1" ? "" : "s"}`,
  network: () => "A network link is down",
  git: (w) => `${w} build${w === "1" ? "" : "s"} failed since your last visit`,
  moderation: (w) => `${w} pending report${w === "1" ? "" : "s"}`,
  "updates-security": () => "Debian security updates waiting to be installed",
};
function drawAttention() {
  const box = document.getElementById("attention");
  if (!box) return;
  const items = Object.entries(AL.badges())
    .filter(([, w]) => w && w !== "working")
    .map(([id, w]) => {
      const say = ATTENTION[id] ? ATTENTION[id](w) : `${AL.titleOf(id)}: ${w}`;
      return say && { id, text: say, where: AL.titleOf(id) };
    })
    .filter(Boolean);
  const T = window.TOUR;
  const left = T ? T.left() : 0;
  if (left > 0)
    items.push({
      id: "welcome",
      text: `${left} incomplete setup step${left === 1 ? "" : "s"}. The box uses default settings until you complete them`,
      where: "Setup steps",
    });
  box.replaceChildren(
    el("h3", { textContent: "Needs attention" }),
    items.length
      ? el(
          "ul",
          { className: "admin-checks attention-list" },
          ...items.map((i) =>
            el(
              "li",
              { className: "check" },
              el("a", { href: "#" + i.id, textContent: i.text }),
              el("span", {
                className: "setting-desc",
                textContent: ` — ${i.where}`,
              }),
            ),
          ),
        )
      : el("p", {
          className: "setting-desc",
          textContent: "No action required.",
        }),
  );
}
AL.onBadge(drawAttention);
AL.onBuild(drawAttention);
drawAttention();

// --- older lists, bounded --------------------------------------------------------------------------
// What people made and the box's records grow without end: each gets a limited height and, once it
// is long, a filter. The catalogue's results have a search of their own, so the height only.
[
  "store-saves",
  "mod-messages",
  "mod-threads",
  "mod-drops",
  "accounts-list",
  "ci-runs",
].forEach((id) => AW.bound(document.getElementById(id)));
AW.bound(document.getElementById("catalogue-results"), { filter: false });

// --- tabs ---------------------------------------------------------------------------------------
// [data-tabs]: a tablist of .view-switch-btn, each naming its panel (aria-controls). One showing;
// arrow keys move along; the choice kept in this browser; an address inside a panel opens its tab.
function initTabs(box) {
  const tabs = [...box.querySelectorAll('[role="tab"]')];
  const key = "irate-tabs-" + box.dataset.tabs;
  const pick = (tab, focus) => {
    tabs.forEach((x) => {
      const on = x === tab;
      x.classList.toggle("active", on);
      x.setAttribute("aria-selected", String(on));
      x.tabIndex = on ? 0 : -1;
      document.getElementById(x.getAttribute("aria-controls")).hidden = !on;
    });
    if (focus) tab.focus();
    try {
      localStorage.setItem(key, tab.id);
    } catch (_) {
      /* not kept */
    }
  };
  tabs.forEach((x, i) => {
    x.addEventListener("click", () => pick(x));
    x.addEventListener("keydown", (event) => {
      const k = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (k) {
        event.preventDefault();
        pick(tabs[(i + k + tabs.length) % tabs.length], true);
      }
    });
  });
  let kept = null;
  try {
    kept = document.getElementById(localStorage.getItem(key));
  } catch (_) {
    /* none */
  }
  pick(tabs.includes(kept) ? kept : tabs[0]);
  const follow = () => {
    const target =
      location.hash.length > 1 &&
      document.getElementById(location.hash.slice(1));
    const panel =
      target && target.closest && target.closest('[role="tabpanel"]');
    const tab =
      panel && tabs.find((x) => x.getAttribute("aria-controls") === panel.id);
    if (tab) pick(tab);
  };
  window.addEventListener("hashchange", follow);
  follow();
}
document.querySelectorAll("[data-tabs]").forEach(initTabs);

// --- what an update must carry (root/signing.py) ----------------------------------------------
// off, merged on GitHub, or signed releases with the owner's keys. The keys are root's: the page
// gets back only their names and types, and each Save replaces the list with what is in the box.
const sigEl = {
  form: document.getElementById("update-signing-form"),
  note: noteEl("update-signing-note"),
  label: document.getElementById("update-signers-label"),
  kept: noteEl("update-signers-kept"),
};
let sigWaiting = null;
const SIG_WORDS = {
  off: "from the branch, without a signature check",
  github: "merged on GitHub (signed by GitHub)",
  tags: "signed releases only",
};
function sigShowKeys() {
  sigEl.label.hidden = sigEl.form.elements.level.value !== "tags";
}
function drawSigning(sig, results) {
  if (!sigEl.form || !sig) return;
  if (sigWaiting) {
    const done = (results || []).find((result) => result.id === sigWaiting.id);
    if (done) {
      sigWaiting.done(done.ok, resultSaid(done));
      sigWaiting = null;
      if (done.ok) say(resultSaid(done), true, sigEl.note);
    }
  }
  // The keys are root's: the box below starts empty, and what is in it is sent with a Save.
  sigSec.fill({ level: sig.level, signers: sigSec.savedValue("signers") ?? "" });
  const says = document.getElementById("update-signing-says");
  if (says) says.textContent = SIG_WORDS[sig.level] || sig.level;
  sigShowKeys();
  sigEl.kept.textContent =
    sig.level === "tags" && sig.keys.length
      ? `Trusted now: ${sig.keys.map((k) => `${k.name} (${k.type}${k.comment ? `, ${k.comment}` : ""})`).join(", ")}. Saving replaces the list with the keys in the box above.`
      : `Now: ${SIG_WORDS[sig.level] || sig.level}.`;
}
sigEl.form.addEventListener("change", sigShowKeys);
const sigSec = AW.form(sigEl.form, {
  label: "Update signing",
  warn: (c, v) =>
    v.level === "off" ? "Updates install without a signature check." : null,
  save: async (c, v) => {
    if (v.level === "tags" && !v.signers.trim())
      throw new Error(
        "Signed releases need at least one key. Paste it in the box.",
      );
    const j = AW.job();
    j.id = (
      await postJSON("/admin/update", {
        action: "signing",
        level: v.level,
        signers: v.signers,
      })
    ).id;
    sigWaiting = j;
    setTimeout(loadUpdate, 1500);
    return j;
  },
});

// ---- All apps: one line per app, from what the other loaders already hold (the manifests, access,
// the librarian, the installer's add-ons, the web add-ons, the tiles). It edits nothing: each line
// leads to the app's own page. What no loader has answered for yet is said as unknown.
let invShow = "all";
const INV_KIND = {
  builtin: "Built-in app",
  app: "App bundle",
  addon: "Service add-on",
  local: "Web add-on",
};
const INV_OPENS = {
  public: "Access: guests",
  users: "Access: users",
  private: "Access: administrators",
  off: "Disabled",
};
function inventoryRows() {
  const manifests = AL.apps() || [];
  const access = new Map(((lastAccess || {}).apps || []).map((app) => [app.id, app]));
  const libApps = lastLibrary ? lastLibrary.apps || {} : null;
  const addons = new Map(
    ((lastAddons || {}).addons || []).map((addon) => [addon.id, addon]),
  );
  const local = lastLocal;
  const tileIds = tilesData ? new Set(tilesData.tiles.map((t) => t.id)) : null;
  const pages = new Set(AL.pages().map((page) => page.id));
  const rows = new Map();
  const add = (id, name, kind) => {
    if (!rows.has(id))
      rows.set(id, { id, name, kind, facts: [], attention: [] });
    return rows.get(id);
  };
  for (const m of manifests.filter((x) => !x.folder)) {
    const appAccess = access.get(m.id);
    add(
      m.id,
      m.name,
      (appAccess && appAccess.kind) ||
        (m.addon ? "addon" : m.local ? "local" : m.updates ? "app" : "builtin"),
    );
  }
  for (const appAccess of access.values()) add(appAccess.id, appAccess.title, appAccess.kind);
  for (const addon of addons.values()) add(addon.id, addon.title, "addon");
  if (libApps)
    for (const [id, libApp] of Object.entries(libApps)) add(id, libApp.title, "app");
  const offered = local ? (local.catalogue || []).filter((c) => !c.added) : [];
  for (const c of offered) add(c.id, c.title, "local").offered = true;
  for (const row of rows.values()) {
    const appAccess = access.get(row.id);
    let state = "unknown";
    if (row.kind === "builtin") state = "builtin";
    else if (row.offered) state = "available";
    else if (row.kind === "addon") {
      const x = addons.get(row.id);
      if (x)
        state =
          x.added === null ? "unknown" : x.added ? "installed" : "available";
      if (x && x.added && x.active === false && !(appAccess && appAccess.mode === "off"))
        row.attention.push("Service not running");
    } else if (row.kind === "app" && libApps) {
      const lib = libApps[row.id];
      state = lib ? (lib.installed ? "installed" : "available") : "unknown";
      const st = (lastLibrary.status || {})[row.id] || {};
      if (st.install_result && !st.install_result.ok)
        row.attention.push("Install failed");
      if (st.error) row.attention.push("Update check failed");
      const target =
        lib && lib.pin && (st.follow || "pinned") === "pinned"
          ? st.pinned
          : st.latest;
      if (target && st.current && target.version !== st.current.version)
        row.attention.push("Newer build known");
    } else if (row.kind === "local" && local) {
      const x = (local.added || []).find((y) => y.id === row.id);
      state = x
        ? x.installed && x.installed.commit
          ? "installed"
          : "fetching"
        : "unknown";
      if (x && x.catalogue && x.catalogue.status === "held")
        row.attention.push("Consent required");
    }
    row.state = state;
    if (state !== "builtin")
      row.facts.push(
        {
          installed: "Installed",
          available: "Not installed",
          fetching: "Downloading",
          unknown: "Installation status unavailable",
        }[state],
      );
    if (appAccess) row.facts.push(INV_OPENS[appAccess.mode] || appAccess.mode);
    if (appAccess && appAccess.mode !== "off" && tileIds) {
      row.facts.push(
        !tileIds.has(row.id)
          ? "No home tile"
          : appAccess.visible && appAccess.visible !== "auto"
            ? `Visibility: ${SEEN_LABEL[appAccess.visible] || appAccess.visible}`
            : "Visibility: as its access",
      );
    }
    row.href = pages.has("page-app-" + row.id)
      ? "#page-app-" + row.id
      : row.kind === "local"
        ? row.offered
          ? "#local-catalogue"
          : "#local-added"
        : row.kind === "addon"
          ? "#addons"
          : "#apps";
  }
  return [...rows.values()];
}
function renderInventory() {
  const list = document.getElementById("inv-list");
  if (!list || !AL.apps()) return;
  const q = document.getElementById("inv-q").value.trim().toLowerCase();
  const all = inventoryRows();
  const want = (row) =>
    invShow === "all" ||
    row.state === invShow ||
    (invShow === "available" && row.state === "fetching");
  const shown = all.filter(
    (row) =>
      want(row) &&
      (!q || `${row.name} ${INV_KIND[row.kind]}`.toLowerCase().includes(q)),
  );
  list.replaceChildren(
    ...shown.map((row) =>
      el(
        "li",
        { className: "inv-row" },
        el("a", { className: "inv-name", href: row.href, textContent: row.name }),
        el("span", {
          className: "inv-kind",
          textContent: INV_KIND[row.kind] || row.kind,
        }),
        el(
          "span",
          { className: "inv-facts" },
          ...row.facts.map((t) =>
            el("span", { className: "inv-fact", textContent: t }),
          ),
          ...row.attention.map((t) =>
            el("span", { className: "inv-fact bad", textContent: t }),
          ),
        ),
      ),
    ),
  );
  document.getElementById("inv-note").textContent = shown.length
    ? `${shown.length} of ${all.length} shown.`
    : "Nothing matches.";
  fillHeadBlocks();
  document.querySelectorAll("[data-inv]").forEach((b) => {
    b.classList.toggle("active", b.dataset.inv === invShow);
    b.setAttribute("aria-pressed", String(b.dataset.inv === invShow));
  });
}
document.querySelectorAll("[data-inv]").forEach((b) =>
  b.addEventListener("click", () => {
    invShow = b.dataset.inv;
    renderInventory();
  }),
);
document.getElementById("inv-q").addEventListener("input", renderInventory);
AL.onBuild(renderInventory);
renderInventory(); // the menu may have been built before this script ran
