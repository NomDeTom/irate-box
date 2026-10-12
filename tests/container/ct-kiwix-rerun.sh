#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# An update (install.sh run again with the box's options, as the updater runs it) leaves Kiwix
# serving when nothing it serves changed, and restarts it when its unit did. Usage: ct-kiwix-rerun.sh
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
inv() { systemctl show -p InvocationID --value kiwix; }
systemctl is-active --quiet kiwix || { echo "  skip kiwix is not running here (no readable book)"; exit 0; }
rerun() { bash /src/install.sh --src /src $(tr '\n' ' ' </etc/hub/install-options) >/tmp/rerun.log 2>&1; }
before=$(inv)
rerun || bad "install.sh again failed: $(tail -3 /tmp/rerun.log)"
grep -q "kiwix: unchanged, left running" /tmp/rerun.log && ok "said: kiwix unchanged, left running" || bad "not said: $(grep -i kiwix /tmp/rerun.log | tail -2)"
[ "$(inv)" = "$before" ] && ok "the same Kiwix still running after an update" || bad "Kiwix was restarted ($before -> $(inv))"
rm -f /var/lib/hub/.kiwix-print
rerun || bad "install.sh again failed"
[ "$(inv)" != "$before" ] && systemctl is-active --quiet kiwix && ok "its record gone (as when its unit or book changed): restarted, and running" \
	|| bad "not restarted when its record changed"
exit $fails
