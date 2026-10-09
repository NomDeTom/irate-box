#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# --with-irc: ngIRCd runs as the irc user from irate-box's own config (Debian's conffile left
# alone), answers on :6667 with #lobby and a limit per address, the how-to-join page is
# served, and --remove takes it away again. Usage (in the container, after install.sh): ct-irc.sh PORT
H=127.0.0.1:$1
fails=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; fails=$((fails + 1)); }

# What a client sees: registered, and the lobby's topic on join. Prints "registered topic" or what failed.
irc_try() {
	python3 - "$@" <<'PY'
import socket, sys, time
try:
    s = socket.create_connection(("127.0.0.1", 6667), timeout=5)
except OSError as e:
    print("refused"); sys.exit(0)
s.sendall(b"NICK ct\r\nUSER ct 0 * :ct\r\nJOIN #lobby\r\n")
end, got = time.time() + 5, b""
s.settimeout(0.5)
while time.time() < end and not (b" 332 " in got and b" 366 " in got):
    try:
        d = s.recv(4096)
        if not d: break
        got += d
    except socket.timeout:
        pass
print(("registered " if b" 001 " in got else "") + ("topic" if b" 332 " in got else "no-topic"))
PY
}

echo "== add"
bash /src/install.sh --src /src --with-irc >/dev/null 2>&1
[ "$(systemctl is-active ngircd)" = active ] && ok "ngircd active" || bad "ngircd $(systemctl is-active ngircd)"
[ "$(systemctl is-enabled ngircd 2>&1)" = enabled ] && ok "ngircd enabled" || bad "ngircd $(systemctl is-enabled ngircd 2>&1)"
grep -qx -- --with-irc /etc/hub/install-options && ok "--with-irc in the record" || bad "record: $(tr '\n' ' ' </etc/hub/install-options)"
systemctl show -p ExecStart --value ngircd | grep -q -- '-f /etc/ngircd/irate-box.conf' && ok "the unit runs irate-box's config" || bad "ExecStart: $(systemctl show -p ExecStart --value ngircd | cut -c1-120)"
[ "$(ps -o user= -C ngircd | sort -u | tr -d ' \n')" = irc ] && ok "ngircd runs as irc" || bad "runs as: $(ps -o user= -C ngircd | sort -u | tr '\n' ' ')"
dpkg -V ngircd 2>/dev/null | grep -q '/etc/ngircd/ngircd.conf' && bad "Debian's ngircd.conf was changed" || ok "Debian's ngircd.conf untouched"
ngircd --configtest -f /etc/ngircd/irate-box.conf >/dev/null 2>&1 && ok "the config passes ngircd --configtest" || bad "configtest fails"
[ "$(irc_try)" = "registered topic" ] && ok "a client registers and joins #lobby, which has its topic" || bad "client: $(irc_try)"
t0=$(date +%s); irc_try >/dev/null; [ $(($(date +%s) - t0)) -le 4 ] && ok "registration is quick (no DNS or ident wait)" || bad "registration took $(($(date +%s) - t0)) s"
code="$(curl -s -o /dev/null -w '%{http_code}' http://$H/irc.html)"; [ "$code" = 200 ] && ok "/irc.html 200" || bad "/irc.html $code"
curl -s http://$H/status | grep -q 'IRC server (ngIRCd)' && ok "/status lists it" || bad "/status does not list it"
bash /src/install.sh --src /src >/dev/null 2>&1
grep -qx -- --with-irc /etc/hub/install-options && [ "$(systemctl is-active ngircd)" = active ] && ok "a rerun naming nothing keeps it" || bad "a rerun dropped it"

echo "== remove"
bash /src/install.sh --src /src --remove irc >/dev/null 2>&1
[ "$(systemctl is-active ngircd 2>&1)" != active ] && ok "ngircd stopped" || bad "ngircd still active"
[ "$(systemctl is-enabled ngircd 2>&1)" != enabled ] && ok "ngircd disabled" || bad "ngircd still enabled"
grep -qx -- --with-irc /etc/hub/install-options && bad "--remove left it in the record" || ok "out of the record"
ls /etc/ngircd/irate-box.conf /etc/ngircd/irate-box.motd /etc/systemd/system/ngircd.service.d/irate-box.conf >/dev/null 2>&1 && bad "config or drop-in left behind" || ok "config and drop-in gone"
[ "$(irc_try)" = refused ] && ok ":6667 closed" || bad ":6667: $(irc_try)"

echo; [ $fails = 0 ] && echo "irc: all passed" || echo "irc: $fails failed"
exit $((fails > 0))
