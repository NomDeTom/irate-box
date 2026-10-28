# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
r"""The App store's keeper: the librarian's hold on chosen files from a project's releases, a set number of
each channel, for the hub to list.

What it keeps, under $HUB_HOLDS_ROOT (/var/lib/hub/holds), one folder to a holder:

  <id>/<tag>/<file>        a held file, checked against the SHA-256 GitHub gave for it
  <id>/<tag>/sha256.json   {name: [size, mtime_ns, sha256]}, as firmware.record_digests keeps them
  <id>/<tag>/refused.json  files that did not match their digest, so a bad one is not fetched at every run
  <id>/index.json          what the hub lists: the kept versions, newest first, each with its files
  <id>/status.json         when it was checked, what it said, what went wrong
  status.json              the last run over all the holders (what `due` reads)

Settings ($HUB_STATE_DIR/library/holders.json):
  enabled      off by default
  holders      a list of {id, title, provider, repo, holds, channels, extra_channels, tag_pattern,
               min_age_days, budget_mb, visibility}:
    provider        "github" (Forgejo and GitLab are to come, each an adapter beside GitHub's)
    holds           [{label, include, exclude?}]: a file is held when a hold's `include` matches its name (found
                    anywhere in it, as Obtainium's apkFilterRegEx is) and its `exclude`, if any, does not.
                    Patterns are Python's, with Dart's named groups and \k<name> translated (translate_regex)
    channels        {"stable": 2, "pre-release": 0}: how many of each to keep, 0 to 5
    extra_channels  [{name, pattern, keep}]: a release whose tag or title matches `pattern` is of that channel
                    instead (Meshtastic's alphas and betas, which GitHub flags alike)
    tag_pattern     a tag must match it to be a version at all, which leaves out a rolling tag such as
                    `snapshot` (default: starts with digits, with or without a `v`)
    min_age_days    a release newer than this does not count yet
    budget_mb       all of the holder's files at most this (0: only the librarian's min_free_mb); over it,
                    the oldest version goes first, but never the newest of a channel
    visibility      "public" or "members": who the hub shows the files to

A release counts only if it has a held file, so a release that lacks them (Meshtastic's v2.7.13 against a pattern for
v2.7.14's names) is passed over and an older one takes its place. Drafts and releases titled "Revoked" never count.
Versions are ordered by their numbers where the tags share a standard shape (releasever.py), else by date.

Runs inside the librarian (its lock, schedule and progress): librarian.py holders [--check] [ID ...].
Stdlib only.
"""

import hashlib
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from irate_box.library import librarian, releasever
from irate_box.library.librarian import LibrarianError
from irate_box.msg import M

ROOT = Path(os.environ.get("HUB_HOLDS_ROOT", librarian.STATE_DIR / "holds"))
SETTINGS = librarian.LIB_DIR / "holders.json"
STATUS = ROOT / "status.json"
DEFAULTS = {"enabled": False, "holders": []}
HOLDER_DEFAULTS = {
    "title": "",
    "provider": "github",
    "holds": [],
    "channels": {"stable": 2, "pre-release": 0},
    "extra_channels": [],
    "tag_pattern": r"^v?\d+(\.\d+)+",
    "min_age_days": 0,
    "budget_mb": 0,
    "visibility": "public",
}
PROVIDERS = ("github",)
VISIBILITIES = ("public", "members")
KEEP_MAX = 5
MAX_HOLDERS = 20
MAX_HOLDS = 20
MAX_EXTRA_CHANNELS = 4
MAX_PATTERN = 200
MAX_NOTES = 4000
RELEASE_PAGES = 3  # GitHub's 100 releases a page: enough to find a few of each channel
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")  # a tag that can be a folder name
FILE_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,128}$")
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
CHANNEL_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
DIGESTS = "sha256.json"
REFUSED = "refused.json"  # {name: [sha256 GitHub gave, when it was refused]}: not fetched again until the digest changes or a day passes
REFUSED_RETRY_SECONDS = 24 * 3600
NETWORK_FAILURES = ("library.download.unreachable", "library.download.cut-short")


# --- patterns ------------------------------------------------------------------------------


