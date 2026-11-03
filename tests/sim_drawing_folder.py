# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Drawing folder: its apps kept inside it, its three-way place on the hub, and the count that
lets it act. Against a hub it starts, with the built-in Excalidraw and Mermaid, then draw.io and
JS Paint added. python3 tests/sim_drawing_folder.py"""

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from freeport import free_port

REPO = Path(__file__).resolve().parents[1]
state = Path(tempfile.mkdtemp(prefix="drawing-folder-"))
(state / "control").mkdir()
(state / "apps.d").mkdir()
(state / "control" / "access.json").write_text(json.dumps({"drawio": "public", "jspaint": "public"}))
port = free_port()
SECRET = "f" * 64
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


def add_local(name, install=True):
    shutil.copyfile(REPO / "addons" / f"{name}.json", state / "apps.d" / f"{name}.json")
    if install:
        (state / "addons" / name).mkdir(parents=True, exist_ok=True)
    time.sleep(0.05)


def names(page, heading=None):
    """The entry names on a menu page (inside one heading, when given)."""
    inside = page.split(f'<h2 id="{heading}">', 1)[1] if heading else page
    if heading:
        inside = inside.split("</section>", 1)[0]
    return [
        name.replace("&amp;", "&").replace("&#x27;", "'")
        for name in __import__("re").findall(r'<span class="name">([^<]+)</span>', inside)
    ]


def tile(page, name):
    i = page.find(f'<span class="name">{name}</span>')
    return None if i < 0 else page[page.rfind("<a ", 0, i) : page.find("</a>", i)]


try:
    for _ in range(300):  # up to 30 s: a busy CI runner can take longer than 5 s to start the hub
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    def go(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {
            "Host": f"127.0.0.1:{port}",
            "X-Irate-Front": SECRET,
            "X-Irate-Admin": "1",
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "Sec-Fetch-Site": "same-origin",
        }
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        return r.status, r.read().decode()

    def arrange(shown=None, keep=None):
        st = {"drawing": {}}
        if shown is not None:
            st["drawing"]["shown"] = shown
        if keep is not None:
            st["drawing"]["keep"] = keep
        return go("POST", "/admin/folders", {"state": st})

    def home():
        return go("GET", "/")[1]

    def folder():
        return {f["id"]: f for f in json.loads(go("GET", "/admin/folders")[1])["folders"]}["drawing"]

    # Two drawing tools: Excalidraw and Mermaid. The folder is hidden by default; both tools show.
    page = home()
    check("two tools, the default: the Drawing tile is hidden", tile(page, "Drawing") is None)
    check("  Excalidraw and Mermaid show on the hub", tile(page, "Excalidraw") and tile(page, "Mermaid"))
    fol = folder()
    check(
        "  the folder says what it may do, and how many are installed",
        fol["keep_members"] == {"min": 3, "word": "drawing tools", "shown": "auto", "keep": True, "installed": 2},
        fol.get("keep_members"),
    )
    check("  and its page lists both", names(go("GET", "/drawing.html")[1]) == ["Excalidraw", "Mermaid"])

    st, _ = arrange("always")
    page = home()
    check(
        "Shown at two: the folder's tile appears, its tools still there",
        st == 200 and tile(page, "Drawing") and tile(page, "Excalidraw") and tile(page, "Mermaid"),
    )
    check("  the folder page still lists both", names(go("GET", "/drawing.html")[1]) == ["Excalidraw", "Mermaid"])

    # A third only lists the folder: its manifest is there, its files are not. It does not count, so
    # the two installed tools stay where they are (and the folder's page still lists it).
    add_local("drawio", install=False)
    page = home()
    check(
        "three list the folder but two are installed: still two, the tools stay on the hub",
        folder()["keep_members"]["installed"] == 2
        and tile(page, "Drawing")
        and tile(page, "Excalidraw")
        and tile(page, "draw.io")
        and tile(page, "Mermaid"),
    )
    check(
        "  the folder page lists the three",
        names(go("GET", "/drawing.html")[1]) == ["Excalidraw", "draw.io", "Mermaid"],
    )

    # Its files arrive. 'Shown' is still the choice, and now the folder may keep them.
    (state / "addons" / "drawio").mkdir(parents=True, exist_ok=True)
    time.sleep(0.05)
    page = home()
    check(
        "three installed, Shown: the folder keeps them, its own tile stays",
        tile(page, "Drawing")
        and tile(page, "Excalidraw") is None
        and tile(page, "Mermaid") is None
        and tile(page, "draw.io") is None,
    )
    check("  /admin/folders says three", folder()["keep_members"]["installed"] == 3)

    st, _ = arrange("auto")
    page = home()
    check(
        "three tools, When it holds them: the folder shows, its tools stay inside",
        st == 200 and tile(page, "Drawing") and tile(page, "Excalidraw") is None,
    )

    st, _ = arrange("auto", False)
    page = home()
    check(
        "  the tickbox off: the folder goes, its tools return to the hub",
        st == 200
        and tile(page, "Drawing") is None
        and tile(page, "Excalidraw")
        and tile(page, "draw.io")
        and tile(page, "Mermaid"),
    )

    st, _ = arrange("hidden", True)
    page = home()
    check(
        "Hidden with the tickbox on and three: the tools stay on the hub, the folder never shows",
        st == 200
        and tile(page, "Drawing") is None
        and tile(page, "Excalidraw")
        and tile(page, "draw.io")
        and tile(page, "Mermaid"),
    )
    check("  /drawing.html still works", go("GET", "/drawing.html")[0] == 200)

    st, _ = arrange("always", True)
    page = home()
    check(
        "Shown with the tickbox on: the folder shows, its tools stay inside",
        st == 200 and tile(page, "Drawing") and tile(page, "Excalidraw") is None,
    )

    # A fourth: JS Paint. The folder lists all four, and now says four installed.
    add_local("jspaint")
    check(
        "four tools: the folder lists all four",
        names(go("GET", "/drawing.html")[1]) == ["Excalidraw", "draw.io", "JS Paint", "Mermaid"],
    )
    check("  /admin/folders says four", folder()["keep_members"]["installed"] == 4)
    _, body = arrange("nowhere")
    check(
        "a place that is not one of the three is refused",
        go("POST", "/admin/folders", {"state": {"drawing": {"shown": "nowhere"}}})[0] == 400,
    )
    check(
        "  a tickbox that is not on or off is refused",
        go("POST", "/admin/folders", {"state": {"drawing": {"keep": "yes"}}})[0] == 400,
    )
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
