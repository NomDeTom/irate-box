const POLL_MS = 8000;

const listView = document.getElementById('list-view');
const threadView = document.getElementById('thread-view');
const threadsEl = document.getElementById('threads');
const postsEl = document.getElementById('posts');
const titleEl = document.getElementById('thread-title');
const decayNote = document.getElementById('decay-note');

const threadForm = document.getElementById('thread-form');
const replyForm = document.getElementById('reply-form');

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
  if (!data.threads.length) {
    threadsEl.innerHTML = '<span class="empty">No threads yet. Start one.</span>';
    return;
  }
  threadsEl.innerHTML = data.threads.map((t) => `
    <a class="thread-row" href="#t${t.id}">
      <span class="t-title">${esc(t.title)}</span>
      <span class="t-excerpt">${esc(t.excerpt)}</span>
      <span class="t-meta">
        ${esc(t.author)} &middot; ${t.replies} ${t.replies === 1 ? 'reply' : 'replies'}
        &middot; active ${formatAge(data.now - t.active)} ago
      </span>
    </a>`).join('');
}

function renderThread(data) {
  setDecayNote(data.ttl);
  const t = data.thread;
  titleEl.textContent = t.title;
  postsEl.innerHTML = t.posts.map((p, i) => `
    <div class="post${i === 0 ? ' op' : ''}">
      <span class="author">${esc(p.author)}</span>
      <span class="time">${formatAge(data.now - p.created)} ago</span>
      <div class="text">${esc(p.text)}</div>
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
        location.hash = '';
      }
    }
  } catch (_) {}
}

threadForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    name: document.getElementById('t-name').value.trim(),
    title: document.getElementById('t-title').value.trim(),
    text: document.getElementById('t-text').value.trim(),
  };
  if (!body.name || !body.title || !body.text) return;
  try {
    const r = await fetch('/board/threads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (r.ok) {
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
    await fetch(`/board/thread/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, text }),
    });
    document.getElementById('r-text').value = '';
    await refresh();
  } catch (_) {}
});

// Persist the name across both forms and refreshes, same key as the shoutbox.
for (const el of [document.getElementById('t-name'), document.getElementById('r-name')]) {
  el.value = localStorage.getItem('shout-name') || '';
  el.addEventListener('input', () => localStorage.setItem('shout-name', el.value));
}

window.addEventListener('hashchange', refresh);
refresh();
setInterval(refresh, POLL_MS);
