// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin's Library drive part: root chooses and mounts the drive; this page asks for a UUID and
// where each kind is kept, and shows what root recorded.
(() => {
  const panel = document.getElementById("library-drive-panel");
  if (!panel) return;
  let snap = null;

  const el = (tag, props = {}, ...kids) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  };
  const gb = (n) => `${(n / (1024 ** 3)).toFixed(1)} GB`;
  const note = el("p", { className: "setting-desc action-note", role: "status", hidden: true });
  const say = (text, bad = false) => {
    note.hidden = !text;
    note.textContent = text;
    note.classList.toggle("bad", bad);
  };

  async function get() {
    const response = await fetch("/admin/librarydrive");
    if (!response.ok) throw new Error(`the box answered ${response.status}`);
    return response.json();
  }

  async function post(body) {
    const response = await fetch("/admin/librarydrive", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Irate-Admin": "1" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `the box answered ${response.status}`);
    say("The box is applying it…");
    setTimeout(poll, 1500);
    return data;
  }

  function render() {
    if (!snap) return;
    const uuid = el("input", { type: "text", value: snap.uuid || "", size: 44, spellcheck: false, placeholder: "filesystem UUID" });
    const drive = snap.drives.length
      ? el(
          "ul",
          { className: "setting-desc" },
          ...snap.drives.map((d) => el("li", { textContent: `${d.device || "drive"} · ${d.fstype || ""} · ${d.mount || ""} · ${gb(d.total || 0)} (${gb(d.free || 0)} free)` })),
        )
      : el("p", { className: "setting-desc", textContent: "No USB drive is mounted now." });
    const kinds = el("div", { className: "library-buttons" });
    for (const kind of ["podcasts", "books"]) {
      const select = el("select", { dataset: { kind } });
      for (const place of ["card", "drive"]) {
        const option = el("option", { value: place, textContent: place === "card" ? "the card" : "the library drive" });
        if ((snap.kinds || {})[kind] === place) option.selected = true;
        select.append(option);
      }
      kinds.append(el("label", { className: "setting" }, el("span", { className: "setting-name", textContent: kind }), select));
    }
    const save = el("button", { type: "button", className: "action-btn primary", textContent: "Save drive and places" });
    save.addEventListener("click", () => {
      const chosen = {};
      for (const select of kinds.querySelectorAll("select[data-kind]")) chosen[select.dataset.kind] = select.value;
      post({ action: "choose", uuid: uuid.value.trim(), kinds: chosen }).catch((e) => say(e.message, true));
    });
    const none = el("button", { type: "button", className: "action-btn", textContent: "No library drive" });
    none.addEventListener("click", () => post({ action: "choose", uuid: "", kinds: { podcasts: "card", books: "card" } }).catch((e) => say(e.message, true)));
    const eject = el("button", { type: "button", className: "action-btn", textContent: "Eject" });
    eject.addEventListener("click", () => post({ action: "eject" }).catch((e) => say(e.message, true)));
    const state = snap.uuid
      ? `Using ${snap.uuid}${snap.mounted ? `, mounted (${gb(snap.total || 0)}, ${gb(snap.free || 0)} free)` : ", not plugged in"}.`
      : "No library drive is set.";
    panel.replaceChildren(
      el("p", { className: "setting-desc", textContent: state }),
      note,
      el("h3", { textContent: "Drives root sees" }),
      drive,
      el("h3", { textContent: "Where each kind is kept" }),
      kinds,
      el("div", { className: "library-buttons" }, uuid, save, none, eject),
      el("p", { className: "setting-desc", textContent: "ext4 is recommended: exFAT and FAT have no hard links (the books' archive needs them) and no owners." }),
    );
  }

  function poll() {
    get()
      .then((data) => {
        snap = data;
        const working = (data.pending || []).length > 0;
        render();
        if (working) setTimeout(poll, 2000);
        else if ((data.results || []).length) {
          const last = data.results[0];
          if (last && last.message) say(last.message, !last.ok);
        }
      })
      .catch((e) => say(e.message, true));
  }

  get().then((data) => {
    snap = data;
    render();
  }).catch((e) => panel.replaceChildren(el("p", { className: "setting-desc bad", textContent: e.message })));
})();
