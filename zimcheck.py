"""Is this file a whole, readable ZIM? One answer for everything that puts a book on the box.

A book only replaces a good one after this says yes: the librarian's downloads and rollbacks
(librarian.py), books from a USB stick (usbstick.py), the installer's library sweep and the
box doctor (health.py, which also sets bad ones aside). Two layers:

  header_problem(path)   cheap, reads 80 bytes: the ZIM signature, a format version Kiwix
                         reads (5 or 6), and whether the file is as long as its header says
                         (the checksum sits at the end, so a short file is a truncated one:
                         an interrupted download or copy, or a stick pulled too soon)
  kiwix_problem(path)    kiwix-manage adds it to a scratch library: what Kiwix itself makes
                         of it. Slower; skipped (None) when kiwix-tools is not installed
  problem(path)          the first of the two that finds something, or None

Each returns None for a good book, or a short reason in plain words. Stdlib only.
"""

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
        return f"cannot be read ({exc.strerror})"
    if head.lstrip()[:1] == b"<":
        return "an HTML page, not a book (a failed download or an error page saved under the book's name)"
    if len(head) < 80:
        return f"too small to be a ZIM ({size} bytes)"
    magic, major, minor = struct.unpack_from("<IHH", head, 0)
    if magic != MAGIC:
        return "not a ZIM file: it does not start with the ZIM signature"
    if major not in (5, 6):
        return f"damaged, or not a real ZIM (its header claims format version {major}.{minor}; books are 5 or 6)"
    checksum_pos = struct.unpack_from("<Q", head, 72)[0]
    if checksum_pos + 16 > size:
        return (f"truncated: {size >> 20} MB of the {(checksum_pos + 16) >> 20} MB its header promises "
                "(an interrupted download or copy)")
    return None


def kiwix_problem(path, timeout=300):
    manage = shutil.which("kiwix-manage")
    if not manage:
        return None
    with tempfile.TemporaryDirectory(prefix="zimcheck-") as d:
        lib = Path(d) / "lib.xml"
        try:
            r = subprocess.run([manage, str(lib), "add", str(path)], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"kiwix-manage could not be run ({exc})"
        if r.returncode == 0 and lib.exists() and "<book " in lib.read_text(errors="replace"):
            return None
        msg = (r.stderr or r.stdout).strip().splitlines()
        return "kiwix-manage cannot read it" + (f": {msg[-1][:160]}" if msg else "")


def problem(path):
    return header_problem(path) or kiwix_problem(path)
