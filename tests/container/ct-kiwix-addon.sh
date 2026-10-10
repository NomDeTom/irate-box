#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Kiwix as an add-on: --with-kiwix installs it with the box's own guide as its first book (made at install), a rerun
# keeps it, --remove kiwix takes it out with its guide and the books already there do not bring it back, --with-kiwix
# puts it back.
# Usage (in the container, after install.sh): ct-kiwix-addon.sh
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
U=/etc/systemd/system/kiwix.service R=/etc/hub/install-options
again() { bash /src/install.sh --src /src "$@" 2>&1; }
rm -f /var/lib/hub/zim/*.zim
out="$(again --with-kiwix)"
[ -f $U ] && grep -qx -- --with-kiwix $R && ok "--with-kiwix with no book: installed, in the record" || bad "unit $( [ -f $U ] && echo there || echo missing); record: $(tr '\n' ' ' <$R)"
G=/var/lib/hub/zim/irate-box-guide.zim
[ -s $G ] && grep -q 'irate-box-guide' /var/lib/hub/zim/library.xml && [[ "$out" != *"problem  Kiwix not started"* ]] \
	&& ok "the box's guide its first book, in the library" || bad "guide $( [ -s $G ] && echo made || echo missing); said: $(grep -i -e kiwix -e guide <<<"$out" | head -3)"
for _ in $(seq 20); do curl -s -o /dev/null -w '%{http_code}' -L http://127.0.0.1:8081/wiki/ | grep -q 200 && break; sleep 1; done
curl -s -L http://127.0.0.1:8081/wiki/content/irate-box-guide/first-use.html | grep -q "Setting up a new box" && ok "Kiwix serves the guide" || bad "the guide not served"
again >/dev/null
grep -qx -- --with-kiwix $R && [ -f $U ] && ok "a rerun keeps it" || bad "rerun lost it: $(tr '\n' ' ' <$R)"
again --remove kiwix >/dev/null
[ ! -f $U ] && ! grep -qx -- --with-kiwix $R && [ -e /etc/hub/kiwix-removed ] && [ ! -e $G ] && ok "--remove kiwix: unit, record and its guide gone, the removal marked" || bad "remove: unit $( [ -f $U ] && echo there || echo gone), guide $( [ -e $G ] && echo there || echo gone)"
install -o hub -g hub -m 644 /dev/null /var/lib/hub/zim/a-book.zim
again >/dev/null
[ ! -f $U ] && ok "a book already there does not bring it back" || bad "the book brought it back"
again --with-kiwix >/dev/null
[ -f $U ] && [ ! -e /etc/hub/kiwix-removed ] && [ -s $G ] && ok "--with-kiwix puts it back with its guide, the mark cleared" || bad "not back"
rm -f /var/lib/hub/zim/a-book.zim
echo "failures: $fails"; exit $((fails > 0))
