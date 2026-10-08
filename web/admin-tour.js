// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The setup tour (menu overhaul M14; proposed 5h). Tom, 2026-10-08: "anything that has an impact
// on user or box security" is one of the setup's decisions; "the checklist should be hyperlinks to
// the settings - and some kind of highlighting of the setting that needs deciding. It's a
// quick-tour wizard"; and "use the clipboard krab as the tour guide".
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
  const finding = (re) => () => [...document.querySelectorAll('#security-findings li')].find((li) => re.test(li.textContent));
  const DECISIONS = [
    { id: 'visitors', label: 'Count unique visitors', section: 'security', target: '#visitor_counts',
      said: 'On by default: different devices today and this week, by salted hashes thrown away daily and weekly; only the counts are kept.' },
    { id: 'names', label: 'Names of those signed in, shown to', section: 'status-tiles', target: '#names-to-box',
      said: 'Guests only ever get how many are signed in. Each person also decides whether their own name shows; new accounts start not shown.' },
    { id: 'signup', label: 'New accounts', section: 'accounts', target: '#accounts-settings',
      said: 'Off by default: the admin makes accounts. Sign-up lets anyone on the box make one.' },
    { id: 'sign-in-offer', label: 'A tile a guest can\'t open', section: 'apps', target: '#sign-in-offer-box',
      said: 'Off by default: an app for users shows its tile only to those signed in. On, guests see it too, with a lock that leads to sign-in. Each app\'s own "Tile shown to" still has the last word.' },
    { id: 'https', label: 'HTTPS', section: 'security', target: '#tls-state',
      said: 'The box\'s own certificate authority by default: phones install it once from /certificate. Off sends passwords in the clear.' },
    { id: 'hotspot', label: 'The hotspot\'s security', section: 'network', target: '#hs-modes',
      said: 'Encrypted with no password (OWE) where the radio can: guests join freely, and nobody nearby can read what they do.' },
    { id: 'guest-net', label: 'Guests reach the internet', section: 'network', target: '#ap-state',
      said: 'Off: guests on the hotspot reach the box and nothing else. Sharing the box\'s connection with them isn\'t built yet.' },
    { id: 'ssh', label: 'SSH takes passwords', section: 'security', target: finding(/SSH password/),
      said: 'Left as the box had it unless you choose here: keys only can lock out an owner who has no key.' },
    { id: 'tailscale', label: 'Remote access (Tailscale)', section: 'access', target: '#remote-section',
      said: 'Left as found. Only where the image has Tailscale; otherwise there is nothing to decide.' },
    { id: 'cockpit', label: 'Cockpit (a root login on :9090)', section: 'security', target: finding(/Cockpit/),
      said: 'Every guest can reach its login while it runs (security review, S1). Only where the image has it.' },
    { id: 'terminal', label: 'Terminal (a shell in the browser)', section: 'app-access-term', fallback: 'apps', target: '#app-access-term',
      said: 'Behind the admin login by default: a root shell, which is its whole point.' },
  ];
  // The setup's own steps, around the decisions: what the box is, first; a backup, last (Tom,
  // 2026-10-08: "make them follow the same format throughout, and make them all part of the tour";
  // "the final step - take a backup - has been missed entirely"). Their state comes from the
  // parts of the page that know it (setupStep in admin.js).
  const OWN = [
    { id: 'password', label: 'Admin password', section: 'access', target: '#access form, #access', said: 'Set at first use. Change it here whenever you like.' },
    { id: 'connection', label: 'How guests reach the box', section: 'network', target: '#net-devices', said: 'How phones and laptops find the hub: on the network the box is on, or its own hotspot.' },
    { id: 'security', label: 'What the box exposes', section: 'security', target: '#security-findings', said: 'What a guest on the network can reach, and the choices that change it.' },
    { id: 'addons', label: 'Add-ons', section: 'addons', target: '#addons-list', said: 'The hub works without any; add the ones this box is for.' },
    { id: 'books', label: 'Books', section: 'books', target: '#books', said: 'What Kiwix serves offline, and which the librarian keeps current.' },
  ];
  const BACKUP = { id: 'backup', label: 'Take a backup', section: 'backup', target: '#backup a[download]',
    said: 'Download the hub\'s state now that it is set up: settings, accounts, saved work and the library\'s sources. Keep it off the box.' };
  const STEPS = [...OWN, ...DECISIONS, BACKUP];
  const KIND = Object.fromEntries([...OWN.map((s) => [s.id, 'step']), ...DECISIONS.map((s) => [s.id, 'decision']), [BACKUP.id, 'step']]);
  const GUIDE = '/art/krab-controller-clipboard.webp';
  let decided = new Set();
  let at = null;        // the decision being toured, or null
  let bar = null;
  let marked = null;    // the highlighted element

  const state = (id) => (window.SETUP_STATE || (typeof SETUP_STATE !== 'undefined' ? SETUP_STATE : {}))[id];
  // Done: a decision kept or changed; a step its part of the page calls fine, or kept in the tour.
  const isDone = (s) => decided.has(s.id) || (KIND[s.id] === 'step' && s.id !== 'backup' && (state(s.id) || {}).status === 'ok');
  const left = () => STEPS.filter((s) => !isDone(s));
  const PILL = (s) => {
    if (KIND[s.id] === 'decision') return decided.has(s.id) ? ['ok-pill', 'decided'] : ['info-pill', 'default'];
    const st = (state(s.id) || {}).status;
    if (isDone(s)) return ['ok-pill', 'done'];
    return st === 'problem' ? ['bad-pill', 'to fix'] : st === 'warn' ? ['warn-pill', 'look at it'] : ['info-pill', 'to do'];
  };

  async function save() {
    try { await postJSON('/admin/settings', { setup_decided: [...decided] }); } catch (_) { /* tried again with the next one */ }
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
    if (typeof drawAttention === 'function') drawAttention();  // Overview's Needs attention counts what is left (F4)
    const list = document.getElementById('setup-steps');
    if (!list) return;
    const n = left().length;
    list.replaceChildren(...STEPS.map((s, i) => {
      const [cls, word] = PILL(s);
      const li = document.createElement('li');
      li.dataset.step = s.id;
      li.className = isDone(s) ? 'step-ok' : `step-${(state(s.id) || {}).status || 'todo'}`;
      const a = Object.assign(document.createElement('a'), { href: '#' + s.section, textContent: 'Go there' });
      a.dataset.tour = s.id;
      a.addEventListener('click', (e) => { e.preventDefault(); go(i); });
      const text = Object.assign(document.createElement('span'), { className: 'step-text', textContent: ((state(s.id) || {}).text || s.said) + ' ' });
      text.append(a);
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
    start.textContent = !n ? 'Take the tour again' : n === STEPS.length ? 'Start the tour' : 'Carry on the tour';
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
      Object.assign(document.createElement('p'), { className: 'setting-desc', textContent: d.said + (here ? '' : ' (Not on this box, or not showing here: keep the default and carry on.)') }));
    const btn = (label, cls, fn) => { const b = Object.assign(document.createElement('button'), { type: 'button', className: cls, textContent: label }); b.addEventListener('click', fn); return b; };
    const back = btn('← Back', 'action-btn', () => go(at - 1));
    back.disabled = at === 0;
    const buttons = document.createElement('div');
    buttons.className = 'chip-group';
    buttons.append(back, btn(KIND[d.id] === 'decision' ? 'Keep this, next →' : 'Done, next →', 'action-btn primary', () => { decide(d.id); (at + 1 < STEPS.length ? go(at + 1) : finish()); }),
      btn('Skip →', 'action-btn', () => (at + 1 < STEPS.length ? go(at + 1) : finish())), btn('Leave the tour', 'action-btn', stop));
    bar.replaceChildren(img, text, buttons);
  }

  async function load() {
    try { decided = new Set((await getJSON('/admin/settings')).setup_decided || []); } catch (_) { /* the list says what it can */ }
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
