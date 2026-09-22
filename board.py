#!/usr/bin/env python3
"""Threaded board — the shoutbox's slower companion.

The shoutbox in server.py is a ring buffer for one-liners: fast, disposable, and it
scrolls away on its own. This keeps conversations instead, so something posted in the
morning is still findable in the afternoon.

Decay is deliberately two-layered, because neither rule is sufficient alone:

  * a thread dies TTL ticks after its *last activity* rather than its creation, so a
    conversation still being replied to is never cut off mid-flow; and
  * independently, the oldest threads are dropped once MAX_THREADS is exceeded.

The count cap is what keeps the box bounded when the tick rule cannot fire. A hub
that only ever runs for ten minutes at a stretch accrues ticks very slowly, so a
seven-day TTL might take months of real time to expire anything -- with the cap, the
board still behaves like an imageboard and pushes old threads off the end.

All ages come from hubclock, never the wall clock. See hubclock.py for why.
"""

import json
import os
import threading
from pathlib import Path

MAX_THREADS = 50
MAX_POSTS_PER_THREAD = 200
THREAD_TTL = 7 * 24 * 3600  # powered-on seconds since last activity

MAX_AUTHOR = 32
MAX_TITLE = 80
MAX_TEXT = 1000


def _clean_hue(value):
    """Optional author colour, stored as a hue only (0-359) -- the client's palette
    picks saturation and lightness. None means "derive it from the name", so a guest
    who renames re-colours instead of carrying a stale hue around."""
    if value is None:
        return None
    try:
        return int(value) % 360
    except (TypeError, ValueError):
        return None


def _clean(value, limit):
    return str(value or "").strip()[:limit]


def _post(author, text, now, hue):
    post = {"author": author, "text": text, "created": now}
    if hue is not None:
        post["hue"] = hue
    return post


class Board:
    def __init__(self, path, clock, max_threads=MAX_THREADS,
                 max_posts=MAX_POSTS_PER_THREAD, ttl=THREAD_TTL):
        self.path = Path(path)
        self.clock = clock
        self.max_threads = max_threads
        self.max_posts = max_posts
        self.ttl = ttl
        self._lock = threading.Lock()

    # --- persistence -------------------------------------------------------

    def _load(self):
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"next_id": 1, "threads": []}
        state.setdefault("next_id", 1)
        state.setdefault("threads", [])
        return state

    def _save(self, state):
        tmp = self.path.parent / (self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump(state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    # --- decay -------------------------------------------------------------

    def _prune(self, state, now):
        """Drop expired and overflowing threads. Returns the surviving list."""
        alive = [t for t in state["threads"]
                 if now - t.get("active", 0) <= self.ttl]
        # Newest activity first, then keep only the head of the list.
        # Id breaks ties so the newest thread wins when two share an activity tick.
        alive.sort(key=lambda t: (t.get("active", 0), t["id"]), reverse=True)
        state["threads"] = alive[:self.max_threads]
        return state["threads"]

    def _visible(self, state, now):
        """Expired threads as seen by a reader, without writing to disk.

        Reads stay pure -- physical removal happens on the next write. On a box where
        posting is rare and reading is common, that is a lot of SD writes avoided.
        """
        alive = [t for t in state["threads"] if now - t.get("active", 0) <= self.ttl]
        # Id breaks ties so the newest thread wins when two share an activity tick.
        alive.sort(key=lambda t: (t.get("active", 0), t["id"]), reverse=True)
        return alive[:self.max_threads]

    # --- reads -------------------------------------------------------------

    def list_threads(self):
        now = self.clock.ticks()
        state = self._load()
        threads = [
            {
                "id": t["id"],
                "title": t["title"],
                "author": t["author"],
                "created": t["created"],
                "active": t["active"],
                "replies": max(0, len(t["posts"]) - 1),
                "excerpt": t["posts"][0]["text"][:140] if t["posts"] else "",
                "hue": t["posts"][0].get("hue") if t["posts"] else None,
            }
            for t in self._visible(state, now)
        ]
        return {"now": now, "ttl": self.ttl, "threads": threads}

    def get_thread(self, tid):
        now = self.clock.ticks()
        state = self._load()
        for t in self._visible(state, now):
            if t["id"] == tid:
                return {"now": now, "ttl": self.ttl, "thread": t}
        return None

    # --- writes ------------------------------------------------------------

    def create_thread(self, author, title, text, hue=None):
        author, title, text = (_clean(author, MAX_AUTHOR),
                               _clean(title, MAX_TITLE),
                               _clean(text, MAX_TEXT))
        hue = _clean_hue(hue)
        if not author or not title or not text:
            raise ValueError("author, title and text are required")

        with self._lock:
            now = self.clock.ticks()
            state = self._load()
            thread = {
                "id": state["next_id"],
                "title": title,
                "author": author,
                "created": now,
                "active": now,
                "posts": [_post(author, text, now, hue)],
            }
            state["next_id"] += 1
            state["threads"].append(thread)
            self._prune(state, now)
            self._save(state)
            return {"now": now, "thread": thread}

    def reply(self, tid, author, text, hue=None):
        author, text = _clean(author, MAX_AUTHOR), _clean(text, MAX_TEXT)
        hue = _clean_hue(hue)
        if not author or not text:
            raise ValueError("author and text are required")

        with self._lock:
            now = self.clock.ticks()
            state = self._load()
            for t in state["threads"]:
                if t["id"] != tid or now - t.get("active", 0) > self.ttl:
                    continue
                post = _post(author, text, now, hue)
                t["posts"].append(post)
                # Oldest replies fall off, but never the opening post -- losing it
                # would leave a thread with a title and no subject.
                if len(t["posts"]) > self.max_posts:
                    t["posts"] = [t["posts"][0]] + t["posts"][-(self.max_posts - 1):]
                t["active"] = now
                self._prune(state, now)
                self._save(state)
                return {"now": now, "post": post}
            return None
