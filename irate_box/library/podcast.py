# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Podcast subscriptions: feeds the librarian follows, and the episodes it keeps on the box.

A feed is an RSS or Atom address the administrator adds on /admin's Library page. The librarian
fetches it on its own schedule, lists the episodes, and downloads the ones the settings allow,
beside the finished files and renamed into place once whole. The player (web/music.js) reads the
episodes from /api/podcasts; a guest's browser never reaches the feed.

What it keeps, under $HUB_STATE_DIR/podcasts/<feed>/:

  <episode-id>.<ext>     an episode's audio (or video, if allowed)
  index.json             the feed's title, cover, last check and episodes (metadata only)
  .part                  a download in flight; cleared on the next check

Settings ($HUB_STATE_DIR/library/podcasts.json):
  enabled              off by default
  check_every_hours    12; 0 means only when asked
  back_catalogue       newest 3 fetched when a feed is added; 0 none, -1 all
  keep_count           per feed, newest kept; 0 means no count limit
  keep_days            per feed, from published (else downloaded); 0 means no age limit
  keep_mb              per feed; 0 means no size limit
  cap_mb               all podcasts together; 0 means no cap
  leave_free_mb        never leave less than this (default: the librarian's min_free_mb)
  keep_played          off: a played episode is pruned like any other
  max_episode_mb       bigger episodes are listed, not fetched
  video                off: audio enclosures only
  feeds                [{id, url, title, check_every_hours?}]

The parser is Debian's python3-podcastparser when installed (gPodder's own), with a small stdlib
fallback for RSS and basic Atom so a checkout without it still works. The feed's text is kept as
text, never rendered as HTML. Only http(s) is fetched, redirects included. Stdlib only.
"""

import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # only its tree and its TreeBuilder: the parse is _parse_xml's, on expat. # nosec B405
from datetime import datetime
from pathlib import Path
from xml.parsers import expat  # nosec B405 - read with a document type and entities refused

from irate_box import confine, validate, webfetch
from irate_box.library import librarian
from irate_box.library.librarian import LibrarianError
from irate_box.msg import M

DEFAULT_ROOT = Path(os.environ.get("HUB_PODCAST_ROOT", librarian.STATE_DIR / "podcasts"))
SETTINGS = librarian.LIB_DIR / "podcasts.json"
STATUS = librarian.LIB_DIR / "podcasts-status.json"
DEFAULTS = {
    "enabled": False,
    "check_every_hours": 12,
    "back_catalogue": 3,
    "keep_count": 10,
    "keep_days": 90,
    "keep_mb": 0,
    "cap_mb": 2048,
    "leave_free_mb": 0,
    "keep_played": False,
    "max_episode_mb": 500,
    "video": False,
    "feeds": [],
}
MAX_FEEDS = 50
MAX_EPISODES = 5000
FEED_BYTES = 4 << 20
USER_AGENT = "irate-box-librarian"
CHUNK = 1 << 20
AUDIO_PREFIXES = ("audio/",)
VIDEO_PREFIXES = ("video/",)
NAME_RE = librarian.NAME_RE
URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.I)
EPISODE_ID_RE = re.compile(r"^[a-f0-9]{16}$")
PART_SUFFIX = ".part"


def _int(value, what, low, high):
    if type(value) is not int or not low <= value <= high:
        raise LibrarianError(
            M(
                "podcast.setting.range",
                "Invalid setting. {what} must be a whole number from {low} to {high}.",
                what=what,
                low=low,
                high=high,
            )
        )
    return value


def _bool(value):
    return bool(value)


# --- settings ------------------------------------------------------------------------------


def settings():
    cfg = dict(DEFAULTS)
    saved = librarian._read_json(SETTINGS, {})
    if isinstance(saved, dict):
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return cfg


def _clean(raw):
    cfg = {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}
    cfg["enabled"] = _bool(cfg["enabled"])
    cfg["video"] = _bool(cfg["video"])
    cfg["keep_played"] = _bool(cfg["keep_played"])
    cfg["check_every_hours"] = _int(cfg["check_every_hours"], "Check every", 0, 168)
    cfg["back_catalogue"] = _int(cfg["back_catalogue"], "New feed: back-catalogue", -1, 1000)
    cfg["keep_count"] = _int(cfg["keep_count"], "Keep per feed, by count", 0, 500)
    cfg["keep_days"] = _int(cfg["keep_days"], "Keep per feed, by age", 0, 3650)
    cfg["keep_mb"] = _int(cfg["keep_mb"], "Keep per feed, by size", 0, 10**7)
    cfg["cap_mb"] = _int(cfg["cap_mb"], "All podcasts' cap", 0, 10**7)
    cfg["leave_free_mb"] = _int(cfg["leave_free_mb"], "Leave free", 0, 10**7)
    cfg["max_episode_mb"] = _int(cfg["max_episode_mb"], "Largest single episode", 1, 4096)
    feeds = cfg.get("feeds") or []
    if not isinstance(feeds, list) or len(feeds) > MAX_FEEDS:
        raise LibrarianError(
            M("podcast.feeds.count", "A box keeps at most {max} feeds. Remove one first.", max=MAX_FEEDS)
        )
    clean = []
    seen = set()
    for raw_feed in feeds:
        feed = validate_feed(raw_feed, existing=False)
        if feed["id"] in seen:
            continue
        seen.add(feed["id"])
        clean.append(feed)
    cfg["feeds"] = clean
    return cfg


def set_settings(**changes):
    cfg = settings()
    cfg.update({k: v for k, v in changes.items() if k in DEFAULTS})
    cfg = _clean(cfg)
    librarian._write_json(SETTINGS, cfg)
    return cfg


def validate_feed(raw, existing=True):
    if not isinstance(raw, dict):
        raise LibrarianError(M("podcast.feed.invalid", "Invalid feed. It must have an address."))
    url = str(raw.get("url", "")).strip()
    if not URL_RE.match(url) or len(url) > 1000:
        raise LibrarianError(M("podcast.feed.url", "Enter the feed's address, starting with http:// or https://."))
    title = str(raw.get("title", "")).strip()[:200]
    feed_id = raw.get("id")
    if not feed_id:
        feed_id = re.sub(r"[^a-z0-9]+", "-", (title or urllib.parse.urlparse(url).netloc).lower()).strip("-")[:32]
        if not feed_id or not NAME_RE.match(feed_id):
            feed_id = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:16]
    if not isinstance(feed_id, str) or not NAME_RE.match(feed_id):
        raise LibrarianError(
            M("podcast.feed.id", "Invalid feed name. Use letters, digits, dots, hyphens and underscores.")
        )
    out = {"id": feed_id, "url": url, "title": title}
    every = raw.get("check_every_hours")
    if every is not None:
        out["check_every_hours"] = _int(every, "Check every", 0, 168)
    return out


# --- feeds ---------------------------------------------------------------------------------


def _fetch(url, limit, accept="application/rss+xml, application/atom+xml, application/xml, text/xml, */*"):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # http(s) checked before and after. # nosec B310
            final = response.geturl()
            if urllib.parse.urlparse(final).scheme not in ("http", "https"):
                raise LibrarianError(M("podcast.feed.scheme", "The feed is not on http or https."))
            data = response.read(limit + 1)
            if len(data) > limit:
                raise LibrarianError(M("podcast.feed.large", "The feed is larger than {mb} MB.", mb=limit >> 20))
            return data, final, response.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LibrarianError(
            M("podcast.feed.unreachable", "Could not read the feed: {error}.", error=str(exc)[:160])
        ) from None


def _text(node):
    return (node.text or "").strip() if node is not None else ""


def _child(node, *names):
    for child in list(node):
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names:
            return child
    return None


def _children(node, *names):
    return [c for c in list(node) if c.tag.rsplit("}", 1)[-1].lower() in names]


def _attr(node, name, default=""):
    for key, value in node.attrib.items():
        if key.rsplit("}", 1)[-1].lower() == name.lower():
            return value
    return default


def _duration(text):
    text = (text or "").strip()
    if not text:
        return 0
    if ":" in text:
        total = 0
        for part in text.split(":"):
            try:
                total = total * 60 + int(part)
            except ValueError:
                return 0
        return total
    try:
        return int(float(text))
    except ValueError:
        return 0


def _pubdate(text):
    text = (text or "").strip()
    if not text:
        return 0
    try:
        from email.utils import parsedate_to_datetime

        return int(parsedate_to_datetime(text).timestamp())
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


class _DocumentType(Exception):
    pass


def _parse_xml(data, refused):
    """The element tree of `data`, built here from expat's events: a document type declaration or an
    entity is refused the moment expat reads one, in any spelling and any encoding (UTF-16 included),
    before anything is expanded, and no external entity or DTD is ever read. `refused` is the message
    said when one is found. A malformed document raises ET.ParseError, as ET.fromstring does. (The
    box's expat is whichever its Debian release links, 2.2 to 2.8; this does not rely on the versions
    from 2.4.1 that bound entity expansion themselves.) The standard `ET.fromstring` is not used: no
    parser in `xml.etree` can be told to refuse them."""

    def refuse(*arguments):
        raise _DocumentType

    def name(text):  # expat's "uri local" is ElementTree's "{uri}local"
        return "{" + text.replace(" ", "}", 1) if " " in text else text

    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartElementHandler = lambda tag, attributes: builder.start(
        name(tag), {name(key): value for key, value in attributes.items()}
    )
    parser.EndElementHandler = lambda tag: builder.end(name(tag))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(data, True)
    except _DocumentType:
        raise LibrarianError(refused) from None
    except expat.ExpatError as error:
        raise ET.ParseError(str(error)) from None
    return builder.close()


def parse_feed(data, url):
    """{title, cover, new_url, episodes}: RSS with iTunes tags or basic Atom. Text is text."""
    if isinstance(data, str):
        data = data.encode()
    try:
        root = _parse_xml(data, M("podcast.feed.doctype", "This feed uses a document type the box does not read."))
    except ET.ParseError as exc:
        raise LibrarianError(
            M("podcast.feed.invalid-xml", "The feed is not valid XML: {error}.", error=str(exc)[:120])
        ) from None
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "rss":
        channel = _child(root, "channel")
        if channel is None:
            raise LibrarianError(M("podcast.feed.no-channel", "The feed has no channel."))
        title = _text(_child(channel, "title"))
        cover = ""
        image = _child(channel, "image")
        if image is not None:
            cover = _text(_child(image, "url"))
        itunes = _child(channel, "image")
        if not cover and itunes is not None:
            cover = _attr(itunes, "href")
        episodes = []
        for item in _children(channel, "item"):
            guid = _text(_child(item, "guid"))
            link = _text(_child(item, "link"))
            title_i = _text(_child(item, "title"))
            published = _pubdate(_text(_child(item, "pubdate", "published", "updated")))
            duration = _duration(_text(_child(item, "duration")))
            enclosures = []
            for enc in _children(item, "enclosure"):
                enc_url = _attr(enc, "url")
                if enc_url:
                    size = _attr(enc, "length")
                    enclosures.append(
                        {
                            "url": enc_url,
                            "mime_type": _attr(enc, "type"),
                            "file_size": int(size) if size.isdigit() else 0,
                        }
                    )
            episodes.append(
                {
                    "guid": guid or link or title_i,
                    "title": title_i or "untitled",
                    "published": published,
                    "total_time": duration,
                    "enclosures": enclosures,
                }
            )
        new_url = ""
        for link in _children(channel, "link"):
            if _attr(link, "rel") == "self" and _attr(link, "href"):
                new_url = _attr(link, "href")
        return {"title": title, "cover": cover, "new_url": new_url, "episodes": episodes}
    if tag == "feed":
        title = _text(_child(root, "title"))
        cover = ""
        for child in list(root):
            if child.tag.rsplit("}", 1)[-1].lower() in ("logo", "icon"):
                cover = _text(child)
                break
        episodes = []
        for entry in _children(root, "entry"):
            guid = _text(_child(entry, "id"))
            title_e = _text(_child(entry, "title"))
            published = _pubdate(_text(_child(entry, "published", "updated")))
            duration = _duration(_text(_child(entry, "duration")))
            enclosures = []
            for link in _children(entry, "link"):
                if _attr(link, "rel") == "enclosure":
                    enc_url = _attr(link, "href")
                    size = _attr(link, "length")
                    if enc_url:
                        enclosures.append(
                            {
                                "url": enc_url,
                                "mime_type": _attr(link, "type"),
                                "file_size": int(size) if size.isdigit() else 0,
                            }
                        )
            episodes.append(
                {
                    "guid": guid or _text(_child(entry, "link")) or title_e,
                    "title": title_e or "untitled",
                    "published": published,
                    "total_time": duration,
                    "enclosures": enclosures,
                }
            )
        new_url = ""
        for link in _children(root, "link"):
            if _attr(link, "rel") == "self" and _attr(link, "href"):
                new_url = _attr(link, "href")
        return {"title": title, "cover": cover, "new_url": new_url, "episodes": episodes}
    raise LibrarianError(M("podcast.feed.not-feed", "This is not an RSS or Atom feed."))


def parse_with_podcastparser(data):
    try:
        import podcastparser  # Debian: python3-podcastparser
    except ImportError:
        return None
    try:
        parsed = podcastparser.parse("", _Bytes(data))
    except Exception:  # noqa: BLE001 - a third-party parser's failure falls back to ours
        return None
    episodes = []
    for item in parsed.get("episodes", []):
        enc = item.get("enclosures") or []
        episodes.append(
            {
                "guid": item.get("guid") or item.get("link") or item.get("title") or "",
                "title": item.get("title") or "untitled",
                "published": int(item.get("published") or 0),
                "total_time": int(item.get("total_time") or 0),
                "enclosures": [
                    {
                        "url": e.get("url", ""),
                        "mime_type": e.get("mime_type", ""),
                        "file_size": int(e.get("file_size") or 0),
                    }
                    for e in enc
                    if e.get("url")
                ],
            }
        )
    return {
        "title": parsed.get("title") or "",
        "cover": parsed.get("cover_url") or "",
        "new_url": parsed.get("new_url") or "",
        "episodes": episodes,
    }


class _Bytes:
    """What podcastparser's parser wants: an object with read(), for bytes already in hand."""

    def __init__(self, data):
        self._data = data

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._data)
        out, self._data = self._data[:n], self._data[n:]
        return out


