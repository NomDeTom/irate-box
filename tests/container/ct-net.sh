#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Network page checks inside the test container, after install.sh --uplink standard,strict.
# Usage: ct-net.sh PORT
H=127.0.0.1:$1
PW="$(cat /etc/hub/admin-password)"
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
j() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
get() { curl -s -u "admin:$PW" "http://$H/admin/network"; }
post() { curl -s -u "admin:$PW" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "$1" -w '\n%{http_code}' "http://$H/admin/network"; }
result() { local f=/var/lib/hub/control/results/$1.json; for _ in $(seq 120); do [ -s "$f" ] && break; sleep 0.5; done
	python3 -c "import json; d=json.load(open('$f')); print(('OK ' if d['ok'] else 'ERR ') + d['message'])"; }
idof() { head -1 | j 'd["id"]'; }

echo "== watchdog"
[ "$(systemctl is-active irate-box-uplink)" = active ] && ok "irate-box-uplink active" || bad "irate-box-uplink $(systemctl is-active irate-box-uplink)"
[ "$(systemctl is-enabled irate-box-uplink)" = enabled ] && ok "enabled" || bad "not enabled"
for _ in $(seq 20); do [ -s /var/lib/hub/control/uplink.json ] && break; sleep 1; done
s="$(get)"
echo "$s" | j '(d["uplink"]["state"], d["uplink"]["iface"], d["uplink"]["backend"], d["uplink"]["chosen"]["pace"], d["uplink"]["chosen"]["reach"], d["uplink"]["chosen"]["sensitivity"], d["uplink"]["stale"])' | sed 's/^/    /'
# The old single level still read: standard is the steady pace, reach reboot; strict, 2 missed checks (2026-10-09).
[ "$(echo "$s" | j 'd["uplink"]["chosen"]["pace"]+","+d["uplink"]["chosen"]["reach"]+","+str(d["uplink"]["chosen"]["sensitivity"])')" = "steady,reboot,2" ] && ok "--uplink standard,strict taken as steady, reboot, sensitivity 2" || bad "settings not taken"
[ "$(echo "$s" | j 'd["inventory"]["uplink"]["iface"]')" = eth0 ] && ok "inventory written at install" || bad "no inventory"
[ "$(echo "$s" | j 'd["levels"]["pace"]==["gentle","steady","prompt","urgent"] and d["levels"]["reach"]==["watch","reconnect","restart","radio","reboot"]')" = True ] && ok "both dials offered" || bad "levels"

echo "== scan one device"
id="$(post '{"action":"scan","iface":"eth0"}' | idof)"; r="$(result "$id")"; echo "    $r"
[[ "$r" == OK* ]] && [ "$(get | j 'd["inventory"]["focus"]')" = eth0 ] && ok "scan eth0" || bad "scan eth0"
id="$(post '{"action":"scan"}' | idof)"; r="$(result "$id")"; [[ "$r" == OK* ]] && ok "scan all: $r" || bad "scan all: $r"
c="$(post '{"action":"scan","iface":"eth0;rm"}' | tail -1)"; [ "$c" = 400 ] && ok "bad iface refused" || bad "bad iface -> $c"

echo "== settings"
id="$(post '{"action":"settings","settings":{"pace":"prompt","reach":"radio","sensitivity":5,"iface":"auto","overrides":{"check":20,"steps":{"radio":null}}}}' | idof)"
r="$(result "$id")"; echo "    $r"; [[ "$r" == OK* ]] && ok "saved" || bad "save"
python3 -c "import json; d=json.load(open('/etc/hub/uplink.json')); assert (d['pace'], d['reach'])==('prompt','radio') and d['overrides']=={'check':20,'steps':{'radio':None}}, d" && ok "/etc/hub/uplink.json written" || bad "file"
for _ in $(seq 30); do e="$(get | j 'd["uplink"]["events"][-1]["text"]')"; [[ "$e" == "Settings changed: prompt pace"* ]] && break; sleep 1; done
[[ "$e" == "Settings changed: prompt pace"* ]] && ok "watchdog took it: $e" || bad "watchdog event: $e"
[ "$(get | j 'd["uplink"]["settings"]["check"]')" = 20 ] && ok "effective check 20" || bad "effective"
c="$(post '{"action":"settings","settings":{"pace":"max"}}' | tail -1)"; [ "$c" = 400 ] && ok "bad pace refused" || bad "bad pace -> $c"
c="$(post '{"action":"settings","settings":{"reach":"nuke"}}' | tail -1)"; [ "$c" = 400 ] && ok "bad reach refused" || bad "bad reach -> $c"
c="$(post '{"action":"settings","settings":{"pace":"gentle","overrides":{"check":1}}}' | tail -1)"; [ "$c" = 400 ] && ok "out-of-range refused" || bad "range -> $c"
c="$(post '{"action":"settings","settings":{"pace":"gentle","overrides":{"rm":1}}}' | tail -1)"; [ "$c" = 400 ] && ok "unknown field refused" || bad "field -> $c"

echo "== a step by hand"
c="$(post '{"action":"do","step":"nuke"}' | tail -1)"; [ "$c" = 400 ] && ok "a made-up step refused" || bad "do nuke -> $c"
id="$(post '{"action":"do","step":"reconnect"}' | idof)"; r="$(result "$id")"; [[ "$r" == OK*"Asked the watchdog"* ]] && ok "$r" || bad "do: $r"
for _ in $(seq 20); do [ -e /etc/hub/uplink-now.json ] || break; sleep 1; done
[ ! -e /etc/hub/uplink-now.json ] && ok "the watchdog took the request" || bad "request not taken"

echo "== hold"
id="$(post '{"action":"hold","minutes":60}' | idof)"; r="$(result "$id")"; [[ "$r" == OK* ]] && ok "$r" || bad "$r"
python3 -c "import json,time; d=json.load(open('/etc/hub/uplink.json')); assert d['hold_until']>time.time()+3500" && ok "hold recorded" || bad "hold"
python3 -c "import json; d=json.load(open('/etc/hub/uplink.json')); assert d['pace']=='prompt' and d['overrides']['check']==20" && ok "hold kept the settings" || bad "hold lost settings"
id="$(post '{"action":"hold","minutes":0}' | idof)"; r="$(result "$id")"; [[ "$r" == OK* ]] && ok "$r" || bad "$r"
c="$(post '{"action":"hold","minutes":5000}' | tail -1)"; [ "$c" = 400 ] && ok "long hold refused" || bad "hold -> $c"

echo "== profile (no NetworkManager here)"
id="$(post '{"action":"profile","on":true}' | idof)"; r="$(result "$id")"; echo "    $r"
[[ "$r" == ERR*NetworkManager* ]] && ok "refused cleanly" || bad "profile: $r"
[ ! -f /etc/hub/uplink-changes.json ] && ok "nothing recorded" || bad "record written"

echo "== unprivileged"
c="$(curl -s -o /dev/null -w '%{http_code}' "http://$H/admin/network")"; [ "$c" = 401 ] && ok "needs the login" || bad "no login -> $c"
echo "failures: $fails"
