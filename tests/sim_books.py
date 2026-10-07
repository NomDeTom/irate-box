# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Books at hundreds (next-work plan step 14), offline. Kiwix's catalogue: a full rebuild in batches,
an unreadable book found by halving and left out, and one new, replaced or removed book changing
only its own entry, counted in kiwix-manage runs. kiwix-manage is stood in by a script that
behaves as the real one did on the Lyra (2026-10-07): entries keyed by the ZIM's UUID, paths
relative to the library, and one unreadable file in an `add` writing nothing at all. The books
are real small ZIMs (tests/zimgen.py). python3 tests/sim_books.py"""
import json, os, struct, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="books-"))
BIN = T / "bin"; BIN.mkdir()
LOG = T / "kiwix-calls.log"
(BIN / "kiwix-manage").write_text(f"""#!{sys.executable}
import os, struct, sys, uuid, xml.etree.ElementTree as ET
lib, action, *rest = sys.argv[1:]
open({str(LOG)!r}, "a").write(action + " " + str(len(rest)) + "\\n")
root = ET.parse(lib).getroot() if os.path.exists(lib) else ET.Element("library", version="20110515")
if action == "add":
    for z in rest:
        try:
            head = open(z, "rb").read(24)
        except OSError:
            sys.exit(1)
        if len(head) < 24 or struct.unpack("<I", head[:4])[0] != 72173914:
            print("Cannot add zim " + z + " to the library.", file=sys.stderr); sys.exit(1)
    for z in rest:
        bid = str(uuid.UUID(bytes=open(z, "rb").read(24)[8:24]))
        for b in [b for b in root if b.get("id") == bid]:
            root.remove(b)
        ET.SubElement(root, "book", id=bid, path=os.path.relpath(z, os.path.dirname(os.path.abspath(lib))))
elif action == "remove":
    for b in [b for b in root if b.get("id") in rest]:
        root.remove(b)
ET.ElementTree(root).write(lib)
""")
(BIN / "kiwix-manage").chmod(0o755)
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"
os.environ["HUB_STATE_DIR"] = str(T / "state")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tests"))
import zimgen  # noqa: E402
from irate_box.library import librarian as L  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def calls():
    lines = LOG.read_text().splitlines() if LOG.exists() else []
    LOG.unlink(missing_ok=True)
    return lines
def books():
    import xml.etree.ElementTree as ET
    return sorted(Path(b.get("path")).name for b in ET.parse(L.LIBRARY_XML).getroot().iter("book"))

L.ZIM_DIR.mkdir(parents=True)
zimgen.main([str(L.ZIM_DIR), "250"])
(L.ZIM_DIR / "testbook-0137.zim").write_bytes(b"not a zim at all, but named like one" * 10)
left = L.rebuild_library()
c = calls()
check("a full rebuild: every readable book, the unreadable one left out and said", len(books()) == 249 and [p.name for p in left] == ["testbook-0137.zim"], left)
check("  in a few runs, not one per book", len(c) <= 20 and c[0] == "add 100", (len(c), c[:4]))
zimgen.write_zim(L.ZIM_DIR / "newbook.zim", name="newbook", title="New")
L.library_put(L.ZIM_DIR / "newbook.zim")
check("a new book: one run, its entry added, the rest untouched", calls() == ["add 1"] and len(books()) == 250 and "newbook.zim" in books())
zimgen.write_zim(L.ZIM_DIR / "newbook.zim", name="newbook", title="New, version 2")
L.library_put(L.ZIM_DIR / "newbook.zim")
check("a book replaced (a new UUID): the old entry removed, the new one added", calls() == ["remove 1", "add 1"] and books().count("newbook.zim") == 1)
(L.ZIM_DIR / "newbook.zim").unlink()
L.library_drop("newbook.zim")
check("a book removed: its entry out, in one run", calls() == ["remove 1"] and "newbook.zim" not in books() and len(books()) == 249)
L.library_drop("never-there.zim")
check("  one never in the catalogue: nothing run", calls() == [])
L.LIBRARY_XML.unlink()
zimgen.write_zim(L.ZIM_DIR / "after.zim", name="after", title="After")
L.library_put(L.ZIM_DIR / "after.zim")
check("no catalogue at all: rebuilt in full instead", len(books()) == 250 and all(x.startswith("add") for x in calls()))
L.LIBRARY_XML.write_text("<library><book id=")
L.library_put(L.ZIM_DIR / "after.zim")
check("an unreadable catalogue: rebuilt in full instead", len(books()) == 250)
calls()
health_src = (REPO / "irate_box/root/health.py").read_text()
check("the Services doctor's rebuild batches too", '"kiwix-manage", str(new), "add", *map(str, batch)' in health_src)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
