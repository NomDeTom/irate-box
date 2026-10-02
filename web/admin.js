// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Admin options. The gate is the web server's basic auth on /admin/* -- by the time this page
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
  // Background art by side-bar group (style.css, web/art/): the controller for Box and System,
  // the librarian for Library and Hub content, the doctor for Health.
  const link = sideLinks.find((l) => l.hash === `#${pane.id}`);
  let group = link && link.previousElementSibling;
  while (group && !group.classList.contains('admin-side-group')) group = group.previousElementSibling;
  const ART = { Box: 'controller', System: 'controller', Library: 'librarian', 'Hub content': 'librarian', Health: 'doctor' };
  document.querySelector('.admin-main').dataset.art = (group && ART[group.textContent.trim()]) || '';
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
    if (boxes.length) say('Could not read the current settings.', false, settingNote(boxes[0]));
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
    say('Saved. It takes effect at the next boot.', true, settingNote(box));
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
      accessSlot(name),
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
  for (const k of ['keep_old', 'check_every_hours', 'min_free_mb', 'auto_install']) body[k] = Number(lib.policy.elements[k].value);
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
        el('span', { className: 'setting-desc', textContent: s.unit || s.note || s.path || '' }),
        s.why ? el('span', { className: 'setting-desc bad', textContent: s.why }) : null),
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

  document.getElementById('mod-drops').replaceChildren(...((data.drops || []).length ? data.drops.map((d) =>
    el('div', { className: 'admin-item' },
      el('span', {}, d.locked ? el('span', { title: 'Locked by a guest\'s device: only it, or you here, can remove it', textContent: '🔒 ' }) : null,
        el('a', { href: `/api/drop/${encodeURIComponent(d.id)}`, textContent: d.name }),
        ` · ${size(d.size)}${d.by ? ` · ${d.by}` : ''} · ${ago(d.age)}${d.locked ? ' · locked' : ''}`),
      actionButton('Delete', del({ action: 'delete_drop', id: d.id }, `"${d.name}"`), { className: 'small' })))
    : [el('p', { className: 'setting-desc', textContent: 'No files.' })]));

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
  const dr = data.drop;
  if (dr) {
    document.getElementById('drop-usage').textContent = `${dr.files} file${dr.files === 1 ? '' : 's'}, ` +
      `${size(dr.bytes)} of ${size(dr.max_total)}. Up to ${size(dr.max_file)} a file` +
      (dr.ttl ? `; each stays ${Math.round(dr.ttl / 3600)} hours of powered-on time.` : '; kept until the cap is reached.');
  }
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
      el('span', {}, el('strong', { textContent: `${s.locked ? '🔒 ' : ''}${s.name}` }),
        el('span', { className: 'setting-desc', textContent: `${s.kind} · ${size(s.size)} · ${ago(data.now - s.created)}` +
          (s.locked ? ' · locked by a guest\'s device: only it can change it, but you can still rename or delete it here' : '') })),
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
    storeForm.elements.drop_max_total_mb.value = s.drop_max_total_mb;
    storeForm.elements.drop_ttl_hours.value = s.drop_ttl_hours;
  } catch (_) { /* the landing-page block already reports a failed read */ }
}
storeForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await postJSON('/admin/settings', {
      store_max_total_mb: Number(storeForm.elements.store_max_total_mb.value),
      store_save_ttl_hours: Number(storeForm.elements.store_save_ttl_hours.value),
      drop_max_total_mb: Number(storeForm.elements.drop_max_total_mb.value),
      drop_ttl_hours: Number(storeForm.elements.drop_ttl_hours.value),
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
  force: document.getElementById('update-force'),
  forceFailed: document.getElementById('force-failed'),
  forceNote: document.getElementById('force-note'),
};
const MARK = { ok: '✅', warn: '⚠️', problem: '❌' };
const checkItem = (status, title, detail, fix) => el('li', { className: `check check-${status}` },
  el('span', { textContent: `${MARK[status]} ` }), el('strong', { textContent: title }),
  detail ? el('span', { textContent: ` — ${detail}` }) : null,
  fix ? el('span', { className: 'setting-desc', textContent: fix }) : null);
const UPD_DOING = { check: 'Checking for updates', fetch: 'Fetching the update', install: 'Installing the update', addon: 'Changing an add-on', repair: 'Running the installer again' };
let updWaiting = null; // { id, action }
let updPoll = null;

// Each answer goes under the buttons that asked: the doctor's and the cache's under theirs.
function updSay(action, text, ok) {
  const node = action === 'force-install' ? upd.forceNote
    : action === 'doctor' || action === 'clear-cache' ? upd.doctorNote : upd.note;
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
  const failedChecks = ready ? [] : checks.filter((c) => !c.ok && !c.warn);
  upd.forceFailed.hidden = !failedChecks.length;
  upd.forceFailed.replaceChildren(...failedChecks.map((c) => checkItem('problem', c.name, c.detail)));
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
  const doctorProblems = d ? d.findings.filter((f) => f.status === 'problem').length : 0;
  badge('updoctor', doctorProblems ? String(doctorProblems) : failedChecks.length ? '!' : '');
  upd.changes.replaceChildren(...((found && s.changes) || []).slice(0, 20)
    .map((c) => el('li', { textContent: c })));
  renderAuto(data.auto);
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
        ? `${done.message} — the Updates doctor (under Health) can say why.` : done.message, !failed);
      updWaiting = null;
      renderUpdate(data);
      return;
    }
  }
  upd.check.disabled = upd.doctor.disabled = upd.clear.disabled = busy;
  upd.fetch.disabled = busy || !found || ready;
  upd.install.disabled = busy || !ready;
  upd.force.disabled = busy || !failedChecks.length;
  upd.force.dataset.failed = failedChecks.map((c) => c.name).join('\n');
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
  if (action === 'force-install' && !confirm(`Install the fetched version although it failed these checks?\n\n${upd.force.dataset.failed}\n\nThe installer still stops on its own errors. The hub restarts during the install.`)) return;
  try {
    updWaiting = { id: (await postJSON('/admin/update', { action })).id, action };
    upd.check.disabled = upd.fetch.disabled = upd.install.disabled = upd.force.disabled = true;
    updSay(action, { check: 'Checking for updates…', fetch: 'Fetching the update: verifying it and caching its downloads…',
      install: 'Installing the update…', 'force-install': 'Installing the fetched version anyway…', doctor: 'Running the update doctor (up to a minute or two)…',
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

// Automatic updates (the librarian's selfupdate.py): its policy, saved through /admin/library,
// and what its last step did. The form is filled from the hub only while nobody is editing it.
const auto = { form: document.getElementById('auto-form'), state: document.getElementById('auto-state'),
  note: noteEl('auto-note'), window: document.getElementById('auto-window') };
for (const name of ['hub_window_start', 'hub_window_end']) {
  auto.form.elements[name].replaceChildren(...Array.from({ length: 24 }, (_, h) =>
    el('option', { value: String(h), textContent: `${String(h).padStart(2, '0')}:00` })));
}
const AUTO_STEP = { check: 'looked for an update', fetch: 'fetched and verified an update', install: 'installed an update' };
function renderAuto(a) {
  if (!a) return;
  if (!auto.form.contains(document.activeElement)) {
    for (const k of ['hub_check_every_hours', 'hub_auto', 'hub_window_start', 'hub_window_end']) auto.form.elements[k].value = String(a[k]);
  }
  auto.window.hidden = auto.form.elements.hub_auto.value !== '2';
  const st = a.state || {};
  const parts = [];
  if (st.waiting) parts.push(`Working: asked the root helper to ${st.waiting.step} at ${new Date(st.waiting.at * 1000).toLocaleTimeString()}.`);
  if (st.last) parts.push(`Last automatic step, ${new Date(st.last.at * 1000).toLocaleString()}: ${AUTO_STEP[st.last.step] || st.last.step}: ${st.last.message}`);
  if (st.note && !st.waiting) parts.push(st.note + '.');
  auto.state.textContent = parts.join(' ') || (Number(a.hub_check_every_hours) ? 'No automatic step yet: the librarian takes the first on its next round.' : 'Automatic checks are off.');
}
auto.form.elements.hub_auto.addEventListener('change', () => { auto.window.hidden = auto.form.elements.hub_auto.value !== '2'; });
auto.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = { action: 'policy' };
  for (const k of ['hub_check_every_hours', 'hub_auto', 'hub_window_start', 'hub_window_end']) body[k] = Number(auto.form.elements[k].value);
  if (body.hub_auto === 2 && body.hub_window_start === body.hub_window_end) { say('The install hours need a start and an end that differ.', false, auto.note); return; }
  try { await libPost(body); say('Saved. The librarian follows it from its next round (hourly).', true, auto.note); loadUpdate(); } catch (err) { say(err.message, false, auto.note); }
});
upd.force.addEventListener('click', () => requestUpdate('force-install'));
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
  auditWhen: document.getElementById('audit-when'),
  auditRun: document.getElementById('audit-run'),
  auditSteps: document.getElementById('audit-steps'),
  auditScope: document.getElementById('audit-scope'),
  auditNot: document.getElementById('audit-not-covered'),
  auditNote: document.getElementById('audit-note'),
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
  const loose = secNote && (secNote.fid === 'scan' || !shown.has(secNote.fid)) && secNote.fid !== 'audit' ? secNote : null;
  say(loose ? loose.text : '', loose ? loose.ok : true, sec.note);
  // The security doctor is its own pane (Health), with its answer under its button.
  const auditSaid = secNote && secNote.fid === 'audit' ? secNote : null;
  say(auditSaid ? auditSaid.text : '', auditSaid ? auditSaid.ok : true, sec.auditNote);

  sec.when.textContent = scan ? `Last scanned ${new Date(scan.at * 1000).toLocaleString()}.` + (busy ? ' Scanning…' : '')
    : busy ? 'Scanning…' : 'Not scanned yet.';
  sec.scan.disabled = busy;
  sec.auditRun.disabled = busy;
  renderAudit(data.audit, busy);
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

// The security doctor's report (secdoctor.py): one block per step, the steps with something to
// look at open. Read-only, so a line has no buttons, only what to do by hand.
function renderAudit(audit, busy) {
  badge('secdoctor', audit && audit.counts.problem ? String(audit.counts.problem) : '');
  if (!audit) {
    sec.auditWhen.textContent = busy ? 'Running…' : 'Not run yet.';
    sec.auditSteps.replaceChildren();
    sec.auditScope.hidden = true;
    return;
  }
  const c = audit.counts;
  sec.auditWhen.textContent = `Last run ${new Date(audit.at * 1000).toLocaleString()}: ${c.problem} to fix, ${c.warn} to look at, ${c.ok} fine`
    + (audit.root ? '.' : ' (not run as root: some checks could not read what they need).') + (busy ? ' Running…' : '');
  sec.auditSteps.replaceChildren(...audit.steps.map((st) => {
    const worst = st.findings.some((f) => f.status === 'problem') ? 'problem' : st.findings.some((f) => f.status === 'warn') ? 'warn' : 'ok';
    const lines = [...st.findings].sort((a, b) => RANK[a.status] - RANK[b.status]);
    const det = el('details', { className: 'admin-output' },
      el('summary', { textContent: `${MARK[worst]} ${st.title}${st.ref ? ` (${st.ref})` : ''}` }),
      el('ul', { className: 'admin-checks' }, ...lines.map((f) => el('li', { className: `check check-${f.status}` },
        el('span', { textContent: `${MARK[f.status]} ` }), el('strong', { textContent: f.title }),
        f.ref ? el('span', { className: 'setting-desc', textContent: ` [${f.ref}]` }) : null,
        el('span', { textContent: ` — ${f.detail}` }),
        f.fix ? el('span', { className: 'setting-desc', textContent: `To do: ${f.fix}` }) : null))));
    det.open = worst !== 'ok';
    return det;
  }));
  sec.auditScope.hidden = !(audit.not_covered || []).length;
  sec.auditNot.replaceChildren(...(audit.not_covered || []).map((t) => el('li', { textContent: t })));
}

async function loadSecurity() {
  try { renderSecurity(await getJSON('/admin/security')); } catch (err) {
    console.error('security pane:', err);
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
sec.auditRun.addEventListener('click', () => secRequest({ action: 'audit' }, 'audit'));
loadSecurity();

// --- health ----------------------------------------------------------------------------
// The box doctor (health.py, through the root helper): findings with what to do and the safe
// repairs as buttons; the last install's record and output, which the hub reads itself. Polled
// on every pane, slowly, for one more thing the hub can tell on its own: whether the root
// helper is answering at all. If not, a banner on every pane gives the commands to type, since
// no button that needs root (the doctor's included) can do anything until it is back.
const hl = {
  when: document.getElementById('health-when'),
  scan: document.getElementById('health-scan'),
  note: document.getElementById('health-note'),
  progress: document.getElementById('health-progress'),
  bar: document.getElementById('health-bar'),
  step: document.getElementById('health-step'),
  findings: document.getElementById('health-findings'),
  install: document.getElementById('health-install'),
  output: document.getElementById('health-output'),
  log: document.getElementById('health-log'),
  clockWhen: document.getElementById('clock-when'),
  clockScan: document.getElementById('clock-scan'),
  clockNote: document.getElementById('clock-note'),
  clockFindings: document.getElementById('clock-findings'),
  banner: document.getElementById('helper-banner'),
  bannerDetail: document.getElementById('helper-banner-detail'),
  bannerCmds: document.getElementById('helper-banner-cmds'),
};
let hlWaiting = null; // { id, fid }
let hlNote = null; // { fid, text, ok }
let hlPoll = null;
let hlAsked = false;

function installText(st) {
  if (!st) return 'No record yet: installs from before 2026-10-02 kept none. The next run of the installer keeps one.';
  const when = new Date(st.started * 1000).toLocaleString();
  const n = (st.problems || []).length;
  if (st.running) return `Started ${when}${st.step ? `, at "${st.step}"` : ''}. If nothing is installing now, it was cut off there (the doctor says so too).`;
  if (st.aborted) return `Started ${when}; stopped during "${st.step}"${st.failed_command ? `: ${st.failed_command} failed (exit ${st.exit})` : ''}. The steps after it were not done.`;
  return `Started ${when} (${st.args || 'no options'}); finished${n ? ` with ${n} problem${n === 1 ? '' : 's'}: ${st.problems.join('; ')}` : ' with no problems'}.`;
}

function renderHealth(data) {
  const h = data.helper || {};
  hl.banner.hidden = !h.stuck;
  if (h.stuck) {
    hl.bannerDetail.textContent = `${h.waiting} request${h.waiting === 1 ? ' is' : 's are'} waiting, the oldest for ${minutes(h.oldest)}.`;
    hl.bannerCmds.textContent = h.commands.join('\n');
  }
  const p = data.progress && data.progress.action === 'repair' ? data.progress : null;
  const busy = data.pending > 0 || !!hlWaiting || !!p;
  if (hlWaiting) {
    const done = (data.results || []).find((r) => r.id === hlWaiting.id);
    if (done) {
      hlNote = { fid: hlWaiting.fid, text: done.message, ok: done.ok };
      hlWaiting = null;
      return renderHealth(data);
    }
  }
  const rep = data.report;
  const all = rep ? [...rep.findings].sort((a, b) => RANK[a.status] - RANK[b.status]) : [];
  const shown = new Set(all.map((f) => f.id));
  const noteUnder = (fid) => (hlNote && hlNote.fid === fid
    ? el('span', { className: `setting-desc action-note${hlNote.ok ? '' : ' bad'}`, role: 'status', textContent: hlNote.text }) : null);
  // The clock and its module have their own pane (Box, Clock); the rest is the services doctor.
  const isClock = (f) => f.id.startsWith('clock') || f.id.startsWith('rtc');
  const item = (f) => el('li', { className: `check check-${f.status}` },
    el('span', { textContent: `${MARK[f.status]} ` }), el('strong', { textContent: f.check }),
    el('span', { textContent: ` — ${f.detail}` }),
    f.fix && f.status !== 'ok' ? el('span', { className: 'setting-desc', textContent: f.fix }) : null,
    f.actions.length ? el('span', { className: 'library-buttons' }, ...f.actions.map((a) => el('button', {
      type: 'button', textContent: a.label, disabled: busy || h.stuck, onclick: () => hlFix(f.id, a),
    }))) : null,
    noteUnder(f.id));
  hl.findings.replaceChildren(...all.filter((f) => !isClock(f)).map(item));
  hl.clockFindings.replaceChildren(...all.filter(isClock).map(item));
  const loose = hlNote && (hlNote.fid === 'scan' || hlNote.fid === 'clock-scan' || !shown.has(hlNote.fid)) ? hlNote : null;
  const inClock = loose && (loose.fid === 'clock-scan' || (!shown.has(loose.fid) && loose.fid.startsWith('rtc')));
  say(loose && !inClock ? loose.text : '', loose ? loose.ok : true, hl.note);
  say(inClock ? loose.text : '', loose ? loose.ok : true, hl.clockNote);
  hl.when.textContent = rep ? `Looked ${new Date(rep.at * 1000).toLocaleString()}.` + (busy ? ' Working…' : '')
    : busy ? 'Looking…' : 'Not looked yet.';
  hl.clockWhen.textContent = hl.when.textContent;
  hl.scan.disabled = hl.clockScan.disabled = busy || h.stuck;
  hl.progress.hidden = !p;
  if (p) {
    const steps = Math.max(p.steps, p.step, 1);
    hl.bar.value = Math.min(Math.max(p.step - 1, 0) / steps, 1);
    hl.step.textContent = `Running the installer again, step ${Math.max(p.step, 1)} of ${p.estimate ? 'about ' : ''}${steps}${p.label ? ` — ${p.label}` : ''}.`;
  }
  hl.install.textContent = installText(data.install);
  hl.log.textContent = (data.log || []).join('\n');
  hl.output.hidden = !(data.log || []).length;
  const problems = all.filter((f) => f.status === 'problem' && !isClock(f)).length;
  const clockProblems = all.filter((f) => f.status === 'problem' && isClock(f)).length;
  badge('health', h.stuck ? '!' : problems ? String(problems) : '');
  badge('clock', clockProblems ? String(clockProblems) : '');

  const stale = !rep || Date.now() / 1000 - rep.at > 15 * 60;
  const here = location.hash === '#health' || location.hash === '#clock';
  if (stale && !busy && !hlAsked && !h.stuck && here) { hlAsked = true; hlRequest({ action: 'scan' }, location.hash === '#clock' ? 'clock-scan' : 'scan'); return; }
  clearTimeout(hlPoll);
  hlPoll = setTimeout(loadHealth, busy ? 2000 : here ? 15000 : 30000);
}

async function loadHealth() {
  try { renderHealth(await getJSON('/admin/health')); } catch (err) {
    console.error('health pane:', err);
    hl.when.textContent = 'Could not read the health report.';
    clearTimeout(hlPoll);
    hlPoll = setTimeout(loadHealth, 30000);
  }
}

async function hlRequest(body, fid) {
  try {
    hlWaiting = { id: (await postJSON('/admin/health', body)).id, fid };
    hlNote = null;
  } catch (err) { hlNote = { fid, text: err.message, ok: false }; }
  loadHealth();
}

function hlFix(fid, action) {
  let choice = action.choice;
  let ask = action.confirm;
  if (choice === 'clock-set') {
    // This device's clock, read at the moment of the click: the box has none it can trust.
    const now = new Date();
    choice = `clock-set:${Math.round(now.getTime() / 1000)}`;
    ask = `Set the box's clock to ${now.toLocaleString()} (this device's time)? Use it only if this device's clock is right.`;
  }
  if (ask && !confirm(ask)) return;
  hlRequest({ action: 'fix', choice }, fid);
}

hl.scan.addEventListener('click', () => hlRequest({ action: 'scan' }, 'scan'));
hl.clockScan.addEventListener('click', () => hlRequest({ action: 'scan' }, 'clock-scan'));
window.addEventListener('hashchange', () => { if (location.hash === '#health' || location.hash === '#clock') loadHealth(); });
loadHealth();

// --- network ---------------------------------------------------------------------------
// The root helper's inventory of the box's networking (netinv.py), and the uplink watchdog
// (uplink.py): its report, its two levels and any custom values. Answers go under the button
// that asked. The form is filled from the watchdog's report only while nobody is editing it.
const net = {
  when: document.getElementById('net-when'),
  device: document.getElementById('net-device'),
  scan: document.getElementById('net-scan'),
  scanNote: document.getElementById('net-scan-note'),
  radios: document.querySelector('#net-radios tbody'),
  hazards: document.getElementById('net-hazards'),
  status: document.getElementById('up-status'),
  eager: document.getElementById('up-eager'),
  eagerDesc: document.getElementById('up-eager-desc'),
  forgive: document.getElementById('up-forgive'),
  forgiveDesc: document.getElementById('up-forgive-desc'),
  iface: document.getElementById('up-iface'),
  custom: document.getElementById('up-custom'),
  fields: document.getElementById('up-fields'),
  save: document.getElementById('up-save'),
  hold: document.getElementById('up-hold'),
  unhold: document.getElementById('up-unhold'),
  note: document.getElementById('up-note'),
  profile: document.getElementById('up-profile'),
  events: document.getElementById('up-events'),
};
const UP_FIELDS = [
  ['check', 'Check every (s)'], ['misses', 'Failed checks before it counts as down'],
  ['grace', 'Then wait (s) before acting'], ['steps.reconnect', 'Reconnect after (s)'],
  ['steps.restart', 'Restart the network service after (s)'], ['steps.radio', 'Reset the radio after (s)'],
  ['steps.reboot', 'Reboot after (s)'], ['repeat', 'Reconnect again every (s)'], ['backoff', '… that gap growing ×'],
  ['max_repeat', '… up to (s)'], ['flap_count', 'Drops that count as flapping'], ['flap_window', '… within (s)'],
  ['flap_action', 'A flapping link is'], ['guests', 'Radio reset and reboot with guests on'],
  ['reboots_per_day', 'Reboots a day, at most'], ['reboot_gap', 'Never reboot within (s) of the last'],
];
const UP_WORDS = {
  flap_action: { note: 'only noted', pin: 'locked to the strongest AP', repair: 'repaired' },
  guests: { protect: 'held back', ignore: 'go ahead' },
};
let netData = null;
let netWaiting = null; // { id, where: 'scan' | 'up' }
let netNotes = {};
let netPoll = null;
let upDirty = false;
let netAsked = false;

function upPreset(e, f, levels) {
  const p = levels.presets;
  const eff = { ...p.common, ...p.forgiveness[f], ...p.eagerness[e] };
  eff.steps = { ...p.eagerness[e].steps };
  return eff;
}
const upGet = (obj, key) => key.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);

function buildUpFields(levels) {
  if (net.fields.childElementCount) return;
  for (const [key, label] of UP_FIELDS) {
    let input;
    if (UP_WORDS[key]) {
      input = el('select', {}, el('option', { value: '', textContent: "(the level's)" }),
        ...Object.entries(UP_WORDS[key]).map(([v, t]) => el('option', { value: v, textContent: t })));
    } else {
      input = el('input', { type: 'text', inputMode: 'decimal', size: 8 });
    }
    input.dataset.key = key;
    input.addEventListener('input', () => { upDirty = true; });
    net.fields.append(el('label', {}, el('span', { textContent: label }), input));
  }
}

function fillUpForm(chosen, levels) {
  const sel = (box, names, value) => {
    if (!box.childElementCount) box.replaceChildren(...names.map((n) => el('option', { value: n, textContent: n[0].toUpperCase() + n.slice(1) })));
    box.value = value;
  };
  sel(net.eager, levels.eagerness, chosen.eagerness);
  sel(net.forgive, levels.forgiveness, chosen.forgiveness);
  net.iface.value = [...net.iface.options].some((o) => o.value === chosen.iface) ? chosen.iface : 'auto';
  const over = chosen.overrides || {};
  for (const input of net.fields.querySelectorAll('[data-key]')) {
    const v = upGet(over, input.dataset.key);
    input.value = v === undefined ? '' : v === null ? 'off' : String(v);
  }
  if (Object.keys(over).length) net.custom.open = true;
  showUpPreset(levels);
}

function showUpPreset(levels) {
  const e = net.eager.value, f = net.forgive.value;
  net.eagerDesc.textContent = levels.describe[e] || '';
  net.forgiveDesc.textContent = levels.describe[f] || '';
  const eff = upPreset(e, f, levels);
  for (const input of net.fields.querySelectorAll('input[data-key]')) {
    const v = upGet(eff, input.dataset.key);
    input.placeholder = v === undefined ? 'off' : String(v);
  }
  for (const s of net.fields.querySelectorAll('select[data-key]')) {
    s.options[0].textContent = `(the level's: ${UP_WORDS[s.dataset.key][eff[s.dataset.key]]})`;
  }
}

function readUpForm() {
  const overrides = {};
  for (const input of net.fields.querySelectorAll('[data-key]')) {
    const raw = input.value.trim();
    if (raw === '') continue;
    const key = input.dataset.key;
    let v = raw;
    if (input.tagName === 'INPUT') {
      if (key.startsWith('steps.') && raw.toLowerCase() === 'off') v = null;
      else if (Number.isNaN(Number(raw))) throw new Error(`${input.previousSibling.textContent}: a number${key.startsWith('steps.') ? ' or off' : ''}, please`);
      else v = Number(raw);
    }
    if (key.startsWith('steps.')) (overrides.steps ||= {})[key.slice(6)] = v;
    else overrides[key] = v;
  }
  return { eagerness: net.eager.value, forgiveness: net.forgive.value, iface: net.iface.value, overrides };
}

const when = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

function upStatusText(u) {
  if (!u) return 'The watchdog has not reported yet (irate-box-uplink starts with the box, or when you save here).';
  if (u.stale) return `The watchdog has not reported since ${new Date(u.at * 1000).toLocaleString()}: is irate-box-uplink running?`;
  if (u.state === 'no-link') return 'No link to watch: the box has no route to a network and no WiFi client interface.';
  if (u.state === 'off') return `${u.iface} was disconnected by hand (nmcli device disconnect), so the watchdog leaves it alone until it is connected again.`;
  const l = u.link || {};
  const on = l.ssid ? `${u.iface}: ${l.ssid} via ${l.bssid}, channel ${l.channel}, ${l.signal} dBm` : u.iface;
  const parts = [];
  if (u.state === 'up') {
    parts.push(`Up on ${on}. The gateway (${u.gateway}) answers.`);
  } else if (u.state === 'checking') {
    parts.push(`Checking ${u.iface}: the gateway has missed ${u.misses} check${u.misses === 1 ? '' : 's'}.`);
  } else {
    const o = u.outage;
    parts.push(o ? `Down for ${minutes(Math.round(u.at - o.since))} on ${u.iface}.` : `Down on ${u.iface}.`);
    if (o && o.done.length) parts.push(`Tried: ${o.done.join(', ')}.`);
    if (u.next) parts.push(`Next: ${u.next.label} at ${when(u.next.at)}.`);
  }
  if (u.pinned) parts.push(`Locked to ${u.pinned.bssid} since ${when(u.pinned.at)}, until the next drop.`);
  if (u.paused_until && u.paused_until > Date.now() / 1000) parts.push(`No repairs until ${when(u.paused_until)}.`);
  if (u.drops_in_window) parts.push(`${u.drops_in_window} drop${u.drops_in_window === 1 ? '' : 's'} lately.`);
  parts.push(`Run by ${u.backend}; repairs possible: ${(u.repairs || []).join(', ') || 'none (watch only)'}.`);
  if (u.dry_run) parts.push('Dry run: it decides and logs, and does nothing.');
  return parts.join(' ');
}

function renderNetwork(data) {
  netData = data;
  const busy = data.pending > 0 || !!netWaiting;
  if (netWaiting) {
    const done = (data.results || []).find((r) => r.id === netWaiting.id);
    if (done) {
      netNotes[netWaiting.where] = { text: done.message, ok: done.ok };
      if (netWaiting.where === 'up' && done.ok) upDirty = false;
      netWaiting = null;
      return renderNetwork(data);
    }
  }
  const inv = data.inventory;
  const u = data.uplink;
  const levels = data.levels;
  // What the box has
  net.when.textContent = inv ? `Looked ${new Date(inv.at * 1000).toLocaleString()}${inv.focus ? ` at ${inv.focus} only` : ''}.`
    + (busy ? ' Looking…' : '') : busy ? 'Looking…' : 'Not looked yet.';
  net.scan.disabled = busy;
  const devices = inv ? [...inv.radios.map((r) => r.iface), ...inv.wired.map((w) => w.iface)] : [];
  for (const box of [net.device, net.iface]) {
    const keep = box.value;
    const first = box.options[0];
    box.replaceChildren(first, ...devices.map((d) => el('option', { value: d, textContent: d })));
    box.value = [...box.options].some((o) => o.value === keep) ? keep : first.value;
  }
  const apFor = (r) => (inv.ap || []).find((a) => a.phy === r.phy);
  net.radios.replaceChildren(...(inv ? inv.radios : []).map((r) => {
    const a = apFor(r);
    const ident = [r.driver, r.bus && r.bus.toUpperCase(), r.usb && r.usb.id].filter(Boolean).join(', ');
    const link = r.link && r.link.ssid ? `${r.link.ssid}, channel ${r.link.channel}, ${r.link.signal} dBm` : r.type;
    return el('tr', {},
      el('td', {}, el('span', { className: 'setting-name', textContent: r.iface }), el('span', { className: 'setting-desc', textContent: `${ident} — ${link}` })),
      el('td', { textContent: r.owner + (r.manager ? ` + ${r.manager}` : '') }),
      el('td', {}, el('span', { className: 'setting-name', textContent: a ? (a.possible ? `Yes: ${a.mode.replace('-', ' ')}, through ${a.backend}` : 'No') : '—' }),
        a ? el('span', { className: 'setting-desc', textContent: a.detail }) : null));
  }), ...(inv ? inv.wired : []).map((w) => el('tr', {},
    el('td', {}, el('span', { className: 'setting-name', textContent: w.iface }), el('span', { className: 'setting-desc', textContent: `${w.driver || 'wired'} — ${w.carrier ? 'cable in' : 'no cable'}` })),
    el('td', { textContent: inv.uplink.iface === w.iface ? inv.uplink.backend : '' }), el('td', { textContent: '—' }))));
  const hz = inv ? [...inv.hazards].sort((x, y) => RANK[x.status] - RANK[y.status]) : [];
  net.hazards.replaceChildren(...hz.map((h) => checkItem(h.status, h.title, h.detail, h.fix)));
  const sn = netNotes.scan;
  say(sn ? sn.text : '', sn ? sn.ok : true, net.scanNote);

  // Staying on the network
  buildUpFields(levels);
  if (!upDirty) fillUpForm((u && u.chosen) || { eagerness: 'patient', forgiveness: 'normal', iface: 'auto', overrides: {} }, levels);
  net.status.textContent = upStatusText(u);
  net.save.disabled = busy;
  const held = u && u.chosen && u.chosen.hold_until > Date.now() / 1000;
  net.hold.hidden = !!held;
  net.unhold.hidden = !held;
  net.hold.disabled = net.unhold.disabled = busy;
  const un = netNotes.up;
  say(un ? un.text : '', un ? un.ok : true, net.note);

  const change = u && u.profile_change;
  const prof = inv && inv.uplink && inv.uplink.backend === 'networkmanager' ? inv.uplink.profile : null;
  const trap = inv && inv.hazards.find((h) => h.id.startsWith('auth-retries:'));
  if (change) {
    net.profile.replaceChildren(el('p', { className: 'setting-desc' },
      `Keep retrying is on for ${change.name} (since ${new Date(change.at * 1000).toLocaleDateString()}): NetworkManager never stops trying it. `,
      actionButton('Undo', () => netRequest({ action: 'profile', on: false }, 'up'), { disabled: busy })));
  } else if (prof && trap) {
    net.profile.replaceChildren(el('p', { className: 'setting-desc' },
      `Your WiFi profile ${prof} stops trying after a few failed handshakes, and then waits for someone to reconnect it by hand — which a box with no screen cannot ask for, and a flaky link can cause. `
      + 'Keep retrying sets its connection.autoconnect-retries and connection.auth-retries to 0. Undo, here or by uninstalling, puts the old values back. ',
      actionButton('Keep retrying', () => {
        if (confirm(`Change two settings of your WiFi profile ${prof}, so it never stops trying? Undo puts them back.`)) netRequest({ action: 'profile', on: true }, 'up');
      }, { disabled: busy })));
  } else {
    net.profile.replaceChildren();
  }
  const evs = (u && u.events) || [];
  net.events.replaceChildren(...evs.slice(-25).reverse().map((e) => el('li', {},
    el('span', { className: 'setting-desc', textContent: `${new Date(e.at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} ` }),
    el('span', { textContent: e.text }))));
  const down = u && !u.stale && u.state !== 'up';
  badge('network', down ? '!' : '');

  const stale = !inv || Date.now() / 1000 - inv.at > 60 * 60;
  if (stale && !busy && !netAsked && location.hash === '#network') { netAsked = true; netRequest({ action: 'scan' }, 'scan'); return; }
  clearTimeout(netPoll);
  netPoll = setTimeout(loadNetwork, busy ? 2000 : location.hash === '#network' ? 10000 : 60000);
}

async function loadNetwork() {
  try { renderNetwork(await getJSON('/admin/network')); } catch (err) {
    console.error('network pane:', err);
    net.when.textContent = 'Could not read the network report.';
    clearTimeout(netPoll);
    netPoll = setTimeout(loadNetwork, 30000);
  }
}

async function netRequest(body, where) {
  try {
    netWaiting = { id: (await postJSON('/admin/network', body)).id, where };
    delete netNotes[where];
  } catch (err) { netNotes[where] = { text: err.message, ok: false }; }
  loadNetwork();
}

net.scan.addEventListener('click', () => netRequest({ action: 'scan', iface: net.device.value || null }, 'scan'));
for (const box of [net.eager, net.forgive, net.iface]) {
  box.addEventListener('change', () => { upDirty = true; if (netData) showUpPreset(netData.levels); });
}
net.save.addEventListener('click', () => {
  let settings;
  try { settings = readUpForm(); } catch (err) { netNotes.up = { text: err.message, ok: false }; renderNetwork(netData); return; }
  if (settings.eagerness === 'stubborn' && !confirm('Stubborn may reset the radio and reboot the box while guests are on it. Use it?')) return;
  netRequest({ action: 'settings', settings }, 'up');
});
net.hold.addEventListener('click', () => netRequest({ action: 'hold', minutes: 60 }, 'up'));
net.unhold.addEventListener('click', () => netRequest({ action: 'hold', minutes: 0 }, 'up'));
window.addEventListener('hashchange', () => { if (location.hash === '#network') loadNetwork(); });
loadNetwork();

// --- the hotspot's own WiFi ----------------------------------------------------------------
// A choice kept in the hub's state (hotspot.py) for the hotspot add-on to apply: open, OWE,
// WPA3 with a published password, or two networks. Modes the radios cannot do are shown, greyed,
// with the reason; the chosen mode's warnings are listed under the choices.
const hs = {
  modes: document.getElementById('hs-modes'),
  fields: document.getElementById('hs-fields'),
  second: document.getElementById('hs-second'),
  secondLabel: document.getElementById('hs-second-label'),
  password: document.getElementById('hs-password'),
  passwordLabel: document.getElementById('hs-password-label'),
  generate: document.getElementById('hs-generate'),
  wpa2: document.getElementById('hs-wpa2'),
  wpa2Label: document.getElementById('hs-wpa2-label'),
  wpa2Desc: document.getElementById('hs-wpa2-desc'),
  warnings: document.getElementById('hs-warnings'),
  save: document.getElementById('hs-save'),
  note: document.getElementById('hs-note'),
};
let hsData = null;

function hsChosen() {
  const r = hs.modes.querySelector('input[name="hs-mode"]:checked');
  return r ? r.value : 'open';
}

function hsShowFields() {
  if (!hsData) return;
  const mode = hsChosen();
  const needsPw = mode === 'sae' || (mode === 'two' && hs.second.value === 'sae');
  hs.second.hidden = hs.secondLabel.hidden = mode !== 'two';
  hs.password.parentElement.hidden = hs.passwordLabel.hidden = !needsPw;
  hs.wpa2Label.hidden = !needsPw;
  hs.fields.hidden = mode !== 'two' && !needsPw;
  const warn = [...(hsData.warnings[mode] || [])];
  if (mode === 'two') warn.push(...(hsData.warnings[hs.second.value] || []).filter((w) => !/^Someone can still|^Anyone who knows/.test(w)));
  if (needsPw && hs.wpa2.checked) warn.push(hsData.wpa2_warning);
  hs.warnings.replaceChildren(...warn.map((w) => checkItem('warn', w, '', '')),
    checkItem('warn', hsData.always, '', ''));
}

function renderHotspot(data) {
  hsData = data;
  const s = data.settings;
  hs.modes.replaceChildren(...data.modes.map((m) => {
    const why = data.available[m];
    const input = el('input', { type: 'radio', name: 'hs-mode', value: m, checked: s.mode === m, disabled: !!why && s.mode !== m });
    input.addEventListener('change', hsShowFields);
    return el('label', { className: `hs-mode${why ? ' unavailable' : ''}` }, input,
      el('span', {}, el('span', { className: 'setting-name', textContent: data.label[m] }),
        el('span', { className: 'setting-desc', textContent: data.what[m] }),
        why ? el('span', { className: 'setting-desc bad', textContent: `Not available here: ${why}.` }) : null));
  }));
  hs.second.value = s.second;
  hs.password.value = s.password || '';
  hs.wpa2.checked = !!s.allow_wpa2;
  hs.wpa2Desc.textContent = 'Let older WPA2 devices join too (WPA3 transition mode).';
  hsShowFields();
}

async function loadHotspot() {
  try { renderHotspot(await getJSON('/admin/hotspot')); } catch (err) {
    console.error('hotspot section:', err);
    say('Could not read the hotspot settings.', false, hs.note);
  }
}

hs.second.addEventListener('change', hsShowFields);
hs.wpa2.addEventListener('change', hsShowFields);
hs.generate.addEventListener('click', () => {
  // Easy to read out and type on a phone: no 0/O, 1/l/I. getRandomValues works on plain HTTP.
  const abc = 'abcdefghjkmnpqrstuvwxyz23456789';
  const n = crypto.getRandomValues(new Uint32Array(12));
  const chars = [...n].map((v) => abc[v % abc.length]);
  hs.password.value = [0, 4, 8].map((i) => chars.slice(i, i + 4).join('')).join('-');
});
hs.save.addEventListener('click', async () => {
  const mode = hsChosen();
  if (mode !== 'open' && !confirm(`Use "${hsData.label[mode]}" for the hotspot? Read the warnings under the choices first.`)) return;
  try {
    const r = await postJSON('/admin/hotspot', { settings: { mode, second: hs.second.value, password: hs.password.value, allow_wpa2: hs.wpa2.checked } });
    say(r.message, true, hs.note);
    loadHotspot();
  } catch (err) { say(err.message, false, hs.note); }
});
loadHotspot();

// --- an offline kit (Backup) -------------------------------------------------------------
// The root helper makes it (hub_control.py offline_kit); the hub streams the download.
const kitEl = { form: document.getElementById('kit-form'), make: document.getElementById('kit-make'),
  books: document.getElementById('kit-books'), progress: document.getElementById('kit-progress'),
  bar: document.getElementById('kit-bar'), step: document.getElementById('kit-step'), note: noteEl('kit-note'),
  state: document.getElementById('kit-state'), details: document.getElementById('kit-details'),
  contents: document.getElementById('kit-contents') };
let kitWaiting = null;
let kitPoll = null;
function renderKit(d) {
  if (kitWaiting) {
    const done = (d.results || []).find((r) => r.id === kitWaiting);
    if (done) { say(done.message, done.ok, kitEl.note); kitWaiting = null; }
  }
  const busy = !!kitWaiting || d.pending > 0 || !!d.progress;
  kitEl.books.textContent = d.books.count
    ? `Include the books (${d.books.count}, ${size(d.books.bytes)})` : 'Include the books (this box has none)';
  kitEl.form.elements.books.disabled = !d.books.count;
  kitEl.make.disabled = busy;
  kitEl.progress.hidden = !d.progress;
  if (d.progress) {
    const p = d.progress;
    kitEl.bar.value = Math.min(Math.max(p.step - 1, 0) / Math.max(p.steps, 1), 1);
    kitEl.step.textContent = `Step ${Math.max(p.step, 1)} of ${p.steps}${p.label ? ` — ${p.label}` : ''}.`;
  }
  const k = d.kit;
  kitEl.state.replaceChildren(...(k ? [
    el('a', { href: '/admin/kit/download', download: k.name, textContent: `Download ${k.name}` }),
    document.createTextNode(` — ${size(k.size)}, made ${new Date(k.at * 1000).toLocaleString()}` +
      (k.books.length ? `, with ${k.books.length} book${k.books.length === 1 ? '' : 's'}` : ', without books') + '.'),
  ] : [document.createTextNode(busy ? 'Making the kit…' : 'No kit made yet.')]));
  kitEl.details.hidden = !(k && k.contents && k.contents.length);
  if (k) kitEl.contents.replaceChildren(...(k.contents || []).map((t) => el('li', { textContent: t })));
  clearTimeout(kitPoll);
  if (busy) kitPoll = setTimeout(loadKit, 2000);
}
async function loadKit() {
  try { renderKit(await getJSON('/admin/kit')); } catch (err) { console.error('kit:', err); }
}
kitEl.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    kitWaiting = (await postJSON('/admin/kit', { action: 'make', books: kitEl.form.elements.books.checked })).id;
    say('Making the kit: a minute or two, longer with books.', true, kitEl.note);
    loadKit();
  } catch (err) { say(err.message, false, kitEl.note); }
});
window.addEventListener('hashchange', () => { if (location.hash === '#backup') loadKit(); });
loadKit();

