#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The theme picker's looks and the colours it chooses, measured in headless Chromium (the irate-chrome image):
#  - a button not chosen and one chosen look different, and each looks different again under the pointer (a copy of
#    style.css with :hover as a class, as a headless page has no pointer to hover with);
#  - the box's own colours (as /layout.css writes them) apply with no palette of the visitor's; Standard and Increased
#    contrast (data-palette) win over them, in dark and in light.
#
#   tests/browser/theme-picker.sh
set -u
cd "$(dirname "$0")/../.." || exit 2
image=${CHROME_IMAGE:-irate-chrome}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp web/palette.css "$work/"
sed 's/:hover/.hov/g' web/style.css > "$work/style.css"
# The box's palettes as the hub writes them: a teal accent in each mode.
python3 -c 'import sys; sys.path.insert(0, "."); from irate_box.hub.server import palette_css
print(palette_css({"dark": {"accent": "#2dd4bf"}, "light": {"accent": "#0f766e"}}))' > "$work/layout.css" || exit 2
cat > "$work/t.html" <<'HTMLEND'
<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><link rel="stylesheet" href="palette.css"><link rel="stylesheet" href="style.css">
<link rel="stylesheet" href="layout.css"><style>* { transition: none !important; }</style></head><body><header style="position:relative;height:60px">
<div class="theme-picker"><button id="off">☀️</button><button id="on" aria-pressed="true">🌙</button>
<div class="theme-menu"><button id="m-off" role="menuitemradio" aria-checked="false"><span class="theme-menu-name">A</span></button>
<button id="m-on" role="menuitemradio" aria-checked="true"><span class="theme-menu-name">B</span></button></div></div></header>
<script>
const look = (id, hov) => { const b = document.getElementById(id); b.classList.toggle('hov', hov);
  const s = getComputedStyle(b); const out = [s.backgroundColor, s.color, s.boxShadow].join('|'); b.classList.remove('hov'); return out; };
const states = {};
for (const id of ['off', 'on', 'm-off', 'm-on']) states[id] = [look(id, false), look(id, true)];
const looks = Object.fromEntries(Object.entries(states).map(([k, [a, b]]) => [k, a !== b]));
const root = document.documentElement, accent = () => getComputedStyle(root).getPropertyValue('--accent').trim(),
  muted = () => getComputedStyle(root).getPropertyValue('--muted').trim();
const colours = {};
for (const [theme, pal] of [['dark', null], ['dark', 'standard'], ['dark', 'low'], ['dark', 'contrast'], ['light', null], ['light', 'standard'], ['light', 'low'], ['light', 'contrast']]) {
  root.setAttribute('data-theme', theme);
  if (pal) root.setAttribute('data-palette', pal); else root.removeAttribute('data-palette');
  colours[`${theme}/${pal || 'box'}`] = `${accent()} ${muted()}`;
}
document.body.dataset.out = JSON.stringify({ hover: looks, chosen: states.off[0] !== states.on[0] && states['m-off'][0] !== states['m-on'][0], colours });
</script></body></html>
HTMLEND
out=$(docker run --rm -v "$work":/p "$image" chromium --headless=new --no-sandbox --disable-gpu --window-size=800,600 \
	--virtual-time-budget=2000 --dump-dom "file:///p/t.html" 2>/dev/null | grep -o 'data-out="[^"]*"' | sed 's/&quot;/"/g; s/^data-out="//; s/"$//')
fails=0
check() { if python3 -c "import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if ($2) else 1)" "$out"; then echo "PASS $1"; else echo "FAIL $1: ${out:-no measurement}"; fails=$((fails + 1)); fi; }
check "not chosen and chosen look different, in the row and in the menu" 'd["chosen"]'
check "each of the four answers the pointer" 'all(d["hover"].values())'
check "the box's colours with no palette of the visitor's, in dark and light" 'd["colours"]["dark/box"].startswith("#2dd4bf") and d["colours"]["light/box"].startswith("#0f766e")'
check "Standard: the hub's own colours over the box's" 'd["colours"]["dark/standard"] == "#9d8ff9 #8a8fa8" and d["colours"]["light/standard"] == "#5b4bd6 #5f6380"'
check "Low contrast: the quieter colours, over the box's" 'd["colours"]["dark/low"] == "#7c6af7 #6b6f8a" and d["colours"]["light/low"] == "#5b4bd6 #6b6f8a"'
check "High contrast: its own, over the box's" 'd["colours"]["dark/contrast"] == "#bdb3ff #b8bcd0" and d["colours"]["light/contrast"] == "#3a2aa8 #43475c"'
echo "failures: $fails"
exit $((fails > 0))
