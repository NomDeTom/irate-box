# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
r"""Looking at a project's releases before holding anything: which kinds of file they carry, and the holds
that choose among them (the App store's "inspect" and "what to hold", holder.py being the keeper).

survey(releases) reads the newest releases of each channel, no file downloaded, and says:

  kinds        the files grouped by *shape*: the name with the version, a build counter and a commit hash
               replaced (`androidApp-fdroid-arm64-v8a-release.apk`, `syncthing_{v}_amd64.deb`). Each kind has
               its type class, size range, the releases it is in, and the names it had there
  companions   a checksum, signature or update helper that belongs to another file is attached to that file's
               kind, not offered as a kind of its own
  tags         operating system, architecture and build flavour read from the names by a table of known words;
               a part of the name that varies between kinds that are otherwise the same and is not in the table
               is that kind's `variant` (Syncthing's 386, mips64le, s390x need no table to be told apart)
  eras         kinds that never share a release, are alike and of one class: probably the same file under a new
               name (Meshtastic Android: app-fdroid-release.apk, then androidApp-fdroid-universal-release.apk)
  twins        two kinds the same size in every release they share (Obtainium's app-release.apk and
               app-fdroid-release.apk): probably the same file twice

and the holds model, which makes what is ticked and what is typed one list (holder.py's `holds`):

  kind_pattern(shape)    the pattern that matches exactly one kind, whatever its version
  held_states / with_kind / preview    what the holds take, a tick turned into a hold and back, and which of the
                         last releases a list of holds matches nothing in
  parse_recipe(text)     an Obtainium recipe as the settings of a holder

Stdlib only.
"""

import difflib
import json
import re
import urllib.parse

from irate_box.library import holder, releasever
from irate_box.library.librarian import LibrarianError
from irate_box.msg import M

WINDOW = 5  # releases of each channel to look at
CLASSES = (
    ("phone app", (".apk", ".xapk", ".apkm", ".apks")),
    ("phone bundle", (".aab",)),
    ("installer", (".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm")),
    ("portable", (".appimage", ".jar", ".snap", ".flatpak")),
    ("archive", (".zip", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".7z")),
    ("checksum or signature", (".sha256", ".sha512", ".md5", ".asc", ".sig", ".minisig", ".pem", ".sigstore.json")),
    ("companion", (".idsig", ".zsync", ".blockmap", ".sbom", ".spdx.json", ".mapping")),
    ("image", (".png", ".jpg", ".jpeg", ".gif", ".svg")),
    ("data", (".json", ".txt", ".xml", ".yml", ".yaml", ".gz", ".csv")),
)
CLASS_ORDER = [name for name, _ in CLASSES] + ["other"]
SIDECARS = (
    ".sha256.sig",
    ".sigstore.json",
    ".sha256",
    ".sha512",
    ".md5",
    ".asc",
    ".sig",
    ".minisig",
    ".idsig",
    ".zsync",
    ".blockmap",
    ".pem",
)
QUIET_CLASSES = ("checksum or signature", "companion")  # their names vary with the file they go with
CHECKSUM_LISTS = re.compile(r"(?i)^(sha\d*sums?|checksums?|sha\d+sum)(\.txt)?(\.asc|\.sig)?$")
# Words the names use, by what they mean. Longest first, so arm64-v8a is not read as arm64.
KNOWN = {
    "os": (
        "linux windows win32 win64 winarm64 macos mac osx darwin android freebsd netbsd openbsd illumos solaris"
    ).split(),
    "architecture": (
        "arm64-v8a armeabi-v7a aarch64 arm64 armv7l armv7 armv6 armhf armel armeabi arm amd64 x86_64 x64 x86 i386 "
        "i686 386 riscv64 ppc64le ppc64 s390x loong64 mips64le mips64 mipsle mips universal noarch"
    ).split(),
    "flavour": "fdroid google debug release snapshot nightly portable musl gnu standalone".split(),
}
KNOWN_RE = {
    label: re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    for label, words in KNOWN.items()
}
VERSION_IN_NAME = re.compile(
    r"v?\d+(?:\.\d+){1,3}(?:[-._+]?(?:alpha|beta|rc|pre|dev|snapshot|nightly)[-._]?\d*)?", re.IGNORECASE
)
PLACEHOLDERS = {
    "{v}": r"v?[0-9][0-9A-Za-z.+_-]*?",
    "{build}": r"[0-9]+",
    "{sha}": r"[0-9a-f]{7,40}",
}
PLACEHOLDER_SPLIT = re.compile(r"(\{v\}|\{build\}|\{sha\})")
MAX_TWINS = 10
MAX_ERAS = 10


