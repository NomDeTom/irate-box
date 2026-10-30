# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""JS Paint's adapt script (irate_box/library/adapt_jspaint.py), offline: the news's pictures from outside the box,
from real lines of JS Paint's index.html at the pinned commit (53be67a). On box 181 (2026-10-30) each of them was
blocked by the add-on's policy, a console error and a broken picture. python3 tests/sim_adapt_jspaint.py"""

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import adapt_jspaint

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


# index.html at 53be67ab8c47cc0d2168899e7481bc04839c4c81, lines 336-338, 543-545, 659-660 and 704-705.
NEWS = """\t\t\t\t<a href="https://discord.gg/jxQBK3k8tx" target="_blank">
\t\t\t\t\t<img width="470" height="470" src="https://flipanim.com/gif/b/h/BhZWtwfg.gif"
\t\t\t\t\t\talt="Animation of Discord logo blinking its eyes">
\t\t\t<img class="inset-deep" width="640" height="480"
\t\t\t\talt="Screenshot of JS Paint with the Bubblegum theme"
\t\t\t\tsrc="https://i.postimg.cc/6QwYrWjM/Screen-Shot-2024-04-27-at-14-57-48.png" />
\t\t\t\t<img alt=""
\t\t\t\t\tsrc="https://github.com/1j01/jspaint/blob/master/images/modern/vista-tools-spaced-like-svg.png?raw=true">
\t\t\t\t<img alt=""
\t\t\t\t\tsrc="https://github.com/1j01/jspaint/blob/03f934eab98f450d3e527ef8e152cc5aa003fe07/images/classic/tools.png?raw=true">
\t\t\t\t<img alt="" src="images/occult/tools.png">
"""

tree = Path(tempfile.mkdtemp(prefix="jspaint-"))
(tree / "images" / "modern").mkdir(parents=True)
(tree / "images" / "modern" / "vista-tools-spaced-like-svg.png").write_bytes(b"png")
# images/classic/tools.png is absent here: an old commit's file this copy does not have is left out like any other.
out = adapt_jspaint.local_images(NEWS, tree)
check("no picture from outside the box is left", "https://" not in out.replace("https://discord.gg", ""), out)
check(
    "  JS Paint's own picture points at this copy's file",
    'src="images/modern/vista-tools-spaced-like-svg.png"' in out,
    out,
)
check(
    "  one this copy does not have, and the other sites' pictures, are left out",
    "flipanim" not in out and "postimg" not in out and "images/classic/tools.png" not in out,
    out,
)
check(
    "  the rest of the page is untouched: the link and a local picture stay",
    'href="https://discord.gg/jxQBK3k8tx"' in out and 'src="images/occult/tools.png"' in out,
    out,
)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
