// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Locks on this device: what this browser holds keys for (lock.js keeps them as
// "hublock:<kind>:<id>" in localStorage), matched against what is on the hub now. A key can be
// shown as text and a QR code to carry it to another device, and added from one. Keys are only
// ever shown on this page, never sent anywhere.
(() => {
  const list = document.getElementById('locks-list');
  const input = document.getElementById('locks-import');
  const note = document.getElementById('locks-note');
  const KINDS = { saves: 'Saved drawing', drop: 'Uploaded file' };

  function held() {
    const out = [];
    for (let i = 0; i < localStorage.length; i++) {
      const m = /^hublock:(saves|drop):([A-Za-z0-9_-]{1,64})$/.exec(localStorage.key(i));
      if (m) out.push({ kind: m[1], id: m[2] });
    }
    return out;
  }

  const keyText = (kind, id) => `hublock:${kind}:${id}:${HubLock.exportKey(kind, id)}`;

  function tell(text, ok) {
    note.hidden = !text;
    note.textContent = text;
    note.classList.toggle('bad', !ok);
  }

  function el(tag, props = {}, ...kids) {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  }

  function qr(text) {
    const q = qrcode(0, 'M');
    q.addData(text);
    q.make();
    const box = el('div', { className: 'locks-qr' });
    box.innerHTML = q.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); // the library's own SVG: no user text in it
    return box;
  }

  async function onHub() {
    const out = { saves: {}, drop: {} };
    try {
      const s = await (await fetch('/api/saves')).json();
      for (const x of s.saves) out.saves[x.id] = x;
    } catch (_) { /* hub not answering: shown as unknown */ }
    try {
      const data = await (await fetch('/api/drop')).json();
      for (const x of data.files) out.drop[x.id] = x;
    } catch (_) { /* same */ }
    return out;
  }

  async function render() {
    const locks = held();
    if (!locks.length) {
      list.replaceChildren(el('li', { className: 'setting-desc', textContent: 'This browser holds no keys.' }));
      return;
    }
    const hub = await onHub();
    list.replaceChildren(...locks.map(({ kind, id }) => {
      const item = hub[kind][id];
      const there = item && item.locked;
      const name = item ? item.name : '(no longer on the box)';
      const detail = !item ? 'The item expired or was deleted. This key no longer works. Remove it from this browser.'
        : !item.locked ? 'The item is on the box. It is no longer locked.'
          : `${KINDS[kind]}, locked. This key can make ${item.lock_n} more changes. It renews itself before the end.`;
      const row = el('li', { className: 'drop-item' }, el('span', { className: 'setting-name', textContent: `${there ? '🔒 ' : ''}${name}` }),
        el('span', { className: 'setting-desc', textContent: ` ${detail}` }));
      const keyBox = el('div', { hidden: true });
      const show = el('button', { type: 'button', className: 'small', textContent: 'Show key' });
      show.addEventListener('click', () => {
        if (!keyBox.hidden) { keyBox.hidden = true; keyBox.replaceChildren(); show.textContent = 'Show key'; return; }
        const text = keyText(kind, id);
        const field = el('input', { value: text, readOnly: true, size: 40, spellcheck: false });
        field.addEventListener('focus', () => field.select());
        keyBox.replaceChildren(qr(text), field,
          el('p', { className: 'setting-desc', textContent: 'Scan or copy this key on the other device. Then add it on this page there. One key allows about 4000 changes. After about 4000 changes from one device, the key renews on that device. Then the other device needs the new key.' }));
        keyBox.hidden = false;
        show.textContent = 'Hide key';
      });
      const forget = el('button', { type: 'button', className: 'small', textContent: 'Remove key from this browser' });
      forget.addEventListener('click', async () => {
        if (there && !(await askHere(`Remove the key for "${name}" from this browser? This browser can then no longer change or delete the item. Other devices that hold the key can still change or delete it. The administrator can also delete it.`, { anchor: forget, label: 'Remove key' }))) return;
        HubLock.forget(kind, id);
        render();
      });
      row.append(' ', ...(HubLock.exportKey(kind, id) ? [show, ' '] : []), forget, keyBox);
      return row;
    }));
  }

  document.getElementById('locks-add').addEventListener('click', () => {
    const m = /^hublock:(saves|drop):([A-Za-z0-9_-]{1,64}):([0-9a-fA-F]{64})$/.exec(input.value.trim());
    if (!m) { tell('This is not a key. A key has this format: hublock:<kind>:<id>:<64 hex digits>.', false); return; }
    HubLock.importKey(m[1], m[2], m[3]);
    input.value = '';
    tell('Key added. This browser can now change or delete the item.', true);
    render();
  });

  render();
})();
