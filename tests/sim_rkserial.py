# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A new board set up over its serial console (root/rkserial.py), offline, against a stand-in console on a pty
(tests/fixtures/fake_firstlogin.py: armbian-firstlogin's questions in its words, then a root shell that answers
without running anything): every first-login question answered, the locale skipped, the user's real name cleared
before it is typed; then the name, time zone, keys, setup page, WiFi and root's lock set at the shell, and the
address read; passwords generated of letters and digits; a password garbled on the way in asked again, three times
refused; Armbian 26.11's "Press Enter" answered with one Enter; a board at its emergency shell, at a login,
already at a shell, or silent, each said.
python3 tests/sim_rkserial.py"""

import json
import os
import pwd
import subprocess
import sys
import tempfile
import termios
from pathlib import Path

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


def board(start="firstlogin", garble=0, locales=1, nm=True, nm_install=True):
    """The stand-in console on a pty: (Console on its master side, the process, its record file)."""
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[6][termios.VKILL] = attrs[6][termios.VINTR] = b"\0"  # Ctrl-U and Ctrl-C reach it, as at a board's readline
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    record = T / f"record-{start}-{garble}.json"
    env = dict(
        os.environ,
        FAKE_RECORD=str(record),
        FAKE_START=start,
        FAKE_GARBLE=str(garble),
        FAKE_LOCALES=str(locales),
        FAKE_NM="1" if nm else "0",
        FAKE_NM_INSTALL="1" if nm_install else "0",
    )
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
    'the console 500 columns wide, then history expansion off, before any other command (a "!" in a WiFi password)',
    r["commands"][:1] == ["stty cols 500"] and "set +H" in r["commands"][1],
    r["commands"][:2],
)
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

# Armbian 26.11 (a Lyra Zero W, 2026-10-29): "Press Enter to start the first login setup" before any question, and
# Ctrl-C does not end its wait. One Enter there, then the questions answered as before.
con, proc, rec = board("enter")
got = rkserial.setup(con, choices)
proc.kill()
a = json.loads(rec.read_text())["answers"]
check(
    'Armbian 26.11\'s "Press Enter to start the first login setup": one Enter, then the first login answered',
    a.get("enter") == [""] and a["root1"] == [got["root_password"]] and a["wifi"] == ["n"],
    a,
)

# Every locale listed (no country found), with the Skip line lost on the way back: the numbers tried downwards from
# above the highest seen among its neighbours, until the board takes one, which is Skip; no real locale chosen.
con, proc, rec = board(locales=340)
got = rkserial.setup(con, choices)
proc.kill()
a = json.loads(rec.read_text())["answers"]
tried = [int(x) for x in a["locale"]]
check(
    "a locale menu with its Skip line lost: numbers tried downwards, the first taken is Skip (341), none below it",
    tried[-1] == 341
    and all(x > 341 for x in tried[:-1])
    and tried[0] <= 340 + 30 + 1
    and a["user"] == ["salty-parrot"],
    tried[:3] + ["..."] + tried[-3:],
)


def written_wifi(ssid, password="S!x&y72CKN3LKP"):
    """The commands _netplan_wifi writes: both files, after a stand-in board's answers."""
    asked = []

    def do(command, key, timeout=60):
        asked.append((key, command))
        return {"wifidev": "wlan0\n", "nminstall": "nm-installed\n", "wifi": "netplan applied\n"}[key]

    _out, _note = rkserial._netplan_wifi(do, {"ssid": ssid, "wifi_password": password})
    return [command for key, command in asked if key == "wifi"]


def netplan_yaml(command):
    """Run the printf of a recorded netplan command in bash (its path swapped to a temp file) and read it back."""
    printf = command.split(" > /etc/netplan/30-wifis-dhcp.yaml", 1)[0]
    target = T / "30-wifis-dhcp.yaml"
    subprocess.run(["bash", "-c", printf + f" > {target}"], check=True)
    return target.read_text()


