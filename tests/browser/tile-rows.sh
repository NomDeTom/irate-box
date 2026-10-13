#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# A large tile is two rows and the gap between them, wherever it lands: measured in headless Chromium
# (the irate-chrome image) on a page of web/style.css with normal, large and QR tiles, at two widths.
# A large tile last in the grid, its second row otherwise empty, is the case content-sized rows got wrong.
#
#   tests/browser/tile-rows.sh
set -u
cd "$(dirname "$0")/../.." || exit 2
image=${CHROME_IMAGE:-irate-chrome}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp web/style.css web/palette.css "$work/"
card() { printf '<a class="service-card %s" href="#"><span class="icon">📚</span><span class="name">%s</span><span class="desc">A short description.</span></a>' "$1" "$2"; }
qr() { printf '<div class="service-card qr-card large"><span class="name">Join</span><span class="qr"><svg viewBox="0 0 29 29"><rect width="29" height="29"/></svg></span><span class="desc">http://hub.local/</span></div>'; }
declare -A layouts=(
	[beside]="$(card large Big)$(card '' A)$(card '' B)$(card '' C)$(card '' D)$(card '' E)$(card '' F)"
	[last]="$(card '' A)$(card '' B)$(card '' C)$(card '' D)$(card large Big)"
	[qr]="$(qr)$(card '' A)$(card '' B)$(card '' C)$(card '' D)"
)
fails=0
for width in 900 600; do
	for name in beside last qr; do
		cat > "$work/$name.html" <<HTMLEND
<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="palette.css"><link rel="stylesheet" href="style.css">
<style>body{margin:0;padding:16px;width:${width}px}</style></head><body><main><div class="service-grid" id="g">${layouts[$name]}</div></main>
<script>
const g = document.getElementById('g'), gap = parseFloat(getComputedStyle(g).rowGap);
const hs = [...g.children].map((c) => [c.classList.contains('large'), c.getBoundingClientRect().height]);
const normal = hs.find(([l]) => !l)[1], large = hs.find(([l]) => l)[1];
document.body.dataset.out = JSON.stringify({ normal, large, ok: Math.abs(large - (2 * normal + gap)) <= 2 });
</script></body></html>
HTMLEND
		out=$(docker run --rm -v "$work":/p "$image" chromium --headless=new --no-sandbox --disable-gpu --window-size=$((width + 40)),1200 \
			--virtual-time-budget=2000 --dump-dom "file:///p/$name.html" 2>/dev/null | grep -o 'data-out="[^"]*"' | sed 's/&quot;/"/g; s/^data-out="//; s/"$//')
		if [[ $out == *'"ok":true'* ]]; then echo "PASS ${width}px, large ${name}: $out"; else echo "FAIL ${width}px, large ${name}: ${out:-no measurement}"; fails=$((fails + 1)); fi
	done
done
echo "failures: $fails"
exit $((fails > 0))
