// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The first-use page (admin-setup.html): create the administrator account. A file of its own, not inline, so
// the page runs under script-src 'self'.
(function () {
  const form = document.getElementById('setup-form');
  const note = document.getElementById('setup-note');
  const say = (text, ok) => { note.textContent = text; note.classList.toggle('bad', !ok); note.hidden = false; };
  // Over HTTP the form waits for the administrator's selection; over HTTPS it is ready.
  const plain = location.protocol !== 'https:';
  const httpOk = document.getElementById('setup-http-ok');
  if (plain) {
    document.getElementById('setup-http').hidden = false;
    document.getElementById('setup-https-link').href = `https://${location.hostname}/admin/setup`;
    const lock = () => { for (const el of form.elements) el.disabled = !httpOk.checked; };
    httpOk.addEventListener('change', lock);
    lock();
  }
  // The root helper takes a second or two. Once the first use has ended, the web server asks for a
  // sign-in, so a 401 here also means "done".
  async function wait(id, tries) {
    try {
      const r = await fetch(`/admin/setup?id=${encodeURIComponent(id)}`);
      if (r.status === 401) { done(); return; }
      const s = await r.json();
      if (s.result) {
        if (s.result.ok) { done(); return; }
        say(s.result.message, false);
        form.querySelector('button').disabled = false;
        return;
      }
      if (!s.unclaimed) { done(); return; }
    } catch (_) { /* the web server restarting */ }
    if (tries > 0) setTimeout(() => wait(id, tries - 1), 1500);
    else { say('No answer from the box. Reload this page to see whether the password was set.', false); }
  }
  function done() {
    say('The administrator account is created and you are signed in. Opening the rest of the setup.', true);
    setTimeout(() => { location.href = '/admin/#welcome'; }, 2500);
  }
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const pw = form.elements.password.value, name = form.elements.name.value.trim();
    if (pw !== form.elements.confirm.value) { say('The passwords do not match.', false); return; }
    form.querySelector('button').disabled = true;
    try {
      const r = await fetch('/admin/setup', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' }, body: JSON.stringify({ name, password: pw, over_http: plain && httpOk.checked }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
      say('Creating the account.', true);
      wait(data.id, 20);
    } catch (err) {
      say(err.message, false);
      form.querySelector('button').disabled = false;
    }
  });
})();
