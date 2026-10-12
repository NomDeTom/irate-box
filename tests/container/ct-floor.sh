#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The floor, as a guest on the hotspot meets it: a network namespace stands in for a phone, on a veth pair whose
# box end is named ap0 (the hotspot's interface, so the ruleset's matches are the real ones). At each level, what
# the phone can reach: the hub, the apps' own origin, and a service of the box's that nobody declared.
# Usage (in the container, after install.sh): ct-floor.sh
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
cleanup() { ip netns del phone 2>/dev/null; ip link del ap0 2>/dev/null; [ -n "${other:-}" ] && kill "$other" 2>/dev/null; }
trap cleanup EXIT
ip link add ap0 type veth peer name phone0 && ip netns add phone && ip link set phone0 netns phone || { echo "no netns here: skipped"; exit 0; }
ip addr add 192.168.4.1/24 dev ap0 && ip link set ap0 up
ip -n phone addr add 192.168.4.2/24 dev phone0 && ip -n phone link set phone0 up && ip -n phone link set lo up
python3 -m http.server 4444 --bind 0.0.0.0 >/dev/null 2>&1 & other=$!
sleep 1
reach() { ip netns exec phone timeout 4 bash -c "</dev/tcp/192.168.4.1/$1" 2>/dev/null && echo open || echo shut; }
floor() { (cd /opt/irate-box && python3 -c "import sys; sys.path.insert(0, '.'); from irate_box.root import security; print(security.fix(sys.argv[1], None))" "$1") 2>&1 | tail -1; }
want() { local w=$1 port=$2 what=$3 g; g=$(reach "$port"); [ "$g" = "$w" ] && ok "$what (:$port) $g" || bad "$what (:$port) $g, want $w"; }

echo "== no floor: everything that listens"
want open 80 "the hub"; want open 8090 "the apps' origin"; want open 4444 "an undeclared service"
echo "== hub and apps: $(floor firewall-apps)"
want open 80 "the hub"; want open 8090 "the apps' origin"; want shut 4444 "an undeclared service"
echo "== hub only: $(floor firewall-hub)"
want open 80 "the hub"; want shut 8090 "the apps' origin"; want shut 4444 "an undeclared service"
echo "== off: $(floor firewall-off)"
want open 4444 "an undeclared service, again"
nft list table inet irate_box >/dev/null 2>&1 && bad "the table still loaded" || ok "the table gone"
echo "failures: $fails"; exit $((fails > 0))
