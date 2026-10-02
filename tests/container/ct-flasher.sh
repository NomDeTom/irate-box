#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The web flasher's hub side, and the firmware mirror. Usage: ct-flasher.sh HOST (the address a guest uses)
H=127.0.0.1 G=$1 PW=correct-horse-1
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails+1)); }
check() { local want=$1; shift; local got; got="$("$@" 2>/dev/null)"; [ "$got" = "$want" ] && ok "$* -> $got" || bad "$* -> $got (want $want)"; }
hdr() { curl -s -D - -o /dev/null "$@" | tr -d '\r' | awk -F': ' -v h="$HDR" 'tolower($1)==tolower(h){print $2}'; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
echo "== the page"
check 302 code http://$H/flasher/
curl -s -H "Host: $G" http://$H/flasher/flasher.html -o /tmp/dl.html
check 0 sh -c "grep -c __IRATE_BOX_ORIGIN__ /tmp/dl.html"
check 16 sh -c "grep -o 'http://$G/flasher' /tmp/dl.html | wc -l"
HDR=content-disposition check 'attachment; filename="meshtastic-flasher.html"' hdr -H "Host: $G" http://$H/flasher/flasher.html
r=$(code -H 'Host: evil"><script>' http://$H/flasher/flasher.html); case $r in 400|404) ok "a malformed Host is refused ($r)" ;; *) bad "malformed Host -> $r" ;; esac
echo "== the API (hub), with CORS"
HDR=access-control-allow-origin check '*' hdr http://$H/flasher/api/resource/deviceHardware
check 200 code http://$H/flasher/api/resource/deviceHardware
check True sh -c "curl -s http://$H/flasher/api/resource/deviceHardware | python3 -c 'import json,sys; print(len(json.load(sys.stdin))>50)'"
check 200 code http://$H/flasher/api/resource/eventFirmware
[ -f /var/lib/hub/firmware/index.json ] || check '[]' sh -c "curl -s http://$H/flasher/api/github/firmware/list | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"releases\"][\"alpha\"])'"
check 204 code -X OPTIONS -H 'Origin: null' -H 'Access-Control-Request-Method: GET' http://$H/flasher/api/github/firmware/list
HDR=access-control-allow-private-network check true hdr -X OPTIONS http://$H/flasher/api/resource/deviceHardware
check 404 code http://$H/flasher/api/github/firmware/pr/1
echo "== static files (web server), with CORS"
check 200 code http://$H/flasher/data/hardware-list.json
HDR=access-control-allow-origin check '*' hdr http://$H/flasher/data/hardware-list.json
check 200 code http://$H/flasher/img/devices/unknown-new-light.svg
check 204 code -X OPTIONS http://$H/flasher/img/devices/unknown-new-light.svg
HDR=access-control-allow-origin check '*' hdr http://$H/flasher/nightly/index.json
echo "== firmware: settings, a sync of two boards"
A() { curl -s -u admin:$PW -X POST -H 'Content-Type: application/json' -d "$1" http://$H/admin/firmware; }
check 401 code http://$H/admin/firmware
A '{"action":"settings","enabled":true,"boards":["rak4631","heltec-v3"],"cache":"discard"}' >/dev/null
check "True ['heltec-v3', 'rak4631'] discard" sh -c "curl -s -u admin:$PW http://$H/admin/firmware | python3 -c 'import json,sys; c=json.load(sys.stdin)[\"settings\"]; print(c[\"enabled\"], c[\"boards\"], c[\"cache\"])'"
check 400 code -u admin:$PW -X POST -H 'Content-Type: application/json' -d '{"action":"settings","boards":["../x"]}' http://$H/admin/firmware
A '{"action":"update"}' >/dev/null
for _ in $(seq 180); do r=$(curl -s -u admin:$PW http://$H/admin/firmware | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["running"], d["status"].get("outcome",""))'); [ "${r%% *}" = False ] && [ -n "${r#* }" ] && break; sleep 2; done
echo "  outcome: ${r#* }"
case "$r" in *error*|"") bad "sync: $r" ;; *) ok "sync finished" ;; esac
V=$(curl -s http://$H/flasher/api/github/firmware/list | python3 -c 'import json,sys; d=json.load(sys.stdin)["releases"]; print((d["alpha"]+d["stable"])[0]["id"].lstrip("v"))')
echo "  first listed: $V"
check 3 sh -c "curl -s http://$H/flasher/api/github/firmware/list | python3 -c 'import json,sys; d=json.load(sys.stdin)[\"releases\"]; print(len(d[\"alpha\"])+len(d[\"stable\"]))'"
check 200 code http://$H/flasher/firmware/$V/firmware-$V.json
check 200 code http://$H/flasher/firmware/$V/firmware-rak4631-$V.mt.json
check 200 code http://$H/flasher/firmware/$V/firmware-rak4631-$V.uf2
HDR=access-control-allow-origin check '*' hdr http://$H/flasher/firmware/$V/firmware-rak4631-$V.uf2
check 0 sh -c "ls /var/lib/hub/firmware/$V/ | grep -c '\.elf$'"
check ok sh -c "python3 - <<P
import json, hashlib
m = json.load(open('/var/lib/hub/firmware/$V/firmware-rak4631-$V.mt.json'))
bad = [f['name'] for f in m['files'] if not f['name'].endswith('.elf') and hashlib.md5(open('/var/lib/hub/firmware/$V/' + f['name'],'rb').read()).hexdigest() != f['md5']]
print('ok' if not bad else bad)
P"
check hub sh -c "stat -c %U /var/lib/hub/firmware/$V/firmware-rak4631-$V.uf2"
du -sh /var/lib/hub/firmware/* | sed 's/^/  /'
echo "== result: $fails failed"; exit $fails