# --- the library ---------------------------------------------------------------------------


def root():
    """The folder the podcasts go in now: the card, the library drive's, or None when the drive is
    chosen and not plugged in."""
    from irate_box.hub import librarydrive

    return librarydrive.place("podcasts", DEFAULT_ROOT)


def feed_dir(feed_id):
    from irate_box.hub import librarydrive

    base = root()
    if base is None:
        raise LibrarianError(librarydrive.said_drive_missing("podcasts"))
    try:
        return confine.under(base, validate.name(feed_id, "The feed name"))
    except (ValueError, TypeError):
        raise LibrarianError(
            M("podcast.feed.id", "Invalid feed name. Use letters, digits, dots, hyphens and underscores.")
        ) from None


def _index_path(feed_id):
    return feed_dir(feed_id) / "index.json"


def _load_index(feed_id):
    data = librarian._read_json(_index_path(feed_id), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("episodes", [])
    return data


def _save_index(feed_id, index):
    feed_dir(feed_id).mkdir(parents=True, exist_ok=True)
    librarian._write_json(_index_path(feed_id), index)
    try:
        os.chmod(_index_path(feed_id), 0o644)
    except OSError:
        pass


def _episode_id(guid):
    return hashlib.sha1(str(guid).encode(), usedforsecurity=False).hexdigest()[:16]


def _extension(url, mime):
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".mp4", ".m4v", ".webm"):
        return suffix
    return {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/flac": ".flac",
        "audio/wav": ".wav",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
    }.get((mime or "").split(";")[0].strip().lower(), ".mp3")


