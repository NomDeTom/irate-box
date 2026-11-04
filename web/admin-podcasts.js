// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin's Podcasts part: the settings, the feeds and OPML. The server runs the checks; the page
// asks and redraws, as the other Library parts do.
(() => {
  const panel = document.getElementById("podcasts-panel");
  if (!panel) return;
  const POLL = 3000;
  let snap = null;

  const el = AW.el;
  const size = (n) => (n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`);
  const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "never");
  const note = el("p", { className: "setting-desc action-note", role: "status", hidden: true });

  // A fault in this page's own script is not the box talking: say so plainly, and keep the box's own words.
  const fault = (e) => {
    const text = e && e.message ? String(e.message) : String(e);
    return /^the box answered/i.test(text)
      ? text
      : "This part of the page could not be drawn: a fault in the page, not the box. Reload; if it stays, it is a bug.";
  };
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

  function settingsForm() {
    const form = el("form", { className: "library-form" });
    const box = el("div", { className: "settings" });
    form.append(box);
    const numberRow = (name, label, min, max, value) => {
      const input = el("input", { name, type: "number", min, max, value: String(value) });
      box.append(el("label", { className: "aw-field" }, el("span", { className: "aw-label", textContent: label }), input));
    };
    const toggleRow = (name, label, value) => {
      const input = el("input", { name, type: "checkbox", checked: !!value });
      box.append(el("label", { className: "aw-field podcast-toggle" }, input, el("span", { className: "aw-label", textContent: label })));
    };
    const settings = snap.settings;
    toggleRow("enabled", "Follow podcasts", settings.enabled);
    const often = el("input", { type: "hidden", name: "check_every_hours", value: String(settings.check_every_hours) });
    const intervals = [["0", "Manual"], ["6", "6 hours"], ["24", "24 hours"], ["168", "Weekly"]];
    const intervalChips = el("div", { className: "chip-group", role: "radiogroup", "aria-label": "Check interval" });
    const drawInterval = () => intervalChips.replaceChildren(...intervals.map(([value, text]) => {
      const chip = el("button", {
        type: "button",
        className: `chip${often.value === value ? " selected" : ""}`,
        role: "radio",
        "aria-checked": String(often.value === value),
        textContent: text,
      });
      chip.addEventListener("click", () => {
        often.value = value;
        drawInterval();
        often.dispatchEvent(new Event("change", { bubbles: true }));
      });
      return chip;
    }));
    drawInterval();
    box.append(el("div", { className: "aw-field" }, el("span", { className: "aw-label", textContent: "Check interval" }), often, intervalChips));
    numberRow("back_catalogue", "New feed: episodes to download (-1 all)", -1, 1000, settings.back_catalogue);
    numberRow("keep_count", "Keep per feed, by count (0 no limit)", 0, 500, settings.keep_count);
    numberRow("keep_days", "Keep per feed, by age in days (0 no limit)", 0, 3650, settings.keep_days);
    numberRow("keep_mb", "Keep per feed, by size in MB (0 no limit)", 0, 10000000, settings.keep_mb);
    numberRow("cap_mb", "All podcasts' cap in MB (0 no cap)", 0, 10000000, settings.cap_mb);
    numberRow("leave_free_mb", "Leave free on the card in MB", 0, 10000000, settings.leave_free_mb);
    numberRow("max_episode_mb", "Largest single episode in MB", 1, 4096, settings.max_episode_mb);
    toggleRow("keep_played", "Keep played episodes", settings.keep_played);
    toggleRow("video", "Allow video enclosures", settings.video);
    const sec = AW.form(form, {
      label: "Podcast settings",
      save: async (changes) => {
        const body = { action: "settings" };
        for (const [key, value] of Object.entries(changes)) body[key] = typeof value === "boolean" ? value : Number(value);
        await post(body);
        return "Saved.";
      },
    });
    sec.fill({
      enabled: settings.enabled,
      check_every_hours: settings.check_every_hours,
      back_catalogue: settings.back_catalogue,
      keep_count: settings.keep_count,
      keep_days: settings.keep_days,
      keep_mb: settings.keep_mb,
      cap_mb: settings.cap_mb,
      leave_free_mb: settings.leave_free_mb,
      max_episode_mb: settings.max_episode_mb,
      keep_played: settings.keep_played,
      video: settings.video,
    });
    return form;
  }

  // Each feed is the same source card as the other subscriptions: a card, or a short line from ten feeds.
  function feedPill(feed) {
    if (feed.last_error) return AW.pill("check failed", "bad");
    if (!feed.last_check) return AW.pill("not checked yet", "info");
    return AW.pill("checked", "ok");
  }
  function feedBody(feed) {
    const remove = el("button", { type: "button", className: "small", textContent: "Remove feed" });
    remove.addEventListener("click", async () => {
      if (!(await AW.confirmHere(`Remove ${feed.title}?`, { anchor: remove, label: "Remove" }))) return;
      const deleteFiles = await AW.confirmHere(
        "Delete its episodes from the box too? Choose Cancel to keep them on the box.",
        { anchor: remove, label: "Delete episodes" },
      );
      post({ action: "remove", id: feed.id, delete_files: deleteFiles }).catch((e) => say(e.message, true));
    });
    return [
      el("p", { className: "setting-desc", textContent: feed.url }),
      el("p", { className: "setting-desc", textContent: `${feed.episodes} kept of ${feed.listed} episodes; ${size(feed.size)}.` }),
      feed.last_check ? el("p", { className: "setting-desc", textContent: `Last checked ${when(feed.last_check)}.` }) : null,
      feed.last_error ? el("p", { className: "setting-desc bad", textContent: feed.last_error }) : null,
      el("p", { className: "library-buttons source-danger" }, remove),
    ];
  }
  function feedCard(feed) {
    return AW.sourceCard({
      id: `feed-${feed.id}`,
      title: feed.title,
      source: feed.url,
      pill: feedPill(feed),
      state: feed.last_check ? `Checked ${when(feed.last_check)} · ${feed.episodes} of ${feed.listed} kept` : "Not checked yet",
      facts: [`${feed.episodes} of ${feed.listed} episodes`, size(feed.size), feed.last_error ? "check failed" : "up to date"],
      actions: [actionButton("Check now", () => post({ action: "update" }).then(() => say("Checking…")).catch((e) => say(e.message, true)), { className: "small" })],
      body: feedBody(feed),
    });
  }
  function addCard() {
    const url = el("input", { type: "url", placeholder: "https://example.org/feed.xml", spellcheck: false });
    const title = el("input", { type: "text", placeholder: "Name (optional)" });
    const add = el("button", { type: "button", className: "action-btn primary", textContent: "Add feed" });
    add.addEventListener("click", () => {
      post({ action: "add", url: url.value.trim(), title: title.value.trim() })
        .then(() => {
          url.value = "";
          title.value = "";
          say("Added. It is checked on the next run, or with Check now.");
        })
        .catch((e) => say(fault(e), true));
    });
    return AW.sourceCard({
      id: "podcast-add",
      title: "Add a feed",
      source: "an RSS or Atom address",
      state: "URL and an optional name",
      facts: ["Schedule: shared"],
      body: [
        el("label", { className: "aw-field" }, el("span", { className: "aw-label", textContent: "Feed URL" }), url),
        el("label", { className: "aw-field" }, el("span", { className: "aw-label", textContent: "Name" }), title),
        note,
        el("p", { className: "library-buttons" }, add),
      ],
      actions: [AW.enterButton()],
    });
  }
  function feedView() {
    const feeds = snap.feeds;
    const add = addCard();
    if (feeds.length > 9) {
      const items = feeds.map((feed) => ({
        id: `feed-${feed.id}`,
        title: feed.title,
        summary: feed.url,
        pill: feedPill(feed),
        badges: [feed.last_error ? "check failed" : "up to date"],
        actions: [actionButton("Check now", () => post({ action: "update" }).then(() => say("Checking…")).catch((e) => say(e.message, true)), { className: "small" })],
        body: feedBody(feed),
      }));
      return [
        AW.sourceList(items, { id: "podcast-feeds" }),
        el("div", { className: "card-grid source-grid" }, add),
      ];
    }
    return [el("div", { className: "card-grid source-grid" }, ...feeds.map(feedCard), add)];
  }

  function opml() {
    const text = el("textarea", { rows: 4, placeholder: "Paste an OPML file here", spellcheck: false });
    const doImport = el("button", { type: "button", className: "action-btn", textContent: "Import OPML" });
    doImport.addEventListener("click", () => {
      post({ action: "import", opml: text.value }).then((data) => say(`Imported. ${data.feeds.length} feeds now.`)).catch((e) => say(fault(e), true));
    });
    const doExport = el("button", { type: "button", className: "action-btn", textContent: "Export OPML" });
    doExport.addEventListener("click", async () => {
      try {
        const data = await post({ action: "export" });
        text.value = data.opml || "";
        say("The OPML file is in the box below. Copy it.");
      } catch (e) {
        say(fault(e), true);
      }
    });
    return el("div", { className: "opml" }, el("h3", { textContent: "OPML" }), text, el("div", { className: "library-buttons" }, doImport, doExport));
  }

  function draw() {
    if (!snap) return;
    const was = panel.querySelector("input:focus, textarea:focus");
    if (was) return; // do not redraw under a field being typed in: the next poll does
    note.hidden = true;
    panel.replaceChildren(
      el("p", { className: "setting-desc", textContent: `Using ${size(snap.used)} on the box; ${snap.free_mb} MB free.` }),
      settingsForm(),
      el("h3", { textContent: "Feeds" }),
      ...feedView(),
      el("p", { className: "library-actions" },
        actionButton("Check all now", () => post({ action: "update" }).then(() => say("Checking…")).catch((e) => say(e.message, true)))),
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
  }).catch((e) => panel.replaceChildren(el("p", { className: "setting-desc bad", textContent: fault(e) })));
})();
