#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Git server checks inside a test container. Usage: ct-git.sh WEB (nginx|caddy); hub on :80,
# admin password correct-horse-1.
WEB=$1 H=127.0.0.1 PW=correct-horse-1
export GIT_TERMINAL_PROMPT=0 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@x GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@x
# Where cgit's web pages (and the private browse, behind the login) live depends on the front:
# under nginx they are on the git origin (a port of its own, for origin isolation — the main
# origin 302-redirects browse there); under Caddy they are on the main origin. Either way the
# smart-HTTP clone/push stays on the main origin ($H). Find the browse origin from where /git/
# goes: a 302 means its own origin, no redirect means the main one.
GB=$(curl -s -o /dev/null -w '%{redirect_url}' "http://$H/git/")
if [ -n "$GB" ]; then GB=$(printf '%s' "$GB" | sed -E 's#(https?://[^/]+)/.*#\1#'); else GB="http://$H"; fi
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
check() { local want=$1; shift; local got; got="$("$@" 2>/dev/null)"; [ "$got" = "$want" ] && ok "$* -> $got" || bad "$* -> $got (want $want)"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$@"; }
admin() { curl -s -u "admin:$PW" -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "$1" "http://$H/admin/git"; }
W=$(mktemp -d); cd "$W" || exit 1
for area in public private; do for n in demo secret big; do admin "{\"action\":\"delete\",\"area\":\"$area\",\"name\":\"$n\"}" >/dev/null; done; done

echo "== units and socket"
check "660 root" stat -c '%a %U' /run/irate-box-git.sock
want_group=www-data; [ "$WEB" = caddy ] && want_group=caddy
check "$want_group" stat -c '%G' /run/irate-box-git.sock

echo "== admin: create"
check 401 code -X POST -d '{}' "http://$H/admin/git"
check "demo" sh -c "curl -s -u admin:$PW -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d '{\"action\":\"create\",\"area\":\"public\",\"name\":\"demo\",\"description\":\"a demo\"}' http://$H/admin/git | python3 -c 'import json,sys; print(\" \".join(r[\"name\"] for r in json.load(sys.stdin)[\"repos\"] if r[\"name\"] != \"irate-box-source\"))'"
admin '{"action":"create","area":"private","name":"secret.git"}' >/dev/null
check "a name is letters, digits, '.', '_' and '-', up to 64, starting with a letter or digit" sh -c "curl -s -u admin:$PW -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d '{\"action\":\"create\",\"area\":\"public\",\"name\":\"../evil\"}' http://$H/admin/git | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"error\"])'"
check "hub" stat -c '%U' /var/lib/hub/git/public/demo.git
check "true" runuser -u hub -- git -C /var/lib/hub/git/public/demo.git config http.receivepack

echo "== public: clone for all, push with the login"
git clone -q "http://$H/git/demo.git" pub 2>/dev/null && ok "anonymous clone (empty)" || bad "anonymous clone"
cd pub && echo hello >README && git add README && git commit -qm first && git branch -M main
git push -q origin main 2>/dev/null && bad "anonymous push went through" || ok "anonymous push refused"
git push -q "http://admin:$PW@$H/git/demo.git" main 2>/dev/null && ok "push with the login" || bad "push with the login"
cd "$W"
git clone -q "http://$H/git/demo.git" pub2 2>/dev/null && [ "$(cat pub2/README)" = hello ] && ok "anonymous clone has the commit" || bad "clone after push"
check 401 code "http://$H/git/demo.git/info/refs?service=git-receive-pack"
check 200 code "http://$H/git/demo.git/info/refs?service=git-upload-pack"

echo "== cgit (on the git origin)"
check 200 code "$GB/git/"
check 1 sh -c "curl -s $GB/git/ | grep -c 'demo.git'"
check 200 code "$GB/git/demo.git/"
check 1 sh -c "curl -s $GB/git/demo.git/ | grep -c 'a demo' | sed 's/^[1-9][0-9]*$/1/'"
check 200 code "$GB/git/demo.git/tree/README"
check 1 sh -c "curl -s $GB/git/demo.git/tree/README | grep -c hello | sed 's/^[1-9][0-9]*$/1/'"
check 1 sh -c "curl -s -H 'Host: box.example' $GB/git/demo.git/ | grep -c 'http://box.example/git/demo.git' | sed 's/^[1-9][0-9]*$/1/'"
check 200 code "$GB/git/demo.git/snapshot/demo-main.tar.gz"
check 200 code "$GB/git-static/cgit.css"
check 301 code "http://$H/git"
check 0 sh -c "curl -s $GB/git/ | grep -c secret"

echo "== private: all behind the login (browse on the git origin, clone on the main origin)"
check 401 code "$GB/git-private/"
check 200 code -u "admin:$PW" "$GB/git-private/"
check 401 code "http://$H/git-private/secret.git/info/refs?service=git-upload-pack"
git clone -q "http://$H/git-private/secret.git" priv 2>/dev/null && bad "anonymous private clone" || ok "anonymous private clone refused"
git clone -q "http://admin:$PW@$H/git-private/secret.git" priv 2>/dev/null && ok "private clone with the login" || bad "private clone with the login"
cd priv && echo s >S && git add S && git commit -qm s && git push -q origin HEAD:main 2>/dev/null && ok "private push" || bad "private push"
cd "$W"
check 1 sh -c "curl -s -u admin:$PW $GB/git-private/ | grep -c 'secret.git' | sed 's/^[1-9][0-9]*$/1/'"

echo "== push presets"
preset() { admin "{\"action\":\"preset\",\"area\":\"public\",\"name\":\"demo\",\"preset\":\"$1\"}" >/dev/null; }
preset public-everything
cd pub && echo more >>README && git commit -qam second
git push -q "http://$H/git/demo.git" main 2>/dev/null && ok "public-everything: a guest pushes" || bad "public-everything: a guest pushes"
cd "$W"
check 401 code "http://$H/git-private/secret.git/info/refs?service=git-receive-pack"
preset public-admin-writes
check 401 code "http://$H/git/demo.git/info/refs?service=git-receive-pack"
check 401 code "http://$H/git/demo.git/info/refs?service=git%2Dreceive-pack"
check 200 code -u "admin:$PW" "http://$H/git/demo.git/info/refs?service=git-receive-pack"
preset public-read-only
check 403 code -u "admin:$PW" "http://$H/git/demo.git/info/refs?service=git-receive-pack"
check 200 code "http://$H/git/demo.git/info/refs?service=git-upload-pack"
preset public-admin-writes

echo "== push size cap"
admin '{"action":"create","area":"public","name":"big"}' >/dev/null
git init -q bigrepo && cd bigrepo && head -c 80000000 /dev/urandom >blob && git add blob && git commit -qm big
out=$(git -c http.postBuffer=200000000 push "http://admin:$PW@$H/git/big.git" HEAD:main 2>&1)
echo "$out" | grep -qE '413|too large|RPC failed' && ok "an 80 MB push is refused" || bad "80 MB push: $(echo "$out" | tail -2 | tr '\n' ' ')"
cd "$W"

echo "== admin: list, describe, delete"
check "demo 2 1" sh -c "curl -s -u admin:$PW http://$H/admin/git | python3 -c 'import json,sys; r=[x for x in json.load(sys.stdin)[\"repos\"] if x[\"name\"]==\"demo\"][0]; print(r[\"name\"], len([1]) + 1 if r[\"last_commit\"] else 0, r[\"branches\"])'"
admin '{"action":"describe","area":"public","name":"demo","description":"renamed desc"}' >/dev/null
check "renamed desc" cat /var/lib/hub/git/public/demo.git/description
admin '{"action":"delete","area":"public","name":"big"}' >/dev/null
check "no" sh -c "[ -e /var/lib/hub/git/public/big.git ] && echo yes || echo no"
check "running" sh -c "curl -s http://$H/status | python3 -c 'import json,sys; print([s[\"state\"] for s in json.load(sys.stdin)[\"services\"] if s[\"path\"]==\"/git/\"][0])'"

echo "== fcgiwrap runs as the hub"
check "hub" sh -c "ps -o user= -C fcgiwrap | sort -u | head -1"

rm -rf "$W"
echo "== result: $fails failed"
exit $fails
