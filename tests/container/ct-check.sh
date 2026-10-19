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
until_pw() { for _ in $(seq 40); do [ "$(code -u "admin:$1" "http://127.0.0.1:8095/admin/")" = 200 ] && return 0; sleep 0.5; done; return 1; }
set_pw() { # current new: at the console (over plain HTTP the hub refuses an admin password change)
	local out
	out="$(printf '%s\n%s\n' "$2" "$2" | /opt/irate-box/irate-box hub_control script-login 2>&1)" && echo "OK ${out##*$'\n'}" || echo "ERR $out"
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
	# Unclaimed, /admin is the set-up page alone (#341): a browser is sent there, the page itself is open.
	check 302 code "http://127.0.0.1:8095/admin/"
	check 200 code "http://127.0.0.1:8095/admin/setup"
	id="$(curl -s -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "{\"password\":\"$PW1\",\"over_http\":true}" "http://127.0.0.1:8095/admin/setup" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
	r="$(result "$id")"; [[ $r == OK* ]] && ok "claim: $r" || bad "claim: $r"
	[ -f "$U" ] && bad "unclaimed mark still there" || ok "unclaimed mark gone"
	until_pw "$PW1" || bad "the claimed password never started working"
fi
# Start from PW1, whatever an earlier run left.
until_pw "$PW1" || { until_pw "$PW2" && set_pw "$PW2" "$PW1" >/dev/null && until_pw "$PW1"; } || bad "neither test password works"
check 401 code "http://127.0.0.1:8095/admin/"
t=$(curl -s -o /dev/null -w '%{http_code} %{time_total}' -u "admin:$PW1" "http://127.0.0.1:8095/admin/")
[ "${t%% *}" = 200 ] && ok "/admin/ with the password: $t" || bad "/admin/ with the password: $t"
t2=$(curl -s -o /dev/null -w '%{time_total}' -u "admin:$PW1" "http://127.0.0.1:8095/admin/settings")
ok "second authenticated request: ${t2}s"
check 401 code -u admin:wrong "http://127.0.0.1:8095/admin/"

echo "== password change"
check 403 code -u "admin:$PW1" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d '{"password":"over-plain-http"}' "http://127.0.0.1:8095/admin/password"
r="$(set_pw "$PW1" "$PW2")"; [[ $r == OK* ]] && ok "change: $r" || bad "change: $r"
until_pw "$PW2"
check 200 code -u "admin:$PW2" "http://127.0.0.1:8095/admin/"
check 401 code -u "admin:$PW1" "http://127.0.0.1:8095/admin/"
# back to PW1 for later runs
set_pw "$PW2" "$PW1" >/dev/null; until_pw "$PW1" || bad "could not set the password back"

echo "== the first use, once ended, stays ended"
# What the set-up page leaves (hub_control claim): the box's own login off, no password kept. Then the installer again,
# as every update runs it: the box must not go back to its first use. (No account is made: the rest of the checks
# expect none.)
cp /etc/hub/admin-password /tmp/pw.keep
rm -f /etc/hub/admin-password
cp /etc/nginx/irate-box.htpasswd /etc/hub/admin-login.off 2>/dev/null || echo "kept aside" >/etc/hub/admin-login.off
bash /src/install.sh --src /src >/dev/null 2>&1
[ ! -e "$U" ] && ok "the installer again: not the first use" || bad "the installer made the box free again (its first-use mark is back)"
grep -qx -- --port /etc/hub/install-options && bad "the installer again moved the hub off port $PORT (it counted its own listeners as someone else's)" || ok "the installer again: the hub keeps port $PORT"
r=$(code "http://127.0.0.1:8095/admin/"); [ "$r" != 200 ] && ok "/admin/ not open to a guest ($r)" || bad "/admin/ open to a guest after the installer"
printf '%s\n%s\n' "$PW1" "$PW1" | /opt/irate-box/irate-box hub_control script-login >/dev/null 2>&1
rm -f /tmp/pw.keep
until_pw "$PW1" || bad "the box's own login not back for the rest"
echo "== the box's own login: from the box itself, or the network too (Accounts)"
IP=$(hostname -I | awk '{print $1}')
reach() {
	local id; id="$(curl -s -u "admin:$PW1" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' \
		-d "{\"action\":\"admin-login\",\"reach\":\"$1\"}" "http://127.0.0.1:8095/admin/accounts" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
	result "$id"
}
# With no administrator account that has signed in over HTTPS the box refuses to narrow script sign-in to
# the box itself (it would lock everyone else out of the administration pages); no such sign-in exists here.
r="$(reach local)"; [[ $r == ERR*"No administrator account"* ]] && ok "reach: from the box itself refused while no admin has signed in over HTTPS" || bad "reach local: $r"
sleep 2
check 200 code -u "admin:$PW1" "http://127.0.0.1:8095/admin/"
r=$(code -u "admin:$PW1" "http://$IP:8095/admin/"); [ "$r" != 200 ] && ok "from another address the login counts for nothing ($r)" || bad "the login worked from $IP"
r="$(reach network)"; [[ $r == OK* ]] && ok "reach: from the network too" || bad "reach network: $r"
sleep 2
check 200 code -u "admin:$PW1" "http://$IP:8095/admin/"

echo "== routes"
# /draw, /mermaid, /sync and /admin (and /wiki and /notes under nginx) live on origins of their own,
# each a port of its own, so the hub's port sends a cross-origin 302 there (the pages themselves
# are checked on those ports, e.g. /admin/ above). Under Caddy the bare /wiki and /notes only 301 to
# their trailing slash, like /tools, /serial and /term on both fronts.
wn=302; [ "$WEB" = caddy ] && wn=301
for pr in "/ 200" "/style.css 200" "/nope-xyz 404" "/wiki $wn" "/draw 302" "/mermaid 302" "/tools 301" "/serial 301" \
	"/notes $wn" "/sync 302" "/admin 302" "/term 301" "/sync/ 302" "/term/ 401" "/status 200"; do
	# shellcheck disable=SC2086 # split "path code" into $1 and $2
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
	# shellcheck disable=SC2086 # split "host path" into $1 and $2
	set -- $hp; check 302 code -H "Host: $1" "http://$H$2"
done

echo "== hub listen queue"
check 64 sh -c "ss -Hltn 'sport = :8000' | awk '{print \$3}'"

echo "== result: $fails failed"
exit $fails
