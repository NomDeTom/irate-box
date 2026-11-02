# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The finish of a new board and irate-box installed on it (root/rkfinish.py), offline: the box's own key made
once and kept root's; the setup page stopped, the name set, the keys put on the board and the OS read back over
one SSH command; a bad name and a failed command refused; the install options this box recorded, without its
password or port, and the install's output written to the page's log as it comes.
python3 tests/sim_rkfinish.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rkfinish-"))
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ROOT_STATE_DIR=str(T / "root-state"),
    HUB_ETC_DIR=str(T / "etc"),
)
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import rkfinish

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Refused as exc:
        return exc.message["id"]
    return None


rkfinish.KEY = T / "root-state" / "board-key"
rkfinish.KEY.parent.mkdir(parents=True)
key = rkfinish.box_key()
check(
    "the box's key made once, kept root's alone, and the same key read again",
    key.startswith("ssh-ed25519 AAAA")
    and rkfinish.box_key() == key
    and oct(rkfinish.KEY.stat().st_mode & 0o777) == "0o600",
    key,
)

asked = []


def ssh(address, command, timeout=120, key=None):
    asked.append((address, command))
    return subprocess.CompletedProcess(
        ["ssh"], 0, 'NAME="Armbian"\nVERSION="26.11.0"\nBOARD=bananapim2zero\nLINUXFAMILY=sunxi\n', ""
    )


rkfinish.ssh = ssh
choices = {
    "hostname": "bpi-1",
    "user": "salty-parrot",
    "keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOw7 tom@laptop"],
    "lock_root": True,
}
got = rkfinish.finish("192.168.1.60", choices)
text = asked[-1][1]
check(
    "the finish: setup page stopped, name set, root's and the user's key, root locked, OS read back",
    "systemctl disable --now armbian-web-config" in text
    and "hostnamectl set-hostname bpi-1" in text
    and text.count("/root/.ssh/authorized_keys") >= 1
    and text.count("/home/salty-parrot/.ssh/authorized_keys") >= 1
    and "passwd -l root" in text
    and "cat /etc/os-release" in text
    and got["name"] == "bpi-1"
    and got["os"] == "Armbian 26.11.0",
    (text, got),
)
check("  the box's own key put on the board beside the administrator's", key.split()[1] in text)
check(
    "a name Armbian cannot take refused before SSH runs",
    refused(rkfinish.finish, "192.168.1.60", {**choices, "hostname": "Bad Name"}) == "rkfinish.value",
)
rkfinish.ssh = lambda *a, **k: subprocess.CompletedProcess(["ssh"], 1, "", "Permission denied")
check(
    "  an SSH command that fails refused, saying what the board said",
    refused(rkfinish.finish, "192.168.1.60", choices) == "rkfinish.failed",
)
asked.clear()
secret_seen = {}


def password_run(*cmd, timeout=120, env=None):
    helper = Path(env["SSH_ASKPASS"])
    secret_seen.update(
        script=helper.read_text(), mode=oct(helper.stat().st_mode & 0o777), cmd=cmd, gone=helper.exists()
    )
    return subprocess.CompletedProcess(cmd, 0, "done", "")


rkfinish.run = password_run
rkfinish.ssh_password("192.168.1.60", "hostnamectl set-hostname bpi-1", "Root16charsAbcd1")
check(
    "a first visit with a password: an askpass helper root's alone, the password not on ssh's command line",
    secret_seen["script"] == "#!/bin/sh\nprintf '%s\\n' Root16charsAbcd1\n"
    and secret_seen["mode"] == "0o700"
    and "Root16charsAbcd1" not in " ".join(secret_seen["cmd"])
    and secret_seen["gone"] is True,
    # Not the script itself: it holds the password, and a failure's output is logged.
    {"mode": secret_seen.get("mode"), "gone": secret_seen.get("gone")},
)
rkfinish.ssh = ssh

(T / "etc" / "install-options").write_text(
    "--repo\nhttps://github.com/NomDeTom/irate-box\n--branch\nmain\n--admin-password\nsecret\n--port\n8080\n--no-https\n"
)
opts = rkfinish.install_options()
check(
    "this box's install options: the repository, branch and flags, never its password or port",
    opts == ["--repo", "https://github.com/NomDeTom/irate-box", "--branch", "main", "--no-https"],
    opts,
)


class FakeProc:
    def __init__(self, *a, **kw):
        self.stdout = iter(["Building…\n", "Installed.\n"])
        self.returncode = 0

    def wait(self, timeout=3600):
        return self.returncode


rkfinish.subprocess.Popen = FakeProc
rkfinish.LOG = T / "control" / "board-install.log"
rkfinish.CONTROL = rkfinish.LOG.parent
said = rkfinish.install("192.168.1.60", ["--no-https"])
check(
    "the install run over SSH with the options, its output written as it comes",
    said["id"] == "rkfinish.installed"
    and rkfinish.LOG.read_text().endswith("Installed.\n")
    and "Installing irate-box on 192.168.1.60" in rkfinish.LOG.read_text(),
    said,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
