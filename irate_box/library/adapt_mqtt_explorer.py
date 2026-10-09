#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapt a clone of the hub's own repository into the MQTT explorer add-on (addons/mqtt-explorer.json).
Run by the librarian on what it fetched, at the commit the manifest pins.

    adapt_mqtt_explorer.py TREE [HUB_STATIC_DIR]

The explorer is the hub's own page (extras/mqtt-explorer/), fetched like any web add-on so that it
is served on the add-on origin under its own Content-Security-Policy: the broker at ws://<box>/mqtt
and nothing else. The tree keeps only that folder, at its top, and the MIT licence it is under.
Taken from the clone, not from the hub's checkout, so what is installed is what the pin says.
Running it twice changes nothing. Stdlib only.
"""

import shutil
import sys
from pathlib import Path

PAGE = Path("extras") / "mqtt-explorer"
FILES = ("index.html", "explorer.js", "explorer.css")
LICENCE = Path("LICENSES") / "MIT.txt"


class AdaptError(Exception):
    pass


def adapt(tree):
    tree = Path(tree)
    if all((tree / f).is_file() for f in FILES) and not (tree / "extras").exists():
        return  # already adapted
    src = tree / PAGE
    missing = [f for f in FILES if not (src / f).is_file()]
    if missing:
        raise AdaptError(f"{PAGE} has no {', '.join(missing)}: not a commit with the explorer")
    keep = tree.parent / f".{tree.name}.keep"
    shutil.rmtree(keep, ignore_errors=True)
    keep.mkdir()
    for f in FILES:
        shutil.copyfile(src / f, keep / f)
    if (tree / LICENCE).is_file():
        shutil.copyfile(tree / LICENCE, keep / "LICENSE")
    for child in tree.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for f in keep.iterdir():
        shutil.move(str(f), tree / f.name)
    keep.rmdir()


def main(argv):
    if not 1 <= len(argv) <= 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        adapt(argv[0])
    except (AdaptError, OSError) as exc:
        print(f"adapt_mqtt_explorer: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
