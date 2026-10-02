// Locks on this device: what this browser holds keys for (lock.js keeps them as
// "hublock:<kind>:<id>" in localStorage), matched against what is on the hub now. A key can be
// shown as text and a QR code to carry it to another device, and added from one. Keys are only
// ever shown on this page, never sent anywhere.
(() => {
  const list = document.getElementById('locks-list');
  const input = document.getElementById('locks-import');
  const note = document.getElementById('locks-note');
  const KINDS = { saves: 'Saved drawing', drop: 'Dropped file' };

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
      const d = await (await fetch('/api/drop')).json();
      for (const x of d.files) out.drop[x.id] = x;
    } catch (_) { /* same */ }
    return out;
  }

  async function render() {
    const locks = held();
    if (!locks.length) {
      list.replaceChildren(el('li', { className: 'setting-desc', textContent: 'This browser holds no locks.' }));
      return;
    }
    const hub = await onHub();
    list.replaceChildren(...locks.map(({ kind, id }) => {
      const item = hub[kind][id];
      const there = item && item.locked;
      const name = item ? item.name : '(no longer on the hub)';
      const detail = !item ? 'It expired or was removed, so this key no longer opens anything.'
        : !item.locked ? 'It is on the hub but no longer locked.'
          : `${KINDS[kind]}, locked (${item.lock_n} changes left on this key; it renews itself near the end).`;
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
          el('p', { className: 'setting-desc', textContent: 'Scan or copy this on the other device, then add it on this page there. If one device makes 60-odd changes, the key renews itself there and the other device needs it again.' }));
        keyBox.hidden = false;
        show.textContent = 'Hide key';
      });
      const forget = el('button', { type: 'button', className: 'small', textContent: 'Forget' });
      forget.addEventListener('click', () => {
        if (there && !confirm(`Forget the key for "${name}"? This device can then no longer change or remove it, and nobody else can either until it expires.`)) return;
        HubLock.forget(kind, id);
        render();
      });
      row.append(' ', ...(HubLock.exportKey(kind, id) ? [show, ' '] : []), forget, keyBox);
      return row;
    }));
  }

  document.getElementById('locks-add').addEventListener('click', () => {
    const m = /^hublock:(saves|drop):([A-Za-z0-9_-]{1,64}):([0-9a-fA-F]{64})$/.exec(input.value.trim());
    if (!m) { tell('That is not a key: it should look like hublock:<kind>:<id>:<64 hex digits>.', false); return; }
    HubLock.importKey(m[1], m[2], m[3]);
    input.value = '';
    tell('Added. This device can now change or remove that item.', true);
    render();
  });

  render();
})();
