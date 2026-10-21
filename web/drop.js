// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The file drop: upload with a bar per file (XHR, which reports upload progress where fetch
// does not), and the list of what is on the box. Downloads are plain links: the hub serves
// every file as an attachment, so nothing here is ever opened as a page.
// In a function of its own: hub.js, loaded first, has globals of its own (picker, …).
(() => {
  const list = document.getElementById('drop-list');
  const uploads = document.getElementById('drop-uploads');
  const picker = document.getElementById('drop-files');
  const zone = document.getElementById('drop-zone');
  const by = document.getElementById('drop-by');
  const limits = document.getElementById('drop-limits');
  const lockBox = document.getElementById('drop-lock');
  lockBox.checked = localStorage.getItem('drop-lock') === '1';
  // Signed in, and no selection made in this browser yet: the account's own default.
  if (localStorage.getItem('drop-lock') === null) {
    fetch('/api/account').then((response) => (response.ok ? response.json() : null)).then((d) => {
      if (d && d.prefs && localStorage.getItem('drop-lock') === null) lockBox.checked = !!d.prefs.lock_default;
    }).catch(() => {});
  }
  lockBox.addEventListener('change', () => localStorage.setItem('drop-lock', lockBox.checked ? '1' : '0'));
  let maxFile = 25 * 2 ** 20;
  const notDeleted = {}; // file id → why the last Delete failed, said under its row until it succeeds

  by.value = localStorage.getItem('shout-name') || '';
  by.addEventListener('input', () => localStorage.setItem('shout-name', by.value));

  const size = (b) => (b >= 2 ** 20 ? `${(b / 2 ** 20).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

  function li(cls, ...kids) {
    const node = document.createElement('li');
    node.className = cls;
    node.append(...kids);
    return node;
  }

  function span(cls, text) {
    const node = document.createElement('span');
    node.className = cls;
    node.textContent = text;
    return node;
  }

  async function refresh() {
    if (list.querySelector('.aw-ask')) return; // a question is open in the list: the next poll draws it again
    try {
      const response = await fetch('/api/drop');
      const data = await response.json();
      maxFile = data.max_file;
      limits.textContent = `Everyone who can open this page can download these files. Maximum file size: ${size(data.max_file)}. ` +
        (data.ttl ? `Retention period: ${Math.round(data.ttl / 3600)} hours of powered-on time. ` : '') +
        'The box deletes the oldest files when the storage limit is reached.';
      list.replaceChildren(...(data.files.length ? data.files.map((f) => {
        const a = document.createElement('a');
        a.href = `/api/drop/${encodeURIComponent(f.id)}`;
        a.textContent = f.name;
        a.setAttribute('download', f.name);
        const row = li('drop-item', f.pinned ? span('drop-locked', '📌 ') : f.locked ? span('drop-locked', '🔒 ') : '', a,
          span('setting-desc', ` ${size(f.size)} · ${f.pinned ? 'the source of this box (see About)' : `${f.by ? `${f.by} · ` : ''}${formatAge(f.age)}`}`));
        // Anyone can report a file to the administrator (not the box's own source).
        if (!f.pinned && typeof reportButton === 'function') row.append(' ', reportButton('drop', { id: f.id }, data.report_reasons));
        if (f.locked && window.HubLock && HubLock.has('drop', f.id)) {
          const rm = document.createElement('button');
          rm.type = 'button';
          rm.className = 'small';
          rm.textContent = 'Delete file';
          rm.title = 'This browser holds the key. It can delete the file before the file expires.';
          rm.addEventListener('click', () => removeMine(f, rm));
          row.append(' ', rm);
        }
        if (notDeleted[f.id]) row.append(span('setting-desc bad', ` ${notDeleted[f.id]}`));
        return row;
      }) : [li('setting-desc', 'No files')]));
    } catch (_) {
      list.replaceChildren(li('setting-desc', 'Could not reach the box.'));
    }
  }

  async function removeMine(f, button) {
    if (!(await askHere(`Delete ${f.name} from the box? This cannot be undone.`, { anchor: button, label: 'Delete' }))) return;
    const c = HubLock.change('drop', f.id, f.lock_n);
    if (!c) return;
    button.disabled = true;
    try {
      const response = await fetch(`/api/drop/${encodeURIComponent(f.id)}`, { method: 'DELETE', headers: c.headers });
      if (response.ok) {
        HubLock.forget('drop', f.id);
        delete notDeleted[f.id];
      } else {
        const data = await response.json().catch(() => ({}));
        notDeleted[f.id] = `File not deleted: ${data.error || `error ${response.status}`}`;
      }
    } catch (_) {
      notDeleted[f.id] = 'File not deleted: the connection dropped.';
    }
    refresh();
  }

  function upload(file) {
    const bar = document.createElement('progress');
    bar.className = 'admin-bar';
    bar.max = file.size || 1;
    bar.value = 0;
    const status = span('setting-desc', 'Waiting');
    const row = li('drop-upload', span('setting-name', file.name), status, bar);
    uploads.append(row);
    if (file.size > maxFile) {
      status.textContent = `Upload rejected: the file is larger than the maximum file size (${size(maxFile)}).`;
      status.classList.add('bad');
      bar.remove();
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/drop');
      xhr.setRequestHeader('X-Drop-Name', encodeURIComponent(file.name));
      xhr.setRequestHeader('X-Drop-By', encodeURIComponent(by.value.trim()));
      const lock = lockBox.checked && window.HubLock ? HubLock.create() : null;
      if (lock) xhr.setRequestHeader('X-Lock-New', lock.header);
      xhr.upload.onprogress = (event) => {
        bar.value = event.loaded;
        status.textContent = `${size(event.loaded)} of ${size(file.size)}`;
      };
      xhr.onload = () => {
        let msg = '';
        try { msg = JSON.parse(xhr.responseText).error || ''; } catch (_) { /* not JSON: the web server's 413 */ }
        if (xhr.status === 201) {
          if (lock) {
            try { HubLock.keep('drop', JSON.parse(xhr.responseText).id, lock.seed); } catch (_) { /* no id: nothing to keep */ }
          }
          status.textContent = lock ? `Upload complete: ${file.name}. Locked with a key in this browser.` : `Upload complete: ${file.name}.`;
          bar.value = bar.max;
          setTimeout(() => row.remove(), 3000);
        } else {
          status.textContent = xhr.status === 413 ? `Upload rejected: the file is larger than the maximum file size (${size(maxFile)}).` : `Upload failed: ${msg || `error ${xhr.status}`}`;
          status.classList.add('bad');
        }
        refresh();
        resolve();
      };
      xhr.onerror = () => { status.textContent = 'Upload failed: the connection dropped. Select the file again.'; status.classList.add('bad'); resolve(); };
      xhr.send(file);
    });
  }

  // One at a time: a phone on a busy hotspot does better with one upload than with five.
  async function uploadAll(files) {
    for (const f of files) await upload(f);
  }

  // The photo and voice buttons are the same upload; on a phone, capture opens the camera
  // or the recorder instead of the file picker (a desktop browser just picks a file).
  for (const input of [picker, document.getElementById('drop-photo'), document.getElementById('drop-voice')]) {
    input.addEventListener('change', () => { uploadAll([...input.files]); input.value = ''; });
  }
  ['dragenter', 'dragover'].forEach((t) => zone.addEventListener(t, (event) => { event.preventDefault(); zone.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((t) => zone.addEventListener(t, () => zone.classList.remove('over')));
  zone.addEventListener('drop', (event) => { event.preventDefault(); uploadAll([...event.dataTransfer.files]); });

  refresh();
  setInterval(refresh, 15000);
})();
