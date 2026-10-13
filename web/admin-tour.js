// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The setup tour: anything that affects user or box security is one of the setup's decisions. The
// setup step links to each setting and highlights it; the clipboard krab is the tour guide.
//
// Each decision has a default already, and the box runs on it until the owner decides: by keeping
// it ("Keep this, next"), or by changing the setting where it lives (any change inside the
// highlighted part counts). The setup step lists them as links; the tour bar steps through them,
// opening the page each lives on and highlighting it. What has been decided is kept on the hub
// (the setting setup_decided). Nothing here is a second copy of a setting.
const TOUR = (() => {
  'use strict';
  // The decisions: words, the section they live in, and the part of it to highlight (a selector,
  // or a finder for a part drawn later, such as one of the security findings).
  const finding = (re) => () => [...document.querySelectorAll('#security-findings .sec-choice, #security-findings .finding')].find((li) => re.test(li.textContent));
  const DECISIONS = [
    { id: 'visitors', label: 'Visitor counts', section: 'security', target: '#visitor_counts',
      said: 'Default: on. The box counts different devices today and this week. It uses salted hashes and deletes them every day and every week. The box keeps only the counts.' },
    { id: 'names', label: 'Signed-in name visibility', section: 'status-tiles', target: '#names-to-box',
      said: 'Guests see only how many people are signed in. Each person decides whether the box shows their name. New accounts start with the name hidden.' },
    { id: 'signup', label: 'Account registration', section: 'accounts', target: '#accounts-settings',
      said: 'Default: off. The administrator creates the accounts. If you turn registration on, anyone on the box\'s network can create an account.' },
    { id: 'sign-in-offer', label: 'Locked tiles', section: 'apps', target: '#sign-in-offer-box',
      said: 'Default: off. The tile of an app for users shows only to signed-in users. If you turn this on, guests also see the tile. It has a lock that leads to the sign-in page. The Visibility setting of each app has priority.' },
    { id: 'https', label: 'HTTPS', section: 'security', target: '#tls-state',
      said: 'Default: the box makes its own certificate authority. Each phone installs it once from /certificate. If you turn HTTPS off, passwords cross the network in clear text.' },
    { id: 'hotspot', label: 'Hotspot security', section: 'network/hotspot', fallback: 'network', target: '#hs-modes',
      said: 'Where the radio supports it, the hotspot uses encryption without a password (OWE). Guests join without a password. A person who only listens cannot read their traffic.' },
    { id: 'guest-net', label: 'Guest internet access', section: 'network/hotspot', fallback: 'network', target: '#guest-net-box',
      said: 'Default: off. Guests on the hotspot reach the box and nothing else. There are five levels. Each level shows what it opens.' },
    { id: 'ssh', label: 'SSH password authentication', section: 'security', target: finding(/SSH password/),
      said: 'The box keeps the image setting until you change it. If you allow keys only and you have no key, you cannot sign in over SSH.' },
    { id: 'tailscale', label: 'Remote access (Tailscale)', section: 'access', target: '#remote-section',
      said: 'The box keeps the setting that it found. This step shows only where the image has Tailscale.' },
    { id: 'cockpit', label: 'Cockpit (root sign-in on port 9090)', section: 'security', target: finding(/Cockpit/),
      said: 'While Cockpit runs, every guest can reach its sign-in page. This step shows only where the image has Cockpit.' },
    { id: 'terminal', label: 'Terminal (shell in the browser)', section: 'app-access-term', fallback: 'apps', target: '#app-access-term',
      said: 'By default, the terminal needs the administrator sign-in. It gives a root shell.' },
  ];
  // The setup's own steps, around the decisions, in the same form: what the box is, first; a backup,
  // last. Their state comes from the
  // parts of the page that know it (setupStep in admin.js).
  const OWN = [
    { id: 'password', label: 'Administrator password', section: 'access', target: '#access form, #access', said: 'You set this at first use. You can change it here at any time.' },
    { id: 'connection', label: 'How guests reach the box', section: 'network/hardware', fallback: 'network', target: '#net-devices', said: 'Phones and laptops find the hub through the network that the box is on, or through the box\'s own hotspot.' },
    { id: 'security', label: 'What the box exposes', section: 'security', target: '#security-findings', said: 'This step lists what a guest on the network can reach. It lists the settings that change that.' },
    { id: 'addons', label: 'Add-ons', section: 'addons', target: '#addons-list', said: 'The hub works without add-ons. Add the add-ons that this box needs.' },
    { id: 'books', label: 'Books', section: 'books', target: '#books', said: 'Kiwix serves the books offline. The librarian can keep them current.' },
  ];
  const BACKUP = { id: 'backup', label: 'Take a backup', section: 'backup', target: '#backup a[download]',
    said: 'Download the box\'s state now that you have set it up: settings, accounts, saved work and the library sources. Keep the file off the box.' };
  const STEPS = [...OWN, ...DECISIONS, BACKUP];
  const KIND = Object.fromEntries([...OWN.map((s) => [s.id, 'step']), ...DECISIONS.map((s) => [s.id, 'decision']), [BACKUP.id, 'step']]);
  const GUIDE = '/art/krab-controller-clipboard.webp';
  let decided = new Set();
  let muted = new Set();   // steps the owner asked not to be reminded of (setup_muted): listed, greyed, not counted
  let at = null;        // the decision being toured, or null
  let bar = null;
  let marked = null;    // the highlighted element

  const state = (id) => (window.SETUP_STATE || (typeof SETUP_STATE !== 'undefined' ? SETUP_STATE : {}))[id];
  // Done: a decision kept or changed; a step its part of the page calls fine, or kept in the tour.
  const isDone = (s) => decided.has(s.id) || (KIND[s.id] === 'step' && s.id !== 'backup' && (state(s.id) || {}).status === 'ok');
  const left = () => STEPS.filter((s) => !isDone(s) && !muted.has(s.id));
  const PILL = (s) => {
    if (KIND[s.id] === 'decision') return decided.has(s.id) ? ['ok-pill', 'decided'] : muted.has(s.id) ? ['info-pill', 'no reminders'] : ['info-pill', 'default'];
    const st = (state(s.id) || {}).status;
    if (isDone(s)) return ['ok-pill', 'done'];
    if (muted.has(s.id)) return ['info-pill', 'no reminders'];
    return st === 'problem' ? ['bad-pill', 'to fix'] : st === 'warn' ? ['warn-pill', 'needs review'] : ['info-pill', 'to do'];
  };

  async function save() {
    try { await postJSON('/admin/settings', { setup_decided: [...decided] }); } catch (_) { /* tried again with the next one */ }
    drawList();
  }
  async function mute(id, on) {
    if (on) muted.add(id); else muted.delete(id);
    try { await postJSON('/admin/settings', { setup_muted: [...muted] }); } catch (_) { /* shown as chosen; tried again with the next change */ }
    drawList();
  }
  function decide(id) {
    if (decided.has(id)) return;
    decided.add(id);
    save();
  }

  // The setup page: every step in one numbered list, each its short name, its pill, a line of what
  // it is now, and a link that starts the tour there.
  function drawList() {
    if (typeof drawAttention === 'function') drawAttention();  // Overview's Needs attention counts what is left
    const list = document.getElementById('setup-steps');
    if (!list) return;
    const n = left().length;
    list.replaceChildren(...STEPS.map((s, i) => {
      const [cls, word] = PILL(s);
      const li = document.createElement('li');
      li.dataset.step = s.id;
      li.className = isDone(s) ? 'step-ok' : muted.has(s.id) ? 'step-muted' : `step-${(state(s.id) || {}).status || 'todo'}`;
      const a = Object.assign(document.createElement('a'), { href: '#' + s.section, textContent: 'Open settings' });
      a.dataset.tour = s.id;
      a.addEventListener('click', (e) => { e.preventDefault(); go(i); });
      const text = Object.assign(document.createElement('span'), { className: 'step-text', textContent: ((state(s.id) || {}).text || s.said) + ' ' });
      text.append(a);
      if (!isDone(s)) {
        const m = Object.assign(document.createElement('button'), { type: 'button', className: 'link-button mute-step',
          textContent: muted.has(s.id) ? 'Resume reminders' : 'Stop reminders', title: muted.has(s.id) ? 'Count this step again' : 'Keep the step in the list. Do not count it as left to do.' });
        m.addEventListener('click', () => mute(s.id, !muted.has(s.id)));
        text.append(' ', m);
      }
      li.append(Object.assign(document.createElement('strong'), { textContent: s.label }), ' ',
        Object.assign(document.createElement('span'), { className: cls, textContent: word }), text);
      return li;
    }));
    const said = document.getElementById('setup-left');
    if (said) said.textContent = n ? `${n} of ${STEPS.length} still to do or decide: until then the box uses each default.` : 'All done.';
    const actions = document.querySelector('#welcome .tour-actions');
    if (!actions) return;
    let start = actions.querySelector('.tour-start');
    if (!start) { start = Object.assign(document.createElement('button'), { type: 'button', className: 'action-btn primary tour-start' }); actions.prepend(start); }
    start.textContent = !n ? 'Take the tour again' : n === STEPS.length ? 'Start the tour' : 'Resume tour';
    start.onclick = () => go(n ? STEPS.indexOf(left()[0]) : 0);
  }

  // The tour: open the decision's page, highlight its part, and the bar below.
  function go(i) {
    if (i == null || i < 0 || i >= STEPS.length) { stop(); return; }
    at = i;
    try { sessionStorage.setItem('irate-tour', String(i)); } catch (_) { /* this page's own memory only */ }
    const d = STEPS[i];
    const section = document.getElementById(d.section) ? d.section : d.fallback || d.section;
    if (location.hash === '#' + section) show(); else location.hash = '#' + section;
  }
  // Past the last decision: leave the tour and go back to where it starts (the setup steps, with the list and its start button).
  function finish() { stop(); location.hash = '#welcome'; }
  function stop() {
    at = null;
    try { sessionStorage.removeItem('irate-tour'); } catch (_) { /* nothing kept */ }
    unmark();
    if (bar) { bar.remove(); bar = null; }
  }
  function unmark() {
    if (!marked) return;
    marked.classList.remove('tour-target');
    marked.removeEventListener('change', onChange, true);
    marked = null;
  }
  function onChange() { if (at != null) decide(STEPS[at].id); }
  function show() {
    if (at == null) return;
    const d = STEPS[at];
    unmark();
    let target = typeof d.target === 'function' ? d.target() : document.querySelector(d.target);
    if (target && target.closest('.settings, .aw-settings, form, .admin-checks > li')) target = target.closest('.settings, .aw-settings, form, li') || target;
    const here = target && !target.closest('[hidden]');
    if (here) {
      marked = target;
      marked.classList.add('tour-target');
      // Changing it where it lives decides it too.
      marked.addEventListener('change', onChange, true);
      setTimeout(() => marked && marked.scrollIntoView && marked.scrollIntoView({ block: 'center', behavior: 'smooth' }), 60);
    }
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'tour-bar';
      bar.setAttribute('role', 'region');
      bar.setAttribute('aria-label', 'Setup tour');
      document.body.append(bar);
    }
    const img = Object.assign(document.createElement('img'), { className: 'tour-guide', src: GUIDE, alt: '' });
    const text = document.createElement('div');
    text.className = 'tour-text';
    text.append(Object.assign(document.createElement('strong'), { textContent: `Setup tour · ${at + 1} of ${STEPS.length}: ${d.label}` }), ' ',
      Object.assign(document.createElement('span'), { className: PILL(d)[0], textContent: PILL(d)[1] }),
      Object.assign(document.createElement('p'), { className: 'setting-desc', textContent: d.said + (here ? '' : ' (This setting is unavailable on this box. Keep the default and continue.)') }));
    const btn = (label, cls, fn) => { const b = Object.assign(document.createElement('button'), { type: 'button', className: cls, textContent: label }); b.addEventListener('click', fn); return b; };
    const back = btn('← Back', 'action-btn', () => go(at - 1));
    back.disabled = at === 0;
    const buttons = document.createElement('div');
    buttons.className = 'chip-group';
    buttons.append(back, btn(KIND[d.id] === 'decision' ? 'Keep and continue →' : 'Done, continue →', 'action-btn primary', () => { decide(d.id); (at + 1 < STEPS.length ? go(at + 1) : finish()); }),
      btn('Skip →', 'action-btn', () => (at + 1 < STEPS.length ? go(at + 1) : finish())), btn('Leave the tour', 'action-btn', stop));
    bar.replaceChildren(img, text, buttons);
  }

  async function load() {
    try { const st = await getJSON('/admin/settings'); decided = new Set(st.setup_decided || []); muted = new Set(st.setup_muted || []); } catch (_) { /* the list says what it can */ }
    drawList();
    let saved = null;
    try { saved = sessionStorage.getItem('irate-tour'); } catch (_) { /* none */ }
    if (saved != null) { at = Number(saved); show(); }
  }
  window.addEventListener('hashchange', () => { if (at != null) setTimeout(show, 30); });
  AL.onBuild(() => { if (at != null) setTimeout(show, 30); drawList(); });
  load();
  // Downloading a backup is the backup step done.
  document.addEventListener('click', (e) => { if (e.target.closest && e.target.closest('#backup a[download]')) decide('backup'); });
  return { DECISIONS, STEPS, go, stop, decided: () => new Set(decided), drawList, left: () => left().length };
})();
if (typeof window !== 'undefined') window.TOUR = TOUR;
