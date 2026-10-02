#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Book checks in the test container: the librarian, the USB import and /status. Usage: ct-zim.sh PORT
P=$1; Z=/var/lib/hub/zim; C=/opt/irate-box
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
GOOD=$Z/mermaid-docs.zim
[ -f $GOOD ] || { echo "needs $GOOD"; exit 1; }
sum0=$(md5sum < $GOOD)
W=$(mktemp -d); chmod 755 $W
head -c 5000000 $GOOD > $W/cut.zim; printf '<html>404</html>' > $W/page.zim; cp $GOOD $W/whole.zim; chmod 644 $W/*

echo "== zimcheck"
cd $C
r=$(python3 -c "import sys; from irate_box.library import zimcheck; print(zimcheck.problem(sys.argv[1]))" $W/whole.zim); [ "$r" = None ] && ok "whole book passes" || bad "whole: $r"
r=$(python3 -c "import sys; from irate_box.library import zimcheck; print(zimcheck.problem(sys.argv[1]))" $W/cut.zim); [[ "$r" == truncated* ]] && ok "truncated: $r" || bad "cut: $r"
r=$(python3 -c "import sys; from irate_box.library import zimcheck; print(zimcheck.problem(sys.argv[1]))" $W/page.zim); [[ "$r" == *HTML* ]] && ok "html: $r" || bad "html: $r"

echo "== librarian: a truncated download is refused, the book in use untouched"
python3 -m http.server 18555 --directory $W >/dev/null 2>&1 & HP=$!; sleep 1
out=$(runuser -u hub -- env HUB_STATE_DIR=/var/lib/hub python3 -c "
from irate_box.library import librarian
src = {'name': 'mermaid-docs'}
cand = {'url': 'http://127.0.0.1:18555/cut.zim', 'auth': None, 'size': 0, 'zip': False, 'version': 'v-bad', 'label': 'bad'}
try:
    librarian.install(src, cand, librarian.load_config()['policy'], {})
    print('INSTALLED')
except librarian.LibrarianError as e:
    print('REFUSED', e)
" 2>&1)
echo "    $out"
[[ "$out" == REFUSED*truncated* ]] && ok "download refused with the reason" || bad "download: $out"
[ "$(md5sum < $GOOD)" = "$sum0" ] && ok "book in use unchanged" || bad "book changed!"
ls -a $Z | grep -q "\.mermaid-docs.zim\.\(fetched\|new\)" && bad "staged leftovers" || ok "no staged leftovers"
out=$(runuser -u hub -- env HUB_STATE_DIR=/var/lib/hub python3 -c "
from irate_box.library import librarian
cand = {'url': 'http://127.0.0.1:18555/whole.zim', 'auth': None, 'size': 0, 'zip': False, 'version': 'v-good', 'label': 'good'}
st = {}
librarian.install({'name': 'mermaid-docs'}, cand, librarian.load_config()['policy'], st)
print('INSTALLED', st['current']['version'])
" 2>&1); [[ "$out" == "INSTALLED v-good" ]] && ok "a whole download still goes in" || bad "good download: $out"
kill $HP

echo "== librarian: a rollback to a damaged archived version is refused"
A=/var/lib/hub/library/archive/mermaid-docs; mkdir -p $A; cp $W/cut.zim $A/v-old.zim; chown -R hub:hub /var/lib/hub/library/archive; touch $A/v-old.zim
sum1=$(md5sum < $GOOD)
out=$(runuser -u hub -- env HUB_STATE_DIR=/var/lib/hub $C/irate-box librarian rollback mermaid-docs 2>&1); echo "    $out" | tail -1
[[ "$out" == *"archived version v-old was not put in place"* ]] && ok "rollback refused" || bad "rollback: $out"
[ "$(md5sum < $GOOD)" = "$sum1" ] && ok "book in use unchanged" || bad "book changed by rollback!"
rm -f $A/v-old.zim

echo "== USB import: checked on the stick, and again as copied"
python3 - $W <<'PY'
import sys, os, shutil, contextlib
sys.path.insert(0, "/opt/irate-box")
from irate_box.root import usbstick
from pathlib import Path
stick = Path(sys.argv[1])
usbstick._find = lambda s, d: {"name": d}
@contextlib.contextmanager
def fake(dev, writable=False):
    yield stick
usbstick.Mounted = fake
def imp(f):
    try:
        return "IMPORTED " + usbstick.import_zim("sdz1", f, "/var/lib/hub/zim", "hub", 0, ["/opt/irate-box/irate-box", "librarian"], "/var/lib/hub")
    except ValueError as e:
        return "REFUSED " + str(e)
print("scan:", [(z["file"], z["zim"], z.get("problem", "")[:30]) for z in usbstick._zims(stick)])
print("cut:", imp("cut.zim"))
# A copy that arrives damaged although the stick's file looked whole.
real_copy = usbstick._copy
def bad_copy(src, dest, report=None):
    real_copy(src, dest, report)
    with open(dest, "r+b") as fh: fh.truncate(4000000)
usbstick._copy = bad_copy
print("damaged:", imp("whole.zim"))
print("left:", sorted(os.listdir("/var/lib/hub/zim")))
PY

echo "== /status says why Kiwix is down"
why() { sleep 6; curl -s http://127.0.0.1:$P/status | python3 -c "import json,sys; s=[x for x in json.load(sys.stdin)['services'] if x['path']=='/wiki/'][0]; print(s['state'], '|', s.get('why'))"; }
systemctl stop kiwix; r=$(why); echo "    $r"; [[ "$r" == "stopped | Kiwix is not running"* ]] && ok "stopped, books fine" || bad "$r"
mv $Z/library.xml $W/lib.bak; r=$(why); echo "    $r"; [[ "$r" == *"library is empty"* ]] && ok "no library" || bad "$r"
mv $GOOD $W/held.zim; cp $W/cut.zim $Z/cut.zim; r=$(why); echo "    $r"; [[ "$r" == *"None of the 1 book can be read"* ]] && ok "only a damaged book" || bad "$r"
rm $Z/cut.zim; r=$(why); echo "    $r"; [[ "$r" == "stopped | No books yet"* ]] && ok "no books" || bad "$r"
mv $W/held.zim $GOOD; mv $W/lib.bak $Z/library.xml; chown hub:hub $GOOD $Z/library.xml; systemctl start kiwix; sleep 6
r=$(curl -s http://127.0.0.1:$P/status | python3 -c "import json,sys; s=[x for x in json.load(sys.stdin)['services'] if x['path']=='/wiki/'][0]; print(s['state'], s.get('why'))"); [ "$r" = "running None" ] && ok "back: running, no reason shown" || bad "back: $r"
rm -rf $W
echo "failures: $fails"
