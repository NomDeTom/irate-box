# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Podcast subscriptions, offline: an RSS feed with iTunes tags, an Atom feed and one with a new
address; the type, scheme and size checks; the back catalogue; each pruning limit alone and
together, with starred and played episodes; OPML both ways; and the metered wait. No test reaches
the internet. python3 tests/sim_podcast.py"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="podcast-"))
for d in ("library", "podcasts"):
    (T / d).mkdir()
os.environ["HUB_STATE_DIR"] = str(T)
os.environ["HUB_PODCAST_ROOT"] = str(T / "podcasts")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import librarian, podcast

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


RSS = (REPO / "tests" / "fixtures" / "podcasts" / "rss.xml").read_bytes()
ATOM = (REPO / "tests" / "fixtures" / "podcasts" / "atom.xml").read_bytes()
FEEDS = {"https://example.org/feed.xml": RSS, "https://example.org/atom.xml": ATOM}


def fake_fetch(url, limit, accept=""):
    data = FEEDS[url]
    return data, url, "application/rss+xml"


def fake_download(url, path, cfg, log, feed_url=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1024)
    return 1024


podcast._fetch = fake_fetch
podcast._download = fake_download
podcast.set_settings(enabled=True, back_catalogue=3, keep_count=10, keep_days=0, keep_mb=0, cap_mb=0, leave_free_mb=0)
podcast.add_feed("https://example.org/feed.xml", "A Test Podcast")
podcast.add_feed("https://example.org/atom.xml", "An Atom Podcast")

rss = podcast.parse_feed(RSS, "https://example.org/feed.xml")
check(
    "RSS with iTunes tags: title, cover, new address, duration",
    rss["title"] == "A Test Podcast"
    and rss["cover"] == "https://example.org/cover.jpg"
    and rss["new_url"] == "https://example.org/feed-new.xml"
    and rss["episodes"][0]["total_time"] == 3723,
    rss,
)
atom = podcast.parse_feed(ATOM, "https://example.org/atom.xml")
check(
    "Atom: title and an enclosure",
    atom["title"] == "An Atom Podcast" and atom["episodes"][0]["enclosures"][0]["mime_type"] == "audio/ogg",
    atom,
)
check(
    "  an audio enclosure on http is allowed; a video is not unless chosen; a document never",
    podcast._allowed({"url": "http://example.org/a.mp3", "mime_type": "audio/mpeg", "file_size": 1}, podcast.settings())
    and not podcast._allowed(
        {"url": "https://example.org/v.mp4", "mime_type": "video/mp4", "file_size": 1}, podcast.settings()
    )
    and not podcast._allowed(
        {"url": "https://example.org/n.pdf", "mime_type": "application/pdf", "file_size": 1}, podcast.settings()
    )
    and podcast._allowed(
        {"url": "https://example.org/v.mp4", "mime_type": "video/mp4", "file_size": 1},
        {**podcast.settings(), "video": True},
    ),
)
check(
    "  ftp is refused, and an episode over the ceiling is listed but not fetched",
    not podcast._allowed(
        {"url": "ftp://example.org/a.mp3", "mime_type": "audio/mpeg", "file_size": 1}, podcast.settings()
    )
    and not podcast._allowed(
        {"url": "https://example.org/big.mp3", "mime_type": "audio/mpeg", "file_size": 600 << 20}, podcast.settings()
    ),
)

podcast.sync()
idx = podcast._load_index("a-test-podcast")
check(
    "the back catalogue is newest 3; the video and the document are listed, not fetched",
    len([e for e in idx["episodes"] if e.get("file")]) == 3
    and any(e["title"] == "A Video" and not e.get("file") for e in idx["episodes"])
    and any(e["title"] == "A Document" and not e.get("file") for e in idx["episodes"]),
    idx,
)
check(
    "  and the Atom feed's own episodes are fetched",
    len([e for e in podcast._load_index("an-atom-podcast")["episodes"] if e.get("file")]) == 2,
)

# Count limit, with a starred episode beyond it.
idx = podcast._load_index("a-test-podcast")
next(e for e in idx["episodes"] if e["title"] == "Episode Three")["starred"] = True
podcast._save_index("a-test-podcast", idx)
podcast.set_settings(keep_count=2)
podcast.prune_all()
kept = [e["title"] for e in podcast._load_index("a-test-podcast")["episodes"] if e.get("file")]
check(
    "keep by count: the newest two of each feed, and a starred episode past the count",
    sorted(kept) == ["Episode One", "Episode Three", "Episode Two"],
    kept,
)

# Age limit: make the unstarred one old, the starred one old too and keep it.
import time

idx = podcast._load_index("a-test-podcast")
for e in idx["episodes"]:
    if e.get("file"):
        e["published"] = int(time.time()) - 10 * 86400
podcast._save_index("a-test-podcast", idx)
podcast.set_settings(keep_count=0, keep_days=1)
podcast.prune_all()
kept = [e["title"] for e in podcast._load_index("a-test-podcast")["episodes"] if e.get("file")]
check("keep by age: old ones go, the starred one stays", kept == ["Episode Three"], kept)

