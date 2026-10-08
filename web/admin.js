// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Every /admin change carries X-Irate-Admin, which the hub requires (server.py _forged): a page
// elsewhere cannot send it with the owner's cached login.
const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
// Admin options. The gate is the web server's basic auth on /admin/* -- by the time this page
// loads, the operator has already authenticated. Each toggle saves on change; there is
// no Save button to forget to press.

// --- the menu --------------------------------------------------------------------------
// Built by admin-layout.js (AL) from its table and the apps' manifests (menu overhaul M4): pages
// holding the sections below, the address naming a section. Everything keeps loading in the
// background, so the sidebar's badges stay current whichever page is open.
const showPane = AL.show;
// A word beside a section's sidebar entry: "new", "working", ... or nothing.
const badge = AL.badge;
// Is this section on the page that's showing? (The loaders that wait to be seen.)
const paneShown = AL.shown;

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
      headers: ADMIN_HEADERS,
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
      headers: ADMIN_HEADERS,
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
const where = (s) => s.type === 'kiwix' ? `Kiwix's catalogue, ${s.kiwix_name}${s.flavour ? ` (${s.flavour})` : ''}` : s.type === 'url' ? s.url
  : `${s.repo}${s.workflow ? ` · ${s.workflow}` : ''}${s.branch ? ` @ ${s.branch}` : ''} · ${s.pattern}`;

async function libPost(body) {
  const r = await fetch('/admin/library', {
    method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body),
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
    // A pinned app (librarian.py, "Latest and pinned") installs the build its source follows.
    const follow = a.pin ? (src && src.follow) || st.follow || 'pinned' : 'latest';
    const target = follow === 'pinned' ? st.pinned : st.latest;
    const newer = !!(target && st.current) && target.version !== st.current.version;
    const fetched = newer && st.fetched && st.fetched.version === target.version ? st.fetched : null;
    const lines = [
      inst === null ? 'Not installed.' : inst.commit
        ? `Installed: ${inst.commit.slice(0, 7)} from ${inst.repository} (${inst.ref}), built ${String(inst.built).slice(0, 10)}.`
        : 'Installed by hand (no bundle record): the first update replaces it.',
      src ? (src.type === 'git' ? `Source: git, ${src.repo} @ ${src.branch}, adapted for the hub.`
        : `Source: ${src.type}, ${src.repo} · ${src.workflow}${src.branch ? ` @ ${src.branch}` : ''}.`) : 'Not kept current: it has no update source yet.',
      a.pin ? `Pinned (last known good): ${a.pin.slice(0, 7)}. Newest: ${st.latest ? st.latest.label : 'not checked yet'}. ` +
        (follow === 'pinned' ? 'Installs the pinned one.' : 'Installs the newest; if it fails, keeps what it has (or the pinned one).') : null,
      st.latest_failed && follow === 'latest' ? `The newest (${st.latest_failed.label}) failed: ${st.latest_failed.error}` : null,
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
          onclick: post(`app:${name}`, { action: 'rollback', name }, `Go back to the previous ${a.title} build?`) }) : null,
        a.pin ? el('button', { type: 'button', textContent: follow === 'pinned' ? 'Follow the newest' : 'Follow the pin', disabled: busy,
          onclick: post(`app:${name}`, { action: 'add', source: { ...src, follow: follow === 'pinned' ? 'latest' : 'pinned' } },
            follow === 'pinned' ? `Install the newest ${a.title} from now on, rather than the pinned commit the hub's maintainers checked? Nobody will have looked at it first.` : null) }) : null)
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

  // The books themselves are a page at a time (loadBooks): reread when the librarian finishes.
  if (libWasBusy && !busy) loadBooks();
  libWasBusy = busy;
  libBusy = busy;
  document.querySelectorAll('[data-all]').forEach((b) => { b.disabled = busy; });
  document.querySelectorAll('[data-bulk]').forEach((b) => { b.disabled = busy; });
  const allNote = noteFor('all');
  lib.allNote.hidden = !allNote;
  if (allNote) { lib.allNote.textContent = allNote.textContent; lib.allNote.className = allNote.className; }

  for (const [k, v] of Object.entries(snap.policy)) {
    const field = lib.policy.elements[k];
    if (field && document.activeElement !== field) field.value = String(v);
  }
  // GitHub's hourly allowance, as the last check saw it (librarian.py: rate): low, the scheduled
  // checks wait for the next hour rather than fail.
  const gh = snap.github || {};
  const resets = gh.reset ? new Date(gh.reset * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  lib.tokenState.textContent = (snap.token_set ? 'A token is set.' : 'No token is set.')
    + (gh.limit && gh.reset * 1000 > Date.now() ? ` GitHub: ${gh.remaining} of ${gh.limit} requests left this hour (until ${resets})`
      + (gh.remaining < 10 ? '; scheduled checks wait for the next hour.' : '.') : '');

  clearTimeout(libPoll);
  // Also while an app the librarian fetched is still with the root helper.
  const installing = Object.values(snap.status).some((e) => e.pending && !e.install_result);
  if (busy || installing) libPoll = setTimeout(loadLibrary, 3000);
}

async function loadLibrary() {
  try {
    const r = await fetch('/admin/library?books=0');
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
  for (const k of ['keep_old', 'check_every_hours', 'min_free_mb', 'books_budget_mb', 'auto_install']) body[k] = Number(lib.policy.elements[k].value);
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

// --- the books, a page at a time (step 14: /admin/books) ----------------------------------------
let libWasBusy = false;
let libBusy = false;
const bk = {
  q: document.getElementById('books-q'), language: document.getElementById('books-language'), state: document.getElementById('books-state'),
  kept: document.getElementById('books-kept'), sort: document.getElementById('books-sort'), body: document.querySelector('#books-table tbody'),
  pageAll: document.getElementById('books-page-all'), summary: document.getElementById('books-summary'), page: document.getElementById('books-page'),
  prev: document.getElementById('books-prev'), next: document.getElementById('books-next'), bulk: document.getElementById('books-bulk'),
  selected: document.getElementById('books-selected'), matching: document.getElementById('books-select-matching'),
  none: document.getElementById('books-select-none'),
};
const bookSel = new Set();
let bookPage = 1;
let bookData = null;
let bookOpen = null;
let bookTimer = null;
const STATE_WORDS = { ok: 'up to date', newer: 'newer available', failed: 'check failed', 'not installed': 'not installed yet', unreadable: 'Kiwix cannot read it' };
const bookQuery = (extra = {}) => new URLSearchParams({ q: bk.q.value.trim(), language: bk.language.value, state: bk.state.value,
  kept: bk.kept.value, sort: bk.sort.value, page: String(bookPage), ...extra }).toString();

async function loadBooks() {
  try { renderBooks(await getJSON(`/admin/books?${bookQuery()}`)); } catch (_) { bk.summary.textContent = 'Could not read the books.'; }
}

function renderBooks(d) {
  bookData = d;
  bookPage = d.page;
  const sm = d.summary;
  setupStep('books', sm.kept ? `${sm.kept} kept current by the librarian.` : 'None kept current yet: Kiwix serves only books copied in by hand.', sm.kept ? 'ok' : 'warn');
  const st = sm.states;
  bk.summary.textContent = `${sm.count} book${sm.count === 1 ? '' : 's'}, ${size(sm.bytes)}`
    + (sm.budget_mb ? ` of a ${size(sm.budget_mb * 2 ** 20)} budget (${Math.round((100 * sm.bytes) / (sm.budget_mb * 2 ** 20))} %)` : '')
    + `; ${sm.kept} kept current.`
    + ['newer', 'failed', 'unreadable', 'not installed'].filter((k) => st[k]).map((k) => ` ${st[k]} ${STATE_WORDS[k]}.`).join('');
  const langs = Object.keys(sm.languages).sort();
  if (bk.language.dataset.langs !== langs.join(',')) {
    const was = bk.language.value;
    bk.language.dataset.langs = langs.join(',');
    bk.language.replaceChildren(el('option', { value: '', textContent: 'any' }), ...langs.map((l) => el('option', { value: l, textContent: `${l} (${sm.languages[l]})` })));
    bk.language.value = langs.includes(was) ? was : '';
  }
  const rows = [];
  for (const b of d.books) {
    const box = el('input', { type: 'checkbox', checked: bookSel.has(b.name), ariaLabel: `Select ${b.title}`,
      onchange: (e) => { if (e.target.checked) bookSel.add(b.name); else bookSel.delete(b.name); renderBookBulk(); } });
    const open = bookOpen === b.name;
    rows.push(el('tr', { className: `book-row state-${b.state.replace(' ', '-')}` },
      el('td', {}, box),
      el('td', {}, el('button', { type: 'button', className: 'link-button', textContent: b.title, ariaExpanded: String(open),
        onclick: () => { bookOpen = open ? null : b.name; renderBooks(bookData); } }),
        b.title !== b.name ? el('span', { className: 'setting-desc', textContent: ` ${b.name}.zim` }) : null),
      el('td', { textContent: b.language || '—' }), el('td', { textContent: b.size ? size(b.size) : '—' }), el('td', { textContent: b.date || '—' }),
      el('td', { textContent: b.kept ? `yes (${b.source.type})` : 'no' }),
      el('td', { className: b.state === 'ok' ? '' : 'bad', textContent: STATE_WORDS[b.state] || b.state })));
    if (open) {
      rows.push(el('tr', { className: 'book-card' }, el('td', { colSpan: 7 }, b.source ? sourceRow(b.source, b.status || {}, libBusy)
        : el('p', { className: 'setting-desc', textContent: `${b.name}.zim was put here by hand or from a USB stick: the librarian leaves it alone. `
          + (b.description ? `“${b.description}”` : '') }))));
    }
  }
  bk.body.replaceChildren(...(rows.length ? rows : [el('tr', {}, el('td', { colSpan: 7, className: 'setting-desc', textContent: sm.count ? 'No book matches.' : 'No books yet.' }))]));
  bk.pageAll.checked = d.books.length > 0 && d.books.every((b) => bookSel.has(b.name));
  bk.page.textContent = `Page ${d.page} of ${d.pages} (${d.matching} matching)`;
  bk.prev.disabled = d.page <= 1;
  bk.next.disabled = d.page >= d.pages;
  renderBookBulk();
}

function renderBookBulk() {
  const n = bookSel.size;
  bk.bulk.hidden = !n && !(bookData && bookData.matching > bookData.books.length);
  bk.selected.textContent = n ? `${n} selected:` : '';
  bk.bulk.querySelectorAll('[data-bulk]').forEach((b) => { b.hidden = !n; });
  bk.matching.textContent = bookData ? `Select all ${bookData.matching} matching` : '';
  bk.matching.hidden = !bookData || bookData.matching <= n;
  bk.none.hidden = !n;
}

const rebook = () => { bookPage = 1; clearTimeout(bookTimer); bookTimer = setTimeout(loadBooks, 250); };
bk.q.addEventListener('input', rebook);
[bk.language, bk.state, bk.kept, bk.sort].forEach((s) => s.addEventListener('change', rebook));
bk.prev.addEventListener('click', () => { bookPage -= 1; loadBooks(); });
bk.next.addEventListener('click', () => { bookPage += 1; loadBooks(); });
bk.pageAll.addEventListener('change', () => {
  (bookData ? bookData.books : []).forEach((b) => { if (bk.pageAll.checked) bookSel.add(b.name); else bookSel.delete(b.name); });
  renderBooks(bookData);
});
bk.matching.addEventListener('click', async () => {
  try { (await getJSON(`/admin/books?${bookQuery({ names: '1' })}`)).names.forEach((n) => bookSel.add(n)); renderBooks(bookData); } catch (err) { say(err.message, false, lib.allNote); }
});
bk.none.addEventListener('click', () => { bookSel.clear(); renderBooks(bookData); });
document.querySelectorAll('[data-bulk]').forEach((b) => b.addEventListener('click', async () => {
  // Every selected name, on any page: the librarian skips one with no source (put here by hand).
  const names = [...bookSel];
  if (b.dataset.bulk === 'untrack') {
    if (!confirm(`Stop keeping ${names.length} book${names.length === 1 ? '' : 's'} current? The books themselves stay in the library.`)) return;
    for (const name of names) {
      try { await libPost({ action: 'remove', name }); } catch (_) { /* not tracked: nothing to stop */ }
    }
    bookSel.clear();
    loadLibrary();
    loadBooks();
    return;
  }
  libAct('all', { action: b.dataset.bulk, names });
}));

// --- Kiwix's catalogue (step 14: /admin/catalogue), online only ---------------------------------
const cat = { form: document.getElementById('catalogue-form'), results: document.getElementById('catalogue-results'),
  note: noteEl('catalogue-note'), pager: document.getElementById('catalogue-pager'), more: document.getElementById('catalogue-more') };
let catStart = 0;
async function searchCatalogue(more) {
  const f = cat.form.elements;
  catStart = more ? catStart + 20 : 0;
  say('Asking Kiwix\'s catalogue…', true, cat.note);
  try {
    const r = await fetch(`/admin/catalogue?${new URLSearchParams({ q: f.q.value.trim(), language: f.language.value.trim(), start: String(catStart) })}`);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    say(`${d.total} book${d.total === 1 ? '' : 's'} in the catalogue match.`, true, cat.note);
    const rows = d.entries.map((e) => {
      const name = e.name.replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 64);
      return el('div', { className: 'admin-item' },
        el('span', { className: 'setting-name', textContent: e.title }),
        el('span', { className: 'setting-desc', textContent: ` ${e.language}, ${size(e.size)}, ${e.updated}${e.flavour ? `, ${e.flavour}` : ''}; ${e.articles} articles. ${e.summary}` }),
        el('span', { className: 'library-buttons' }, actionButton('Keep current here', async () => {
          try {
            await libPost({ action: 'add', source: { name: e.flavour ? `${name}_${e.flavour}`.slice(0, 64) : name, type: 'kiwix', kiwix_name: e.name, flavour: e.flavour } });
            say(`Added ${e.title}: Update in the table above fetches it (${size(e.size)}).`, true, cat.note);
            loadBooks();
          } catch (err) { say(err.message, false, cat.note); }
        })));
    });
    if (more) cat.results.append(...rows); else cat.results.replaceChildren(...rows);
    cat.pager.hidden = catStart + 20 >= d.total;
  } catch (err) { say(err.message, false, cat.note); }
}
cat.form.addEventListener('submit', (e) => { e.preventDefault(); searchCatalogue(false); });
cat.more.addEventListener('click', () => searchCatalogue(true));

showTypeFields();
loadLibrary();
loadBooks();

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
    method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body),
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

