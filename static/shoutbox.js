const POLL_MS = 4000;
const MAX_DISPLAY = 50;

const messagesEl = document.getElementById('messages');
const form = document.getElementById('shout-form');
const nameInput = document.getElementById('name-input');
const msgInput = document.getElementById('msg-input');
const nameRoll = document.getElementById('name-roll');
const decayNote = document.getElementById('decay-note');

// Random handle: adjective + noun, pirate-flavoured to match the box. Longest combo
// is well inside the input's 32-char limit.
const ADJECTIVES = [
  'Salty', 'Rusty', 'Grumpy', 'Soggy', 'Barnacled', 'Weathered', 'Marooned',
  'Squinting', 'Drunken', 'Peg-legged', 'One-eyed', 'Windward', 'Leeward',
  'Scurvy', 'Mutinous', 'Becalmed', 'Shipwrecked', 'Tattooed', 'Bilge', 'Irate',
];
const NOUNS = [
  'Parrot', 'Cutlass', 'Doubloon', 'Kraken', 'Compass', 'Anchor', 'Cannon',
  'Buccaneer', 'Deckhand', 'Bosun', 'Swab', 'Sextant', 'Galleon', 'Corsair',
  'Cabin Boy', 'Quartermaster', 'Lookout', 'Castaway', 'Spyglass', 'Grog',
];

function randomName() {
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  return `${pick(ADJECTIVES)} ${pick(NOUNS)}`;
}

let signature = '';
let shown = [];

function setDecayNote(ttl) {
  decayNote.textContent =
    `Messages fade ${formatAge(ttl)} after they are posted — measured in hub uptime, ` +
    `not calendar time. The hub has no clock; while it is switched off, nothing ages.`;
}

function renderMessages(data) {
  setDecayNote(data.ttl);
  const slice = data.messages.slice(-MAX_DISPLAY);
  const sig = `${slice.length}:${slice.length ? slice[slice.length - 1].created : 0}`;

  // Nothing new -- just move the ages along, so the DOM (and any text selection in
  // it) survives the poll.
  if (sig === signature) {
    shown.forEach((el, i) => {
      el.textContent = `${formatAge(data.now - slice[i].created)} ago`;
    });
    return;
  }
  signature = sig;

  const atBottom = messagesEl.scrollHeight - messagesEl.scrollTop <= messagesEl.clientHeight + 8;
  messagesEl.innerHTML = '';
  shown = [];

  if (!slice.length) {
    const em = document.createElement('span');
    em.className = 'empty';
    em.textContent = 'No messages yet. Say hi!';
    messagesEl.appendChild(em);
    return;
  }

  for (const m of slice) {
    const div = document.createElement('div');
    div.className = 'msg';
    // Ages come from the uptime clock, never `new Date()` -- see age.js.
    div.innerHTML =
      `<span class="author">${esc(m.name)}</span>` +
      `<span class="text">${esc(m.text)}</span>` +
      `<span class="time">${formatAge(data.now - m.created)} ago</span>`;
    messagesEl.appendChild(div);
    shown.push(div.querySelector('.time'));
  }

  if (atBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function poll() {
  try {
    const r = await fetch('/messages');
    if (r.ok) renderMessages(await r.json());
  } catch (_) {}
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = nameInput.value.trim();
  const text = msgInput.value.trim();
  if (!name || !text) return;
  try {
    await fetch('/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, text }),
    });
    msgInput.value = '';
    await poll();
  } catch (_) {}
});

// Persist name across refreshes
nameInput.value = localStorage.getItem('shout-name') || '';
nameInput.addEventListener('input', () => localStorage.setItem('shout-name', nameInput.value));

nameRoll.addEventListener('click', () => {
  nameInput.value = randomName();
  localStorage.setItem('shout-name', nameInput.value);
  msgInput.focus();
});

poll();
setInterval(poll, POLL_MS);
