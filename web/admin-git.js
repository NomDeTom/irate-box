// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Git's Builds on its own page, /admin/git.html: the build cache, the limits, the runs and their
// logs, and the setup templates. Behind the same login as /admin; admin.js keeps the Git
// repositories and links here. Asks the same /admin/ci and /admin/firmware APIs.
(function () {
  'use strict';
  const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
  const noteEl = (id) => document.getElementById(id);
  function say(text, ok, at) { at.textContent = text; at.classList.toggle('bad', !ok); at.hidden = !text; }
  const el = (tag, props = {}, ...kids) => {
    if (tag === 'button' && !props.className) props = { ...props, className: 'action-btn' };
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  };
  async function getJSON(url) { const response = await fetch(url); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
  async function postJSON(url, body) {
    const response = await fetch(url, { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  const actionButton = (label, onclick, extra = {}) => el('button', { type: 'button', textContent: label, onclick, ...extra, className: `action-btn${extra.className ? ' ' + extra.className : ''}` });
  const size = (bytes) => {
    if (Number.isNaN(bytes)) return 'size unknown';
    if (!bytes) return '0 MB';
    const gb = bytes / 2 ** 30;
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.max(0.1, bytes / 2 ** 20).toFixed(1)} MB`;
  };
  const commitDate = (unix) => new Date(unix * 1000).toISOString().slice(0, 10);
  const STATE_ICON = { passed: '✅', failed: '❌', 'timed out': '⏱', interrupted: '⏹', running: '⏳' };
  const minutes = (seconds) => (seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`);
  const GIT_ADD_BACK = 'To add Git back, run install.sh again with --with-git.';
  let gitServed = true;
  const gitRemoved = () => !gitServed;

  // --- builds on push (ci.py): the newest runs, refreshed while one is going -----------------
  const ciAbout = noteEl('ci-about');
  const ciRuns = noteEl('ci-runs');
  const ciNote = noteEl('ci-note');
  let ciPoll = null;
  let ciData = null;
  let ciViewing = null; // { run, next, log } while a run's view is open

  function renderCi(data) {
    ciData = data;
    if (!data.installed) {
      ciAbout.textContent = 'Builds are not set up on this box. Run install.sh again.';
      return;
    }
    ciAbout.textContent =
      `A push to an administrator-only repository builds here when its commit has a ${data.script} at the top. ` +
      `The box makes a fresh clone and runs bash ${data.script} as a dedicated user at the lowest priority. Repositories that guests can push to never build. ` +
      'Set up builds, or select Build now, under Manage on the card of a repository.' +
      (data.queued ? ` ${data.queued} waiting.` : '') +
      (gitRemoved() ? ` Git is removed from this box, so nothing can be pushed and no push builds. ${GIT_ADD_BACK}` : '');
    const mem = data.memory || {};
    const memText = /^\d+$/.test(mem.max || '') ? `memory limit ${size(Number(mem.max))}` : 'no memory limit';
    noteEl('ci-limits').textContent =
      `Each build: up to ${Math.round(data.time_limit / 3600)} hours, ${memText}, ` +
      `the lowest CPU priority. The runs use ${size(data.runs_bytes || 0)}. The box retains the newest ${data.keep_runs} runs for each repository, and every run marked Retain run.`;
    ciKeep.fill({ keep: data.keep_runs });
    noteEl('ci-offline').textContent =
      'Without internet access: ' +
      ((data.mirrored || []).length
        ? `git downloads of ${data.mirrored.length} URL${data.mirrored.length === 1 ? '' : 's'} in a build use the mirrors of this box (${data.mirrored
            .slice(0, 3)
            .map((url) => url.replace(/^https:\/\//, ''))
            .join(', ')}${data.mirrored.length > 3 ? '…' : ''}).`
        : 'no mirrors exist yet, so a build that downloads from the internet needs internet access (Git → Mirrors).') +
      (data.pio_deps
        ? ' PlatformIO packages come from the cache of the library (CI_PIO_DEPS).'
        : ' No PlatformIO cache is kept (the firmware build cache above).') +
      (data.wheelhouse
        ? ' pip installs (platformio itself) use the wheelhouse of the Building toolkit (PIP_NO_INDEX, PIP_FIND_LINKS).'
        : ' The Building toolkit has no wheelhouse yet, so a pip install in a build still needs internet access.');
    const runRows = data.runs.length
      ? data.runs.map((run) => {
          const said = [
            run.state,
            run.by === 'build-now' ? 'built manually' : null,
            run.duration != null ? minutes(run.duration) : null,
            run.started ? commitDate(run.started) : null,
          ].filter(Boolean).join(' · ');
          return el('div', { className: 'admin-item' },
            el('span', {},
              el('strong', {
                textContent:
                  `${STATE_ICON[run.state] || ''} ${run.repo} · ${run.branch} · ${run.commit.slice(0, 7)}` +
                  (run.keep ? ' · retained' : ''),
              }),
              el('span', { className: 'setting-desc', textContent: said })),
            el('span', { className: 'library-buttons' },
              actionButton('View', () => openRun(run.run), { className: 'small' })));
        })
      : [el('p', { className: 'setting-desc', textContent: 'No builds yet.' })];
    ciRuns.replaceChildren(...runRows);
    const setup = buildSetup();
    noteEl('ci-setup').replaceChildren(...(setup ? [setup] : []));
    try {
      localStorage.setItem('irate-ci-seen', String(Date.now() / 1000));
    } catch (_) {
      /* no storage */
    }
    clearTimeout(ciPoll);
    if (data.queued || data.runs.some((run) => run.state === 'running')) ciPoll = setTimeout(loadCi, 5000);
  }

  const ciFile = (run, name) =>
    `/admin/ci/file?run=${encodeURIComponent(run)}&name=${encodeURIComponent(name)}`;
  async function openRun(run) {
    ciViewing = { run, next: 0, log: '' };
    await followRun();
  }
  async function followRun() {
    if (!ciViewing) return;
    const box = noteEl('ci-run-view');
    let v;
    try {
      v = await getJSON(`/admin/ci/run?run=${encodeURIComponent(ciViewing.run)}&from=${ciViewing.next}`);
    } catch (err) {
      box.hidden = false;
      box.replaceChildren(el('p', { className: 'setting-desc bad', textContent: err.message }));
      return;
    }
    ciViewing.log += v.log;
    ciViewing.next = v.next;
    const st = v.status;
    const running = st.state === 'running';
    const pre = el('pre', { className: 'admin-log ci-log', textContent: ciViewing.log.slice(-200000) });
    box.hidden = false;
    box.replaceChildren(
      el('div', { className: 'git-manage-head' },
        el('strong', { textContent: `${STATE_ICON[st.state] || ''} ${st.repo} · ${st.branch} · ${st.commit.slice(0, 7)}` }),
        actionButton('Close', () => { ciViewing = null; box.hidden = true; }, { className: 'small' })),
      el('p', {
        className: 'setting-desc',
        textContent: [
          st.state,
          st.duration != null ? minutes(st.duration) : running ? `running for ${minutes(Math.round(Date.now() / 1000 - st.started))}` : null,
          st.keep ? 'retained' : null,
        ].filter(Boolean).join(' · '),
      }),
      v.steps.length ? el('ol', { className: 'ci-steps' }, ...v.steps.map((x) => el('li', { textContent: x }))) : null,
      pre,
      v.artifacts.length
        ? el('p', { className: 'library-buttons' },
            el('span', { className: 'setting-desc', textContent: 'Artifacts: ' }),
            ...v.artifacts.map((artifact) => el('a', {
              href: ciFile(v.run, artifact.name),
              className: 'button-link small',
              textContent: `${artifact.name} (${size(artifact.size)})`,
            })))
        : null,
      el('p', { className: 'library-buttons' },
        el('a', { href: ciFile(v.run, 'log.txt'), target: '_blank', className: 'button-link small', textContent: 'Download full log' }),
        running ? null : actionButton(st.keep ? 'Stop retaining run' : 'Retain run', () => ciChange(v.run, st.keep ? 'unkeep' : 'keep'), { className: 'small' }),
        running ? null : actionButton('Delete run', async (event) => {
          if (await AW.confirmHere('Delete this run, its log and its artifacts?', { anchor: event.currentTarget }))
            ciChange(v.run, 'delete');
        }, { className: 'small danger' })),
    );
    pre.scrollTop = pre.scrollHeight;
    if (running) setTimeout(followRun, 2000);
  }
  async function ciChange(run, action) {
    try {
      await postJSON('/admin/ci', { action, run });
      say(action === 'delete' ? 'Queued. The builder deletes the run after any build in progress.' : 'Queued. The builder applies the change when it is free.', true, ciNote);
      if (action === 'delete') {
        ciViewing = null;
        noteEl('ci-run-view').hidden = true;
      }
      setTimeout(loadCi, 1500);
    } catch (err) {
      say(err.message, false, ciNote);
    }
  }
  const ciKeep = AW.form(document.getElementById('ci-keep-form'), {
    label: 'Runs kept',
    save: async (c) => {
      const out = await postJSON('/admin/ci', { action: 'settings', keep_runs: Number(c.keep) });
      setTimeout(() => renderCi(out));
      return 'Saved.';
    },
  });

  // "Set up builds": what a script gets, and templates to start from.
  function buildSetup() {
    if (!ciData || !ciData.templates) return null;
    return el('details', { className: 'ci-setup' },
      el('summary', { textContent: 'Set up builds: script environment and templates' }),
      el('p', { className: 'setting-desc', textContent: `Put a ${ciData.script} at the top of the repository. Each push to a branch runs it with bash in a fresh clone of that commit.` }),
      el('dl', { className: 'ci-env' },
        ...Object.entries(ciData.env || {}).flatMap(([k, v]) => [el('dt', {}, el('code', { textContent: k })), el('dd', { textContent: v })])),
      ...Object.values(ciData.templates).map((t) => {
        const pre = el('pre', { className: 'admin-log', textContent: t.script });
        return el('div', { className: 'ci-template' },
          el('strong', { textContent: t.title }),
          actionButton('Copy', () => navigator.clipboard?.writeText(t.script).then(
            () => say(`Copied the ${t.title} template: save it as ${ciData.script}.`, true, ciNote),
            () => say('Select the text and copy it.', false, ciNote),
          ), { className: 'small' }),
          pre);
      }));
  }

  async function loadCi() {
    try {
      renderCi(await getJSON('/admin/ci'));
    } catch (_) {
      ciAbout.textContent = 'Could not read the builds.';
    }
  }

  // --- the build cache (firmware.py) --------------------------------------------------------
  const cacheForm = document.getElementById('ci-cache-form');
  const cacheStatus = noteEl('ci-cache-status');
  const cacheNote = noteEl('ci-cache-note');
  function renderCache(data) {
    const cfg = data.settings || {};
    const st = data.status || {};
    cacheForm.querySelectorAll('input[name=cache]').forEach((input) => {
      input.checked = input.value === (cfg.cache || 'discard');
    });
    cacheStatus.replaceChildren(
      ...(st.cache
        ? [
            `Stored firmware: ${st.cache.version}, ${st.cache.mode === 'native' ? 'headless' : 'complete'}, ${size(st.cache.bytes)}. `,
            actionButton('Remove cache', async () => {
              try {
                renderCache(await postJSON('/admin/firmware', { action: 'flush-cache' }));
                say('Cache removed. The next check or update downloads it again if the policy requires a cache.', true, cacheNote);
              } catch (err) {
                say(err.message, false, cacheNote);
              }
            }, { className: 'small' }),
          ]
        : [cfg.cache === 'discard' ? '' : 'No cache stored yet. The library downloads it on its schedule, or select Update now on the Firmware page.']),
    );
  }
  async function loadCache() {
    try {
      renderCache(await getJSON('/admin/firmware'));
    } catch (_) {
      cacheStatus.textContent = 'Could not read the build cache.';
    }
  }
  cacheForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const cache = new FormData(cacheForm).get('cache');
    try {
      renderCache(await postJSON('/admin/firmware', { action: 'settings', cache }));
      say('Saved. The library downloads or removes the cache on its schedule. Update now on the Firmware page does it now.', true, cacheNote);
    } catch (err) {
      say(err.message, false, cacheNote);
    }
  });

  async function loadGitState() {
    try {
      const g = await getJSON('/admin/git');
      gitServed = g.served !== false;
    } catch (_) {
      /* leave it as served: the page still works */
    }
  }

  (async () => {
    await loadGitState();
    loadCache();
    loadCi();
  })();
})();