// The services' uptime (step 35): a row per service, the last 72 hours by hour and 72 days by
// day (githubstatus.com's format), from the hub's five-minute samples (svchistory.py); a dot
// where it started (a reboot starts them all).
function renderServiceUptime(services, u) {
  const body = document.getElementById('svc-uptime-body');
  const units = (u && u.units) || {};
  const mine = services.filter((s) => s.unit && units[s.unit]);
  if (!mine.length) {
    body.replaceChildren(el('p', { className: 'setting-desc', textContent: 'Nothing recorded yet: the hub looks at every service every five minutes, '
      + 'once the box\'s clock is known to be right (network time, or set on Clock), and keeps 72 days.' }));
    return;
  }
  const said = (s) => { const sm = units[s.unit].summary;
    return sm.up == null ? `${s.name}: no data lately` : `${s.name}: up ${Heatmap.percent(sm.up)}${sm.restarts ? `, started ${sm.restarts} time${sm.restarts === 1 ? '' : 's'}` : ''}`; };
  const dayLabel = (back) => u.month_days[71 - back].label;
  body.replaceChildren(
    el('p', { className: 'setting-desc', textContent: mine.map(said).join('; ') + '.' }),
    el('p', { className: 'setting-desc', textContent: 'The last 72 hours, by hour:' }),
    Heatmap.grid({ caption: 'Each service, the last 72 hours by hour', cols: u.hour_cols,
      rows: mine.map((s) => ({ label: s.name, cells: units[s.unit].hours, where: (i) => `${s.name}, ${u.hour_full[i]}` })) }),
    el('p', { className: 'setting-desc', textContent: 'The last 72 days, by day:' }),
    Heatmap.grid({ caption: 'Each service, the last 72 days by day', cols: Array.from({ length: 72 }, (_, i) => (i % 12 ? '' : dayLabel(71 - i))),
      rows: mine.map((s) => ({ label: s.name, cells: units[s.unit].month, where: (i) => `${s.name}, ${dayLabel(71 - i)}` })) }),
    Heatmap.legend(),
    el('p', { className: 'setting-desc', textContent: 'A dot marks an hour or a day in which the service started: restarted, or the box rebooted.' }));
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
  renderServiceUptime(data.services, data.service_uptime);

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
  renderReports(data, del);
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

// The queue across the apps (M10): what was reported enough to count, worst first; Keep or Delete.
function renderReports(data, del) {
  const queue = data.queue || [], rs = data.reports;
  badge('moderation', queue.length ? String(queue.length) : '');
  const q = document.getElementById('mod-queue');
  if (q) q.replaceChildren(queue.length ? AW.shortList(queue.map((r) => ({ id: r.key, title: r.text.slice(0, 80) || '(empty)',
    summary: `${r.where} · by ${r.by} · ${r.count} report${r.count === 1 ? '' : 's'}`,
    badges: Object.keys(r.reasons || {}),
    detail: () => [
      AW.h('p', { class: 'admin-text', text: r.text }),
      AW.dl(Object.fromEntries(Object.entries(r.reasons || {}).map(([k, n]) => [k, `${n}`]))),
      AW.btn('Keep it', { onclick: async () => { try { renderModeration(await postJSON('/admin/moderation', { action: 'keep', key: r.key })); } catch (err) { say(err.message, false, noteEl('mod-note')); } } }),
      ' ',
      AW.btn('Delete it', { onclick: r.key.startsWith('shoutbox:')
        ? del({ action: 'delete_message', created: Number(r.key.split(':')[1]), name: r.by }, 'this message')
        : del(r.index === 0 ? { action: 'delete_thread', id: r.thread } : { action: 'delete_post', id: r.thread, index: r.index }, r.index === 0 ? 'the whole thread' : 'this post') }),
    ] })), { id: 'mod-queue-list' }) : AW.h('p', { class: 'setting-desc', text: 'Nothing reported.' }));
  const box = document.getElementById('report-settings');
  if (!box || !rs) return;
  // The reasons offered (any of them), how many reports count, and whether to hide meanwhile.
  const chosen = new Set(rs.reasons);
  const reasons = AW.h('div', { class: 'filter-chips', role: 'group', 'aria-label': 'Reasons offered' }, rs.all_reasons.map((r) =>
    AW.h('button', { type: 'button', class: 'filter-chip' + (chosen.has(r) ? ' active' : ''), 'aria-pressed': String(chosen.has(r)), onclick: async () => {
      const next = rs.all_reasons.filter((x) => (x === r ? !chosen.has(r) : chosen.has(x)));
      if (!next.length) return;
      try { await postJSON('/admin/settings', { report_reasons: next }); loadModeration(); } catch (err) { say(err.message, false, noteEl('mod-note')); }
    } }, r)));
  box.replaceChildren(AW.h('div', { class: 'aw-settings' }, AW.h('div', { class: 'aw-field' }, AW.h('span', { class: 'aw-label', text: 'Reasons offered' }), reasons,
    AW.h('small', { class: 'setting-desc', text: 'saved as you choose' }))),
  AW.settings([
    { key: 'report_threshold', label: 'In the queue after', kind: 'choice', value: rs.threshold, options: [[1, '1 report'], [2, '2'], [3, '3'], [5, '5']] },
    { key: 'report_hide', label: 'Hidden from everyone else until you look', kind: 'toggle', value: rs.hide,
      note: 'off: it stays up while waiting; on: it is hidden once it counts, and shows again if you keep it' },
  ], { save: async (changed) => { const r = await postJSON('/admin/settings', changed); loadModeration(); return r; } }));
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
        ? `${done.message} — the Updates doctor (under Doctors) can say why.` : done.message, !failed);
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
  auditDeep: document.getElementById('audit-deep'),
  auditDeepWhen: document.getElementById('audit-deep-when'),
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
  sec.auditDeep.disabled = busy;
  const deep = data.deep || {};
  sec.auditDeepWhen.textContent = deep.progress
    ? `Deep audit running: ${deep.progress.step} (${deep.progress.n} of ${deep.progress.total})…`
    : (deep.at ? `Last deep audit ${new Date(deep.at * 1000).toLocaleString()}, ${Math.round((deep.took || 0) / 60)} min. ` : 'No deep audit yet. ')
      + 'It runs debian-cis\'s CIS benchmark checks and Lynis (about 8 minutes on a small board), weekly while the Security kit is kept current.';
  renderAudit(data.audit, busy);
  renderImports(data.imports);
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
// --- the joint report (secdoctor.joint): what several sources say about one thing, once ------------
const SOURCE_WORDS = { doctor: 'the doctor', 'security-page': 'the Security page', debsecan: 'debsecan', 'debian-cis': 'debian-cis',
  lynis: 'Lynis', openvas: 'OpenVAS', nmap: 'nmap' };
const srcWords = (list) => list.map((x) => SOURCE_WORDS[x] || x).join(', ');
function renderJoint(j) {
  const box = (id) => document.getElementById(id);
  if (!j) { box('joint-summary').textContent = 'Run the doctor to see it.'; box('joint-items').replaceChildren(); return; }
  const a = j.after || { problem: 0, warn: 0 };
  box('joint-summary').textContent = `After merging what the sources agree on: ${a.problem} to fix, ${a.warn} to look at` +
    (j.agreed_ok ? `; ${j.agreed_ok} thing${j.agreed_ok === 1 ? '' : 's'} several sources agree are fine` : '') + '.';
  const items = (j.items || []);
  box('joint-items').replaceChildren(...items.map((i) => el('div', { className: `git-card joint-item joint-${i.status}` },
    el('h4', { textContent: `${MARK[i.status]} ${i.title}` }),
    el('p', { className: 'badges' }, el('span', { className: 'badge', textContent: i.about.kind }),
      ...i.sources.map((x) => el('span', { className: 'badge badge-mirror', textContent: SOURCE_WORDS[x] || x }))),
    el('p', { className: 'setting-desc', textContent: i.sources.length > 1 ? `${i.sources.length} sources agree.` : `Said by ${srcWords(i.sources)}.` }),
    el('ul', { className: 'joint-titles' }, ...i.titles.slice(0, 4).map((x) => el('li', { textContent: x }))),
    i.fix ? el('p', { className: 'setting-desc', textContent: `To do: ${i.fix}` }) : null)));
  const alone = items.filter((i) => i.alone);
  box('joint-alone').hidden = !alone.length;
  box('joint-alone-list').replaceChildren(...alone.map((i) => el('li', { className: `check check-${i.status}` },
    el('strong', { textContent: i.title }),
    el('span', { textContent: ` — only ${srcWords(i.sources)} said so; ${srcWords(i.could_see)} could have seen it and did not.` }))));
  const fresh = j.freshness || {};
  box('joint-sources').replaceChildren(...Object.keys(j.sources || {}).map((src) => {
    const c = j.sources[src];
    return el('tr', {}, el('td', { textContent: SOURCE_WORDS[src] || src }), el('td', { className: 'setting-desc', textContent: (j.coverage || {})[src] || '' }),
      el('td', { textContent: fresh[src] ? new Date(fresh[src] * 1000).toISOString().slice(0, 10) : '—' }),
      el('td', { textContent: `${c.problem || 0} / ${c.warn || 0}` }));
  }));
}

// Scan reports, read here in the browser: only what they found goes to the box (secimports.py).
function xmlDoc(text) {
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) throw new Error('This is not XML the page can read.');
  return doc;
}
const kid = (node, name) => { const n = node && [...node.children].find((c) => c.tagName === name); return n ? n.textContent.trim() : ''; };
function parseNmapXml(text) {
  const doc = xmlDoc(text);
  if (doc.documentElement.tagName !== 'nmaprun') throw new Error('Not an nmap XML report (nmap -oX).');
  const results = [];
  for (const host of doc.getElementsByTagName('host')) {
    const addr = (host.getElementsByTagName('address')[0] || { getAttribute: () => '' }).getAttribute('addr');
    for (const p of host.getElementsByTagName('port')) {
      const st = p.getElementsByTagName('state')[0];
      const sv = p.getElementsByTagName('service')[0];
      results.push({ port: Number(p.getAttribute('portid')), proto: p.getAttribute('protocol'), host: addr, state: st ? st.getAttribute('state') : '',
        service: sv ? [sv.getAttribute('name'), sv.getAttribute('product'), sv.getAttribute('version')].filter(Boolean).join(' ') : '' });
    }
  }
  const start = Number(doc.documentElement.getAttribute('start')) || null;
  return { kind: 'nmap', ran: start, results: results.filter((r) => r.state === 'open' || r.state === 'open|filtered') };
}
function splitPort(text) {
  const [p, proto] = String(text || '').split('/');
  return { port: /^\d+$/.test(p) ? Number(p) : 'general', proto: (proto || 'tcp').toLowerCase() === 'udp' ? 'udp' : 'tcp' };
}
function parseOpenvasXml(text) {
  const doc = xmlDoc(text);
  const results = [];
  for (const r of doc.getElementsByTagName('result')) {
    const nvt = [...r.children].find((c) => c.tagName === 'nvt');
    if (!nvt) continue;
    const tags = kid(nvt, 'tags');
    const tag = (name) => ((tags.match(new RegExp(`(?:^|\\|)${name}=([^|]*)`)) || [])[1] || '').trim();
    const cvss = parseFloat(kid(r, 'severity') || kid(nvt, 'cvss_base')) || 0;
    if (cvss <= 0) continue;
    const cves = [...nvt.getElementsByTagName('ref')].filter((x) => x.getAttribute('type') === 'cve').map((x) => x.getAttribute('id'))
      .concat((kid(nvt, 'cve').match(/CVE-\d{4}-\d{4,7}/g) || []));
    results.push({ ...splitPort(kid(r, 'port')), host: (r.getElementsByTagName('host')[0] || { firstChild: null }).firstChild?.textContent?.trim() || '',
      cvss, name: kid(nvt, 'name'), summary: tag('summary') || kid(r, 'description').slice(0, 600),
      solution: kid(nvt, 'solution') || tag('solution'), cves: [...new Set(cves)].slice(0, 50) });
  }
  const start = Date.parse((doc.getElementsByTagName('scan_start')[0] || {}).textContent || '');
  return { kind: 'openvas', ran: Number.isFinite(start) ? start / 1000 : null, results: results.slice(0, 1000) };
}
function csvRows(text) {
  const rows = []; let row = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; } else field += c; }
    else if (c === '"') q = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; row.push(field); rows.push(row); row = []; field = ''; }
    else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((x) => x.trim()));
}
function parseOpenvasCsv(text) {
  const [head, ...rows] = csvRows(text);
  const ix = (name) => head.findIndex((h) => h.trim().toLowerCase() === name);
  const col = { ip: ix('ip'), port: ix('port'), proto: ix('port protocol'), cvss: ix('cvss'), name: ix('nvt name'), summary: ix('summary'),
    solution: ix('solution'), cves: ix('cves'), time: ix('timestamp') };
  if (col.cvss < 0 || col.name < 0) throw new Error('Not an OpenVAS CSV report (it has no CVSS and NVT Name columns).');
  const results = rows.map((r) => ({ port: /^\d+$/.test(r[col.port] || '') ? Number(r[col.port]) : 'general',
    proto: (r[col.proto] || 'tcp').toLowerCase() === 'udp' ? 'udp' : 'tcp', host: r[col.ip] || '', cvss: parseFloat(r[col.cvss]) || 0,
    name: r[col.name] || '', summary: r[col.summary] || '', solution: col.solution >= 0 ? r[col.solution] : '',
    cves: ((col.cves >= 0 ? r[col.cves] : '') .match(/CVE-\d{4}-\d{4,7}/g) || []).slice(0, 50) })).filter((r) => r.cvss > 0);
  const t = col.time >= 0 && rows[0] ? Date.parse(rows[0][col.time]) : NaN;
  return { kind: 'openvas', ran: Number.isFinite(t) ? t / 1000 : null, results: results.slice(0, 1000) };
}
function parseScanReport(text, name) {
  const t = text.replace(/^\uFEFF/, '').trimStart();
  const report = !t.startsWith('<') ? parseOpenvasCsv(t) : /<nmaprun[\s>]/.test(t.slice(0, 4000)) ? parseNmapXml(t) : parseOpenvasXml(t);
  return { ...report, name: name || '' };
}

