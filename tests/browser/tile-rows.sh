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
card() { printf '<a class="service-card %s" href="#"><span class="icon">📚</span><span class="name">%s</span><span class="desc">%s</span></a>' "$1" "$2" "${3:-A short description.}"; }
long="The box's MQTT broker, live: topics, traffic and messages"
qr() { printf '<div class="service-card qr-card large"><span class="name">Join</span><span class="qr"><svg viewBox="0 0 29 29"><rect width="29" height="29"/></svg></span><span class="desc">http://hub.local/</span></div>'; }
declare -A layouts=(
	[beside]="$(card large Big)$(card '' A)$(card '' B)$(card '' C)$(card '' D)$(card '' E)$(card '' F)"
	[last]="$(card '' A)$(card '' B)$(card '' C)$(card '' D)$(card large Big)"
	[qr]="$(qr)$(card '' A)$(card '' B)$(card '' C)$(card '' D)"
	[home]="$(card '' A "$long")$(card '' B "$long")$(card '' C "$long")$(card '' D)$(qr)$(card '' E)$(card '' F)$(card large Big)"
)
fails=0
for width in 900 600; do
	for name in beside last qr home; do
		cat > "$work/$name.html" <<HTMLEND
<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="palette.css"><link rel="stylesheet" href="style.css">
<style>body{margin:0;padding:16px;width:${width}px}</style></head><body><main><div class="service-grid" id="g">${layouts[$name]}</div></main>
<script>
const g = document.getElementById('g'), gap = parseFloat(getComputedStyle(g).rowGap);
const hs = [...g.children].map((c) => [c.classList.contains('large'), c.getBoundingClientRect().height]);
// The QR on a large tile: as big as the tile lets it be, square, inside the tile (its height less the name and the address).
const qrCard = g.querySelector('.qr-card'), qrSvg = qrCard && qrCard.querySelector('svg');
const qr = qrSvg && (() => { const c = qrCard.getBoundingClientRect(), s = qrSvg.getBoundingClientRect();
  return { fill: +(s.height / c.height).toFixed(2), square: Math.abs(s.width - s.height) <= 1, inside: s.top >= c.top && s.bottom <= c.bottom && s.right <= c.right }; })();
// Every normal tile one row's height (the tallest one's), and every large tile two of those rows and the gap.
const normals = hs.filter(([l]) => !l).map(([, h]) => h), larges = hs.filter(([l]) => l).map(([, h]) => h);
const normal = Math.max(...normals);
document.body.dataset.out = JSON.stringify({ normal, larges, qr, ok: normals.every((h) => Math.abs(h - normal) <= 1)
  && larges.every((h) => Math.abs(h - (2 * normal + gap)) <= 2) && (!qr || (qr.fill >= 0.6 && qr.square && qr.inside)) });
</script></body></html>
HTMLEND
		out=$(docker run --rm -v "$work":/p "$image" chromium --headless=new --no-sandbox --disable-gpu --window-size=$((width + 40)),1200 \
			--virtual-time-budget=2000 --dump-dom "file:///p/$name.html" 2>/dev/null | grep -o 'data-out="[^"]*"' | sed 's/&quot;/"/g; s/^data-out="//; s/"$//')
		if [[ $out == *'"ok":true'* ]]; then echo "PASS ${width}px, large ${name}: $out"; else echo "FAIL ${width}px, large ${name}: ${out:-no measurement}"; fails=$((fails + 1)); fi
	done
done
# On a phone: a large tile one full-width row, and the others not stretched to its height. Measured in a 400 px frame,
# as headless Chromium keeps its window 500 px wide at the least, and the frame's width is what the media query reads.
cat > "$work/phone-inner.html" <<HTMLEND
<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="palette.css"><link rel="stylesheet" href="style.css">
<style>body{margin:0;padding:16px}</style></head><body><main><div class="service-grid" id="g">${layouts[home]}</div></main>
<script>
const hs = [...document.getElementById('g').children].map((c) => [c.classList.contains('qr-card'), c.getBoundingClientRect().height]);
const qrH = hs.find(([q]) => q)[1], rest = Math.max(...hs.filter(([q]) => !q).map(([, h]) => h));
parent.postMessage(JSON.stringify({ width: innerWidth, qr: qrH, others: rest, ok: innerWidth <= 480 && rest < qrH - 40 }), '*');
</script></body></html>
HTMLEND
cat > "$work/phone.html" <<HTMLEND
<!doctype html><html><body><iframe src="phone-inner.html" width="400" height="1600"></iframe>
<script>addEventListener('message', (e) => { document.body.dataset.out = e.data; });</script></body></html>
HTMLEND
out=$(docker run --rm -v "$work":/p "$image" chromium --headless=new --no-sandbox --disable-gpu --window-size=400,1600 \
	--virtual-time-budget=2000 --dump-dom "file:///p/phone.html" 2>/dev/null | grep -o 'data-out="[^"]*"' | sed 's/&quot;/"/g; s/^data-out="//; s/"$//')
if [[ $out == *'"ok":true'* ]]; then echo "PASS phone: other tiles not stretched to the QR's height: $out"; else echo "FAIL phone: ${out:-no measurement}"; fails=$((fails + 1)); fi
echo "failures: $fails"
exit $((fails > 0))
