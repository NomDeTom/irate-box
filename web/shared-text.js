// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// One shared text per namespace (store.py's notepad and pastebin), polled as the shoutbox is: everyone
// who can open the page reads the same document and writes it. A newer copy on the box is never
// written over while this browser has unsaved words.
(() => {
  const namespace = document.body.dataset.shared;
  const url = `/api/v2/${namespace}/text`;
  const area = document.getElementById("shared-text");
  const saveButton = document.getElementById("shared-save");
  const state = document.getElementById("shared-state");
  const POLL_MS = 4000;
  let etag = "";
  let dirty = false;
  let pending = null;

  const setState = (text) => {
    state.textContent = text;
  };

  async function load(first = false) {
    if (dirty && !first) return; // unsaved words here: do not read over them
    try {
      const response = await fetch(url, { headers: etag ? { "If-None-Match": etag } : {} });
      if (response.status === 304) return;
      if (response.status === 404) {
        if (first) {
          area.value = "";
          setState("Nothing here yet. Start typing.");
        }
        return;
      }
      if (!response.ok) throw new Error(`the box answered ${response.status}`);
      const data = await response.json();
      etag = response.headers.get("ETag") || "";
      if (dirty) {
        setState("The box has a newer copy. Your words here are not saved; Save writes them over it.");
        return;
      }
      area.value = typeof data.text === "string" ? data.text : "";
      setState(data.updated ? `Last saved ${new Date(data.updated * 1000).toLocaleString()}.` : "Nothing here yet. Start typing.");
      saveButton.disabled = true;
    } catch (error) {
      setState(`The box did not answer: ${error.message}`);
    }
  }

  async function save() {
    if (pending) return;
    saveButton.disabled = true;
    setState("Saving…");
    const body = JSON.stringify({ text: area.value, updated: Math.floor(Date.now() / 1000) });
    pending = fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body,
    })
      .then((response) => {
        if (!response.ok) throw new Error(`the box answered ${response.status}`);
        etag = "";
        dirty = false;
        setState("Saved.");
        return load();
      })
      .catch((error) => {
        dirty = true;
        saveButton.disabled = false;
        setState(`Not saved: ${error.message}`);
      })
      .finally(() => {
        pending = null;
      });
    return pending;
  }

  area.addEventListener("input", () => {
    dirty = true;
    saveButton.disabled = false;
    setState("Unsaved.");
  });
  saveButton.addEventListener("click", () => save());
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "s") {
      event.preventDefault();
      save();
    }
  });
  load(true);
  setInterval(() => load(false), POLL_MS);
})();
