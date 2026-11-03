// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// A bridge on the hub's own origin: an add-on opens it and asks, and this page does the store or
// drop work as the visitor. It answers the add-on's origin only, and only what the visitor picks.
(() => {
  const say = document.getElementById("addon-bridge-say");
  const body = document.getElementById("addon-bridge-body");
  let addonOrigin = null;
  let pendingTitle = "An add-on";

  const el = (tag, props = {}, ...kids) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  };
  const tell = (text, bad = false) => {
    say.hidden = false;
    say.textContent = text;
    say.classList.toggle("bad", bad);
  };
  const send = (message, transfer = []) => {
    if (window.opener && addonOrigin) window.opener.postMessage(message, addonOrigin, transfer);
  };
  const fail = (action, error) => send({ type: "irate-bridge-result", action, ok: false, error: String(error) });

  if (!window.opener) {
    tell("Open this page from an add-on. It does nothing on its own.", true);
    return;
  }

  function who(title) {
    return el("p", { className: "setting-name", textContent: String(title || "An add-on").slice(0, 80) });
  }

  function pick(list, label, onPick) {
    body.replaceChildren(
      who(pendingTitle),
      ...list.map((item) => {
        const button = el("button", { type: "button", className: "action-btn" });
        if (item.thumb) {
          const img = el("img", { src: item.thumb, alt: "", width: 96, height: 72, loading: "lazy" });
          button.append(img, el("br"));
        }
        button.append(document.createTextNode(item.label));
        button.addEventListener("click", () => onPick(item, button));
        return el("div", { className: "library-buttons" }, button);
      }),
    );
    if (!list.length) tell(label);
  }

  function offerSave(payload) {
    const name = el("input", { type: "text", value: payload.name || "Picture", size: 30 });
    const view = payload.thumb ? el("img", { src: payload.thumb, alt: "", width: 160 }) : null;
    const save = el("button", { type: "button", className: "action-btn primary", textContent: "Save in the box's gallery" });
    save.addEventListener("click", async () => {
      save.disabled = true;
      const headers = { "Content-Type": "application/json" };
      let id = payload.id || null;
      const wanted = id;
      let change = null;
      let fresh = null;
      if (id && window.HubLock && HubLock.has("saves", id)) {
        change = HubLock.change("saves", id, payload.lock_n || 0);
      }
      if (!change) {
        id = null;
        if (window.HubLock) fresh = HubLock.create();
      }
      if (change) Object.assign(headers, change.headers);
      if (fresh) headers["X-Lock-New"] = fresh.header;
      try {
        const response = await fetch("/api/saves", {
          method: "POST",
          headers,
          body: JSON.stringify({ kind: "jspaint", id: id || undefined, name: name.value.trim() || "Picture", state: payload.state, thumb: payload.thumb }),
        });
        if (!response.ok) throw new Error(`the gallery answered ${response.status}`);
        const meta = await response.json();
        if (change) change.done();
        if (fresh) HubLock.keep("saves", meta.id, fresh.seed);
        send({
          type: "irate-bridge-result",
          action: "save",
          ok: true,
          save: {
            id: meta.id,
            name: name.value.trim() || "Picture",
            locked: !!meta.locked,
            lock_n: meta.lock_n || 0,
            copied: !!wanted && !id,
          },
        });
        tell("Saved in the box's gallery.");
        window.setTimeout(() => window.close(), 400);
      } catch (error) {
        save.disabled = false;
        tell(`Not saved: ${error.message}`, true);
        fail("save", error.message);
      }
    });
    body.replaceChildren(who(pendingTitle), name, view || el("span"), el("div", { className: "library-buttons" }, save));
    tell("Save this picture in the box's gallery?");
  }

  async function offerOpen() {
    tell("Reading the gallery…");
    try {
      const response = await fetch("/api/saves");
      if (!response.ok) throw new Error(`the gallery answered ${response.status}`);
      const data = await response.json();
      const saves = (data.saves || []).filter((save) => save.kind === "jspaint");
      if (!saves.length) {
        tell("No picture is saved in this box's gallery yet.");
        return;
      }
      pick(
        saves.map((save) => ({ ...save, label: save.name || "untitled", thumb: `/api/saves/${encodeURIComponent(save.id)}/thumb` })),
        "No picture is saved in this box's gallery yet.",
        async (save, button) => {
          button.disabled = true;
          try {
            const whole = await fetch(`/api/saves/${encodeURIComponent(save.id)}`);
            if (!whole.ok) throw new Error(`the gallery answered ${whole.status}`);
            const full = await whole.json();
            send({
              type: "irate-bridge-result",
              action: "open",
              ok: true,
              save: { id: save.id, name: save.name || "Picture", state: full.state, locked: !!full.locked, lock_n: full.lock_n || 0 },
            });
            window.setTimeout(() => window.close(), 400);
          } catch (error) {
            button.disabled = false;
            tell(`Not opened: ${error.message}`, true);
            fail("open", error.message);
          }
        },
      );
      tell("Choose a picture.");
    } catch (error) {
      tell(`The gallery could not be read: ${error.message}`, true);
      fail("open", error.message);
    }
  }

  async function offerPdf() {
    tell("Reading the file drop…");
    try {
      const response = await fetch("/api/drop");
      if (!response.ok) throw new Error(`the box answered ${response.status}`);
      const data = await response.json();
      const pdfs = (data.files || []).filter((file) => /\.pdf$/i.test(file.name));
      if (!pdfs.length) {
        tell("No PDF is in the file drop yet.");
        return;
      }
      pick(
        pdfs.map((file) => ({ ...file, label: file.name })),
        "No PDF is in the file drop yet.",
        async (file, button) => {
          button.disabled = true;
          try {
            const whole = await fetch(`/api/drop/${encodeURIComponent(file.id)}`);
            if (!whole.ok) throw new Error(`the box answered ${whole.status}`);
            const buffer = await whole.arrayBuffer();
            send(
              { type: "irate-bridge-result", action: "pdf", ok: true, file: { name: file.name, data: buffer } },
              [buffer],
            );
            window.setTimeout(() => window.close(), 400);
          } catch (error) {
            button.disabled = false;
            tell(`Not opened: ${error.message}`, true);
            fail("pdf", error.message);
          }
        },
      );
      tell("Choose a PDF.");
    } catch (error) {
      tell(`The file drop could not be read: ${error.message}`, true);
      fail("pdf", error.message);
    }
  }

  window.addEventListener("message", (event) => {
    if (!addonOrigin || event.origin !== addonOrigin || event.source !== window.opener) return;
    const data = event.data;
    if (!data || data.type !== "irate-bridge-request") return;
    pendingTitle = data.payload && data.payload.title ? data.payload.title : "An add-on";
    if (data.action === "save") offerSave(data.payload || {});
    else if (data.action === "open") offerOpen();
    else if (data.action === "pdf") offerPdf();
  });

  // The hub serves its own add-on origin (the scheme, host and port /addons/<id>/ goes to). No
  // Referer is trusted: the add-on server sends none, and any page could open this bridge.
  fetch("/api/addons/origin")
    .then((response) => {
      if (!response.ok) throw new Error(`the box answered ${response.status}`);
      return response.json();
    })
    .then((data) => {
      if (!data || typeof data.origin !== "string") throw new Error("the box named no add-on origin");
      addonOrigin = data.origin;
      send({ type: "irate-bridge-ready" });
    })
    .catch((error) => tell(`The hub did not name the add-on origin: ${error.message}`, true));
})();
