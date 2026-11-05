# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The finish of a new board over SSH, root's half: the box's own key put on the board, its first-boot setting
page stopped, its name set and its OS read back. A board set up over its serial console, from its card, or through
its setup hotspot is on the administrator's network by then. The box signs in with its own key (root/rkfinish.py
makes one, kept in root's state, and the setup writes its public half onto the board) and asks nothing else.

The install of irate-box on the new board is run over the same SSH link, with the options this box was installed
with (except the administrator password, which is this box's). Its output is written to control/board-install.log
as it comes, for the page to show, and the new box comes up unclaimed: the page says to claim it at once.

Every value has passed hub/rksetup.problem. The address and the name are checked here again. Stdlib only.
"""

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import safeio

KEY = safeio.ROOT_STATE / "board-key"
ADDRESS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
CONTROL = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control"
LOG = CONTROL / "board-install.log"
SSH_OPTS = (
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ServerAliveInterval=15",
)


def run(*cmd, timeout=120, env=None):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def box_key():
    """This box's public key for a new board, made once and kept in root's state. Its private half never leaves
    the box."""
    if not KEY.is_file() or KEY.is_symlink():
        KEY.parent.mkdir(parents=True, exist_ok=True)
        made = run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(KEY), timeout=120)
        if made.returncode != 0:
            raise Refused(
                M(
                    "rkfinish.no-key",
                    "The box could not make the key for a new board: {detail}",
                    detail=made.stderr[-160:],
                )
            )
        KEY.chmod(0o600)
    got = run("ssh-keygen", "-y", "-f", str(KEY), timeout=60)
    if got.returncode != 0 or not got.stdout.strip():
        raise Refused(M("rkfinish.key-unread", "The box could not read the key for a new board."))
    return got.stdout.strip()


def ssh(address, command, timeout=120, key=None):
    """One command at the board as root, with the box's key. Nothing of the command is built from an unchecked
    value: the caller quotes what it puts in."""
    if not ADDRESS_RE.fullmatch(address or ""):
        raise Refused(M("rkfinish.address", "The board's address is not valid."))
    if not Path(key or KEY).is_file():
        box_key()
    cmd = ["ssh", "-i", str(key or KEY), "-o", "BatchMode=yes", *SSH_OPTS, f"root@{address}", command]
    return run(*cmd, timeout=timeout)


def ssh_password(address, command, password, timeout=180):
    """One command at the board as root with a password, for the first visit before the box's key is on it. The
    password goes to ssh through an askpass helper in root's own temporary folder, never on its command line."""
    if not ADDRESS_RE.fullmatch(address or ""):
        raise Refused(M("rkfinish.address", "The board's address is not valid."))
    KEY.parent.mkdir(parents=True, exist_ok=True)
    fd, helper = tempfile.mkstemp(prefix="board-askpass-", dir=str(KEY.parent))
    try:
        os.fchmod(fd, 0o700)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(password)}\n")
        env = dict(os.environ, SSH_ASKPASS=helper, SSH_ASKPASS_REQUIRE="force", DISPLAY=":0")
        return run(
            "ssh",
            "-o",
            "BatchMode=no",
            *SSH_OPTS,
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "PreferredAuthentications=password",
            "-o",
            "NumberOfPasswordPrompts=1",
            f"root@{address}",
            command,
            timeout=timeout,
            env=env,
        )
    finally:
        try:
            os.unlink(helper)
        except OSError:
            pass


