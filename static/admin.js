// Admin options. The gate is Caddy's basic_auth on /admin/* -- by the time this page
// loads, the operator has already authenticated. Each toggle saves on change; there is
// no Save button to forget to press.

// --- panes ------------------------------------------------------------------------------
// One pane at a time, chosen by the sidebar and kept in the address (#updates), so a
// reload or a bookmark comes back to the same place. Everything keeps loading in the
// background, so the sidebar's badges stay current whichever pane is open.
const panes = [...document.querySelectorAll('.admin-pane')];
const sideLinks = [...document.querySelectorAll('.admin-side-list a')];
const menu = document.querySelector('.admin-menu');
const side = document.querySelector('.admin-side');

function showPane() {
  const pane = panes.find((p) => `#${p.id}` === location.hash) || panes[0];
  panes.forEach((p) => { p.hidden = p !== pane; });
  sideLinks.forEach((a) => {
    if (a.hash === `#${pane.id}`) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  const title = pane.querySelector('h2').textContent;
  document.getElementById('admin-current').textContent = title;
  document.title = `${title} · Hub admin`;
  side.classList.remove('open');
  menu.setAttribute('aria-expanded', 'false');
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', showPane);
menu.addEventListener('click', () => {
  const open = side.classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
});
showPane();

// A word beside a sidebar entry: "new", "working", ... or nothing.
function badge(pane, text) {
  const a = sideLinks.find((l) => l.hash === `#${pane}`);
  if (a) { if (text) a.dataset.badge = text; else delete a.dataset.badge; }
}

// Every answer goes in the note right under the control that asked for it.
const noteEl = (id) => document.getElementById(id);
function say(text, ok, at) {
  at.textContent = text;
  at.classList.toggle('bad', !ok);
  at.hidden = !text;
}

const boxes = document.querySelectorAll('.settings input[type="checkbox"]');
const landingNote = noteEl('landing-note');
// Each group of checkboxes names the note its answers go in (data-note).
const settingNote = (box) => noteEl(box.closest(".settings").dataset.note);

async function load() {
  try {
    const r = await fetch('/admin/settings');
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    boxes.forEach((b) => { if (typeof data[b.id] === 'boolean') b.checked = data[b.id]; });
    applySetup(data.setup_done === true);
  } catch (_) {
    say('Could not read the current settings.', false, landingNote);
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
    say(box.id === 'show_term_card' ? 'Saved. The landing page picks it up within about 15 seconds.'
      : 'Saved. It takes effect at the next boot.', true, settingNote(box));
  } catch (_) {
    box.checked = !box.checked;
    say('Could not save — the setting is unchanged.', false, settingNote(box));
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
const remoteNote = noteEl('remote-note');
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
    say('Saved. Tailscale follows within a few seconds.', true, remoteNote);
    [2000, 5000, 10000].forEach((ms) => setTimeout(loadRemote, ms));
  } catch (_) {
    say('Could not change remote access.', false, remoteNote);
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
  states: document.querySelectorAll('.library-state'),
  bars: document.querySelectorAll('.library-bar'),
  allNote: document.getElementById('library-all-note'),
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
  if (!r.ok) throw Object.assign(new Error(data.error || `HTTP ${r.status}`), { status: r.status });
  return data;
}

// What a library button's action answered goes right under that button, not in the banner
// at the top. "Already running" (409) stays until the librarian is free, then goes.
let libNote = null; // { key, text, busy }

async function libAct(key, body, confirmText) {
  if (confirmText && !confirm(confirmText)) return;
  libNote = null;
  try {
    renderLibrary(await libPost(body));
  } catch (e) {
    libNote = e.status === 409
      ? { key, busy: true, text: 'The librarian is already running. The buttons come back when it is free; try again then.' }
      : { key, busy: false, text: e.message };
    loadLibrary();
  }
}

function noteFor(key) {
  if (!libNote || libNote.key !== key) return null;
  return el('span', { className: `setting-desc action-note ${libNote.busy ? 'busy' : 'bad'}`, role: 'status', textContent: libNote.text });
}

function sourceRow(src, st, busy) {
  const cur = st.current || {};
  const latest = st.latest || {};
  const archive = st.archive || [];
  const key = `book:${src.name}`;
  const button = (label, body, confirmText, opts = {}) => el('button', {
    type: 'button', textContent: label, disabled: busy || !!opts.off, title: opts.title || '',
    onclick: () => libAct(key, body, confirmText),
  });
  // Check finds a newer version; Fetch downloads and checks it beside the book in use;
  // Update swaps it in (fetching first if that has not happened).
  const newer = !!latest.version && latest.version !== cur.version;
  const fetched = st.fetched && st.fetched.version === latest.version ? st.fetched : null;
  return el('div', { className: 'setting library-source' },
    el('span', {},
      el('span', { className: 'setting-name', textContent: `${src.name}.zim` }),
      el('span', { className: 'setting-desc', textContent: `${src.type}: ${where(src)}` }),
      el('span', { className: 'setting-desc',
        textContent: cur.version ? `Installed: ${cur.label || cur.version} (${mb(cur.size)}, ${cur.installed})` : 'Not installed by the librarian yet' }),
      newer ? el('span', { className: 'setting-desc', textContent: fetched
        ? `Fetched: ${fetched.label} (${mb(fetched.size)}), ready to update.`
        : `Available: ${latest.label} (${mb(latest.size)})` }) : null,
      archive.length ? el('span', { className: 'setting-desc', textContent: `Archived: ${archive.join(', ')}` }) : null,
      st.last_check ? el('span', { className: 'setting-desc', textContent: `Checked ${st.last_check}: ${st.outcome || ''}` }) : null,
      st.error ? el('span', { className: 'setting-desc bad', textContent: st.error }) : null,
      el('span', { className: 'library-buttons' },
        button('Check', { action: 'check', names: [src.name] }),
        button('Fetch', { action: 'fetch', names: [src.name] }, null, {
          off: !newer || fetched, title: !newer ? 'Check first: nothing newer is known' : fetched ? 'Already fetched' : '' }),
        button('Update', { action: 'update', names: [src.name] }),
        archive.length ? button('Roll back', { action: 'rollback', name: src.name },
          `Put ${archive[0]} back as ${src.name}.zim? The current version is archived.`) : null,
        button('Remove', { action: 'remove', name: src.name },
          `Stop tracking ${src.name}? The book itself stays in the library.`),
      ),
      noteFor(key),
    ),
  );
}

function renderApps(snap, busy) {
  const apps = snap.apps || {};
  const sources = Object.fromEntries(snap.sources.filter((s) => s.kind === 'app').map((s) => [s.name, s]));
  const post = (key, body, confirmText) => () => libAct(key, body, confirmText);
  document.getElementById('apps-list').replaceChildren(...Object.entries(apps).map(([name, a]) => {
    const inst = a.installed;
    const src = sources[name];
    const st = snap.status[name] || {};
    const res = st.install_result;
    const newer = !!(st.latest && st.current) && st.latest.version !== st.current.version;
    const fetched = newer && st.fetched && st.fetched.version === st.latest.version ? st.fetched : null;
    const lines = [
      inst === null ? 'Not installed.' : inst.commit
        ? `Installed: ${inst.commit.slice(0, 7)} from ${inst.repository} (${inst.ref}), built ${String(inst.built).slice(0, 10)}.`
        : 'Installed by hand (no bundle record): the first update replaces it.',
      src ? (src.type === 'git' ? `Source: git, ${src.repo} @ ${src.branch}, adapted for the hub.`
        : `Source: ${src.type}, ${src.repo} · ${src.workflow}${src.branch ? ` @ ${src.branch}` : ''}.`) : 'Not kept current: it has no update source yet.',
      st.last_check ? `Checked ${st.last_check}: ${st.outcome || ''}` : null,
      fetched ? `Fetched: ${fetched.commit} (${fetched.label}), ready to update.` : null,
      res ? `${res.ok ? 'Installed' : 'Install failed'}: ${res.message}` : null,
      st.error || null,
    ];
    return el('div', { className: 'setting library-source' }, el('span', {},
      el('span', { className: 'setting-name', textContent: a.title }),
      ...lines.filter(Boolean).map((t) => el('span', { className: `setting-desc${t === st.error || (res && !res.ok && t.startsWith('Install failed')) ? ' bad' : ''}`, textContent: t })),
      src ? el('span', { className: 'library-buttons' },
        el('button', { type: 'button', textContent: 'Check', disabled: busy, onclick: post(`app:${name}`, { action: 'check', names: [name] }) }),
        el('button', { type: 'button', textContent: 'Fetch', disabled: busy || !newer || !!fetched,
          title: !newer ? 'Check first: nothing newer is known' : fetched ? 'Already fetched' : '',
          onclick: post(`app:${name}`, { action: 'fetch', names: [name] }) }),
        el('button', { type: 'button', textContent: 'Update', disabled: busy, onclick: post(`app:${name}`, { action: 'update', names: [name] }) }),
        inst && inst.has_previous ? el('button', { type: 'button', textContent: 'Roll back', disabled: busy,
          onclick: post(`app:${name}`, { action: 'rollback', name }, `Go back to the previous ${a.title} build?`) }) : null)
        : el('span', { className: 'library-buttons' },
          el('button', { type: 'button', textContent: 'Keep current', disabled: busy,
            title: `Track ${a.title}'s published builds, so Check, Fetch and Update work for it`,
            onclick: post(`app:${name}`, { action: 'add-apps', names: [name] }) })),
      noteFor(`app:${name}`)));
  }));
}

function renderLibrary(snap) {
  const busy = snap.running;
  if (libNote && libNote.busy && !busy) libNote = null;
  renderApps(snap, busy);
  const job = snap.job || {};
  const parts = [];
  const p = snap.progress;
  if (busy && p) {
    const rate = p.seconds > 0 ? p.done / p.seconds : 0;
    const left = rate && p.total > p.done ? Math.round((p.total - p.done) / rate) : null;
    parts.push(`Downloading ${p.name}: ${mb(p.done) || '0 MB'}${p.total ? ` of ${mb(p.total)} (${Math.round((100 * p.done) / p.total)}%)` : ''}` +
      (rate ? `, ${Math.round(rate / 1024)} KB/s` : '') + (left !== null ? `, about ${left < 90 ? `${left} s` : `${Math.round(left / 60)} min`} left` : '') + '.');
  } else if (busy) parts.push(`Working (${job.action || 'scheduled check'})…`);
  // The bar: a download's bytes when its size is known, otherwise the indeterminate stripe.
  lib.bars.forEach((bar) => {
    bar.hidden = !busy;
    if (busy && p && p.total) {
      bar.max = p.total;
      bar.value = Math.min(p.done, p.total);
    } else bar.removeAttribute('value');
  });
  badge('apps', busy ? 'working' : '');
  badge('books', busy ? 'working' : '');
  if (snap.free_mb !== null && snap.free_mb !== undefined) parts.push(`${snap.free_mb} MB free on the card.`);
  if (!busy && job.result && job.result.error) parts.push(`Last action failed: ${job.result.error}`);
  lib.states.forEach((n) => { n.textContent = parts.join(' '); });

  const books = snap.sources.filter((s) => s.kind !== 'app');
  setupStep('books', books.length ? `${books.length} kept current by the librarian.`
    : 'None kept current yet: Kiwix serves only books copied in by hand.', books.length ? 'ok' : 'warn');
  lib.sources.replaceChildren(...(books.length
    ? books.map((s) => sourceRow(s, snap.status[s.name] || {}, busy))
    : [el('p', { className: 'setting-desc', textContent: 'No sources yet.' })]));
  document.querySelectorAll('[data-all]').forEach((b) => { b.disabled = busy || !snap.sources.length; });
  const allNote = noteFor('all');
  lib.allNote.hidden = !allNote;
  if (allNote) { lib.allNote.textContent = allNote.textContent; lib.allNote.className = allNote.className; }

  for (const [k, v] of Object.entries(snap.policy)) {
    const field = lib.policy.elements[k];
    if (field && document.activeElement !== field) field.value = String(v);
  }
  lib.tokenState.textContent = snap.token_set ? 'A token is set.' : 'No token is set.';

  clearTimeout(libPoll);
  // Also while an app the librarian fetched is still with the root helper.
  const installing = Object.values(snap.status).some((e) => e.pending && !e.install_result);
  if (busy || installing) libPoll = setTimeout(loadLibrary, 3000);
}

async function loadLibrary() {
  try {
    const r = await fetch('/admin/library');
    if (!r.ok) throw new Error(r.status);
    renderLibrary(await r.json());
  } catch (_) {
    lib.states.forEach((n) => { n.textContent = 'Could not read the library settings.'; });
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
    say(`Added ${source.name}. Use Check or Update to fetch it.`, true, noteEl('library-add-note'));
    lib.add.reset();
    showTypeFields();
  } catch (err) { say(`Could not add the source: ${err.message}`, false, noteEl('library-add-note')); }
});
lib.policy.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = { action: 'policy' };
  for (const k of ['keep_old', 'check_every_hours', 'min_free_mb']) body[k] = Number(lib.policy.elements[k].value);
  const at = noteEl('library-policy-note');
  try { renderLibrary(await libPost(body)); say('Saved.', true, at); } catch (err) { say(err.message, false, at); }
});
lib.token.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    renderLibrary(await libPost({ action: 'token', value: lib.token.elements.value.value.trim() }));
    lib.token.reset();
    say('Token saved.', true, noteEl('library-token-note'));
  } catch (err) { say(err.message, false, noteEl('library-token-note')); }
});
document.getElementById('library-token-clear').addEventListener('click', async () => {
  const at = noteEl('library-token-note');
  try { renderLibrary(await libPost({ action: 'token', value: '' })); say('Token cleared.', true, at); } catch (err) { say(err.message, false, at); }
});
document.querySelectorAll('[data-all]').forEach((b) => b.addEventListener('click', () => libAct('all', { action: b.dataset.all })));

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
let waitingFor = null; // { id, at }: a control request, and the note its answer goes in
const controlNote = noteEl('control-note');

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
    const done = results.find((r) => r.id === waitingFor.id);
    if (done) {
      // A service's answer is already at the top of the list above; the password's is not.
      if (waitingFor.at === controlNote) say('', true, controlNote);
      else say(done.message, done.ok, waitingFor.at);
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
    waitingFor = { id: (await postJSON('/admin/control', { unit: s.unit, op })).id, at: controlNote };
    say(`${OP_LABEL[op]}: ${s.name}…`, true, controlNote);
    loadBox();
  } catch (err) { say(err.message, false, controlNote); }
}