// --- who can open each app --------------------------------------------------------------
// Public, private or off (access.py): a three-way switch on each app (Apps), each add-on
// (Add-ons) and the hub's own parts (Apps, Built into the hub). The root helper rewrites the
// web server's part and answers; each switch keeps its node, so a list that re-renders moves
// it rather than losing what it shows.
const ACCESS_LABEL = { public: '🌐 Public', private: '🔒 Private', off: '⭘ Off' };
const accessNodes = new Map(); // id -> { node, app }
let accessWaiting = null; // { id, app }
let accessNote = null; // { app, text, ok }
let accessPoll = null;

function accessDesc(a) {
  if (a.mode === 'public') return a.login ? 'Public: on the home page; it still asks for the admin login.' : 'Public: on the home page, open to everyone on the network.';
  if (a.mode === 'private') return 'Private: not on the home page; its address asks for the admin login.';
  return `Off: not on the home page; its address answers "not found"${a.unit ? ', and its service is stopped' : ''}.`;
}

// A list may render before the choices arrive: its slot is made empty and filled in later.
function accessSlot(id) {
  if (!accessNodes.has(id)) accessNodes.set(id, { node: el('span', { className: 'access' }) });
  return accessNodes.get(id).node;
}

function accessSet(a, mode) {
  if (mode === a.mode) return;
  if (mode === 'off' && a.unit && !confirm(`Turn ${a.title} off? Its service stops until you switch it back on.`)) return;
  if (mode === 'public' && a.mode !== 'public' && !a.login
    && !confirm(`Make ${a.title} public? Everyone on the box's network can open it, with no login.`)) return;
  postJSON('/admin/access', { app: a.id, mode }).then((r) => {
    accessWaiting = { id: r.id, app: a.id };
    accessNote = null;
    loadAccess();
  }, (err) => { accessNote = { app: a.id, text: err.message, ok: false }; loadAccess(); });
}

