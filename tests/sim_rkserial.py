# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A new board set up over its serial console (root/rkserial.py), offline, against a stand-in console on a pty
(tests/fixtures/fake_firstlogin.py: armbian-firstlogin's questions in its words, then a root shell that answers
without running anything): every first-login question answered, the locale skipped, the user's real name cleared
before it is typed; then the name, time zone, keys, setup page, WiFi and root's lock set at the shell, and the
address read; passwords generated of letters and digits; a password garbled on the way in asked again, three times
refused; a board at its emergency shell, at a login, already at a shell, or silent, each said.
python3 tests/sim_rkserial.py"""

import json
import os
import subprocess
import sys
import tempfile
import termios
from pathlib import Path

import pwd

REPO = Path(__file__).resolve().parents[1]
T0 = Path(tempfile.mkdtemp(prefix="rkserial-state-"))
(T0 / "control").mkdir()
(T0 / "by-id").mkdir()
os.environ.update(
    HUB_STATE_DIR=str(T0),
    HUB_ETC_DIR=str(T0 / "etc"),
    HUB_SERIAL_BY_ID=str(T0 / "by-id"),
    HUB_USER=pwd.getpwuid(os.getuid()).pw_name,
)
sys.path.insert(0, str(REPO))
from irate_box.hub import rksetup
from irate_box.msg import Refused
from irate_box.root import hub_control, rkserial

rkserial.PACE = 0
T = Path(tempfile.mkdtemp(prefix="rkserial-"))
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def board(start="firstlogin", garble=0):
    """The stand-in console on a pty: (Console on its master side, the process, its record file)."""
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[6][termios.VKILL] = attrs[6][termios.VINTR] = b"\0"  # Ctrl-U and Ctrl-C reach it, as at a board's readline
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    record = T / f"record-{start}-{garble}.json"
    env = dict(os.environ, FAKE_RECORD=str(record), FAKE_START=start, FAKE_GARBLE=str(garble))
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "tests/fixtures/fake_firstlogin.py")],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
    )
    os.close(slave)
    return rkserial.Console(master), proc, record


def refused(fn, *args):
    try:
        fn(*args)
    except Refused as exc:
        return exc.message["id"]
    return None


KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOw7 tom@laptop"
choices = {
    "user": "salty-parrot",
    "real_name": "Salty Parrot",
    "hostname": "lyra-3",
    "timezone": "Europe/London",
    "locale": "en_GB.UTF-8",
    "ssid": "GOODNET",
    "wifi_password": "eight888",
    "country": "GB",
    "keys": [KEY],
    "lock_root": True,
    "listen": 5,
}

con, proc, rec = board()
got = rkserial.setup(con, choices)
proc.kill()
r = json.loads(rec.read_text())
a = r["answers"]
cmds = "\n".join(r["commands"])
check(
    "the first login answered: root's password twice, bash, no WiFi there, the locale skipped",
    a["root1"] == a["root2"] == [got["root_password"]]
    and a["shell"] == ["1"]
    and a["wifi"] == ["n"]
    and a["locale"] == ["2"],
    a,
)
check(
    "  the user, its password twice, its real name typed over the one filled in",
    a["user"] == ["salty-parrot"]
    and a["user1"] == a["user2"] == [got["user_password"]]
    and a["realname"] == ["Salty Parrot"],
    a,
)
check(
    "  passwords generated: letters and digits, 16, which Armbian's setup takes",
    all(
        p.isalnum() and len(p) == 16 and rksetup.problem(p, "password") is None
        for p in (got["root_password"], got["user_password"])
    )
    and got["root_password"] != got["user_password"],
)
check(
    "at the shell: the name set and read back, the time zone, the locale",
    'hostnamectl set-hostname "lyra-3"' in cmds
    and 'timedatectl set-timezone "Europe/London"' in cmds
    and 'update-locale LANG="en_GB.UTF-8"' in cmds,
    cmds,
)
check(
    "  the key for root and the user",
    f'echo "{KEY}" >> /root/.ssh/authorized_keys' in cmds
    and f'echo "{KEY}" >> /home/salty-parrot/.ssh/authorized_keys' in cmds,
)
check("  the setup page stopped", "systemctl disable --now armbian-web-config" in cmds)
check(
    "  the WiFi joined, in its country",
    'iw reg set "GB"' in cmds and 'nmcli dev wifi connect "GOODNET" password "eight888"' in cmds,
)
check("  root's password locked, as there is a key", "passwd -l root" in cmds)
check(
    "  the address read, and what was done said",
    got["address"] == "192.168.1.77" and "joined GOODNET" in got["said"],
    got,
)

con, proc, rec = board(garble=1)
got = rkserial.setup(con, choices)
proc.kill()
check(
    "a root password garbled on the way in: the board asks again, and the second goes through",
    len(json.loads(rec.read_text())["answers"]["root1"]) == 2 and got["address"],
)
con, proc, rec = board(garble=3)
check(
    "  garbled three times: refused, saying to check the wiring",
    refused(rkserial.setup, con, choices) == "rkserial.garbled",
)
proc.kill()

con, proc, rec = board()
check(
    "a network it cannot join: refused, saying so",
    refused(rkserial.setup, con, dict(choices, ssid="NONET")) == "rkserial.wifi",
)
proc.kill()

for start, said in (("initramfs", "rkserial.initramfs"), ("login", "rkserial.login"), ("silent", "rkserial.silent")):
    con, proc, rec = board(start)
    check(
        f"a board {'at its emergency shell' if start == 'initramfs' else 'at a login' if start == 'login' else 'saying nothing'}: refused, said",
        refused(rkserial.setup, con, dict(choices, listen=1)) == said,
    )
    proc.kill()

con, proc, rec = board("shell")
got = rkserial.setup(con, dict(choices, listen=1))
proc.kill()
check(
    "a board already at a root shell: the rest set, no password changed or said",
    got["root_password"] is None and got["user_password"] is None and got["address"] == "192.168.1.77",
    got,
)

# The root helper's action: a page's choices, every value checked again, the console by its by-id name, and the
# passwords made kept for the page to read once (rk-secrets/<id>.json), not in the result.
(T0 / "by-id" / "usb-1a86_USB2.0-Serial-if00-port0").write_text("")
page = {
    "route": "serial",
    "network": "other",
    "otherSsid": "GOODNET",
    "otherPass": "eight888",
    "rootPass": "generated",
    "userName": "salty-parrot",
    "userPass": "generated",
    "lockRoot": False,
    "key": "",
    "hostname": "lyra-4",
    "place": "box",
    "locale": "en_GB.UTF-8",
    "timezone": "Europe/London",
    "country": "GB",
    "address": "dhcp",
}
act = hub_control.ACTIONS["rk-serial-setup"]
check(
    "the action: an adapter not on the box refused",
    refused(act, {"id": "rk6", "console": "usb-nothing", "choices": page}) == "rkserial.no-console",
)
check(
    "  a user name Armbian's setup cannot take refused before anything is sent",
    refused(
        act, {"id": "rk6", "console": "usb-1a86_USB2.0-Serial-if00-port0", "choices": dict(page, userName="bad$name")}
    )
    == "rkserial.value",
)
check(
    "  a fixed address refused, said",
    refused(act, {"id": "rk6", "console": "usb-1a86_USB2.0-Serial-if00-port0", "choices": dict(page, address="fixed")})
    == "rkserial.fixed",
)
con, proc, rec = board()
rkserial.Console.open = classmethod(lambda cls, path, baud=rkserial.BAUD: con)
said = act({"id": "rk7", "console": "usb-1a86_USB2.0-Serial-if00-port0", "choices": dict(page, listen=5)})
proc.kill()
secret = T0 / "control" / "rk-secrets" / "rk7.json"
kept = json.loads(secret.read_text()) if secret.exists() else {}
check(
    "  set up: the result names the board and its address, and holds no password",
    said["id"] == "rkserial.done"
    and said["args"]["name"] == "lyra-4"
    and said["args"]["address"] == "192.168.1.77"
    and "password" not in json.dumps(said).lower().replace("passwords", ""),
    said,
)
check(
    "  the passwords made kept once for the page: the hub's to read (0640), root's and the user's",
    oct(secret.stat().st_mode & 0o777) == "0o640"
    and len(kept.get("root", "")) == 16
    and kept.get("user") == "salty-parrot"
    and len(kept.get("user_password", "")) == 16,
    kept,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
