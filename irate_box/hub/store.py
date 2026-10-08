#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Shared blob store for the hub: Excalidraw's storage API and a saved-work gallery.

Two HTTP contracts over one directory of files, because they want the same thing.

1. `/api/v2/{scenes,rooms,files}` reimplements excalidraw-storage-backend exactly, so
   `VITE_APP_HTTP_STORAGE_BACKEND_URL` can point here instead. That deletes a NestJS
   service and a Redis container from the deployment: the original is a `Keyv` over
   three namespaces with get/set, and this is the same thing over a directory.

2. `/api/saves` is the gallery — a named save with an optional client-rendered
   thumbnail, for Mermaid's History panel and anything else that wants to persist a
   document. Deliberately not a second store: same files, same quota, same eviction.

Everything expires and is ordered by hubclock ticks, never the wall clock, for the
reasons in hubclock.py. Thumbnails are always rendered by the browser and uploaded --
this process must never decode or resize an image, because on a Zero W that is the
difference between a hub that works and one that stalls for everyone on the SSID.

Run directly to serve just this API, which is how to test it against a desktop
Excalidraw or Mermaid build without the rest of the hub:

    ./irate-box store            # listens on :8090, state in ./store-state
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
from pathlib import Path

from irate_box import confine
from irate_box.hub import hubclock

# Namespaces the Excalidraw frontend uses. Scenes are shared drawings, rooms are
# collaboration state, files are pasted images.
BLOB_NAMESPACES = ("scenes", "rooms", "files")

# The upstream default, and the frontend can genuinely produce large pastes.
MAX_BODY = int(os.environ.get("HUB_STORE_MAX_BODY", 50 * 1024 * 1024))
DEV_CORS = os.environ.get("HUB_STORE_CORS", "")  # one origin, for development only (F20)
# Total on-disk budget for everything here. The card also holds ZIMs and firmware, so
# this is a real ceiling rather than a formality; oldest content is evicted to stay under.
MAX_TOTAL = int(os.environ.get("HUB_STORE_MAX_TOTAL", 64 * 1024 * 1024))
# 0 disables. Saves are meant to persist; the file drop will set its own.
SAVE_TTL = float(os.environ.get("HUB_STORE_SAVE_TTL", 0))

GLOBAL_PREFIX = os.environ.get("HUB_STORE_PREFIX", "/api/v2")

# The file drop (/api/drop): its own folder and budget, apart from the store's, since a few
# phone videos would otherwise evict everyone's drawings. The per-file cap is the web server's
# too (client_max_body_size, or Caddy's request_body, on /api/drop), which refuses a large
# body before it is read.
DROP_MAX_FILE = int(os.environ.get("HUB_DROP_MAX_FILE", 25 * 1024 * 1024))
DROP_MAX_TOTAL = int(os.environ.get("HUB_DROP_MAX_TOTAL", 256 * 1024 * 1024))
DROP_TTL = float(os.environ.get("HUB_DROP_TTL", 24 * 3600))  # powered-on seconds; 0 = never
# Room an upload leaves on the card, so a full drop never fills the books' filesystem.
DROP_MIN_FREE = 64 * 1024 * 1024
DROP_CHUNK = 64 * 1024
MAX_FILENAME = 160

# Ids appear in filesystem paths, so they are validated rather than escaped.
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

MAX_NAME = 64
MAX_KIND = 32


# --- locks ---------------------------------------------------------------------------
# A guest can lock a save or a dropped file so that nobody else can change or remove it (it
# still expires and is evicted as usual, and the admin can still remove it). No password is
# typed: the hub serves plain HTTP on an open network, where a password would cross the air in
# clear on every use, and people reuse passwords. Instead a hash chain (S/KEY): the browser
# picks a random seed and sends only head = SHA-256 applied n times to it. Each change sends
# the value one step earlier (the "proof"); the hub checks that one SHA-256 of it is the head
# it holds, then keeps the proof as the new head. A listener sees values already used, and
# cannot hash backwards to make the next one. When n runs low the browser sends a new head
# with a valid proof (X-Lock-Next). Headers:
#   X-Lock-New: <head hex>:<n>    lock what this request creates
#   X-Lock: <proof hex>           authorise a change to something locked
#   X-Lock-Next: <head hex>:<n>   replace the chain (with a valid X-Lock)
# web/lock.js is the browser side. Responses carry "locked" and "lock_n" (how many changes
# the chain has left), never the head.
LOCK_MAX_N = 65536
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LockError(Exception):
    """A change to a locked item without a valid proof."""

    def __init__(self, lock, pinned=False):
        super().__init__("this file is the box's own; only the admin can remove it" if pinned
                         else "locked: only the device that locked it can change it")
        self.lock = lock
        self.pinned = pinned