// --- moderation ------------------------------------------------------------------
function renderModeration(data) {
  const now = data.now;
  const del = (body, what) => async () => {
    if (!confirm(`Delete ${what}?`)) return;
    try { renderModeration(await postJSON('/admin/moderation', body)); } catch (err) { say(err.message, false, noteEl('mod-note')); }
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
      try { renderStore(await postJSON('/admin/store', { action: 'clear', namespace: ns })); } catch (err) { say(err.message, false, noteEl('store-note')); }
    }, { disabled: !u.namespaces[ns].files, className: 'small' })));

  const act = (body) => async () => {
    try { renderStore(await postJSON('/admin/store', body)); } catch (err) { say(err.message, false, noteEl('store-note')); }
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
    say('Saved. The new cap applies from the next write to the store.', true, noteEl('store-settings-note'));
    loadStoreSettings();
    loadStore();
  } catch (err) { say(err.message, false, noteEl('store-settings-note')); }
});

// --- password -----------------------------------------------------------------------
const pwForm = document.getElementById('password-form');
const pwNote = noteEl('password-note');
pwForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const pw = pwForm.elements.password.value;
  if (pw !== pwForm.elements.confirm.value) { say('The two passwords differ.', false, pwNote); return; }
  try {
    waitingFor = { id: (await postJSON('/admin/password', { password: pw })).id, at: pwNote };
    pwForm.reset();
    say('Changing the password… your browser will ask for the new one.', true, pwNote);
    loadBox();
  } catch (err) { say(err.message, false, pwNote); }
});

