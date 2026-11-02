# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""systemd's readiness and watchdog messages, without a dependency: a datagram to $NOTIFY_SOCKET.

A service started with Type=notify sends READY=1 once it is watching, and WATCHDOG=1 every round
while WatchdogSec is set. A process outside systemd, or a test, has no NOTIFY_SOCKET: every call
then does nothing. Stdlib only.
"""

import os
import socket


def notify(message):
    """Send one line to systemd's notification socket; False when there is none."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return False
    address = "\0" + path[1:] if path.startswith("@") else path
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
    except OSError:
        return False
    return True


def ready():
    """The service is up and watching."""
    return notify("READY=1")


def watchdog():
    """One watchdog round: the loop is alive."""
    return notify("WATCHDOG=1")


def stopping():
    """The service is shutting down cleanly."""
    return notify("STOPPING=1")
