#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# A rerun keeps the add-ons already on the box (the record lost --with-term; ttyd is enabled),
# says so, and --remove takes one away. Usage (in the container, after install.sh): ct-addons-kept.sh
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
systemctl enable --quiet ttyd
sed -i '/^--with-term$/d' /etc/hub/install-options
out="$(bash /src/install.sh --src /src 2>&1)"
[[ "$out" == *"Keeping the add-ons already on this box: term"* ]] && ok "the run says it keeps the terminal" || bad "no notice"
grep -qx -- --with-term /etc/hub/install-options && ok "--with-term back in the record" || bad "record: $(tr '\n' ' ' </etc/hub/install-options)"
[ "$(systemctl is-enabled ttyd)" = enabled ] && ok "ttyd still enabled" || bad "ttyd $(systemctl is-enabled ttyd)"
bash /src/install.sh --src /src --remove term >/dev/null 2>&1
grep -qx -- --with-term /etc/hub/install-options && bad "--remove left it in the record" || ok "--remove term: out of the record"
[ "$(systemctl is-enabled ttyd 2>&1)" != enabled ] && ok "ttyd disabled" || bad "ttyd still enabled"
echo "failures: $fails"; exit $((fails > 0))