loadBox();
loadModeration();
loadStore();
loadStoreSettings();

// --- updates ---------------------------------------------------------------------------
// The root helper does all three steps (check into a root-owned cache; fetch: verify it and
// cache its downloads; install: rerun install.sh); this only asks, and shows what it
// reports, with a bar for the step it is on. The hub restarts during an install, so a
// failed read while one is running means "keep waiting", not "broken".
const upd = {
  summary: document.getElementById('update-summary'),
  check: document.getElementById('update-check'),
  fetch: document.getElementById('update-fetch'),
  install: document.getElementById('update-install'),
  progress: document.getElementById('update-progress'),
  bar: document.getElementById('update-bar'),
  step: document.getElementById('update-step'),
  note: document.getElementById('update-note'),
  changes: document.getElementById('update-changes'),
  checks: document.getElementById('update-checks'),
  output: document.getElementById('update-output'),
  log: document.getElementById('update-log'),
  doctor: document.getElementById('update-doctor'),
  clear: document.getElementById('update-clear'),
  doctorNote: document.getElementById('doctor-note'),
  doctorWhen: document.getElementById('doctor-when'),
  findings: document.getElementById('doctor-findings'),
};
const MARK = { ok: '✅', warn: '⚠️', problem: '❌' };
const checkItem = (status, title, detail, fix) => el('li', { className: `check check-${status}` },
  el('span', { textContent: `${MARK[status]} ` }), el('strong', { textContent: title }),
  detail ? el('span', { textContent: ` — ${detail}` }) : null,
  fix ? el('span', { className: 'setting-desc', textContent: fix }) : null);
