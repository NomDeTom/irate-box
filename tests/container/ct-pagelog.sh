#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The pages the web server serves itself, logged as they are loaded (time, status, path; nothing about who asked),
# for the hub's use heatmap: a page load is a line, a script or picture is not, the rest of the box no line at all.
# Usage: ct-pagelog.sh PORT
H=127.0.0.1:${1:-80}; L=/run/irate-box/pagelog/pages.log
fails=0; ok() { echo "  ok   $*"; }; bad() { echo "  FAIL $*"; fails=$((fails + 1)); }
before=$(wc -l <"$L" 2>/dev/null || echo 0)
curl -s -o /dev/null "http://$H/flasher/freeocd/"
curl -s -o /dev/null "http://$H/flasher/freeocd/lib/dap.umd.js"
curl -s -o /dev/null "http://$H/"
sleep 1
new=$(tail -n +"$((before + 1))" "$L" 2>/dev/null)
[ "$(printf '%s\n' "$new" | grep -cE ' 200 /flasher/freeocd/(index.html)?$')" = 1 ] && ok "a page load: one line, with its status and path" || bad "page load not logged: $new"
printf '%s\n' "$new" | grep -q 'dap.umd.js' && bad "a script was logged" || ok "its script not logged"
[ "$(printf '%s\n' "$new" | grep -c .)" = 1 ] && ok "the hub's own page not logged" || bad "more lines than the one page: $new"
printf '%s\n' "$new" | grep -qE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' && bad "an address in the log" || ok "no address in the log"
[ -f /etc/logrotate.d/irate-box-pages ] && ok "logrotate keeps it small" || bad "no logrotate entry"
if findmnt -n -o FSTYPE --target "$L" | grep -q tmpfs; then ok "the log is in RAM (/run)"; else bad "the log is not in RAM"; fi
if [ -f /etc/tmpfiles.d/irate-box-pagelog.conf ]; then ok "its folder made at every boot"; else bad "no tmpfiles rule"; fi
echo "failures: $fails"; exit $((fails > 0))
