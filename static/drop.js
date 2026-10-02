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
  let maxFile = 25 * 2 ** 20;

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
    try {
      const r = await fetch('/api/drop');
      const data = await r.json();
      maxFile = data.max_file;
      limits.textContent = `Anyone on this network can download what is here. Up to ${size(data.max_file)} a file; ` +
        (data.ttl ? `each stays ${Math.round(data.ttl / 3600)} hours of the box being on, ` : '') +
        'and the oldest go first when the box is full.';
      list.replaceChildren(...(data.files.length ? data.files.map((f) => {
        const a = document.createElement('a');
        a.href = `/api/drop/${encodeURIComponent(f.id)}`;
        a.textContent = f.name;
        a.setAttribute('download', f.name);
        return li('drop-item', a, span('setting-desc', ` ${size(f.size)} · ${f.by ? `${f.by} · ` : ''}${formatAge(f.age)}`));
      }) : [li('setting-desc', 'Nothing here yet.')]));
    } catch (_) {
      list.replaceChildren(li('setting-desc', 'Could not reach the box.'));
    }
  }

  function upload(file) {
    const bar = document.createElement('progress');
    bar.className = 'admin-bar';
    bar.max = file.size || 1;
    bar.value = 0;
    const status = span('setting-desc', 'Waiting…');
    const row = li('drop-upload', span('setting-name', file.name), status, bar);
    uploads.append(row);
    if (file.size > maxFile) {
      status.textContent = `Too large: the limit is ${size(maxFile)}.`;
      status.classList.add('bad');
      bar.remove();
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/drop');
      xhr.setRequestHeader('X-Drop-Name', encodeURIComponent(file.name));
      xhr.setRequestHeader('X-Drop-By', encodeURIComponent(by.value.trim()));
      xhr.upload.onprogress = (e) => {
        bar.value = e.loaded;
        status.textContent = `${size(e.loaded)} of ${size(file.size)}`;
      };
      xhr.onload = () => {
        let msg = '';
        try { msg = JSON.parse(xhr.responseText).error || ''; } catch (_) { /* not JSON: the web server's 413 */ }
        if (xhr.status === 201) {
          status.textContent = 'Done.';
          bar.value = bar.max;
          setTimeout(() => row.remove(), 3000);
        } else {
          status.textContent = xhr.status === 413 ? `Too large: the limit is ${size(maxFile)}.` : `Not saved: ${msg || `error ${xhr.status}`}`;
          status.classList.add('bad');
        }
        refresh();
        resolve();
      };
      xhr.onerror = () => { status.textContent = 'Not saved: the connection dropped.'; status.classList.add('bad'); resolve(); };
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
  ['dragenter', 'dragover'].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((t) => zone.addEventListener(t, () => zone.classList.remove('over')));
  zone.addEventListener('drop', (e) => { e.preventDefault(); uploadAll([...e.dataTransfer.files]); });

  refresh();
  setInterval(refresh, 15000);
})();