def _allowed(enc, cfg):
    mime = (enc.get("mime_type") or "").split(";")[0].strip().lower()
    if not (mime.startswith(AUDIO_PREFIXES) or (cfg["video"] and mime.startswith(VIDEO_PREFIXES))):
        return False
    parsed = urllib.parse.urlparse(enc.get("url") or "")
    if parsed.scheme not in ("http", "https"):
        return False
    size = int(enc.get("file_size") or 0)
    return not size or size <= cfg["max_episode_mb"] * (1 << 20)


def _free_mb(path):
    try:
        return shutil.disk_usage(path).free >> 20
    except OSError:
        return 0


def _episode_opener(url, feed_url):
    """How an episode's address is opened. The address came out of the feed, so it is read as an
    answer from the internet: a web address with a host name, and every connection (redirects
    included) to a public address, checked as it connects. The one exception is the feed's own host,
    which the owner chose and which may be on their own network."""
    if feed_url and urllib.parse.urlparse(url).hostname == urllib.parse.urlparse(feed_url).hostname:
        return urllib.request.build_opener()
    try:
        validate.url(url, schemes=("http", "https"), what="The episode address")
    except ValueError:
        raise LibrarianError(
            M(
                "podcast.episode.not-public",
                "The episode is not downloaded. Its address is not a web address with a host name.",
            )
        ) from None
    return webfetch.opener(resolve=lambda *arguments: librarian.RESOLVE(*arguments), schemes=("http", "https"))


