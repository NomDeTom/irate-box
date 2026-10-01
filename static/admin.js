// Admin options. The gate is Caddy's basic_auth on /admin/* -- by the time this page
// loads, the operator has already authenticated. Each toggle saves on change; there is
// no Save button to forget to press.
const note = document.getElementById('save-note');
const boxes = document.querySelectorAll('.settings input[type="checkbox"]');

function say(text, ok) {
  note.textContent = text;
  note.classList.toggle('bad', !ok);
  note.hidden = false;
}

async function load() {
  try {
    const r = await fetch('/admin/settings');
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    boxes.forEach((b) => { if (typeof data[b.id] === 'boolean') b.checked = data[b.id]; });
  } catch (_) {
    say('Could not read the current settings.', false);
  }
}

async function save(box) {
  try {
    const r = await fetch('/admin/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [box.id]: box.checked }),
    });
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    // Show what the hub stored, not what we sent -- they differ if it rejected the value.
    boxes.forEach((b) => { if (typeof data[b.id] === 'boolean') b.checked = data[b.id]; });
    say('Saved. The landing page picks it up within about 15 seconds.', true);
  } catch (_) {
    box.checked = !box.checked;
    say('Could not save — the setting is unchanged.', false);
  }
}

boxes.forEach((b) => b.addEventListener('change', () => save(b)));
load();

// --- remote access --------------------------------------------------------------
// The hub only records the choice; a root path unit acts on it a moment later, so the
// state line is re-read a few times after each change rather than assumed.
const remote = document.getElementById('remote-section');
const radios = remote.querySelectorAll('input[name="remote"]');
const hours = document.getElementById('remote-hours');
const remoteState = document.getElementById('remote-state');
const STATE_TEXT = {
  running: 'Tailscale is running.',
  stopped: 'Tailscale is not running.',
};

function showRemote(data) {
  remote.hidden = data.state === 'missing';
  if (remote.hidden) return;
  radios.forEach((r) => { r.checked = r.value === data.want.mode; });
  if (data.want.mode === 'timed') hours.value = data.want.hours;
  hours.max = data.max_hours;
  remoteState.textContent = STATE_TEXT[data.state] || '';
}

async function loadRemote() {
  try {
    const r = await fetch('/admin/tailscale');
    if (!r.ok) throw new Error(r.status);
    showRemote(await r.json());
  } catch (_) {
    remote.hidden = true;
  }
}

async function saveRemote() {
  const mode = [...radios].find((r) => r.checked)?.value;
  if (!mode) return;
  const body = { mode };
  if (mode === 'timed') body.hours = Number(hours.value);
  try {
    const r = await fetch('/admin/tailscale', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(r.status);
    showRemote(await r.json());
    say('Saved. Tailscale follows within a few seconds.', true);
    [2000, 5000, 10000].forEach((ms) => setTimeout(loadRemote, ms));
  } catch (_) {
    say('Could not change remote access.', false);
    loadRemote();
  }
}

radios.forEach((r) => r.addEventListener('change', saveRemote));
hours.addEventListener('change', () => {
  const timed = [...radios].find((r) => r.value === 'timed');
  timed.checked = true;
  saveRemote();
});
loadRemote();

// --- library ---------------------------------------------------------------------
// The librarian (librarian.py) does the work; this page shows its snapshot and posts
// actions. Checks and downloads run in the background on the hub, so while one is going
// the page re-reads the snapshot every few seconds.
const lib = {
  state: document.getElementById('library-state'),
  sources: document.getElementById('library-sources'),
  add: document.getElementById('library-add'),
  policy: document.getElementById('library-policy'),
  token: document.getElementById('library-token'),
  tokenState: document.getElementById('library-token-state'),
  typeHelp: document.getElementById('library-type-help'),
};
const TYPE_HELP = {
  release: "The newest release with a matching asset. No token. Use this when the project itself publishes its ZIM.",
  actions: "The newest successful run's artifact. Needs the GitHub token below. Artifacts expire after 90 days, so the workflow must run at least that often.",
  'nightly-link': "The same artifacts, downloaded through the free nightly.link service, so no token is needed.",
  url: "A direct link to a .zim (or a .zip holding one). Updated when its ETag or Last-Modified changes.",
};
const PRESETS = {
  release: { type: 'release', name: 'project-docs', repo: 'owner/project', pattern: '*.zim' },
  actions: { type: 'actions', name: 'mermaid-docs', repo: 'NomDeTom/mermaid', workflow: 'docs-zim.yml', pattern: 'mermaid-docs-*' },
  'nightly-link': { type: 'nightly-link', name: 'meshtastic-docs', repo: 'NomDeTom/meshtasticDocs', workflow: 'docs-zim.yml', pattern: 'meshtastic-docs-*' },
};
let libPoll = null;

const el = (tag, props = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...kids.filter((k) => k !== null && k !== undefined));
  return node;
};
const mb = (bytes) => (bytes ? `${Math.round(bytes / 2 ** 20)} MB` : '');
const where = (s) => s.type === 'url' ? s.url
  : `${s.repo}${s.workflow ? ` · ${s.workflow}` : ''}${s.branch ? ` @ ${s.branch}` : ''} · ${s.pattern}`;

