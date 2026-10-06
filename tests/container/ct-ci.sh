#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
H=127.0.0.1 PW=correct-horse-1
export GIT_TERMINAL_PROMPT=0 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@x GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@x
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails+1)); }
admin() { curl -s -u "admin:$PW" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "$1" "http://$H/admin/git" >/dev/null; }
ci() { curl -s -u "admin:$PW" "http://$H/admin/ci"; }
wait_runs() { for _ in $(seq 60); do n=$(ci | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(1 for r in d["runs"] if r["state"]!="running"), d["queued"])'); [ "${n%% *}" -ge "$1" ] && [ "${n##* }" = 0 ] && return 0; sleep 1; done; return 1; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT; cd "$W"
admin '{"action":"create","area":"private","name":"citest"}'; admin '{"action":"create","area":"public","name":"pubci"}'
[ "$(runuser -u hub -- git -C /var/lib/hub/git/private/citest.git config core.hooksPath)" = /opt/irate-box/scripts/git-hooks ] && ok "private repo has the hub's hooks" || bad "hooksPath"
[ -z "$(runuser -u hub -- git -C /var/lib/hub/git/public/pubci.git config core.hooksPath)" ] && ok "public repo has none" || bad "public hooksPath"
git init -q -b main p && cd p && cat > .irate-ci.sh <<'S'
echo "building $CI_REPO $CI_BRANCH $CI_COMMIT as $(id -un), HOME=$HOME"
uname -m > "$CI_ARTIFACTS/arch.txt"
echo cached >> "$HOME/cache-marker"; wc -l < "$HOME/cache-marker"
S
git add . && git commit -qm "ci test"
out=$(git push "http://admin:$PW@$H/git-private/citest.git" main 2>&1)
echo "$out" | grep -q "build queued for citest main" && ok "push says the build is queued" || bad "push output: $out"
wait_runs 1 && ok "build finished" || bad "build did not finish"
st=$(ci | python3 -c 'import json,sys; r=json.load(sys.stdin)["runs"][0]; print(r["state"], r["repo"], r["branch"], r["artifacts"], r["run"])')
[ "${st%% *}" = passed ] && ok "run: $st" || bad "run: $st"
run=$(echo "$st" | awk '{print $NF}')
log=$(curl -s -u "admin:$PW" "http://$H/admin/ci/file?run=$run&name=log.txt")
echo "$log" | grep -q "as hubci, HOME=/var/lib/hub/ci/home" && ok "built as hubci with the persistent HOME" || bad "log: $log"
[ "$(curl -s -u "admin:$PW" "http://$H/admin/ci/file?run=$run&name=arch.txt")" = x86_64 ] && ok "artifact downloadable" || bad "artifact"
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://$H/admin/ci/file?run=$run&name=log.txt")" = 401 ] && ok "build files need the login" || bad "build files open"
[ "$(curl -s -o /dev/null -w '%{http_code}' -u "admin:$PW" "http://$H/admin/ci/file?run=../../etc&name=passwd")" = 404 ] && ok "no path escape" || bad "path escape"
echo "exit 3" >> .irate-ci.sh && git commit -qam fail && git push -q "http://admin:$PW@$H/git-private/citest.git" main 2>/dev/null
wait_runs 2; st=$(ci | python3 -c 'import json,sys; r=json.load(sys.stdin)["runs"][0]; print(r["state"])')
[ "$st" = failed ] && ok "a failing script is reported failed" || bad "failing run: $st"
curl -s -u "admin:$PW" "http://$H/admin/ci/file?run=citest/2&name=log.txt" | grep -q "^2$" && ok "HOME kept between builds (cache)" || bad "cache not kept"
before=$(ci | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["runs"]))')
git push -q "http://admin:$PW@$H/git/pubci.git" main 2>/dev/null; sleep 4
after=$(ci | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["runs"]))')
[ "$before" = "$after" ] && ok "a push to a public repo builds nothing" || bad "public push built"
stat -c '%U' /var/lib/hub/ci/runs/citest | grep -qx hubci && ok "runs owned by hubci" || bad "runs owner"
admin '{"action":"delete","area":"private","name":"citest"}'; admin '{"action":"delete","area":"public","name":"pubci"}'
echo "== result: $fails failed"; exit $fails
