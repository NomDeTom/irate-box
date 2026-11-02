#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The phone menu: /admin in a frame 390 x 740, the ☰ opened, and the last link of the open list scrolled
# to within the list while the page behind and the ☰ stay put (tests/browser/menu-mobile.html). The hub
# runs in a container with throwaway state, behind the same stand-in for the web server as style-audit.sh.
#
#   tests/browser/menu-mobile.sh
set -u
cd "$(dirname "$0")/../.." || exit 2
repo=$(pwd)
image=${CHROME_IMAGE:-irate-chrome}
py=${PY_IMAGE:-python:3.12-slim}
net=irate-menu-mobile-$$
secret=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
cleanup() {
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
out=$(docker run --rm --network "$net" "$image" chromium --headless=new --no-sandbox --disable-gpu --window-size=390,740 \
	--virtual-time-budget=60000 --dump-dom "http://$net-front:8080/__test/menu-mobile.html" 2>/dev/null \
	| grep -o 'data-out="[^"]*"' | sed 's/&quot;/"/g; s/&amp;/\&/g; s/&lt;/</g; s/&gt;/>/g; s/^data-out="//; s/"$//')
if [ -z "$out" ]; then
	echo "FAIL the page did not finish"
	exit 1
fi
python3 - "$out" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
checks = [
    ("the ☰ opens the panel", d["open"]),
    ("the ☰ is in view", d["menuInView"]),
    ("the open list scrolls by itself", d["overflows"]),
    ("  its last link scrolls into view within the list", d["lastInList"]),
    ("  the page behind it does not move", d["pageStill"]),
    ("  the ☰ stays in view", d["menuStillInView"]),
    ("closed, the page scrolled far down", d["pageScrolled"]),
    ("  the ☰ is still in view", d["menuAfterScroll"]),
]
fails = 0
for name, ok in checks:
    print(("PASS " if ok else "FAIL ") + name)
    fails += not ok
print(f"{d['clientHeight']} px of list shown, {d['scrollHeight']} px of list")
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
PY