# --- kinds ---------------------------------------------------------------------------------


def shape(name, tag):
    """A file name with what changes from release to release taken out: the release's own tag (with or without a
    `v`), its trailing commit hash where the name repeats it, any version number, and a build counter."""
    out = name
    for form in sorted({tag, tag.lstrip("vV")}, key=len, reverse=True):
        if form:
            out = out.replace(form, "{v}")
    commit = re.search(r"[0-9a-f]{7,40}$", tag)
    if commit:
        out = out.replace(commit.group(0), "{sha}")
    out = VERSION_IN_NAME.sub("{v}", out)
    out = re.sub(r"(?<=\{v\}[-_.])\d{3,}(?=[-_.])", "{build}", out)  # a counter after the version
    out = re.sub(r"(?<=[-_.])\d{6,}(?=[-_.])", "{build}", out)  # a run number, a date
    return re.sub(r"(\{v\}){2,}", "{v}", out)


def type_class(name):
    low = name.lower()
    if CHECKSUM_LISTS.match(name):
        return "checksum or signature"
    for label, extensions in CLASSES:
        if low.endswith(extensions):
            return label
    return "other"


def parent_of(name, names):
    """The file a checksum, signature or update helper belongs to (`x.apk` for `x.apk.sha256.sig`), or None."""
    current, parent = name, None
    while True:
        for suffix in SIDECARS:
            if current.endswith(suffix) and current != suffix:
                current = current[: -len(suffix)]
                break
        else:
            return parent
        if current in names:
            parent = current


def tags_of(text):
    """{label: [words]} for the known words in a name or shape."""
    found = {}
    for label, pattern in KNOWN_RE.items():
        words = sorted({match.group(1).lower() for match in pattern.finditer(text)})
        if words:
            found[label] = words
    return found


def window(releases, per_channel=WINDOW):
    """The releases to look at, newest first in each channel: {"stable": [...], "pre-release": [...]} and
    which channels came up short. Drafts, revoked releases and tags that are not versions are left out."""
    counting = {"tag_pattern": holder.HOLDER_DEFAULTS["tag_pattern"], "extra_channels": []}
    ordered = releasever.newest_first(releases, lambda release: release["tag"], lambda release: release["published"])
    chosen = {"stable": [], "pre-release": []}
    for release in ordered:
        channel = holder.channel_of(release, counting)
        if channel and len(chosen[channel]) < per_channel:
            chosen[channel].append(release)
    return chosen


def _skeletons(kinds):
    """For every kind, the part of its name that differs from a kind that is the same but for that part and is in
    a release with it: a *variant* (Syncthing's operating systems and architectures). Two such kinds that are never
    in one release together are one file renamed (see eras), and the file extension is the class's business."""
    pieces = {kind["shape"]: re.split(r"([-_.])", kind["shape"]) for kind in kinds}
    groups = {}
    for kind in kinds:
        parts = pieces[kind["shape"]]
        for position in range(0, len(parts) - 2, 2):  # the last part is the extension
            key = (tuple(parts[:position]), tuple(parts[position + 1 :]), len(parts), kind["class"], position)
            groups.setdefault(key, []).append((kind, parts[position]))
    for members in groups.values():
        for kind, word in members:
            if re.fullmatch(r"\{(v|build|sha)\}", word) or word.isdigit() or kind["class"] in QUIET_CLASSES:
                continue
            if any(other_word != word and set(other["names"]) & set(kind["names"]) for other, other_word in members):
                kind["_varying"].add(word)


