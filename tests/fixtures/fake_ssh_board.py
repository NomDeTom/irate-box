# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A stand-in for a stock Armbian board reached over SSH at its first boot, for tests/sim_rkssh.py: it asks for
Armbian's default password, refuses any other, and then is the first login on its console
(tests/fixtures/fake_firstlogin.py). The ssh program runs this file with the address and options after it; they are
not used."""

import os
import sys

OUT = sys.stdout
OUT.write("root@192.168.1.60's password: ")
OUT.flush()
if sys.stdin.readline().strip() != "1234":
    OUT.write("\nPermission denied (password).\n")
    OUT.flush()
    sys.exit(1)
fake = os.path.join(os.path.dirname(__file__), "fake_firstlogin.py")
os.execv(sys.executable, [sys.executable, fake])