function renderAccess(data) {
  if (accessWaiting) {
    const done = (data.results || []).find((r) => r.id === accessWaiting.id);
    if (done) {
      accessNote = { app: accessWaiting.app, text: done.message, ok: done.ok };
      accessWaiting = null;
      return loadAccess();
    }
  }
  const builtin = [];
  for (const a of data.apps) {
    const slot = accessSlot(a.id);
    const group = el('span', { className: 'access-toggle' },
      ...['public', 'private', 'off'].map((mode) => {
        const b = el('button', { type: 'button', textContent: ACCESS_LABEL[mode], disabled: !!accessWaiting, onclick: () => accessSet(a, mode) });
        b.setAttribute('role', 'radio');
        b.setAttribute('aria-checked', String(a.mode === mode));
        return b;
      }));
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', `Who can open ${a.title}`);
    const note = accessNote && accessNote.app === a.id
      ? el('span', { className: `setting-desc action-note${accessNote.ok ? '' : ' bad'}`, role: 'status', textContent: accessNote.text }) : null;
    slot.replaceChildren(group, el('span', { className: 'setting-desc', textContent: accessWaiting && accessWaiting.app === a.id ? 'Changing…' : accessDesc(a) }), note);
    if (a.kind === 'builtin') builtin.push(a);
  }
  document.getElementById('builtin-list').replaceChildren(...builtin.map((a) => el('div', { className: 'setting library-source' },
    el('span', {}, el('span', { className: 'setting-name', textContent: a.title }), accessSlot(a.id)))));
  clearTimeout(accessPoll);
  if (accessWaiting) accessPoll = setTimeout(loadAccess, 1000);
}

