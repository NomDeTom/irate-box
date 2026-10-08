// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Makes web/emoji-data.js, the emoji picker's list, from Unicode's emoji-test.txt (any version: each
// line says the release that added it). Unicode's groups and order, fully-qualified emoji only, none
// newer than MAX (phones older than that release draw a newer emoji as an empty box), and for each
// emoji that takes a skin tone its five toned forms, exactly as Unicode lists them.
// Usage: node tools/make-emoji-data.cjs path/to/emoji-test.txt > web/emoji-data.js
const fs = require('fs');

const MAX = 13.1;   // Emoji 13.1 (2020): iOS 14.5, Android 12
const TONES = ['light', 'medium-light', 'medium', 'medium-dark', 'dark'];
const ICONS = { 'Smileys & Emotion': '😀', 'People & Body': '👋', 'Animals & Nature': '🐻', 'Food & Drink': '🍔',
  'Travel & Places': '✈️', Activities: '⚽', Objects: '💡', Symbols: '🔣', Flags: '🏁' };

const src = fs.readFileSync(process.argv[2], 'utf8');
const version = (src.match(/^# Version: ([\d.]+)/m) || [])[1];
const groups = [];
let group = null;
const byName = new Map();
for (const line of src.split('\n')) {
  const g = line.match(/^# group: (.+)$/);
  if (g) { group = g[1] === 'Component' ? null : { label: g[1], icon: ICONS[g[1]], entries: [] }; if (group) groups.push(group); continue; }
  const m = line.match(/^[0-9A-F ]+; fully-qualified\s+# (\S+) E(\d+\.\d+) (.+)$/);
  if (!m || !group || Number(m[2]) > MAX) continue;
  const [, ch, , name] = m;
  const toned = name.match(/^(.+): (light|medium-light|medium|medium-dark|dark) skin tone$/);
  if (toned) {
    // One tone for the whole emoji (mixed tones, such as two people's, are left out).
    const base = byName.get(toned[1]);
    if (base) (base.tones = base.tones || {})[toned[2]] = ch;
    continue;
  }
  if (/skin tone/.test(name)) continue;
  const entry = { ch, name };
  byName.set(name, entry);
  group.entries.push(entry);
}

const out = groups.map((g) => [g.label, g.icon, g.entries.map((e) => {
  const tones = e.tones && TONES.every((t) => e.tones[t]) ? TONES.map((t) => e.tones[t]) : null;
  return tones ? [e.ch, e.name, tones] : [e.ch, e.name];
})]);
const count = out.reduce((n, g) => n + g[2].length, 0);
process.stdout.write(`// SPDX-License-Identifier: Unicode-3.0
// SPDX-FileCopyrightText: 1991-2025 Unicode, Inc.
// Made by tools/make-emoji-data.cjs from Unicode's emoji-test.txt ${version}: ${count} emoji up to Emoji ${MAX}, in
// Unicode's groups, each [emoji, name] or [emoji, name, [its five skin tones, light to dark]]. Do not edit by hand.
window.EMOJI_DATA = { version: '${MAX}', groups: ${JSON.stringify(out)} };
`);
