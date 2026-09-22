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
