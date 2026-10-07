// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// In a scope of its own: the landing page loads shoutbox.js and board.js together.
(() => {
const POLL_MS = 8000;

const listView = document.getElementById('list-view');
const threadView = document.getElementById('thread-view');
const threadsEl = document.getElementById('threads');
const postsEl = document.getElementById('posts');
const titleEl = document.getElementById('thread-title');
const decayNote = document.getElementById('board-decay-note');

const threadForm = document.getElementById('thread-form');
const replyForm = document.getElementById('reply-form');

// Who may post, as the box's admin set it (accounts step 16): the forms say so, and a logged-in
// user posts under their account's name, marked ✓ where the admin shows marks.
let marks = false;
const notes = [threadForm, replyForm].map((f) => {
  const n = document.createElement('p');
  n.className = 'setting-desc board-post-note';
  f.after(n);
  return n;
});
function applyPosting(p) {
  if (!p) return;
  marks = p.marks;
  const closed = p.who === 'off' || (p.who === 'users' && !p.me);
  [threadForm, replyForm].forEach((f, i) => {
    f.hidden = closed;
    notes[i].replaceChildren();
    if (p.who === 'off') notes[i].textContent = 'The forum is closed to new posts.';
    else if (closed) {
      const a = document.createElement('a'); a.href = `/account.html?next=${encodeURIComponent(location.pathname)}`; a.textContent = 'Log in';
      notes[i].append(a, ' to post here: the box\'s admin keeps the forum for its users.');
    }
  });
  for (const id of ['t-name', 'r-name']) {
    const el = document.getElementById(id);
    el.readOnly = !!p.me;
    if (p.me) el.value = p.me;
  }
}
const mark = (x) => (x.account && marks ? '<span class="verified" title="Posted by the box\'s account of that name">✓</span>' : '');
async function posted(r, i) {
  if (r.ok) { notes[i].textContent = ''; return true; }
  notes[i].textContent = (await r.json().catch(() => ({}))).error || `Not posted (HTTP ${r.status}).`;
  return false;
}

// Which thread we are looking at, or null for the list. Kept in the hash so a thread
// is linkable and survives a refresh.
function currentId() {
  const m = location.hash.match(/^#t(\d+)$/);
  return m ? Number(m[1]) : null;
}

function setDecayNote(ttl) {
  decayNote.textContent =
    `Threads fade ${formatAge(ttl)} after their last reply — measured in hub uptime, ` +
    `not calendar time. The hub has no clock; while it is switched off, nothing ages.`;
}

function renderThreads(data) {
  setDecayNote(data.ttl);
  applyPosting(data.posting);
  if (!data.threads.length) {
    threadsEl.innerHTML = '<span class="empty">No threads yet. Start one.</span>';
    return;
  }
  threadsEl.innerHTML = data.threads.map((t) => `
    <a class="thread-row" href="#t${t.id}" style="--author-hue:${hueOf(t, 'author')}">
      <span class="t-title">${esc(t.title)}</span>
      <span class="t-excerpt">${mdInline(t.excerpt)}</span>
      <span class="t-meta">
        <span class="author">${esc(t.author)}</span>${mark(t)} &middot; ${t.replies} ${t.replies === 1 ? 'reply' : 'replies'}
        &middot; active ${formatAge(data.now - t.active)} ago
      </span>
    </a>`).join('');
}

function renderThread(data) {
  setDecayNote(data.ttl);
  applyPosting(data.posting);
  const t = data.thread;
  titleEl.textContent = t.title;
  postsEl.innerHTML = t.posts.map((p, i) => `
    <div class="post${i === 0 ? ' op' : ''}" style="--author-hue:${hueOf(p, 'author')}">
      <span class="author">${esc(p.author)}</span>${mark(p)}
      <span class="time">${formatAge(data.now - p.created)} ago</span>
      <div class="text">${mdInline(p.text)}</div>
    </div>`).join('');
}

async function refresh() {
  const id = currentId();
  listView.hidden = id !== null;
  threadView.hidden = id === null;

  try {
    if (id === null) {
      const r = await fetch('/board/threads');
      if (r.ok) renderThreads(await r.json());
    } else {
      const r = await fetch(`/board/thread/${id}`);
      if (r.ok) {
        renderThread(await r.json());
      } else {
        // Decayed out from under us while we were reading it.
        location.hash = '#board';
      }
    }
  } catch (_) {}
}

threadForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = withHue({
    name: document.getElementById('t-name').value.trim(),
    title: document.getElementById('t-title').value.trim(),
    text: document.getElementById('t-text').value.trim(),
  });
  if (!body.name || !body.title || !body.text) return;
  try {
    const r = await fetch('/board/threads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (await posted(r, 0)) {
      const { thread } = await r.json();
      document.getElementById('t-title').value = '';
      document.getElementById('t-text').value = '';
      document.getElementById('new-thread').open = false;
      location.hash = `#t${thread.id}`;
    }
  } catch (_) {}
});

replyForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = currentId();
  const name = document.getElementById('r-name').value.trim();
  const text = document.getElementById('r-text').value.trim();
  if (id === null || !name || !text) return;
  try {
    const r = await fetch(`/board/thread/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withHue({ name, text })),
    });
    if (!(await posted(r, 1))) return;
    document.getElementById('r-text').value = '';
    await refresh();
  } catch (_) {}
});

// Persist the name across both forms and refreshes, same key as the shoutbox.
for (const [el, swatch] of [['t-name', 't-hue'], ['r-name', 'r-hue']].map(
       ([a, b]) => [document.getElementById(a), document.getElementById(b)])) {
  el.value = localStorage.getItem('shout-name') || '';
  el.addEventListener('input', () => localStorage.setItem('shout-name', el.value));
  attachHuePicker(swatch, el);
}

window.addEventListener('hashchange', refresh);
refresh();
setInterval(refresh, POLL_MS);
})();