function renderAudit(audit, busy) {
  badge('secdoctor', audit ? String((audit.joint && audit.joint.after ? audit.joint.after.problem : audit.counts.problem) || '') : '');
  renderJoint(audit && audit.joint);
  if (!audit) {
    sec.auditWhen.textContent = busy ? 'Running…' : 'Not run yet.';
    sec.auditSteps.replaceChildren();
    sec.auditScope.hidden = true;
    return;
  }
  const c = audit.counts;
  sec.auditWhen.textContent = `Last run ${new Date(audit.at * 1000).toLocaleString()}: ${c.problem} to fix, ${c.warn} to look at, ${c.ok} fine`
    + ((audit.new || []).length ? `; ${audit.new.length} new since ${new Date(audit.previous_at * 1000).toLocaleDateString()}` : '')
    + (audit.root ? '.' : ' (not run as root: some checks could not read what they need).') + (busy ? ' Running…' : '');
  sec.auditSteps.replaceChildren(...audit.steps.map((st) => {
    const worst = st.findings.some((f) => f.status === 'problem') ? 'problem' : st.findings.some((f) => f.status === 'warn') ? 'warn' : 'ok';
    const lines = [...st.findings].sort((a, b) => RANK[a.status] - RANK[b.status]);
    const det = el('details', { className: 'admin-output' },
      el('summary', { textContent: `${MARK[worst]} ${st.title}${st.ref ? ` (${st.ref})` : ''}` }),
      el('ul', { className: 'admin-checks' }, ...lines.map((f) => el('li', { className: `check check-${f.status}` },
        el('span', { textContent: `${MARK[f.status]} ` }), el('strong', { textContent: f.title }),
        (audit.new || []).includes(f.id) ? el('span', { className: 'badge-push bad', textContent: 'new' }) : null,
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
sec.auditDeep.addEventListener('click', () => secRequest({ action: 'deep' }, 'audit'));
const importNote = document.getElementById('import-note');
document.getElementById('import-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = e.target.elements.file.files[0];
  if (!file) return;
  try {
    if (file.size > 64 * 2 ** 20) throw new Error('That file is over 64 MB: export fewer results.');
    const text = file.text ? await file.text() : await new Promise((ok, no) => {
      const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => no(r.error); r.readAsText(file);
    });
    const report = parseScanReport(text, file.name);
    const r = await postJSON('/admin/security', { action: 'import', report });
    say(`${report.kind === 'nmap' ? 'nmap' : 'OpenVAS'}: ${report.results.length} results read. ${r.message || ''}`, true, importNote);
    secWaiting = { id: r.id, fid: 'audit' };
    loadSecurity();
  } catch (err) { say(err.message, false, importNote); }
});
function renderImports(imports) {
  document.getElementById('import-list').replaceChildren(...Object.entries(imports || {}).map(([kind, i]) => el('li', { className: 'admin-item' },
    el('span', {}, el('strong', { textContent: kind === 'nmap' ? 'nmap' : 'OpenVAS' }),
      el('span', { className: 'setting-desc', textContent: ` ${i.name || ''}: ${i.results} results; ran ${i.ran ? new Date(i.ran * 1000).toISOString().slice(0, 10) : '?'}, imported ${new Date(i.imported * 1000).toISOString().slice(0, 10)}` })),
    actionButton('Remove', async () => {
      try { const r = await postJSON('/admin/security', { action: 'import-remove', kind }); secWaiting = { id: r.id, fid: 'audit' }; loadSecurity(); }
      catch (err) { say(err.message, false, importNote); }
    }, { className: 'small' }))));
}
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
  // The clock and its module have their own pane (System, Clock); the rest is the services doctor.
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
  const here = paneShown('health') || paneShown('clock');
  if (stale && !busy && !hlAsked && !h.stuck && here) { hlAsked = true; hlRequest({ action: 'scan' }, paneShown('clock') ? 'clock-scan' : 'scan'); return; }
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
window.addEventListener('hashchange', () => { if (paneShown('health') || paneShown('clock')) loadHealth(); });
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
  devices: document.getElementById('net-devices'),
  hazards: document.getElementById('net-hazards'),
  status: document.getElementById('up-status'),
  eager: document.getElementById('up-eager'),
  forgive: document.getElementById('up-forgive'),
  will: document.getElementById('up-will'),
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
// The levels chosen on the page (both as radio cards), before Save.
const upPick = { eagerness: 'patient', forgiveness: 'normal' };
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
    input.addEventListener('input', () => { upDirty = true; if (netData) showUpPreset(netData.levels); showUpSave(); });
    net.fields.append(el('label', {}, el('span', { textContent: label }), input));
  }
}

// --- the levels in words ---------------------------------------------------------------
const cap1 = (t) => t[0].toUpperCase() + t.slice(1);
const dur = (sec) => (sec < 60 ? `${sec} s` : sec % 3600 === 0 ? `${sec / 3600} h` : `${Math.round(sec / 60)} min`);
const FLAP_DOES = { note: 'is only noted', pin: 'is locked to the strongest access point', repair: 'is repaired like any outage' };

// What a set of numbers does once the link counts as down: an eagerness level's own preset (its
// line on the ladder), or the effective numbers with any custom values ("What this will do").
function stepsInWords(p) {
  const st = p.steps || {};
  if (!Object.keys(st).length) return 'does nothing but note it';
  const parts = [];
  if ('reconnect' in st) {
    let r = st.reconnect ? `reconnects after ${dur(st.reconnect)}` : 'reconnects at once';
    if (p.repeat) r += `, then again every ${dur(p.repeat)}` + (p.backoff > 1 && p.max_repeat ? ` (the gap growing ×${p.backoff}, up to ${dur(p.max_repeat)})` : '');
    parts.push(r);
  }
  if ('restart' in st) parts.push(`restarts the network service after ${dur(st.restart)}`);
  if ('radio' in st) parts.push(`resets the radio after ${dur(st.radio)}`);
  parts.push('reboot' in st ? `reboots after ${dur(st.reboot)}${p.reboots_per_day ? ` (at most ${p.reboots_per_day} a day)` : ''}` : 'never reboots');
  const heavy = 'radio' in st || 'reboot' in st;
  return parts.join(', ') + (heavy ? (p.guests === 'ignore' ? ', with guests on or not' : ', but not while guests are on the hotspot') : '');
}

function rungLine(name, levels) {
  const p = { ...levels.presets.common, ...levels.presets.eagerness[name] };
  return name === 'off' ? 'Checks, and never acts.' : `When it's down: ${stepsInWords(p)}.`;
}

function forgiveLine(f) {
  return `Down after ${f.misses} failed check${f.misses === 1 ? '' : 's'} and ${dur(f.grace)} more. `
    + `${f.flap_count} drops within ${dur(f.flap_window)} count as flapping, which ${FLAP_DOES[f.flap_action] || f.flap_action}.`;
}

function willText(eff) {
  return `What this will do: it checks the link every ${dur(eff.check)}. It counts it as down after ${eff.misses} failed `
    + `check${eff.misses === 1 ? '' : 's'} and ${dur(eff.grace)} more, and then ${stepsInWords(eff)}. `
    + `A link that drops ${eff.flap_count} times within ${dur(eff.flap_window)} ${FLAP_DOES[eff.flap_action] || eff.flap_action}.`;
}

// Both levels the same way (Tom, 2026-10-06): radio cards, each level's description and what it
// does shown at once.
function rungs(box, group, names, line, levels) {
  box.replaceChildren(...names.map((name) => {
    const input = el('input', { type: 'radio', name: `up-${group}`, value: name });
    input.addEventListener('change', () => upChoose({ [group]: name }, levels));
    return el('label', { className: 'up-rung' }, input,
      el('span', { className: 'up-rung-text' },
        el('span', { className: 'setting-name', textContent: cap1(name) }),
        el('span', { className: 'setting-desc', textContent: levels.describe[name] || '' }),
        el('span', { className: 'setting-desc up-does', textContent: line(name) })));
  }));
}

function buildUpChoices(levels) {
  if (net.eager.childElementCount) return;
  rungs(net.eager, 'eagerness', levels.eagerness, (name) => rungLine(name, levels), levels);
  rungs(net.forgive, 'forgiveness', levels.forgiveness,
    (name) => forgiveLine({ ...levels.presets.common, ...levels.presets.forgiveness[name] }), levels);
}

function upChoose(change, levels) {
  Object.assign(upPick, change);
  upDirty = true;
  showUpPreset(levels);
  showUpSave();
}

function showUpSave() {
  const busy = !!netWaiting || (netData && netData.pending > 0);
  net.save.disabled = busy || !upDirty;
  net.save.textContent = upDirty ? 'Save (not saved yet)' : 'Save';
}

function fillUpForm(chosen, levels) {
  buildUpChoices(levels);
  upPick.eagerness = chosen.eagerness;
  upPick.forgiveness = chosen.forgiveness;
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
  const e = upPick.eagerness, f = upPick.forgiveness;
  for (const [box, chosen] of [[net.eager, e], [net.forgive, f]]) {
    for (const input of box.querySelectorAll('input')) {
      input.checked = input.value === chosen;
      input.closest('.up-rung').classList.toggle('chosen', input.checked);
    }
  }
  const eff = upPreset(e, f, levels);
  try {
    const over = readUpForm().overrides;
    const shown = { ...eff, ...over, steps: { ...eff.steps } };
    for (const [k, v] of Object.entries(over.steps || {})) { if (v === null) delete shown.steps[k]; else shown.steps[k] = v; }
    net.will.textContent = willText(shown);
  } catch (_) {
    net.will.textContent = willText(eff);
  }
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
  return { eagerness: upPick.eagerness, forgiveness: upPick.forgiveness, iface: net.iface.value, overrides };
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

// --- the devices, in plain words -------------------------------------------------------
const MANAGED_BY = {
  networkmanager: 'NetworkManager, the system\'s own network settings. irate-box asks it, and doesn\'t take over.',
  wpa_supplicant: 'wpa_supplicant, which joins WiFi networks (with something else giving the address).',
  ifupdown: 'ifupdown, from /etc/network/interfaces.',
  networkd: 'systemd-networkd.',
  dhcpcd: 'dhcpcd.',
  iwd: 'iwd. irate-box can watch it, not repair it yet.',
  connman: 'connman. irate-box can watch it, not repair it yet.',
  none: 'Nothing irate-box recognises.',
};
const BUS_WORDS = { usb: 'USB', sdio: 'built-in (SDIO)', pci: 'built-in (PCI)', platform: 'built-in' };
const COND_MARK = { how: '•', limit: '◦', untested: '?', needs: '!' };
const COND_WORD = { how: '', limit: 'Limit: ', untested: 'Untested: ', needs: 'Needs: ' };

function signalWords(dbm) {
  if (typeof dbm !== 'number') return '';
  return dbm >= -50 ? 'excellent' : dbm >= -60 ? 'good' : dbm >= -70 ? 'fair' : dbm >= -80 ? 'weak' : 'poor';
}

// A link's uptime (step 34): a folded part on its card, the last 72 hours by hour and 72 days
// by day (githubstatus.com's format, snag 5), from the watchdog's five-minute record
// (linkhistory.py), drawn by heatmap.js.
function uptimeSection(iface) {
  const u = netData && netData.uptime && netData.uptime[iface];
  const fold = el('details', { className: 'net-uptime' });
  const sm = u && u.summary;
  const time = (t) => new Date(t * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
  const said = !sm || sm.up == null ? 'Not recorded yet.'
    : `Up ${Heatmap.percent(sm.up)} over the last 72 hours (${sm.hours_seen} hours recorded)`
      + (sm.drops ? `; ${sm.drops} drop${sm.drops === 1 ? '' : 's'}` : '; no drops')
      + (sm.longest ? `; longest outage ${sm.longest.minutes} min, ${time(sm.longest.at)}.` : '.');
  fold.append(el('summary', {}, el('span', { className: 'net-label', textContent: 'Uptime' }), el('span', { textContent: ` ${said}` })));
  if (!u) {
    fold.append(el('p', { className: 'setting-desc', textContent: 'The watchdog (irate-box-uplink) records each link in five-minute slots, '
      + 'once the box\'s clock is known to be right (network time, or set on Clock), and keeps 72 days.' }));
    return fold;
  }
  fold.append(el('p', { className: 'setting-desc', textContent: 'The last 72 hours, by hour:' }),
    Heatmap.grid({ caption: `${iface}, the last 72 hours by hour`, cols: u.hour_cols,
      rows: [{ label: iface, cells: u.hours, where: (i) => u.hour_full[i] }] }));
  const day = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
  fold.append(el('p', { className: 'setting-desc', textContent: 'The last 72 days, by day:' }),
    Heatmap.grid({ caption: `${iface}, the last 72 days by day`, cols: Array.from({ length: 72 }, (_, i) => (i % 12 ? '' : day(u.month[i].date))),
      rows: [{ label: iface, cells: u.month.map((c) => (c.n ? c : null)), where: (i) => day(u.month[i].date) }] }),
    Heatmap.legend());
  fold.append(el('p', { className: 'setting-desc', textContent: u.kind === 'uplink'
    ? 'Up means the link was up and the gateway answered. A drop is the link going down after being up; switched off by you is not counted.'
    : 'Only whether the link was up (a cable in, or joined to a network): the gateway is checked on the box\'s link to your network.' }));
  return fold;
}

function deviceCard(inv, d, wifi, hazards) {
  const line = (label, text) => el('p', { className: 'net-line' }, el('span', { className: 'net-label', textContent: label }), el('span', { textContent: text }));
  // Each labelled part a section of its own, a hairline between them, so the parts don't run together.
  const section = (...parts) => el('div', { className: 'net-section' }, ...parts);
  const kind = wifi ? 'WiFi' : 'Wired';
  const usb = d.usb || {};
  const what = [wifi ? `${BUS_WORDS[d.bus] || d.bus || ''} WiFi adapter`.trim() : `${BUS_WORDS[d.bus] || ''} Ethernet port`.trim(),
    d.driver ? `driver ${d.driver}` : null, usb.product || null, usb.id ? `USB id ${usb.id}` : null].filter(Boolean).join(', ');
  let now;
  if (wifi) {
    const l = d.link || {};
    if (l.ssid) {
      const band = l.freq ? (l.freq < 3000 ? '2.4 GHz' : l.freq < 5925 ? '5 GHz' : '6 GHz') : '';
      now = `Connected to ${l.ssid}, channel ${l.channel}${band ? ` (${band})` : ''}, signal ${l.signal} dBm (${signalWords(l.signal)}).`;
    } else if (d.type === 'AP') now = 'Running a hotspot.';
    else now = 'Not connected to a network.';
  } else {
    now = d.carrier ? 'Cable in.' : 'No cable.';
  }
  if (inv.uplink && inv.uplink.iface === d.iface) now += ' This is the box\'s link to your network.';
  const owner = wifi ? d.owner : (inv.uplink && inv.uplink.iface === d.iface ? inv.uplink.backend : null);
  const managed = owner ? (MANAGED_BY[owner] || owner) + (d.manager ? ` With ${d.manager} for the address.` : '') : '—';
  const kids = [
    el('h4', {}, el('span', { textContent: d.iface }), el('span', { className: 'state', textContent: kind })),
    section(line('What it is', what || '—')),
    section(line('Now', now)),
    section(line('Managed by', managed)),
  ];
  if (wifi) {
    const a = (inv.ap || []).find((x) => x.phy === d.phy);
    if (a) {
      const conds = a.conditions || [{ kind: 'how', text: a.detail }];
      const any = conds.some((c) => c.kind !== 'how');
      const verdict = !a.possible ? 'No.' : any ? `Yes, with conditions (through ${a.backend}).` : `Yes (through ${a.backend}).`;
      kids.push(section(line('Can it run the hotspot?', verdict),
        el('ul', { className: 'net-conditions' }, ...conds.map((c) => el('li', { className: `cond-${c.kind}` },
          el('span', { className: 'cond-mark', textContent: COND_MARK[c.kind] || '•', ariaHidden: 'true' }),
          el('span', { textContent: `${COND_WORD[c.kind] || ''}${c.text}` }))))));
    }
  }
  if (!(wifi && d.type === 'AP')) kids.push(section(uptimeSection(d.iface)));
  const mine = hazards.filter((h) => h.iface === d.iface);
  if (mine.length) kids.push(section(el('ul', { className: 'admin-checks' }, ...mine.map((h) => checkItem(h.status, h.title, h.detail, h.fix)))));
  return el('div', { className: 'net-device setting' }, ...kids);
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
  // A card per device, in plain words: what it is, what it's doing, what manages it, whether it
  // can run the hotspot and on what conditions, and the warnings that are about it.
  const shownIfaces = new Set(inv ? [...inv.radios.map((r) => r.iface), ...inv.wired.map((w) => w.iface)] : []);
  const hz = inv ? [...inv.hazards].sort((x, y) => RANK[x.status] - RANK[y.status]) : [];
  net.devices.replaceChildren(...(inv ? [...inv.radios.map((r) => deviceCard(inv, r, true, hz)), ...inv.wired.map((w) => deviceCard(inv, w, false, hz))] : []));
  net.hazards.replaceChildren(...hz.filter((h) => !(h.iface && shownIfaces.has(h.iface))).map((h) => checkItem(h.status, h.title, h.detail, h.fix)));
  const sn = netNotes.scan;
  say(sn ? sn.text : '', sn ? sn.ok : true, net.scanNote);

  // Staying on the network
  buildUpFields(levels);
  if (!upDirty) fillUpForm((u && u.chosen) || { eagerness: 'patient', forgiveness: 'normal', iface: 'auto', overrides: {} }, levels);
  net.status.textContent = upStatusText(u);
  showUpSave();
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
  // One line per event: when, then what (Tom, 2026-10-06).
  net.events.replaceChildren(...evs.slice(-25).reverse().map((e) => el('li', {},
    el('time', { className: 'ev-time', dateTime: new Date(e.at * 1000).toISOString(),
      textContent: new Date(e.at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }),
    el('span', { className: 'ev-text', textContent: e.text }))));
  const down = u && !u.stale && u.state !== 'up';
  badge('network', down ? '!' : '');

  const stale = !inv || Date.now() / 1000 - inv.at > 60 * 60;
  if (stale && !busy && !netAsked && paneShown('network')) { netAsked = true; netRequest({ action: 'scan' }, 'scan'); return; }
  clearTimeout(netPoll);
  netPoll = setTimeout(loadNetwork, busy ? 2000 : paneShown('network') ? 10000 : 60000);
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
for (const box of [net.iface]) {
  box.addEventListener('change', () => { upDirty = true; if (netData) showUpPreset(netData.levels); showUpSave(); });
}
net.save.addEventListener('click', () => {
  let settings;
  try { settings = readUpForm(); } catch (err) { netNotes.up = { text: err.message, ok: false }; renderNetwork(netData); return; }
  if (settings.eagerness === 'stubborn' && !confirm('Stubborn may reset the radio and reboot the box while guests are on it. Use it?')) return;
  netRequest({ action: 'settings', settings }, 'up');
});
net.hold.addEventListener('click', () => netRequest({ action: 'hold', minutes: 60 }, 'up'));
net.unhold.addEventListener('click', () => netRequest({ action: 'hold', minutes: 0 }, 'up'));
window.addEventListener('hashchange', () => { if (paneShown('network')) loadNetwork(); });

// --- the hotspot itself (item 2: root/ap.py, the plan from hub/apmode.py) -----------------------------
const apEl = { state: document.getElementById('ap-state'), form: document.getElementById('ap-form'), radio: document.getElementById('ap-radio'),
  channel: document.getElementById('ap-channel'), take: document.getElementById('ap-take'), takeLabel: document.getElementById('ap-take-label'),
  sw: document.getElementById('ap-switch'), tryB: document.getElementById('ap-try'), confirmB: document.getElementById('ap-confirm'), note: noteEl('ap-note') };
let apRun = null;
let apWait = null; // {id, until}: a request to the root helper, and when to stop waiting for it
function apFill(sel, values, keep) {
  const want = keep !== undefined ? keep : sel.value;
  sel.replaceChildren(el('option', { value: '', textContent: 'Automatic' }), ...values.map((v) => el('option', { value: String(v), textContent: String(v) })));
  sel.value = values.map(String).includes(String(want)) ? String(want) : '';
}
function renderAp(run) {
  apRun = run;
  const p = run.up ? run.plan : run.preview;
  const where = (q) => (q && q.channel ? ` on ${q.iface === 'ap0' || q.kind === 'own-channel' || q.kind === 'follow' ? 'a second interface beside ' + q.iface : q.iface}, channel ${q.channel}` : '');
  apEl.state.textContent = run.up
    ? `On${where(p)}. ${p.text}${run.confirmed === false ? ' Your WiFi link is off: press Keep it from the hotspot, or it comes back by itself.' : ''}`
    : p ? `Off. Switched on, it would run${where(p)}. ${p.text}${p.to_try ? ' Whether it can keep a channel of its own here is still to be tried.' : ''}${p.why ? ` (${p.why})` : ''}`
      : 'Off. The radios have not been looked at yet: Look again, above.';
  if (run.note) say(run.note, true, apEl.note);
  const radio = apEl.radio.value;
  apFill(apEl.radio, (run.radios || []).map((r) => r.iface), (run.owner || {}).radio || radio);
  const chans = ((run.radios || []).find((r) => r.iface === (apEl.radio.value || (p && p.iface))) || (run.radios || [])[0] || { channels: [] }).channels;
  apFill(apEl.channel, chans, (run.owner || {}).channel);
  apEl.sw.textContent = run.up ? 'Switch off' : 'Switch on';
  apEl.takeLabel.hidden = run.up || !(p && p.needs_choice);
  apEl.tryB.hidden = run.up || !(p && p.to_try);
  apEl.confirmB.hidden = !(run.up && run.confirmed === false);
  [apEl.sw, apEl.tryB, apEl.confirmB].forEach((b) => { b.disabled = !!apWait; });
}
async function loadAp() {
  try {
    const d = await getJSON('/admin/hotspot');
    renderAp(d.running || {});
    if (apWait) {
      const done = ((d.running || {}).at || 0) * 1000 > apWait.since;
      if (done || Date.now() > apWait.until) { apWait = null; renderAp(d.running || {}); } else setTimeout(loadAp, 2000);
    }
  } catch (_) { apEl.state.textContent = 'Could not read the hotspot.'; }
}
async function apAct(body) {
  try {
    await postJSON('/admin/hotspot', body);
    apWait = { since: Date.now() - 1000, until: Date.now() + 90000 };
    say('Asked: the box is doing it…', true, apEl.note);
    renderAp(apRun || {});
    setTimeout(loadAp, 2000);
  } catch (err) { say(err.message, false, apEl.note); }
}
apEl.form.addEventListener('submit', (e) => {
  e.preventDefault();
  if (apRun && apRun.up) { apAct({ action: 'off' }); return; }
  const body = { action: 'on' };
  if (apEl.radio.value) body.radio = apEl.radio.value;
  if (apEl.channel.value) body.channel = Number(apEl.channel.value);
  if (!apEl.takeLabel.hidden) {
    if (!apEl.take.checked) { say('This radio can only do one thing at a time: tick the box to use it for the hotspot, or leave the hotspot off.', false, apEl.note); return; }
    if (!confirm('The box will leave your WiFi while the hotspot runs. Open the hub from the hotspot within 5 minutes and press Keep it, or your WiFi comes back by itself.')) return;
    body.take_radio = true;
  }
  apAct(body);
});
apEl.radio.addEventListener('change', () => renderAp(apRun || {}));
apEl.tryB.addEventListener('click', () => apAct({ action: 'try' }));
apEl.confirmB.addEventListener('click', () => apAct({ action: 'confirm' }));
window.addEventListener('hashchange', () => { if (paneShown('network')) loadAp(); });
if (paneShown('network')) loadAp();
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
  // Bytes past the last whole multiple of the alphabet are dropped, so every letter is as likely.
  const chars = [];
  const limit = 256 - (256 % abc.length);
  while (chars.length < 12) {
    for (const v of crypto.getRandomValues(new Uint8Array(16))) {
      if (v < limit && chars.length < 12) chars.push(abc[v % abc.length]);
    }
  }
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
window.addEventListener('hashchange', () => { if (paneShown('backup')) loadKit(); });
loadKit();

// --- who can open each app --------------------------------------------------------------
// Public, private or off (access.py): a three-way switch on each app (Apps), each add-on
// (Add-ons) and the hub's own parts (Apps, Built into the hub). The root helper rewrites the
// web server's part and answers; each switch keeps its node, so a list that re-renders moves
// it rather than losing what it shows.
const ACCESS_LABEL = { public: '🌐 Public', users: '👥 Users', private: '🔒 Private', off: '⭘ Off' };
const accessNodes = new Map(); // id -> { node, app }
let accessWaiting = null; // { id, app }
let accessNote = null; // { app, text, ok }
let accessPoll = null;

const SEEN_LABEL = { auto: 'as its access', guests: 'everyone', users: 'those logged in', hidden: 'nobody' };
function seenDesc(a) {
  a = { ...a, visible: a.visible || 'auto' };
  if (a.mode === 'off') return 'Off: no tile.';
  if (a.visible === 'auto') return '';
  const opens = a.mode === 'public' ? 'everyone' : a.mode === 'users' ? 'those logged in' : 'the admin';
  if (a.visible === 'hidden') return 'No tile; its address still works for whoever may open it.';
  return a.visible === 'guests' && a.mode !== 'public'
    ? `Everyone sees its tile, with a lock: it opens for ${opens}, and anyone else is asked to sign in.`
    : `Its tile shows to ${SEEN_LABEL[a.visible]}; it opens for ${opens}.`;
}
function visibilitySet(a, v) {
  if (v === a.visible) return;
  postJSON('/admin/visibility', { app: a.id, visible: v }).then(() => loadAccess(),
    (err) => { accessNote = { app: a.id, text: err.message, ok: false }; loadAccess(); });
}

function accessDesc(a) {
  if (a.mode === 'public') return a.login ? 'Public: on the home page; it still asks for the admin login.' : 'Public: on the home page, open to everyone on the network.';
  if (a.mode === 'users') return 'Users: on the home page for anyone logged in to an account (Accounts); anyone else is asked to log in.';
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
  lastAccess = data;
  for (const a of data.apps) {
    accessSlot(a.id).replaceChildren(...accessControls(a));
    if (a.kind === 'builtin') builtin.push(a);
  }
  fillAccessBlocks();
  document.getElementById('builtin-list').replaceChildren(...builtin.map((a) => el('div', { className: 'setting library-source' },
    el('span', {}, el('span', { className: 'setting-name', textContent: a.title }), accessSlot(a.id)))));
  clearTimeout(accessPoll);
  if (accessWaiting) accessPoll = setTimeout(loadAccess, 1000);
}

// Each app's own page starts with its access (M6): the same controls, drawn again there.
let lastAccess = null;
const TAB_SEEN = { guests: 'everyone', users: 'those logged in', hidden: 'nobody' };
function fillSeenBlocks() {
  if (!lastAccess) return;
  document.querySelectorAll('.seen-block[data-app]').forEach((block) => {
    const app = block.dataset.app, cur = ((lastAccess.pages || {})[app] || 'auto').replace('auto', 'guests');
    block.replaceChildren(el('span', { className: 'chip-group access-seen' }, el('span', { className: 'setting-desc', textContent: 'Its tab on the hub page, shown to:' }),
      ...Object.entries(TAB_SEEN).map(([v, label]) => {
        const b = el('button', { type: 'button', className: 'chip' + (cur === v ? ' selected' : ''), textContent: label,
          onclick: () => { if (v !== cur) postJSON('/admin/visibility', { app, visible: v }).then(loadAccess, (err) => say(err.message, false, block)); } });
        b.setAttribute('aria-pressed', String(cur === v));
        return b;
      })));
  });
}
function fillAccessBlocks() {
  if (!lastAccess) return;
  fillSeenBlocks();
  document.querySelectorAll('.access-block[data-app]').forEach((block) => {
    const a = lastAccess.apps.find((x) => x.id === block.dataset.app);
    const seen = (lastAccess.seen || {})[block.dataset.app];
    block.replaceChildren(...(a ? accessControls(a) : seen ? seenControls(block.dataset.app, seen)
      : [el('p', { className: 'setting-desc', textContent: 'Always on the hub: no switch.' })]));
  });
  drawTileOrder();
}
AL.onBuild(fillAccessBlocks);

// A tile with no switch of its own (a folder, About; F3): anyone may open it, so only who sees
// its tile is to choose. Hidden, its address still works.
const SEEN_ONLY = { auto: 'everyone', users: 'those logged in', hidden: 'nobody' };
function seenControls(app, cur) {
  const seen = el('span', { className: 'chip-group access-seen' }, el('span', { className: 'setting-desc', textContent: 'Tile shown to:' }),
    ...Object.entries(SEEN_ONLY).map(([v, label]) => {
      const b = el('button', { type: 'button', className: 'chip' + (cur === v ? ' selected' : ''), textContent: label,
        onclick: () => visibilitySet({ id: app, visible: cur }, v) });
      b.setAttribute('aria-pressed', String(cur === v));
      return b;
    }));
  seen.setAttribute('role', 'group');
  seen.setAttribute('aria-label', 'Who sees its tile');
  return [seen, el('span', { className: 'setting-desc', textContent: cur === 'hidden' ? 'No tile; its address still works.'
    : 'Open to anyone who reaches it: it has no switch of its own.' })];
}

// The box-wide sign-in offer (F3; the setup decision "sign-in-offer"): an app for users, left at
// "as its access", shows its tile to guests too, locked, leading to sign-in.
async function loadSignInOffer() {
  const box = document.getElementById('sign-in-offer-box');
  if (!box) return;
  let st;
  try { st = await getJSON('/admin/settings'); } catch (e) { return; }
  box.replaceChildren(AW.settings([{ key: 'sign_in_offer', label: 'Show guests the tiles of apps for users, locked, with sign-in', kind: 'toggle',
    value: !!st.sign_in_offer, decision: 'sign-in-offer',
    note: 'Off: an app for users shows its tile only to those signed in. On: guests see it too, with a lock that leads to sign-in. An app\'s own "Tile shown to" still has the last word.' }],
  { save: async (changed) => { await postJSON('/admin/settings', changed); loadAccess(); } }));
}
loadSignInOffer();

// The apps row's order (F3), on "All apps": each tile with who opens it and who sees it; moved
// with ↑/↓, held until Save, kept by the hub (/admin/tiles).
let tilesData = null;
let tilesDraft = null;
async function loadTileOrder() {
  try { tilesData = await getJSON('/admin/tiles'); } catch (e) { return; }
  tilesDraft = { order: tilesData.tiles.map((x) => x.id) };
  drawTileOrder();
}
const OPENS = { public: 'everyone', users: 'users', private: 'the admin', off: 'off' };
function tileWords(id) {
  const a = lastAccess && lastAccess.apps.find((x) => x.id === id);
  if (a) {
    const seen = a.mode === 'off' ? 'nobody' : a.visible === 'auto' || !a.visible ? 'as its access' : SEEN_LABEL[a.visible];
    return { summary: `opens: ${OPENS[a.mode] || a.mode} · seen: ${seen}`, badges: [a.mode === 'off' ? 'off' : a.mode] };
  }
  const s = lastAccess && (lastAccess.seen || {})[id];
  return { summary: `opens: everyone · seen: ${SEEN_ONLY[s || 'auto']}`, badges: [s === 'hidden' ? 'hidden' : 'shown'] };
}
function drawTileOrder(openId) {
  const box = document.getElementById('tile-order-box');
  if (!box || !tilesData || !tilesDraft) return;
  const byId = new Map(tilesData.tiles.map((x) => [x.id, x]));
  const order = tilesDraft.order.filter((i) => byId.has(i));
  const move = (id, d) => { const i = order.indexOf(id), k = i + d; if (k < 0 || k >= order.length) return;
    [order[i], order[k]] = [order[k], order[i]]; tilesDraft.order = order; drawTileOrder(id); };
  const saved = tilesData.tiles.map((x) => x.id);
  const dirty = order.join() !== saved.join();
  const items = order.map((id) => { const t = byId.get(id), w = tileWords(id);
    return { id, title: `${t.icon} ${t.name}`.trim(), summary: w.summary, badges: w.badges, detail: () => [
      AW.h('div', { class: 'aw-field' }, AW.h('span', { class: 'aw-label', text: 'Place' }),
        AW.btn('↑ Earlier', { onclick: () => move(id, -1) }), AW.btn('↓ Later', { onclick: () => move(id, 1) })),
      document.getElementById('page-app-' + id) ? AW.h('a', { href: '#page-app-' + id, class: 'action-btn' }, 'Its page →') : null,
    ].filter(Boolean) }; });
  const note = AW.h('span', { class: 'note', text: dirty ? 'Changes not saved yet.' : 'The order waits for Save.' });
  box.replaceChildren(AW.shortList(items, { id: 'tile-order-list' }), AW.h('div', { class: 'aw-foot' }, note,
    AW.btn('Discard', { disabled: !dirty, onclick: () => { tilesDraft = { order: saved.slice() }; drawTileOrder(); } }),
    AW.btn('Save', { class: 'action-btn primary', disabled: !dirty, onclick: async () => {
      try { tilesData = await postJSON('/admin/tiles', { state: { order } }); tilesDraft = { order: tilesData.tiles.map((x) => x.id) }; drawTileOrder(); }
      catch (err) { note.textContent = err.message; }
    } })));
  const open = openId && [...box.querySelectorAll('.aw-row')].find((r) => r.dataset.awId === openId);
  if (open) AW.foldRow(open.querySelector('.aw-row-head'), true);
}
loadTileOrder();

function accessControls(a) {
  a = { ...a, visible: a.visible || 'auto' };  // a hub from before M5 says nothing: as its access
  const group = el('span', { className: 'access-toggle' },
    ...['public', 'users', 'private', 'off'].filter((mode) => mode !== 'users' || a.users).map((mode) => {
      const b = el('button', { type: 'button', textContent: ACCESS_LABEL[mode], disabled: !!accessWaiting, onclick: () => accessSet(a, mode) });
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(a.mode === mode));
      return b;
    }));
  group.setAttribute('role', 'radiogroup');
  group.setAttribute('aria-label', `Who can open ${a.title}`);
  const note = accessNote && accessNote.app === a.id
    ? el('span', { className: `setting-desc action-note${accessNote.ok ? '' : ' bad'}`, role: 'status', textContent: accessNote.text }) : null;
  // Who sees its tile, apart from who opens it (M5, checklist 4a): the hub's own, saved at once.
  const seen = el('span', { className: 'chip-group access-seen' }, el('span', { className: 'setting-desc', textContent: 'Tile shown to:' }),
    ...Object.entries(SEEN_LABEL).map(([v, label]) => {
      const b = el('button', { type: 'button', className: 'chip' + (a.visible === v ? ' selected' : ''), textContent: label,
        disabled: a.mode === 'off', onclick: () => visibilitySet(a, v) });
      b.setAttribute('aria-pressed', String(a.visible === v));
      return b;
    }));
  seen.setAttribute('role', 'group');
  seen.setAttribute('aria-label', `Who sees ${a.title}'s tile`);
  // No note: left out, not passed as null (replaceChildren would show the word "null").
  return [group, el('span', { className: 'setting-desc', textContent: accessWaiting && accessWaiting.app === a.id ? 'Changing…' : accessDesc(a) }),
    seen, el('span', { className: 'setting-desc', textContent: seenDesc(a) }), note].filter(Boolean);
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

// --- web add-ons (local: plans/no-root-addons-plan) ---------------------------------------
// Added, kept current and removed by the hub itself, no installer; switched like any app
// (accessSlot). Adding one asks its consent text first; a pasted one needs the warning read.
const loc = {
  added: document.getElementById('local-added'),
  cat: document.getElementById('local-catalogue'),
  errors: document.getElementById('local-errors'),
  port: document.getElementById('local-port'),
  paste: document.getElementById('local-paste-text'),
  pasteOk: document.getElementById('local-paste-ok'),
  pasteGo: document.getElementById('local-paste-go'),
  pasteNote: document.getElementById('local-paste-note'),
};
let locNote = null; // { id, text, ok }
let locPoll = null;
const LOC_UNDERSTOOD = "I understand this runs someone else's code on this box's address";

function capsText(c) {
  const connect = (c && c.connect) || [];
  return (connect.length ? `Connects to: ${connect.join(', ')} ({box} is this hub).` : 'Connects to nothing but its own address.')
    + (c && c.storage ? " Keeps data in the visitor's browser." : '');
}

function renderLocal(data) {
  loc.port.textContent = data.addon_port;
  const note = (id) => (locNote && locNote.id === id
    ? el('span', { className: `setting-desc action-note${locNote.ok ? '' : ' bad'}`, role: 'status', textContent: locNote.text }) : null);
  const fetching = data.running || !!(data.job && data.job.action && !data.job.result);
  loc.added.replaceChildren(...(data.added.length ? data.added.map((a) => {
    const inst = a.installed;
    const st = a.status || {};
    const state = inst && inst.commit ? `Installed: ${inst.commit.slice(0, 7)} (${inst.ref === 'pinned' ? 'the pinned commit' : inst.ref}), from ${a.repo}.`
      : st.error ? `Not installed: ${st.error}` : fetching ? 'Fetching…' : 'Not installed yet.';
    return el('div', { className: 'setting library-source' }, el('span', {},
      el('span', { className: 'setting-name', textContent: a.title }),
      accessSlot(a.id),
      el('span', { className: 'setting-desc', textContent: a.summary }),
      el('span', { className: `setting-desc${st.error && !(inst && inst.commit) ? ' bad' : ''}`, textContent: state }),
      el('span', { className: 'setting-desc', textContent: capsText(a.capabilities) + (a.from_catalogue ? '' : ' Added by pasting its manifest.') }),
      catalogueNote(a),
      el('span', { className: 'library-buttons' },
        inst && inst.commit ? el('a', { className: 'head-btn', href: a.href, target: '_blank', textContent: 'Open' }) : null,
        a.catalogue && a.catalogue.status === 'held'
          ? el('button', { type: 'button', textContent: 'Accept the new version', onclick: () => localAccept(a, data) }) : null,
        el('button', { type: 'button', textContent: 'Remove', onclick: () => localRemove(a) })),
      note(a.id)));
  }) : [el('p', { className: 'setting-desc', textContent: 'None added yet.' })]));
  const offered = data.catalogue.filter((c) => !c.added);
  loc.cat.replaceChildren(
    ...(data.catalogue_error ? [el('p', { className: 'setting-desc bad', textContent: `The catalogue has a fault: ${data.catalogue_error}` })] : []),
    ...(offered.length ? offered.map((c) => el('div', { className: 'setting library-source' }, el('span', {},
      el('span', { className: 'setting-name', textContent: c.title }),
      el('span', { className: 'setting-desc', textContent: c.summary }),
      el('span', { className: 'setting-desc', textContent: `${capsText(c.capabilities)} From ${c.repo} at ${String(c.pin).slice(0, 7)}.` }),
      el('span', { className: 'library-buttons' }, el('button', { type: 'button', textContent: 'Add', disabled: fetching, onclick: () => localAdd(c) })),
      note(c.id)))) : [el('p', { className: 'setting-desc', textContent: 'Everything in the catalogue is added.' })]));
  loc.errors.replaceChildren(...Object.values(data.errors || {}).map((why) => el('p', { className: 'setting-desc bad', textContent: `Left out: ${why}` })));
  clearTimeout(locPoll);
  if (fetching) locPoll = setTimeout(() => { loadLocal(); loadAccess(); }, 1500);
}

// Where an added add-on stands against the catalogue (server.py catalogue_sync).
function catalogueNote(a) {
  const c = a.catalogue;
  if (!c) return null;
  const day = new Date(c.at * 1000).toLocaleDateString();
  if (c.status === 'updated') {
    return el('span', { className: 'setting-desc', textContent: `Updated from the catalogue on ${day}: ${(c.changed || []).join(', ') || 'small changes'}.` });
  }
  if (c.status === 'held') {
    return el('span', { className: 'setting-desc bad', textContent: `The catalogue has a newer version that changes what you agreed to: ${(c.held || []).join('; ')}. `
      + 'The version you agreed to keeps running until you accept the new one.' });
  }
  if (c.status === 'gone') return el('span', { className: 'setting-desc', textContent: "No longer in the catalogue: it keeps working, but won't be updated from it." });
  return null;
}

function localAccept(a, data) {
  const c = (data.catalogue || []).find((x) => x.id === a.id) || {};
  if (!confirm(`${c.consent || ''}\n\nWhat changed: ${((a.catalogue || {}).held || []).join('; ')}.\n\n${capsText(c.capabilities)}`)) return;
  localPost({ action: 'accept', id: a.id, agree: true }, a.id, `${a.title}: the new version accepted, fetching it…`);
}

async function loadLocal() {
  try { renderLocal(await getJSON('/admin/local-addons')); } catch (err) { console.error('local add-ons:', err); }
}

async function localPost(body, id, doing) {
  try {
    await postJSON('/admin/local-addons', body);
    locNote = { id, text: doing, ok: true };
  } catch (err) { locNote = { id, text: err.message, ok: false }; }
  loadLocal();
  loadAccess();
}

function localAdd(c) {
  if (!confirm(`${c.consent}\n\n${capsText(c.capabilities)}\n\nIt starts off: switch it on here once it is installed.`)) return;
  localPost({ action: 'add', id: c.id, agree: true }, c.id, `Adding ${c.title}: fetching it…`);
}

function localRemove(a) {
  if (!confirm(`Remove ${a.title}? Its files go; adding it back fetches it again.`)) return;
  localPost({ action: 'remove', id: a.id }, a.id, `${a.title} removed.`);
}

loc.pasteGo.addEventListener('click', async () => {
  const bad = (text) => { loc.pasteNote.textContent = text; loc.pasteNote.className = 'setting-desc bad'; };
  let manifest;
  try { manifest = JSON.parse(loc.paste.value); } catch (err) { bad(`Not JSON: ${err.message}`); return; }
  if (!loc.pasteOk.checked) { bad('Read the warning, and tick the box, first.'); return; }
  const addon = (manifest && manifest.addon) || {};
  if (!confirm(`Add "${addon.title}" from a pasted manifest?\n\n${addon.consent || ''}\n\n${capsText(manifest.capabilities)}`)) return;
  try {
    await postJSON('/admin/local-addons', { action: 'paste', manifest, understood: LOC_UNDERSTOOD });
    loc.pasteNote.textContent = 'Added: fetching it.';
    loc.pasteNote.className = 'setting-desc';
    loc.paste.value = '';
    loc.pasteOk.checked = false;
  } catch (err) { bad(err.message); }
  loadLocal();
  loadAccess();
});

loadLocal();

// --- setup steps -----------------------------------------------------------------------
// The rest of the first-use setup (the password is step 1, on admin-setup.html). Each step's
// line is filled in by the part of this page that already loads its data; the pane shows
// until the owner finishes it, and Overview's link brings it back.
const setupList = document.getElementById('setup-steps');

function setupStep(step, text, status) {
  const li = setupList.querySelector(`[data-step="${step}"]`);
  li.querySelector('span').textContent = text;
  li.className = `step-${status}`;
}

function applySetup(done) {
  AL.hide('welcome', done);
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
  usage: noteEl('git-usage'), note: noteEl('git-note'),
  form: document.getElementById('git-create'), createNote: noteEl('git-create-note'),
  grid: noteEl('git-grid'), side: noteEl('git-side'), search: document.getElementById('git-search'),
  chipsKind: noteEl('git-chips-kind'), chipsArea: noteEl('git-chips-area'), manageAs: document.getElementById('git-manage-as'),
  mirrors: noteEl('git-mirrors'), mirrorForm: document.getElementById('git-mirror-add'), mirrorNote: noteEl('git-mirror-note'),
};
const commitDate = (unix) => new Date(unix * 1000).toISOString().slice(0, 10);
// The Git page as cards (git-ci-plan §1, Kiwix's library as the model): a filter bar, a card per
// repository with its badges in words, and Manage, opening under the card or beside the grid.
let gitData = null;
const gitView = { kind: 'all', area: 'all', q: '', open: null };
try { git.manageAs.value = localStorage.getItem('irate-git-manage-as') || 'drawer'; } catch (_) { /* no storage */ }
const PUSH_WORDS = { everyone: 'anyone pushes', admin: 'the admin pushes', nobody: 'read-only' };
const builds = (r) => r.can_build && r.build && r.has_script;
const repoKey = (r) => `${r.area}/${r.name}`;
const GIT_KINDS = [['all', 'All', () => true], ['mine', 'Mine', (r) => !r.mirror_of], ['mirrors', 'Mirrors', (r) => !!r.mirror_of],
  ['builds', 'Builds', builds]];
const GIT_AREAS = [['all', 'Public and private', () => true], ['public', 'Public', (r) => r.area === 'public'],
  ['private', 'Private', (r) => r.area === 'private']];

// One sentence for the card: who sees it, who pushes, whether pushes are built (git-ci-plan §2).
function accessSentence(r) {
  const see = r.area === 'public' ? 'Anyone on the network can see and clone it' : 'Only the admin can see and clone it';
  const push = r.mirror_of ? 'nobody pushes (the librarian keeps it)' : { everyone: 'anyone can push', admin: 'only the admin can push', nobody: 'nobody can push' }[r.write];
  const build = builds(r) ? '; pushes are built' : r.can_build && r.has_script && !r.build ? '; builds are switched off' : '';
  return `${see}; ${push}${build}.`;
}

function renderGit(data) {
  gitData = data;
  if (!data.installed) {
    git.usage.textContent = 'The git servers are not set up on this box: rerun install.sh.';
    git.grid.replaceChildren();
    return;
  }
  renderMirrors(data);
  const total = data.repos.reduce((n, r) => n + r.size, 0);
  git.usage.textContent = `${data.repos.length} repositor${data.repos.length === 1 ? 'y' : 'ies'}, ${size(total)}` +
    (data.free != null ? `; ${size(data.free)} free on the card` : '') +
    `. A single push can be up to ${size(data.max_push)}.`;
  // Submodule mirrors have no card of their own: Git → Mirrors lists them under their parent.
  const shown = data.repos.filter((r) => !r.submodule_of);
  const chips = (box, list, key) => box.replaceChildren(...list.map(([id, label, test]) => el('button', {
    type: 'button', className: `chip${gitView[key] === id ? ' on' : ''}`, textContent: `${label} ${shown.filter(test).length}`,
    onclick: () => { gitView[key] = id; renderGit(gitData); } })));
  chips(git.chipsKind, GIT_KINDS, 'kind');
  chips(git.chipsArea, GIT_AREAS, 'area');
  [...git.chipsKind.children, ...git.chipsArea.children].forEach((b) => b.setAttribute('aria-pressed', b.classList.contains('on')));
  const q = gitView.q.toLowerCase();
  const kindTest = GIT_KINDS.find((k) => k[0] === gitView.kind)[2];
  const areaTest = GIT_AREAS.find((k) => k[0] === gitView.area)[2];
  const list = shown.filter((r) => kindTest(r) && areaTest(r) && (!q || r.name.toLowerCase().includes(q) || (r.description || '').toLowerCase().includes(q)));
  const side = git.manageAs.value === 'side';
  const cards = [newCard()];
  for (const r of list) {
    cards.push(repoCard(r, data));
    if (!side && gitView.open === repoKey(r)) cards.push(managePanel(r, data, 'git-drawer'));
  }
  if (!side && gitView.open === 'new') cards.splice(1, 0, newPanel('git-drawer'));
  if (!list.length) cards.push(el('p', { className: 'setting-desc', textContent: shown.length ? 'Nothing matches.' : 'No repositories yet.' }));
  git.grid.replaceChildren(...cards);
  const openRepo = shown.find((r) => repoKey(r) === gitView.open);
  git.side.hidden = !(side && (openRepo || gitView.open === 'new'));
  git.side.replaceChildren(...(git.side.hidden ? [] : [openRepo ? managePanel(openRepo, data, 'git-sidepanel') : newPanel('git-sidepanel')]));
  document.getElementById('git-layout').classList.toggle('with-side', !git.side.hidden);
}

function newCard() {
  return withAttrs(el('button', { type: 'button', className: 'git-card git-new',
    onclick: () => { gitView.open = gitView.open === 'new' ? null : 'new'; renderGit(gitData); } },
  el('span', { className: 'git-new-plus', textContent: '＋', ariaHidden: 'true' }), el('strong', { textContent: 'New' }),
  el('span', { className: 'setting-desc', textContent: 'An empty repository, or a mirror of one on the internet' })),
  { 'aria-expanded': String(gitView.open === 'new') });
}
const withAttrs = (node, attrs) => { for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); return node; };

function newPanel(cls) {
  git.form.hidden = false;
  return el('div', { className: `git-manage ${cls}` },
    el('div', { className: 'git-manage-head' }, el('strong', { textContent: 'New' }), closeButton()),
    el('section', {}, el('h4', { textContent: 'An empty repository' }),
      el('p', { className: 'setting-desc', textContent: 'Push to it from a computer. Public: anyone on the network can browse and clone it; only the admin pushes until you choose otherwise. Private: everything needs the admin login.' }),
      git.form, git.createNote),
    el('section', {}, el('h4', { textContent: 'A mirror of a repository on the internet' }),
      el('p', { className: 'setting-desc', textContent: 'Kept by the librarian to a policy (branches, releases, submodules), read-only here. Mirrors are set up and managed under Mirrors, below.' }),
      el('a', { href: '#mirrors', className: 'button-link', textContent: 'Add a mirror' })));
}

const closeButton = () => actionButton('Close', () => { gitView.open = null; renderGit(gitData); }, { className: 'small' });
const copyUrl = (r) => actionButton('Copy clone URL', () => {
  const url = `${location.origin}${r.url.replace(/\/$/, '')}`;
  navigator.clipboard?.writeText(url).then(() => say(`Copied ${url}`, true, git.note), () => say(url, true, git.note));
}, { className: 'small' });

function repoCard(r, data) {
  const m = r.mirror_of ? (data.mirrors || []).find((x) => x.name === r.name && x.area === r.area) : null;
  const ms = (m && m.status) || {};
  const b = r.last_build;
  const badges = [
    [r.area === 'public' ? 'Public' : 'Private', `badge-${r.area}`],
    [r.mirror_of ? 'read-only' : PUSH_WORDS[r.write], 'badge-push'],
    r.mirror_of ? ['Mirror', 'badge-mirror'] : null,
    builds(r) ? [b ? `${STATE_ICON[b.state] || ''} build ${b.state}${b.started ? `, ${commitDate(b.started)}` : ''}` : 'builds on push', `badge-build${b && b.state === 'failed' ? ' bad' : ''}`] : null,
  ].filter(Boolean);
  const facts = r.mirror_of
    ? [`from ${r.mirror_of.replace(/^https:\/\//, '')}`, ms.updated ? `updated ${commitDate(ms.updated)}` : 'not fetched yet', size(r.size)]
    : [r.last_commit ? `Last commit ${commitDate(r.last_commit)}` : 'Empty', r.branches ? `${r.branches} branch${r.branches === 1 ? '' : 'es'}` : null, size(r.size)];
  const open = gitView.open === repoKey(r);
  return el('div', { className: `git-card${open ? ' open' : ''}` },
    el('h4', {}, el('a', { href: r.url, textContent: `${r.name}.git` })),
    el('p', { className: 'badges' }, ...badges.map(([text, cls]) => el('span', { className: `badge ${cls}`, textContent: text }))),
    r.description ? el('p', { className: 'git-about' }, el('span', { className: 'git-label', textContent: 'About ' }), r.description) : null,
    el('p', { className: 'setting-desc git-facts', textContent: facts.filter(Boolean).join(' · ') }),
    el('p', { className: 'setting-desc git-access', textContent: accessSentence(r) }),
    el('p', { className: 'library-buttons' }, copyUrl(r),
      r.mirror_of ? el('a', { href: '#mirrors', className: 'button-link small', textContent: 'Manage the mirror' })
        : withAttrs(actionButton('Manage', () => { gitView.open = open ? null : repoKey(r); renderGit(gitData); },
          { className: 'small' }), { 'aria-expanded': String(open) })));
}

// Manage: each part with a sentence of what it does; Delete at the bottom, apart.
function managePanel(r, data, cls) {
  const act = (body, confirmText, done) => async () => {
    if (confirmText && !confirm(confirmText)) return false;
    try {
      const out = await postJSON('/admin/git', body);
      if (body.action === 'move') gitView.open = `${body.to}/${r.name}`;
      if (body.action === 'delete') gitView.open = null;
      renderGit(out);
      say(done || '', true, git.note);
      return true;
    } catch (err) { say(err.message, false, git.note); renderGit(gitData); return false; }
  };
  const radio = (group, value, label, checked, onpick) => el('label', { className: 'inline' },
    el('input', { type: 'radio', name: `${group}-${repoKey(r)}`, value, checked, onchange: onpick }), ` ${label}`);
  // Who can see it: the area. Moving changes the clone URL, so it asks.
  const see = el('fieldset', { className: 'git-q' }, el('legend', { textContent: 'Who can see it?' }),
    radio('see', 'public', 'Everyone on the network', r.area === 'public', act({ action: 'move', area: r.area, name: r.name, to: 'public' },
      `Move ${r.name}.git to /git/? Anyone on the network will be able to browse and clone it, and its clone URL changes.`, `${r.name}.git is public now.`)),
    radio('see', 'private', 'Only the admin', r.area === 'private', act({ action: 'move', area: r.area, name: r.name, to: 'private' },
      `Move ${r.name}.git to /git-private/? Only the admin login will see or clone it, and its clone URL changes.`, `${r.name}.git is private now.`)),
    el('p', { className: 'setting-desc', textContent: 'Moving it changes its clone URL; clones elsewhere need git remote set-url.' }));
  const presets = (data.presets || {})[r.area] || [];
  const WHO = { everyone: 'Anyone', users: 'The box\'s users (their account\'s name and password)', admin: 'Only the admin', nobody: 'Nobody (read-only)' };
  const push = el('fieldset', { className: 'git-q' }, el('legend', { textContent: 'Who can push?' }),
    ...presets.map((p) => radio('push', p.name, WHO[p.write], p.name === r.preset, act({ action: 'preset', area: r.area, name: r.name, preset: p.name },
      p.write === 'everyone' ? `Let anyone on the network push to ${r.name}.git, without the login? They cannot rewrite or delete its history, and the public repositories have a size cap, but anything they push is served from this box.` : null,
      `${r.name}.git: ${p.text}.`))),
    el('p', { className: 'setting-desc', textContent: 'The hub decides each push; nobody can rewrite or delete history in a public repository.' }));
  const buildNow = r.can_build && r.build && r.has_script ? actionButton('Build now', async () => {
    try {
      const out = await postJSON('/admin/ci', { action: 'build', repo: r.name });
      say(`Queued: ${r.name}.git ${out.branch} at ${out.commit.slice(0, 7)}. Its run shows under Builds.`, true, git.note);
      renderCi(out.snapshot);
    } catch (err) { say(err.message, false, git.note); }
  }, { className: 'small' }) : null;
  const build = el('fieldset', { className: 'git-q' }, el('legend', { textContent: 'Build on push?' }),
    r.can_build
      ? el('label', { className: 'inline' }, el('input', { type: 'checkbox', className: 'git-build', checked: r.build,
        onchange: (e) => act({ action: 'build', area: r.area, name: r.name, on: e.target.checked }, null,
          `${r.name}.git: builds ${e.target.checked ? 'on' : 'off'}.`)() }), ' Build each push that has a .irate-ci.sh')
      : null,
    el('p', { className: 'setting-desc', textContent: r.can_build
      ? (r.has_script ? 'Its newest commit has a .irate-ci.sh. A build runs as a user of its own, sandboxed, at the lowest priority (Builds, below).'
        : 'Its newest commit has no .irate-ci.sh yet: add one at the top of the repository to build.')
      : r.area === 'public' ? 'Public repositories never build: anyone could push what runs.'
        : 'Only for repositories that only the admin can push to.' }),
    buildNow, r.can_build ? buildSetup(r) : null);
  const desc = el('textarea', { rows: 2, maxLength: 200, value: r.description || '', className: 'git-desc' });
  const about = el('section', {}, el('h4', { textContent: 'About' }),
    el('p', { className: 'setting-desc', textContent: 'A line on what it is, shown on its card and in cgit.' }), desc,
    actionButton('Save', () => act({ action: 'describe', area: r.area, name: r.name, description: desc.value }, null, 'Saved.')(), { className: 'small' }));
  const others = data.repos.filter((o) => !(o.area === r.area && o.name === r.name) && !o.mirror_of);
  const target = el('select', { className: 'git-publish-to' }, ...others.map((o) => el('option', { value: repoKey(o), textContent: `${o.area}/${o.name}.git` })));
  const refs = withAttrs(el('input', { value: 'main', className: 'git-publish-refs' }), { 'aria-label': 'Branches or tags, separated by spaces' });
  const publish = el('section', {}, el('h4', { textContent: 'Publish to…' }),
    el('p', { className: 'setting-desc', textContent: 'Copy branches or tags into another repository on the box (they replace those of the same name there): a private draft to a public copy, say.' }),
    others.length ? el('p', { className: 'library-buttons' }, target, refs, actionButton('Publish', () => {
      const [toArea, toName] = target.value.split('/');
      act({ action: 'publish', from: { area: r.area, name: r.name }, to: { area: toArea, name: toName }, refs: refs.value.trim().split(/\s+/) },
        null, 'Published.')();
    }, { className: 'small' })) : el('p', { className: 'setting-desc', textContent: 'There is no other repository to publish to yet.' }));
  return el('div', { className: `git-manage ${cls}` },
    el('div', { className: 'git-manage-head' }, el('strong', { textContent: `Manage ${r.name}.git` }), closeButton()),
    el('section', {}, el('h4', { textContent: 'Access' }), el('p', { className: 'setting-desc', textContent: accessSentence(r) }), see, push, build),
    about, publish,
    el('section', { className: 'git-danger' }, el('h4', { textContent: 'Delete' }),
      el('p', { className: 'setting-desc', textContent: 'Its history goes with it. Clones elsewhere keep theirs; this one cannot be brought back.' }),
      actionButton(`Delete ${r.name}.git`, act({ action: 'delete', area: r.area, name: r.name },
        `Delete ${r.name}.git and all its history? Clones elsewhere keep theirs; this one cannot be brought back.`, 'Deleted.'), { className: 'small danger' })));
}

git.search.addEventListener('input', () => { gitView.q = git.search.value.trim(); if (gitData) renderGit(gitData); });
git.manageAs.addEventListener('change', () => {
  try { localStorage.setItem('irate-git-manage-as', git.manageAs.value); } catch (_) { /* no storage */ }
  if (gitData) renderGit(gitData);
});

// Mirrors (mirrors.py): what each keeps, its size and last outcome, and check / update / remove.
function renderMirrors(data) {
  const act = (body, confirmText) => async () => {
    if (confirmText && !confirm(confirmText)) return;
    try { renderGit(await postJSON('/admin/git', body)); say(body.action === 'mirror-remove' ? 'Removed.' : 'Started: the outcome shows here when it is done.', true, git.mirrorNote); }
    catch (err) { say(err.message, false, git.mirrorNote); }
  };
  const list = data.mirrors || [];
  git.mirrors.replaceChildren(...(list.length ? list.map((m) => {
    const st = m.status || {};
    const keeps = [m.branches.join(', '),
      ...m.groups.map((g) => `${g.keep} ${g.kind === 'tags' ? `tag${g.keep === 1 ? '' : 's'} ${g.pattern}` : g.kind + (g.keep === 1 ? '' : 's')}`),
      m.history, m.submodules ? 'with submodules' : null, `budget ${m.budget_mb} MB`].filter(Boolean).join(' · ');
    const subs = Object.entries(st.submodules || {}).map(([p, s]) => `${p} (${s.kept.length} commit${s.kept.length === 1 ? '' : 's'}, ${size(s.size)})`);
    const state = st.error ? `error: ${st.error}` : st.outcome
      ? `${st.outcome}${st.size != null ? ` · ${size(st.size)}` : ''}${st.checked ? ` · checked ${commitDate(st.checked)}` : ''}`
      : 'not fetched yet';
    // Release groups: revoked releases (named so by their project) are left out unless switched off.
    const relGroups = m.groups.filter((g) => g.kind !== 'tags');
    const skipping = relGroups.some((g) => g.skip_revoked !== false);
    const revoked = ((st.releases || {}).list || []).filter((r) => r[2]).map((r) => r[0]);
    const relNote = !relGroups.length ? null : [
      skipping ? (revoked.length ? `revoked, left out: ${revoked.join(', ')}` : 'revoked releases are left out')
        : 'revoked releases are kept like any other',
      st.releases_cached ? `release list from the cache of ${st.releases_cached.since ? commitDate(st.releases_cached.since) : 'earlier'} (${st.releases_cached.why})` : null,
    ].filter(Boolean).join(' · ');
    return el('div', { className: 'admin-item' },
      el('span', {},
        el('strong', {}, el('a', { href: `${m.area === 'public' ? '/git/' : '/git-private/'}${m.name}.git/`, textContent: `${m.name}.git` })),
        el('span', { className: 'setting-desc', textContent: `from ${m.upstream}` }),
        el('span', { className: 'setting-desc', textContent: `keeps ${keeps}` }),
        subs.length ? el('span', { className: 'setting-desc', textContent: `submodules: ${subs.join(', ')}` }) : null,
        relNote ? el('span', { className: `setting-desc${st.releases_cached ? ' warn' : ''}`, textContent: relNote }) : null,
        el('span', { className: `setting-desc${st.error || st.over_budget ? ' bad' : ''}`, textContent: state })),
      el('span', { className: 'library-buttons' },
        actionButton('Check', act({ action: 'mirror-check', name: m.name }), { className: 'small', disabled: data.running }),
        actionButton('Update', act({ action: 'mirror-update', name: m.name }), { className: 'small', disabled: data.running }),
        actionButton(m.submodules ? 'Without submodules' : 'With submodules', act({ action: 'mirror-change',
          mirror: Object.assign({}, m, { status: undefined, submodules: !m.submodules }) }), { className: 'small' }),
        relGroups.length ? actionButton(skipping ? 'Keep revoked' : 'Leave revoked out', act({ action: 'mirror-change',
          mirror: Object.assign({}, m, { status: undefined, groups: m.groups.map((g) => g.kind === 'tags' ? g : Object.assign({}, g, { skip_revoked: !skipping })) }) },
          skipping ? 'Keep releases their project has revoked (for looking into them)? They are left out by default.' : null), { className: 'small' }) : null,
        actionButton('Remove', act({ action: 'mirror-remove', name: m.name },
          `Remove the mirror ${m.name}.git and its copy on the box? The upstream is not touched.`), { className: 'small' })));
  }) : [el('p', { className: 'setting-desc', textContent: 'None yet.' })]));
}

git.mirrorForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = git.mirrorForm.elements;
  const groups = [];
  if (f.pattern.value.trim()) groups.push({ kind: 'tags', pattern: f.pattern.value.trim(), keep: Number(f.releases.value) || 0 });
  else {
    if (Number(f.releases.value)) groups.push({ kind: 'release', keep: Number(f.releases.value) });
    if (Number(f.prereleases.value)) groups.push({ kind: 'prerelease', keep: Number(f.prereleases.value) });
  }
  const mirror = { upstream: f.upstream.value.trim(), name: f.name.value.trim(), area: f.area.value,
    branches: f.branches.value.trim().split(/\s+/).filter(Boolean), groups, history: f.history.value,
    budget_mb: Number(f.budget.value) || 2048, submodules: f.submodules.checked };
  try {
    renderGit(await postJSON('/admin/git', { action: 'mirror-add', mirror }));
    say(`Added ${mirror.name}.git: Update fetches it now, or the librarian on its schedule.`, true, git.mirrorNote);
  } catch (err) { say(err.message, false, git.mirrorNote); }
});

async function loadGit() {
  try { renderGit(await getJSON('/admin/git')); } catch (_) { git.usage.textContent = 'Could not read the repositories.'; }
}

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

let ciData = null;
let ciViewing = null; // { run, next, log } while a run's view is open
function renderCi(data) {
  ciData = data;
  if (!data.installed) { ciAbout.textContent = 'Builds are not set up on this box: rerun install.sh.'; return; }
  ciAbout.textContent = `A push to a private repository whose commit has a ${data.script} at its top builds it here: ` +
    `a fresh clone, then bash ${data.script}, as a user of its own, at the lowest priority. Public repositories never build. ` +
    'Set one up, or build now, under Manage on its card.' + (data.queued ? ` ${data.queued} waiting.` : '');
  const mem = data.memory || {};
  const memText = /^\d+$/.test(mem.max || '') ? `memory capped at ${size(Number(mem.max))}` : 'no memory cap';
  noteEl('ci-limits').textContent = `Each build: up to ${Math.round(data.time_limit / 3600)} hours, ${memText}, ` +
    `the lowest CPU priority. The runs use ${size(data.runs_bytes || 0)}; the newest ${data.keep_runs} per repository are kept, and any marked Keep.`;
  const kf = document.getElementById('ci-keep-form');
  if (!kf.contains(document.activeElement)) kf.elements.keep.value = data.keep_runs;
  noteEl('ci-offline').textContent = 'With no internet: ' + ((data.mirrored || []).length
    ? `a build's git fetches of ${data.mirrored.length} URL${data.mirrored.length === 1 ? '' : 's'} come from this box's mirrors (${data.mirrored.slice(0, 3).map((u) => u.replace(/^https:\/\//, '')).join(', ')}${data.mirrored.length > 3 ? '…' : ''})`
    : 'no mirrors yet, so a build that fetches from the internet needs it (Git → Mirrors)')
    + (data.pio_deps ? '; PlatformIO\'s packages come from the library\'s cache (CI_PIO_DEPS).' : '; no PlatformIO cache kept (Builds above, the firmware build cache).')
    + (data.wheelhouse ? ' pip installs (platformio itself) come from the Building kit\'s wheelhouse (PIP_NO_INDEX, PIP_FIND_LINKS).'
      : ' The Building kit has no wheelhouse yet: a build\'s pip install still needs the internet.');
  ciRuns.replaceChildren(...(data.runs.length ? data.runs.map((r) => el('div', { className: 'admin-item' },
    el('span', {},
      el('strong', { textContent: `${STATE_ICON[r.state] || ''} ${r.repo} · ${r.branch} · ${r.commit.slice(0, 7)}` + (r.keep ? ' · kept' : '') }),
      el('span', { className: 'setting-desc', textContent: [r.state, r.by === 'build-now' ? 'built by hand' : null,
        r.duration != null ? minutes(r.duration) : null,
        r.started ? commitDate(r.started) : null].filter(Boolean).join(' · ') })),
    el('span', { className: 'library-buttons' }, actionButton('View', () => openRun(r.run), { className: 'small' }))))
    : [el('p', { className: 'setting-desc', textContent: 'No builds yet.' })]));
  // A build that failed since this page last showed the Git pane: a mark on its side-bar link.
  let seen = 0;
  try { seen = Number(localStorage.getItem('irate-ci-seen')) || 0; } catch (_) { /* no storage */ }
  const newBad = data.runs.filter((r) => r.finished && r.finished > seen && r.state !== 'passed').length;
  if (paneShown('git')) { try { localStorage.setItem('irate-ci-seen', String(Date.now() / 1000)); } catch (_) { /* no storage */ } }
  badge('git', !paneShown('git') && newBad ? String(newBad) : '');
  clearTimeout(ciPoll);
  if (data.queued || data.runs.some((r) => r.state === 'running')) ciPoll = setTimeout(loadCi, 5000);
}

const ciFile = (run, name) => `/admin/ci/file?run=${encodeURIComponent(run)}&name=${encodeURIComponent(name)}`;
async function openRun(run) {
  ciViewing = { run, next: 0, log: '' };
  await followRun();
}
async function followRun() {
  if (!ciViewing) return;
  const box = noteEl('ci-run-view');
  let v;
  try { v = await getJSON(`/admin/ci/run?run=${encodeURIComponent(ciViewing.run)}&from=${ciViewing.next}`); }
  catch (err) { box.hidden = false; box.replaceChildren(el('p', { className: 'setting-desc bad', textContent: err.message })); return; }
  ciViewing.log += v.log;
  ciViewing.next = v.next;
  const st = v.status;
  const running = st.state === 'running';
  const pre = el('pre', { className: 'admin-log ci-log', textContent: ciViewing.log.slice(-200000) });
  box.hidden = false;
  box.replaceChildren(
    el('div', { className: 'git-manage-head' }, el('strong', { textContent: `${STATE_ICON[st.state] || ''} ${st.repo} · ${st.branch} · ${st.commit.slice(0, 7)}` }),
      actionButton('Close', () => { ciViewing = null; box.hidden = true; }, { className: 'small' })),
    el('p', { className: 'setting-desc', textContent: [st.state, st.duration != null ? minutes(st.duration) : running ? `running for ${minutes(Math.round(Date.now() / 1000 - st.started))}` : null,
      st.keep ? 'kept' : null].filter(Boolean).join(' · ') }),
    v.steps.length ? el('ol', { className: 'ci-steps' }, ...v.steps.map((x) => el('li', { textContent: x }))) : null,
    pre,
    v.artifacts.length ? el('p', { className: 'library-buttons' }, el('span', { className: 'setting-desc', textContent: 'Artifacts: ' }),
      ...v.artifacts.map((a) => el('a', { href: ciFile(v.run, a.name), className: 'button-link small', textContent: `${a.name} (${size(a.size)})` }))) : null,
    el('p', { className: 'library-buttons' },
      el('a', { href: ciFile(v.run, 'log.txt'), target: '_blank', className: 'button-link small', textContent: 'The whole log' }),
      running ? null : actionButton(st.keep ? 'Stop keeping' : 'Keep', () => ciChange(v.run, st.keep ? 'unkeep' : 'keep'), { className: 'small' }),
      running ? null : actionButton('Delete', () => { if (confirm('Delete this run, its log and its artifacts?')) ciChange(v.run, 'delete'); }, { className: 'small danger' })));
  pre.scrollTop = pre.scrollHeight;
  if (running) setTimeout(followRun, 2000);
}
async function ciChange(run, action) {
  try {
    await postJSON('/admin/ci', { action, run });
    say(action === 'delete' ? 'Deleted once the builder gets to it (after a build in progress).' : 'Done once the builder gets to it.', true, git.note);
    if (action === 'delete') { ciViewing = null; noteEl('ci-run-view').hidden = true; }
    setTimeout(loadCi, 1500);
  } catch (err) { say(err.message, false, git.note); }
}
document.getElementById('ci-keep-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try { renderCi(await postJSON('/admin/ci', { action: 'settings', keep_runs: Number(e.target.elements.keep.value) })); say('Saved.', true, git.note); }
  catch (err) { say(err.message, false, git.note); }
});

// "Set up builds", in a repository's Manage: what a script gets, and templates to start from.
function buildSetup(r) {
  if (!ciData || !ciData.templates) return null;
  return el('details', { className: 'ci-setup' }, el('summary', { textContent: 'Set up builds: what a script gets, and templates' }),
    el('p', { className: 'setting-desc', textContent: `Put a ${ciData.script} at the top of the repository; each push to a branch runs it with bash, in a fresh clone of that commit.` }),
    el('dl', { className: 'ci-env' }, ...Object.entries(ciData.env || {}).flatMap(([k, v]) => [el('dt', {}, el('code', { textContent: k })), el('dd', { textContent: v })])),
    ...Object.entries(ciData.templates).map(([k, t]) => {
      const pre = el('pre', { className: 'admin-log', textContent: t.script });
      return el('div', { className: 'ci-template' }, el('strong', { textContent: t.title }),
        actionButton('Copy', () => navigator.clipboard?.writeText(t.script).then(() => say(`Copied the ${t.title} template: save it as ${ciData.script}.`, true, git.note),
          () => say('Select the text and copy it.', false, git.note)), { className: 'small' }), pre);
    }));
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
  boards: noteEl('fw-boards'), filter: noteEl('fw-filter'), kept: noteEl('fw-kept'), source: noteEl('fw-source'),
  cacheForm: document.getElementById('ci-cache-form'), cacheStatus: noteEl('ci-cache-status'), cacheNote: noteEl('ci-cache-note'),
};
// The firmware's source, as a mirror (git-ci-plan 4a): what the Mirror the source button adds.
const FW_SOURCE_MIRROR = { upstream: 'https://github.com/meshtastic/firmware', name: 'meshtastic-firmware', area: 'public',
  branches: ['master'], groups: [{ kind: 'release', keep: 2 }, { kind: 'prerelease', keep: 2 }], history: 'shallow',
  budget_mb: 2048, submodules: true };
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
  if (!fw.cacheForm.contains(document.activeElement)) fw.cacheForm.elements.cache.value = cfg.cache;
  fw.cacheStatus.replaceChildren(...(st.cache
    ? [`Kept: ${st.cache.version}'s, ${st.cache.mode === 'native' ? 'headless' : 'all of it'}, ${size(st.cache.bytes)}. `,
      actionButton('Flush', async () => {
        try { fwData = await postJSON('/admin/firmware', { action: 'flush-cache' }); renderFirmware(fwData); say('Gone. The next check or update re-fetches it if the policy still wants one.', true, fw.cacheNote); }
        catch (err) { say(err.message, false, fw.cacheNote); }
      }, { className: 'small' })]
    : [cfg.cache === 'discard' ? '' : 'Not fetched yet: the library fetches it on its schedule, or Update now on the Firmware page.']));
  fw.source.replaceChildren(...(data.source
    ? [`Source: mirrored as `, el('a', { href: `${data.source.area === 'public' ? '/git/' : '/git-private/'}${data.source.name}.git/`, textContent: `${data.source.name}.git` }),
      ' (Git → Mirrors). The pinout map\'s board configs are read from it, with no internet.']
    : ['Source: not mirrored on this box. ', actionButton('Mirror the source', async () => {
      try {
        await postJSON('/admin/git', { action: 'mirror-add', mirror: FW_SOURCE_MIRROR });
        say('Added meshtastic-firmware.git (master, 2 releases, 2 pre-releases, shallow, with submodules): the library fetches it on its schedule, or Update on Git → Mirrors.', true, fw.note);
        loadFirmware();
      } catch (err) { say(err.message, false, fw.note); }
    }, { className: 'small' })]));
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
        (info.unavailable && info.unavailable.length ? `; left out, a file missing from the release: ${info.unavailable.map((u) => u.board).join(', ')}` : '') +
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
      keep_alpha: Number(f.keep_alpha.value), keep_beta: Number(f.keep_beta.value),
      boards: f.all_boards.checked ? 'all' : [...fwChosen] });
    renderFirmware(fwData);
    say('Saved. Update now fetches what is missing; the schedule does the rest.', true, fw.note);
  } catch (err) { say(err.message, false, fw.note); }
});
fw.cacheForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    fwData = await postJSON('/admin/firmware', { action: 'settings', cache: fw.cacheForm.elements.cache.value });
    renderFirmware(fwData);
    say('Saved. The library fetches or drops it on its schedule (Update now on the Firmware page does it now).', true, fw.cacheNote);
  } catch (err) { say(err.message, false, fw.cacheNote); }
});
for (const [id, action] of [['fw-check', 'check'], ['fw-update', 'update']]) {
  document.getElementById(id).addEventListener('click', async () => {
    try { fwData = await postJSON('/admin/firmware', { action }); renderFirmware(fwData); say('', true, fw.note); } catch (err) { say(err.message, false, fw.note); }
  });
}
loadFirmware();

