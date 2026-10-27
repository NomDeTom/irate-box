# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The shared notes as a zip, against a hub it starts: /admin/notes/download (the admin's only), every page and
attachment of SilverBullet's space, none of its hidden files, and never through a link out of the folder; no notes
folder, a 404. python3 tests/sim_notes_download.py"""

import http.client
import io
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from freeport import free_port

REPO = Path(__file__).resolve().parents[1]
state = Path(tempfile.mkdtemp(prefix="notesdl-"))
port = free_port()
SECRET = "e" * 64
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(state),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


try:
    for _ in range(150):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    def get(path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
        r = c.getresponse()
        return r.status, r.getheader("Content-Type") or "", r.getheader("Content-Disposition") or "", r.read()

    front = {"X-Irate-Front": SECRET}
    st, _, _, _ = get("/admin/notes/download", front)
    check("no notes folder: 404", st == 404, st)
    notes = state / "notes"
    (notes / "Journal").mkdir(parents=True)
    (notes / "index.md").write_text("# Welcome\n")
    (notes / "Journal" / "2026-10-12.md").write_text("Camp day one.\n")
    (notes / "Journal" / "map.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 10)
    (notes / ".silverbullet.db").write_text("internal")
    (notes / "_plug").mkdir()
    (notes / "_plug" / "x.plug.js").write_text("plug")
    (notes / ".hidden").mkdir()
    (notes / ".hidden" / "secret.md").write_text("no")
    outside = state / "outside"
    outside.mkdir()
    (outside / "admin-password").write_text("hunter2")
    (notes / "escape").symlink_to(outside)
    (notes / "leak.md").symlink_to(outside / "admin-password")
    st, ctype, disp, body = get("/admin/notes/download")
    check("without the web server's word: refused", st == 403, st)
    st, ctype, disp, body = get("/admin/notes/download", front)
    names = sorted(zipfile.ZipFile(io.BytesIO(body)).namelist()) if st == 200 else []
    check(
        "a zip, as a download",
        st == 200 and ctype == "application/zip" and disp.startswith('attachment; filename="notes-'),
        (st, ctype, disp),
    )
    check(
        "  every page and attachment, folders kept",
        {"index.md", "Journal/2026-10-12.md", "Journal/map.png", "_plug/x.plug.js"} <= set(names),
        names,
    )
    check(
        "  none of its hidden files, nothing through a link",
        not any(n.startswith(".") or "/." in n or "escape" in n or "leak" in n for n in names)
        and b"hunter2" not in body,
        names,
    )
finally:
    hub.terminate()
import shutil

shutil.rmtree(state, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