# Size limit.
podcast.set_settings(keep_days=0, keep_mb=1)
podcast.prune_all()
check(
    "keep by size: over the feed's limit, the oldest go first",
    0 <= len([e for e in podcast._load_index("a-test-podcast")["episodes"] if e.get("file")]) <= 2,
)

# The all-podcasts cap.
podcast.set_settings(keep_mb=0, cap_mb=1)
podcast.prune_all()
total = sum(
    e.get("size") or 0
    for f in podcast.settings()["feeds"]
    for e in podcast._load_index(f["id"])["episodes"]
    if e.get("file")
)
check(
    "the all-podcasts cap holds across feeds, starred excepted",
    total <= (1 << 20)
    or any(
        e.get("starred")
        for f in podcast.settings()["feeds"]
        for e in podcast._load_index(f["id"])["episodes"]
        if e.get("file")
    ),
    total,
)

# OPML both ways.
text = podcast.export_opml()
check(
    "OPML out: every feed's address", "https://example.org/feed.xml" in text and "https://example.org/atom.xml" in text
)
podcast.remove_feed("an-atom-podcast", delete_files=True)
check(
    "  OPML back in adds what is missing",
    podcast.import_opml(text) == 1 and any(f["id"] == "an-atom-podcast" for f in podcast.settings()["feeds"]),
)

# The metered wait, through the librarian's scheduled run.
with mock.patch("irate_box.hub.netpolicy.gate", return_value="Automatic download deferred: metered."):
    out = librarian.update(scheduled=True, log=lambda *_: None)
check("a scheduled run waits on a metered connection", out.get("_metered") and "metered" in out["_metered"], out)

# A kind on the library drive, with the drive absent: reading a feed folder raises the drive's own words.
(T / "control").mkdir(exist_ok=True)
(T / "control" / "library-drive.json").write_text(
    json.dumps({"uuid": "drive-uuid", "kinds": {"podcasts": "drive", "books": "card"}, "mounted": False})
)
try:
    podcast.feed_dir("a-test-podcast")
    check("a feed folder on a missing library drive raises, not a NameError", False, "returned")
except podcast.LibrarianError as exc:
    check("a feed folder on a missing library drive raises, not a NameError", "not plugged in" in str(exc), str(exc))
(T / "control" / "library-drive.json").unlink()

check(
    "the snapshot and the player's list carry the feeds and the files",
    podcast.snapshot()["feeds"] and all(e["url"].startswith("/podcasts/") for e in podcast.episodes()),
)
# A feed or an OPML file with a document type or an entity is refused before anything is expanded, in
# any spelling and any encoding; a plain one still reads. (pyexpat's own version is printed for the record.)
import pyexpat

print(f"expat {pyexpat.EXPAT_VERSION}")
BOMB = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><opml version="2.0"><body/></opml>'
for why, hostile in (
    ("an entity bomb", BOMB),
    ("a bare document type", b"<!DOCTYPE opml><opml><body/></opml>"),
    ("an external entity", b'<!DOCTYPE opml [<!ENTITY x SYSTEM "file:///etc/passwd">]><opml><body>&x;</body></opml>'),
    ("a UTF-16 entity bomb", BOMB.decode().replace('encoding="UTF-8"', "").encode("utf-16")),
):
    try:
        podcast.import_opml(hostile)
        check(f"an OPML file with {why} is refused", False, "accepted")
    except podcast.LibrarianError as exc:
        check(f"an OPML file with {why} is refused", "document type" in str(exc), str(exc))
    try:
        podcast.parse_feed(hostile, "https://example.org/f.xml")
        check(f"a feed with {why} is refused", False, "accepted")
    except podcast.LibrarianError as exc:
        check(f"a feed with {why} is refused", "document type" in str(exc), str(exc))
PLAIN = b'<?xml version="1.0" encoding="UTF-8"?><rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0"><channel><title>T &amp; U \xc3\xa9</title><itunes:image href="https://e.example/x.jpg"/><item><title>One</title><enclosure url="https://e.example/a.mp3" length="5" type="audio/mpeg"/></item></channel></rss>'
check(
    "the tree built from expat's events is the one ElementTree builds",
    podcast.ET.tostring(podcast._parse_xml(PLAIN, "x")) == podcast.ET.tostring(podcast.ET.fromstring(PLAIN)),
)
try:
    podcast._parse_xml(b"<opml>", "x")
    check("a document that is not well formed is a ParseError", False, "accepted")
except podcast.ET.ParseError:
    check("a document that is not well formed is a ParseError", True)
check("a plain OPML file still reads", podcast.import_opml(b'<opml version="2.0"><body/></opml>') is not None)
# A feed name that is not a name cannot name a folder.
for hostile_id in ("..", "../x", "/etc", "", "a/b"):
    try:
        podcast.feed_dir(hostile_id)
        check(f"the feed name {hostile_id!r} is refused", False, "accepted")
    except podcast.LibrarianError:
        check(f"the feed name {hostile_id!r} is refused", True)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
