#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# FreeOCD's WebDebugger in the flashing tools: served at /flasher/freeocd/ as its release holds it, its version and
# notices beside it, its scripts as JavaScript; the Flashing tools list naming it. Usage: ct-freeocd.sh PORT
H=127.0.0.1:${1:-80}
fails=0
check() { local want=$1; shift; local got; got=$("$@" 2>/dev/null); [ "$got" = "$want" ] && echo "  ok   $* -> $got" || { echo "  FAIL $* -> $got (want $want)"; fails=$((fails + 1)); }; }
code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }
check 200 code "http://$H/flasher/freeocd/"
check 0.0.3 sh -c "curl -s http://$H/flasher/freeocd/VERSION"
check 1 sh -c "curl -s http://$H/flasher/freeocd/THIRD-PARTY-NOTICES.txt | grep -c '^BSD 3-Clause License'"
check 1 sh -c "curl -s -D - -o /dev/null http://$H/flasher/freeocd/lib/dap.umd.js | grep -ci '^content-type: [a-z/]*javascript'"
check 1 sh -c "curl -s http://$H/flashing.html | grep -c 'href=\"/flasher/freeocd/\"'"
echo "failures: $fails"; exit $((fails > 0))