def build_kinds(chosen):
    """The kinds of file in the chosen releases. See the module's docstring."""
    every = [(channel, release) for channel in chosen for release in chosen[channel]]
    order = {
        release["tag"]: index
        for index, (_, release) in enumerate(sorted(every, key=lambda item: item[1]["published"] or "", reverse=True))
    }
    kinds = {}
    for channel, release in every:
        names = {asset["name"] for asset in release["assets"]}
        for asset in release["assets"]:
            name = asset["name"]
            if not holder.FILE_RE.match(name):
                continue
            parent = parent_of(name, names)
            if parent and holder.FILE_RE.match(parent):
                host = kinds.setdefault(shape(parent, release["tag"]), _new_kind(shape(parent, release["tag"]), parent))
                host["companions"].add(shape(name, release["tag"]))
                continue
            key = shape(name, release["tag"])
            kind = kinds.setdefault(key, _new_kind(key, name))
            kind["names"][release["tag"]] = name
            kind["sizes"][release["tag"]] = asset["size"]
            kind["channels"].setdefault(channel, set()).add(release["tag"])
            kind["digests"] += bool(asset.get("digest"))
            kind["files"] += 1
            if kind["newest"] is None or order[release["tag"]] < order[kind["newest"]]:
                kind["newest"], kind["example"] = release["tag"], name
    kinds = [kind for kind in kinds.values() if kind["names"]]
    for kind in kinds:
        kind["_varying"] = set()
    _skeletons(kinds)
    for kind in kinds:
        kind["tags"] = tags_of(kind["shape"])
        known = {word for words in kind["tags"].values() for word in words}
        kind["variant"] = sorted(word for word in kind.pop("_varying") if word.lower() not in known)
        kind["releases"] = sorted(kind["names"], key=order.get)
        kind["position"] = [order[tag] for tag in kind["releases"]]
        sizes = list(kind["sizes"].values())
        kind["size_min"], kind["size_max"] = min(sizes), max(sizes)
        kind["size_newest"] = kind["sizes"][kind["newest"]]
        kind["companions"] = sorted(kind["companions"])
        kind["seen"] = {channel: len(tags) for channel, tags in kind.pop("channels").items()}
    kinds.sort(key=lambda kind: (CLASS_ORDER.index(kind["class"]), kind["shape"]))
    return kinds


def _new_kind(shape_text, example):
    return {
        "shape": shape_text,
        "example": example,
        "class": type_class(example),
        "names": {},
        "sizes": {},
        "channels": {},
        "companions": set(),
        "digests": 0,
        "files": 0,
        "newest": None,
    }


def _flavour(kind):
    return set(kind["tags"].get("flavour", [])) - {"release"}


def _architecture(kind):
    return set(kind["tags"].get("architecture", [])) - {"universal"}


def eras(kinds):
    """Pairs of kinds that are probably one file under two names: never in one release together, one wholly
    newer than the other, of one class, with the same build flavour and compatible architectures, alike in name."""
    found = []
    for older in kinds:
        for newer in kinds:
            if older is newer or older["class"] != newer["class"] or older["class"] in QUIET_CLASSES:
                continue
            if set(older["releases"]) & set(newer["releases"]) or not max(newer["position"]) < min(older["position"]):
                continue
            if _flavour(older) != _flavour(newer):
                continue
            if _architecture(older) and _architecture(newer) and _architecture(older) != _architecture(newer):
                continue
            similarity = difflib.SequenceMatcher(None, older["shape"], newer["shape"]).ratio()
            if similarity >= 0.5:
                found.append(
                    {
                        "older": older["shape"],
                        "newer": newer["shape"],
                        "similarity": round(similarity, 2),
                        "newer_in": len(newer["releases"]),
                    }
                )
    return sorted(found, key=lambda pair: (-pair["similarity"], -pair["newer_in"]))[:MAX_ERAS]