async function libPost(body) {
  const r = await fetch('/admin/library', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function sourceRow(src, st, busy) {
  const cur = st.current || {};
  const latest = st.latest || {};
  const archive = st.archive || [];
  const button = (label, body, confirmText) => el('button', {
    type: 'button', textContent: label, disabled: busy,
    onclick: async () => {
      if (confirmText && !confirm(confirmText)) return;
      try { renderLibrary(await libPost(body)); } catch (e) { say(e.message, false); }
    },
  });
  return el('div', { className: 'setting library-source' },
    el('span', {},
      el('span', { className: 'setting-name', textContent: `${src.name}.zim` }),
      el('span', { className: 'setting-desc', textContent: `${src.type}: ${where(src)}` }),
      el('span', { className: 'setting-desc',
        textContent: cur.version ? `Installed: ${cur.label || cur.version} (${mb(cur.size)}, ${cur.installed})` : 'Not installed by the librarian yet' }),
      latest.version && latest.version !== cur.version
        ? el('span', { className: 'setting-desc', textContent: `Available: ${latest.label} (${mb(latest.size)})` }) : null,
      archive.length ? el('span', { className: 'setting-desc', textContent: `Archived: ${archive.join(', ')}` }) : null,
      st.last_check ? el('span', { className: 'setting-desc', textContent: `Checked ${st.last_check}: ${st.outcome || ''}` }) : null,
      st.error ? el('span', { className: 'setting-desc bad', textContent: st.error }) : null,
      el('span', { className: 'library-buttons' },
        button('Check', { action: 'check', names: [src.name] }),
        button('Update', { action: 'update', names: [src.name] }),
        archive.length ? button('Roll back', { action: 'rollback', name: src.name },
          `Put ${archive[0]} back as ${src.name}.zim? The current version is archived.`) : null,
        button('Remove', { action: 'remove', name: src.name },
          `Stop tracking ${src.name}? The book itself stays in the library.`),
      ),
    ),
  );
}

function renderLibrary(snap) {
  const busy = snap.running;
  const job = snap.job || {};
  const parts = [];
  if (busy) parts.push(`Working (${job.action || 'scheduled check'})…`);
  if (snap.free_mb !== null && snap.free_mb !== undefined) parts.push(`${snap.free_mb} MB free on the card.`);
  if (!busy && job.result && job.result.error) parts.push(`Last action failed: ${job.result.error}`);
  lib.state.textContent = parts.join(' ');

  lib.sources.replaceChildren(...(snap.sources.length
    ? snap.sources.map((s) => sourceRow(s, snap.status[s.name] || {}, busy))
    : [el('p', { className: 'setting-desc', textContent: 'No sources yet.' })]));
  document.querySelectorAll('[data-all]').forEach((b) => { b.disabled = busy || !snap.sources.length; });

  for (const [k, v] of Object.entries(snap.policy)) {
    const field = lib.policy.elements[k];
    if (field && document.activeElement !== field) field.value = String(v);
  }
  lib.tokenState.textContent = snap.token_set ? 'A token is set.' : 'No token is set.';

  clearTimeout(libPoll);
  if (busy) libPoll = setTimeout(loadLibrary, 3000);
}

async function loadLibrary() {
  try {
    const r = await fetch('/admin/library');
    if (!r.ok) throw new Error(r.status);
    renderLibrary(await r.json());
  } catch (_) {
    lib.state.textContent = 'Could not read the library settings.';
  }
}

function showTypeFields() {
  const type = lib.add.elements.type.value;
  lib.add.querySelectorAll('[data-for]').forEach((label) => {
    label.hidden = !label.dataset.for.split(' ').includes(type);
  });
  lib.typeHelp.textContent = TYPE_HELP[type];
}

lib.add.elements.type.addEventListener('change', showTypeFields);
document.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => {
  lib.add.reset();
  for (const [k, v] of Object.entries(PRESETS[b.dataset.preset])) lib.add.elements[k].value = v;
  showTypeFields();
}));
lib.add.addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = lib.add.elements;
  const source = { name: f.name.value.trim(), type: f.type.value, prerelease: f.prerelease.checked };
  for (const k of ['repo', 'workflow', 'branch', 'pattern', 'url']) {
    if (!f[k].closest('label').hidden && f[k].value.trim()) source[k] = f[k].value.trim();
  }
  try {
    renderLibrary(await libPost({ action: 'add', source }));
    say(`Added ${source.name}. Use Check or Update to fetch it.`, true);
    lib.add.reset();
    showTypeFields();
  } catch (err) { say(`Could not add the source: ${err.message}`, false); }
});
lib.policy.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = { action: 'policy' };
  for (const k of ['keep_old', 'check_every_hours', 'min_free_mb']) body[k] = Number(lib.policy.elements[k].value);
  try { renderLibrary(await libPost(body)); say('Saved.', true); } catch (err) { say(err.message, false); }
});
lib.token.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    renderLibrary(await libPost({ action: 'token', value: lib.token.elements.value.value.trim() }));
    lib.token.reset();
    say('Token saved.', true);
  } catch (err) { say(err.message, false); }
});
document.getElementById('library-token-clear').addEventListener('click', async () => {
  try { renderLibrary(await libPost({ action: 'token', value: '' })); say('Token cleared.', true); } catch (err) { say(err.message, false); }
});
document.querySelectorAll('[data-all]').forEach((b) => b.addEventListener('click', async () => {
  try { renderLibrary(await libPost({ action: b.dataset.all })); } catch (err) { say(err.message, false); }
}));