def _download(url, path, cfg, log, feed_url=None):
    if _free_mb(path.parent) < max(cfg["leave_free_mb"], cfg["max_episode_mb"]):
        raise LibrarianError(
            M(
                "podcast.download.no-space",
                "Not enough free space for {mb} MB, and the box must leave {free} MB free.",
                mb=cfg["max_episode_mb"],
                free=cfg["leave_free_mb"],
            )
        )
    part = path.with_name(path.name + PART_SUFFIX)
    limit = cfg["max_episode_mb"] * (1 << 20)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _episode_opener(url, feed_url).open(request, timeout=60) as response:  # nosec B310 - http(s), public
            if urllib.parse.urlparse(response.geturl()).scheme not in ("http", "https"):
                raise LibrarianError(M("podcast.episode.scheme", "The episode is not on http or https."))
            length = int(response.headers.get("Content-Length") or 0)
            if length > limit:
                raise LibrarianError(
                    M("podcast.episode.large", "The episode is larger than {mb} MB.", mb=cfg["max_episode_mb"])
                )
            got = 0
            part.parent.mkdir(parents=True, exist_ok=True)
            with open(part, "wb") as out:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    got += len(block)
                    if got > limit:
                        raise LibrarianError(
                            M("podcast.episode.large", "The episode is larger than {mb} MB.", mb=cfg["max_episode_mb"])
                        )
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
        os.replace(part, path)
        return got
    except LibrarianError:
        part.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        part.unlink(missing_ok=True)
        raise LibrarianError(
            M("podcast.episode.unreachable", "Could not download the episode: {error}.", error=str(exc)[:160])
        ) from None