// --- toolkits (toolkits.py, root's kits.py): a card per kit, Kiwix's library as the model --------
// Each action is a request for the root helper; the page shows "Working…" until its answer is in.
const kitsEl = { summary: noteEl('kits-summary'), note: noteEl('kits-note'), grid: noteEl('kits-grid'),
  problems: noteEl('kits-problems'), budget: document.getElementById('kits-budget'),
  add: document.getElementById('kits-add'), addTitle: noteEl('kits-form-title'), addCancel: document.getElementById('kits-add-cancel') };
const REMOVE_AFTER = [[1, 'an hour'], [4, '4 hours'], [24, 'a day'], [168, 'a week'], [720, 'a month'], [null, 'never']];
const hoursWords = (h) => (REMOVE_AFTER.find(([v]) => v === h) || [h, h == null ? 'never' : `${h} hours`])[1];
const dayOf = (unix) => (unix ? new Date(unix * 1000).toISOString().slice(0, 10) : '');
const kitsWaiting = new Set();
let kitsData = null;
let kitsPoll = null;
let kitsOpen = null; // the card whose Install… step is showing

function hoursPicker(value) {
  return el('select', { className: 'kit-hours' }, ...REMOVE_AFTER.map(([v, label]) =>
    el('option', { value: v == null ? 'never' : String(v), textContent: label, selected: v === value })));
}
const pickedHours = (sel) => (sel.value === 'never' ? null : Number(sel.value));

