# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The home page's link to /account.html: "Sign in" for a guest whether sign-up is on or off (admins sign in
there too), "My account" once signed in, never both. python3 tests/sim_account_nav.py"""
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

def link(signed_in):
    return server.home_page(signed_in).decode()

guest = link(False)
check("sign-up off (the default), a guest: Sign in, linking to /account.html", "Sign in</a>" in guest and 'href="/account.html"' in guest, guest[-600:])
check("  not claiming My account", "My account" not in guest)
check("sign-up off, signed in: My account", "My account</a>" in link(True))

accounts.set_settings(signup="open")
guest = link(False)
check("sign-up on, a guest: Sign in", "Sign in</a>" in guest and "My account" not in guest)
signed_in = link(True)
check("sign-up on, signed in: My account, linking to /account.html, no Sign in", "My account</a>" in signed_in
      and 'href="/account.html"' in signed_in and "Sign in</a>" not in signed_in)

accounts.set_settings(signup="off")
check("switched off again: still Sign in for a guest", "Sign in</a>" in link(False))

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
