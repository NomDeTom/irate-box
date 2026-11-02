# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Stock Armbian's default password over a cable, root's half (route R3): a board on the box's own hotspot or on a
cable comes up with root and 1234. The box finds it from its own leases (and what the kernel saw on the wire),
signs in once, answers the same first-boot questions as the serial route (rkserial.first_login, over an SSH pty
instead of a serial line), then sets the name, keys, WiFi and the rest at the shell.

The window is the network: anyone on it can use the same default. So the route is offered only where the box
controls the link (its own hotspot, or a cable), and the address must be one root found itself. Stdlib only.
"""

import os
import pty
import re
import subprocess

from irate_box.hub import boardfind
from irate_box.msg import M, Refused
from irate_box.root import rkserial

DEFAULT_PASSWORD = "1234"  # nosec B105 - Armbian's public default
SSH = ["ssh"]


def run(*cmd, timeout=30):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def candidates():
    """The boards the box can see (hub/boardfind.py)."""
    return boardfind.candidates()


def find(address=None):
    """The board at address, which must be one root found itself, or the first board-like one."""
    found = boardfind.candidates()
    if address:
        match = next((c for c in found if c["address"] == address), None)
        if not match:
            raise Refused(
                M(
                    "rkssh.not-found",
                    "No board at {address} is on this box's own network now.",
                    address=address,
                )
            )
        return match
    if not found:
        raise Refused(M("rkssh.none", "The box sees no board on its own network."))
    return found[0]


def controlled(address):
    """Whether the board's address is reached over a link the box controls: its own hotspot, or a cable."""
    got = run("ip", "route", "get", address, timeout=15)
    fields = (got.stdout or "").split()
    device = fields[fields.index("dev") + 1] if "dev" in fields else ""
    if not device:
        return False
    return not (device.startswith(("wl", "wlan")))


def _spawn(address, key=None):
    """An SSH session whose terminal is a pty, so the board's first boot asks its questions there."""
    master, slave = pty.openpty()
    cmd = [*SSH, "-tt", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
    if key:
        cmd += ["-i", str(key), "-o", "BatchMode=yes"]
    else:
        cmd += ["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no"]
    cmd.append(f"root@{address}")
    proc = subprocess.Popen(  # noqa: TID251
        cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True
    )
    os.close(slave)
    return rkserial.Console(master), proc


def setup(address, choices, listen=60):
    """Sign in at the board's address with Armbian's default password, answer its first-boot questions, and set the
    rest at its shell. choices as rkserial.setup."""
    board = find(address)
    if not controlled(board["address"]):
        raise Refused(
            M(
                "rkssh.not-controlled",
                "That board is on a network this box does not control. Use its own hotspot, or a cable.",
            )
        )
    console, proc = _spawn(board["address"])
    try:
        name, _ = console.expect({"password": re.compile(r"[Pp]assword:\s*$")}, listen)
        if name != "password":
            raise Refused(
                M(
                    "rkssh.no-prompt",
                    "The board at {address} did not ask for its default password.",
                    address=board["address"],
                )
            )
        console.send(DEFAULT_PASSWORD + "\r", pace=0)
        state = rkserial.where(console, listen=listen)
        if state == "login":
            raise Refused(
                M(
                    "rkssh.default-refused",
                    "The board did not take Armbian's default password: it can be set up already.",
                )
            )
        if state is None:
            raise Refused(
                M("rkssh.silent", "The board at {address} did not answer after signing in.", address=board["address"])
            )
        if state == "firstlogin":
            root_password = choices.get("root_password") or rkserial.generated()
            user_password = choices.get("user_password") or rkserial.generated()
            rkserial.first_login(
                console,
                root_password,
                choices["user"],
                user_password,
                choices.get("real_name") or choices["user"],
            )
        elif state == "shell":
            root_password = user_password = None
            console.send("armbian-firstlogin\r")
            again = rkserial.where(console, listen=20)
            if again == "firstlogin":
                root_password = choices.get("root_password") or rkserial.generated()
                user_password = choices.get("user_password") or rkserial.generated()
                rkserial.first_login(
                    console,
                    root_password,
                    choices["user"],
                    user_password,
                    choices.get("real_name") or choices["user"],
                )
        else:
            raise Refused(M("rkssh.emergency", "The board stopped at its emergency shell: its card is not readable."))
        done = rkserial.configure(console, choices)
        return {
            **done,
            "root_password": root_password,
            "user_password": user_password,
            "address": done.get("address") or board["address"],
        }
    finally:
        try:
            console.send("exit\r", pace=0)
        except OSError:
            pass
        try:
            console.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
