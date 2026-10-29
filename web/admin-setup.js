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
    // The set-up page at the box's HTTPS port, once the box says it.
    httpsInfo().then((info) => {
      document.getElementById('setup-https-link').href = `${httpsUrl(info && info.port)}admin/setup`;
    });
    const lock = () => { for (const el of form.elements) el.disabled = !httpOk.checked; };
    httpOk.addEventListener('change', lock);
    lock();
  }
  // The root helper takes a second or two. Once the first use has ended, the web server asks for a
  // sign-in, so a 401 here also means "done".
  async function wait(id, tries) {
    try {
      const answer = await fetch(`/admin/setup?id=${encodeURIComponent(id)}`);
      if (answer.status === 401) { done(); return; }
      const state = await answer.json();
      if (state.result) {
        if (state.result.ok) { done(); return; }
        say(state.result.message, false);
        form.querySelector('button').disabled = false;
        return;
      }
      if (!state.unclaimed) { done(); return; }
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
      const answer = await fetch('/admin/setup', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' }, body: JSON.stringify({ name, password: pw, over_http: plain && httpOk.checked }),
      });
      const data = await answer.json().catch(() => ({}));
      if (!answer.ok) throw new Error(data.error || `HTTP ${answer.status}`);
      say('Creating the account.', true);
      wait(data.id, 20);
    } catch (err) {
      say(err.message, false);
      form.querySelector('button').disabled = false;
    }
  });
})();
