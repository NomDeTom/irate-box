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
FAKE = """#!@PY@
import os, struct, sys, uuid, xml.etree.ElementTree as ET
lib, action, *rest = sys.argv[1:]
open("@LOG@", "a").write(action + " " + str(len(rest)) + "\\n")
root = ET.parse(lib).getroot() if os.path.exists(lib) else ET.Element("library", version="20110515")
if action == "add":
    for z in rest:
        try:
            head = open(z, "rb").read(24)
        except OSError:
            sys.exit(1)
        if len(head) < 24 or struct.unpack("<I", head[:4])[0] != 72173914:
            print("Cannot add zim " + z + " to the library.", file=sys.stderr); sys.exit(1)
    def meta(z):  # the M/ entries, from the one uncompressed cluster zimgen.py writes
        d = open(z, "rb").read()
        n, _, urlp, _, clp = struct.unpack("<IIQQQ", d[24:56])
        cl = struct.unpack("<Q", d[clp:clp + 8])[0] + 1
        out = {}
        for i in range(n):
            o = struct.unpack("<Q", d[urlp + 8 * i:urlp + 8 * i + 8])[0]
            ns, blob = chr(d[o + 3]), struct.unpack("<I", d[o + 12:o + 16])[0]
            url = d[o + 16:d.index(b"\\0", o + 16)].decode()
            a, b = struct.unpack("<II", d[cl + 4 * blob:cl + 4 * blob + 8])
            if ns == "M":
                out[url] = d[cl + a:cl + b].decode()
        return out
    for z in rest:
        bid = str(uuid.UUID(bytes=open(z, "rb").read(24)[8:24]))
        for b in [b for b in root if b.get("id") == bid]:
            root.remove(b)
        m = meta(z)
        ET.SubElement(root, "book", id=bid, path=os.path.relpath(z, os.path.dirname(os.path.abspath(lib))), title=m.get("Title", ""),
                      language=m.get("Language", ""), date=m.get("Date", ""), description=m.get("Description", ""), name=m.get("Name", ""))
elif action == "remove":
    for b in [b for b in root if b.get("id") in rest]:
        root.remove(b)
ET.ElementTree(root).write(lib)
"""
(BIN / "kiwix-manage").write_text(FAKE.replace("@PY@", sys.executable).replace("@LOG@", str(LOG)))
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
# The books a page at a time (/admin/books): the catalogue, the files and the sources together.
L.rebuild_library()
srcs = [{"name": f"testbook-{i:04d}", "type": "url", "url": f"https://example.invalid/{i}.zim"} for i in range(1, 41)]
srcs += [{"name": "coming-soon", "type": "url", "url": "https://example.invalid/soon.zim"}, {"name": "draw", "kind": "app", "type": "git"}]
L.save_config({"policy": L.load_config()["policy"], "sources": srcs})
L.save_status({"testbook-0002": {"current": {"version": "a"}, "latest": {"version": "b"}}, "testbook-0003": {"error": "HTTP 404"}})
pg = L.books_page()
sm = pg["summary"]
check("summary: every book (files, sources not yet installed), sizes, languages, states", sm["count"] == 252 and sm["kept"] == 41
      and sm["states"] == {"ok": 248, "newer": 1, "failed": 1, "unreadable": 1, "not installed": 1} and sm["languages"]["fra"] > 20, sm)
check("  an app's source is not a book", all(r["name"] != "draw" for r in L.book_rows()[0]))
check("a page of 50, sorted by title, with its sources and status", len(pg["books"]) == 50 and pg["pages"] == 6 and pg["matching"] == 252
      and [r["title"] for r in pg["books"]] == sorted((r["title"] for r in pg["books"]), key=str.lower) and "source" in pg["books"][0])
by = {r["name"]: r for r in L.book_rows()[0]}
check("  each book's state: newer, failed, unreadable, not installed, its title from Kiwix", by["testbook-0002"]["state"] == "newer"
      and by["testbook-0003"]["state"] == "failed" and by["testbook-0137"]["state"] == "unreadable" and by["coming-soon"]["state"] == "not installed"
      and by["testbook-0005"]["title"] == "Test book 5" and by["testbook-0005"]["kept"] and not by["testbook-0099"]["kept"])
check("search: in the title, name or description", L.books_page(q="book number 77")["matching"] == 1 and L.books_page(q="TESTBOOK-01")["matching"] == 100)
check("filters: language, state, kept current or not", L.books_page(language="fra")["matching"] == sm["languages"]["fra"]
      and L.books_page(state="failed")["books"][0]["name"] == "testbook-0003" and L.books_page(kept="yes")["matching"] == 41
      and L.books_page(kept="no")["matching"] == 211)
check("sort: size (largest first), date (newest first)", L.books_page(sort="size")["books"][0]["size"] >= L.books_page(sort="size")["books"][-1]["size"]
      and L.books_page(sort="date")["books"][0]["date"] >= L.books_page(sort="date")["books"][-1]["date"])
check("pages: the last one, past the end clamped", len(L.books_page(page=6)["books"]) == 2 and L.books_page(page=99)["page"] == 6)
check("every matching name, for a bulk action on all of them", L.books_page(kept="yes", names_only=True)["matching"] == 41
      and len(L.books_page(kept="yes", names_only=True)["names"]) == 41)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
