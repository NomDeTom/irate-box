# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Small, valid ZIM files for tests and measurements (next-work plan step 14: books at hundreds).
A ZIM (openzim.org/wiki/ZIM_file_format, major version 5, the 'A' and 'M' namespaces) with one
page and the metadata Kiwix reads (Title, Language, Name, Description, Creator, Publisher, Date),
uncompressed, with its MD5 checksum. A few KB each, each with its own UUID.

    python3 tests/zimgen.py DIR COUNT [PREFIX]     COUNT files, PREFIX-0001.zim …
    from zimgen import write_zim; write_zim(path, name="x", title="X", language="eng")
Stdlib only."""
import hashlib
import struct
import sys
import uuid as uuidlib
from pathlib import Path

MAGIC = 72173914
LANGS = ("eng", "fra", "deu", "spa", "ita", "por", "nld", "pol", "rus", "jpn")


def write_zim(path, name, title, language="eng", description="A test book", date="2026-10-07", body=None, uid=None):
    mimes = ["text/html", "text/plain"]
    page = body if body is not None else f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{description}</p></body></html>"
    entries = [("A", "index.html", title, 0, page.encode())]
    meta = {"Creator": "irate-box tests", "Date": date, "Description": description, "Language": language,
            "Name": name, "Publisher": "irate-box tests", "Title": title}
    entries += [("M", k, "", 1, v.encode()) for k, v in sorted(meta.items())]
    entries.sort(key=lambda e: (e[0], e[1]))
    # One uncompressed cluster with every blob: an info byte (1), the offsets (n+1, uint32), the blobs.
    blobs = [e[4] for e in entries]
    offs, pos = [], 4 * (len(blobs) + 1)
    for b in blobs:
        offs.append(pos)
        pos += len(b)
    offs.append(pos)
    cluster = bytes([1]) + b"".join(struct.pack("<I", o) for o in offs) + b"".join(blobs)
    mime_list = b"".join(m.encode() + b"\0" for m in mimes) + b"\0"
    dirents = [struct.pack("<HBcIII", mime, 0, ns.encode(), 0, 0, i) + url.encode() + b"\0" + t.encode() + b"\0"
               for i, (ns, url, t, mime, _) in enumerate(entries)]
    n = len(entries)
    header_len = 80
    mime_pos = header_len
    url_ptr_pos = mime_pos + len(mime_list)
    title_ptr_pos = url_ptr_pos + 8 * n
    cluster_ptr_pos = title_ptr_pos + 4 * n
    dirent_pos = cluster_ptr_pos + 8
    dpos, url_ptrs = dirent_pos, []
    for d in dirents:
        url_ptrs.append(dpos)
        dpos += len(d)
    cluster_pos = dpos
    checksum_pos = cluster_pos + len(cluster)
    titles = sorted(range(n), key=lambda i: (entries[i][0], entries[i][2] or entries[i][1]))
    main = next(i for i, e in enumerate(entries) if e[0] == "A")
    uid = uid or uuidlib.uuid4().bytes
    header = struct.pack("<IHH16sIIQQQQIIQ", MAGIC, 5, 0, uid, n, 1, url_ptr_pos, title_ptr_pos, cluster_ptr_pos,
                         mime_pos, main, 0xFFFFFFFF, checksum_pos)
    data = (header + mime_list + b"".join(struct.pack("<Q", p) for p in url_ptrs) + b"".join(struct.pack("<I", i) for i in titles)
            + struct.pack("<Q", cluster_pos) + b"".join(dirents) + cluster)
    assert len(data) == checksum_pos
    Path(path).write_bytes(data + hashlib.md5(data).digest())
    return uid


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    out, count = Path(argv[0]), int(argv[1])
    prefix = argv[2] if len(argv) == 3 else "testbook"
    out.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        name = f"{prefix}-{i:04d}"
        write_zim(out / f"{name}.zim", name=name, title=f"Test book {i}", language=LANGS[i % len(LANGS)],
                  description=f"Generated test book number {i}", date=f"2026-{1 + i % 9:02d}-{1 + i % 27:02d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
