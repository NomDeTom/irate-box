// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Makes web/emoji-data.js, the emoji picker's list, from Unicode's emoji-test.txt (any version: each
// line says the release that added it). Unicode's groups and order, fully-qualified emoji only, none
// newer than MAX, and for each emoji that takes a skin tone its five toned forms, exactly as Unicode
// lists them. An emoji (or its tones) newer than BASE carries its release, so the picker can leave
// out what the owner's chosen set lacks (/admin → Appearance): a phone older than a release draws its
// emoji as empty boxes.
// Usage: node tools/make-emoji-data.cjs path/to/emoji-test.txt > web/emoji-data.js
const fs = require('fs');

const BASE = 13.1;  // Emoji 13.1 (2020): iOS 14.5, Android 12. The default set; older entries carry no release.
const MAX = 16.0;   // Emoji 16.0 (2024): iOS 18.4, Android 16. The newest set offered.
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
  const [, ch, ver, name] = m;
  const toned = name.match(/^(.+): (light|medium-light|medium|medium-dark|dark) skin tone$/);
  if (toned) {
    // One tone for the whole emoji (mixed tones, such as two people's, are left out).
    const base = byName.get(toned[1]);
    if (base) { (base.tones = base.tones || {})[toned[2]] = ch; base.tonesVer = Math.max(base.tonesVer || 0, Number(ver)); }
    continue;
  }
  if (/skin tone/.test(name)) continue;
  const entry = { ch, name, ver: Number(ver) };
  byName.set(name, entry);
  group.entries.push(entry);
}

const out = groups.map((g) => [g.label, g.icon, g.entries.map((e) => {
  const tones = e.tones && TONES.every((t) => e.tones[t]) ? TONES.map((t) => e.tones[t]) : null;
  // [emoji, name, tones or 0, its release or 0, its tones' release or 0], the trailing zeros left off.
  const row = [e.ch, e.name, tones || 0, e.ver > BASE ? e.ver : 0, tones && e.tonesVer > BASE && e.tonesVer > e.ver ? e.tonesVer : 0];
  while (row.length > 2 && row[row.length - 1] === 0) row.pop();
  return row;
})]);
const count = out.reduce((n, g) => n + g[2].length, 0);
process.stdout.write(`// SPDX-License-Identifier: Unicode-3.0
// SPDX-FileCopyrightText: 1991-2025 Unicode, Inc.
// Made by tools/make-emoji-data.cjs from Unicode's emoji-test.txt ${version}: ${count} emoji up to Emoji ${MAX}, in
// Unicode's groups, each [emoji, name, its five skin tones light to dark or 0, its release if newer than ${BASE},
// its tones' release if newer still] with the trailing zeros left off. Do not edit by hand.
window.EMOJI_DATA = { base: ${BASE}, max: ${MAX}, groups: ${JSON.stringify(out)} };
`);
