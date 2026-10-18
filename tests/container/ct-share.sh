#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Guests' internet, as a phone on the hotspot meets it: network namespaces stand in for a phone (on a veth
# whose box end is ap0, the hotspot's interface), a host on the internet (a public address, on up0) and the
# owner's router (a private one, on lan0). At each level of sharing: whether the phone reaches the outside
# host's web and another of its ports, before and after tapping through the sheet (a POST to the hub, as
# the sheet sends it), and never the router. Addresses only: the hotspot's DNS doesn't run here.
# Usage (in the container, after install.sh and the admin's claim): ct-share.sh
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
servers=()
cleanup() {
	(cd /opt/irate-box && python3 -c "import sys; sys.path.insert(0, '.'); from irate_box.root import hub_control; hub_control.share_set({'level': 'off'})") >/dev/null 2>&1
	for n in phone outside router; do ip netns del "$n" 2>/dev/null; done
	for l in ap0 up0 lan0; do ip link del "$l" 2>/dev/null; done
}
trap cleanup EXIT
pair() { # pair NS BOXIF NSIF BOXADDR NSADDR
	ip link add "$2" type veth peer name "$3" && ip netns add "$1" && ip link set "$3" netns "$1" || return 1
	ip addr add "$4" dev "$2" && ip link set "$2" up
	ip -n "$1" addr add "$5" dev "$3" && ip -n "$1" link set "$3" up && ip -n "$1" link set lo up
}
fwd_before=$(cat /proc/sys/net/ipv4/ip_forward)
pair phone ap0 phone0 192.168.4.1/24 192.168.4.2/24 || { echo "no netns here: skipped"; exit 0; }
ip -n phone route add default via 192.168.4.1
pair outside up0 wan0 11.1.1.1/24 11.1.1.2/24
pair router lan0 rtr0 192.168.77.10/24 192.168.77.1/24
for ns in outside router; do
	ip netns exec $ns python3 -m http.server 80 --bind 0.0.0.0 >/dev/null 2>&1 & servers+=($!)
done
ip netns exec outside python3 -m http.server 2222 --bind 0.0.0.0 >/dev/null 2>&1 & servers+=($!)
sleep 1
reach() { ip netns exec phone timeout 4 bash -c "</dev/tcp/$1/$2" 2>/dev/null && echo open || echo shut; }
want() { local w=$1 host=$2 port=$3 what=$4 g; g=$(reach "$host" "$port"); [ "$g" = "$w" ] && ok "$what ($host:$port) $g" || bad "$what ($host:$port) $g, want $w"; }
# Who answers a web request from the phone: the host itself (Python's directory listing), the hub (a guest not let
# out yet has its web requests sent there, for the sheet), or nobody.
whose() { local b; b=$(ip netns exec phone curl -s -m 5 "http://$1/" 2>/dev/null); case "$b" in *"Directory listing"*) echo it ;; "") echo nobody ;; *) echo hub ;; esac; }
web() { local w=$1 host=$2 what=$3 g; g=$(whose "$host"); [ "$g" = "$w" ] && ok "$what ($host): $g answers" || bad "$what ($host): $g answers, want $w"; }
level() { (cd /opt/irate-box && python3 -c "import sys; sys.path.insert(0, '.'); from irate_box.root import hub_control; print(hub_control.share_set({'level': sys.argv[1]}))" "$1") 2>&1 | tail -1; }
tap() { # the sheet's "I agree", sent from the phone; the root helper lets it out a moment later
	local said i
	said=$(ip netns exec phone curl -s -m 10 -X POST -H 'Content-Type: application/json' -d '{"agree": true}' http://192.168.4.1/api/guest-net)
	echo "    tapped through: $said"
	for i in $(seq 30); do nft list table inet irate_box 2>/dev/null | grep -qE "192\.168\.4\.2|([0-9a-f]{2}:){5}[0-9a-f]{2}" && return 0; sleep 1; done
	return 1
}

echo "== off: the box only"
web nobody 11.1.1.2 "the outside web"; web nobody 192.168.77.1 "the owner's router"
echo "== after the sheet, web only: $(level sheet-web)"
web hub 11.1.1.2 "the outside web, before the sheet (the sheet instead)"
tap && ok "let out by the root helper" || bad "not let out within 30 s"
web it 11.1.1.2 "the outside web, after the sheet"; want shut 11.1.1.2 2222 "another port (web only)"
web nobody 192.168.77.1 "the owner's router (never: contained)"
echo "== after the sheet, everything: $(level sheet-all)"
# Not tapped again: a device let out stays out when the level changes (the ruleset is written again).
want open 11.1.1.2 2222 "another port, still let out after the change"; web nobody 192.168.77.1 "the owner's router"
echo "== everyone, no sheet: $(level open)"
web it 11.1.1.2 "the outside web"; web nobody 192.168.77.1 "the owner's router"
echo "== off again: $(level off)"
web nobody 11.1.1.2 "the outside web"
[ "$(cat /proc/sys/net/ipv4/ip_forward)" = "$fwd_before" ] && ok "forwarding back as it was ($fwd_before)" || bad "forwarding $(cat /proc/sys/net/ipv4/ip_forward), was $fwd_before"
kill "${servers[@]}" 2>/dev/null
echo "failures: $fails"; exit $((fails > 0))
