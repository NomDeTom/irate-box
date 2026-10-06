#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapt a clone of https://github.com/anthay/ELIZA for the hub. Run by the librarian on what
it fetched (the commit its manifest pins, or the newest, apps.d/65-eliza.json).

    adapt_eliza.py TREE [HUB_STATIC_DIR]

The repository stays as Anthony and Max Hay publish it; only the hub's copy changes, and only
this much:

  * src/eliza.html becomes eliza.html at the top, with a note saying what changed, and one
    block added to its load handler (marked "Irate-Box"): ?script=NAME runs scripts/NAME.txt
    as the page's own *load does with a file from disk, and &add=NAME also puts
    scripts/NAME.txt in front of the script's closing "()". The list page the hub renders at
    /eliza.html links each script this way. With no ?script= the page is exactly upstream's;
  * scripts/ is kept as it is, and the box's own keywords (extras/eliza/box.txt in the hub's
    code) are added to it as box.txt;
  * index.html sends the add-on's own address to eliza.html (the list of scripts is the hub's
    page, behind ELIZA's tile);
  * the rest of src/ (the C++ version) and doc/ go: /eliza/ serves the whole folder, and
    neither is a page a visitor opens. LICENSE, README.md and scripts/scripts.md stay.

The scripts are not the hub's to relicense: some are CC0, some are not (the 1966 CACM DOCTOR
is (c) ACM; YAPYAP is reproduced with the Garfinkel archive's permission). That is why the
hub's code carries none of them, and the box fetches them from where their publisher keeps
them. The page's code is the Hays' (public domain, CC0 1.0 or MIT), so changing it is fine.

Anything the added block relies on that a new upstream commit no longer has stops this script
with an error, so the librarian keeps what the box has (or falls back to the pinned commit)
rather than install a page that half works. Running it twice changes nothing. Stdlib only.
"""

import json
import re
import shutil
import sys
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
BOX_KEYWORDS = CHECKOUT / "extras" / "eliza" / "box.txt"
MANIFEST = CHECKOUT / "addons" / "eliza.json"  # its catalogue entry (a local add-on)
MARK = "irate-box-adapted"
# The list's 1966 entry runs the page's built-in copy of this one (so *cacm works), not the file.
BUILT_IN = "ELIZA-script-DOCTOR-original-1966-CACM-appendix"
ORIGIN = "https://github.com/anthay/ELIZA"

# The line the block goes after, and what the block uses: each must be in the page once.
ANCHOR = re.compile(r"^    let traceText = conversation;\r?\n", re.M)
NEEDS = {
    "the engine": r"^class Eliza\b",
    "the script reader": r"^function readScript\(",
    "the tracer": r"^class Tracer\b",
    "join()": r"^function join\(",
    "the load handler": r"^window\.addEventListener\('load',",
    "the log element": r"^    const divLog = ",
    "writeText()": r"^    function writeText\(",
    "the script text": r"^    let currentScriptText = ",
    "the script": r"^    let \[status, script\] = ",
    "the tracer in use": r"^    let tracer = ",
    "the transformation limit": r"^    let currentTransformationLimit = ",
    "ELIZA in use": r"^    let eliza = ",
    "the conversation log": r"^    let conversation = ",
}

NOTE = """<!-- {mark}: the Irate-Box's copy of src/eliza.html from {origin}
     (the commit is in irate-box-bundle.json beside it). Changed for the hub by
     irate_box/library/adapt_eliza.py: this note, and one block in the load handler (marked
     "Irate-Box") so that ?script=NAME runs scripts/NAME.txt. Nothing else. -->
