// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Firmware Factory's own page, /admin/factory.html (menu overhaul F7; checklist 5b, snag 7: "all of
// the decisions about building and scheduling builds should be inside it"). Moved whole from
// admin.js, which keeps only the Factory's settings outside the app (its tile, where builds come
// from). Behind the same login as /admin, and asking the same /admin/factory API.
(function () {
  'use strict';
  const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
  const noteEl = (id) => document.getElementById(id);
  function say(text, ok, at) { at.textContent = text; at.classList.toggle('bad', !ok); at.hidden = !text; }
  const el = (tag, props = {}, ...kids) => {
    // A button with no class of its own is an action button (rule 6a: the control vocabulary).
  if (tag === 'button' && !props.className) props = { ...props, className: 'action-btn' };
  const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  };
  const ago = (secs) => {
    if (secs < 90) return 'just now';
    const m = Math.round(secs / 60);
    if (m < 90) return `${m} min ago`;
    const h = Math.round(m / 60);
    return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
  };
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }
  const actionButton = (label, onclick, extra = {}) => el('button', { type: 'button', textContent: label, onclick, ...extra, className: `action-btn${extra.className ? ' ' + extra.className : ''}` });
  // The page is the Factory: always showing (admin.js asked its layout).
  const paneShown = () => true;

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
  loadFactory();
  window.renderFactory = renderFactory;  // as it was when it lived in admin.js (the jsdom test draws with it)
})();