const UPD_DOING = { check: 'Checking for updates', fetch: 'Fetching the update', install: 'Installing the update', addon: 'Changing an add-on' };
let updWaiting = null; // { id, action }
let updPoll = null;

// Each answer goes under the buttons that asked: the doctor's and the cache's under theirs.
function updSay(action, text, ok) {
  const node = action === 'doctor' || action === 'clear-cache' ? upd.doctorNote : upd.note;
  node.textContent = text;
  node.classList.toggle('bad', !ok);
  node.hidden = !text;
}

function renderUpdateProgress(p) {
  upd.progress.hidden = !p;
  if (!p) return;
  const steps = Math.max(p.steps, p.step, 1);
  const part = p.total ? Math.min(p.done / p.total, 1) : 0;
  upd.bar.value = Math.min((Math.max(p.step - 1, 0) + part) / steps, 1);
  const bytes = p.total ? `: ${size(p.done)} of ${size(p.total)} (${Math.round(100 * part)}%)` : p.done ? `: ${size(p.done)}` : '';
  upd.step.textContent = `${UPD_DOING[p.action] || 'Working'}, step ${Math.max(p.step, 1)} of ${p.estimate ? 'about ' : ''}${steps}` +
    (p.label ? ` — ${p.label}${bytes}` : '') + '.';
}

