#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Who may open each app (public, private, off) through /admin, the root helper and the web
# server in front, then everything back to its default. Usage (in the container, after
# install.sh and a claimed password): ct-access.sh PORT
H=127.0.0.1:$1
PW="$(cat /etc/hub/admin-password)"
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
want() { local w=$1; shift; local g; g="$(code "$@")"; [ "$g" = "$w" ] && ok "$* -> $g" || bad "$* -> $g (want $w)"; }
result() { local f=/var/lib/hub/control/results/$1.json; for _ in $(seq 60); do [ -s "$f" ] && break; sleep 0.5; done
	python3 -c "import json; d=json.load(open('$f')); print(('OK ' if d['ok'] else 'ERR ') + d['message'])"; }
set_access() {
	local id r
	id="$(curl -s -u "admin:$PW" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "{\"app\":\"$1\",\"mode\":\"$2\"}" "http://$H/admin/access" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
	r="$(result "$id")"; [[ $r == OK* ]] && ok "$1 $2: $r" || bad "$1 $2: $r"
	sleep 2 # nginx reloads at once; Caddy restarts a second later
	for _ in $(seq 20); do code "http://$H/status" | grep -q 200 && break; sleep 0.5; done
}
# No login asked: an app missing from a test box answers 500 (nginx) or 404 (Caddy) instead of
# 200, so the strict checks are on the flasher, which ct-flasher's setup installs.
open_() { local g; g="$(code "$@")"; [ "$g" != 401 ] && ok "$* -> $g (no login)" || bad "$* -> $g (want no login)"; }
tiles() { curl -s "http://$H/" | grep -o 'class="name">[^<]*' | sed 's/.*>//' | tr '\n' '|'; }

echo "== defaults"
open_ "http://$H/draw/"
want 200 "http://$H/flasher/flasher.html"
want 200 "http://$H/drop.html"
want 401 "http://$H/sync/"
[[ "$(tiles)" == *"Excalidraw|"* ]] && ok "Excalidraw tile shown" || bad "tiles: $(tiles)"

echo "== private"
for a in draw drop git flasher wiki; do set_access $a private; done
want 401 "http://$H/draw/"
open_ -u "admin:$PW" "http://$H/draw/"
want 401 "http://$H/drop.html"
want 401 "http://$H/api/drop"
want 401 "http://$H/api/drop/whatever"
want 401 "http://$H/git/"
want 401 "http://$H/git/irate-box-source.git/info/refs?service=git-upload-pack"
want 200 -u "admin:$PW" "http://$H/git/irate-box-source.git/info/refs?service=git-upload-pack"
want 401 "http://$H/flasher/flasher.html"
want 200 "http://$H/flasher/api/resource/deviceHardware"
want 401 "http://$H/wiki/"
t="$(tiles)"
[[ "$t" != *"Excalidraw|"* && "$t" != *"File drop|"* && "$t" != *"Git|"* && "$t" != *"Kiwix|"* ]] && ok "their tiles gone: $t" || bad "tiles: $t"

echo "== off"
for a in draw flasher term; do set_access $a off; done
want 404 "http://$H/draw/"
want 404 -u "admin:$PW" "http://$H/draw/"
want 404 "http://$H/flasher/api/resource/deviceHardware"
want 404 -u "admin:$PW" "http://$H/term/"
[[ "$(tiles)" != *"Terminal|"* ]] && ok "Terminal tile gone" || bad "tiles: $(tiles)"
[ "$(systemctl is-enabled ttyd 2>/dev/null)" != enabled ] && ok "ttyd disabled ($(systemctl is-enabled ttyd 2>&1))" || bad "ttyd still enabled"

echo "== refused"
c="$(code -u "admin:$PW" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d '{"app":"room","mode":"off"}' "http://$H/admin/access")"
[ "$c" = 400 ] && ok "an app with no route: 400" || bad "room -> $c"
c="$(code -u "admin:$PW" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d '{"app":"draw","mode":"secret"}' "http://$H/admin/access")"
[ "$c" = 400 ] && ok "an unknown mode: 400" || bad "mode -> $c"

echo "== back to the defaults"
for a in draw drop git flasher wiki term; do set_access $a public; done
open_ "http://$H/draw/"
want 200 "http://$H/drop.html"
want 200 "http://$H/git/"
want 200 "http://$H/flasher/flasher.html"
want 401 "http://$H/term/"
[[ "$(tiles)" == *"Excalidraw|"*"Terminal|"* ]] && ok "tiles back: $(tiles)" || bad "tiles: $(tiles)"
python3 -c "import json; d=json.load(open('/etc/hub/access.json')); print(d)" | sed 's/^/    /'

echo; echo "failures: $fails"
exit $((fails > 0))