async function loadAccess() {
  try { renderAccess(await getJSON('/admin/access')); } catch (err) {
    console.error('access:', err);
    clearTimeout(accessPoll);
    if (accessWaiting) accessPoll = setTimeout(loadAccess, 3000);
  }
}
loadAccess();

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
      accessSlot(a.id),
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

// --- books on a USB stick ---------------------------------------------------------------
// The root helper mounts a stick only for a scan or a copy (usbstick.py); this lists what the
// last scan found, offers Import for each book and Export of the hub's books, and shows the
// copy's bar.
const usb = {
  scan: document.getElementById('usb-scan'),
  note: document.getElementById('usb-note'),
  progress: document.getElementById('usb-progress'),
  bar: document.getElementById('usb-bar'),
  step: document.getElementById('usb-step'),
  devices: document.getElementById('usb-devices'),
};
let usbWaiting = null; // { id, at }
let usbNote = null; // { at, text, ok }
let usbPoll = null;

function renderUsb(data) {
  const p = data.progress;
  const busy = data.pending > 0 || !!usbWaiting || !!p;
  if (usbWaiting) {
    const done = (data.results || []).find((r) => r.id === usbWaiting.id);
    if (done) {
      usbNote = { at: usbWaiting.at, text: done.message, ok: done.ok };
      usbWaiting = null;
      if (done.ok) loadLibrary();
      return renderUsb(data);
    }
  }
  const noteAt = (at) => (usbNote && usbNote.at === at
    ? el('span', { className: `setting-desc action-note${usbNote.ok ? '' : ' bad'}`, role: 'status', textContent: usbNote.text }) : null);
  say(usbNote && usbNote.at === 'scan' ? usbNote.text : '', usbNote ? usbNote.ok : true, usb.note);
  usb.scan.disabled = busy;
  const devs = (data.scan && data.scan.devices) || [];
  usb.devices.replaceChildren(...devs.map((d) => {
    const pick = el('select', { disabled: busy || !data.books.length },
      ...data.books.map((b) => el('option', { value: b, textContent: `${b}.zim` })));
    return el('div', { className: 'setting library-source' }, el('span', {},
      el('span', { className: 'setting-name', textContent: `${d.label || d.name} (${d.fstype}, ${size(Number(d.size))})` }),
      d.error ? el('span', { className: 'setting-desc bad', textContent: d.error }) : null,
      ...(d.zims.length ? d.zims.map((z) => el('span', { className: 'usb-book' },
        el('span', { className: 'setting-desc', textContent: `${z.file} · ${size(z.size)}${z.zim ? '' : ` · cannot be imported: ${z.problem || 'not a ZIM file'}`}` }),
        z.zim ? el('button', { type: 'button', className: 'small', textContent: 'Import', disabled: busy,
          onclick: () => usbRequest({ action: 'import', device: d.name, file: z.file }, `${d.name}:${z.file}`,
            `Copy ${z.file} into the library (${size(z.size)})?`) }) : null,
        noteAt(`${d.name}:${z.file}`)))
        : [el('span', { className: 'setting-desc', textContent: d.error ? '' : 'No books on this stick.' })]),
      data.books.length ? el('span', { className: 'library-buttons' }, pick,
        el('button', { type: 'button', textContent: 'Export to this stick', disabled: busy,
          onclick: () => usbRequest({ action: 'export', device: d.name, book: pick.value }, `export:${d.name}`,
            `Copy ${pick.value}.zim onto ${d.label || d.name}?`) })) : null,
      noteAt(`export:${d.name}`)));
  }));
  if (data.scan && !devs.length) usb.devices.replaceChildren(el('p', { className: 'setting-desc', textContent: 'No USB stick found. Plug one into the box and scan again.' }));
  usb.progress.hidden = !p;
  if (p) {
    usb.bar.value = p.total ? Math.min(p.done / p.total, 1) : 0;
    usb.step.textContent = `${p.label}${p.total ? `: ${size(p.done)} of ${size(p.total)} (${Math.round((100 * p.done) / p.total)}%)` : '…'}`;
  }
  clearTimeout(usbPoll);
  if (busy) usbPoll = setTimeout(loadUsb, 1000);
}

