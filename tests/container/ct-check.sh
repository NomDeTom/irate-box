#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Checks run inside a test container after install.sh. Usage: ct-check.sh WEB PORT [claim]
# WEB = nginx|caddy (expected front), PORT = where the hub is served.
WEB=$1 PORT=$2 CLAIM=${3:-}
OTHER=caddy; [ "$WEB" = caddy ] && OTHER=nginx
H=127.0.0.1:$PORT
PW1=correct-horse-1 PW2=battery-staple-2
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
check() { local want=$1; shift; local got; got="$("$@" 2>/dev/null)"; [ "$got" = "$want" ] && ok "$* -> $got" || bad "$* -> $got (want $want)"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$@"; }
# Wait (up to 20 s; Caddy restarts after a change) until $1 is the working password.
until_pw() { for _ in $(seq 40); do [ "$(code -u "admin:$1" "http://$H/admin/")" = 200 ] && return 0; sleep 0.5; done; return 1; }
set_pw() { # current new
	local id; id="$(curl -s -u "admin:$1" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "{\"password\":\"$2\"}" "http://$H/admin/password" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
	result "$id"
}
result() { # wait for a control request's result; print ok/error
	local id=$1 f=/var/lib/hub/control/results/$1.json
	for _ in $(seq 60); do [ -s "$f" ] && break; sleep 0.5; done
	python3 -c "import json,sys; d=json.load(open('$f')); print(('OK ' if d.get('ok') else 'ERR ') + str(d.get('message') or d.get('error')))"
}

echo "== services"
check active systemctl is-active "$WEB"
[ "$(systemctl is-active "$OTHER" 2>/dev/null)" = active ] && bad "$OTHER is also active" || ok "$OTHER not active"
check "$WEB" sh -c "ss -Hltnp 'sport = :$PORT' | grep -o 'users:((\"[^\"]*' | cut -d'\"' -f2 | head -1"
check "HUB_WEB_SERVER=$WEB" grep -x "HUB_WEB_SERVER=$WEB" /etc/hub/hub.env
check "--web" sh -c "grep -x -- --web /etc/hub/install-options"
check "$WEB" sh -c "grep -A1 -x -- --web /etc/hub/install-options | tail -1"

if [ "$WEB" = nginx ]; then
	echo "== nginx files"
	check "640 root www-data" stat -c '%a %U %G' /etc/nginx/irate-box.htpasswd
	check "admin" sh -c "cut -d: -f1 /etc/nginx/irate-box.htpasswd"
	check '$6$' sh -c "cut -d: -f2 /etc/nginx/irate-box.htpasswd | cut -c1-3"
	check "0" sh -c "grep -c '@[A-Z]*@' /etc/nginx/conf.d/irate-box.conf"
	nginx -t -q 2>/dev/null && ok "nginx -t" || bad "nginx -t"
fi

echo "== first use"
U=/etc/$WEB/irate-box-unclaimed
if [ -n "$CLAIM" ]; then
	[ -f "$U" ] && ok "unclaimed mark at $U" || bad "no unclaimed mark at $U"
	check 200 code "http://$H/admin/"
	id="$(curl -s -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "{\"password\":\"$PW1\"}" "http://$H/admin/setup" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
	r="$(result "$id")"; [[ $r == OK* ]] && ok "claim: $r" || bad "claim: $r"
	[ -f "$U" ] && bad "unclaimed mark still there" || ok "unclaimed mark gone"
	until_pw "$PW1" || bad "the claimed password never started working"
fi
# Start from PW1, whatever an earlier run left.
until_pw "$PW1" || { until_pw "$PW2" && set_pw "$PW2" "$PW1" >/dev/null && until_pw "$PW1"; } || bad "neither test password works"
check 401 code "http://$H/admin/"
t=$(curl -s -o /dev/null -w '%{http_code} %{time_total}' -u "admin:$PW1" "http://$H/admin/")
[ "${t%% *}" = 200 ] && ok "/admin/ with the password: $t" || bad "/admin/ with the password: $t"
t2=$(curl -s -o /dev/null -w '%{time_total}' -u "admin:$PW1" "http://$H/admin/settings")
ok "second authenticated request: ${t2}s"
check 401 code -u admin:wrong "http://$H/admin/"

echo "== password change"
r="$(set_pw "$PW1" "$PW2")"; [[ $r == OK* ]] && ok "change: $r" || bad "change: $r"
until_pw "$PW2"
check 200 code -u "admin:$PW2" "http://$H/admin/"
check 401 code -u "admin:$PW1" "http://$H/admin/"
# back to PW1 for later runs
set_pw "$PW2" "$PW1" >/dev/null; until_pw "$PW1" || bad "could not set the password back"

echo "== routes"
# Under nginx, /wiki and /notes live on their own origin (a port of their own, for origin
# isolation), so the main origin sends a cross-origin 302 to that port; under Caddy they stay on
# the main origin and 301 to their trailing slash, like /draw, /mermaid, /tools, /serial, /sync,
# /admin and /term do on both fronts.
wn=302; [ "$WEB" = caddy ] && wn=301
for pr in "/ 200" "/style.css 200" "/nope-xyz 404" "/wiki $wn" "/draw 301" "/mermaid 301" "/tools 301" "/serial 301" \
	"/notes $wn" "/sync 301" "/admin 301" "/term 301" "/sync/ 401" "/term/ 401" "/status 200"; do
	set -- $pr; check "$2" code "http://$H$1"
done
check "/draw/" sh -c "curl -s -o /dev/null -w '%{redirect_url}' http://$H/draw | sed 's|^http://[^/]*||'"
check "no-cache" sh -c "curl -s -D - -o /dev/null http://$H/style.css | tr -d '\r' | awk -F': ' 'tolower(\$1)==\"cache-control\"{print \$2}'"
check "True" sh -c "curl -s http://$H/status | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"proxied\"])'"
want_name=nginx; [ "$WEB" = caddy ] && want_name=Caddy
check "running" sh -c "curl -s http://$H/status | python3 -c 'import json,sys; print([s[\"state\"] for s in json.load(sys.stdin)[\"services\"] if s[\"name\"]==\"Web server ($want_name)\"][0])'"
# An over-cap upload is rejected. The hub answers 413 and closes without draining the body
# (Connection: close). Under nginx the client always sees that 413. Under Caddy the body is
# streamed to the hub, so whether the client gets the forwarded 413 or a 502 from the upload
# connection breaking mid-stream is a timing race — accept either as "rejected".
if [ "$WEB" = caddy ]; then
	got=$(code -X POST -H 'Content-Type: application/octet-stream' --data-binary @<(head -c 27000000 /dev/zero) "http://$H/api/drop?name=big.bin")
	case "$got" in 413 | 502) ok "over-cap upload refused ($got)" ;; *) bad "over-cap upload -> $got (want 413 or 502)" ;; esac
else
	check 413 code -X POST -H 'Content-Type: application/octet-stream' --data-binary @<(head -c 27000000 /dev/zero) "http://$H/api/drop?name=big.bin"
fi

echo "== captive probes"
for hp in "captive.apple.com /hotspot-detect.html" "connectivitycheck.gstatic.com /generate_204"; do
	set -- $hp; check 302 code -H "Host: $1" "http://$H$2"
done

echo "== hub listen queue"
check 64 sh -c "ss -Hltn 'sport = :8000' | awk '{print \$3}'"

echo "== result: $fails failed"
exit $fails
