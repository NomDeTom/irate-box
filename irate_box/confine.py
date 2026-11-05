# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A path under a folder, refused if it would leave it.

Every name the hub puts in a path is checked first by its caller (a repository's NAME_RE, a save's
key, a run's id); this is the second line behind those checks, in the form CodeQL's path-injection
query recognises (the joined path normalised, then held to the folder's prefix), so a name that
got past a caller's check still can't reach outside. Stdlib only.

For the hub's own folders. For anything another user can write, links resolved, use validate.path; the
two, and what each does and does not give, are described together in validate.py ("Two path checks"),
where `under` is re-exported."""

import os
from pathlib import Path


def under(base, *parts):
    """base/parts as a Path, or ValueError if, normalised, it is not inside base (base itself is not
    inside it). Links are not followed: the folders are the hub's own."""
    root = os.path.normpath(os.path.abspath(os.fspath(base)))
    full = os.path.normpath(os.path.join(root, *(os.fspath(p) for p in parts)))
    if not full.startswith(root + os.sep):
        raise ValueError("Path outside permitted directory.")
    return Path(full)