def access_point(text):
    """The one SSID under access-points, read as a YAML double-quoted scalar (JSON's rules are the same here)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "access-points:")
    line = lines[start + 1].strip()
    assert line.endswith(":"), line
    scalar = line[:-1]
    return json.loads(scalar) if scalar.startswith('"') else scalar


# Armbian's minimal image has no NetworkManager (a Lyra Zero W, 2026-10-29): join with systemd-networkd first
# (the install needs that network), then install NetworkManager, hand it the WiFi and apply.
con, proc, rec = board("shell", nm=False)
got = rkserial.setup(con, dict(choices, listen=1, wifi_password="S!x&y72CKN3LKP"))
proc.kill()
cmds = json.loads(rec.read_text())["commands"]
wifi = [c for c in cmds if "30-wifis-dhcp.yaml" in c]
check(
    "no NetworkManager: join over systemd-networkd, install it, then write renderer NetworkManager and apply",
    len(wifi) == 2
    and "renderer:" not in wifi[0]
    and "renderer: NetworkManager" in wifi[1]
    and any("apt-get install" in c and "network-manager" in c and "policy-rc.d" in c for c in cmds)
    and any("mktemp -d" in c and "/tmp/irate-box-policy-rc.d" not in c for c in cmds)
    and "umask 077" in wifi[1]
    and '"wlan0" "GOODNET" "S!x&y72CKN3LKP"' in wifi[1]
    and "password: |-" in wifi[1]
    and "chmod 600" in wifi[1]
    and "systemctl enable --now NetworkManager" in wifi[1]
    and "netplan apply" in wifi[1]
    and not any("nmcli dev wifi connect" in c for c in cmds)
    and "joined GOODNET" in got["said"],
    (cmds, wifi, got),
)
pairs = [(ssid, command) for ssid in ("yes", "a: b") for command in written_wifi(ssid)]
check(
    "  the SSID is a quoted YAML scalar, so yes, true, 123 or a colon is a name and not a number or mapping",
    len(pairs) == 4 and all(access_point(netplan_yaml(c)) == ssid for ssid, c in pairs),
    [(ssid, netplan_yaml(c)) for ssid, c in pairs],
)

con, proc, rec = board("shell", nm=False, nm_install=False)
got = rkserial.setup(con, dict(choices, listen=1))
proc.kill()
cmds = json.loads(rec.read_text())["commands"]
wifi = [c for c in cmds if "30-wifis-dhcp.yaml" in c]
check(
    "no NetworkManager and no way to install it: the networkd file stays, the board still joins, and it is said",
    wifi
    and all("renderer:" not in c for c in wifi)
    and not any("systemctl enable --now NetworkManager" in c for c in cmds)
    and "joined GOODNET" in " ".join(got["said"])
    and any("systemd-networkd" in said for said in got["said"]),
    (cmds, got),
)


def install_output(existing=None, apt_ok=True):
    """Run the install command against a temp /usr/sbin/policy-rc.d and stub apt-get/nmcli."""
    asked = []

    def do(command, key, timeout=60):
        asked.append((key, command))
        return {"wifidev": "wlan0\n", "nminstall": "nm-installed\n", "wifi": "netplan applied\n"}[key]

    rkserial._netplan_wifi(do, {"ssid": "GOODNET", "wifi_password": "eight888"})
    command = next(c for key, c in asked if key == "nminstall")
    box = Path(tempfile.mkdtemp(prefix="rkserial-policy-"))
    policy = box / "policy-rc.d"
    if existing is not None:
        policy.write_bytes(existing)
    stub = box / "bin"
    stub.mkdir()
    apt = stub / "apt-get"
    apt.write_text(
        "#!/bin/sh\n" + ("exit 0\n" if apt_ok else "echo 'E: Unable to fetch some archives' >&2\nexit 100\n")
    )
    apt.chmod(0o755)
    nmcli = stub / "nmcli"
    nmcli.write_text("#!/bin/sh\nexit 0\n")
    nmcli.chmod(0o755)
    env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")
    done = subprocess.run(
        ["bash", "-c", command.replace("/usr/sbin/policy-rc.d", str(policy))],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )
    return done.stdout, policy.exists(), policy.read_bytes() if policy.exists() else None


old = b"#!/bin/sh\nexit 0\n"
out, there, data = install_output(existing=old)
check(
    "policy-rc.d that was there is put back byte for byte, and the install says it worked",
    there and data == old and "nm-installed" in out,
    (out, there, data),
)
out, there, data = install_output(apt_ok=False)
check(
    "a failed install keeps its last lines and leaves no policy-rc.d",
    not there and "E: Unable to fetch some archives" in out and "systemd-networkd" in out,
    (out, there, data),
)

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