def parse_lock(value):
    """{"head", "n"} from "<head>:<n>", or None."""
    m = re.fullmatch(r"([0-9a-f]{64}):(\d{1,6})", (value or "").strip().lower())
    if not m or not 1 <= int(m.group(2)) <= LOCK_MAX_N:
        return None
    return {"head": m.group(1), "n": int(m.group(2))}


def advance_lock(lock, proof):
    """The lock after a valid proof, or None if the proof is not the next step of the chain."""
    proof = (proof or "").strip().lower()
    if not lock or not _HEX64.match(proof) or lock.get("n", 0) < 1:
        return None
    if hashlib.sha256(bytes.fromhex(proof)).hexdigest() != lock.get("head"):
        return None
    return {"head": proof, "n": lock["n"] - 1}


def public_meta(meta):
    """Metadata as clients see it: whether it is locked, never the lock itself."""
    out = {k: v for k, v in meta.items() if k != "lock"}
    out["locked"] = bool(meta.get("lock"))
    if meta.get("lock"):
        out["lock_n"] = meta["lock"]["n"]
    return out


def _lock_after(current, proof=None, new=None, nxt=None, force=False):
    """What the lock becomes for a change, or LockError. current: the item's lock (or None)."""
    if current and not force:
        advanced = advance_lock(current, proof)
        if advanced is None:
            raise LockError(current)
        return nxt or advanced
    if current and force:
        return current
    return new  # unlocked: whoever writes may lock it


def _numeric_id(length=16):
    """Scene ids must be digits only -- the Excalidraw frontend parses them as numbers.

    Upstream uses nanoid with a '0123456789' alphabet for exactly this reason. Getting
    it wrong produces a share link the app silently refuses to open.
    """
    return "".join(secrets.choice("0123456789") for _ in range(length))


def _etag(data):
    """Short content hash, so a polling client can ask 'changed?' for ~0 bytes.

    This is what makes poll-based collaboration viable without a websocket: a client
    holding the current ETag gets a 304 and no body until someone actually edits.
    """
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