def twins(kinds):
    """Pairs of kinds the same size in every release both are in (at least two): probably one file twice."""
    found = []
    for index, first in enumerate(kinds):
        for second in kinds[index + 1 :]:
            shared = set(first["releases"]) & set(second["releases"])
            if (
                len(shared) >= 2
                and first["class"] == second["class"]
                and first["class"] not in ("checksum or signature", "companion", "data")
                and all(first["sizes"][tag] == second["sizes"][tag] for tag in shared)
                and first["size_max"] > 1 << 20
            ):
                found.append({"first": first["shape"], "second": second["shape"], "releases": len(shared)})
    return found[:MAX_TWINS]


def survey(releases, per_channel=WINDOW):
    """What the newest releases of each channel carry. JSON-serialisable."""
    chosen = window(releases, per_channel)
    kinds = build_kinds(chosen)
    dimensions = {}
    for kind in kinds:
        for label, words in kind["tags"].items():
            dimensions.setdefault(label, set()).update(words)
    return {
        "window": {channel: [release["tag"] for release in chosen[channel]] for channel in chosen},
        "short": [channel for channel in chosen if len(chosen[channel]) < per_channel],
        "kinds": kinds,
        "dimensions": {label: sorted(words) for label, words in dimensions.items()},
        "eras": eras(kinds),
        "twins": twins(kinds),
    }


def inspect(repo, per_channel=WINDOW):
    """The survey of a GitHub repository, from its release list."""
    return survey(holder.fetch_releases(repo), per_channel)


# --- the holds model -----------------------------------------------------------------------


def kind_pattern(shape_text):
    """The pattern that takes one kind, whatever the version, build counter or commit in the name."""
    out = ""
    for part in PLACEHOLDER_SPLIT.split(shape_text):
        out += PLACEHOLDERS.get(part) or re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", part)
    return f"^{out}$"


def _matches(holds, name):
    return holder.held_files({"assets": [{"name": name}]}, {"holds": holds})


def held_states(result, holds):
    """{shape: "all" | "some" | "none"}: in how many of the releases the kind is in, the holds take its file."""
    states = {}
    for kind in result["kinds"]:
        taken = sum(bool(_matches(holds, name)) for name in kind["names"].values())
        states[kind["shape"]] = "none" if not taken else "all" if taken == len(kind["names"]) else "some"
    return states


def own_patterns(result, holds):
    """The holds that are not a tick: [{label, include, files}], files the number of names in the survey they take."""
    ticks = {kind_pattern(kind["shape"]) for kind in result["kinds"]}
    return [
        {
            "label": hold.get("label", ""),
            "include": hold["include"],
            "files": sum(bool(_matches([hold], name)) for kind in result["kinds"] for name in kind["names"].values()),
        }
        for hold in holds
        if hold["include"] not in ticks
    ]


def with_kind(result, holds, shape_text, on):
    """The holds with one kind ticked or unticked, and a message when a pattern of the admin's own still decides it."""
    pattern = kind_pattern(shape_text)
    kind = next(kind for kind in result["kinds"] if kind["shape"] == shape_text)
    holds = [dict(hold) for hold in holds]
    if on:
        if not all(_matches(holds, name) for name in kind["names"].values()):
            holds.append({"label": shape_text, "include": pattern})
        return holds, None
    holds = [hold for hold in holds if hold["include"] != pattern]
    still = [hold for hold in holds if any(_matches([hold], name) for name in kind["names"].values())]
    if still:
        return holds, M(
            "library.holder.held-by-pattern",
            "Still held by the pattern {pattern}. Change or remove that pattern to stop holding it.",
            pattern=still[0]["include"],
        )
    return holds, None


def preview(result_window, holds):
    """For each release looked at, the names the holds take: [{tag, channel, files, none}]. `none` marks a release
    the holds take nothing from, which the keeper passes over."""
    return [
        {
            "tag": release["tag"],
            "channel": channel,
            "files": [asset["name"] for asset in holder.held_files(release, {"holds": holds})],
            "none": not holder.held_files(release, {"holds": holds}),
        }
        for channel, releases in result_window.items()
        for release in releases
    ]