function renderUpdate(data) {
  const s = data.state;
  const p = data.progress;
  const busy = data.pending > 0 || !!updWaiting || !!p;
  const found = !!s && !s.up_to_date; // a check found a newer version
  const fetched = found && !!(s.checks && s.checks.length);
  const ready = found && s.verified === s.available;
  if (!s) {
    upd.summary.textContent = 'Not checked yet.';
  } else {
    const when = new Date(s.fetched * 1000).toLocaleString();
    upd.summary.textContent = s.up_to_date
      ? `Up to date with ${s.branch} (${s.available}, ${s.available_date}). Checked ${when}.`
      : `Available: ${s.available} (${s.available_date}) on ${s.branch}` +
        (s.changes_known ? `, ${s.changes.length} new commit${s.changes.length === 1 ? '' : 's'}` : '') +
        `. Checked ${when}. ` + (ready ? 'Fetched and verified, and its downloads are cached: ready to install.'
          : fetched ? 'It did not pass verification (below), so it cannot be installed.'
          : 'Fetch it to verify it and download what it needs.');
  }
  const checks = (fetched && s.checks) || [];
  upd.checks.hidden = !checks.length;
  upd.checks.replaceChildren(...checks.map((c) =>
    checkItem(c.ok ? 'ok' : c.warn ? 'warn' : 'problem', c.name, c.detail)));
  const d = data.doctor;
  upd.doctorWhen.hidden = upd.findings.hidden = !d;
  if (d) {
    const n = d.findings.filter((f) => f.status === 'problem').length;
    upd.doctorWhen.textContent = `Last run ${new Date(d.at * 1000).toLocaleString()}: ` +
      (n ? `${n} problem${n === 1 ? '' : 's'}.` : 'no problems found.');
    upd.findings.replaceChildren(...d.findings.map((f) => checkItem(f.status, f.check, f.detail, f.status === 'ok' ? '' : f.fix)));
  }
  upd.changes.replaceChildren(...((found && s.changes) || []).slice(0, 20)
    .map((c) => el('li', { textContent: c })));
  upd.output.hidden = !data.log.length || (!busy && s && s.up_to_date);
  if (p && p.action === 'install') upd.output.open = true;
  upd.log.textContent = data.log.join('\n');
  upd.log.scrollTop = upd.log.scrollHeight;
  renderUpdateProgress(p);
  badge('updates', p ? 'working' : ready ? 'ready' : found && !fetched ? 'new' : '');

  if (updWaiting) {
    const done = (data.results || []).find((r) => r.id === updWaiting.id);
    if (done) {
      const failed = !done.ok || /did not pass verification/.test(done.message);
      updSay(updWaiting.action, failed && updWaiting.action !== 'doctor'
        ? `${done.message} — "Run the update doctor" below can say why.` : done.message, !failed);
      updWaiting = null;
      renderUpdate(data);
      return;
    }
  }
  upd.check.disabled = upd.doctor.disabled = upd.clear.disabled = busy;
  upd.fetch.disabled = busy || !found || ready;
  upd.install.disabled = busy || !ready;
  clearTimeout(updPoll);
  if (busy) updPoll = setTimeout(loadUpdate, p ? 1000 : 2000);
}