class Store:
    """Blobs plus sidecar metadata, in one flat directory per namespace."""

    def __init__(self, root, clock=None):
        self.root = Path(root)
        self.clock = clock or hubclock.get_clock()
        self._lock = threading.Lock()

    def _dir(self, namespace):
        path = confine.under(self.root, namespace)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _blob_path(self, namespace, key):
        return confine.under(self._dir(namespace), key)

    def _write(self, path, data):
        """Write-and-rename, as everywhere else in the hub: a power cut mid-write must
        leave the old file intact rather than a truncated new one."""
        tmp = path.parent / (path.name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    # -- blob API (Excalidraw) ------------------------------------------------

    def get(self, namespace, key):
        path = self._blob_path(namespace, key)
        try:
            return path.read_bytes()
        except OSError:
            return None

    def put(self, namespace, key, data):
        with self._lock:
            self._write(self._blob_path(namespace, key), data)
            self._enforce_quota()
        return key

    def create_scene(self, data):
        with self._lock:
            for _ in range(8):
                key = _numeric_id()
                path = self._blob_path("scenes", key)
                if not path.exists():
                    self._write(path, data)
                    self._enforce_quota()
                    return key
        # 16 digits colliding eight times running means something is badly wrong.
        raise RuntimeError("could not allocate a scene id")

    # -- saves API (gallery) --------------------------------------------------

    def save(self, kind, name, payload, thumb=None, key=None, proof=None, lock_new=None, lock_next=None):
        """Store a document plus optional pre-rendered thumbnail. Returns its metadata.

        A client may supply its own id. The Mermaid History panel mints uuids locally
        before it knows the save succeeded, and letting it keep them means the client
        never has to reconcile a local id against a server-assigned one. Overwriting a
        locked save needs its proof (see "locks" above); raises LockError otherwise.
        """
        key = key or secrets.token_hex(8)
        meta = {
            "id": key,
            "kind": kind,
            "name": name,
            "created": self.clock.ticks(),
            "size": len(payload),
            "thumb": bool(thumb),
        }
        with self._lock:
            saves = self._dir("saves")
            old = self._read_meta(confine.under(saves, key + ".meta"))
            lock = _lock_after((old or {}).get("lock"), proof, lock_new, lock_next)
            if lock:
                meta["lock"] = lock
            self._write(confine.under(saves, key + ".data"), payload)
            if thumb:
                self._write(confine.under(saves, key + ".thumb"), thumb)
            self._write(confine.under(saves, key + ".meta"), json.dumps(meta).encode())
            self._enforce_quota()
        return meta

    def list_saves(self):
        """Metadata only, newest first. Payloads are never read to build a listing."""
        now = self.clock.ticks()
        out = []
        for meta_path in self._dir("saves").glob("*.meta"):
            meta = self._read_meta(meta_path)
            if meta is None:
                continue
            if SAVE_TTL and now - meta.get("created", now) > SAVE_TTL:
                self._drop_save(meta["id"])
                continue
            out.append(meta)
        out.sort(key=lambda m: m.get("created", 0), reverse=True)
        return out

    def rename_save(self, key, name, proof=None, lock_next=None, unlock=False, force=False):
        """Rename (and, with unlock, remove the lock). force: the admin, past any lock."""
        meta_path = confine.under(self._dir("saves"), key + ".meta")
        with self._lock:
            meta = self._read_meta(meta_path)
            if meta is None:
                return None
            lock = _lock_after(meta.get("lock"), proof, None, lock_next, force)
            if name:
                meta["name"] = name
            if unlock or not lock:
                meta.pop("lock", None)
            else:
                meta["lock"] = lock
            self._write(meta_path, json.dumps(meta).encode())
        return meta

    def get_save(self, key):
        meta = self._read_meta(confine.under(self._dir("saves"), key + ".meta"))
        if meta is None:
            return None
        data = self.get("saves", key + ".data")
        if data is None:
            return None
        return meta, data

    def get_thumb(self, key):
        return self.get("saves", key + ".thumb")

    def delete_save(self, key, proof=None, force=False):
        with self._lock:
            meta = self._read_meta(confine.under(self._dir("saves"), key + ".meta"))
            if meta and meta.get("lock") and not force:
                if advance_lock(meta["lock"], proof) is None:
                    raise LockError(meta["lock"])
            return self._drop_save(key)

    def _read_meta(self, path):
        try:
            meta = json.loads(path.read_text())
        except (OSError, ValueError):
            return None  # torn write from a power cut; treat as absent
        return meta if isinstance(meta, dict) and "id" in meta else None

    def _drop_save(self, key):
        found = False
        for suffix in (".data", ".thumb", ".meta"):
            try:
                (confine.under(self._dir("saves"), key + suffix)).unlink()
                found = True
            except OSError:
                pass
        return found

    # -- quota ----------------------------------------------------------------

    # -- admin -----------------------------------------------------------------

    def usage(self):
        """Files and bytes per namespace, against the quota. For the admin page."""
        out = {"max_total": MAX_TOTAL, "save_ttl": SAVE_TTL, "namespaces": {}}
        total = 0
        for namespace in BLOB_NAMESPACES + ("saves",):
            files = size = 0
            for path in self._dir(namespace).iterdir():
                if path.is_file() and path.suffix != ".tmp":
                    try:
                        size += path.stat().st_size
                        files += 1
                    except OSError:
                        pass
            out["namespaces"][namespace] = {"files": files, "bytes": size}
            total += size
        out["total"] = total
        return out

    def clear_namespace(self, namespace):
        """Delete every blob in one of BLOB_NAMESPACES (shared scenes, rooms, files)."""
        if namespace not in BLOB_NAMESPACES:
            raise ValueError(f"not a blob namespace: {namespace}")
        removed = 0
        with self._lock:
            for path in self._dir(namespace).iterdir():
                if path.is_file():
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def _enforce_quota(self):
        """Evict oldest-first until the whole store fits in MAX_TOTAL.

        Called with the lock held. With no authentication anywhere on this hub, the
        quota is the only thing standing between a guest and a full SD card, so it runs
        on every write rather than on a timer.
        """
        entries = []
        total = 0
        for namespace in BLOB_NAMESPACES + ("saves",):
            for path in self._dir(namespace).iterdir():
                if not path.is_file() or path.suffix == ".tmp":
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                total += stat.st_size
                entries.append((stat.st_mtime, stat.st_size, path))

        if total <= MAX_TOTAL:
            return

        # mtime is fine for ordering here even though it is wall-clock derived: within
        # one boot it is monotonic, and across boots the only cost of getting the order
        # wrong is evicting the wrong old thing first.
        entries.sort()
        for _, size, path in entries:
            if total <= MAX_TOTAL:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass


class Drop:
    """Files guests leave for each other: <id>.data with a sidecar <id>.meta (name, size, who,
    created in hubclock ticks). Bodies are streamed to disk, never held whole in memory; the
    oldest go first when the budget is reached, and each lives DROP_TTL of powered-on time."""

    def __init__(self, root, clock=None):
        self.dir = Path(root)
        self.clock = clock or hubclock.get_clock()
        self._lock = threading.Lock()

    def _metas(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        out = []
        for path in self.dir.glob("*.meta"):
            try:
                meta = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(meta, dict) and ID_RE.match(str(meta.get("id", ""))):
                out.append(meta)
        return out

    def _remove(self, key):
        found = False
        for suffix in (".data", ".meta"):
            try:
                (confine.under(self.dir, key + suffix)).unlink()
                found = True
            except OSError:
                pass
        return found

    def _prune(self, keep=None):
        """Expired files go; then the oldest, until the rest fit DROP_MAX_TOTAL. Lock held.
        Pinned files (the box's own source, put there by install.sh) never expire or go."""
        now = self.clock.ticks()
        metas = []
        for meta in self._metas():
            if not meta.get("pinned") and DROP_TTL and now - meta.get("created", now) > DROP_TTL:
                self._remove(meta["id"])
            else:
                metas.append(meta)
        metas.sort(key=lambda m: m.get("created", 0))
        total = sum(m.get("size", 0) for m in metas)
        for meta in metas:
            if total <= DROP_MAX_TOTAL:
                break
            if meta["id"] != keep and not meta.get("pinned"):
                self._remove(meta["id"])
                total -= meta.get("size", 0)
        return [m for m in metas if confine.under(self.dir, m["id"] + ".meta").exists()]

    def pin(self, src, key, name, by=""):
        """A fixed file in the drop: copied from src under id `key`, replacing an earlier pin of
        the same id; it never expires, is never evicted, and guests cannot remove it (the
        admin can). install.sh pins the box's own source this way (the AGPL's offer)."""
        if not ID_RE.match(key):
            raise ValueError("bad id")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = confine.under(self.dir, key + ".tmp")
        shutil.copyfile(src, tmp)
        meta = {"id": key, "name": clean_filename(name), "size": tmp.stat().st_size, "by": str(by)[:MAX_NAME],
                "created": self.clock.ticks(), "pinned": True}
        with self._lock:
            os.replace(tmp, confine.under(self.dir, key + ".data"))
            (confine.under(self.dir, key + ".meta")).write_text(json.dumps(meta))
        return meta

    def list(self):
        with self._lock:
            metas = self._prune()
        now = self.clock.ticks()
        return [dict(m, age=now - m.get("created", now)) for m in sorted(metas, key=lambda m: -m.get("created", 0))]

    def usage(self):
        metas = self._metas()
        return {"files": len(metas), "bytes": sum(m.get("size", 0) for m in metas),
                "max_total": DROP_MAX_TOTAL, "max_file": DROP_MAX_FILE, "ttl": DROP_TTL}

    def receive(self, stream, length, name, by="", lock=None):
        """Stream `length` bytes from `stream` into a new drop. Returns its meta; raises
        ValueError (too large, card too full) or OSError (the body stopped short)."""
        if length > DROP_MAX_FILE:
            raise ValueError("too large")
        self.dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.dir).free < length + DROP_MIN_FREE:
            raise ValueError("the card is too full")
        key = secrets.token_urlsafe(12)
        tmp = confine.under(self.dir, key + ".tmp")
        try:
            with open(tmp, "wb") as fh:
                left = length
                while left:
                    chunk = stream.read(min(DROP_CHUNK, left))
                    if not chunk:
                        raise OSError("the upload stopped part-way")
                    fh.write(chunk)
                    left -= len(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            meta = {"id": key, "name": clean_filename(name), "size": length,
                    "by": str(by).strip()[:MAX_NAME], "created": self.clock.ticks()}
            if lock:
                meta["lock"] = lock
            with self._lock:
                os.replace(tmp, confine.under(self.dir, key + ".data"))
                (confine.under(self.dir, key + ".meta")).write_text(json.dumps(meta))
                self._prune(keep=key)
            return meta
        finally:
            tmp.unlink(missing_ok=True)

    def open(self, key):
        """(meta, open file) or None."""
        if not ID_RE.match(key):
            return None
        try:
            meta = json.loads((confine.under(self.dir, key + ".meta")).read_text())
            return meta, open(confine.under(self.dir, key + ".data"), "rb")
        except (OSError, ValueError):
            return None

    def delete(self, key, proof=None, force=False):
        """Remove a file: the admin (force), or a guest with the proof for a locked file.
        Nobody else: a file nobody locked stays until it expires or the admin removes it."""
        if ID_RE.match(key) is None:
            return False
        with self._lock:
            try:
                meta = json.loads((confine.under(self.dir, key + ".meta")).read_text())
            except (OSError, ValueError):
                return False
            if not force:
                if meta.get("pinned"):
                    raise LockError(None, pinned=True)
                if not meta.get("lock") or advance_lock(meta["lock"], proof) is None:
                    raise LockError(meta.get("lock"))
            return self._remove(key)


def clean_filename(name):
    """A name to show and to offer when saving: no path, no control characters."""
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable()).strip().lstrip(".")
    return name[:MAX_FILENAME] or "file"


def content_disposition(name):
    """attachment, with an ASCII fallback and the real name per RFC 6266 / 5987."""
    from urllib.parse import quote
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\%' else "_" for c in name)
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name, safe="")}'


