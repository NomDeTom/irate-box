#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""SilverBullet's own manual, in the notes space, so the links its starting page offers open offline.

SilverBullet's documentation is itself a SilverBullet space: the docs/ folder of its repository at
each release. This puts that folder's pages under SilverBullet/ in the hub's notes space:

  - its links point at the copies (a [[Page]] becomes [[SilverBullet/Page]], and a link to
    silverbullet.md/Page the same), leaving code, live queries and SilverBullet's own library
    pages (^Library/…) alone;
  - nothing in it runs in the owner's space: its space-lua and space-style blocks become plain lua
    and css blocks, read as examples, and the site's own configuration (CONFIG, Library/) is left out;
  - SilverBullet.md says what the folder is; the starting page's two links to the online manual
    point at the copies; Syncthing leaves the folder out, as each box carries its own version.

The pages are replaced whole when SilverBullet's version changes, and left alone otherwise.

    sbdocs.py fetch COMMIT OUT.tar.gz    docs/ at that commit (git checks it), packed reproducibly
    sbdocs.py sum FILE                   the sha256 of the tar it holds (what install.sh pins)
    sbdocs.py install TAR SPACE VERSION  into the space (as the hub user); nothing if already there
Stdlib only, besides git for fetch.
"""
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = "https://github.com/silverbulletmd/silverbullet"
FOLDER = "SilverBullet"
MARK = ".irate-box-sbdocs"           # the version installed, beside the folder
LEFT_OUT = ("CONFIG.md", "Library/")  # the site's own configuration: it would act on the owner's space
FENCES = {"space-lua": "lua", "space-style": "css"}
ONLINE = re.compile(r"\[([^\]]*)\]\(https://silverbullet\.md/([^)#\s]*)(#[^)\s]*)?\)")
WIKI = re.compile(r"(!?)\[\[([^\]|#]*)(#[^\]|]*)?(\|[^\]]*)?\]\]")
STIGNORE = ("// SilverBullet's manual: each box has its own (irate-box)", f"/{FOLDER}", f"/{FOLDER}.md", f"/{MARK}")


def fetch(commit, out):
    """docs/ at `commit`, by a sparse git fetch (git checks every object against the commit), packed as a
    reproducible tar.gz: sorted names, no times or owners, so the same commit gives the same bytes."""
    with tempfile.TemporaryDirectory(prefix="sbdocs-") as tmp:
        git = ["git", "-C", tmp, "-c", "advice.detachedHead=false"]
        for cmd in (["init", "-q"], ["remote", "add", "origin", REPO], ["sparse-checkout", "set", "--no-cone", "/docs/"],
                    ["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit], ["checkout", "-q", "FETCH_HEAD"]):
            subprocess.run(git + cmd, check=True, capture_output=True, timeout=600)
        got = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        if got != commit:
            raise ValueError(f"fetched {got}, not {commit}")
        pack(Path(tmp) / "docs", out)


def pack(src, out):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in sorted(x for x in Path(src).rglob("*") if x.is_file() and not x.is_symlink()):
            info = tarfile.TarInfo("docs/" + p.relative_to(src).as_posix())
            info.size, info.mode, info.mtime = p.stat().st_size, 0o644, 0
            with open(p, "rb") as fh:
                tar.addfile(info, fh)
    with open(out, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, filename="") as gz:
        gz.write(buf.getvalue())


def sha256(path):
    """Of the tar inside: zlib builds differ in how they compress the same bytes, so the pin is on what the gzip holds."""
    with gzip.open(path, "rb") as fh:
        h = hashlib.sha256()
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _target(name):
    """A link's page, as it is in the copy; None to leave the link as it was."""
    name = name.strip()
    if not name or name.startswith(("^", "/")) or "://" in name or name.startswith(FOLDER + "/"):
        return None
    return f"{FOLDER}/{name}"


def _links(text):
    def wiki(m):
        t = _target(m.group(2))
        return m.group(0) if t is None else f"{m.group(1)}[[{t}{m.group(3) or ''}{m.group(4) or ''}]]"

    def online(m):
        from urllib.parse import unquote
        page = unquote(m.group(2)) or "index"
        label = m.group(1)
        return f"[[{FOLDER}/{page}{m.group(3) or ''}{'|' + label if label else ''}]]"

    return ONLINE.sub(online, WIKI.sub(wiki, text))