async function loadUsb() {
  try { renderUsb(await getJSON('/admin/usb')); } catch (_) { /* shown on the next poll */ }
}

async function usbRequest(body, at, confirmText) {
  if (confirmText && !confirm(confirmText)) return;
  try {
    usbWaiting = { id: (await postJSON('/admin/usb', body)).id, at };
    usbNote = null;
    loadUsb();
  } catch (err) { usbNote = { at, text: err.message, ok: false }; loadUsb(); }
}

usb.scan.addEventListener('click', () => usbRequest({ action: 'scan' }, 'scan'));
loadUsb();

// --- git: the two servers' repositories (gitrepos.py) ----------------------------------
// The hub owns the repositories, so these changes need no root helper: each answer is the
// new list.
const git = {
  usage: noteEl('git-usage'), note: noteEl('git-note'), guest: noteEl('git-guest-push'),
  form: document.getElementById('git-create'), createNote: noteEl('git-create-note'),
  lists: { public: noteEl('git-public'), private: noteEl('git-private') },
};
const commitDate = (unix) => new Date(unix * 1000).toISOString().slice(0, 10);

function renderGit(data) {
  if (!data.installed) {
    git.usage.textContent = 'The git servers are not set up on this box: rerun install.sh.';
    git.form.hidden = true;
    return;
  }
  git.form.hidden = false;
  const total = data.repos.reduce((n, r) => n + r.size, 0);
  git.usage.textContent = `${data.repos.length} repositor${data.repos.length === 1 ? 'y' : 'ies'}, ${size(total)}` +
    (data.free != null ? `; ${size(data.free)} free on the card` : '') +
    `. A single push can be up to ${size(data.max_push)}.`;
  git.guest.checked = data.guest_push;
  const act = (body, at, confirmText) => async () => {
    if (confirmText && !confirm(confirmText)) return;
    try { renderGit(await postJSON('/admin/git', body)); say('', true, at); } catch (err) { say(err.message, false, at); }
  };
  for (const area of ['public', 'private']) {
    const repos = data.repos.filter((r) => r.area === area);
    git.lists[area].replaceChildren(...(repos.length ? repos.map((r) => el('div', { className: 'admin-item' },
      el('span', {},
        el('strong', {}, el('a', { href: r.url, textContent: `${r.name}.git` })),
        el('span', { className: 'setting-desc', textContent: [r.description,
          r.last_commit ? `last commit ${commitDate(r.last_commit)}` : 'empty',
          r.branches > 1 ? `${r.branches} branches` : null, size(r.size)].filter(Boolean).join(' · ') })),
      el('span', { className: 'library-buttons' },
        actionButton('Copy clone URL', () => {
          const url = `${location.origin}${r.url.replace(/\/$/, '')}`;
          navigator.clipboard?.writeText(url).then(() => say(`Copied ${url}`, true, git.note),
            () => say(url, true, git.note));
        }, { className: 'small' }),
        actionButton('Describe', () => {
          const d = prompt(`Description for ${r.name}.git`, r.description);
          if (d !== null) act({ action: 'describe', area, name: r.name, description: d }, git.note)();
        }, { className: 'small' }),
        actionButton('Delete', act({ action: 'delete', area, name: r.name }, git.note,
          `Delete ${r.name}.git and all its history? Clones elsewhere keep theirs; this one cannot be brought back.`),
        { className: 'small' }))))
      : [el('p', { className: 'setting-desc', textContent: 'None yet.' })]));
  }
}