async function loadUpdate() {
  try {
    renderUpdate(await getJSON('/admin/update'));
  } catch (_) {
    clearTimeout(updPoll);
    if (updWaiting) {
      upd.summary.textContent = 'Installing… the hub is restarting.';
      updPoll = setTimeout(loadUpdate, 3000);
    }
  }
}

async function requestUpdate(action) {
  if (action === 'install' && !confirm('Install the update now? The hub restarts during the install.')) return;
  try {
    updWaiting = { id: (await postJSON('/admin/update', { action })).id, action };
    upd.check.disabled = upd.fetch.disabled = upd.install.disabled = true;
    updSay(action, { check: 'Checking for updates…', fetch: 'Fetching the update: verifying it and caching its downloads…',
      install: 'Installing the update…', doctor: 'Running the update doctor (up to a minute or two)…',
      'clear-cache': 'Clearing the update cache…' }[action], true);
    loadUpdate();
  } catch (err) { updSay(action, err.message, false); }
}

upd.check.addEventListener('click', () => requestUpdate('check'));
upd.fetch.addEventListener('click', () => requestUpdate('fetch'));
upd.doctor.addEventListener('click', () => requestUpdate('doctor'));
upd.clear.addEventListener('click', () => {
  if (confirm('Remove the cached copy and downloads? The next check starts afresh.')) requestUpdate('clear-cache');
});
document.getElementById('backup-with-keys').addEventListener('click', (e) => {
  if (!confirm("This backup includes Syncthing's private keys. Anyone with the file can pose as this box to its Syncthing peers. Download it?")) e.preventDefault();
});
upd.install.addEventListener('click', () => requestUpdate('install'));
loadUpdate();

// --- security --------------------------------------------------------------------------
// The hub's own lines (password, plain HTTP, one origin) and the root helper's scan of the
// box (listeners, SSH, security updates). Each offer is a button on its line; its answer goes
// under it. A scan older than 15 minutes is refreshed when the page opens.
const sec = {
  when: document.getElementById('security-when'),
  scan: document.getElementById('security-scan'),
  note: document.getElementById('security-note'),
  findings: document.getElementById('security-findings'),
  listeners: document.querySelector('#security-listeners tbody'),
  output: document.getElementById('security-output'),
  log: document.getElementById('security-log'),
};
const RANK = { problem: 0, warn: 1, ok: 2 };
let secWaiting = null; // { id, fid }: a request, and the line its answer goes under
let secNote = null; // { fid, text, ok }
let secPoll = null;
let secAsked = false;

