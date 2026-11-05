# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A book's download never renames across a mount: the download is written in the book's own home
(the card's zim/ or the library drive), and every later rename stays there. os.replace is made to
raise EXDEV whenever its parents differ, as a real rename across filesystems does.
python3 tests/sim_cross_mount.py"""

import os
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="cross-mount-"))
(T / "state" / "library").mkdir(parents=True)
(T / "card" / "zim").mkdir(parents=True)
(T / "drive" / "zim").mkdir(parents=True)
os.environ["HUB_STATE_DIR"] = str(T / "state")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
from zimgen import write_zim

from irate_box.library import librarian

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


real_replace = os.replace


def strict_replace(src, dst):
    if Path(src).parent.resolve() != Path(dst).parent.resolve():
        raise OSError(18, "Invalid cross-device link", str(src), str(dst))
    return real_replace(src, dst)


def fake_download(url, dest, *args, **kwargs):
    write_zim(Path(dest), name="book", title="A book")
    return Path(dest).stat().st_size


librarian._download = fake_download
librarian.os.replace = strict_replace
policy = {"min_free_mb": 0, "books_budget_mb": 0, "keep_old": 1}
for where, home in (("the card's zim/", T / "card" / "zim"), ("the library drive", T / "drive" / "zim")):
    librarian.book_home = lambda name=None, home=home: home
    name = "cross-" + home.parent.name
    status = {}
    try:
        librarian.fetch_book(
            {"name": name},
            {
                "size": 4096,
                "zip": False,
                "url": "https://example.org/book.zim",
                "auth": None,
                "version": "v1",
                "label": "v1",
            },
            policy,
            status,
        )
        staged = librarian.staged_book(name)
        check(f"a download into {where} stays on that filesystem", staged.is_file() and staged.parent == home)
        check(
            "  and its temporary file was in the same home, not library/tmp",
            not (librarian.TMP_DIR / f"{name}.download").exists(),
        )
    except OSError as exc:
        check(f"a download into {where} stays on that filesystem", False, exc)
# A podcast episode on the library drive: its .part is beside its file, so the rename stays there.
os.environ["HUB_PODCAST_ROOT"] = str(T / "drive" / "podcasts")
from irate_box.library import podcast

podcast._fetch = lambda url, limit, accept="": (
    (REPO / "tests" / "fixtures" / "podcasts" / "rss.xml").read_bytes(),
    url,
    "application/rss+xml",
)
podcast._download = lambda url, path, cfg, log, feed_url=None: (Path(path).write_bytes(b"x" * 1024), 1024)[1]
podcast.set_settings(
    enabled=True, feeds=[{"id": "one", "url": "https://example.org/feed.xml", "title": "One"}], back_catalogue=1
)
podcast.sync()
files = list((T / "drive" / "podcasts" / "one").glob("*.mp3"))
check(
    "a podcast episode on the drive is renamed within its own folder",
    bool(files) and all(f.suffix != ".part" for f in files),
    files,
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