async function loadGit() {
  try { renderGit(await getJSON('/admin/git')); } catch (_) { git.usage.textContent = 'Could not read the repositories.'; }
}

git.guest.addEventListener('change', async () => {
  const on = git.guest.checked;
  if (on && !confirm('Let anyone on the network push to the public repositories, without the login?')) {
    git.guest.checked = false;
    return;
  }
  try {
    renderGit(await postJSON('/admin/git', { action: 'guest-push', on }));
    say(on ? 'Guests can push to /git/ now.' : 'Pushing to /git/ needs the admin login again.', true, git.note);
  } catch (err) { git.guest.checked = !on; say(err.message, false, git.note); }
});
git.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = git.form.elements;
  try {
    renderGit(await postJSON('/admin/git', { action: 'create', area: f.area.value, name: f.name.value, description: f.description.value }));
    say(`Created ${f.name.value.replace(/\.git$/, '')}.git. Push to it with: git push http://${location.host}${f.area.value === 'public' ? '/git/' : '/git-private/'}${f.name.value.replace(/\.git$/, '')}.git main`, true, git.createNote);
    git.form.reset();
  } catch (err) { say(err.message, false, git.createNote); }
});
loadGit();

// --- builds on push (ci.py): the newest runs, refreshed while one is going -----------------
const ciAbout = noteEl('ci-about');
const ciRuns = noteEl('ci-runs');
let ciPoll = null;
const STATE_ICON = { passed: '✅', failed: '❌', 'timed out': '⏱', running: '⏳' };
const minutes = (s) => (s < 90 ? `${s} s` : `${Math.round(s / 60)} min`);