function renderSecurity(data) {
  const scan = data.scan;
  const busy = data.pending > 0 || !!secWaiting;
  if (secWaiting) {
    const done = (data.results || []).find((r) => r.id === secWaiting.id);
    if (done) {
      secNote = { fid: secWaiting.fid, text: done.message, ok: done.ok };
      secWaiting = null;
      return renderSecurity(data);
    }
  }
  const all = [...data.hub, ...(scan ? scan.findings : [])].sort((a, b) => RANK[a.status] - RANK[b.status]);
  const shown = new Set(all.map((f) => f.id));
  const noteUnder = (fid) => (secNote && secNote.fid === fid
    ? el('span', { className: `setting-desc action-note${secNote.ok ? '' : ' bad'}`, role: 'status', textContent: secNote.text }) : null);
  sec.findings.replaceChildren(...all.map((f) => el('li', { className: `check check-${f.status}` },
    el('span', { textContent: `${MARK[f.status]} ` }), el('strong', { textContent: f.title }),
    el('span', { textContent: ` — ${f.detail}` }),
    f.fix ? el('span', { className: 'setting-desc', textContent: f.fix }) : null,
    f.actions.length ? el('span', { className: 'library-buttons' }, ...f.actions.map((a) => el('button', {
      type: 'button', textContent: a.label, disabled: busy, onclick: () => secFix(f.id, a),
    }))) : null,
    noteUnder(f.id))));
  // An answer whose line went away with the fix (Cockpit closed, say) shows under Scan again.
  const loose = secNote && (secNote.fid === 'scan' || !shown.has(secNote.fid)) ? secNote : null;
  say(loose ? loose.text : '', loose ? loose.ok : true, sec.note);

  sec.when.textContent = scan ? `Last scanned ${new Date(scan.at * 1000).toLocaleString()}.` + (busy ? ' Scanning…' : '')
    : busy ? 'Scanning…' : 'Not scanned yet.';
  sec.scan.disabled = busy;
  sec.listeners.replaceChildren(...((scan && scan.listeners) || []).map((l) => el('tr', {},
    el('td', { textContent: `${l.proto.toUpperCase()} ${l.port}` }),
    el('td', {}, el('span', { className: 'setting-name', textContent: l.name }),
      el('span', { className: 'setting-desc', textContent: l.unit || l.process || '' })),
    el('td', { textContent: l.addr }))));
  sec.output.hidden = !data.log.length;
  sec.log.textContent = data.log.join('\n');
  const problems = all.filter((f) => f.status === 'problem').length;
  badge('security', problems ? String(problems) : '');
  if (scan) setupStep('security', problems ? `${problems} thing${problems === 1 ? '' : 's'} to fix or leave.` : 'Nothing to fix.', problems ? 'problem' : 'ok');

  const stale = !scan || Date.now() / 1000 - scan.at > 15 * 60;
  if (stale && !busy && !secAsked) { secAsked = true; secRequest({ action: 'scan' }, 'scan'); return; }
  clearTimeout(secPoll);
  if (busy) secPoll = setTimeout(loadSecurity, 2000);
}

async function loadSecurity() {
  try { renderSecurity(await getJSON('/admin/security')); } catch (_) {
    sec.when.textContent = 'Could not read the security report.';
  }
}

async function secRequest(body, fid) {
  try {
    secWaiting = { id: (await postJSON('/admin/security', body)).id, fid };
    secNote = null;
    loadSecurity();
  } catch (err) { secNote = { fid, text: err.message, ok: false }; loadSecurity(); }
}

function secFix(fid, action) {
  if (action.confirm && !confirm(action.confirm)) return;
  secRequest({ action: 'fix', choice: action.choice }, fid);
}

sec.scan.addEventListener('click', () => secRequest({ action: 'scan' }, 'scan'));
loadSecurity();

// --- add-ons ---------------------------------------------------------------------------
// Each add-on from apps.d: Add (after its consent text) or Remove, through the root helper,
// which reruns install.sh. The run's bar and output are the update's own (one installer at a
// time), so the Updates pane shows it too.
const ado = {
  list: document.getElementById('addons-list'),
  progress: document.getElementById('addons-progress'),
  bar: document.getElementById('addons-bar'),
  step: document.getElementById('addons-step'),
  output: document.getElementById('addons-output'),
  log: document.getElementById('addons-log'),
};
let adoWaiting = null; // { id, addon }
let adoNote = null; // { addon, text, ok }
let adoPoll = null;