"""

BLOCK = """
    // --- Irate-Box: ?script=NAME runs scripts/NAME.txt in place of the built-in DOCTOR, as
    // *load does with a file from disk; &add=NAME also puts scripts/NAME.txt in front of the
    // script's closing "()" (the box's own keywords). The hub's list page (/eliza.html) links
    // each script this way. With no ?script= the page is exactly as upstream has it.
    (async () => {
        const q = new URLSearchParams(location.search);
        const name = q.get('script'), add = q.get('add');
        if (!name) return;
        const get = async (n) => {
            if (!/^[A-Za-z0-9-]+$/.test(n)) throw new Error(`'${n}' is not a script name`);
            const r = await fetch(`scripts/${n}.txt`, { cache: 'no-cache' });
            if (!r.ok) throw new Error(`scripts/${n}.txt: ${r.status}`);
            return r.text();
        };
        let text;
        try {
            text = await get(name);
            if (add) {
                const extra = await get(add);
                text = text.replace(/\\(\\s*\\)\\s*$/, () => extra + '\\n()');
            }
        } catch (e) {
            writeText(`Could not load the script (${e.message}). This is the built-in 1966 DOCTOR.`);
            writeText('');
            return;
        }
        const [status, s] = readScript(text);
        divLog.innerHTML = '';
        writeText(`Script: ${name}.txt` + (add ? ` with ${add}.txt` : '') + '. Type *help and press the Enter key to see a list of commands.');
        writeText('');
        if (status !== 'success') {
            writeText(`This script does not run as written: ${status}`);
            writeText('ELIZA is still running the built-in 1966 DOCTOR script.');
            writeText('');
            return;
        }
        currentScriptText = text;
        script = s;
        tracer = new Tracer();
        eliza = new Eliza(s.rules, s.memoryRule, tracer, currentTransformationLimit);
        writeText(join(s.helloMessage));
        writeText('');
        conversation = join(s.helloMessage) + '\\n\\n';
        traceText = conversation;
    })();
    // --- end Irate-Box
"""

INDEX = """<!DOCTYPE html>
<!-- {mark}: /eliza/ itself, on the add-on's own address: the page, with its built-in 1966 DOCTOR.
     The list of every script is the hub's page, behind ELIZA's tile on the hub. -->
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="0; url=eliza.html">
  <title>ELIZA</title>
</head>
<body>
  <p><a href="eliza.html">ELIZA</a></p>
</body>
</html>
"""


class AdaptError(Exception):
    pass


def adapt_page(text):
    """src/eliza.html as the hub serves it: the note after the doctype, the block after ANCHOR,
    in the file's own line endings."""
    if MARK in text:
        return text
    nl = "\r\n" if "\r\n" in text else "\n"
    first, sep, rest = text.partition(nl)
    if first.strip().lower() != "<!doctype html>":
        raise AdaptError("src/eliza.html does not start with <!doctype html>")
    missing = [what for what, pat in NEEDS.items() if len(re.findall(pat, text, re.M)) != 1]
    if missing:
        raise AdaptError("src/eliza.html has changed: not found once: " + ", ".join(missing))
    anchors = ANCHOR.findall(text)
    if len(anchors) != 1:
        raise AdaptError(f"src/eliza.html has changed: the line the block goes after is there {len(anchors)} times, not once")
    note = NOTE.format(mark=MARK, origin=ORIGIN).replace("\n", nl)
    block = BLOCK.replace("\n", nl)
    rest = ANCHOR.sub(lambda m: m.group(0) + block, rest, count=1)
    return first + sep + note + rest


def listed_scripts():
    """The script names the manifest's entries link to (?script=NAME)."""
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {n for e in m.get("entries", []) for n in re.findall(r"[?&]script=([A-Za-z0-9-]+)", e.get("path", ""))}


def adapt(tree):
    tree = Path(tree)
    page = tree / "eliza.html"
    if page.exists() and MARK in page.read_text(encoding="utf-8"):
        return []  # already adapted
    src = tree / "src" / "eliza.html"
    scripts = tree / "scripts"
    if not src.is_file():
        raise AdaptError("no src/eliza.html: is this anthay/ELIZA?")
    if not scripts.is_dir() or not any(scripts.glob("*.txt")):
        raise AdaptError("no scripts/*.txt")
    if (scripts / "box.txt").exists():
        raise AdaptError("upstream now has a scripts/box.txt, which the box's keywords would replace")
    with open(src, encoding="utf-8", newline="") as fh:
        text = adapt_page(fh.read())
    with open(page, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    shutil.copyfile(BOX_KEYWORDS, scripts / "box.txt")
    (tree / "index.html").write_text(INDEX.format(mark=MARK), encoding="utf-8")
    for gone in ("src", "doc"):
        shutil.rmtree(tree / gone, ignore_errors=True)
    # Not an error: a script upstream added is served, just not on the list until the
    # manifest names it (when the pin is next moved, after a look at it).
    have = {p.stem for p in scripts.glob("*.txt")} - {"box", BUILT_IN}
    return sorted(have - listed_scripts())


def main(argv):
    if not 1 <= len(argv) <= 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        unlisted = adapt(argv[0])
    except (AdaptError, OSError) as exc:
        print(f"adapt_eliza: {exc}", file=sys.stderr)
        return 1
    for name in unlisted:
        print(f"adapt_eliza: scripts/{name}.txt is not on the list page (addons/eliza.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
