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
    { id: 'sign-in-offer', label: 'A tile a guest can\'t open', section: 'apps', target: '#builtin-list',
      said: 'Each app\'s tile can show to everyone with a lock that leads to sign-in, or only to those who may open it (as its access, the default).' },
    { id: 'https', label: 'HTTPS', section: 'security', target: '#tls-state',
      said: 'The box\'s own certificate authority by default: phones install it once from /certificate. Off sends passwords in the clear.' },
    { id: 'hotspot', label: 'The hotspot\'s security', section: 'security', target: '#hs-modes',
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
  const GUIDE = '/art/krab-controller-clipboard.webp';
  let decided = new Set();
  let at = null;        // the decision being toured, or null
  let bar = null;
  let marked = null;    // the highlighted element

  const byId = (id) => DECISIONS.find((d) => d.id === id);
  const left = () => DECISIONS.filter((d) => !decided.has(d.id));

  async function save() {
    try { await postJSON('/admin/settings', { setup_decided: [...decided] }); } catch (_) { /* tried again with the next one */ }
    drawList();
  }
  function decide(id) {
    if (decided.has(id)) return;
    decided.add(id);
    save();
  }

  // The setup step: each decision a link, its state beside it.
  function drawList() {
    const li = document.querySelector('#setup-steps [data-step="decisions"]');
    if (!li) return;
    const n = left().length;
    li.className = n ? 'step-todo' : 'step-done';
    li.querySelector('span').textContent = n ? `${n} of ${DECISIONS.length} still to decide: the box uses each default until you do.` : 'All decided.';
    const list = li.querySelector('.tour-list') || li.appendChild(Object.assign(document.createElement('ol'), { className: 'tour-list' }));
    list.replaceChildren(...DECISIONS.map((d, i) => {
      const item = document.createElement('li');
      if (decided.has(d.id)) item.className = 'done';
      const a = document.createElement('a');
      a.href = '#' + d.section;
      a.dataset.tour = d.id;
      a.textContent = d.label;
      a.addEventListener('click', (e) => { e.preventDefault(); go(i); });
      item.append(a, ' ', Object.assign(document.createElement('span'), { className: decided.has(d.id) ? 'ok-pill' : 'info-pill', textContent: decided.has(d.id) ? 'decided' : 'default' }));
      return item;
    }));
    const start = li.querySelector('.tour-start') || li.appendChild(Object.assign(document.createElement('button'), { type: 'button', className: 'action-btn primary tour-start' }));
    start.textContent = !n ? 'Go round again' : n === DECISIONS.length ? 'Start the tour' : 'Carry on the tour';
    start.onclick = () => go(n ? DECISIONS.indexOf(left()[0]) : 0);
  }

  // The tour: open the decision's page, highlight its part, and the bar below.
  function go(i) {
    if (i == null || i < 0 || i >= DECISIONS.length) { stop(); return; }
    at = i;
    try { sessionStorage.setItem('irate-tour', String(i)); } catch (_) { /* this page's own memory only */ }
    const d = DECISIONS[i];
    const section = document.getElementById(d.section) ? d.section : d.fallback || d.section;
    if (location.hash === '#' + section) show(); else location.hash = '#' + section;
  }
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
  function onChange() { if (at != null) decide(DECISIONS[at].id); }
  function show() {
    if (at == null) return;
    const d = DECISIONS[at];
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
    text.append(Object.assign(document.createElement('strong'), { textContent: `Setup tour · ${at + 1} of ${DECISIONS.length}: ${d.label}` }), ' ',
      Object.assign(document.createElement('span'), { className: decided.has(d.id) ? 'ok-pill' : 'warn-pill', textContent: decided.has(d.id) ? 'decided' : 'to decide' }),
      Object.assign(document.createElement('p'), { className: 'setting-desc', textContent: d.said + (here ? '' : ' (Not on this box, or not showing here: keep the default and carry on.)') }));
    const btn = (label, cls, fn) => { const b = Object.assign(document.createElement('button'), { type: 'button', className: cls, textContent: label }); b.addEventListener('click', fn); return b; };
    const back = btn('← Back', 'action-btn', () => go(at - 1));
    back.disabled = at === 0;
    const buttons = document.createElement('div');
    buttons.className = 'chip-group';
    buttons.append(back, btn('Keep this, next →', 'action-btn primary', () => { decide(d.id); go(at + 1 < DECISIONS.length ? at + 1 : -1); }),
      btn('Skip →', 'action-btn', () => go(at + 1 < DECISIONS.length ? at + 1 : -1)), btn('Leave the tour', 'action-btn', stop));
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
  return { DECISIONS, go, stop, decided: () => new Set(decided), drawList };
})();
if (typeof window !== 'undefined') window.TOUR = TOUR;
