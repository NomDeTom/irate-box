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
# Every temporary file and folder the tests make goes in one folder for the run (they all take
# TMPDIR: Python's tempfile, mktemp), removed at the end, pass or fail: the tests leave theirs.
run_tmp="$(mktemp -d "${TMPDIR:-/tmp}/irate-box-tests.XXXXXX")"
export TMPDIR="$run_tmp"
pid=""
cleanup() { [ -z "$pid" ] || kill "$pid" 2>/dev/null; rm -rf "$run_tmp"; }
trap cleanup EXIT
run() {
	echo "== $1"
	local out rc
	out="$(python3 "$here/$1" 2>&1)"
	rc=$?
	grep -E "^(FAIL|failures)|Error" <<<"$out"
	[ "$rc" = 0 ] || failed=$((failed + 1))
}
run sim_uplink.py
run sim_crashwatch.py
run sim_usb_filesystems.py
run sim_doctor_choices.py
run sim_sbdocs.py
run sim_pkgwatch.py
run sim_update_pattern.py
run sim_wifijoin.py
run sim_nmconf.py
run sim_nmhandover.py
run sim_nmconnection.py
run sim_netpolicy.py
run sim_otherap.py
run sim_roaming.py
run sim_backup.py
run sim_declared_services.py
run sim_one_login.py
run sim_account_nav.py
run sim_secdoctor_accounts.py
run sim_helper_busy.py
run sim_widths.py
run sim_admin_apps.py
run sim_visibility.py
run sim_folders.py
run sim_visitors.py
run sim_status_tiles.py
run sim_reports.py
run sim_prefs.py
run sim_tour.py
run sim_seen.py
run sim_svchistory.py
run sim_rtc.py
run sim_tls.py
run sim_mesh.py
run sim_accounts.py
run sim_selfupdate.py
run sim_configs.py
run sim_books.py
run sim_ghpace.py
run sim_pin.py
run sim_local_addons.py
run api_admin_gate.py
run doctor_guard.py
run sim_netinv.py
run sim_apmode.py
run sim_syncthing_gui.py
run sim_confine.py
run sim_reach.py
run theme_guard.py
run root_routes_guard.py
run sim_root_links.py
run cgit_guard.py
run sim_security_image.py
run sim_forged_requests.py
run sim_git_public.py
run sim_git_levels.py
run origin_guard.py
run unit_guard.py
run sim_mirrors.py
run sim_ci_view.py
run sim_factory.py
run sim_toolkits.py
run sim_kits_usb.py
run sim_secdoctor_debsecan.py
run sim_deepaudit.py
run sim_image_step.py
run sim_offline_step.py
run sim_firewall.py
run sim_secdoctor_joint.py
run sim_secdoctor_lyra.py
run sim_signing.py
run sim_share.py
run sim_guest_net.py
run sim_overview_net.py
run guide_guard.py

state="$(mktemp -d)"
port=$((20000 + RANDOM % 20000))
HUB_STATE_DIR="$state" PORT=$port HUB_BIND=127.0.0.1 "$repo/irate-box" server >"$state/server.log" 2>&1 &
pid=$!
for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$port/status" && break; sleep 0.1; done
PORT=$port run api_locks.py

# A warning, not a failure: names, plan references and dated comments in the code (history_guard.py --strict fails).
echo "== history_guard.py"
python3 "$here/history_guard.py" | tail -1

[ "$failed" = 0 ] && echo "all passed" || echo "$failed suite(s) failed"
exit $((failed > 0))