function renderAddons(data) {
  const p = data.progress;
  const busy = data.pending > 0 || !!adoWaiting || !!p;
  if (adoWaiting) {
    const done = (data.results || []).find((r) => r.id === adoWaiting.id);
    if (done) {
      adoNote = { addon: adoWaiting.addon, text: done.message, ok: done.ok };
      adoWaiting = null;
      return renderAddons(data);
    }
  }
  ado.list.replaceChildren(...data.addons.map((a) => {
    const state = a.added === null ? 'Unknown: this box records no install options.'
      : a.added ? (a.active ? 'Added, and running.' : 'Added, but not running (see Overview).') : 'Not added.';
    const note = adoNote && adoNote.addon === a.id
      ? el('span', { className: `setting-desc action-note${adoNote.ok ? '' : ' bad'}`, role: 'status', textContent: adoNote.text }) : null;
    return el('div', { className: 'setting library-source' }, el('span', {},
      el('span', { className: 'setting-name', textContent: a.title }),
      el('span', { className: 'setting-desc', textContent: a.summary }),
      el('span', { className: `setting-desc${a.added && !a.active ? ' bad' : ''}`, textContent: state }),
      !a.added && a.needs ? el('span', { className: 'setting-desc', textContent: `Needs: ${a.needs}` }) : null,
      a.added === null ? null : el('span', { className: 'library-buttons' }, el('button', {
        type: 'button', textContent: a.added ? 'Remove' : 'Add', disabled: busy,
        onclick: () => addonSet(a, !a.added),
      })),
      note));
  }));
  // The run in flight: the same bar as Updates, for an add-on's run or an update's.
  ado.progress.hidden = !p;
  if (p) {
    const steps = Math.max(p.steps, p.step, 1);
    ado.bar.value = Math.min(Math.max(p.step - 1, 0) / steps, 1);
    ado.step.textContent = `${UPD_DOING[p.action] || 'Working'}, step ${Math.max(p.step, 1)} of ${p.estimate ? 'about ' : ''}${steps}` +
      (p.label ? ` — ${p.label}` : '') + '.';
  }
  ado.output.hidden = !(data.log.length && (busy || adoNote));
  ado.log.textContent = data.log.join('\n');
  ado.log.scrollTop = ado.log.scrollHeight;
  if (p && p.action === 'addon') ado.output.open = true;
  badge('addons', p && p.action === 'addon' ? 'working' : '');
  const added = data.addons.filter((a) => a.added).map((a) => a.title);
  setupStep('addons', added.length ? `Added: ${added.join(', ')}.` : 'None added: the hub works without them.', 'ok');
  clearTimeout(adoPoll);
  if (busy) adoPoll = setTimeout(loadAddons, 1500);
}

async function loadAddons() {
  try { renderAddons(await getJSON('/admin/addons')); } catch (_) {
    clearTimeout(adoPoll);
    if (adoWaiting) adoPoll = setTimeout(loadAddons, 3000); // the hub restarts during the run
  }
}

async function addonSet(a, on) {
  const ask = on ? (a.consent || `Add ${a.title}? The installer runs for a few minutes.`)
    : `Remove ${a.title}? Its service stops; its data stays, so adding it back finds it.`;
  if (!confirm(ask)) return;
  try {
    adoWaiting = { id: (await postJSON('/admin/addons', { addon: a.id, on })).id, addon: a.id };
    adoNote = { addon: a.id, text: on ? `Adding ${a.title}…` : `Removing ${a.title}…`, ok: true };
    loadAddons();
  } catch (err) { adoNote = { addon: a.id, text: err.message, ok: false }; loadAddons(); }
}

loadAddons();

// --- setup steps -----------------------------------------------------------------------
// The rest of the first-use setup (the password is step 1, on admin-setup.html). Each step's
// line is filled in by the part of this page that already loads its data; the pane shows
// until the owner finishes it, and Overview's link brings it back.
const setupList = document.getElementById('setup-steps');
const welcomeLink = document.getElementById('welcome-link');

function setupStep(step, text, status) {
  const li = setupList.querySelector(`[data-step="${step}"]`);
  li.querySelector('span').textContent = text;
  li.className = `step-${status}`;
}

function applySetup(done) {
  welcomeLink.hidden = done;
  if (!done && !location.hash) location.hash = '#welcome';
}

async function setSetupDone(done) {
  try {
    await postJSON('/admin/settings', { setup_done: done });
    applySetup(done);
    location.hash = done ? '#overview' : '#welcome';
  } catch (err) { say(err.message, false, noteEl('setup-note')); }
}

// How guests reach the box: what this page was loaded from.
(() => {
  const port = location.port && location.port !== '80' ? location.port : '';
  setupStep('connection', port
    ? `At http://${location.host}/, on port ${port}: something else has port 80, so a phone joining a hotspot would not get the sign-in sheet. Everything else works.`
    : `At http://${location.host}/ on the network the box is already on. A hotspot of its own is planned as an add-on.`,
  port ? 'warn' : 'ok');
  setupStep('password', 'Set.', 'ok');
})();
document.getElementById('setup-finish').addEventListener('click', () => setSetupDone(true));
document.getElementById('setup-again').addEventListener('click', (e) => { e.preventDefault(); setSetupDone(false); });