def _episode_size(ep):
    try:
        return Path(ep.get("file", "")).stat().st_size if ep.get("file") else 0
    except OSError:
        return 0


def prune(feed_id, cfg=None):
    """The plan's rule: whichever limit is reached first decides; starred episodes are never pruned."""
    cfg = cfg or settings()
    index = _load_index(feed_id)
    episodes = [e for e in index.get("episodes", []) if e.get("file")]
    now = time.time()
    for ep in episodes:
        ep["_size"] = _episode_size(ep)
    kept = []
    # Newest first; old ones past a limit go.
    ordered = sorted(episodes, key=lambda e: e.get("published") or e.get("downloaded") or 0, reverse=True)
    total = 0
    for position, ep in enumerate(ordered):
        starred = bool(ep.get("starred"))
        age_days = (now - (ep.get("published") or ep.get("downloaded") or now)) / 86400
        over = False
        if cfg["keep_count"] and position >= cfg["keep_count"] and not starred:
            over = True
        if cfg["keep_days"] and age_days > cfg["keep_days"] and not starred:
            over = True
        if cfg["keep_mb"] and total + ep["_size"] > cfg["keep_mb"] * (1 << 20) and not starred:
            over = True
        if over:
            continue
        kept.append(ep)
        total += ep["_size"]
    kept_ids = {e["id"] for e in kept}
    for ep in episodes:
        if ep["id"] not in kept_ids:
            try:
                Path(ep["file"]).unlink()
            except OSError:
                pass
    index["episodes"] = [e for e in index.get("episodes", []) if e.get("id") in kept_ids or not e.get("file")]
    for ep in index["episodes"]:
        ep.pop("_size", None)
    _save_index(feed_id, index)
    return len(episodes) - len(kept)