async function kitAct(body, quiet) {
  try {
    const data = await postJSON('/admin/kits', body);
    if (data.id) kitsWaiting.add(data.id);
    if (!quiet) say(data.id ? 'Asked: the root helper works on it now (a fetch takes a few minutes).' : 'Saved.', true, kitsEl.note);
    renderKits(data);
  } catch (err) { say(err.message, false, kitsEl.note); }
}

function renderKits(data) {
  kitsData = data;
  const st = data.status || {};
  const cfg = data.settings;
  const kits = Object.values(data.kits || {});
  // Answers to what this page asked: shown, and the waiting ends.
  for (const r of data.results || []) {
    if (kitsWaiting.has(r.id)) { kitsWaiting.delete(r.id); say(r.message, r.ok, kitsEl.note); }
  }
  const cached = kits.filter((k) => ((st.kits || {})[k.id] || {}).cached);
  const newest = Math.max(0, ...cached.map((k) => st.kits[k.id].cached.fetched));
  const feed = (data.feeds || {}).debsecan;
  kitsEl.summary.textContent = (st.at ? `${cached.length} of ${kits.length} toolkits cached, ${size(st.pool_bytes || 0)} of ${cfg.budget_mb} MB` +
    (newest ? `; newest fetch ${dayOf(newest)}` : '') + '. Cached kits install with no internet.' : 'Not looked at yet.') +
    (feed && feed.fetched ? ` debsecan's data: ${dayOf(feed.fetched)}.` : '') + (kitsWaiting.size ? ' Working…' : '');
  if (!kitsEl.budget.contains(document.activeElement)) kitsEl.budget.elements.budget.value = cfg.budget_mb;
  kitsEl.problems.replaceChildren(...(st.problems || []).map((p) => checkItem('problem', 'The cache', p,
    'Fetch the kit again while online; nothing installs from a cache that fails its check.')));
  kitsEl.grid.replaceChildren(...kits.map((k) => kitCard(k, st, cfg)));
  clearTimeout(kitsPoll);
  if (kitsWaiting.size) kitsPoll = setTimeout(loadKits, 3000);
}

