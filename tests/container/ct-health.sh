#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Health page checks inside the test container. Usage: ct-health.sh PORT
H=127.0.0.1:$1
PW="$(cat /etc/hub/admin-password)"
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
j() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
get() { curl -s -u "admin:$PW" "http://$H/admin/health"; }
post() { curl -s -u "admin:$PW" -X POST -H 'Content-Type: application/json' -d "$1" -w '\n%{http_code}' "http://$H/admin/health"; }
result() { local f=/var/lib/hub/control/results/$1.json; for _ in $(seq ${2:-120}); do [ -s "$f" ] && break; sleep 1; done
	[ -s "$f" ] && python3 -c "import json; d=json.load(open('$f')); print(('OK ' if d['ok'] else 'ERR ') + d['message'])" || echo "NO ANSWER"; }
idof() { head -1 | j 'd["id"]'; }

# The page and the doctor should report a stopped install: unless the last one really stopped,
# record one that did (the repair at the end runs the installer again, which replaces it).
STATEF=/var/log/irate-box/install-state.json
if ! python3 -c "import json,sys; sys.exit(0 if json.load(open('$STATEF')).get('aborted') else 1)" 2>/dev/null; then
	python3 - "$STATEF" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.update(aborted=True, exit=1, step="Starting services", failed_line=1234, failed_command="systemctl start example")
json.dump(d, open(p, "w"))
PY
	echo "    (recorded a stopped install to look at)"
fi

echo "== the page's data"
s="$(get)"
[ "$(echo "$s" | j 'd["helper"]["stuck"]')" = False ] && ok "helper answering" || bad "helper stuck?"
[ "$(echo "$s" | j 'd["install"]["aborted"]')" = True ] && ok "last install shown as stopped: $(echo "$s" | j 'd["install"]["step"]')" || bad "install record"
[ "$(echo "$s" | j 'len(d["log"]) > 3')" = True ] && ok "install log tail readable by the hub" || bad "log"
c="$(post '{"action":"fix","choice":"unit-restart:sshd.service;x"}' | tail -1)"; [ "$c" = 400 ] && ok "bad choice refused (400)" || bad "bad choice -> $c"
id="$(post '{"action":"fix","choice":"unit-restart:ssh.service"}' | idof)"; r="$(result "$id")"; [[ "$r" == ERR*"not irate-box"* ]] && ok "not ours refused: $r" || bad "ssh restart: $r"

echo "== scan through the page"
id="$(post '{"action":"scan"}' | idof)"; r="$(result "$id")"; echo "    $r"
[[ "$r" == OK* ]] && ok "scan answered" || bad "scan"
[ "$(get | j 'any(f["id"]=="install" and f["status"]=="problem" for f in d["report"]["findings"])')" = True ] && ok "report names the stopped install" || bad "report"
[ "$(get | j 'not any(f["id"]=="helper-queue" for f in d["report"]["findings"])')" = True ] && ok "no false queue alarm from inside the helper" || bad "queue alarm"

echo "== the root helper stops answering"
systemctl stop irate-box-control.path
id="$(post '{"action":"scan"}' | idof)"
echo "    waiting 95 s with one request queued…"; sleep 95
s="$(get)"
[ "$(echo "$s" | j 'd["helper"]["stuck"]')" = True ] && ok "page sees the helper stuck: $(echo "$s" | j 'd["helper"]["oldest"]') s" || bad "not seen stuck"
echo "$s" | j '"\n".join(d["helper"]["commands"])' | sed 's/^/      /'
/opt/irate-box/irate-box health summary | grep -q "Root helper queue" && ok "shell doctor names the queue" || bad "shell doctor"
/opt/irate-box/irate-box health summary | grep -A1 "Root helper queue" | sed 's/^/      /'
/opt/irate-box/irate-box health fix unit-restart:irate-box-control.path && ok "shell repair" || bad "shell repair"
r="$(result "$id" 60)"; [[ "$r" == OK* ]] && ok "the queued request answered afterwards: $r" || bad "queued: $r"
sleep 2; [ "$(get | j 'd["helper"]["stuck"]')" = False ] && ok "banner condition cleared" || bad "still stuck"

echo "== repair: run the installer again from the page"
id="$(post '{"action":"fix","choice":"rerun-install"}' | idof)"; r="$(result "$id" 900)"; echo "    $r"
[[ "$r" == OK* ]] && ok "installer ran" || bad "rerun: $r"
[ "$(get | j 'd["install"]["aborted"]')" = False ] && ok "record: finished ($(get | j 'len(d["install"]["problems"])') problems)" || bad "record after rerun"
[ "$(get | j 'not any(f["id"]=="install" and f["status"]=="problem" for f in d["report"]["findings"])')" = True ] && ok "report fresh after the repair" || bad "report not refreshed"
echo "failures: $fails"