function renderCi(data) {
  if (!data.installed) { ciAbout.textContent = 'Builds are not set up on this box: rerun install.sh.'; return; }
  ciAbout.textContent = `A push to a private repository whose commit has a ${data.script} at its top builds it here: ` +
    `a fresh clone, then bash ${data.script}, as a user of its own, at the lowest priority, for up to ` +
    `${Math.round(data.time_limit / 3600)} hours. Public repositories never build.` +
    (data.queued ? ` ${data.queued} waiting.` : '');
  const fileUrl = (r, name) => `/admin/ci/file?run=${encodeURIComponent(r.run)}&name=${encodeURIComponent(name)}`;
  ciRuns.replaceChildren(...(data.runs.length ? data.runs.map((r) => el('div', { className: 'admin-item' },
    el('span', {},
      el('strong', { textContent: `${STATE_ICON[r.state] || ''} ${r.repo} · ${r.branch} · ${r.commit.slice(0, 7)}` }),
      el('span', { className: 'setting-desc', textContent: [r.state,
        r.duration != null ? minutes(r.duration) : null,
        r.started ? commitDate(r.started) : null].filter(Boolean).join(' · ') })),
    el('span', { className: 'library-buttons' },
      el('a', { href: fileUrl(r, 'log.txt'), textContent: 'Log', target: '_blank', className: 'small' }),
      ...(r.artifacts || []).map((a) => el('a', { href: fileUrl(r, a), textContent: a, className: 'small' })))))
    : [el('p', { className: 'setting-desc', textContent: 'No builds yet.' })]));
  clearTimeout(ciPoll);
  if (data.queued || data.runs.some((r) => r.state === 'running')) ciPoll = setTimeout(loadCi, 5000);
}