def finish(address, choices, key=None, password=None):
    """The setup page stopped, the name set, the box's key and the administrator's keys put on the board, and the
    OS and release read back. With a password, the first visit uses it and puts the key on for the rest."""
    from irate_box.hub import rksetup

    name = str(choices.get("hostname") or "")
    if (why := rksetup.problem(name, "hostname")) is not None:
        raise Refused(M("rkfinish.value", "The board's name: {why}", why=why))
    user = str(choices.get("user") or "armbian")
    keys = [k.strip() for k in (choices.get("keys") or []) if k.strip()]
    try:
        keys.append(box_key())
    except Refused:
        pass
    asked = [
        "systemctl disable --now armbian-web-config 2>/dev/null || true",
        f"hostnamectl set-hostname {shlex.quote(name)}",
        f"sed -i 's/^127\\\\.0\\\\.1\\\\.1.*/127.0.1.1\\\\t{name}/' /etc/hosts 2>/dev/null || true",
    ]
    for who, home in (("root", "/root"), (user, f"/home/{user}")):
        for one in keys:
            asked.append(
                f"mkdir -p {home}/.ssh && grep -qxF {shlex.quote(one)} {home}/.ssh/authorized_keys 2>/dev/null "
                f"|| echo {shlex.quote(one)} >> {home}/.ssh/authorized_keys"
            )
        asked.append(f"chown -R {who}: {home}/.ssh 2>/dev/null || true")
        asked.append(f"chmod 700 {home}/.ssh 2>/dev/null || true")
        asked.append(f"chmod 600 {home}/.ssh/authorized_keys 2>/dev/null || true")
    if choices.get("lock_root") and keys:
        asked.append("passwd -l root")
    asked.append("cat /etc/os-release 2>/dev/null; cat /etc/armbian-release 2>/dev/null")
    command = " && ".join(asked[:-1]) + " ; " + asked[-1]
    got = (
        ssh_password(address, command, password, timeout=180)
        if password
        else ssh(address, command, timeout=180, key=key)
    )
    if got.returncode != 0:
        raise Refused(
            M(
                "rkfinish.failed",
                "The board did not take the finish at {address}: {detail}",
                address=address,
                detail=(got.stderr or got.stdout).strip()[-200:],
            )
        )
    fields = {}
    for line in got.stdout.splitlines():
        key_, _, value = line.partition("=")
        if key_.strip() and value.strip():
            fields[key_.strip()] = value.strip().strip('"')
    release = fields.get("VERSION") or fields.get("PRETTY_NAME") or fields.get("VERSION_ID") or ""
    os_name = fields.get("NAME") or "Armbian"
    return {
        "name": name,
        "address": address,
        "os": f"{os_name} {release}".strip(),
        "said": ["setup page stopped", f"name {name}", f"OS {os_name} {release}".strip()],
    }


def install_options():
    """This box's install options, without the administrator password or a port this box took for itself."""
    try:
        opts = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"), "install-options").read_text().split()
    except OSError:
        opts = []
    keep, i = [], 0
    while i < len(opts):
        one = opts[i]
        name, eq, value = one.partition("=")
        if name in ("--admin-password", "--port"):
            i += 1 if eq else 2
            continue
        if name in ("--repo", "--branch"):
            if eq:
                keep.append(one)
                i += 1
            elif i + 1 < len(opts):
                keep.extend([name, opts[i + 1]])
                i += 2
            else:
                i += 1
            continue
        if one in ("--no-https", "--allow-admin-http", "--with-meshtastic", "--with-books", "--with-kits"):
            keep.append(one)
        i += 1
    return keep


def install(address, options=None, key=None):
    """irate-box installed on the new board over SSH, its output written to control/board-install.log as it comes.
    The new box is unclaimed: the administrator claims it at once, with its address and its setup code."""
    options = install_options() if options is None else options
    repo, branch = "https://github.com/NomDeTom/irate-box", "main"
    rest = []
    for one in options:
        if one.startswith("--repo="):
            repo = one.split("=", 1)[1]
        elif one.startswith("--branch="):
            branch = one.split("=", 1)[1]
        else:
            rest.append(one)
    if not re.fullmatch(r"^https://[A-Za-z0-9./_-]+$", repo):
        raise Refused(M("rkfinish.repo", "This box's recorded repository is not an https address the box can use."))
    for one in (repo, branch, *rest):
        if any(ch in one for ch in "\"'`$\\\n\r"):
            raise Refused(M("rkfinish.option", "An install option has a character that cannot be sent to the board."))
    command = (
        "set -eu; rm -rf /tmp/irate-box-install; "
        f"git clone --depth 1 {repo} /tmp/irate-box-install; "
        f"cd /tmp/irate-box-install && git fetch --depth 1 origin {shlex.quote(branch)} "
        f"&& git checkout FETCH_HEAD; ./install.sh {' '.join(rest)}"
    )
    CONTROL.mkdir(parents=True, exist_ok=True)
    with safeio.open_new(LOG, mode=0o644) as log:
        log.write(f"Installing irate-box on {address}\n")
        log.flush()
        if not Path(key or KEY).is_file():
            box_key()
        proc = subprocess.Popen(  # noqa: TID251
            ["ssh", "-i", str(key or KEY), "-o", "BatchMode=yes", *SSH_OPTS, f"root@{address}", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout or ():
            log.write(line)
            log.flush()
        code = proc.wait(timeout=3600)
    if code != 0:
        raise Refused(
            M("rkfinish.install-failed", "The install did not finish on the board. Its last lines are on the page.")
        )
    return M(
        "rkfinish.installed",
        "irate-box is installed on the board at {address}. It is unclaimed: claim it at once.",
        address=address,
    )
