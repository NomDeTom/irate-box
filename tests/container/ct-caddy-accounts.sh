#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Accounts under Caddy (item 7): Debian's own Caddy in front of the hub, the admin gate
# (access.py caddy_admin_gate) with the box's own login on, then off, then with no gate files.
# Usage, from the checkout: docker run --rm -v "$PWD":/src:ro debian:trixie bash /src/tests/container/ct-caddy-accounts.sh
set -u
apt-get -qq update >/dev/null && apt-get -qq install -y caddy python3 curl git fcgiwrap >/dev/null 2>&1 || { echo "apt failed"; exit 2; }
cp -r /src /w && cd /w
caddy version
PW=right-password
HASH="$(caddy hash-password --plaintext "$PW")"
mkdir -p /tmp/access /tmp/st /tmp/etc /tmp/caddydir
render() { sed -e "s|\$2a\$14\$REPLACE_ME_WITH_CADDY_HASH_PASSWORD_OUTPUT|$1|" -e 's|^:80 {|:8088 {|' -e 's/\bbasic_auth\b/basicauth/' config/Caddyfile > /tmp/Caddyfile; }
gen() { python3 -c "
import sys; sys.path.insert(0, '/w')
from irate_box.hub import access
for n, t in access.caddy_snippets({'tools': 'private'}, sys.argv[1], 'basicauth', admin_login=sys.argv[2] == 'on').items():
    open('/tmp/access/' + n, 'w').write(t)" "$HASH" "$1"; }
# Repositories: one users may push to, one only the admin, one private (gitrepos' own config keys).
mkrepo() { git init -q --bare "/tmp/git/$1/$2.git" && git -C "/tmp/git/$1/$2.git" symbolic-ref HEAD refs/heads/main \
	&& git -C "/tmp/git/$1/$2.git" config irate-box.write "$3" \
	&& git -C "/tmp/git/$1/$2.git" config http.receivepack true \
	&& { [ "$1" = public ] && git -C "/tmp/git/$1/$2.git" config core.hooksPath /tmp/hooks || true; }; }
mkdir -p /tmp/git/public /tmp/git/private /tmp/hooks
# The push hook, recording who pushed (REMOTE_USER), then the real one.
printf '#!/bin/sh\necho "${REMOTE_USER:-}" >> /tmp/pushers\nexec /w/scripts/git-hooks-public/pre-receive\n' > /tmp/hooks/pre-receive && chmod +x /tmp/hooks/pre-receive
mkrepo public users-repo users; mkrepo public admin-repo admin; mkrepo private secret admin
git config --global safe.directory '*'; git config --global user.email t@example; git config --global user.name t
fcgiwrap -s unix:/run/irate-box-git.sock & sleep 0.5
export HUB_GIT_ROOT=/tmp/git HUB_CODE_DIR=/w
export HUB_ACCESS_DIR=/tmp/access HUB_FRONT_SECRET=s3cret HUB_CADDY_DIR=/tmp/caddydir HUB_STATIC=/w/web HUB_TOOLS_ROOT=/w/web
HUB_STATE_DIR=/tmp/st HUB_ETC_DIR=/tmp/etc PORT=8000 HUB_BIND=127.0.0.1 ./irate-box server >/tmp/hub.log 2>&1 &
caddy_up() { caddy run --adapter caddyfile --config /tmp/Caddyfile >/tmp/caddy.log 2>&1 & CADDY=$!; for _ in $(seq 40); do curl -s -o /dev/null http://127.0.0.1:8088/status && return; sleep 0.25; done; echo "caddy didn't start"; tail -5 /tmp/caddy.log; }
for _ in $(seq 60); do curl -s -o /dev/null http://127.0.0.1:8000/status && break; sleep 0.25; done
render "$HASH"; gen on
caddy validate --adapter caddyfile --config /tmp/Caddyfile >/tmp/validate.log 2>&1 && echo "validate: ok" || { echo "validate FAILED"; tail -5 /tmp/validate.log; exit 1; }
caddy_up
B=http://127.0.0.1:8088
fails=0
want() { local w=$1 what=$2; shift 2; local g; g="$(curl -s -o /dev/null -w '%{http_code}' "$@")"; if [ "$g" = "$w" ]; then echo "  ok   $what: $g"; else echo "  FAIL $what: $g (want $w)"; fails=$((fails+1)); fi; }
api() { curl -s -u "admin:$PW" -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' -d "$2" "$B$1"; }
acct() { curl -s -D /tmp/h -H 'Content-Type: application/json' -H 'X-Irate-Account: 1' -d "$1" "$B/api/account" >/dev/null; grep -i '^set-cookie' /tmp/h | sed -E 's/^[Ss]et-[Cc]ookie: ([^;]*).*/\1/' | tr -d '\r'; }
# An admin account and a user's, each logged in.
api /admin/accounts '{"action":"settings","signup":"apply"}' >/dev/null
for who in gina:admin erin:user; do
	n=${who%%:*}; r=${who##*:}
	resp="$(api /admin/accounts "{\"action\":\"make\",\"name\":\"$n\",\"role\":\"$r\"}")"; code="$(echo "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("code",""))')"
	acct "{\"action\":\"code\",\"code\":\"$code\",\"password\":\"password9\"}" >/dev/null
done
GTOK="$(acct '{"action":"login","name":"gina","password":"password9"}')"
UTOK="$(acct '{"action":"login","name":"erin","password":"password9"}')"
[ -n "$GTOK" ] && [ -n "$UTOK" ] && echo "  ok   two accounts logged in" || { echo "  FAIL accounts: '$GTOK' '$UTOK'"; tail -3 /tmp/hub.log; fails=$((fails+1)); }
echo "== the box's own login on"
want 401 "/admin/, nothing" "$B/admin/settings"
want 200 "/admin/, the login" -u "admin:$PW" "$B/admin/settings"
want 401 "/admin/, a wrong password" -u "admin:nope" "$B/admin/settings"
want 200 "/admin/, an admin's session" -H "Cookie: $GTOK" "$B/admin/settings"
want 401 "/admin/, a user's session" -H "Cookie: $UTOK" "$B/admin/settings"
want 401 "/admin/, a forged X-Irate-Session" -H "X-Irate-Session: admin" "$B/admin/settings"
want 502 "/term/, an admin's session (past the gate; no ttyd here)" -H "Cookie: $GTOK" "$B/term/"
want 401 "/term/, a user's session" -H "Cookie: $UTOK" "$B/term/"
want 502 "/sync/, the login (past the gate; no Syncthing)" -u "admin:$PW" "$B/sync/"
want 401 "/tools/ (private), nothing" "$B/tools/"
want 200 "/tools/ (private), an admin's session" -H "Cookie: $GTOK" "$B/tools/"
want 200 "/tools/ (private), the login" -u "admin:$PW" "$B/tools/"
echo "== git, through Caddy, as the hub decides"
gitok() { local w=$1 what=$2; shift 2; if "$@" >/tmp/git.out 2>&1; then r=ok; else r=refused; fi; if [ "$r" = "$w" ]; then echo "  ok   $what: $r"; else echo "  FAIL $what: $r (want $w)"; tail -2 /tmp/git.out; fails=$((fails+1)); fi; }
G=127.0.0.1:8088
push() { rm -rf /tmp/wc && git clone -q "http://$1@$G/git/$2.git" /tmp/wc 2>/dev/null || git init -q /tmp/wc; cd /tmp/wc && echo "$RANDOM" > f && git add f && git commit -qm x && git push -q "http://$1@$G/git/$2.git" HEAD:refs/heads/main; local rc=$?; cd /w; return $rc; }
export GIT_TERMINAL_PROMPT=0
gitok ok "users-repo: a guest clones" git ls-remote "http://$G/git/users-repo.git"
gitok refused "users-repo: a guest pushes" push "" users-repo
gitok ok "users-repo: a user pushes with their account" push "erin:password9" users-repo
tail -1 /tmp/pushers | grep -qx erin && echo "  ok   … and the hook saw erin (REMOTE_USER)" || { echo "  FAIL the hook saw '$(tail -1 /tmp/pushers)'"; fails=$((fails+1)); }
gitok refused "users-repo: a wrong password" push "erin:nope" users-repo
gitok refused "admin-repo: a user pushes" push "erin:password9" admin-repo
gitok ok "admin-repo: an admin account pushes" push "gina:password9" admin-repo
gitok ok "admin-repo: the box's own login pushes" push "admin:$PW" admin-repo
tail -1 /tmp/pushers | grep -qx admin && echo "  ok   … and the hook saw admin" || { echo "  FAIL the hook saw '$(tail -1 /tmp/pushers)'"; fails=$((fails+1)); }
gitok refused "private: a guest" git ls-remote "http://$G/git-private/secret.git"
gitok refused "private: a user's account" git ls-remote "http://erin:password9@$G/git-private/secret.git"
gitok ok "private: an admin account" git ls-remote "http://gina:password9@$G/git-private/secret.git"
gitok ok "private: the box's own login" git ls-remote "http://admin:$PW@$G/git-private/secret.git"
g="$(curl -s -o /dev/null -w '%{http_code}' -H "Cookie: $GTOK" "$B/git-private/")"
case "$g" in 401|302) echo "  FAIL private cgit view, an admin's session: $g"; fails=$((fails+1)) ;; *) echo "  ok   private cgit view, an admin's session: past the gate ($g; no cgit here)" ;; esac
want 401 "private cgit view, a guest" "$B/git-private/"
want 401 "private cgit view, a user's session" -H "Cookie: $UTOK" "$B/git-private/"
echo "== the box's own login off (an unknown hash, the hard gate), as hub_control.admin_login does"
kill $CADDY; wait $CADDY 2>/dev/null
render "$(caddy hash-password --plaintext "$(head -c 24 /dev/urandom | base64)")"; gen off
caddy_up
want 302 "/admin/, the old login: sent to log in" -u "admin:$PW" "$B/admin/settings"
loc="$(curl -s -o /dev/null -w '%{redirect_url}' "$B/admin/settings")"
[ "$loc" = "$B/account.html?next=/admin/settings" ] && echo "  ok   … to /account.html?next=/admin/settings" || { echo "  FAIL redirect: $loc"; fails=$((fails+1)); }
want 200 "/admin/, an admin's session" -H "Cookie: $GTOK" "$B/admin/settings"
want 302 "/admin/, a user's session" -H "Cookie: $UTOK" "$B/admin/settings"
want 302 "/term/, the old login" -u "admin:$PW" "$B/term/"
want 502 "/term/, an admin's session" -H "Cookie: $GTOK" "$B/term/"
want 302 "/tools/ (private), the old login" -u "admin:$PW" "$B/tools/"
want 200 "/tools/ (private), an admin's session" -H "Cookie: $GTOK" "$B/tools/"
gitok ok "private, own login off: an admin account's git credentials (no cookie, no redirect)" git ls-remote "http://gina:password9@$G/git-private/secret.git"
gitok refused "private, own login off: the old login" git ls-remote "http://admin:$PW@$G/git-private/secret.git"
echo "== no gate files (an older helper): the login alone"
kill $CADDY; wait $CADDY 2>/dev/null
rm -f /tmp/access/*; render "$HASH"; caddy_up
want 401 "/admin/, an admin's session" -H "Cookie: $GTOK" "$B/admin/settings"
want 200 "/admin/, the login" -u "admin:$PW" "$B/admin/settings"
echo "failures: $fails"
exit $fails
