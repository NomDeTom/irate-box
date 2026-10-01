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
