# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Three small routes to root stay shut (security review F6, F9, F12; next-work plan step 4):
the doctor's restart allow-list names the hub's own Syncthing only; ttyd has no credential
(it was the admin password, on its command line) and listens on a socket the web server
reaches; kiwix-manage reads a book as the hub, not root. python3 tests/root_routes_guard.py"""
import os, re, sys
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("HUB_STATE_DIR", "/nonexistent-state")
from irate_box.library import zimcheck  # noqa: E402
from irate_box.root import health, secdoctor  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

# F6
check("F6: syncthing@hub.service may be restarted", health.OUR_UNIT.match(f"syncthing@{health.HUB_USER}.service"))
for other in ("root", "lyra", "www-data"):
    check(f"F6: syncthing@{other}.service may not", not health.OUR_UNIT.match(f"syncthing@{other}.service"))
check("F6: the doctor's own reading of OUR_UNIT is not 'wide'",
      not re.search(r"syncthing@\[", (REPO / "irate_box/root/health.py").read_text()))

# F9
install = (REPO / "install.sh").read_text()
unit = install[install.index("cat >/etc/systemd/system/ttyd.service"):]
unit = unit[:unit.index("\nEOF")]
start = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
check("F9: ttyd's unit has no credential", "--credential" not in start and " -c " not in start, start)
check("F9: ttyd's unit reads no env file (ttyd.env held the password)", "EnvironmentFile" not in unit, unit)
check("F9: ttyd listens on its socket", "--interface $TTYD_SOCKET" in unit and "TTYD_SOCKET=/run/ttyd/ttyd.sock" in install)
check("F9: the install removes an old ttyd.env", 'rm -f "$ETC/ttyd.env"' in install)
nginx = (REPO / "config/irate-box.nginx").read_text()
term = nginx[nginx.index("location /term/"):]
term = term[:term.index("\n\t}")]
check("F9: nginx's /term/ asks for the admin login", "auth_basic " in term and "auth_basic_user_file" in term, term)
check("F9: nginx's /term/ goes to ttyd's socket", "proxy_pass http://unix:/run/ttyd/ttyd.sock;" in term, term)
caddy = (REPO / "config/Caddyfile").read_text()
term = caddy[caddy.index("handle /term/*"):]
term = term[:term.index("redir /term")]
check("F9: Caddy's /term/ asks for the admin login, to ttyd's socket",
      "basic_auth" in term and "reverse_proxy unix//run/ttyd/ttyd.sock" in term, term)
leftover = [str(p.relative_to(REPO)) for p in list((REPO / "irate_box").rglob("*.py")) + [REPO / "install.sh"]
            if "TTYD_CREDENTIAL" in p.read_text()]
check("F9: nothing writes TTYD_CREDENTIAL any more", not leftover, leftover)
show = lambda argv: {"ExecStart": "{ path=/usr/local/bin/ttyd ; argv[]=" + " ".join(argv) + " ; ignore_errors=no ; }"}
said = secdoctor.probe_ttyd({}, {}, show(["/usr/local/bin/ttyd", "--interface", "lo", "--port", "7681", "/bin/login"]))
check("F9: the doctor warns of ttyd on a TCP port", any(s == "warn" and "TCP" in m for s, m, _ in said), said)
said = secdoctor.probe_ttyd({}, {}, show(["/usr/local/bin/ttyd", "--interface", "/run/ttyd/ttyd.sock", "/bin/login"]))
check("F9: and not of ttyd on its socket", said == [], said)

# F12
ran = []
def fake_run(cmd, **kw):
    ran.append(cmd)
    return mock.Mock(returncode=1, stderr="no", stdout="")
with mock.patch.object(zimcheck.shutil, "which", return_value="/usr/bin/kiwix-manage"), \
     mock.patch.object(zimcheck.shutil, "chown") as chown, \
     mock.patch.object(zimcheck.subprocess, "run", side_effect=fake_run), \
     mock.patch.object(zimcheck.os, "geteuid", return_value=0):
    zimcheck.kiwix_problem("/var/lib/hub/zim/x.zim", user="hub")
    check("F12: as root, kiwix-manage runs through runuser -u hub", ran and ran[-1][:4] == ["runuser", "-u", "hub", "--"], ran)
    check("F12: its scratch folder is the hub's", chown.called and chown.call_args[0][1:] == ("hub", "hub"), chown.call_args)
    with mock.patch.object(zimcheck, "header_problem", return_value=None):
        zimcheck.problem("/tmp/y.zim", user="hub")
    check("F12: problem() passes the user on", ran[-1][:3] == ["runuser", "-u", "hub"], ran[-1])
with mock.patch.object(health.zimcheck, "kiwix_problem", return_value=None) as kp:
    health.kiwix_reads("/var/lib/hub/zim/x.zim")
check("F12: the health scan asks as the hub", kp.call_args.kwargs.get("user") == health.HUB_USER, kp.call_args)
check("F12: the USB import checks a copy as the hub",
      "zimcheck.problem(tmp, user=hub_user)" in (REPO / "irate_box/root/usbstick.py").read_text())

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
