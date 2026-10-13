#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The style audit: every /admin pane of this checkout, drawn in headless Chromium (the irate-chrome image) at a
# desktop width and a phone's, looked over for what makes a page look broken: a link in the browser's default colour,
# loose text at the page's full size, a gap above a card's first line, something wider than its pane, a list of terms
# loose in a pane (in no card). The hub runs in a container with throwaway state, behind a stand-in for
# the web server (front-proxy.py) on a network of their own; nothing on the machine is touched.
#
#   tests/browser/style-audit.sh            all panes, both widths
#   KEEP=1 tests/browser/style-audit.sh     leave the containers running after (to look at a page by hand)
set -u
cd "$(dirname "$0")/../.." || exit 2
repo=$(pwd)
image=${CHROME_IMAGE:-irate-chrome}
py=${PY_IMAGE:-python:3.12-slim}
net=irate-style-audit-$$
secret=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
cleanup() {
	[ "${KEEP:-0}" = 1 ] && { echo "left running: network $net, containers $net-hub $net-front"; return; }
	docker rm -f "$net-hub" "$net-front" >/dev/null 2>&1
	docker network rm "$net" >/dev/null 2>&1
}
trap cleanup EXIT
docker network create "$net" >/dev/null || exit 2
docker run -d --rm --name "$net-hub" --network "$net" -e HUB_FRONT_SECRET="$secret" -e HUB_STATE_DIR=/tmp/st -e HUB_ETC_DIR=/tmp/st \
	-e PORT=8000 -e HUB_BIND=0.0.0.0 -v "$repo":/repo:ro "$py" sh -c 'mkdir -p /tmp/st && exec /repo/irate-box server' >/dev/null || exit 2
docker run -d --rm --name "$net-front" --network "$net" -e FRONT_SECRET="$secret" -v "$repo/tests/browser":/t:ro "$py" \
	python3 -u /t/front-proxy.py 8080 "$net-hub" 8000 /t >/dev/null || exit 2
sleep 4
fails=0
for w in 1280 390; do
	out=$(docker run --rm --network "$net" "$image" chromium --headless=new --no-sandbox --disable-gpu --window-size=$((w + 40)),2600 \
		--virtual-time-budget=120000 --dump-dom "http://$net-front:8080/__test/style-audit.html?w=$w" 2>/dev/null \
		| grep -o 'data-out="[^"]*"' | sed 's/&quot;/"/g; s/&amp;/\&/g; s/&lt;/</g; s/&gt;/>/g; s/^data-out="//; s/"$//')
	if [ -z "$out" ]; then echo "FAIL ${w}px: no measurement (the page did not finish)"; fails=$((fails + 1)); continue; fi
	n=$(python3 - "$out" <<'PYOUT'
import json, sys
from collections import defaultdict
d = json.loads(sys.argv[1])
by = defaultdict(list)
for f in d["findings"]:
    by[(f["pane"], f["rule"])].append(f["what"])
for (pane, rule), whats in sorted(by.items()):
    print(f"FAIL {d['width']}px #{pane}: {rule} ({len(whats)})")
    for w in whats[:4]:
        print(f"       {w}")
print(f"{d['width']}px: {d['panes']} panes, {len(d['findings'])} findings", file=sys.stderr)
print(len(by), file=sys.stderr)
PYOUT
)
	echo "$n"
	fails=$((fails + $(printf '%s\n' "$n" | grep -c '^FAIL')))
done
echo "failures: $fails"
exit $((fails > 0))
