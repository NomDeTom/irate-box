# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The first-boot setup code: the console's proof before anyone on the network may make the first administrator.

The plain code is root's alone (0400), so the console can print it again; the hub gets its SHA-256 (0640,
root:hub) and never holds the code itself. Both files go when the first administrator account exists."""

import hashlib
import hmac
import os
import pwd
import re
import secrets
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
CODE_FILE = ETC / "setup-code"
HASH_FILE = ETC / "setup-code.hash"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _unclaimed():
    path = os.environ.get("HUB_UNCLAIMED_FILE")
    if path:
        return Path(path)
    return Path(f"/etc/{os.environ.get('HUB_WEB_SERVER', 'nginx')}/irate-box-unclaimed")


def normalize(code):
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper()


def format_code(code):
    """ABCD-EFGH-IJKL-MNOP: easier to read out and type; normalize() drops the dashes."""
    code = normalize(code)
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


def _hash(code):
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def verify(code):
    """The hash the hub may read, compared in constant time with the code it was given."""
    try:
        expected = HASH_FILE.read_text().strip()
    except OSError:
        return False
    return hmac.compare_digest(_hash(code), expected)


def _secure():
    """The modes the hub's read relies on, and the hash's group."""
    try:
        os.chmod(CODE_FILE, 0o400)
        os.chmod(HASH_FILE, 0o640)
    except OSError:
        return
    if os.geteuid() == 0:
        try:
            os.chown(HASH_FILE, 0, pwd.getpwnam(os.environ.get("HUB_USER", "hub")).pw_gid)
        except KeyError:
            pass


def clear():
    CODE_FILE.unlink(missing_ok=True)
    HASH_FILE.unlink(missing_ok=True)


def ensure(unclaimed=None):
    """The code while the box has no administrator account; None once it has one. An existing code is kept, so the
    installer and an update show the same one."""
    if not (Path(unclaimed) if unclaimed else _unclaimed()).exists():
        clear()
        return None
    try:
        code = CODE_FILE.read_text().strip()
        if len(code) == 16:
            if not HASH_FILE.exists() or HASH_FILE.read_text().strip() != _hash(code):
                fd = os.open(HASH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
                with os.fdopen(fd, "w") as fh:
                    fh.write(_hash(code) + "\n")
            _secure()
            return code
    except OSError:
        pass
    code = "".join(secrets.choice(ALPHABET) for _ in range(16))
    ETC.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd = os.open(CODE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "w") as fh:
        fh.write(code + "\n")
    fd = os.open(HASH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, "w") as fh:
        fh.write(_hash(code) + "\n")
    _secure()
    return code


def show(unclaimed=None):
    """The plain code for the console, made first if the box is still unclaimed."""
    return format_code(ensure(unclaimed) or "")
