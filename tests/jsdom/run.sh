#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Every page test here (dom-*.cjs) in jsdom, the ones that need a hub against one started here on a spare port
# with throwaway state, as tests/run.sh starts its own, and the front's secret set (as nginx sends it). jsdom is not the repository's: give its folder with
# JSDOM=/path/to/node_modules/jsdom, or have it where node finds it (NODE_PATH). CI installs a pinned one.
#   tests/jsdom/run.sh             all of them
#   tests/jsdom/run.sh dom-kits    the ones whose names contain dom-kits
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
failed=0
run_tmp="$(mktemp -d "${TMPDIR:-/tmp}/irate-box-pages.XXXXXX")"
export TMPDIR="$run_tmp"
state="$run_tmp/state"
mkdir -p "$state"
port=$((20000 + RANDOM % 20000))
# The front's secret, as the web server sends it: tests of /admin (dom-net) send it too and are answered as admin.
FRONT_SECRET="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export FRONT_SECRET
HUB_STATE_DIR="$state" PORT=$port HUB_BIND=127.0.0.1 HUB_FRONT_SECRET="$FRONT_SECRET" "$repo/irate-box" server >"$run_tmp/server.log" 2>&1 &
pid=$!
cleanup() {
	kill "$pid" 2>/dev/null
	rm -rf "$run_tmp"
}
trap cleanup EXIT
for _ in $(seq 50); do
	curl -s -o /dev/null "http://127.0.0.1:$port/status" && break
	sleep 0.1
done
export BASE="http://127.0.0.1:$port"
for t in "$here"/dom-*.cjs; do
	name="$(basename "$t")"
	[[ "$name" == *"${1:-}"* ]] || continue
	echo "== $name"
	out="$(timeout 300 node "$t" 2>&1)"
	rc=$?
	grep -E "^ *FAIL|^failures" <<<"$out"
	if [ "$rc" != 0 ]; then
		failed=$((failed + 1))
		# A crash says no FAIL line: its last lines say why.
		grep -qE "^ *FAIL" <<<"$out" || tail -5 <<<"$out"
	fi
done
[ "$failed" = 0 ] && echo "all passed" || echo "$failed page test(s) failed"
[ "$failed" = 0 ]
