#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The tests that need nothing but Python: the uplink and clock simulations, and the device-lock
# API against a hub started here on a spare port with throwaway state. tests/container/ (inside
# a systemd container with irate-box installed) and tests/jsdom/ (the pages, with jsdom) need
# more; each says how at the top.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
failed=0
run() {
	echo "== $1"
	local out rc
	out="$(python3 "$here/$1" 2>&1)"
	rc=$?
	grep -E "^(FAIL|failures)|Error" <<<"$out"
	[ "$rc" = 0 ] || failed=$((failed + 1))
}
run sim_uplink.py
run sim_rtc.py
run sim_selfupdate.py
run sim_configs.py
run sim_pin.py
run sim_local_addons.py
run api_admin_gate.py
run doctor_guard.py
run sim_netinv.py
run theme_guard.py
run root_routes_guard.py
run sim_root_links.py
run cgit_guard.py
run sim_security_image.py
run sim_forged_requests.py
run sim_git_public.py
run sim_git_levels.py

state="$(mktemp -d)"
port=$((20000 + RANDOM % 20000))
HUB_STATE_DIR="$state" PORT=$port HUB_BIND=127.0.0.1 "$repo/irate-box" server >"$state/server.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; rm -rf "$state"' EXIT
for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$port/status" && break; sleep 0.1; done
PORT=$port run api_locks.py

[ "$failed" = 0 ] && echo "all passed" || echo "$failed suite(s) failed"
exit $((failed > 0))