def estimate(chosen, holds, keep):
    """Bytes: what the holds take from the newest release they take anything from (`chosen` is window()'s), and
    that `keep` times."""
    ordered = sorted(
        (release for releases in chosen.values() for release in releases),
        key=lambda release: release["published"] or "",
        reverse=True,
    )
    for release in ordered:
        files = holder.held_files(release, {"holds": holds})
        if files:
            once = sum(asset["size"] for asset in files)
            return {"tag": release["tag"], "per_version": once, "total": once * keep}
    return {"tag": None, "per_version": 0, "total": 0}


# --- an Obtainium recipe -------------------------------------------------------------------


def parse_recipe(text):
    """An Obtainium recipe (the JSON an app is exported as, or a link carrying it) as the settings of a holder:
    {repo, title, holds, channels, notes}. `notes` lists, as messages, what the recipe asks for that the box does
    not do. The `obtainium://app/<json>` form of a link is read as `app/` followed by the JSON, percent-encoded
    (from memory of Obtainium's deep links, not checked against them)."""
    text = (text or "").strip()
    if text.startswith("obtainium://app/"):
        text = urllib.parse.unquote(text[len("obtainium://app/") :])
    try:
        recipe = json.loads(text)
        settings = recipe.get("additionalSettings") or {}
        if isinstance(settings, str):
            settings = json.loads(settings)
        if not isinstance(recipe, dict) or not isinstance(settings, dict):
            raise ValueError("not an object")
    except (ValueError, AttributeError) as exc:
        raise LibrarianError(
            M("library.holder.recipe-invalid", "The recipe cannot be read: it is not a JSON object from Obtainium.")
        ) from exc
    match = re.match(
        r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$", str(recipe.get("url", ""))
    )
    if not match:
        raise LibrarianError(
            M(
                "library.holder.recipe-not-github",
                "The recipe is not for a GitHub project. Only GitHub is supported so far.",
            )
        )
    include = settings.get("apkFilterRegEx") or ""
    holds = []
    if include and settings.get("invertAPKFilter"):
        holds = [{"label": "Everything but the filter", "include": ".", "exclude": include}]
    elif include:
        holds = [{"label": "Obtainium's filter", "include": include}]
    else:
        holds = [{"label": "Phone apps", "include": r"\.(apk|xapk|apkm|apks)$"}]
    notes = [
        M("library.holder.recipe-ignored", "The recipe's {setting} is not used.", setting=name)
        for name in (
            "filterReleaseTitlesByRegEx",
            "filterReleaseNotesByRegEx",
            "versionExtractionRegEx",
            "releaseDateAsVersion",
            "autoApkFilterByArch",
            "trackOnly",
        )
        if settings.get(name)
    ]
    return {
        "repo": match.group(1),
        "title": str(recipe.get("name") or "")[:80],
        "holds": holds,
        "channels": {"stable": 2, "pre-release": 1 if settings.get("includePrereleases") else 0},
        "notes": notes,
    }


# --- a report to read ----------------------------------------------------------------------


def report(result):
    """The survey as text, for `librarian holders --survey OWNER/REPO`."""
    lines = [f"{channel}: {', '.join(tags) or '-'}" for channel, tags in result["window"].items()]
    if result["short"]:
        lines.append(f"fewer than the usual number in: {', '.join(result['short'])}")
    current = None
    for kind in result["kinds"]:
        if kind["class"] != current:
            current = kind["class"]
            lines.append(f"[{current}]")
        low, high = kind["size_min"] / 1e6, kind["size_max"] / 1e6
        size = f"{high:.1f} MB" if low == high else f"{low:.1f}-{high:.1f} MB"
        extras = "; ".join(
            [f"{label}={','.join(words)}" for label, words in kind["tags"].items()]
            + ([f"variant={','.join(kind['variant'])}"] if kind["variant"] else [])
            + ([f"with {len(kind['companions'])} companion(s)"] if kind["companions"] else [])
        )
        lines.append(f"  {kind['shape']:<60} {len(kind['releases'])} releases  {size:<16} {extras}")
    lines += [f"era? {pair['older']} (older) ~ {pair['newer']} (newer) {pair['similarity']}" for pair in result["eras"]]
    lines += [f"twins? {pair['first']} = {pair['second']} ({pair['releases']} releases)" for pair in result["twins"]]
    return "\n".join(lines)