def prune_all(cfg=None):
    cfg = cfg or settings()
    removed = 0
    episodes = []
    for feed in cfg["feeds"]:
        removed += prune(feed["id"], cfg)
        for ep in _load_index(feed["id"]).get("episodes", []):
            if ep.get("file"):
                episodes.append((feed["id"], ep))
    if cfg["cap_mb"]:
        cap = cfg["cap_mb"] * (1 << 20)
        total = sum(_episode_size(ep) for _, ep in episodes)
        for feed_id, ep in sorted(episodes, key=lambda item: item[1].get("published") or 0):
            if total <= cap:
                break
            if ep.get("starred"):
                continue
            size = _episode_size(ep)
            try:
                Path(ep["file"]).unlink()
            except OSError:
                pass
            index = _load_index(feed_id)
            index["episodes"] = [x for x in index.get("episodes", []) if x.get("id") != ep["id"]]
            _save_index(feed_id, index)
            total -= size
            removed += 1
    return removed


def _merge_episodes(feed_id, parsed, cfg):
    index = _load_index(feed_id)
    by_id = {e.get("id"): e for e in index.get("episodes", [])}
    episodes = list(parsed.get("episodes", []))
    episodes.sort(key=lambda e: e.get("published") or 0, reverse=True)
    back = cfg["back_catalogue"]
    wanted = episodes if back < 0 else episodes[: max(back, 0)]
    added = 0
    for ep in wanted:
        eid = _episode_id(ep["guid"])
        if eid in by_id:
            continue
        encs = [x for x in ep.get("enclosures", []) if _allowed(x, cfg)]
        if not encs:
            continue
        enc = encs[0]
        by_id[eid] = {
            "id": eid,
            "guid": ep["guid"],
            "title": ep["title"],
            "published": ep.get("published") or 0,
            "duration": ep.get("total_time") or 0,
            "mime": enc.get("mime_type") or "",
            "url": enc["url"],
            "file": "",
            "size": 0,
            "starred": False,
            "played": False,
        }
        added += 1
    # Episodes past the back catalogue are still listed (so the player shows what exists), without files.
    for ep in episodes:
        eid = _episode_id(ep["guid"])
        if eid in by_id:
            continue
        by_id[eid] = {
            "id": eid,
            "guid": ep["guid"],
            "title": ep["title"],
            "published": ep.get("published") or 0,
            "duration": ep.get("total_time") or 0,
            "mime": "",
            "url": "",
            "file": "",
            "size": 0,
            "starred": False,
            "played": False,
        }
    index["title"] = parsed.get("title") or index.get("title") or feed_id
    index["cover"] = parsed.get("cover") or index.get("cover") or ""
    index["new_url"] = parsed.get("new_url") or ""
    index["episodes"] = sorted(by_id.values(), key=lambda e: e.get("published") or 0, reverse=True)[:MAX_EPISODES]
    _save_index(feed_id, index)
    return added


def _download_new(feed_id, cfg, log):
    index = _load_index(feed_id)
    wanted = []
    for ep in index.get("episodes", []):
        if ep.get("file") or not ep.get("url"):
            continue
        if not _allowed({"url": ep["url"], "mime_type": ep["mime"], "file_size": ep.get("size") or 0}, cfg):
            continue
        wanted.append(ep)
    wanted.sort(key=lambda e: e.get("published") or 0, reverse=True)
    room = cfg["keep_count"] or 10**9
    if cfg["cap_mb"]:
        used = sum(_episode_size(e) for e in index.get("episodes", []))
        if used >= cfg["cap_mb"] * (1 << 20):
            return 0
    done = 0
    for ep in wanted:
        if done >= room:
            break
        path = feed_dir(feed_id) / (ep["id"] + _extension(ep["url"], ep.get("mime")))
        try:
            log(f"podcasts: downloading {ep['title'][:60]}")
            size = _download(ep["url"], path, cfg, log, index.get("url"))
        except LibrarianError as exc:
            ep["error"] = str(exc)
            continue
        ep["file"] = str(path)
        ep["size"] = size
        ep["downloaded"] = int(time.time())
        ep.pop("error", None)
        done += 1
    _save_index(feed_id, index)
    return done


