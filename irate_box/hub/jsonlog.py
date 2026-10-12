# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Records kept as JSON lines, appended rather than rewritten, so a record that grows a line at a time
writes a line at a time to the card. Compacted (rewritten whole, only what is kept) when the file has
grown to twice what is kept. Reads the older form too, a JSON array, which the next write turns into
lines. Never through a link: the folder may be the hub's while the writer is root. Stdlib only."""

import json
import os
import stat
from pathlib import Path


def read(path):
    """The records, oldest first; [] when there is none or it cannot be read. A line that does not
    parse (a write cut short by a power cut) is left out."""
    try:
        text = Path(path).read_text()
    except (OSError, ValueError):
        return []
    if text.lstrip().startswith("["):
        try:
            rows = json.loads(text)
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        except ValueError:
            return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _lines(rows):
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def rewrite(path, rows):
    """The whole record, replaced at once: a new file renamed into place. As root (in a folder the hub may
    also write) never through a link; as the hub, in its own folder, a plain rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        from irate_box.root import safeio
        safeio.write(path, _lines(rows))
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_lines(rows))
    os.replace(tmp, path)


def append(path, rows):
    """Lines added at the end. The file must be a plain file of this user's (not a link, not someone
    else's); anything else, or the older JSON form, is replaced by a fresh file of these rows: the caller
    then rewrites what it keeps (returns False)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        fd = None
    except OSError:
        return False
    if fd is not None:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
                return False
            if st.st_size:
                with open(path, "rb") as fh:
                    if fh.read(1) == b"[":
                        return False
            os.write(fd, _lines(rows).encode())
            return True
        finally:
            os.close(fd)
    return False


def count(path):
    """How many lines the file has; the older JSON form counts as needing a rewrite (a very large number)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return 0
    return 1 << 30 if data[:1] == b"[" else data.count(b"\n")
