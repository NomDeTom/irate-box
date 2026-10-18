# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Is this file a whole, readable ZIM? One answer for everything that puts a book on the box.

A book only replaces a good one after this says yes: the librarian's downloads and rollbacks
(librarian.py), books from a USB stick (usbstick.py), the installer's library sweep and the
box doctor (health.py, which also sets bad ones aside). Two layers:

  header_problem(path)   cheap, reads 80 bytes: the ZIM signature, a format version Kiwix
                         reads (5 or 6), and whether the file is as long as its header says
                         (the checksum sits at the end, so a short file is a truncated one:
                         an interrupted download or copy, or a stick pulled too soon)
  kiwix_problem(path)    kiwix-manage adds it to a scratch library: what Kiwix itself makes
                         of it. Slower; skipped (None) when kiwix-tools is not installed.
                         user="hub": run as that user when called as root (F12: libzim and
                         Xapian reading an untrusted book must not be root)
  problem(path[, user])  the first of the two that finds something, or None

Each returns None for a good book, or a short reason in plain words. Stdlib only.
"""

import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

MAGIC = 72173914  # 0x044D495A: "ZIM\x04", little-endian


def header_problem(path):
    path = Path(path)
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(80)
    except OSError as exc:
        return f"The file cannot be read ({exc.strerror})."
    if head.lstrip()[:1] == b"<":
        return "Downloaded file is HTML, not a ZIM book. The download probably failed and saved an error page under the name of the book."
    if len(head) < 80:
        return f"Book file incomplete: {size} bytes are too few for a ZIM header."
    magic, major, minor = struct.unpack_from("<IHH", head, 0)
    if magic != MAGIC:
        return "Invalid ZIM header: the file does not start with the ZIM signature."
    if major not in (5, 6):
        return f"Unsupported ZIM format: the header states version {major}.{minor}. The box reads versions 5 and 6."
    checksum_pos = struct.unpack_from("<Q", head, 72)[0]
    if checksum_pos + 16 > size:
        return (
            f"Book file incomplete: {size >> 20} MB of the {(checksum_pos + 16) >> 20} MB that the header states. "
            "Possible causes: an interrupted download or copy, or a USB drive removed too early."
        )
    return None


def kiwix_problem(path, timeout=300, user=None):
    manage = shutil.which("kiwix-manage")
    if not manage:
        return None
    with tempfile.TemporaryDirectory(prefix="zimcheck-") as d:
        lib = Path(d) / "lib.xml"
        cmd = [manage, str(lib), "add", str(path)]
        if user and os.geteuid() == 0:
            shutil.chown(d, user, user)  # the scratch library is written as that user
            cmd = ["runuser", "-u", user, "--", *cmd]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"Book reader unavailable: kiwix-manage could not run ({exc})."
        if r.returncode == 0 and lib.exists() and "<book " in lib.read_text(errors="replace"):
            return None
        msg = (r.stderr or r.stdout).strip().splitlines()
        return (
            "Book reader unavailable: kiwix-manage cannot read the file" + (f": {msg[-1][:160]}" if msg else "") + "."
        )


def problem(path, user=None):
    return header_problem(path) or kiwix_problem(path, user=user)