def check_feed(feed, cfg, check_only=False, log=print, first=False):
    data, final, _ctype = _fetch(feed["url"], FEED_BYTES)
    parsed = parse_with_podcastparser(data) or parse_feed(data, final)
    if parsed.get("new_url") and parsed["new_url"] != feed["url"]:
        # Followed only after the administrator agrees: it is shown, never followed by itself.
        pass
    feed_id = feed["id"]
    index = _load_index(feed_id)
    index["url"] = feed["url"]
    index["last_check"] = int(time.time())
    index["last_error"] = ""
    _save_index(feed_id, index)
    added = _merge_episodes(feed_id, parsed, cfg)
    downloaded = 0 if check_only else _download_new(feed_id, cfg, log)
    if not check_only:
        prune(feed_id, cfg)
    return {"added": added, "downloaded": downloaded, "title": parsed.get("title") or feed.get("title") or feed_id}


def add_feed(url, title="", cfg=None):
    cfg = cfg or settings()
    feed = validate_feed({"url": url, "title": title})
    if any(f["id"] == feed["id"] or f["url"] == feed["url"] for f in cfg["feeds"]):
        raise LibrarianError(M("podcast.feed.exists", "That feed is already on the box."))
    cfg["feeds"].append(feed)
    cfg = _clean(cfg)
    librarian._write_json(SETTINGS, cfg)
    return feed


def remove_feed(feed_id, delete_files=False):
    cfg = settings()
    feed = next((f for f in cfg["feeds"] if f["id"] == feed_id), None)
    if feed is None:
        raise LibrarianError(M("podcast.feed.unknown", "There is no feed called {id}.", id=feed_id))
    cfg["feeds"] = [f for f in cfg["feeds"] if f["id"] != feed_id]
    librarian._write_json(SETTINGS, cfg)
    if delete_files:
        shutil.rmtree(feed_dir(feed_id), ignore_errors=True)
    return feed


def due(hours):
    try:
        last = json.loads(STATUS.read_text()).get("last_check") or 0
    except (OSError, ValueError, AttributeError):
        last = 0
    return not last or time.time() - last >= max(0, hours) * 3600


def active():
    return bool(settings().get("enabled") and settings().get("feeds"))


def sync(check_only=False, log=print, scheduled=False):
    cfg = settings()
    if not cfg["enabled"]:
        return "off"
    if not cfg["feeds"]:
        return "no feeds"
    from irate_box.hub import librarydrive

    if librarydrive.drive_missing("podcasts"):
        return "waiting: the library drive is not plugged in"
    now = int(time.time())
    results = {}
    for feed in cfg["feeds"]:
        every = feed.get("check_every_hours", cfg["check_every_hours"])
        index = _load_index(feed["id"])
        last = index.get("last_check") or 0
        if scheduled and every and last and now - last < every * 3600:
            continue
        try:
            results[feed["id"]] = check_feed(feed, cfg, check_only=check_only, log=log)
        except LibrarianError as exc:
            index["last_check"] = now
            index["last_error"] = str(exc)
            _save_index(feed["id"], index)
            results[feed["id"]] = f"error: {exc}"
            log(f"podcasts: {feed['id']}: {exc}")
    if not check_only:
        prune_all(cfg)
    librarian._write_json(STATUS, {"last_check": now})
    return results


def set_episode(feed_id, episode_id, starred=None, played=None):
    """Mark one kept episode, on the player or /admin. Only the flags given change."""
    index = _load_index(feed_id)
    for ep in index.get("episodes", []):
        if ep.get("id") == episode_id:
            if starred is not None:
                ep["starred"] = bool(starred)
            if played is not None:
                ep["played"] = bool(played)
            _save_index(feed_id, index)
            return ep
    raise LibrarianError(M("podcast.episode.unknown", "There is no episode called {id}.", id=episode_id))


def episodes():
    """Every kept episode, newest first, for the player: one list over all feeds."""
    out = []
    cfg = settings()
    for feed in cfg["feeds"]:
        index = _load_index(feed["id"])
        for ep in index.get("episodes", []):
            if not ep.get("file") or not Path(ep["file"]).is_file():
                continue
            out.append(
                {
                    "feed": feed["id"],
                    "feed_title": index.get("title") or feed["id"],
                    "id": ep["id"],
                    "title": ep["title"],
                    "published": ep.get("published") or 0,
                    "duration": ep.get("duration") or 0,
                    "size": _episode_size(ep),
                    "starred": bool(ep.get("starred")),
                    "played": bool(ep.get("played")),
                    "url": f"/podcasts/{feed['id']}/{Path(ep['file']).name}",
                }
            )
    out.sort(key=lambda e: e.get("published") or 0, reverse=True)
    return out


