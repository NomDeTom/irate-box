// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin's Podcasts part: the settings, the feeds and OPML. The server runs the fetches; the page
// asks and redraws, as the other Library parts do.
(() => {
  const panel = document.getElementById("podcasts-panel");
  if (!panel) return;
  const POLL = 3000;
  let snap = null;

  const el = (tag, props = {}, ...kids) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  };
  const size = (n) => (n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`);
  const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "never");
  const note = el("p", { className: "setting-desc action-note", role: "status", hidden: true });

  function say(text, bad = false) {
    note.hidden = !text;
    note.textContent = text;
    note.classList.toggle("bad", bad);
  }

  async function post(body) {
    const response = await fetch("/admin/podcasts", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Irate-Admin": "1" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `the box answered ${response.status}`);
    snap = data.settings ? data : await get();
    draw();
    return data;
  }

  async function get() {
    const response = await fetch("/admin/podcasts");
    if (!response.ok) throw new Error(`the box answered ${response.status}`);
    return response.json();
  }

  const field = (label, input) => el("label", { className: "setting" }, el("span", { className: "setting-name", textContent: label }), input);

  function number(key, label, min, max, value) {
    const input = el("input", { type: "number", min, max, value, dataset: { key } });
    return field(label, input);
  }

  function check(key, label, value) {
    const input = el("input", { type: "checkbox", checked: !!value, dataset: { key } });
    input.addEventListener("change", () => post({ action: "settings", [key]: input.checked }).catch((e) => say(e.message, true)));
    return el("label", { className: "setting" }, input, el("span", { className: "setting-name", textContent: ` ${label}` }));
  }

  function settingsForm() {
    const s = snap.settings;
    const box = el("div", { className: "settings-block" });
    box.append(check("enabled", "Follow podcasts", s.enabled));
    box.append(
      number("check_every_hours", "Check every (hours; 0 only when asked)", 0, 168, s.check_every_hours),
      number("back_catalogue", "New feed: episodes to fetch (-1 all)", -1, 1000, s.back_catalogue),
      number("keep_count", "Keep per feed, by count (0 no limit)", 0, 500, s.keep_count),
      number("keep_days", "Keep per feed, by age in days (0 no limit)", 0, 3650, s.keep_days),
      number("keep_mb", "Keep per feed, by size in MB (0 no limit)", 0, 10000000, s.keep_mb),
      number("cap_mb", "All podcasts' cap in MB (0 no cap)", 0, 10000000, s.cap_mb),
      number("leave_free_mb", "Leave free on the card in MB", 0, 10000000, s.leave_free_mb),
      number("max_episode_mb", "Largest single episode in MB", 1, 4096, s.max_episode_mb),
    );
    box.append(check("keep_played", "Keep played episodes", s.keep_played), check("video", "Allow video enclosures", s.video));
    const save = el("button", { type: "button", className: "action-btn primary", textContent: "Save settings" });
    save.addEventListener("click", () => {
      const changes = { action: "settings" };
      for (const input of box.querySelectorAll("input[data-key]")) {
        changes[input.dataset.key] = input.type === "checkbox" ? input.checked : Number(input.value);
      }
      post(changes).then(() => say("Saved.")).catch((e) => say(e.message, true));
    });
    const checkNow = el("button", { type: "button", className: "action-btn", textContent: "Check now" });
    checkNow.addEventListener("click", () => post({ action: "update" }).then(() => say("Checking…")).catch((e) => say(e.message, true)));
    box.append(el("div", { className: "library-buttons" }, save, checkNow));
    return box;
  }

  function addForm() {
    const url = el("input", { type: "url", placeholder: "https://example.org/feed.xml", size: 40, spellcheck: false });
    const title = el("input", { type: "text", placeholder: "Name (optional)", size: 24 });
    const add = el("button", { type: "button", className: "action-btn primary", textContent: "Add feed" });
    add.addEventListener("click", () => {
      post({ action: "add", url: url.value.trim(), title: title.value.trim() })
        .then(() => {
          url.value = "";
          title.value = "";
          say("Added. It is checked on the next run, or with Check now.");
        })
        .catch((e) => say(e.message, true));
    });
    return el("div", { className: "library-buttons" }, url, title, add);
  }

  function feedList() {
    if (!snap.feeds.length) return el("p", { className: "setting-desc", textContent: "No feeds yet." });
    const table = el("table", { className: "admin-table" });
    table.append(
      el(
        "tr",
        {},
        el("th", { textContent: "Feed" }),
        el("th", { textContent: "Episodes" }),
        el("th", { textContent: "Size" }),
        el("th", { textContent: "Last check" }),
        el("th", { textContent: "" }),
      ),
    );
    for (const feed of snap.feeds) {
      const remove = el("button", { type: "button", className: "small", textContent: "Remove" });
      remove.addEventListener("click", async () => {
        if (!(await AW.confirmHere(`Remove ${feed.title}?`, { anchor: remove, label: "Remove" }))) return;
        const deleteFiles = await AW.confirmHere(
          "Delete its episodes from the box too? Choose Cancel to keep them on the box.",
          { anchor: remove, label: "Delete episodes" },
        );
        post({ action: "remove", id: feed.id, delete_files: deleteFiles }).catch((e) => say(e.message, true));
      });
      table.append(
        el(
          "tr",
          {},
          el("td", {}, el("span", { className: "setting-name", textContent: feed.title }), el("br"), el("span", { className: "setting-desc", textContent: feed.url })),
          el("td", { textContent: `${feed.episodes} kept of ${feed.listed}` }),
          el("td", { textContent: size(feed.size) }),
          el("td", {}, el("span", { textContent: when(feed.last_check) }), feed.last_error ? el("span", { className: "setting-desc bad", textContent: ` ${feed.last_error}` }) : null),
          el("td", {}, remove),
        ),
      );
    }
    return table;
  }

  function opml() {
    const text = el("textarea", { rows: 4, placeholder: "Paste an OPML file here", spellcheck: false });
    const doImport = el("button", { type: "button", className: "action-btn", textContent: "Import OPML" });
    doImport.addEventListener("click", () => {
      post({ action: "import", opml: text.value }).then((data) => say(`Imported. ${data.feeds.length} feeds now.`)).catch((e) => say(e.message, true));
    });
    const doExport = el("button", { type: "button", className: "action-btn", textContent: "Export OPML" });
    doExport.addEventListener("click", async () => {
      try {
        const data = await post({ action: "export" });
        text.value = data.opml || "";
        say("The OPML file is in the box below. Copy it.");
      } catch (e) {
        say(e.message, true);
      }
    });
    return el("div", {}, el("h3", { textContent: "OPML" }), text, el("div", { className: "library-buttons" }, doImport, doExport));
  }

  function draw() {
    if (!snap) return;
    const was = panel.querySelector("input:focus, textarea:focus");
    if (was) return; // do not redraw under a field being typed in: the next poll does
    panel.replaceChildren(
      el("p", { className: "setting-desc", textContent: `Using ${size(snap.used)} on the box; ${snap.free_mb} MB free.` }),
      note,
      settingsForm(),
      el("h3", { textContent: "Feeds" }),
      addForm(),
      feedList(),
      opml(),
      snap.running ? el("p", { className: "setting-desc", textContent: "The librarian is working…" }) : null,
    );
  }

  function poll() {
    get()
      .then((data) => {
        snap = data;
        if (data.running) setTimeout(poll, POLL);
        else draw();
      })
      .catch(() => {});
  }

  get().then((data) => {
    snap = data;
    draw();
    if (data.running) setTimeout(poll, POLL);
  }).catch((e) => panel.replaceChildren(el("p", { className: "setting-desc bad", textContent: e.message })));
})();
