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
  let theirs = null;
  const conflict = document.createElement("div");
  conflict.className = "library-buttons";
  conflict.hidden = true;
  const conflictSay = document.createElement("span");
  conflictSay.className = "setting-desc bad";
  const useTheirs = document.createElement("button");
  useTheirs.type = "button";
  useTheirs.className = "action-btn";
  useTheirs.textContent = "Use theirs";
  const copyMine = document.createElement("button");
  copyMine.type = "button";
  copyMine.className = "action-btn";
  copyMine.textContent = "Copy mine";
  conflict.append(conflictSay, useTheirs, copyMine);
  saveButton.closest(".library-buttons").after(conflict);

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
    const body = JSON.stringify({ text: area.value });
    const headers = { "Content-Type": "application/json" };
    if (etag) headers["If-Match"] = etag;
    else headers["If-None-Match"] = "*";
    pending = fetch(url, { method: "PUT", headers, body })
      .then(async (response) => {
        if (response.status === 412) {
          const now = await fetch(url);
          theirs = now.ok ? await now.json() : null;
          conflictSay.textContent = "Another person saved this page first. Your words are still here.";
          conflict.hidden = false;
          saveButton.disabled = false;
          setState("Not saved: another person saved first.");
          return;
        }
        if (!response.ok) throw new Error(`the box answered ${response.status}`);
        etag = response.headers.get("ETag") || "";
        dirty = false;
        conflict.hidden = true;
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

  useTheirs.addEventListener("click", () => {
    if (!theirs) return;
    area.value = typeof theirs.text === "string" ? theirs.text : "";
    dirty = false;
    conflict.hidden = true;
    saveButton.disabled = true;
    etag = "";
    setState("Their words are here. Save writes yours over them.");
  });
  copyMine.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(area.value);
      setState("Your words are on the clipboard.");
    } catch (_) {
      setState("Could not copy. Select the words and copy them.");
    }
  });
  area.addEventListener("input", () => {
    conflict.hidden = true;
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