def snapshot():
    cfg = settings()
    feeds = []
    total = 0
    for feed in cfg["feeds"]:
        index = _load_index(feed["id"])
        kept = [e for e in index.get("episodes", []) if e.get("file")]
        size = sum(_episode_size(e) for e in kept)
        total += size
        feeds.append(
            {
                **feed,
                "title": index.get("title") or feed.get("title") or feed["id"],
                "last_check": index.get("last_check") or 0,
                "last_error": index.get("last_error") or "",
                "new_url": index.get("new_url") or "",
                "episodes": len(kept),
                "listed": len(index.get("episodes", [])),
                "size": size,
            }
        )
    return {
        "settings": {k: v for k, v in cfg.items() if k != "feeds"},
        "feeds": feeds,
        "used": total,
        "free_mb": _free_mb(root() or DEFAULT_ROOT),
    }


# --- OPML ----------------------------------------------------------------------------------


def export_opml():
    cfg = settings()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "<head>",
        "<title>Irate-Box podcasts</title>",
        "</head>",
        "<body>",
    ]
    for feed in cfg["feeds"]:
        title = feed.get("title") or feed["id"]
        lines.append(f'  <outline type="rss" text="{_xml(title)}" title="{_xml(title)}" xmlUrl="{_xml(feed["url"])}"/>')
    lines += ["</body>", "</opml>"]
    return "\n".join(lines) + "\n"


def _xml(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def import_opml(text):
    try:
        root = _parse_xml(
            text.encode() if isinstance(text, str) else text,
            M("podcast.opml.doctype", "This file uses a document type the box does not read."),
        )
    except ET.ParseError as exc:
        raise LibrarianError(
            M("podcast.opml.invalid", "That is not an OPML file: {error}.", error=str(exc)[:120])
        ) from None
    if root.tag.rsplit("}", 1)[-1].lower() != "opml":
        raise LibrarianError(M("podcast.opml.not", "That is not an OPML file."))
    cfg = settings()
    added = 0
    known = {f["url"] for f in cfg["feeds"]}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() != "outline":
            continue
        url = _attr(node, "xmlUrl")
        if not url or url in known or not URL_RE.match(url):
            continue
        feed = validate_feed({"url": url, "title": _attr(node, "title") or _attr(node, "text")})
        if any(f["id"] == feed["id"] for f in cfg["feeds"]):
            feed["id"] = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:16]
        cfg["feeds"].append(feed)
        known.add(url)
        added += 1
        if len(cfg["feeds"]) >= MAX_FEEDS:
            break
    librarian._write_json(SETTINGS, _clean(cfg))
    return added


# --- what the doctor says ------------------------------------------------------------------


def findings():
    """(id, title, status, summary) for the Box doctor: only what a check recorded, never a guess."""
    cfg = settings()
    if not cfg["enabled"]:
        return []
    out = []
    for feed in cfg["feeds"]:
        index = _load_index(feed["id"])
        err = index.get("last_error") or ""
        if err:
            out.append(("podcast-feed-error", "A podcast feed keeps failing", "warn", f"{feed['id']}: {err}"))
        failed = sum(1 for e in index.get("episodes", []) if e.get("error"))
        if failed >= 3:
            out.append(
                (
                    "podcast-episode-errors",
                    "Podcast episodes keep failing to download",
                    "warn",
                    f"{feed['id']}: {failed} episodes",
                )
            )
    used = sum(
        _episode_size(e) for f in cfg["feeds"] for e in _load_index(f["id"]).get("episodes", []) if e.get("file")
    )
    if cfg["cap_mb"] and used >= 0.9 * cfg["cap_mb"] * (1 << 20):
        out.append(
            (
                "podcast-space",
                "The podcasts are near their space cap",
                "warn",
                f"{used >> 20} MB of {cfg['cap_mb']} MB",
            )
        )
    starred = sum(
        1 for f in cfg["feeds"] for e in _load_index(f["id"]).get("episodes", []) if e.get("file") and e.get("starred")
    )
    if cfg["cap_mb"] and used >= cfg["cap_mb"] * (1 << 20) and starred:
        out.append(
            (
                "podcast-starred-cap",
                "Only starred episodes fill the podcasts' cap",
                "warn",
                f"{starred} starred episodes",
            )
        )
    return out