def translate_regex(pattern):
    r"""A pattern as Python's `re` reads it, from one written for Dart (an Obtainium recipe): `(?<name>…)` becomes
    `(?P<name>…)` and `\k<name>` becomes `(?P=name)`. Refused, in words: one too long, one with \p{…} classes
    (Python's `re` has none), or one that does not compile. Matching is on file names of 128 characters at most, which
    bounds what a pattern can cost."""
    if not isinstance(pattern, str) or not pattern:
        raise LibrarianError(M("library.holder.pattern-empty", "Enter a pattern."))
    if len(pattern) > MAX_PATTERN:
        raise LibrarianError(
            M("library.holder.pattern-long", "The pattern is longer than {max} characters.", max=MAX_PATTERN)
        )
    if re.search(r"(?<!\\)\\[pP]\{", pattern):
        raise LibrarianError(
            M(
                "library.holder.pattern-classes",
                "The pattern uses a \\p{…} class for letters or digits. The box does not read those. "
                "Use [A-Za-z] or [0-9] instead.",
            )
        )
    translated = re.sub(r"(?<!\\)\(\?<(?![=!])([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)
    translated = re.sub(r"(?<!\\)\\k<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P=\1)", translated)
    try:
        re.compile(translated, re.ASCII)
    except re.error as exc:
        raise LibrarianError(
            M("library.holder.pattern-invalid", "The pattern is not valid: {error}.", error=str(exc)[:120])
        ) from exc
    return translated


def _compile(pattern):
    return re.compile(translate_regex(pattern), re.ASCII)


# --- settings ------------------------------------------------------------------------------


def _count(value, what, maximum=KEEP_MAX):
    if type(value) is not int or not 0 <= value <= maximum:
        raise LibrarianError(
            M(
                "library.holder.count-invalid",
                "Invalid setting. {what} must be from 0 to {max}.",
                what=what,
                max=maximum,
            )
        )
    return value


def validate_holder(raw):
    """A holder as the settings keep it, from what the admin sent; LibrarianError says what is wrong."""
    if not isinstance(raw, dict):
        raise LibrarianError(M("library.holder.invalid", "Invalid holder. It must be an object."))
    holder = {**HOLDER_DEFAULTS, **{key: raw[key] for key in HOLDER_DEFAULTS if key in raw}}
    holder["id"] = raw.get("id")
    if not isinstance(holder["id"], str) or not librarian.NAME_RE.match(holder["id"]):
        raise LibrarianError(
            M(
                "library.holder.id-invalid",
                "Invalid name. Use letters, digits, dots, hyphens and underscores, starting with a letter or digit.",
            )
        )
    if holder["provider"] not in PROVIDERS:
        raise LibrarianError(
            M(
                "library.holder.provider-invalid",
                "Invalid source. Use one of these: {providers}.",
                providers=list(PROVIDERS),
            )
        )
    repo = raw.get("repo")
    if not isinstance(repo, str) or not librarian.REPO_RE.match(repo):
        raise LibrarianError(
            M(
                "library.holder.repo-invalid",
                "Invalid repository. Enter it as account/repository, for example meshtastic/Meshtastic-Android.",
            )
        )
    holder["repo"] = repo
    if not isinstance(holder["title"], str) or len(holder["title"]) > 80:
        raise LibrarianError(M("library.holder.title-invalid", "The title must be text of at most 80 characters."))
    if holder["visibility"] not in VISIBILITIES:
        raise LibrarianError(
            M(
                "library.holder.visibility-invalid",
                "Invalid setting. Use one of these: {values}.",
                values=list(VISIBILITIES),
            )
        )
    holds = holder["holds"]
    if not isinstance(holds, list) or len(holds) > MAX_HOLDS:
        raise LibrarianError(M("library.holder.holds-invalid", "Invalid list of what to hold."))
    cleaned = []
    for hold in holds:
        if not isinstance(hold, dict) or not isinstance(hold.get("label", ""), str) or len(hold.get("label", "")) > 80:
            raise LibrarianError(M("library.holder.holds-invalid", "Invalid list of what to hold."))
        entry = {"label": hold.get("label", ""), "include": hold.get("include")}
        _compile(entry["include"])
        if hold.get("exclude"):
            _compile(hold["exclude"])
            entry["exclude"] = hold["exclude"]
        cleaned.append(entry)
    holder["holds"] = cleaned
    channels = holder["channels"]
    if not isinstance(channels, dict) or set(channels) - {"stable", "pre-release"}:
        raise LibrarianError(
            M("library.holder.channels-invalid", "Invalid setting. Channels are stable and pre-release.")
        )
    holder["channels"] = {
        name: _count(channels.get(name, HOLDER_DEFAULTS["channels"][name]), name) for name in ("stable", "pre-release")
    }
    extras = holder["extra_channels"]
    if not isinstance(extras, list) or len(extras) > MAX_EXTRA_CHANNELS:
        raise LibrarianError(M("library.holder.extras-invalid", "Invalid list of other channels."))
    seen = {"stable", "pre-release"}
    cleaned_extras = []
    for extra in extras:
        if not isinstance(extra, dict) or not isinstance(extra.get("name"), str) or not CHANNEL_RE.match(extra["name"]):
            raise LibrarianError(M("library.holder.extras-invalid", "Invalid list of other channels."))
        if extra["name"] in seen:
            raise LibrarianError(M("library.holder.extras-repeated", "Each channel needs a name of its own."))
        seen.add(extra["name"])
        _compile(extra.get("pattern"))
        cleaned_extras.append(
            {"name": extra["name"], "pattern": extra["pattern"], "keep": _count(extra.get("keep", 0), extra["name"])}
        )
    holder["extra_channels"] = cleaned_extras
    _compile(holder["tag_pattern"])
    holder["min_age_days"] = _count(holder["min_age_days"], "min_age_days", 365)
    holder["budget_mb"] = _count(holder["budget_mb"], "budget_mb", 10**7)
    return holder


def settings():
    stored = librarian._read_json(SETTINGS, {})
    cfg = dict(DEFAULTS)
    cfg["enabled"] = bool(stored.get("enabled", False))
    holders = []
    for raw in stored.get("holders", []) if isinstance(stored.get("holders"), list) else []:
        try:
            holders.append(validate_holder(raw))
        except LibrarianError:
            continue  # a hand-edited entry that no longer validates is left out, not a reason to stop the rest
    cfg["holders"] = holders
    return cfg


def set_settings(**changes):
    """Save `enabled` and/or the whole `holders` list (each validated; names unique)."""
    cfg = settings()
    if "enabled" in changes:
        cfg["enabled"] = bool(changes["enabled"])
    if "holders" in changes:
        raw = changes["holders"]
        if not isinstance(raw, list) or len(raw) > MAX_HOLDERS:
            raise LibrarianError(M("library.holder.list-invalid", "Invalid list of holders."))
        holders = [validate_holder(item) for item in raw]
        names = [holder["id"] for holder in holders]
        if len(set(names)) != len(names):
            raise LibrarianError(M("library.holder.id-repeated", "Each holder needs a name of its own."))
        cfg["holders"] = holders
    librarian._write_json(SETTINGS, cfg)
    return cfg


def remove_files(holder_id):
    """The files of a holder, for when the admin removes it or asks for the files gone."""
    if not isinstance(holder_id, str) or not librarian.NAME_RE.match(holder_id):
        return
    shutil.rmtree(ROOT / holder_id, ignore_errors=True)


# --- the releases --------------------------------------------------------------------------


def _normalise(raw):
    """One GitHub release in the shape the rest of this module reads (and Forgejo's and GitLab's adapters will
    produce): {tag, name, published, prerelease, draft, notes, assets: [{name, size, url, digest}]}."""
    return {
        "tag": str(raw.get("tag_name") or ""),
        "name": raw.get("name") or "",
        "published": raw.get("published_at") or raw.get("created_at"),
        "prerelease": bool(raw.get("prerelease")),
        "draft": bool(raw.get("draft")),
        "notes": (raw.get("body") or "")[:MAX_NOTES],
        "assets": [
            {
                "name": asset.get("name") or "",
                "size": int(asset.get("size") or 0),
                "url": asset.get("browser_download_url") or "",
                "digest": asset.get("digest"),
            }
            for asset in raw.get("assets", [])
        ],
    }


def fetch_releases(repo):
    """The newest releases of a GitHub repository, normalised, newest first as GitHub lists them."""
    out = []
    for page in range(1, RELEASE_PAGES + 1):
        batch = librarian._api(f"/repos/{repo}/releases?per_page=100&page={page}", librarian.token())
        if not isinstance(batch, list):
            break
        out.extend(_normalise(raw) for raw in batch if isinstance(raw, dict))
        if len(batch) < 100:
            break
    return out


# --- what to keep --------------------------------------------------------------------------


def channel_of(release, holder):
    """The channel a release counts in, or None when it does not count at all: a draft, a revoked release, a tag
    that is not a version (`snapshot`)."""
    if release["draft"] or librarian.revoked({"name": release["name"]}):
        return None
    if not _compile(holder["tag_pattern"]).search(release["tag"]):
        return None
    text = f"{release['tag']}\n{release['name']}"
    for extra in holder["extra_channels"]:
        if _compile(extra["pattern"]).search(text):
            return extra["name"]
    return "pre-release" if release["prerelease"] else "stable"


def keep_for(holder, channel):
    if channel in holder["channels"]:
        return holder["channels"][channel]
    return next((extra["keep"] for extra in holder["extra_channels"] if extra["name"] == channel), 0)


def held_files(release, holder):
    """The assets of a release the holds name: some hold's `include` matches the name and its `exclude` does not."""
    holds = [
        (_compile(hold["include"]), _compile(hold["exclude"]) if hold.get("exclude") else None)
        for hold in holder["holds"]
    ]
    return [
        asset
        for asset in release["assets"]
        if FILE_RE.match(asset["name"])
        and any(
            include.search(asset["name"]) and not (exclude and exclude.search(asset["name"]))
            for include, exclude in holds
        )
    ]


def _age_days(published, now):
    try:
        then = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (now - then).total_seconds() / 86400


def plan(holder, releases, now=None):
    """The versions to keep, newest first: [{tag, channel, published, notes, files}]. Each channel keeps its own
    number of the newest releases that count (channel_of), are old enough, have a folder-safe tag and hold at least
    one file; one that does not is passed over and an older one takes its place. Then the budget."""
    now = now or datetime.now(timezone.utc)
    ordered = releasever.newest_first(releases, lambda release: release["tag"], lambda release: release["published"])
    kept, counts = [], {}
    for release in ordered:
        channel = channel_of(release, holder)
        if channel is None or not TAG_RE.match(release["tag"]):
            continue
        if counts.get(channel, 0) >= keep_for(holder, channel):
            continue
        if holder["min_age_days"]:
            age = _age_days(release["published"], now)
            if age is None or age < holder["min_age_days"]:
                continue
        files = held_files(release, holder)
        if not files:
            continue
        counts[channel] = counts.get(channel, 0) + 1
        kept.append(
            {
                "tag": release["tag"],
                "channel": channel,
                "published": release["published"],
                "notes": release["notes"],
                "files": files,
            }
        )
    kept.sort(key=lambda entry: entry["published"] or "", reverse=True)
    return _within_budget(holder, kept)


def _within_budget(holder, kept):
    """The oldest version out first until the rest fits the holder's budget; the newest of each channel stays."""
    budget = holder["budget_mb"] << 20
    if not budget:
        return kept
    newest = {}
    for entry in kept:
        newest.setdefault(entry["channel"], entry["tag"])
    kept = list(kept)
    while sum(file["size"] for entry in kept for file in entry["files"]) > budget:
        victim = next((entry for entry in reversed(kept) if newest[entry["channel"]] != entry["tag"]), None)
        if victim is None:
            break
        kept.remove(victim)
    return kept


# --- fetching ------------------------------------------------------------------------------


def _publish(path, data):
    """JSON the web server reads: world-readable, unlike the librarian's own files."""
    librarian._write_json(path, data)
    os.chmod(path, 0o644)


def _sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mirror_version(holder, entry, policy, log):
    """One version's held files into <id>/<tag>/, each checked against GitHub's digest; returns the files that
    are there: [{name, size, sha256}]. A file with no digest, or one that does not match, is left out and said.
    Running out of room, or the network failing, stops the run (LibrarianError)."""
    folder = ROOT / holder["id"] / entry["tag"]
    folder.mkdir(parents=True, exist_ok=True)
    recorded = librarian._read_json(folder / DIGESTS, {})
    refused = librarian._read_json(folder / REFUSED, {})
    names = {asset["name"] for asset in entry["files"]}
    for leftover in folder.iterdir():
        if leftover.name in (DIGESTS, REFUSED) or (
            leftover.name in names and leftover.is_file() and not leftover.is_symlink()
        ):
            continue
        if leftover.is_dir() and not leftover.is_symlink():
            shutil.rmtree(leftover, ignore_errors=True)
        else:
            leftover.unlink()
    have = {}
    for asset in entry["files"]:
        name = asset["name"]
        match = DIGEST_RE.match(asset["digest"] or "")
        if not match:
            log(f"holder {holder['id']} {entry['tag']}: {name} left out: GitHub gave no SHA-256 for it")
            continue
        expected = match.group(1)
        path = folder / name
        note = recorded.get(name)
        stat = path.stat() if path.is_file() and not path.is_symlink() else None
        if stat and note == [stat.st_size, stat.st_mtime_ns, expected] and stat.st_size == asset["size"]:
            have[name] = {"name": name, "size": stat.st_size, "sha256": expected}
            continue
        turned_away = refused.get(name)
        if turned_away and turned_away[0] == expected and time.time() - turned_away[1] < REFUSED_RETRY_SECONDS:
            continue
        if librarian._free_bytes(ROOT) < asset["size"] + policy["min_free_mb"] * 2**20:
            raise LibrarianError(
                M(
                    "library.holder.no-space",
                    "Insufficient storage for {name}. The box keeps {reserve} MB free.",
                    name=name,
                    reserve=policy["min_free_mb"],
                )
            )
        partial = path.with_name(path.name + ".part")
        try:
            librarian._download(
                asset["url"], partial, name=f"{holder['id']} {entry['tag']}: {name}", expected=asset["size"]
            )
            librarian._verify_sha256(partial, expected, name)
        except LibrarianError as exc:
            partial.unlink(missing_ok=True)
            if isinstance(exc.message, dict) and exc.message.get("id") in NETWORK_FAILURES:
                raise
            log(f"holder {holder['id']} {entry['tag']}: {name} left out: {exc}")
            refused[name] = [expected, int(time.time())]
            continue
        refused.pop(name, None)
        os.replace(partial, path)
        stat = path.stat()
        recorded[name] = [stat.st_size, stat.st_mtime_ns, expected]
        have[name] = {"name": name, "size": stat.st_size, "sha256": expected}
    recorded = {name: note for name, note in recorded.items() if name in have}
    _publish(folder / DIGESTS, recorded)
    refused = {name: note for name, note in refused.items() if name in names and name not in have}
    if refused:
        librarian._write_json(folder / REFUSED, refused)
    else:
        (folder / REFUSED).unlink(missing_ok=True)
    return list(have.values())


def holder_status(holder_id):
    return librarian._read_json(ROOT / holder_id / "status.json", {})


def disk_uses(holder_id):
    """What this holder's folder really holds, as the admin page counts it: its published versions, each with
    the files still on disk (bytes from what the keeper recorded), and the total. A file published but gone
    (a card taken out mid-run) shows in no size: the next run puts it back or drops the version."""
    index = librarian._read_json(ROOT / holder_id / "index.json", {})
    versions, total = [], 0
    for version in index.get("versions", []) if isinstance(index, dict) else []:
        if not TAG_RE.match(str(version.get("tag", ""))):
            continue
        files = []
        for file in version.get("files", []):
            if not FILE_RE.match(str(file.get("name", ""))):
                continue
            path = ROOT / holder_id / version["tag"] / file["name"]
            if path.is_file() and not path.is_symlink():
                files.append(file)
        bytes_ = sum(file["size"] for file in files)
        total += bytes_
        versions.append(
            {
                "tag": version["tag"],
                "channel": version.get("channel", ""),
                "published": version.get("published"),
                "files": files,
                "bytes": bytes_,
            }
        )
    return {"versions": versions, "bytes": total}


def save_survey(holder_id, data):
    """A survey the administrator asked for (holdsurvey.py), kept in the holder's status so the page can
    show it again and the doctor can say when a holder was never looked at."""
    state = holder_status(holder_id)
    state["survey"] = data
    state["surveyed"] = librarian.now_iso()
    state.pop("survey_error", None)
    _save_holder_status(holder_id, state)
    return state


def patch_status(holder_id, changes):
    """A few keys of a holder's status, changed in place (the survey's error, say), for the admin page to read."""
    state = holder_status(holder_id)
    state.update(changes)
    _save_holder_status(holder_id, state)
    return state


def _save_holder_status(holder_id, state):
    (ROOT / holder_id).mkdir(parents=True, exist_ok=True)
    _publish(ROOT / holder_id / "status.json", state)


def sync_holder(holder, check_only=False, log=print):
    """Bring one holder's folder in line with its settings. Returns a one-line outcome."""
    state = holder_status(holder["id"])
    state["last_check"] = librarian.now_iso()
    try:
        if not holder["holds"]:
            outcome = "nothing chosen to hold"
        else:
            kept = plan(holder, fetch_releases(holder["repo"]))
            state["latest"] = [{"tag": entry["tag"], "channel": entry["channel"]} for entry in kept]
            if check_only:
                outcome = "newest: " + (", ".join(f"{entry['tag']} ({entry['channel']})" for entry in kept) or "none")
            else:
                policy = librarian.load_config()["policy"]
                versions = []
                for entry in kept:
                    log(f"holder {holder['id']} {entry['tag']}: {len(entry['files'])} files")
                    files = _mirror_version(holder, entry, policy, log)
                    versions.append(
                        {**{key: entry[key] for key in ("tag", "channel", "published", "notes")}, "files": files}
                    )
                keep_tags = {entry["tag"] for entry in kept}
                for folder in (ROOT / holder["id"]).iterdir() if (ROOT / holder["id"]).is_dir() else []:
                    if folder.is_dir() and not folder.is_symlink() and folder.name not in keep_tags:
                        shutil.rmtree(folder, ignore_errors=True)
                _publish(
                    ROOT / holder["id"] / "index.json",
                    {
                        "id": holder["id"],
                        "title": holder["title"] or holder["id"],
                        "repo": holder["repo"],
                        "provider": holder["provider"],
                        "visibility": holder["visibility"],
                        "updated": librarian.now_iso(),
                        "versions": versions,
                    },
                )
                state["versions"] = {version["tag"]: version["channel"] for version in versions}
                outcome = f"{len(versions)} versions, {sum(file['size'] for version in versions for file in version['files']) >> 20} MB"
        state.pop("error", None)
    except (LibrarianError, OSError, ValueError, KeyError) as exc:
        state["error"] = str(exc)
        outcome = f"error: {exc}"
    state["outcome"] = outcome
    _save_holder_status(holder["id"], state)
    return outcome


def active():
    cfg = settings()
    return cfg["enabled"] and bool(cfg["holders"])


def sync(check_only=False, log=print, only=None):
    """Every holder (or those named), one failing never stopping the others. Returns a one-line outcome."""
    cfg = settings()
    chosen = [holder for holder in cfg["holders"] if not only or holder["id"] in only]
    outcomes = {holder["id"]: sync_holder(holder, check_only, log) for holder in chosen}
    ROOT.mkdir(parents=True, exist_ok=True)
    _publish(STATUS, {"last_check": librarian.now_iso(), "outcomes": outcomes})
    return "; ".join(f"{name}: {outcome}" for name, outcome in outcomes.items()) or "no holders"


def due(hours):
    return librarian._due(librarian._read_json(STATUS, {}), hours)


def snapshot():
    cfg = settings()
    return {
        "settings": cfg,
        "status": librarian._read_json(STATUS, {}),
        "holders": {holder["id"]: holder_status(holder["id"]) for holder in cfg["holders"]},
        "free_mb": librarian._free_bytes(ROOT) >> 20 if ROOT.exists() else None,
    }
