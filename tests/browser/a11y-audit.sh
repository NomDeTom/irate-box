#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The accessibility audit: axe-core's WCAG 2.0 and 2.1 rules (A and AA) on every hub page and every /admin page, in
# each theme (Light, Dark, E-ink, Cybercore) and palette (standard, Low and High contrast), in headless Chromium through
# Playwright on this machine (no Docker). A hub of this checkout runs on a spare port with throwaway state, as
# tests/jsdom/run.sh starts one, and answers as admin to the front's secret. Part of the manual local suite: not CI.
#
#   tests/browser/a11y-audit.sh                         everything (about 15 minutes)
#   A11Y_THEMES=light A11Y_PALETTES=standard tests/browser/a11y-audit.sh    one theme, one palette
#   A11Y_PAGES=Network tests/browser/a11y-audit.sh      the pages whose path or admin title holds the words
#   A11Y_JSON=out.jsonl tests/browser/a11y-audit.sh     every failing element as JSON lines too
#
# Needs Node and Playwright: PLAYWRIGHT=/path/to/node_modules/playwright, or Playwright where node finds it
# (NODE_PATH). CHROME=/path/to/chrome-headless-shell to use a Chromium other than Playwright's own. Without
# Playwright it says so and stops (exit 0). axe-core (MPL-2.0) is not in the repository: the pinned version is fetched
# once into ~/.cache/irate-box and checked against npm's sha512 before it is used.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
AXE_VERSION=4.14.0
AXE_SHA512=9WTZxEjsZ7b13TH8JPmbV2z8CHbl80/2hm3XPEG4JgNdQLK81IBRXmSxHfMAOkSqQeRxT/0dwNDz2GOm3zzpcQ==

pw="${PLAYWRIGHT:-}"
if [ -z "$pw" ]; then
	pw=$(node -e 'console.log(require.resolve("playwright").replace(/\/index\.js$/, ""))' 2>/dev/null) || pw=""
fi
if [ -z "$pw" ] || [ ! -d "$pw" ]; then
	echo "skipped: no Playwright (set PLAYWRIGHT=/path/to/node_modules/playwright, or NODE_PATH)"
	exit 0
fi

cache="${XDG_CACHE_HOME:-$HOME/.cache}/irate-box/axe-core-$AXE_VERSION"
if [ ! -f "$cache/axe.min.js" ]; then
	mkdir -p "$cache" || exit 2
	tgz="$cache/axe-core.tgz"
	curl -fsSL "https://registry.npmjs.org/axe-core/-/axe-core-$AXE_VERSION.tgz" -o "$tgz" || { echo "could not fetch axe-core $AXE_VERSION"; exit 2; }
	got=$(openssl dgst -sha512 -binary "$tgz" | base64 -w0)
	if [ "$got" != "$AXE_SHA512" ]; then
		rm -f "$tgz"
		echo "axe-core $AXE_VERSION: the download does not match npm's sha512; not used"
		exit 2
	fi
	tar -xzf "$tgz" -C "$cache" package/axe.min.js package/LICENSE && mv "$cache/package/axe.min.js" "$cache/package/LICENSE" "$cache/" && rm -rf "$cache/package" "$tgz" || exit 2
fi

run_tmp="$(mktemp -d "${TMPDIR:-/tmp}/irate-box-a11y.XXXXXX")"
port=$((20000 + RANDOM % 20000))
secret="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
mkdir -p "$run_tmp/state"
HUB_STATE_DIR="$run_tmp/state" PORT=$port HUB_BIND=127.0.0.1 HUB_FRONT_SECRET="$secret" "$repo/irate-box" server >"$run_tmp/server.log" 2>&1 &
pid=$!
cleanup() {
	kill "$pid" 2>/dev/null
	rm -rf "$run_tmp"
}
trap cleanup EXIT
for _ in $(seq 50); do
	curl -s -o /dev/null "http://127.0.0.1:$port/status" && break
	sleep 0.2
done

BASE="http://127.0.0.1:$port" FRONT_SECRET="$secret" AXE="$cache/axe.min.js" PLAYWRIGHT="$pw" node "$here/a11y-audit.cjs"
