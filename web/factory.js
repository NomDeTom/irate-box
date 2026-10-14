// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The Firmware Factory's own page, /admin/factory.html: every decision about building and scheduling
// builds is here; admin.js keeps only the Factory's settings outside the app (its tile, where builds come
// from). Behind the same login as /admin, and asking the same /admin/factory API.
(function () {
  'use strict';
  const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
  const noteEl = (id) => document.getElementById(id);
  function say(text, ok, at) { at.textContent = text; at.classList.toggle('bad', !ok); at.hidden = !text; }
  const el = (tag, props = {}, ...kids) => {
    // A button with no class of its own is an action button (the control vocabulary).
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

  // --- the Firmware Factory (factory.py): select a source, a ref and targets; follow the queue ------
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
  const facReady = (f) => (facData && facData.readiness[f]) || 'Not tested on this box. Its first build downloads its toolchain';
  const sentence = (s) => { const x = String(s).replace(/\.$/, ''); return `${x.charAt(0).toUpperCase()}${x.slice(1)}.`; };
  const facEst = (f) => { const e = facData && facData.estimates[f]; return e ? `about ${facDur(e.seconds)} a target here` : 'no build estimate'; };

  async function loadFactory() {
    try { renderFactory(await getJSON('/admin/factory')); } catch (_) { fac.waiting.textContent = 'Could not load the Firmware Factory.'; }
  }

  function renderFactory(d) {
    facData = d;
    fac.paused.hidden = !d.paused;
    fac.pause.textContent = d.paused ? 'Resume queue' : 'Pause queue';
    const names = d.sources.map((s) => s.name).join('\n');
    if (fac.source.dataset.names !== names) {
      const was = fac.source.value;
      fac.source.dataset.names = names;
      fac.source.replaceChildren(...d.sources.map((s) => el('option', { value: s.name, textContent: `${s.name} (${s.mirror ? 'mirror' : 'local repository'})` })));
      if (d.sources.some((s) => s.name === was)) fac.source.value = was;
      if (d.sources.length) loadFactoryRefs();
      else fac.targets.replaceChildren(el('p', { className: 'setting-desc', textContent: 'No source configured. Mirror meshtastic/firmware in Git → Mirrors, or push a fork to a repository on this box.' }));
    }
    const describe = (j) => `${j.name && j.name !== j.env ? `${j.name} (${j.env})` : j.env}, ${j.family}: ${j.source} ${j.ref}`;
    const rows = [];
    if (d.running) {
      const e = d.estimates[d.running.family];
      rows.push(el('div', { className: 'admin-item' }, el('span', { className: 'state state-running', textContent: 'Building' }),
        el('span', { textContent: ` ${describe(d.running)}` }),
        el('span', { className: 'setting-desc', textContent: ` Started ${ago(Date.now() / 1000 - d.running.started)}.${e ? ` Finishes about ${facClock(d.running.started + e.seconds)}.` : ''}` })));
    }
    d.waiting.forEach((j, i) => {
      rows.push(el('div', { className: 'admin-item' }, el('span', { textContent: `${i + 1}. ${describe(j)}` }),
        el('span', { className: 'setting-desc', textContent: j.start ? ` Starts about ${facClock(j.start)}.${j.finish ? ` Finishes about ${facClock(j.finish)}.` : ''}`
          : ' No build estimate. The box has not built this family, or an earlier family in the queue.' }),
        el('span', { className: 'library-buttons' },
          i ? actionButton('Move earlier', () => facAct({ action: 'up', id: j.id })) : null,
          actionButton('Cancel queued build', () => facAct({ action: 'cancel', id: j.id })))));
    });
    fac.waiting.replaceChildren(...(rows.length ? rows : [el('p', { className: 'setting-desc', textContent: 'No build running. No build queued.' })]));
    const mbs = (b) => (b == null ? '?' : `${Math.round(b / 2 ** 20)} MB`);
    const published = new Set((d.flasher || []).flatMap((rel) => rel.targets.map((t) => t.run)));
    fac.runs.replaceChildren(...(d.runs.filter((r) => r.state !== 'running').map((r) => {
      const u = r.resources || {};
      const used = r.resources ? [`${facDur(r.duration)}`, `CPU ${facDur(u.cpu)}`, `peak memory ${mbs(u.peak_memory)}`, `disk ${mbs(u.work_bytes)}`,
        u.hottest != null ? `peak temperature ${Math.round(u.hottest)} °C` : null,
        r.offline ? 'network disabled during build' : u.received ? `network bytes received during build: ${mbs(u.received)}` : u.received === 0 ? 'no network bytes received during build' : null,
        r.tools_only && u.tools_bytes ? `PlatformIO tools: ${mbs(u.tools_bytes)}` : null,
        u.from_cache ? `build cache hits: ${u.from_cache} of ${u.from_cache + (u.compiled || 0)} objects` : null].filter(Boolean).join(', ') : facDur(r.duration);
      const file = (n) => `/admin/ci/file?run=${encodeURIComponent(r.run)}&name=${encodeURIComponent(n)}`;
      return el('div', { className: 'admin-item' },
        el('span', { className: `state state-${r.state === 'passed' ? 'running' : 'stopped'}`, textContent: r.state === 'passed' ? (r.tools_only ? 'Dependencies downloaded' : r.offline ? 'Built offline' : 'Built') : r.state }),
        el('span', { textContent: ` ${describe(r)} (${String(r.commit || '').slice(0, 7)})` }),
        el('span', { className: 'setting-desc', textContent: ` ${r.finished ? `${ago(Date.now() / 1000 - r.finished)}. ` : ''}${used}.` }),
        el('span', { className: 'factory-files' }, ...(r.artifacts || []).map((n) => el('a', { href: file(n), textContent: n, download: n })),
          el('a', { href: file('log.txt'), textContent: 'Download build log', target: '_blank', rel: 'noopener' })),
        r.state === 'passed' && (r.artifacts || []).some((n) => n.endsWith('.mt.json'))
          ? el('span', { className: 'library-buttons' }, actionButton(published.has(r.run) ? 'Republish firmware' : 'Publish firmware',
            () => facAct({ action: 'publish', run: r.run.split('/')[1] }),
            { title: 'Offer this build in the web flasher as a release built on this box' })) : null);
    })));
    if (!fac.runs.children.length) fac.runs.replaceChildren(el('p', { className: 'setting-desc', textContent: 'No completed builds' }));
    const pub = (d.flasher || []).flatMap((rel) => rel.targets.map((t) => el('div', { className: 'admin-item' },
      el('span', { textContent: `${t.board}, ${t.platform}: ${rel.version.replace(/-built$/, '')}` }),
      el('span', { className: 'setting-desc', textContent: ` from ${t.ref || '?'}${t.epoch ? `, built ${new Date(t.epoch * 1000).toLocaleDateString()}` : ''}.` }),
      el('span', { className: 'library-buttons' }, actionButton('Unpublish firmware', () => facAct({ action: 'unpublish', version: rel.version, env: t.board }), { title: 'Withdraw this firmware from the web flasher. This does not delete the build.' })))));
    fac.flasher.replaceChildren(...(pub.length ? pub : [el('p', { className: 'setting-desc', textContent: 'No published firmware' })]));
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
    fac.targets.replaceChildren(el('p', { className: 'setting-desc', textContent: 'Reading build targets' }));
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

  // Each family's test target for Download build dependencies and Test target offline: a board people use (the hub suggests
  // one: the flasher's, a common board, a best-supported one), not the project's CI target; the
  // administrator can select another, kept while the page is open.
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
          el('span', { className: 'setting-desc', textContent: ` ${list.length} target${list.length === 1 ? '' : 's'}.${picked ? ` ${picked} selected.` : ''} ${sentence(facReady(f))} ${sentence(facEst(f))}` })),
        el('p', { className: 'library-buttons' }, el('label', { className: 'inline factory-test-target' }, 'Test target ', facTestSelect(f)),
        actionButton('Download build dependencies', async () => {
          try {
            const r = await postJSON('/admin/factory', { action: 'tools', source: fac.source.value, ref: fac.ref.value, family: f, env: facTest[f] });
            say(`Downloading build dependencies for ${r.env}, a ${f} target: queued. The box compiles nothing. After the download, a build of ${r.env} needs no internet access. Other ${f} targets can need more downloads.`, true, fac.note);
            renderFactory(r.snapshot);
          } catch (err) { say(err.message, false, fac.note); }
        }, { title: 'Download the toolchain, framework and libraries for the test target, without a build. A later build of that target needs no internet access. Other targets in the family can need more downloads.' }),
        actionButton('Test target offline', async () => {
          try {
            const r = await postJSON('/admin/factory', { action: 'offline', source: fac.source.value, ref: fac.ref.value, family: f, env: facTest[f] });
            say(`Building ${r.env} with the network disabled: queued. A pass shows that ${r.env} builds offline with the cached dependencies. It does not show that every ${f} target builds offline.`, true, fac.note);
            renderFactory(r.snapshot);
          } catch (err) { say(err.message, false, fac.note); }
        }, { title: 'Build the test target with the network disabled. A pass applies to that target, that revision and the cached dependencies only.' })),
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
    fac.chosen.textContent = n ? `${n} selected, in ${fams.size} famil${fams.size === 1 ? 'y' : 'ies'}.${secs !== null ? ` About ${facDur(secs)} in all.` : ''}${n > max ? ` At most ${max} at a time.` : ''}` : 'No targets selected.';
    fac.queue.disabled = !n || n > max;
    fac.queue.textContent = n ? `Add ${n} target${n === 1 ? '' : 's'} to queue` : 'Add to queue';
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
      say(`Added ${r.queued} target${r.queued === 1 ? '' : 's'} to the queue at ${r.commit.slice(0, 7)}.`, true, fac.note);
      facChosen.clear();
      renderFactory(r.snapshot);
      renderFactoryTargets();
    } catch (err) { say(err.message, false, fac.note); }
  });
  loadFactory();
  window.renderFactory = renderFactory;  // as it was when it lived in admin.js (the jsdom test draws with it)
})();