def _expression_end(text, i):
    """The end of a ${…} expression starting at i, braces balanced; the text's end if unclosed."""
    depth = 0
    for j in range(i + 1, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(text)


def adapt(text):
    """One page, as it is in the copy: fences made inert, links re-pointed outside code and ${…}."""
    out, fence = [], None
    for line in text.splitlines(keepends=True):
        m = re.match(r"^(\s*)(`{3,}|~{3,})\s*([\w-]*)", line)
        if fence is None and m:
            fence = m.group(2)
            kind = m.group(3)
            if kind in FENCES:
                line = line.replace(kind, FENCES[kind], 1)
            out.append(line)
            continue
        if fence is not None:
            if line.strip().startswith(fence) and line.strip().strip(fence[0]) == "":
                fence = None
            out.append(line)
            continue
        # Outside code: inline code and ${…} kept as they are, links in the rest re-pointed.
        parts, i = [], 0
        while i < len(line):
            if line.startswith("${", i):
                j = _expression_end(line, i + 1)
                parts.append(line[i:j]); i = j
            elif line[i] == "`":
                j = line.find("`", i + 1)
                j = len(line) if j < 0 else j + 1
                parts.append(line[i:j]); i = j
            else:
                j = min([k for k in (line.find("${", i), line.find("`", i)) if k >= 0] or [len(line)])
                parts.append(_links(line[i:j])); i = j
        out.append("".join(parts))
    return "".join(out)


def _left_out(rel):
    return any(rel == x or (x.endswith("/") and rel.startswith(x)) for x in LEFT_OUT)


def _stignore(space):
    p = space / ".stignore"
    have = p.read_text().splitlines() if p.is_file() else []
    missing = [l for l in STIGNORE if l not in have]
    if missing:
        p.write_text("\n".join(have + missing) + "\n")


def _starting_page(space):
    """The two links to the online manual on the starting page SilverBullet writes, pointed at the copies.
    Only those exact links: the rest of the page is the owner's."""
    p = space / "index.md"
    if not p.is_file():
        return
    t = p.read_text()
    new = (t.replace("[Manual](https://silverbullet.md/Manual)", f"[[{FOLDER}/Manual|Manual]]")
            .replace("[Getting Started](https://silverbullet.md/Getting%20Started)", f"[[{FOLDER}/Getting Started|Getting Started]]"))
    if new != t:
        p.write_text(new)


def install(tar_path, space, version):
    """The manual into `space`/SilverBullet, replacing an older version's whole. Returns what it did."""
    space = Path(space)
    if not space.is_dir():
        raise ValueError(f"{space}: no notes space")
    mark = space / MARK
    if mark.is_file() and mark.read_text().strip() == version and (space / FOLDER).is_dir():
        _starting_page(space)
        return f"SilverBullet's manual {version}: already in the notes"
    stage = Path(tempfile.mkdtemp(prefix=".sbdocs-", dir=space))
    try:
        n = 0
        with tarfile.open(tar_path) as tar:
            for m in tar.getmembers():
                rel = m.name[len("docs/"):] if m.name.startswith("docs/") else None
                if not m.isfile() or not rel or ".." in Path(rel).parts or rel.startswith("/") or _left_out(rel):
                    continue
                dest = stage / FOLDER / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                data = tar.extractfile(m).read()
                if rel.endswith(".md"):
                    data = adapt(data.decode("utf-8", errors="replace")).encode()
                    n += 1
                dest.write_bytes(data)
        (stage / f"{FOLDER}.md").write_text(
            f"# SilverBullet's manual\n\nThe manual of the SilverBullet this box runs ({version}), kept here so it opens with no "
            f"internet. Start at [[{FOLDER}/Manual]] or [[{FOLDER}/Getting Started]].\n\n"
            "It is replaced whole when SilverBullet is updated, so notes of your own belong outside this folder. Its code "
            "examples are shown, not run. The community forums are online only: https://community.silverbullet.md\n")
        old = space / f".{FOLDER}.old"
        shutil.rmtree(old, ignore_errors=True)
        if (space / FOLDER).exists():
            os.rename(space / FOLDER, old)
        os.rename(stage / FOLDER, space / FOLDER)
        os.replace(stage / f"{FOLDER}.md", space / f"{FOLDER}.md")
        shutil.rmtree(old, ignore_errors=True)
        mark.write_text(version + "\n")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    _starting_page(space)
    _stignore(space)
    return f"SilverBullet's manual {version}: {n} pages in the notes, under {FOLDER}/"


def main(argv):
    if len(argv) == 3 and argv[0] == "fetch":
        fetch(argv[1], argv[2])
    elif len(argv) == 2 and argv[0] == "sum":
        print(sha256(argv[1]))
    elif len(argv) == 4 and argv[0] == "install":
        print(install(argv[1], argv[2], argv[3]))
    else:
        sys.exit(__doc__.split("\n\n")[-1])


if __name__ == "__main__":
    main(sys.argv[1:])
