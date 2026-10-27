# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A port for a test's hub: below the range the system hands out for outgoing connections (32768 and up on Linux,
GitHub's runners included), and free when asked. A port picked at random without the check now and then belonged
to a connection already, the hub could not listen, and the test failed with ConnectionRefusedError after its wait."""

import os
import socket

LOW, HIGH = 20000, 32768


def free_port():
    for _ in range(200):
        port = LOW + int.from_bytes(os.urandom(2), "big") % (HIGH - LOW)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port between 20000 and 32767")