async function loadCi() {
  try { renderCi(await getJSON('/admin/ci')); } catch (_) { ciAbout.textContent = 'Could not read the builds.'; }
}
loadCi();

// --- firmware for the web flasher (firmware.py) ---------------------------------------------
// The board picker lists what the newest kept release offers; until a first check has run,
// there is nothing to pick yet, and Check now fetches the list.
const fw = {
  form: document.getElementById('fw-form'), status: noteEl('fw-status'), note: noteEl('fw-note'),
  boards: noteEl('fw-boards'), filter: noteEl('fw-filter'), kept: noteEl('fw-kept'),
};
let fwPoll = null;
let fwChosen = new Set();

function renderFirmware(data) {
  const cfg = data.settings;
  const st = data.status || {};
  const f = fw.form.elements;
  if (!fw.form.contains(document.activeElement)) {
    f.enabled.checked = cfg.enabled;
    f.configs.checked = cfg.configs !== false;
    f.keep_alpha.value = cfg.keep_alpha;
    f.keep_beta.value = cfg.keep_beta;
    f.cache.value = cfg.cache;
    f.all_boards.checked = cfg.boards === 'all';
    fwChosen = new Set(cfg.boards === 'all' ? [] : cfg.boards);
  }
  const running = data.running ? (data.progress
    ? ` Working: ${data.progress.name} (${size(data.progress.done)}${data.progress.total ? ` of ${size(data.progress.total)}` : ''}).`
    : ' Working…') : '';
  fw.status.textContent = (st.last_check ? `Last checked ${st.last_check.replace('T', ' ').replace('Z', ' UTC')}: ${st.outcome || ''}.`
    : 'Not checked yet.') + (st.error ? ` Error: ${st.error}` : '') + running +
    (data.free_mb != null ? ` ${size(data.free_mb * 2 ** 20)} free.` : '');
  const q = fw.filter.value.trim().toLowerCase();
  const targets = (data.targets || []).filter((t) => !q || t.board.includes(q) || t.platform.includes(q));
  fw.boards.hidden = f.all_boards.checked;
  fw.boards.replaceChildren(...(data.targets && data.targets.length ? targets.map((t) => el('label', { className: 'inline' },
    el('input', { type: 'checkbox', checked: fwChosen.has(t.board),
      onchange: (e) => { if (e.target.checked) fwChosen.add(t.board); else fwChosen.delete(t.board); } }),
    ` ${t.board} `, el('span', { className: 'setting-desc', textContent: t.platform })))
    : [el('p', { className: 'setting-desc', textContent: 'No board list yet: switch it on, Save, then Check now.' })]));
  const versions = Object.entries(st.versions || {});
  fw.kept.replaceChildren(...(versions.length ? versions.map(([v, info]) => el('div', { className: 'admin-item' },
    el('span', {}, el('strong', { textContent: `${v} (${info.channel})` }),
      el('span', { className: 'setting-desc', textContent: `${info.boards.length} board${info.boards.length === 1 ? '' : 's'}, ` +
        `${info.files} files, ${size(info.bytes)}` + (info.missing && info.missing.length ? `; not in this release: ${info.missing.join(', ')}` : '') +
        (st.cache && st.cache.version === v ? `; build cache (${st.cache.mode}) ${size(st.cache.bytes)}` : '') }))))
    : [el('p', { className: 'setting-desc', textContent: 'Nothing kept yet.' })]));
  clearTimeout(fwPoll);
  if (data.running) fwPoll = setTimeout(loadFirmware, 2000);
}

let fwData = null;
async function loadFirmware() {
  try { fwData = await getJSON('/admin/firmware'); renderFirmware(fwData); } catch (_) { fw.status.textContent = 'Could not read the firmware settings.'; }
}
fw.filter.addEventListener('input', () => fwData && renderFirmware(fwData));
fw.form.elements.all_boards.addEventListener('change', () => fwData && renderFirmware(fwData));
fw.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = fw.form.elements;
  try {
    fwData = await postJSON('/admin/firmware', { action: 'settings', enabled: f.enabled.checked, configs: f.configs.checked,
      keep_alpha: Number(f.keep_alpha.value), keep_beta: Number(f.keep_beta.value), cache: f.cache.value,
      boards: f.all_boards.checked ? 'all' : [...fwChosen] });
    renderFirmware(fwData);
    say('Saved. Update now fetches what is missing; the schedule does the rest.', true, fw.note);
  } catch (err) { say(err.message, false, fw.note); }
});
for (const [id, action] of [['fw-check', 'check'], ['fw-update', 'update']]) {
  document.getElementById(id).addEventListener('click', async () => {
    try { fwData = await postJSON('/admin/firmware', { action }); renderFirmware(fwData); say('', true, fw.note); } catch (err) { say(err.message, false, fw.note); }
  });
}
loadFirmware();