function kitCard(k, st, cfg) {
  const s = (st.kits || {})[k.id] || {};
  const c = s.cached;
  const inst = (st.installed || {})[k.id];
  const mine = cfg.kits[k.id];
  const left = inst && inst.remove_at ? Math.max(0, Math.round((inst.remove_at - Date.now() / 1000) / 3600)) : null;
  const badges = [
    c ? [`Cached ${dayOf(c.fetched)}, ${size(c.bytes)}`, 'badge-public'] : ['Not cached', 'badge-push'],
    s.previous ? ['2 versions', 'badge-push'] : null,
    k.owner ? ['Yours', 'badge-mirror'] : null,
    ((kitsData || {}).flag_details || {})[k.id] ? ['Flagged by the doctor', 'badge-push bad'] : null,
    (k.extra || []).length ? [`+${k.extra.length} extra`, 'badge-mirror'] : null,
    inst ? [inst.remove_at ? `Installed: removed in ${left < 1 ? 'under an hour' : `${left} h`}` : 'Installed, kept', 'badge-private'] : null,
  ].filter(Boolean);
  const body = [];
  if (inst) {
    const keep = hoursPicker(mine.remove_after);
    body.push(el('p', { className: 'library-buttons' },
      actionButton('Remove now', () => kitAct({ action: 'remove', kit: k.id }), { className: 'small' }),
      el('span', { className: 'setting-desc', textContent: 'Keep it for ' }), keep,
      actionButton('Keep longer', () => kitAct({ action: 'keep', kit: k.id, hours: pickedHours(keep) }), { className: 'small' })),
    inst.upgraded && inst.upgraded.length ? el('p', { className: 'setting-desc', textContent:
      `Installing it also brought these up to date (they stay when it goes): ${inst.upgraded.join(', ')}.` }) : null);
  } else if (kitsOpen === k.id) {
    // The consent step: what the kit can do, and when it goes again.
    const hours = hoursPicker(mine.remove_after);
    const flag = ((kitsData || {}).flag_details || {})[k.id];
    body.push(el('div', { className: 'kit-consent' },
      el('p', { textContent: k.consent }),
      flag ? el('p', { className: 'kit-flag', textContent: `The security doctor flags this kit's cache: ${flag} Refresh it first if the box can reach the internet.` }) : null,
      el('p', { className: 'library-buttons' }, el('span', { className: 'setting-desc', textContent: 'Remove it again after ' }), hours,
        actionButton('Install', () => { kitsOpen = null; kitAct({ action: 'install', kit: k.id, hours: pickedHours(hours) }); }, { className: 'small' }),
        actionButton('Cancel', () => { kitsOpen = null; renderKits(kitsData); }, { className: 'small' }))));
  } else {
    body.push(el('p', { className: 'library-buttons' },
      actionButton('Install…', () => { kitsOpen = k.id; renderKits(kitsData); },
        { className: 'small', disabled: !c, title: c ? '' : 'Not cached yet: Refresh it while the box has internet.' }),
      c ? null : el('span', { className: 'setting-desc', textContent: 'Refresh it while the box has internet to cache it.' })));
  }
  if (k.owner) {
    body.push(el('p', { className: 'library-buttons' },
      actionButton('Edit', () => editKit(k, mine), { className: 'small' }),
      actionButton('Delete', () => {
        if (confirm(`Delete ${k.title} and its cache?`)) kitAct({ action: 'undefine', kit: k.id });
      }, { className: 'small', disabled: !!inst, title: inst ? 'Remove it first' : '' })));
  }
  const def = hoursPicker(mine.remove_after);
  def.addEventListener('change', () => kitAct({ action: 'settings', kits: { [k.id]: { remove_after: pickedHours(def) } } }));
  const details = el('details', { className: 'kit-details' }, el('summary', { textContent: 'Details' }),
    el('ul', { className: 'kit-notes' }, ...(k.notes || []).map((n) => el('li', { textContent: n }))),
    c ? el('p', { className: 'setting-desc', textContent: `${c.packages} packages: ` +
      Object.entries(c.versions || {}).map(([n, v]) => `${n} ${v}`).join(', ') +
      (c.on_box && c.on_box.length ? `. Already on the box: ${c.on_box.join(', ')}.` : '.') }) : null,
    k.owner ? null : extraTools(k),
    c && c.left_out && c.left_out.length ? el('p', { className: 'setting-desc', textContent: `Left out on this ${c.arch || ''} board, as they need a 64-bit one: ${c.left_out.join(', ')}.` }) : null,
    (k.git || []).length ? el('p', { className: 'setting-desc', textContent: `From git: ${k.git.map((g) => `${g.name} (${g.upstream.replace(/^https:\/\//, '')}, a mirror)`).join(', ')}.` }) : null,
    el('p', { className: 'library-buttons' },
      actionButton('Refresh this kit', () => kitAct({ action: 'fetch', kit: k.id }), { className: 'small' }),
      s.previous ? actionButton(`Roll back to ${dayOf(s.previous.fetched)}`, () => {
        if (confirm(`Go back to ${k.title}'s set fetched ${dayOf(s.previous.fetched)}?${inst ? ' Its installed packages are put back to those versions.' : ''}`)) kitAct({ action: 'rollback', kit: k.id });
      }, { className: 'small' }) : null),
    el('p', { className: 'library-buttons' }, el('span', { className: 'setting-desc', textContent: 'Removed after, by default: ' }), def));
  const keepCurrent = el('input', { type: 'checkbox', checked: mine.keep_current,
    onchange: (e) => kitAct({ action: 'settings', kits: { [k.id]: { keep_current: e.target.checked } } }) });
  return el('div', { className: `git-card kit-card${inst ? ' open' : ''}` },
    el('h4', { textContent: k.title }),
    el('p', { className: 'badges' }, ...badges.map(([text, cls]) => el('span', { className: `badge ${cls}`, textContent: text }))),
    el('p', { className: 'git-about', textContent: k.summary }),
    ...body,
    el('label', { className: 'inline setting-desc' }, keepCurrent, ' Keep current (the library refreshes it on its schedule)'),
    details);
}

// Extra tools tracked in a shipped kit (Tom, 2026-10-06: "the toolkit may want an extra tool").
function extraTools(k) {
  const input = el('input', { className: 'kit-extra', value: (k.extra || []).join(' '), placeholder: 'e.g. ltrace gdbserver' });
  return el('p', { className: 'library-buttons' }, el('span', { className: 'setting-desc', textContent: 'Extra tools: ' }), input,
    actionButton('Save', () => kitAct({ action: 'extra', kit: k.id, packages: input.value.trim().split(/[\s,]+/).filter(Boolean) }), { className: 'small' }));
}

function editKit(k, mine) {
  const f = kitsEl.add.elements;
  f.id.value = k.id; f.title.value = k.title; f.summary.value = k.summary || ''; f.packages.value = k.packages.join(' ');
  f.hours.value = mine.remove_after == null ? 'never' : String(mine.remove_after);
  kitsEl.addTitle.textContent = `Change ${k.title}`;
  kitsEl.addCancel.hidden = false;
  kitsEl.add.querySelector('button[type=submit]').textContent = 'Save toolkit';
  kitsEl.add.scrollIntoView?.({ block: 'center' });
}
function resetKitForm() {
  kitsEl.add.reset();
  kitsEl.add.elements.id.value = '';
  kitsEl.addTitle.textContent = 'Add a toolkit of your own';
  kitsEl.addCancel.hidden = true;
  kitsEl.add.querySelector('button[type=submit]').textContent = 'Add toolkit';
}
kitsEl.add.addEventListener('submit', (e) => {
  e.preventDefault();
  const f = kitsEl.add.elements;
  const kit = { title: f.title.value.trim(), summary: f.summary.value.trim(), packages: f.packages.value.trim().split(/[\s,]+/).filter(Boolean),
    remove_after_hours: f.hours.value === 'never' ? null : Number(f.hours.value) };
  if (f.id.value) kit.id = f.id.value;
  kitAct({ action: 'define', kit });
  resetKitForm();
});
kitsEl.addCancel.addEventListener('click', resetKitForm);

// Kits by USB stick (usbstick.py, kits.py): the root helper's last stick scan, Import for each kit
// found for this box's architecture, and a cached kit to copy onto each stick.
let kitsUsbWaiting = null;
async function loadKitsUsb() {
  let data;
  try { data = await getJSON('/admin/usb'); } catch (_) { return; }
  const note = noteEl('kits-usb-note');
  if (kitsUsbWaiting) {
    const done = (data.results || []).find((r) => r.id === kitsUsbWaiting);
    if (done) { kitsUsbWaiting = null; say(done.message, done.ok, note); loadKits(); }
  }
  const p = data.progress;
  const busy = !!kitsUsbWaiting || !!p || data.pending > 0;
  document.getElementById('kits-usb-scan').disabled = busy;
  const st = (kitsData || {}).status || {};
  const cached = Object.entries(st.kits || {}).filter(([, v]) => v.cached);
  const here = (cached[0] && cached[0][1].cached.arch) || '';
  const devs = (data.scan && data.scan.devices) || [];
  noteEl('kits-usb').replaceChildren(...(data.scan && !devs.length ? [el('p', { className: 'setting-desc', textContent: 'No USB stick found. Plug one into the box and look again.' })] : []),
    ...devs.map((d) => {
      const pick = el('select', { disabled: busy || !cached.length }, ...cached.map(([kid]) => el('option', { value: kid, textContent: ((kitsData.kits || {})[kid] || {}).title || kid })));
      return el('div', { className: 'setting library-source' }, el('span', {},
        el('span', { className: 'setting-name', textContent: `${d.label || d.name} (${d.fstype}, ${size(Number(d.size))})` }),
        d.error ? el('span', { className: 'setting-desc bad', textContent: d.error }) : null,
        ...((d.kits || []).length ? d.kits.map((k) => el('span', { className: 'usb-book' },
          el('span', { className: 'setting-desc', textContent: `${k.title}: ${k.packages} packages, ${size(k.bytes)}, ${k.arch}` +
            (here && k.arch !== here ? ` (for another kind of board: this one is ${here})` : '') }),
          !here || k.arch === here ? el('button', { type: 'button', className: 'small', textContent: 'Import', disabled: busy,
            onclick: () => kitsUsbAsk({ action: 'kit-import', device: d.name, kit: k.kit }, `Import ${k.title} from the stick? Every package is checked against Debian's signatures first.`) }) : null))
          : [el('span', { className: 'setting-desc', textContent: 'No toolkits on this stick.' })]),
        cached.length ? el('span', { className: 'library-buttons' }, pick, el('button', { type: 'button', textContent: 'Copy to this stick', disabled: busy,
          onclick: () => kitsUsbAsk({ action: 'kit-export', device: d.name, kit: pick.value }, `Copy ${pick.value} onto ${d.label || d.name}?`) })) : null));
    }),
    p ? el('p', { className: 'setting-desc', textContent: `${p.label}${p.total ? `: ${size(p.done)} of ${size(p.total)}` : '…'}` }) : null);
  if (busy) setTimeout(loadKitsUsb, 1500);
}
async function kitsUsbAsk(body, confirmText) {
  if (confirmText && !confirm(confirmText)) return;
  try { kitsUsbWaiting = (await postJSON('/admin/usb', body)).id; say('Working…', true, noteEl('kits-usb-note')); loadKitsUsb(); }
  catch (err) { say(err.message, false, noteEl('kits-usb-note')); }
}
document.getElementById('kits-usb-scan').addEventListener('click', () => kitsUsbAsk({ action: 'scan' }));

async function loadKits() {
  try {
    const data = await getJSON('/admin/kits');
    // Root writes what is cached and installed; on a first visit, ask it to.
    if (!(data.status || {}).at && !kitsWaiting.size) { kitAct({ action: 'status' }, true); return; }
    renderKits(data);
  } catch (_) { kitsEl.summary.textContent = 'Could not read the toolkits.'; }
}
kitsEl.budget.addEventListener('submit', (e) => {
  e.preventDefault();
  kitAct({ action: 'settings', budget_mb: Number(kitsEl.budget.elements.budget.value) });
});
document.getElementById('kits-refresh-all').addEventListener('click', async () => {
  for (const k of Object.keys((kitsData || {}).kits || {})) await kitAct({ action: 'fetch', kit: k }, true);
  say('Asked for each kit: the root helper fetches them one after another (minutes each).', true, kitsEl.note);
});
window.addEventListener('hashchange', () => { if (paneShown('toolkits')) { loadKits(); loadKitsUsb(); } });
if (paneShown('toolkits')) { loadKits(); loadKitsUsb(); }

// --- the Firmware Factory (factory.py): choose a source, a ref and targets; follow the queue ------
const fac = {
  form: document.getElementById('factory-form'), source: document.getElementById('factory-source'),
  ref: document.getElementById('factory-ref'), search: document.getElementById('factory-search'),
  targets: document.getElementById('factory-targets'), chosen: document.getElementById('factory-chosen'),
  queue: document.getElementById('factory-queue'), note: noteEl('factory-note'), paused: document.getElementById('factory-paused'),
  pause: document.getElementById('factory-pause'), waiting: document.getElementById('factory-waiting'), runs: document.getElementById('factory-runs'),
  flasher: document.getElementById('factory-flasher'),
};
let facData = null;
let facTargets = null;
const facChosen = new Set();
let facPoll = null;
let facFlasher = null; // the boards the Firmware pane keeps for the flasher: ticked to start with
const facDur = (s) => (s == null ? '?' : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const facClock = (t) => new Date(t * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
const facReady = (f) => (facData && facData.readiness[f]) || 'untested on this box: its first build downloads its toolchain';
const facEst = (f) => { const e = facData && facData.estimates[f]; return e ? `about ${facDur(e.seconds)} a target here` : 'no estimate yet'; };

async function loadFactory() {
  try { renderFactory(await getJSON('/admin/factory')); } catch (_) { fac.waiting.textContent = 'Could not read the Firmware Factory.'; }
}

function renderFactory(d) {
  facData = d;
  fac.paused.hidden = !d.paused;
  fac.pause.textContent = d.paused ? 'Resume' : 'Pause';
  const names = d.sources.map((s) => s.name).join('\n');
  if (fac.source.dataset.names !== names) {
    const was = fac.source.value;
    fac.source.dataset.names = names;
    fac.source.replaceChildren(...d.sources.map((s) => el('option', { value: s.name, textContent: `${s.name} (${s.mirror ? 'a mirror' : 'private'})` })));
    if (d.sources.some((s) => s.name === was)) fac.source.value = was;
    if (d.sources.length) loadFactoryRefs();
    else fac.targets.replaceChildren(el('p', { className: 'setting-desc', textContent: 'No source yet: mirror meshtastic/firmware in Git → Mirrors, or push a fork to a private repository.' }));
  }
  const describe = (j) => `${j.name && j.name !== j.env ? `${j.name} (${j.env})` : j.env}, ${j.family}: ${j.source} ${j.ref}`;
  const rows = [];
  if (d.running) {
    const e = d.estimates[d.running.family];
    rows.push(el('div', { className: 'admin-item' }, el('span', { className: 'state state-running', textContent: 'Building' }),
      el('span', { textContent: ` ${describe(d.running)}` }),
      el('span', { className: 'setting-desc', textContent: ` Started ${ago(Date.now() / 1000 - d.running.started)}${e ? `; done about ${facClock(d.running.started + e.seconds)}` : ''}.` })));
  }
  d.waiting.forEach((j, i) => {
    rows.push(el('div', { className: 'admin-item' }, el('span', { textContent: `${i + 1}. ${describe(j)}` }),
      el('span', { className: 'setting-desc', textContent: j.start ? ` Starts about ${facClock(j.start)}${j.finish ? `, done about ${facClock(j.finish)}` : ''}.`
        : ' No estimate: a family before it, or its own, has not been built here.' }),
      el('span', { className: 'library-buttons' },
        i ? actionButton('Up', () => facAct({ action: 'up', id: j.id })) : null,
        actionButton('Cancel', () => facAct({ action: 'cancel', id: j.id })))));
  });
  fac.waiting.replaceChildren(...(rows.length ? rows : [el('p', { className: 'setting-desc', textContent: 'Nothing building or waiting.' })]));
  const mbs = (b) => (b == null ? '?' : `${Math.round(b / 2 ** 20)} MB`);
  const published = new Set((d.flasher || []).flatMap((rel) => rel.targets.map((t) => t.run)));
  fac.runs.replaceChildren(...(d.runs.filter((r) => r.state !== 'running').map((r) => {
    const u = r.resources || {};
    const used = r.resources ? [`${facDur(r.duration)}`, `CPU ${facDur(u.cpu)}`, `peak memory ${mbs(u.peak_memory)}`, `disk ${mbs(u.work_bytes)}`,
      u.hottest != null ? `hottest ${Math.round(u.hottest)} °C` : null,
      r.offline ? 'no network' : u.received ? `the box received ${mbs(u.received)} meanwhile` : null,
      r.tools_only && u.tools_bytes ? `PlatformIO's tools now ${mbs(u.tools_bytes)}` : null,
      u.from_cache ? `${u.from_cache} of ${u.from_cache + (u.compiled || 0)} objects from the build cache` : null].filter(Boolean).join(', ') : facDur(r.duration);
    const file = (n) => `/admin/ci/file?run=${encodeURIComponent(r.run)}&name=${encodeURIComponent(n)}`;
    return el('div', { className: 'admin-item' },
      el('span', { className: `state state-${r.state === 'passed' ? 'running' : 'stopped'}`, textContent: r.state === 'passed' ? (r.tools_only ? 'Tools fetched' : r.offline ? 'Built offline' : 'Built') : r.state }),
      el('span', { textContent: ` ${describe(r)} (${String(r.commit || '').slice(0, 7)})` }),
      el('span', { className: 'setting-desc', textContent: ` ${r.finished ? ago(Date.now() / 1000 - r.finished) : ''}; ${used}.` }),
      el('span', { className: 'factory-files' }, ...(r.artifacts || []).map((n) => el('a', { href: file(n), textContent: n, download: n })),
        el('a', { href: file('log.txt'), textContent: 'log', target: '_blank', rel: 'noopener' })),
      r.state === 'passed' && (r.artifacts || []).some((n) => n.endsWith('.mt.json'))
        ? el('span', { className: 'library-buttons' }, actionButton(published.has(r.run) ? 'Publish again' : 'Publish to the web flasher',
          () => facAct({ action: 'publish', run: r.run.split('/')[1] }),
          { title: 'Offer this build in the web flasher, as a release built on this box' })) : null);
  })));
  if (!fac.runs.children.length) fac.runs.replaceChildren(el('p', { className: 'setting-desc', textContent: 'Nothing built here yet.' }));
  const pub = (d.flasher || []).flatMap((rel) => rel.targets.map((t) => el('div', { className: 'admin-item' },
    el('span', { textContent: `${t.board}, ${t.platform}: ${rel.version.replace(/-built$/, '')}` }),
    el('span', { className: 'setting-desc', textContent: ` from ${t.ref || '?'}${t.epoch ? `, built ${new Date(t.epoch * 1000).toLocaleDateString()}` : ''}.` }),
    el('span', { className: 'library-buttons' }, actionButton('Remove', () => facAct({ action: 'unpublish', version: rel.version, env: t.board }))))));
  fac.flasher.replaceChildren(...(pub.length ? pub : [el('p', { className: 'setting-desc', textContent: 'Nothing published yet.' })]));
  clearTimeout(facPoll);
  if (paneShown('factory')) facPoll = setTimeout(loadFactory, d.running || d.waiting.length ? 15000 : 60000);
}

async function loadFactoryRefs() {
  try {
    const d = await getJSON(`/admin/factory/targets?source=${encodeURIComponent(fac.source.value)}`);
    const group = (label, list) => (list.length ? el('optgroup', { label }, ...list.map((r) => el('option', { value: r.ref, textContent: r.ref }))) : null);
    fac.ref.replaceChildren(...[group('Releases and tags', d.refs.tags), group('Branches', d.refs.branches)].filter(Boolean));
    loadFactoryTargets();
  } catch (err) { say(err.message, false, fac.note); }
}

async function loadFactoryTargets() {
  if (!fac.ref.value) { fac.targets.replaceChildren(); return; }
  fac.targets.replaceChildren(el('p', { className: 'setting-desc', textContent: 'Reading its platformio.ini files…' }));
  try {
    if (facFlasher === null) {
      try { const fw = await getJSON('/admin/firmware'); facFlasher = Array.isArray(fw.settings && fw.settings.boards) ? fw.settings.boards : []; } catch (_) { facFlasher = []; }
    }
    facTargets = await getJSON(`/admin/factory/targets?source=${encodeURIComponent(fac.source.value)}&ref=${encodeURIComponent(fac.ref.value)}`);
    facChosen.clear();
    facTargets.targets.forEach((t) => { if (facFlasher.includes(t.env)) facChosen.add(t.env); });
    renderFactoryTargets();
  } catch (err) { fac.targets.replaceChildren(el('p', { className: 'setting-desc bad', textContent: err.message })); }
}

// Each family's test target for Fetch tools and Test offline: a board people use (the hub suggests
// one: the flasher's, a common board, a best-supported one), not the project's CI target; the
// owner may pick another, kept while the page is open.
const facTest = {};
function facTestSelect(f) {
  const all = facTargets.targets.filter((t) => t.family === f);
  if (!all.some((t) => t.env === facTest[f])) facTest[f] = (facTargets.suggested || {})[f] || all[0].env;
  const sel = el('select', { onchange: (e) => { facTest[f] = e.target.value; } },
    ...all.map((t) => el('option', { value: t.env, textContent: t.name !== t.env ? `${t.name} (${t.env})` : t.env })));
  sel.value = facTest[f];
  return sel;
}

function renderFactoryTargets() {
  if (!facTargets) return;
  const q = fac.search.value.trim().toLowerCase();
  const fams = {};
  facTargets.targets.forEach((t) => {
    if (!q || t.env.toLowerCase().includes(q) || t.name.toLowerCase().includes(q)) (fams[t.family] = fams[t.family] || []).push(t);
  });
  fac.targets.replaceChildren(...Object.keys(fams).sort().map((f) => {
    const list = fams[f];
    const picked = list.filter((t) => facChosen.has(t.env)).length;
    const fold = el('details', { className: 'factory-family', open: !!q || picked > 0 },
      el('summary', {}, el('strong', { textContent: f }),
        el('span', { className: 'setting-desc', textContent: ` ${list.length} target${list.length === 1 ? '' : 's'}${picked ? `, ${picked} chosen` : ''}; ${facReady(f)}; ${facEst(f)}.` })),
      el('p', { className: 'library-buttons' }, el('label', { className: 'inline factory-test-target' }, 'Test target ', facTestSelect(f)),
      actionButton('Fetch tools', async () => {
        try {
          const r = await postJSON('/admin/factory', { action: 'tools', source: fac.source.value, ref: fac.ref.value, family: f, env: facTest[f] });
          say(`Fetching ${f}'s tools (by ${r.env}): queued. Nothing is compiled; once fetched, its builds need no internet.`, true, fac.note);
          renderFactory(r.snapshot);
        } catch (err) { say(err.message, false, fac.note); }
      }, { title: 'Install what this family needs (toolchain, framework, libraries) without building: a later build needs no internet' }),
      actionButton('Test offline', async () => {
        try {
          const r = await postJSON('/admin/factory', { action: 'offline', source: fac.source.value, ref: fac.ref.value, family: f, env: facTest[f] });
          say(`Building ${r.env} with no network at all: queued. If it passes, ${f} is offline ready.`, true, fac.note);
          renderFactory(r.snapshot);
        } catch (err) { say(err.message, false, fac.note); }
      }, { title: 'Build one target of this family with the network cut off: proves the box can build it offline' })),
      el('div', { className: 'factory-boards' }, ...list.map((t) => el('label', { title: [t.file, t.level ? `board_level ${t.level}` : '', t.support ? `support level ${t.support}` : ''].filter(Boolean).join(', ') },
        el('input', { type: 'checkbox', value: t.env, checked: facChosen.has(t.env),
          onchange: (e) => { if (e.target.checked) facChosen.add(t.env); else facChosen.delete(t.env); factoryChosen(); } }),
        el('span', { textContent: t.name !== t.env ? ` ${t.name} (${t.env})` : ` ${t.env}` })))));
    return fold;
  }));
  factoryChosen();
}

function factoryChosen() {
  const n = facChosen.size;
  const fams = new Set(facTargets.targets.filter((t) => facChosen.has(t.env)).map((t) => t.family));
  const secs = [...facChosen].reduce((a, env) => { const t = facTargets.targets.find((x) => x.env === env); const e = facData && facData.estimates[t.family]; return e && a !== null ? a + e.seconds : null; }, 0);
  const max = facData ? facData.max_per_request : 50;
  fac.chosen.textContent = n ? `${n} chosen, in ${fams.size} famil${fams.size === 1 ? 'y' : 'ies'}${secs !== null ? `; about ${facDur(secs)} in all` : ''}${n > max ? `; at most ${max} at a time` : ''}.` : 'None chosen.';
  fac.queue.disabled = !n || n > max;
  fac.queue.textContent = n ? `Queue ${n}` : 'Queue';
}

async function facAct(body) {
  try { renderFactory(await postJSON('/admin/factory', body)); } catch (err) { say(err.message, false, fac.note); }
}

fac.source.addEventListener('change', loadFactoryRefs);
fac.ref.addEventListener('change', loadFactoryTargets);
fac.search.addEventListener('input', renderFactoryTargets);
fac.pause.addEventListener('click', () => facAct({ action: facData && facData.paused ? 'resume' : 'pause' }));
fac.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const r = await postJSON('/admin/factory', { action: 'queue', source: fac.source.value, ref: fac.ref.value, targets: [...facChosen] });
    say(`Queued ${r.queued} target${r.queued === 1 ? '' : 's'} at ${r.commit.slice(0, 7)}.`, true, fac.note);
    facChosen.clear();
    renderFactory(r.snapshot);
    renderFactoryTargets();
  } catch (err) { say(err.message, false, fac.note); }
});
window.addEventListener('hashchange', () => { if (paneShown('factory')) loadFactory(); });
if (paneShown('factory')) loadFactory();

// --- HTTPS (step 15: root/tls.py, /admin/tls) -------------------------------------------------------
const tlsEl = { state: document.getElementById('tls-state'), make: document.getElementById('tls-make'), sw: document.getElementById('tls-switch'),
  again: document.getElementById('tls-again'), note: noteEl('tls-note'), box: document.getElementById('tls-box'),
  own: document.getElementById('tls-own-form'), plain: document.getElementById('tls-own-plain'),
  adminOnly: document.getElementById('tls-admin-only'), adminOnlyNote: document.getElementById('tls-admin-only-note') };
let tlsWaiting = null;
let tlsData = null;
async function loadTls() {
  try { renderTls(await getJSON('/admin/tls')); } catch (_) { tlsEl.state.textContent = 'Could not read the HTTPS state.'; }
}
function renderTls(d) {
  tlsData = d;
  if (tlsWaiting) {
    const done = (d.results || []).find((r) => r.id === tlsWaiting);
    if (done) { say(done.message, done.ok, tlsEl.note); tlsWaiting = null; }
  }
  const busy = !!tlsWaiting || d.pending > 0;
  const c = d.cert || {};
  const day = (t) => new Date(t * 1000).toLocaleDateString();
  tlsEl.state.textContent = !d.set_up ? 'No certificate yet: pages are served over plain HTTP only.'
    : `${d.on ? `On: https://<this box>/ (port ${d.ports.main}) and each app's twin.` : 'Off: the certificate is kept, pages are plain HTTP only.'}`
      + ` The certificate covers ${[...(c.names || []), ...(c.addresses || [])].join(', ')}, until ${c.expires ? day(c.expires) : '?'}`
      + `${c.own ? ' (your own: renew it before then)' : ', renewed by the box before then'}.${d.ca ? ` The CA's fingerprint: ${d.ca.fingerprint}.` : ''}`
      + (d.outside && d.outside.length ? ` The box is now at ${d.outside.join(', ')}, outside what its CA may vouch for: make a new CA for that network.` : '');
  tlsEl.state.classList.toggle('bad', !!(d.outside && d.outside.length));
  tlsEl.make.hidden = !!d.set_up;
  tlsEl.sw.hidden = !d.set_up;
  tlsEl.sw.textContent = d.on ? 'Switch HTTPS off' : 'Switch HTTPS on';
  tlsEl.again.hidden = !d.set_up;
  tlsEl.box.hidden = !(c.own && d.ca);
  tlsEl.make.hidden = !!(d.set_up && d.ca);
  tlsEl.plain.hidden = location.protocol === 'https:';
  // /admin over HTTPS only: offered while HTTPS is on, and turned on only from a page that came
  // over HTTPS (this device trusts the box, so it can't lock its owner out).
  const secure = location.protocol === 'https:';
  tlsEl.adminOnly.hidden = !d.on;
  tlsEl.adminOnly.textContent = d.admin_only ? 'Let /admin answer on plain HTTP too' : 'Make /admin HTTPS only';
  tlsEl.adminOnlyNote.hidden = !d.on;
  tlsEl.adminOnlyNote.textContent = d.admin_only ? 'On: /admin answers only over HTTPS, so the password never crosses the network in clear.'
    : secure ? 'Off: /admin also answers on plain HTTP.' : 'To make /admin HTTPS only, open it over HTTPS first (once this device trusts the box): then it is safe.';
  [tlsEl.make, tlsEl.sw, tlsEl.again, tlsEl.box, tlsEl.adminOnly].forEach((b) => { b.disabled = busy; });
  if (!d.admin_only && !secure) tlsEl.adminOnly.disabled = true;
  if (busy) setTimeout(loadTls, 1500);
}
async function tlsAct(body) {
  try {
    const r = await postJSON('/admin/tls', body);
    tlsWaiting = r.id;
    say('Working…', true, tlsEl.note);
    loadTls();
  } catch (err) { say(err.message, false, tlsEl.note); }
}
tlsEl.make.addEventListener('click', () => tlsAct({ action: 'make' }));
tlsEl.box.addEventListener('click', () => tlsAct({ action: 'box' }));
tlsEl.adminOnly.addEventListener('click', () => tlsAct({ action: 'admin-only', on: !(tlsData && tlsData.admin_only) }));
tlsEl.own.addEventListener('submit', (e) => {
  e.preventDefault();
  const f = tlsEl.own.elements;
  tlsAct({ action: 'import', chain: f.chain.value, key: f.key.value });
  f.key.value = '';  // not left in the page
});
tlsEl.sw.addEventListener('click', () => tlsAct({ action: tlsData && tlsData.on ? 'off' : 'on' }));
tlsEl.again.addEventListener('click', () => {
  if (confirm('Make a new certificate authority? Every phone and computer that installed the old one must install the new one, or it will warn about the box.')) {
    tlsAct({ action: 'make', again: true });
  }
});
window.addEventListener('hashchange', () => { if (paneShown('security')) loadTls(); });
if (paneShown('security')) loadTls();

// --- accounts (step 16: accounts.py, /admin/accounts) ------------------------------------------------
const acctEl = { settings: document.getElementById('accounts-settings'), note: noteEl('accounts-note'), counts: document.getElementById('accounts-counts'),
  make: document.getElementById('accounts-make'), code: document.getElementById('accounts-code'), list: document.getElementById('accounts-list'),
  login: document.getElementById('accounts-admin-login'), loginSwitch: document.getElementById('accounts-admin-login-switch') };
let acctWaiting = null; // the root helper's request for the box's own login
async function loadAccounts() {
  try { renderAccounts(await getJSON('/admin/accounts')); } catch (_) { acctEl.counts.textContent = 'Could not read the accounts.'; }
}
function renderAccounts(d) {
  const al = d.admin_login || { on: true, https_admins: [], results: [] };
  if (acctWaiting) {
    const done = (al.results || []).find((r) => r.id === acctWaiting);
    if (done) { acctWaiting = null; say(done.message, done.ok, acctEl.note); }
    else setTimeout(loadAccounts, 1000);
  }
  const admins = al.https_admins.join(', ');
  acctEl.login.textContent = al.on
    ? `On: "admin" and its password open /admin${admins ? `, as do the admin accounts (${admins})` : ''}. ${admins ? 'It can be switched off: the admin accounts then are the way in.' : 'To switch it off, first make an admin account and log in with it over HTTPS.'}`
    : `Off: only admin accounts (${admins || 'none!'}) open /admin. A new admin password, or reset-password at the box's console, turns it on again.`;
  acctEl.loginSwitch.hidden = al.on && !admins;
  acctEl.loginSwitch.disabled = !!acctWaiting;
  acctEl.loginSwitch.textContent = acctWaiting ? 'Changing…' : al.on ? 'Switch it off' : 'Switch it on';
  acctEl.loginSwitch.onclick = async () => {
    if (al.on && !confirm(`Switch the box's own admin login off? Only ${admins} can then open /admin.`)) return;
    try { acctWaiting = (await postJSON('/admin/accounts', { action: 'admin-login', on: !al.on })).id; loadAccounts(); }
    catch (err) { say(err.message, false, acctEl.note); }
  };
  acctEl.settings.elements.signup.value = d.settings.signup;
  acctEl.settings.elements.http.value = d.settings.http;
  const c = d.counts;
  acctEl.counts.textContent = `${c.user} user${c.user === 1 ? '' : 's'} (${c.admins} admin${c.admins === 1 ? '' : 's'}), ${c.asked} asking, ${c.disabled} switched off.`;
  const when = (t) => (t ? ago(Date.now() / 1000 - t) : 'never');
  acctEl.list.replaceChildren(...(d.accounts.length ? d.accounts.map((a) => {
    const act = (action, label) => actionButton(label, () => acctAct({ action, name: a.name }));
    const state = a.state === 'asked' ? 'Asking' : a.state === 'disabled' ? 'Off' : a.role === 'admin' ? 'Admin' : 'User';
    return el('div', { className: 'admin-item' },
      el('span', { className: `state state-${a.state === 'user' ? 'running' : 'stopped'}`, textContent: state }),
      el('span', { className: 'setting-name', textContent: ` ${a.name}` }),
      el('span', { className: 'setting-desc', textContent: ` ${a.by}, ${when(a.created)}; last seen ${when(a.seen)}${a.password_set ? '' : '; no password yet'}; ${a.shown_online ? 'shown by name online' : 'not shown by name'} (their own choice).` }),
      el('span', { className: 'library-buttons' },
        a.state === 'asked' ? act('accept', 'Accept') : null,
        a.state === 'user' ? act('disable', 'Switch off') : a.state === 'disabled' ? act('enable', 'Switch on') : null,
        a.state === 'user' ? act(a.role === 'admin' ? 'user' : 'admin', a.role === 'admin' ? 'Make a user' : 'Make an admin') : null,
        a.state !== 'asked' ? act('reset', 'Reset password') : null,
        act('delete', a.state === 'asked' ? 'Refuse' : 'Delete')));
  }) : [el('p', { className: 'setting-desc', textContent: 'None yet.' })]));
}
function showCode(name, code) {
  acctEl.code.hidden = false;
  acctEl.code.replaceChildren(el('span', { textContent: `${name}'s one-time code: ` }), el('code', { textContent: code }),
    el('span', { className: 'setting-desc', textContent: ' Give it to them now: it is not shown again.' }));
}
async function acctAct(body) {
  if (body.action === 'delete' && !confirm(`Delete ${body.name}'s account?`)) return;
  try {
    const d = await postJSON('/admin/accounts', body);
    if (d.code) showCode(body.name, d.code);
    say('Done.', true, acctEl.note);
    renderAccounts(d);
  } catch (err) { say(err.message, false, acctEl.note); }
}
acctEl.settings.addEventListener('submit', (e) => {
  e.preventDefault();
  acctAct({ action: 'settings', signup: acctEl.settings.elements.signup.value, http: acctEl.settings.elements.http.value });
});
acctEl.make.addEventListener('submit', (e) => {
  e.preventDefault();
  const f = acctEl.make.elements;
  acctAct({ action: 'make', name: f.name.value.trim(), role: f.role.value });
  f.name.value = '';
});
// Who may post on the shoutbox and the forum, and the users' marks (the hub's settings): a form on
// each app's own page (menu overhaul M9).
const postingForms = [...document.querySelectorAll('.posting-form')];
const fillPosting = (st) => postingForms.forEach((form) => [...form.elements].forEach((f) => {
  if (!(f.name in st)) return;
  if (f.type === 'checkbox') f.checked = st[f.name] === true; else f.value = st[f.name];
}));
async function loadPosting() {
  try { fillPosting(await getJSON('/admin/settings')); } catch (_) { /* the page says if the hub can't be read */ }
}
postingForms.forEach((form) => form.addEventListener('change', async (e) => {
  const f = e.target, note = noteEl(form.dataset.note);
  try {
    fillPosting(await postJSON('/admin/settings', { [f.name]: f.type === 'checkbox' ? f.checked : f.value }));
    say('Saved.', true, note);
  } catch (err) { say(err.message, false, note); loadPosting(); }
}));
const postingShown = () => paneShown('shoutbox-settings') || paneShown('board-settings');
window.addEventListener('hashchange', () => { if (postingShown()) loadPosting(); });
if (postingShown()) loadPosting();
window.addEventListener('hashchange', () => { if (paneShown('accounts')) loadAccounts(); });
if (paneShown('accounts')) loadAccounts();

// --- the mesh (step 18: meshbridge.py, /admin/mesh) -------------------------------------------------
const meshEl = { state: document.getElementById('mesh-admin-state'), channels: document.getElementById('mesh-channels'),
  form: document.getElementById('mesh-channel-form'), longfast: document.getElementById('mesh-longfast'), note: noteEl('mesh-note'),
  counts: document.getElementById('mesh-admin-counts'), packets: document.getElementById('mesh-admin-packets') };
let meshPoll = null;
async function loadMesh() {
  try { renderMesh(await getJSON('/admin/mesh')); } catch (_) { meshEl.state.textContent = 'Could not read the mesh.'; }
}
function renderMesh(d) {
  meshEl.state.textContent = d.state === 'listening' ? `Listening to the broker: ${d.nodes.length} node${d.nodes.length === 1 ? '' : 's'} heard in the last 7 days.`
    : `Not connected to the broker${d.error ? ` (${d.error})` : ''}: is it installed (Add-ons, MQTT broker) and running? It tries again by itself.`;
  meshEl.channels.replaceChildren(...(d.channels.length ? d.channels.map((c) => el('div', { className: 'admin-item' },
    el('span', { className: 'setting-name', textContent: c.name }),
    el('span', { className: 'setting-desc', textContent: c.public_key ? ' Meshtastic\'s public default key: anyone can read this channel.' : c.no_key ? ' No key: unencrypted.' : ' Its own key (not shown).' }),
    el('span', { className: 'library-buttons' }, actionButton('Remove', () => meshAct({ action: 'remove-channel', name: c.name })))))
    : [el('p', { className: 'setting-desc', textContent: 'None yet: every packet stays encrypted. Add a channel and its key, as your Meshtastic app shows them.' })]));
  const names = Object.fromEntries(d.nodes.map((n) => [n.id, n.long_name || n.id]));
  const counts = Object.entries(d.counts || {}).map(([k, n]) => `${n} ${k}`);
  meshEl.counts.textContent = counts.length ? `Since the hub started: ${counts.join(', ')}.` : 'Nothing heard yet.';
  meshEl.packets.replaceChildren(...d.packets.map((p) => el('li', {},
    el('span', { textContent: `${new Date(p.at * 1000).toLocaleTimeString()}: ${p.port} from ${names[p.from] || p.from}${p.channel ? ` on ${p.channel}` : ''}` }),
    p.text != null ? el('span', { className: 'mesh-text', textContent: ` “${p.text}”` }) : null)));
  clearTimeout(meshPoll);
  if (paneShown('mesh')) meshPoll = setTimeout(loadMesh, 15000);
}
async function meshAct(body) {
  try { await postJSON('/admin/mesh', body); say(body.action === 'add-channel' ? `Added ${body.name}.` : `Removed ${body.name}.`, true, meshEl.note); loadMesh(); }
  catch (err) { say(err.message, false, meshEl.note); }
}
meshEl.form.addEventListener('submit', (e) => {
  e.preventDefault();
  const f = meshEl.form.elements;
  meshAct({ action: 'add-channel', name: f.name.value.trim(), key: f.key.value.trim() });
  f.key.value = '';
});
meshEl.longfast.addEventListener('click', () => meshAct({ action: 'add-channel', name: 'LongFast', key: 'AQ==' }));
window.addEventListener('hashchange', () => { if (paneShown('mesh')) loadMesh(); });
if (paneShown('mesh')) loadMesh();

// ---- Appearance (menu overhaul M2): the page widths, for everyone, read by every page from
// /layout.css. A choice shows on this page at once; Save keeps it.
const WIDTHS = [45, 60, 80, 90, 100];
let appearanceSettings = null;
async function loadAppearance() {
  const box = document.getElementById('appearance-box');
  if (!box) return;
  let st;
  try { st = await getJSON('/admin/settings'); } catch (e) { return; }
  appearanceSettings = st;
  fillWidthBlocks();
  const row = (key, label, note) => ({ key, label, kind: 'choice', value: st[key], note, options: WIDTHS.map((w) => [w, w + 'rem']) });
  box.replaceChildren(AW.settings([
    row('page_width', 'Hub and admin', 'the tiles, the menu and its pages'),
    row('shout_width', 'Shoutbox', 'its tab on the hub page'),
    row('board_width', 'Forum', 'its tab on the hub page'),
  ], { save: async (changed) => {
    const now = await postJSON('/admin/settings', changed);
    document.documentElement.style.setProperty('--page-width', now.page_width + 'rem');
    return now;
  } }));
}
loadAppearance();

// ---- Folders (menu overhaul M7; checklist 5e): each folder's page arranges its entries. The
// owner may hide an entry from a folder, order a folder's entries, and put an entry in other
// folders as well. The draft is the whole arrangement; Save sends it, Discard puts it back.
let folders = null, folderDraft = null;
const folderOpen = {};  // folder -> the entry left open, so a change keeps it in view
const KIND_WORD = { switches: 'starting switches', download: 'a download', page: 'a page' };
async function loadFolders() {
  try { folders = await getJSON('/admin/folders'); } catch (e) { return; }
  folderDraft = JSON.parse(JSON.stringify(folders.state || {}));
  fillFolderBlocks();
}
function fillFolderBlocks() {
  if (!folders) return;
  document.querySelectorAll('.folder-block[data-folder]').forEach((b) => drawFolder(b, b.dataset.folder));
}
AL.onBuild(fillFolderBlocks);
// The arrangement without its empty parts, sorted by folder: what changed, and nothing else.
const folderNorm = (s) => Object.fromEntries(Object.entries(s || {}).sort().map(([f, v]) => [f, Object.fromEntries(['hidden', 'order', 'extra'].filter((k) => (v[k] || []).length).map((k) => [k, v[k]]))]).filter(([, v]) => Object.keys(v).length));
const fpart = (f) => { folderDraft[f] = folderDraft[f] || { hidden: [], order: [], extra: [] }; return folderDraft[f]; };
// Is an entry in a folder: one of the folder's own and not hidden there, or put in from another.
function inFolder(f, href) {
  const own = (folders.folders.find((x) => x.id === f) || { entries: [] }).entries.some((e) => e.href === href && !e.extra);
  const st = folderDraft[f] || {};
  return own ? !(st.hidden || []).includes(href) : (st.extra || []).includes(href);
}
function setInFolder(f, href, yes) {
  const own = (folders.folders.find((x) => x.id === f) || { entries: [] }).entries.some((e) => e.href === href && !e.extra);
  const st = fpart(f);
  const without = (list) => list.filter((h) => h !== href);
  if (own) st.hidden = yes ? without(st.hidden) : [...without(st.hidden), href];
  else st.extra = yes ? [...without(st.extra), href] : without(st.extra);
}
function drawFolder(block, f) {
  const fol = folders.folders.find((x) => x.id === f);
  if (!fol) { block.replaceChildren(); return; }
  const st = fpart(f);
  const pos = (h) => { const i = st.order.indexOf(h); return i < 0 ? 1e6 : i; };
  // The folder's entries, plus any put in since the last save, in the draft's order.
  const pool = new Map(folders.folders.flatMap((x) => x.entries).map((e) => [e.href, e]));
  const hrefs = [...new Set([...fol.entries.map((e) => e.href), ...st.extra])].filter((h) => pool.has(h));
  const list = hrefs.map((h, i) => ({ h, i })).sort((a, b) => (pos(a.h) - pos(b.h)) || (a.i - b.i)).map((x) => pool.get(x.h));
  const dirty = JSON.stringify(folderNorm(folderDraft)) !== JSON.stringify(folderNorm(folders.state));
  const move = (e, d) => {
    const order = list.map((x) => x.href), i = order.indexOf(e.href), j = i + d;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    st.order = order;
    folderOpen[f] = e.href; fillFolderBlocks();
  };
  const items = list.map((e) => {
    const shown = inFolder(f, e.href);
    return { id: e.href, title: e.name, summary: e.desc || e.href, badges: [shown ? 'shown' : 'hidden', KIND_WORD[e.kind] || e.kind].concat(e.from !== f ? ['from ' + e.from] : []),
      detail: () => [
        AW.h('div', { class: 'aw-field' }, AW.h('span', { class: 'aw-label', text: 'In this folder' }),
          AW.h('button', { type: 'button', class: 'chip-btn' + (shown ? ' active' : ''), 'aria-pressed': String(shown),
            onclick: () => { setInFolder(f, e.href, !shown); folderOpen[f] = e.href; fillFolderBlocks(); } }, shown ? 'Shown: On' : 'Shown: Off'),
          AW.btn('↑ Earlier', { onclick: () => move(e, -1) }), AW.btn('↓ Later', { onclick: () => move(e, 1) })),
        AW.h('div', { class: 'aw-field' }, AW.h('span', { class: 'aw-label', text: 'Also in' }),
          AW.h('div', { class: 'filter-chips' }, folders.folders.filter((x) => x.id !== f).map((x) => {
            const on = inFolder(x.id, e.href);
            return AW.h('button', { type: 'button', class: 'filter-chip' + (on ? ' active' : ''), 'aria-pressed': String(on),
              onclick: () => { setInFolder(x.id, e.href, !on); folderOpen[f] = e.href; fillFolderBlocks(); } }, x.title);
          }))),
        AW.dl({ Opens: e.href }),
      ] };
  });
  const shownCount = list.filter((e) => inFolder(f, e.href)).length;
  const note = AW.h('span', { class: 'note', text: dirty ? 'Changes not saved yet (they may touch other folders too).' : 'Settings wait for Save.' });
  block.replaceChildren(
    AW.h('p', { class: 'setting-desc', text: `${shownCount} of ${list.length} shown on its page. The entries come from the apps' manifests, from what an app's own index adds, and from starting switches of one page.` }),
    AW.shortList(items, { id: 'folder-list-' + f }),
    AW.h('div', { class: 'aw-foot' }, note,
      AW.btn('Discard', { disabled: !dirty, onclick: () => { folderDraft = JSON.parse(JSON.stringify(folders.state || {})); fillFolderBlocks(); } }),
      AW.btn('Save', { class: 'action-btn primary', disabled: !dirty, onclick: async () => {
        try { folders = await postJSON('/admin/folders', { state: folderDraft }); folderDraft = JSON.parse(JSON.stringify(folders.state)); fillFolderBlocks(); }
        catch (err) { note.textContent = err.message; }
      } })));
  const open = folderOpen[f] && [...block.querySelectorAll('.aw-row')].find((r) => r.dataset.awId === folderOpen[f]);
  if (open) AW.foldRow(open.querySelector('.aw-row-head'), true);
}
loadFolders();

// An app drawn on the hub page has its width on its own page too (M9; Tom, 2026-10-08: "in both
// the apps page and the appearance page"): one row of the same setting.
const WIDTH_LABEL = { shout_width: 'Its width on the hub page', board_width: 'Its width on the hub page' };
function fillWidthBlocks() {
  if (!appearanceSettings) return;
  document.querySelectorAll('.width-block[data-key]').forEach((block) => {
    const key = block.dataset.key;
    block.replaceChildren(AW.settings([{ key, label: WIDTH_LABEL[key] || 'Width', kind: 'choice', value: appearanceSettings[key],
      note: 'also on System → Appearance', options: WIDTHS.map((w) => [w, w + 'rem']) }],
    { save: async (changed) => { appearanceSettings = await postJSON('/admin/settings', changed); loadAppearance(); return appearanceSettings; } }));
  });
}
AL.onBuild(fillWidthBlocks);

// ---- Status tiles (M8): the box row arranged, held until Save.
let stTiles = null, stDraft = null;
async function loadStatusTiles() {
  try { stTiles = await getJSON('/admin/status-tiles'); } catch (e) { return; }
  stDraft = JSON.parse(JSON.stringify(stTiles.state));
  drawStatusTiles();
}
function drawStatusTiles(openId) {
  const box = document.getElementById('status-tiles-box');
  if (!box || !stTiles) return;
  const byId = new Map(stTiles.tiles.map((x) => [x.id, x]));
  const pos = (i) => { const n = stDraft.order.indexOf(i); return n < 0 ? 1e6 : n; };
  const list = stTiles.tiles.map((x, i) => ({ x, i })).sort((a, b) => (pos(a.x.id) - pos(b.x.id)) || (a.i - b.i)).map((p) => p.x);
  const toggle = (k, id, on) => { stDraft[k] = on ? [...new Set([...stDraft[k], id])] : stDraft[k].filter((x) => x !== id); };
  const redraw = (id) => drawStatusTiles(id);
  const move = (id, d) => { const order = list.map((x) => x.id), i = order.indexOf(id), k = i + d; if (k < 0 || k >= order.length) return;
    [order[i], order[k]] = [order[k], order[i]]; stDraft.order = order; redraw(id); };
  const norm = (s) => JSON.stringify(['order', 'hidden', 'double'].map((k) => [...(s[k] || [])].sort()).concat([s.order || []]));
  const dirty = norm(stDraft) !== norm(stTiles.state);
  const items = list.map((t) => {
    const shown = !stDraft.hidden.includes(t.id), dbl = stDraft.double.includes(t.id);
    return { id: t.id, title: t.name, summary: dbl ? 'two cells' : 'one cell', badges: [shown ? 'shown' : 'hidden'], detail: () => [
      AW.h('div', { class: 'aw-field' }, AW.h('span', { class: 'aw-label', text: 'On the hub page' }),
        AW.h('button', { type: 'button', class: 'chip-btn' + (shown ? ' active' : ''), 'aria-pressed': String(shown), onclick: () => { toggle('hidden', t.id, shown); redraw(t.id); } }, shown ? 'Shown: On' : 'Shown: Off'),
        AW.btn('↑ Earlier', { onclick: () => move(t.id, -1) }), AW.btn('↓ Later', { onclick: () => move(t.id, 1) })),
      AW.h('div', { class: 'aw-field' }, AW.h('span', { class: 'aw-label', text: 'Size' }),
        AW.h('div', { class: 'chip-group', role: 'group' }, [['single', false], ['double', true]].map(([label, v]) => AW.h('button', { type: 'button',
          class: 'chip' + (dbl === v ? ' selected' : ''), 'aria-pressed': String(dbl === v), onclick: () => { toggle('double', t.id, v); redraw(t.id); } }, label))),
        t.id === 'box-people' ? AW.h('small', { class: 'setting-desc', text: 'double shows the different devices seen today and this week, if counting is on (Security)' }) : null),
    ] };
  });
  const note = AW.h('span', { class: 'note', text: dirty ? 'Changes not saved yet.' : 'Settings wait for Save.' });
  box.replaceChildren(AW.shortList(items, { id: 'status-tiles-list' }), AW.h('div', { class: 'aw-foot' }, note,
    AW.btn('Discard', { disabled: !dirty, onclick: () => { stDraft = JSON.parse(JSON.stringify(stTiles.state)); redraw(); } }),
    AW.btn('Save', { class: 'action-btn primary', disabled: !dirty, onclick: async () => {
      try { stTiles = await postJSON('/admin/status-tiles', { state: stDraft }); stDraft = JSON.parse(JSON.stringify(stTiles.state)); redraw(); }
      catch (err) { note.textContent = err.message; }
    } })));
  const open = openId && [...box.querySelectorAll('.aw-row')].find((r) => r.dataset.awId === openId);
  if (open) AW.foldRow(open.querySelector('.aw-row-head'), true);
}
loadStatusTiles();

// Who sees the names of those signed in (M12, a setup decision, M14): users or the admin only.
async function loadNamesTo() {
  const box = document.getElementById('names-to-box');
  if (!box) return;
  let st;
  try { st = await getJSON('/admin/settings'); } catch (e) { return; }
  box.replaceChildren(AW.settings([{ key: 'names_to', label: 'Shown to', kind: 'choice', value: st.names_to, decision: 'names',
    options: [['users', 'those logged in'], ['admin', 'the admin only']],
    note: 'only of those who chose to be shown, on their own account page; guests get how many' }],
  { save: (changed) => postJSON('/admin/settings', changed) }));
}
loadNamesTo();

// Saves tied to accounts (accounts-plan stage 6, item 6): off by default, so a device lock is
// the only thing that guards a save unless the admin turns this on.
async function loadSavesCrossDevice() {
  const box = document.getElementById('saves-cross-device-box');
  if (!box) return;
  let st;
  try { st = await getJSON('/admin/settings'); } catch (e) { return; }
  box.replaceChildren(AW.settings([{ key: 'saves_cross_device', label: 'A user may change or remove their own save from another device', kind: 'toggle',
    value: st.saves_cross_device,
    note: 'Off: only the device that made or locked a save may change it, as before. On: once logged in, a user may change or remove any save of their own account’s, on any device, past its lock. A guest’s saves are unaffected either way.' }],
  { save: (changed) => postJSON('/admin/settings', changed) }));
}
loadSavesCrossDevice();

// --- Needs attention (menu overhaul F4; the mock's ov-attention) --------------------------------
// One list at the top of Overview of what is waiting on the admin, each a link to where it is dealt
// with: the words the sections already put beside their sidebar entries, and the setup decisions
// not yet made. Work in progress ("working") isn't waiting on anyone, so it is left out.
const ATTENTION = {
  health: (w) => (w === '!' ? 'The root helper is stuck: nothing the box is asked to do gets done' : `${w} thing${w === '1' ? '' : 's'} wrong with the box`),
  secdoctor: (w) => `${w} security finding${w === '1' ? '' : 's'} to fix`,
  security: (w) => `${w} security choice${w === '1' ? '' : 's'} to make or leave`,
  updoctor: (w) => (w === '!' ? 'An update failed its checks' : `${w} problem${w === '1' ? '' : 's'} with updates`),
  updates: (w) => ({ ready: 'An update is fetched, checked and ready to install', new: 'An update is available' }[w] || null),
  clock: (w) => `${w} clock problem${w === '1' ? '' : 's'}`,
  network: () => 'A network link is down',
  git: (w) => `${w} build${w === '1' ? '' : 's'} failed since you last looked`,
  moderation: (w) => `${w} reported item${w === '1' ? '' : 's'} waiting for a decision`,
};
function drawAttention() {
  const box = document.getElementById('attention');
  if (!box) return;
  const items = Object.entries(AL.badges()).filter(([, w]) => w && w !== 'working').map(([id, w]) => {
    const say = ATTENTION[id] ? ATTENTION[id](w) : `${AL.titleOf(id)}: ${w}`;
    return say && { id, text: say, where: AL.titleOf(id) };
  }).filter(Boolean);
  const T = window.TOUR;
  const left = T ? T.DECISIONS.length - T.decided().size : 0;
  if (left > 0) items.push({ id: 'welcome', text: `${left} setup decision${left === 1 ? '' : 's'} not made yet: the box runs on the defaults until then`, where: 'Setup steps' });
  box.replaceChildren(el('h3', { textContent: 'Needs attention' }), items.length
    ? el('ul', { className: 'admin-checks attention-list' }, ...items.map((i) => el('li', { className: 'check' },
      el('a', { href: '#' + i.id, textContent: i.text }), el('span', { className: 'setting-desc', textContent: ` — ${i.where}` }))))
    : el('p', { className: 'setting-desc', textContent: 'Nothing is waiting on you.' }));
}
AL.onBadge(drawAttention);
AL.onBuild(drawAttention);
drawAttention();
