#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The style audit (style-audit.html) on pages full of findings: a fresh hub has none to draw, so the page tests that
# draw them (the doctors, Security, Network) are run in jsdom here and their pages saved (dump-page.cjs), then drawn
# in headless Chromium (the irate-chrome image) with the real stylesheets, at a desktop width and a phone's. What it
# looks for on cards: a gap above a card's first line, a state inside a heading or before its card's title, a state
# as long as a sentence; and the audit's other rules. Needs Docker, and jsdom (tests/jsdom/jsdom.sh installs it).
#
#   tests/browser/card-audit.sh
set -u
cd "$(dirname "$0")/../.." || exit 2
repo=$(pwd)
image=${CHROME_IMAGE:-irate-chrome}
py=${PY_IMAGE:-python:3.12-slim}
# shellcheck source=tests/jsdom/jsdom.sh
. tests/jsdom/jsdom.sh
if ! node -e "require(process.env.JSDOM || 'jsdom')" 2>/dev/null; then
	echo "   skipped (no jsdom: npm could not install it; set JSDOM=)" >&2
	exit 0
fi
work=$(mktemp -d)
net=irate-card-audit-$$
state="$work/state"
mkdir -p "$state" "$work/site"
port=$((20000 + RANDOM % 20000))
secret=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
hub=""
# shellcheck disable=SC2329  # run by the trap below
cleanup() {
	[ -z "$hub" ] || kill "$hub" 2>/dev/null
	docker rm -f "$net-web" >/dev/null 2>&1
	docker network rm "$net" >/dev/null 2>&1
	rm -rf "$work"
}
trap cleanup EXIT
# A hub of this checkout, for the one page test that reads one (Network) and the layout's own stylesheet.
HUB_STATE_DIR="$state" PORT=$port HUB_BIND=127.0.0.1 HUB_FRONT_SECRET="$secret" "$repo/irate-box" server >"$work/hub.log" 2>&1 &
hub=$!
for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$port/status" && break; sleep 0.1; done
cp -rL web/. "$work/site/"
cp tests/browser/style-audit.html "$work/site/"
curl -s -H "X-Irate-Front: $secret" "http://127.0.0.1:$port/layout.css" >"$work/site/layout.css"
pages=()
for t in dom-health dom-box-findings dom-secdoctor dom-security-findings dom-updoctor dom-update-findings dom-net; do
	BASE="http://127.0.0.1:$port" FRONT_SECRET="$secret" timeout 300 node tests/browser/dump-page.cjs "tests/jsdom/$t.cjs" "$work/site" >/dev/null 2>&1
	for f in "$work/site/$t"-*.html; do [ -e "$f" ] && pages+=("$(basename "$f")"); done
done
docker network create "$net" >/dev/null || exit 2
docker run -d --rm --name "$net-web" --network "$net" -v "$work/site":/site:ro "$py" python3 -m http.server 8000 -d /site >/dev/null || exit 2
sleep 2
fails=0
for page in "${pages[@]}"; do
	for w in 1280 390; do
		out=$(docker run --rm --network "$net" "$image" chromium --headless=new --no-sandbox --disable-gpu --window-size=$((w + 40)),2600 \
			--virtual-time-budget=60000 --dump-dom "http://$net-web:8000/style-audit.html?static=1&w=$w&src=$page" 2>/dev/null \
			| grep -o 'data-out="[^"]*"' | sed 's/&quot;/"/g; s/&amp;/\&/g; s/&lt;/</g; s/&gt;/>/g; s/^data-out="//; s/"$//')
		if [ -z "$out" ]; then echo "FAIL $page ${w}px: no measurement"; fails=$((fails + 1)); continue; fi
		n=$(python3 - "$out" "$page" <<'PYOUT'
import json, sys
from collections import defaultdict
d, page = json.loads(sys.argv[1]), sys.argv[2]
by = defaultdict(list)
for f in d["findings"]:
    if f["rule"] == "not shown by its own link":
        continue  # a saved page has no links to follow
    by[(f["pane"], f["rule"])].append(f["what"])
for (pane, rule), whats in sorted(by.items()):
    print(f"FAIL {page} {d['width']}px #{pane}: {rule} ({len(whats)})")
    for w in whats[:3]:
        print(f"       {w}")
PYOUT
)
		[ -n "$n" ] && echo "$n"
		fails=$((fails + $(printf '%s\n' "$n" | grep -c '^FAIL')))
	done
done
echo "${#pages[@]} pages audited; failures: $fails"
exit $((fails > 0))
