#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The vertical-spacing audit: every /admin page and the sidebar in headless Chromium through Playwright on this
# machine (no Docker). A hub of this checkout runs on a spare port with throwaway state and answers as admin to the
# front's secret. Reports cards and buttons that sit on the text above or below them, or gaps outside their band.
#
#   tests/browser/spacing-audit.sh                     both widths (1280 and 390)
#   SPACING_WIDTHS=390 tests/browser/spacing-audit.sh  one width
#   SPACING_JSON=out.jsonl tests/browser/spacing-audit.sh
#
# Needs Node and Playwright: PLAYWRIGHT=/path/to/node_modules/playwright, or Playwright where node finds it (NODE_PATH).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
pw="${PLAYWRIGHT:-}"
if [ -z "$pw" ]; then
	pw=$(node -e 'console.log(require.resolve("playwright").replace(/\/index\.js$/, ""))' 2>/dev/null) || pw=""
fi
if [ -z "$pw" ] || [ ! -d "$pw" ]; then
	# A required run (CI's spacing job) must measure, not pass because the browser is missing.
	if [ "${SPACING_REQUIRED:-0}" = 1 ]; then
		echo "spacing audit failed: no Playwright (set PLAYWRIGHT=/path/to/node_modules/playwright, or NODE_PATH)" >&2
		exit 1
	fi
	echo "skipped: no Playwright (set PLAYWRIGHT=/path/to/node_modules/playwright, or NODE_PATH)"
	exit 0
fi
run_tmp="$(mktemp -d "${TMPDIR:-/tmp}/irate-box-spacing.XXXXXX")"
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
BASE="http://127.0.0.1:$port" FRONT_SECRET="$secret" PLAYWRIGHT="$pw" node "$here/spacing-audit.cjs"
