#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The package layout on an installed box: irate_box/root/ for root alone, everything started
# through the launcher, and the hub and its jobs still working without root/.
# Usage (in the container, after install.sh): ct-layout.sh PORT
H=127.0.0.1:$1
C=/opt/irate-box
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }

echo "== root/ is for root"
[ "$(stat -c '%a %U' $C/irate_box/root)" = "700 root" ] && ok "irate_box/root is 700 root" || bad "irate_box/root is $(stat -c '%a %U' $C/irate_box/root)"
runuser -u hub -- ls $C/irate_box/root >/dev/null 2>&1 && bad "hub can list root/" || ok "hub cannot list root/"
out="$(runuser -u hub -- $C/irate-box health 2>&1)"
[[ "$out" == *"irate_box/root/ is for root"* ]] && ok "launcher tells the hub user root/ is root's" || bad "launcher as hub: $out"
runuser -u hub -- env HUB_STATE_DIR=/var/lib/hub $C/irate-box librarian status >/dev/null 2>&1 && ok "hub runs the librarian" || bad "hub cannot run the librarian"
$C/irate-box health summary >/dev/null 2>&1; [ $? -le 1 ] && ok "root runs the doctor" || bad "root cannot run the doctor"

echo "== units start modules through the launcher"
for u in irate-box irate-box-control irate-box-uplink irate-box-librarian irate-box-ci; do
	ex="$(systemctl show -p ExecStart --value $u.service 2>/dev/null | grep -o 'path=[^ ;]*' | head -1)"
	[ -z "$ex" ] && continue
	[ "$ex" = "path=$C/irate-box" ] && ok "$u: $ex" || bad "$u: $ex"
done
[ "$(systemctl is-active irate-box)" = active ] && ok "irate-box active" || bad "irate-box $(systemctl is-active irate-box)"
[ "$(systemctl is-active irate-box-uplink)" = active ] && ok "irate-box-uplink active" || bad "irate-box-uplink $(systemctl is-active irate-box-uplink)"
code="$(curl -s -o /dev/null -w '%{http_code}' http://$H/status)"; [ "$code" = 200 ] && ok "/status 200" || bad "/status $code"
code="$(curl -s -o /dev/null -w '%{http_code}' http://$H/lock.js)"; [ "$code" = 200 ] && ok "/lock.js (web/) 200" || bad "/lock.js $code"

echo "== the source offer includes root/"
tar -tzf /var/lib/hub/source/irate-box-source.tar.gz | grep -q '/irate_box/root/health.py$' && ok "tarball has irate_box/root/health.py" || bad "tarball lacks root/"
runuser -u hub -- git -C /var/lib/hub/git/public/irate-box-source.git ls-tree -r --name-only main | grep -qx 'irate_box/root/health.py' && ok "git copy has irate_box/root/health.py" || bad "git copy lacks root/"
n_tar=$(tar -tzf /var/lib/hub/source/irate-box-source.tar.gz | grep -vc '/$')
n_git=$(runuser -u hub -- git -C /var/lib/hub/git/public/irate-box-source.git ls-tree -r --name-only main | wc -l)
[ "$n_tar" = "$n_git" ] && ok "tarball and git copy: $n_git files each" || bad "tarball $n_tar files, git $n_git"

echo "== git hook runs through the launcher"
hook="$(tail -1 $C/scripts/git-hooks/post-receive)"
[[ "$hook" == *'/../../irate-box" ci enqueue' ]] && ok "post-receive: $hook" || bad "post-receive: $hook"

echo; [ $fails = 0 ] && echo "layout: all passed" || echo "layout: $fails failed"
exit $((fails > 0))
