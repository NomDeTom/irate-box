// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Recovery → Rescue instructions: the living will the rescue follows. The page shows the saved instructions and
// the safe defaults, saves them through /admin/rescue-will, and writes both copies after a typed confirmation.
// It never shows the hotspot password: only whether one is set, and a field for a new one.
(function () {
  'use strict';
  const root = document.getElementById('rescue-will');
  if (!root) return;
  const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Irate-Admin': '1' };
  const $ = (id) => document.getElementById(id);
  const el = AW.h;
  const WORDS = { match: 'matches the box', stale: 'older than the box', missing: 'not written', absent: 'not written' };
  let snapshot = null;
  let sec = null;
  let newPassword = '';
  let pushBusy = false;

  async function getJSON(url) {
    const response = await fetch(url, { headers: ADMIN_HEADERS });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((data.message && AW.say(data.message)) || data.error || `HTTP ${response.status}`);
    return data;
  }
  async function postJSON(url, body) {
    const response = await fetch(url, { method: 'POST', headers: ADMIN_HEADERS, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((data.message && AW.say(data.message)) || data.error || `HTTP ${response.status}`);
    return data;
  }
  function say(text, ok, at) {
    if (!at) return;
    at.textContent = text || '';
    at.classList.toggle('bad', !ok);
    at.hidden = !text;
  }
  const resultSaid = (result) => (result.msg ? AW.say(result.msg) : result.message || 'The root helper did not answer.');
  async function waitResult(id) {
    for (let i = 0; i < 60; i++) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      let box;
      try { box = await getJSON('/admin/box'); } catch (_) { continue; }
      const found = (box.results || []).find((result) => result.id === id);
      if (found) return found;
    }
    throw new Error('The rescue instructions were not saved in time.');
  }
  function state(text, bad) {
    const at = $('rescue-will-state');
    if (!at) return;
    if (text) {
      at.textContent = text;
      at.classList.toggle('bad', !!bad);
      return;
    }
    const w = snapshot.will || {};
    if (w.valid) {
      const copies = w.copies || {};
      const said = [copies.nand && `Onboard flash: ${WORDS[copies.nand] || copies.nand}.`, copies.usb && `USB drive: ${WORDS[copies.usb] || copies.usb}.`]
        .filter(Boolean).join(' ');
      at.textContent = `Saved on ${w.written ? new Date(w.written * 1000).toLocaleDateString() : 'an unknown date'}. ${said}`;
      at.classList.remove('bad');
    } else {
      const problems = Array.isArray(w.problems) ? w.problems.join(' ') : (w.problem || '');
      at.textContent = problems || 'There is no saved living will on this box. The safe defaults are shown; Save creates one.';
      at.classList.add('bad');
    }
  }
  function warn(text) {
    return el('p', { class: 'setting-desc bad', hidden: !text }, text || '');
  }
  function setWarn(id, text) {
    const at = $(id);
    if (!at) return;
    at.textContent = text || '';
    at.hidden = !text;
  }
  function def(on) { return on ? 'On' : 'Off'; }
  function field(label, control, help, defaultSaid, warnEl) {
    return el('div', { class: 'aw-field' },
      el('span', { class: 'aw-label', text: label }),
      control,
      help ? el('span', { class: 'setting-desc', text: help }) : null,
      defaultSaid ? el('span', { class: 'setting-desc', text: `Safe default: ${defaultSaid}.` }) : null,
      warnEl || null);
  }
  function chipRow(label, get, set, help, defaultSaid, warnEl) {
    let chips = null;
    const draw = () => {
      const next = AW.onOffChips(label, !!get(), false, (on) => {
        set(on);
        draw();
        sync();
      });
      if (chips) chips.replaceWith(next);
      chips = next;
    };
    draw();
    return field(label, chips, help, defaultSaid, warnEl);
  }
  function choiceRow(label, name, options, get, set, defaultSaid) {
    const box = el('div');
    AW.choices(box, name, options, {
      value: get(),
      compact: true,
      onChange: (value) => { set(value); sync(); },
    });
    return field(label, box, '', defaultSaid);
  }
  function group(title, description, ...kids) {
    return el('div', {},
      el('h3', { text: title }),
      description ? el('p', { class: 'setting-desc', text: description }) : null,
      el('div', { class: 'settings' }, ...kids));
  }
  function textInput(value, onInput, attrs = {}) {
    const input = el('input', { type: 'text', autocomplete: 'off', spellcheck: 'false', ...attrs });
    input.value = value == null ? '' : String(value);
    input.addEventListener('input', () => onInput(input.value));
    return input;
  }
  const AUTO = [
    ['triage', 'Read-only triage', 'Read the card and its boot files. Nothing is written.'],
    ['repair_gpt', 'Partition-table repair', 'Rebuild a damaged primary partition table from its valid backup. The data is not changed.'],
    ['repair_fs', 'Filesystem repair', 'Run e2fsck -y and fsck.fat -a. It can change the filesystem.'],
    ['reflash', 'Write an image', 'Write the restore image named below. It erases the card.'],
  ];
  const ROLES = [
    ['boot', 'Boot-chain diagnosis'],
    ['fsck', 'Filesystem check and repair'],
    ['health', 'Card health and the genuine test'],
    ['salvage', 'Salvage state, keys and WiFi'],
    ['reflash', 'Reflash and restore'],
    ['reachability', 'Reachability check'],
  ];
  const METHOD = [
    { value: 'none', title: 'Do not restore', desc: 'The rescue reports and asks; it does not restore the card by itself.' },
    { value: 'other-slot', title: 'The other A/B slot', desc: 'Use the inactive slot on a card that has two.' },
    { value: 'image', title: 'A full image', desc: 'Write the named image from this box, the USB drive or a URL.' },
    { value: 'salvage', title: 'A salvage copy', desc: 'Restore a copy taken before the trouble.' },
  ];
  const WHERE = {
    findings: [['usb', 'USB drive'], ['nand', 'Onboard flash'], ['both', 'Both']],
    backups: [['usb', 'USB drive'], ['nand', 'Onboard flash']],
    salvage: [['usb', 'USB drive'], ['nand', 'Onboard flash']],
  };
  const CONSENT = [
    { value: 'page', title: 'The rescue page', desc: 'The owner confirms on the page, with the typed words.' },
    { value: 'ssh', title: 'SSH', desc: 'The owner confirms over SSH.' },
    { value: 'console', title: 'The console', desc: 'The owner confirms on the board console.' },
  ];
  const AFTER = [
    { value: 'ask', title: 'Ask', desc: 'Offer to restart to the card after a repair.' },
    { value: 'never', title: 'Never', desc: 'Do not offer to restart to the card.' },
    { value: 'always', title: 'Always', desc: 'Restart to the card after a repair without asking.' },
  ];

  function autoValue(key) { return sec.get('auto')[key]; }
  function restoreValue() { return sec.get('restore'); }
  function whereValue() { return sec.get('where'); }
  function reachValue() { return sec.get('reach'); }
  function rolesValue() { return sec.get('roles'); }
  function setAuto(key, on) {
    sec.set('auto', { ...sec.get('auto'), [key]: !!on });
  }
  function setRestore(key, value) {
    sec.set('restore', { ...sec.get('restore'), [key]: value });
  }
  function setImage(key, value) {
    const restore = sec.get('restore');
    sec.set('restore', { ...restore, image: { ...restore.image, [key]: value } });
  }
  function setSalvage(key, value) {
    const restore = sec.get('restore');
    sec.set('restore', { ...restore, salvage: { ...restore.salvage, [key]: value } });
  }
  function setWhere(key, value) {
    sec.set('where', { ...sec.get('where'), [key]: value });
  }
  function setReach(key, value) {
    sec.set('reach', { ...sec.get('reach'), [key]: value });
  }
  function setRole(key, on) {
    sec.set('roles', { ...sec.get('roles'), [key]: !!on });
  }

  function buildFields() {
    const d = snapshot.defaults || {};
    const restore = restoreValue();
    const image = restore.image || {};
    const salvage = restore.salvage || {};
    const reach = reachValue();
    const form = [];

    form.push(group('What the rescue may do without asking', 'The rescue follows these before it asks. The safe defaults are the read-only check and the validated partition-table repair.', ...AUTO.map(([key, label, help]) =>
      chipRow(label, () => autoValue(key), (on) => setAuto(key, on), help, def(d.auto && d.auto[key])))),
      el('p', { class: 'setting-desc bad', id: 'rescue-will-auto-warn', hidden: true }));

    const methodBox = el('div');
    AW.choices(methodBox, 'rescue-will-method', METHOD, {
      value: restore.method,
      compact: true,
      onChange: (value) => { setRestore('method', value); sync(); },
    });
    const otherSlotBox = el('div');
    AW.choices(otherSlotBox, 'rescue-will-other-slot', [
      { value: 'auto', title: 'Auto', desc: 'Use the inactive slot.' },
      { value: 'a', title: 'Slot A', desc: 'Restore to slot A.' },
      { value: 'b', title: 'Slot B', desc: 'Restore to slot B.' },
    ], { value: restore.other_slot || 'auto', compact: true, onChange: (value) => { setRestore('other_slot', value); sync(); } });

    const sourceBox = el('div');
    AW.choices(sourceBox, 'rescue-will-image-source', [
      { value: 'usb', title: 'USB drive', desc: 'An image on the rescue USB drive.' },
      { value: 'box', title: 'This box', desc: 'An image the box holds.' },
      { value: 'url', title: 'A URL', desc: 'An HTTPS address.' },
    ], { value: image.source || 'usb', compact: true, onChange: (value) => { setImage('source', value); sync(); } });
    const imageName = textInput(image.name, (value) => setImage('name', value), { placeholder: 'mpwrd-os.img.xz' });
    const imageUrl = textInput(image.url, (value) => setImage('url', value), { placeholder: 'https://example.org/image.img.xz' });
    const imageSha = textInput(image.sha256, (value) => setImage('sha256', value), { placeholder: '64 lowercase hex digits' });
    const imagePick = el('select', {});
    const images = (snapshot.restore_candidates && snapshot.restore_candidates.images) || [];
    imagePick.append(el('option', { value: '', text: images.length ? 'Select an image on this box' : 'No image is on this box' }));
    for (const item of images) imagePick.append(el('option', { value: item.name, text: `${item.name} (${Math.round((item.size || 0) / 2 ** 20)} MB)` }));
    if (image.name) imagePick.value = image.name;
    imagePick.addEventListener('change', () => {
      const chosen = images.find((item) => item.name === imagePick.value);
      if (!chosen) return;
      setImage('name', chosen.name);
      setImage('sha256', chosen.sha256 || '');
      imageName.value = chosen.name;
      imageSha.value = chosen.sha256 || '';
      sync();
    });
    const imageBox = el('div', { id: 'rescue-will-image' },
      field('Source', sourceBox, 'Where the image is kept.'),
      field('Image on this box', imagePick, 'Select one to fill its name and SHA-256.'),
      field('Image name', imageName, 'A bare file name; no path.'),
      field('Image URL', imageUrl, 'An HTTPS address.'),
      field('SHA-256', imageSha, 'The image’s own digest, so the rescue can verify it before writing.'));

    const salvageSourceBox = el('div');
    AW.choices(salvageSourceBox, 'rescue-will-salvage-source', [
      { value: 'usb', title: 'USB drive', desc: 'A copy on the rescue USB drive.' },
      { value: 'nand', title: 'Onboard flash', desc: 'A copy in the flash state.' },
    ], { value: salvage.source || 'usb', compact: true, onChange: (value) => { setSalvage('source', value); sync(); } });
    const salvageName = textInput(salvage.name, (value) => setSalvage('name', value), { placeholder: '2026-11-06T101500' });
    const salvagePick = el('select', {});
    const sets = (snapshot.restore_candidates && snapshot.restore_candidates.salvage) || [];
    salvagePick.append(el('option', { value: '', text: sets.length ? 'Select a salvage copy' : 'No salvage copy is listed' }));
    for (const item of sets) salvagePick.append(el('option', { value: item.id, text: item.label || item.id }));
    if (salvage.name && salvage.source) salvagePick.value = `${salvage.source}:${salvage.name}`;
    salvagePick.addEventListener('change', () => {
      const chosen = sets.find((item) => item.id === salvagePick.value);
      if (!chosen) return;
      const [source, name] = String(chosen.id).split(':');
      setSalvage('source', source);
      setSalvage('name', name);
      salvageName.value = name || '';
      sync();
    });
    const salvageBox = el('div', { id: 'rescue-will-salvage' },
      field('Source', salvageSourceBox, 'Where the salvage copy is kept.'),
      field('Salvage copy', salvagePick, 'Select a set to fill its source and name.'),
      field('Copy name', salvageName, 'A set name from the copy’s own listing.'));

    form.push(group('How to restore the card', 'The rescue uses this after it has reported the trouble.', field('Method', methodBox, '', 'Do not restore'),
      el('div', { id: 'rescue-will-other-slot', hidden: (restore.method || 'none') !== 'other-slot' }, field('Slot', otherSlotBox, '', 'Auto')),
      imageBox,
      salvageBox,
      el('p', { class: 'setting-desc bad', id: 'rescue-will-restore-warn', hidden: true })));

    const whereFields = [
      ['findings', 'Findings and the report'],
      ['backups', 'Partition-table copies'],
      ['salvage', 'Salvage copies'],
    ].map(([key, label]) => choiceRow(label, `rescue-will-where-${key}`, WHERE[key].map(([value, title]) => ({ value, title, desc: '' })), () => whereValue()[key], (value) => setWhere(key, value), (d.where && d.where[key]) || ''));
    form.push(group('Where to keep what the rescue finds', 'These are places, not findings.', ...whereFields));

    const passwordState = el('p', { class: 'setting-desc', id: 'rescue-will-password-state' });
    const newPass = el('input', { type: 'password', autocomplete: 'new-password', id: 'rescue-will-new-password', placeholder: 'Leave blank to keep the saved password' });
    newPass.addEventListener('input', () => { newPassword = newPass.value; sync(); });
    form.push(group('How the rescue is reached', 'The hotspot is the page’s own network. Joining a saved network and SSH are second routes.',
      chipRow('Hotspot', () => reach.hotspot !== false, (on) => setReach('hotspot', on), 'Start the rescue hotspot.', def(!d.reach || d.reach.hotspot !== false)),
      field('Hotspot name', textInput(reach.ssid, (value) => setReach('ssid', value), { maxlength: '32' }), '1 to 32 characters.', (d.reach && d.reach.ssid) || 'irate-rescue'),
      field('New hotspot password', newPass, 'At least 8 characters. Leave it blank and the box makes a new one, shown once after Save.', ''),
      passwordState,
      chipRow('Join a saved network', () => reach.join_saved !== false, (on) => setReach('join_saved', on), 'Try the box’s saved WiFi profiles.', def(!d.reach || d.reach.join_saved !== false)),
      chipRow('SSH', () => reach.ssh === true, (on) => setReach('ssh', on), 'Run sshd in the rescue.', def(d.reach && d.reach.ssh)),
      el('p', { class: 'setting-desc bad', id: 'rescue-will-reach-warn', hidden: true })));

    form.push(group('Which roles are on', 'A role is a section of the rescue page and its helper verb.', ...ROLES.map(([key, label]) =>
      chipRow(label, () => rolesValue()[key] !== false, (on) => setRole(key, on), '', def(!d.roles || d.roles[key] !== false)))),
      el('p', { class: 'setting-desc bad', id: 'rescue-will-roles-warn', hidden: true }));

    form.push(group('Who confirms a destructive step', 'The page uses the typed words. SSH and the console use their own.',
      field('Consent', (() => {
        const box = el('div');
        AW.choices(box, 'rescue-will-consent', CONSENT, {
          value: sec.get('consent') || 'page',
          compact: true,
          onChange: (value) => { sec.set('consent', value); sync(); },
        });
        return box;
      })(), '', 'The rescue page'),
      el('p', { class: 'setting-desc bad', id: 'rescue-will-consent-warn', hidden: true })));

    form.push(group('After a repair', 'What the rescue offers once it has finished.',
      field('Restart to the card', (() => {
        const box = el('div');
        AW.choices(box, 'rescue-will-after', AFTER, {
          value: (sec.get('after') || {}).reboot_to_card || 'ask',
          compact: true,
          onChange: (value) => { sec.set('after', { ...(sec.get('after') || {}), reboot_to_card: value }); sync(); },
        });
        return box;
      })(), '', 'Ask'),
      el('p', { class: 'setting-desc bad', id: 'rescue-will-after-warn', hidden: true })));

    return form;
  }

  function sync() {
    if (!sec) return;
    const v = sec.values();
    const d = snapshot.defaults || {};
    const restore = v.restore || {};
    const image = restore.image || {};
    const salvage = restore.salvage || {};
    const reach = v.reach || {};
    const roles = v.roles || {};
    const auto = v.auto || {};

    const other = $('rescue-will-other-slot');
    if (other) other.hidden = restore.method !== 'other-slot';
    const imageBox = $('rescue-will-image');
    if (imageBox) imageBox.hidden = restore.method !== 'image';
    const salvageBox = $('rescue-will-salvage');
    if (salvageBox) salvageBox.hidden = restore.method !== 'salvage';
    const urlField = $('rescue-will-image-url');
    // The URL field is inside the image box; hide it by its label when the source is not a URL.
    const imageUrlLabel = imageBox && [...imageBox.querySelectorAll('label')].find((label) => label.textContent.includes('Image URL'));
    if (imageUrlLabel) imageUrlLabel.hidden = image.source !== 'url';

    const autoWarn = [];
    if (auto.repair_fs) autoWarn.push('The rescue will repair filesystems without asking. It can change a card before anything is copied off it.');
    if (auto.reflash) autoWarn.push('The rescue will write the restore image without asking.');
    setWarn('rescue-will-auto-warn', autoWarn.join(' '));

    const restoreWarn = [];
    if (restore.method === 'image') {
      if (!image.name) restoreWarn.push('An image needs its name.');
      if (!image.sha256) restoreWarn.push('Give the image’s SHA-256, so the rescue can verify it.');
      else if (!/^[0-9a-f]{64}$/.test(image.sha256.toLowerCase())) restoreWarn.push('The SHA-256 must be 64 hexadecimal digits.');
      if (image.source === 'url' && !/^https:\/\//.test(image.url || '')) restoreWarn.push('A URL image needs an HTTPS address.');
    }
    if (restore.method === 'salvage' && !salvage.name) restoreWarn.push('A salvage copy needs its name.');
    setWarn('rescue-will-restore-warn', restoreWarn.join(' '));

    const reachWarn = [];
    if (!reach.hotspot && !reach.join_saved && !reach.ssh) reachWarn.push('The rescue would not be reachable: the hotspot, joining a saved network and SSH are all off.');
    if (reach.hotspot && !String(reach.ssid || '').trim()) reachWarn.push('The hotspot needs a name.');
    if (String(reach.ssid || '').length > 32) reachWarn.push('The hotspot name is longer than 32 characters.');
    if (newPassword && newPassword.length < 8) reachWarn.push('A new hotspot password needs at least 8 characters.');
    setWarn('rescue-will-reach-warn', reachWarn.join(' '));

    const passState = $('rescue-will-password-state');
    if (passState) {
      const saved = snapshot.will && snapshot.will.password_set;
      passState.textContent = newPassword
        ? 'A new password will be saved with the instructions.'
        : saved
          ? 'A hotspot password is set. It is not shown here; leave the field blank to make a new one when you Save.'
          : 'The box makes a hotspot password when you first Save. It is shown once.';
    }

    const rolesOn = Object.values(roles).some((on) => on !== false);
    setWarn('rescue-will-roles-warn', rolesOn ? '' : 'No role is on, so the rescue would have nothing to do.');

    const consent = v.consent || 'page';
    setWarn('rescue-will-consent-warn', consent === 'page' ? '' : `The rescue page will not confirm a destructive step; ${consent === 'ssh' ? 'SSH' : 'the console'} does.`);

    const after = (v.after || {}).reboot_to_card || 'ask';
    setWarn('rescue-will-after-warn', after === 'always' ? 'The rescue will restart to the card after a repair without asking.' : '');

    void d;
    updatePush();
  }

  function copySaid(value) {
    return WORDS[value] || value || 'not written';
  }
  function updatePush() {
    if (!sec) return;
    const w = (snapshot && snapshot.will) || {};
    const copies = w.copies || {};
    const at = $('rescue-will-copies');
    if (at) at.textContent = `Onboard flash: ${copySaid(copies.nand)}. USB drive: ${copySaid(copies.usb)}.`;
    const confirm = ($('rescue-will-confirm') || {}).value || '';
    const ready = Boolean(w.valid) && !sec.dirty() && !sec.saving() && confirm === 'WRITE WILL' && !pushBusy;
    const button = $('rescue-will-push');
    if (button) {
      button.disabled = !ready;
      button.title = w.valid ? (sec.dirty() ? 'Save the instructions first.' : '') : 'Save the living will first.';
    }
  }

  function build() {
    const saved = snapshot.will && snapshot.will.valid ? snapshot.will.instructions : snapshot.defaults;
    sec = AW.section({
      id: 'rescue-will',
      label: 'Rescue instructions',
      fresh: true,
      saved: JSON.parse(JSON.stringify(saved || {})),
      save: async () => {
        const payload = sec.values();
        if (newPassword) payload.reach.password = newPassword;
        else delete payload.reach.password;
        const answer = await postJSON('/admin/rescue-will', { action: 'save', will: payload });
        const done = await waitResult(answer.id);
        if (!done.ok) throw new Error(resultSaid(done));
        newPassword = '';
        const at = $('rescue-will-new-password');
        if (at) at.value = '';
        say(resultSaid(done), true, $('rescue-will-note'));
        await load();
        return true;
      },
      onChange: () => { sync(); updatePush(); },
    });
    const form = $('rescue-will-form');
    form.replaceChildren(...buildFields(), sec.foot);
    sync();
    updatePush();
  }

  async function push() {
    if (!sec || sec.dirty() || pushBusy) return;
    if (($('rescue-will-confirm') || {}).value !== 'WRITE WILL') return;
    pushBusy = true;
    updatePush();
    say('Writing the living will to the onboard flash and the USB drive…', true, $('rescue-will-push-note'));
    try {
      const answer = await postJSON('/admin/rescue-will', { action: 'push', confirm: 'WRITE WILL' });
      const done = await waitResult(answer.id);
      if (!done.ok) throw new Error(resultSaid(done));
      say(resultSaid(done), true, $('rescue-will-push-note'));
      await load();
    } catch (err) {
      say(err.message || 'The living will could not be written.', false, $('rescue-will-push-note'));
    } finally {
      pushBusy = false;
      updatePush();
    }
  }

  async function load() {
    try {
      snapshot = await getJSON('/admin/rescue-will');
    } catch (err) {
      $('rescue-will-push-card').hidden = true;
      $('rescue-will-form').replaceChildren();
      state(`The rescue instructions are not available on this box yet. This page waits for the root actions will-show, will-save and will-push.`, true);
      return;
    }
    snapshot.will = snapshot.will || {};
    snapshot.defaults = snapshot.defaults || {};
    snapshot.restore_candidates = snapshot.restore_candidates || {};
    $('rescue-will-push-card').hidden = false;
    build();
    state();
  }

  $('rescue-will-confirm').addEventListener('input', updatePush);
  $('rescue-will-push').addEventListener('click', push);

  load();
}());
