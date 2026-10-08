# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The home page's link to /account.html (item 6 of plans/current-and-next-actions): nothing while
sign-up is off (the default), "Sign in" for a guest once it's on, "My account" once signed in.
python3 tests/sim_account_nav.py"""
import os, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="account-nav-"))
os.environ["HUB_STATE_DIR"] = str(T)
sys.path.insert(0, str(REPO))
from irate_box.hub import accounts, server  # noqa: E402

fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

check("sign-up off by default: no account link", "/account.html" not in server.home_page(False).decode())

accounts.set_settings(signup="open")
guest = server.home_page(False).decode()
check("sign-up on, a guest: Sign in, linking to /account.html", "Sign in</a>" in guest and 'href="/account.html"' in guest)
check("not already claiming My account", "My account" not in guest)

signed_in = server.home_page(True).decode()
check("sign-up on, signed in: My account", "My account</a>" in signed_in and 'href="/account.html"' in signed_in)

accounts.set_settings(signup="off")
check("switched off again: no account link", "/account.html" not in server.home_page(False).decode())

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