def _handle_drop(handler, method, path, drop):
    from urllib.parse import unquote
    if path == "/api/drop" and method == "GET":
        _send_json(handler, 200, {"files": [{**{k: m.get(k) for k in ("id", "name", "size", "by", "age")}, "locked": bool(m.get("lock")),
                                             "pinned": bool(m.get("pinned")),
                                             **({"lock_n": m["lock"]["n"]} if m.get("lock") else {})}
                                            for m in drop.list()],
                                  "max_file": DROP_MAX_FILE, "max_total": DROP_MAX_TOTAL, "ttl": DROP_TTL})
        return True
    if path == "/api/drop" and method == "POST":
        if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
            _send_json(handler, 411, {"error": "send the file with a Content-Length"})
            return True
        try:
            length = int(handler.headers.get("Content-Length", ""))
        except ValueError:
            _send_json(handler, 411, {"error": "send the file with a Content-Length"})
            return True
        if length <= 0:
            _send_json(handler, 400, {"error": "empty file"})
            return True
        if length > DROP_MAX_FILE:
            handler.close_connection = True  # the body is not read
            _send_json(handler, 413, {"error": f"too large: the limit is {DROP_MAX_FILE >> 20} MB"})
            return True
        name = unquote(handler.headers.get("X-Drop-Name", "") or "file")
        by = unquote(handler.headers.get("X-Drop-By", "") or "")
        try:
            meta = drop.receive(handler.rfile, length, name, by, lock=parse_lock(handler.headers.get("X-Lock-New")))
        except ValueError as exc:
            handler.close_connection = True
            _send_json(handler, 507 if "full" in str(exc) else 413, {"error": str(exc)})
            return True
        except OSError:
            handler.close_connection = True
            _send_json(handler, 400, {"error": "the upload stopped part-way"})
            return True
        _send_json(handler, 201, {**{k: meta[k] for k in ("id", "name", "size")}, "locked": bool(meta.get("lock"))})
        return True
    if path.startswith("/api/drop/") and method == "DELETE":
        key = path[len("/api/drop/"):]
        try:
            gone = drop.delete(key, proof=handler.headers.get("X-Lock"))
        except LockError as exc:
            if exc.pinned:
                _send_json(handler, 403, {"error": str(exc), "pinned": True})
            elif exc.lock is None:
                _send_json(handler, 403, {"error": "only the admin can remove a file nobody locked; it goes when it expires",
                                          "locked": False})
            else:
                _send_locked(handler, exc)
            return True
        _send_json(handler, 200, {"id": key}) if gone else _send_json(handler, 404, {"error": "not found (it may have expired)"})
        return True
    if path.startswith("/api/drop/") and method in ("GET", "HEAD"):
        found = drop.open(path[len("/api/drop/"):])
        if not found:
            _send_json(handler, 404, {"error": "not found (it may have expired)"})
            return True
        meta, fh = found
        with fh:
            size = os.fstat(fh.fileno()).st_size
            handler.send_response(200)
            # Whatever the name says, the browser gets bytes to save, never a page to run.
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("Content-Length", str(size))
            handler.send_header("Content-Disposition", content_disposition(meta.get("name", "file")))
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            handler.end_headers()
            if method == "GET":
                shutil.copyfileobj(fh, handler.wfile, DROP_CHUNK)
        return True
    _send_json(handler, 405, {"error": "method not allowed"})
    return True


