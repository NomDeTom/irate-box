#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Kiwix as an add-on: --with-kiwix installs it with no book yet (and says it waits for one), a rerun keeps it,
# --remove kiwix takes it out and the books already there do not bring it back, --with-kiwix puts it back.
# Usage (in the container, after install.sh): ct-kiwix-addon.sh
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
U=/etc/systemd/system/kiwix.service R=/etc/hub/install-options
again() { bash /src/install.sh --src /src "$@" 2>&1; }
rm -f /var/lib/hub/zim/*.zim
out="$(again --with-kiwix)"
[ -f $U ] && grep -qx -- --with-kiwix $R && ok "--with-kiwix with no book: installed, in the record" || bad "unit $( [ -f $U ] && echo there || echo missing); record: $(tr '\n' ' ' <$R)"
[[ "$out" == *"waiting for a first book"* && "$out" != *"problem  Kiwix not started"* ]] && ok "it says it waits for a book, not a problem" || bad "said: $(grep -i kiwix <<<"$out" | head -3)"
again >/dev/null
grep -qx -- --with-kiwix $R && [ -f $U ] && ok "a rerun keeps it" || bad "rerun lost it: $(tr '\n' ' ' <$R)"
again --remove kiwix >/dev/null
[ ! -f $U ] && ! grep -qx -- --with-kiwix $R && [ -e /etc/hub/kiwix-removed ] && ok "--remove kiwix: unit and record gone, the removal marked" || bad "remove: unit $( [ -f $U ] && echo there || echo gone)"
install -o hub -g hub -m 644 /dev/null /var/lib/hub/zim/a-book.zim
again >/dev/null
[ ! -f $U ] && ok "a book already there does not bring it back" || bad "the book brought it back"
again --with-kiwix >/dev/null
[ -f $U ] && [ ! -e /etc/hub/kiwix-removed ] && ok "--with-kiwix puts it back, the mark cleared" || bad "not back"
rm -f /var/lib/hub/zim/a-book.zim
echo "failures: $fails"; exit $((fails > 0))