showTypeFields();
loadLibrary();

// --- shared helpers for the sections below --------------------------------------
const size = (bytes) => {
  if (!bytes) return '0 MB';
  const gb = bytes / 2 ** 30;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(0.1, bytes / 2 ** 20).toFixed(1)} MB`;
};
// Ages come from the hub's powered-on clock (seconds), never this browser's.
const ago = (secs) => {
  if (secs < 90) return 'just now';
  const m = Math.round(secs / 60);
  if (m < 90) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
};
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}
const actionButton = (label, onclick, extra = {}) =>
  el('button', { type: 'button', textContent: label, onclick, ...extra });

// --- box and services ------------------------------------------------------------
const STATE_LABEL = { running: 'Running', stopped: 'Not running', missing: 'Not installed' };
const OP_LABEL = { start: 'Start', stop: 'Stop', restart: 'Restart', enable: 'Start at boot', disable: "Don't start at boot" };
let boxPoll = null;
let waitingFor = null; // a control request id whose answer we want to announce

function tile(label, value) {
  return el('div', { className: 'admin-tile' },
    el('span', { className: 'setting-desc', textContent: label }),
    el('span', { className: 'setting-name', textContent: value }));
}

function renderBox(data) {
  const sys = data.system || {};
  const h = Math.floor((data.uptime || 0) / 3600);
  document.getElementById('box-tiles').replaceChildren(
    tile('Memory', sys.mem_total ? `${size(sys.mem_available)} free of ${size(sys.mem_total)}` : '—'),
    tile('Card', sys.disk_total ? `${size(sys.disk_free)} free of ${size(sys.disk_total)}` : '—'),
    tile('Powered on', `${Math.floor(h / 24)} d ${h % 24} h in total`),
    tile('Guests', `${data.online} on the page${data.joined !== null && data.joined !== undefined ? `, ${data.joined} on the WiFi` : ''}`),
  );
  document.getElementById('hub-version').textContent = data.version;

  const rows = data.services.map((s) => {
    const ops = s.ops.filter((op) => (op === 'start' ? !s.active
      : op === 'stop' ? s.active
        : op === 'enable' ? !s.enabled
          : op === 'disable' ? s.enabled : true));
    return el('tr', {},
      el('td', {}, el('span', { className: 'setting-name', textContent: s.name }),
        el('span', { className: 'setting-desc', textContent: s.unit || s.note || s.path || '' })),
      el('td', {}, el('span', { className: `state state-${s.state}`, textContent: STATE_LABEL[s.state] || s.state })),
      el('td', { textContent: s.unit && s.state !== 'missing' ? (s.enabled ? 'yes' : 'no') : '—' }),
      el('td', {}, el('span', { className: 'library-buttons' },
        ...(s.state === 'missing' ? [] : ops.map((op) => actionButton(OP_LABEL[op], () => control(s, op)))))),
    );
  });
  document.querySelector('#service-table tbody').replaceChildren(...rows);

  const results = data.results || [];
  document.getElementById('control-results').replaceChildren(...results.slice(0, 4).map((r) =>
    el('p', { className: `setting-desc${r.ok ? '' : ' bad'}`, textContent: r.message })));
  if (waitingFor) {
    const done = results.find((r) => r.id === waitingFor);
    if (done) {
      say(done.message, done.ok);
      waitingFor = null;
    }
  }
  clearTimeout(boxPoll);
  boxPoll = setTimeout(loadBox, data.pending || waitingFor ? 1500 : 15000);
}

async function loadBox() {
  try { renderBox(await getJSON('/admin/box')); } catch (_) {
    clearTimeout(boxPoll);
    boxPoll = setTimeout(loadBox, 5000); // the hub may be restarting
  }
}

async function control(s, op) {
  if (s.unit === 'irate-box.service' && !confirm('Restart the hub server? This page reconnects by itself.')) return;
  if (op === 'stop' && !confirm(`Stop ${s.name}?`)) return;
  try {
    waitingFor = (await postJSON('/admin/control', { unit: s.unit, op })).id;
    say(`${OP_LABEL[op]}: ${s.name}…`, true);
    loadBox();
  } catch (err) { say(err.message, false); }
}

// --- moderation ------------------------------------------------------------------
function renderModeration(data) {
  const now = data.now;
  const del = (body, what) => async () => {
    if (!confirm(`Delete ${what}?`)) return;
    try { renderModeration(await postJSON('/admin/moderation', body)); } catch (err) { say(err.message, false); }
  };
  document.getElementById('mod-messages').replaceChildren(...(data.messages.length ? data.messages.map((m) =>
    el('div', { className: 'admin-item' },
      el('span', {}, el('strong', { textContent: m.name }), ` · ${ago(now - m.created)}`),
      el('span', { className: 'admin-text', textContent: m.text }),
      actionButton('Delete', del({ action: 'delete_message', created: m.created, name: m.name }), { className: 'small' })))
    : [el('p', { className: 'setting-desc', textContent: 'No messages.' })]));

  const threads = data.board.threads;
  document.getElementById('mod-threads').replaceChildren(...(threads.length ? threads.map((t) =>
    el('details', { className: 'admin-item' },
      el('summary', {}, el('strong', { textContent: t.title }),
        ` · ${t.posts.length} post${t.posts.length === 1 ? '' : 's'} · active ${ago(now - t.active)}`),
      ...t.posts.map((p, i) => el('div', { className: 'admin-subitem' },
        el('span', {}, el('strong', { textContent: p.author }), ` · ${ago(now - p.created)}${i === 0 ? ' · opening post' : ''}`),
        el('span', { className: 'admin-text', textContent: p.text }),
        actionButton(i === 0 ? 'Delete thread' : 'Delete post',
          del(i === 0 ? { action: 'delete_thread', id: t.id } : { action: 'delete_post', id: t.id, index: i },
            i === 0 ? `the whole thread "${t.title}"` : 'this post'), { className: 'small' }))),
    ))
    : [el('p', { className: 'setting-desc', textContent: 'No threads.' })]));
}

async function loadModeration() {
  try { renderModeration(await getJSON('/admin/moderation')); } catch (_) {
    document.getElementById('mod-messages').textContent = 'Could not read the shoutbox and board.';
  }
}

// --- saved work -------------------------------------------------------------------
const NS_LABEL = { scenes: 'shared drawings', rooms: 'collaboration rooms', files: 'pasted images', saves: 'gallery' };

function thumbFor(id) {
  const img = el('img', { className: 'admin-thumb', alt: '' });
  // Thumbnails are served as opaque downloads, so they are fetched and re-typed.
  fetch(`/api/saves/${id}/thumb`).then((r) => (r.ok ? r.blob() : null)).then((b) => {
    if (b) img.src = URL.createObjectURL(new Blob([b], { type: 'image/png' }));
  }).catch(() => {});
  return img;
}

function renderStore(data) {
  const u = data.usage;
  const pct = Math.min(100, Math.round((100 * u.total) / u.max_total));
  document.getElementById('store-usage').textContent =
    `${size(u.total)} of ${size(u.max_total)} used (${pct}%): ` +
    Object.entries(u.namespaces).map(([ns, v]) => `${v.files} ${NS_LABEL[ns] || ns} file${v.files === 1 ? '' : 's'} (${size(v.bytes)})`).join(', ') + '.';
  document.getElementById('store-meter').style.width = `${pct}%`;
  document.getElementById('store-clear').replaceChildren(...['scenes', 'rooms', 'files'].map((ns) =>
    actionButton(`Clear ${NS_LABEL[ns]}`, async () => {
      if (!confirm(`Delete all ${NS_LABEL[ns]}? Links to them stop working.`)) return;
      try { renderStore(await postJSON('/admin/store', { action: 'clear', namespace: ns })); } catch (err) { say(err.message, false); }
    }, { disabled: !u.namespaces[ns].files, className: 'small' })));

  const act = (body) => async () => {
    try { renderStore(await postJSON('/admin/store', body)); } catch (err) { say(err.message, false); }
  };
  document.getElementById('store-saves').replaceChildren(...(data.saves.length ? data.saves.map((s) =>
    el('div', { className: 'admin-item admin-save' },
      s.thumb ? thumbFor(s.id) : el('span', { className: 'admin-thumb' }),
      el('span', {}, el('strong', { textContent: s.name }),
        el('span', { className: 'setting-desc', textContent: `${s.kind} · ${size(s.size)} · ${ago(data.now - s.created)}` })),
      el('span', { className: 'library-buttons' },
        actionButton('Rename', () => {
          const name = prompt('New name', s.name);
          if (name && name.trim() && name !== s.name) act({ action: 'rename', id: s.id, name: name.trim() })();
        }, { className: 'small' }),
        actionButton('Delete', () => { if (confirm(`Delete "${s.name}"?`)) act({ action: 'delete', id: s.id })(); }, { className: 'small' }))))
    : [el('p', { className: 'setting-desc', textContent: 'Nothing saved to the gallery yet.' })]));
}

async function loadStore() {
  try { renderStore(await getJSON('/admin/store')); } catch (_) {
    document.getElementById('store-usage').textContent = 'Could not read the store.';
  }
}

// --- settings: the store's cap and expiry -----------------------------------------
const storeForm = document.getElementById('store-settings');
async function loadStoreSettings() {
  try {
    const s = await getJSON('/admin/settings');
    storeForm.elements.store_max_total_mb.value = s.store_max_total_mb;
    storeForm.elements.store_save_ttl_hours.value = s.store_save_ttl_hours;
  } catch (_) { /* the landing-page block already reports a failed read */ }
}
storeForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await postJSON('/admin/settings', {
      store_max_total_mb: Number(storeForm.elements.store_max_total_mb.value),
      store_save_ttl_hours: Number(storeForm.elements.store_save_ttl_hours.value),
    });
    say('Saved. The new cap applies from the next write to the store.', true);
    loadStoreSettings();
    loadStore();
  } catch (err) { say(err.message, false); }
});

// --- password -----------------------------------------------------------------------
const pwForm = document.getElementById('password-form');
pwForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const pw = pwForm.elements.password.value;
  if (pw !== pwForm.elements.confirm.value) { say('The two passwords differ.', false); return; }
  try {
    waitingFor = (await postJSON('/admin/password', { password: pw })).id;
    pwForm.reset();
    say('Changing the password… your browser will ask for the new one.', true);
    loadBox();
  } catch (err) { say(err.message, false); }
});

loadBox();
loadModeration();
loadStore();
loadStoreSettings();
