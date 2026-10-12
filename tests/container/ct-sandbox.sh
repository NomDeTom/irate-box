#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The root units' sandboxes: each carries its lines (the uplink watchdog all but ProtectSystem),
# and still starts and stays up inside them. Usage (in the container, after install.sh): ct-sandbox.sh
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
prop() { systemctl show -p "$2" --value "$1"; }

for u in irate-box-crashwatch irate-box-uplink irate-box-firewall; do
	systemctl cat "$u.service" >/dev/null 2>&1 || { ok "$u: not installed here"; continue; }
	want_ps=strict
	[ "$u" = irate-box-uplink ] && want_ps=no
	got="$(prop "$u" ProtectSystem) $(prop "$u" NoNewPrivileges) $(prop "$u" PrivateTmp) $(prop "$u" ProtectHome) $(prop "$u" ProtectProc)"
	[ "$got" = "$want_ps yes yes yes invisible" ] && ok "$u: its sandbox lines" || bad "$u: $got"
done
sleep 5
for u in irate-box-crashwatch irate-box-uplink; do
	st=$(systemctl is-active "$u")
	n=$(prop "$u" NRestarts)
	[ "$st" = active ] && [ "${n:-0}" = 0 ] && ok "$u: up inside it, no restarts" ||
		{ bad "$u: $st, $n restarts"; journalctl -u "$u" -n 8 --no-pager | sed 's/^/       /'; }
done
cw=$(systemctl show -p ReadWritePaths --value irate-box-crashwatch)
case "$cw" in *sysctl.d*boot*) ok "crash watch: its write paths" ;; *) bad "crash watch's write paths: $cw" ;; esac

echo "== result: $fails failed"
exit $((fails > 0))