# ---------------------------------------------------------------------------
# HTTP glue. Kept as a single `handle()` so server.py can delegate in one line
# and this module stays runnable on its own.
# ---------------------------------------------------------------------------


def _send(handler, code, body=b"", ctype="application/octet-stream", extra=None):
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    # No CORS on the box (F20): everything that uses the store is on the hub's own origin, and
    # a wildcard let any website a guest visited read the drop and saves and change saves.
    # For development with Excalidraw served elsewhere, HUB_STORE_CORS names that one origin.
    if DEV_CORS:
        handler.send_header("Access-Control-Allow-Origin", DEV_CORS)
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "*")
    for key, value in (extra or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    if body and handler.command != "HEAD":
        handler.wfile.write(body)


def _send_json(handler, code, data, extra=None):
    _send(handler, code, json.dumps(data).encode(), "application/json", extra)


def _lock_headers(handler):
    """(proof, new lock, next lock) from the request: see "locks" above."""
    h = handler.headers
    return h.get("X-Lock"), parse_lock(h.get("X-Lock-New")), parse_lock(h.get("X-Lock-Next"))


def _send_locked(handler, exc):
    lock = exc.lock or {}
    _send_json(handler, 403, {"error": str(exc), "locked": True, **({"lock_n": lock["n"]} if "n" in lock else {})})


def _read_body(handler):
    """Raw bytes. Returns None if the body is missing or over the limit.

    The Excalidraw frontend sends no Content-Type, which is why upstream parses the
    body raw regardless of type; we do the same rather than guessing.
    """
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except ValueError:
        return None
    if length <= 0 or length > MAX_BODY:
        return None
    return handler.rfile.read(length)


def _blob_get(handler, store, namespace, key):
    data = store.get(namespace, key)
    if data is None:
        _send_json(handler, 404, {"error": "not found"})
        return
    tag = _etag(data)
    if handler.headers.get("If-None-Match") == tag:
        # Polling clients live here: no body, no disk read on the wire.
        _send(handler, 304, b"", "application/octet-stream", {"ETag": tag})
        return
    _send(handler, 200, data, "application/octet-stream", {"ETag": tag})


def handle(handler, method, path, store, drop=None):
    """Route one request. Returns True if this module owned it.

    server.py calls this before its own routing; anything not matching a prefix here
    falls through to the landing page, shoutbox and board untouched.
    """
    if method == "OPTIONS" and (path.startswith(GLOBAL_PREFIX) or path.startswith("/api/saves")):
        _send(handler, 204)
        return True

    if path.startswith(GLOBAL_PREFIX + "/"):
        return _handle_blob(handler, method, path[len(GLOBAL_PREFIX) + 1:], store)

    if path == "/api/saves" or path.startswith("/api/saves/"):
        return _handle_saves(handler, method, path, store)

    if drop is not None and (path == "/api/drop" or path.startswith("/api/drop/")):
        return _handle_drop(handler, method, path, drop)

    return False


def _handle_blob(handler, method, rest, store):
    parts = rest.split("/")
    namespace = parts[0]
    if namespace not in BLOB_NAMESPACES:
        return False

    key = parts[1] if len(parts) > 1 and parts[1] else None

    if key is not None and not ID_RE.match(key):
        _send_json(handler, 400, {"error": "bad id"})
        return True

    if method == "GET" and key:
        _blob_get(handler, store, namespace, key)
        return True

    # Upstream: POST /scenes mints an id, PUT /rooms/:id and /files/:id take one.
    if method == "POST" and namespace == "scenes" and key is None:
        data = _read_body(handler)
        if data is None:
            _send_json(handler, 413, {"error": "missing or oversized body"})
            return True
        _send_json(handler, 201, {"id": store.create_scene(data)})
        return True

    if method == "PUT" and key and namespace in ("rooms", "files"):
        data = _read_body(handler)
        if data is None:
            _send_json(handler, 413, {"error": "missing or oversized body"})
            return True
        store.put(namespace, key, data)
        _send_json(handler, 200, {"id": key})
        return True

    _send_json(handler, 405, {"error": "method not allowed"})
    return True


def _with_state(store, meta):
    """Metadata plus the stored document, decoded if it is JSON."""
    out = dict(meta)
    data = store.get("saves", meta["id"] + ".data")
    if data is None:
        out["state"] = None
        return out
    try:
        out["state"] = json.loads(data)
    except ValueError:
        out["state"] = data.decode("utf-8", "replace")
    return out


def _handle_saves(handler, method, path, store):
    if path == "/api/saves":
        if method == "GET":
            saves = store.list_saves()
            if "full=1" in (handler.path.split("?", 1)[1] if "?" in handler.path else ""):
                # Opt-in: the listing normally stays metadata-only so a gallery costs
                # one small read per entry. Text documents are cheap enough to inline,
                # which saves the client an N+1 round trip on a hotspot.
                saves = [_with_state(store, meta) for meta in saves]
            _send_json(handler, 200, {
                "now": store.clock.ticks(),
                "ttl": SAVE_TTL,
                "saves": [public_meta(m) for m in saves],
            })
            return True
        if method == "POST":
            _create_save(handler, store)
            return True
        _send_json(handler, 405, {"error": "method not allowed"})
        return True

    rest = path[len("/api/saves/"):].split("/")
    key = rest[0]
    if not ID_RE.match(key):
        _send_json(handler, 400, {"error": "bad id"})
        return True

    wants_thumb = len(rest) > 1 and rest[1] == "thumb"

    if method == "GET" and wants_thumb:
        thumb = store.get_thumb(key)
        if thumb is None:
            _send_json(handler, 404, {"error": "not found"})
        else:
            # Thumbnails are whatever the browser uploaded. Served as an opaque
            # download rather than inline: everything on this hub shares one origin,
            # so a stored SVG rendered in place would run script against the shoutbox.
            _send(handler, 200, thumb, "application/octet-stream",
                  {"X-Content-Type-Options": "nosniff",
                   "Content-Disposition": "attachment"})
        return True

    if method == "GET":
        meta = store._read_meta(confine.under(store._dir("saves"), key + ".meta"))
        if meta is None:
            _send_json(handler, 404, {"error": "not found"})
            return True
        payload = public_meta(_with_state(store, meta))
        payload["now"] = store.clock.ticks()
        _send_json(handler, 200, payload)
        return True

    if method == "PATCH":
        raw = _read_body(handler)
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        name = str(body.get("name", "")).strip()[:MAX_NAME]
        unlock = body.get("unlock") is True
        proof, _, nxt = _lock_headers(handler)
        if not name and not unlock and not nxt:
            _send_json(handler, 400, {"error": "name required"})
            return True
        try:
            meta = store.rename_save(key, name, proof=proof, lock_next=nxt, unlock=unlock)
        except LockError as exc:
            _send_locked(handler, exc)
            return True
        _send_json(handler, 200, public_meta(meta)) if meta else _send_json(handler, 404, {"error": "not found"})
        return True

    if method == "DELETE":
        try:
            gone = store.delete_save(key, proof=_lock_headers(handler)[0])
        except LockError as exc:
            _send_locked(handler, exc)
            return True
        if gone:
            _send_json(handler, 200, {"id": key})
        else:
            _send_json(handler, 404, {"error": "not found"})
        return True

    _send_json(handler, 405, {"error": "method not allowed"})
    return True


def _create_save(handler, store):
    raw = _read_body(handler)
    if raw is None:
        _send_json(handler, 413, {"error": "missing or oversized body"})
        return
    try:
        payload = json.loads(raw)
    except ValueError:
        _send_json(handler, 400, {"error": "bad json"})
        return
    if not isinstance(payload, dict):
        _send_json(handler, 400, {"error": "bad json"})
        return

    state = payload.get("state")
    if state is None:
        _send_json(handler, 400, {"error": "state required"})
        return

    kind = str(payload.get("kind", "unknown")).strip()[:MAX_KIND] or "unknown"
    name = str(payload.get("name", "")).strip()[:MAX_NAME] or "untitled"

    key = payload.get("id")
    if key is not None:
        key = str(key)
        if not ID_RE.match(key):
            _send_json(handler, 400, {"error": "bad id"})
            return

    thumb = None
    if payload.get("thumb"):
        # Data URL or bare base64, whichever the client finds easier to produce.
        import base64
        blob = str(payload["thumb"])
        if "," in blob and blob.startswith("data:"):
            blob = blob.split(",", 1)[1]
        try:
            thumb = base64.b64decode(blob, validate=True)
        except (ValueError, TypeError):
            thumb = None

    body = json.dumps(state).encode()
    proof, new, nxt = _lock_headers(handler)
    try:
        meta = store.save(kind, name, body, thumb, key, proof=proof, lock_new=new, lock_next=nxt)
    except LockError as exc:
        _send_locked(handler, exc)
        return
    _send_json(handler, 201, public_meta(meta))


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 5 and sys.argv[1] == "pin-drop":
        # store.py pin-drop STATE_DIR FILE NAME: install.sh, as the hub user, pinning the
        # box's own source into the file drop.
        state = Path(sys.argv[2])
        meta = Drop(state / "drop", hubclock.HubClock(state / "clock.json")).pin(sys.argv[3], "irate-box-source", sys.argv[4],
                                                                              by="this box")
        print(f"pinned {meta['name']} ({meta['size']} bytes) in the file drop")
        sys.exit(0)
    from http.server import BaseHTTPRequestHandler, HTTPServer

    STATE = Path(os.environ.get("HUB_STORE_DIR", Path(__file__).resolve().parents[2] / "store-state"))
    CLOCK = hubclock.HubClock(STATE / "clock.json")
    STORE = Store(STATE / "store", CLOCK)
    PORT = int(os.environ.get("PORT", 8090))

    class StoreHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _dispatch(self, method):
            path = self.path.split("?")[0]
            if not handle(self, method, path, STORE):
                _send_json(self, 404, {"error": "not found"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_OPTIONS(self):
            self._dispatch("OPTIONS")

    CLOCK.start()
    print(f"Store running at http://0.0.0.0:{PORT}{GLOBAL_PREFIX} and /api/saves")
    print(f"State in {STATE}")
    try:
        HTTPServer(("0.0.0.0", PORT), StoreHandler).serve_forever()
    finally:
        CLOCK.stop()
