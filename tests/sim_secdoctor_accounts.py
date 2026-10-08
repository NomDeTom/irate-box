# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's checks of the hub's own accounts (item 6 of plans/current-and-next-actions,
accounts-plan stage 2's "still to do"): a weak or hand-edited hash, users mode left over plain HTTP
with no HTTPS, and an app in users mode whose gate isn't actually wired. python3 tests/sim_secdoctor_accounts.py"""
import json, os, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="secdoc-acc-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_CADDY_DIR=str(T / "caddy"))
(T / "state").mkdir(parents=True)
(T / "etc").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor as sd  # noqa: E402
from irate_box.hub import accounts as accmod  # noqa: E402

fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond


def by_id(findings):
    return {f["id"]: f for f in findings}


ctx = {"front_kind": "nginx"}

# No accounts.json yet: nothing to flag.
out = by_id(sd.step_accounts_hub(ctx))
check("no accounts.json: ok, nothing else", out["accounts-hash"]["status"] == "ok" and "accounts-http" not in out and "accounts-gate" not in out)

# A real account (hashed the way accounts.py does) is never flagged.
good_hash = accmod.hash_password("a password, long enough")
(T / "state" / "accounts.json").write_text(json.dumps({"accounts": {"tom": {"name": "Tom", "hash": good_hash, "state": "user", "role": "user"}},
                                                        "settings": {"signup": "off"}}))
out = by_id(sd.step_accounts_hub(ctx))
check("a real scrypt hash: ok", out["accounts-hash"]["status"] == "ok")

# A hand-edited or weak hash is a problem.
(T / "state" / "accounts.json").write_text(json.dumps({"accounts": {"tom": {"name": "Tom", "hash": "md5$deadbeef", "state": "user", "role": "user"}},
                                                        "settings": {"signup": "off"}}))
out = by_id(sd.step_accounts_hub(ctx))
check("a hash that isn't scrypt: a problem, naming the account", out["accounts-hash"]["status"] == "problem" and "tom" in out["accounts-hash"]["detail"])

# An account still waiting for its one-time code (hash "") is not a weak hash.
(T / "state" / "accounts.json").write_text(json.dumps({"accounts": {"tom": {"name": "Tom", "hash": "", "state": "user", "role": "user"}},
                                                        "settings": {"signup": "off"}}))
out = by_id(sd.step_accounts_hub(ctx))
check("an account waiting for its code: not flagged", out["accounts-hash"]["status"] == "ok")

# Sign-up off, or no app for users: no HTTP or gate findings even with a weak hash.
check("sign-up off: no HTTP or gate check needed", "accounts-http" not in out and "accounts-gate" not in out)

# Sign-up on, an app for users, no HTTPS, HTTP stance not prevented: a problem.
(T / "state" / "accounts.json").write_text(json.dumps({"accounts": {}, "settings": {"signup": "open", "http": "warning"}}))
(T / "etc" / "access.json").write_text(json.dumps({"drop": "users"}))
out = by_id(sd.step_accounts_hub(ctx))
check("users mode, no HTTPS, warning stance: a problem", out["accounts-http"]["status"] == "problem" and "drop" in out["accounts-http"]["detail"])
check("no gate file on disk: a problem, naming the app", out["accounts-gate"]["status"] == "problem" and "drop" in out["accounts-gate"]["detail"])

# The gate file is there: ok.
(T / "etc" / "nginx-access.conf.d").mkdir(parents=True)
(T / "etc" / "nginx-access.conf.d" / "gate-drop.conf").write_text("auth_request /_irate_user;\nerror_page 401 = @irate_box_login;\n")
out = by_id(sd.step_accounts_hub(ctx))
check("the gate file matches: ok", out["accounts-gate"]["status"] == "ok")

# Prevented stance with no HTTPS: no HTTP problem.
(T / "state" / "accounts.json").write_text(json.dumps({"accounts": {}, "settings": {"signup": "open", "http": "prevented"}}))
out = by_id(sd.step_accounts_hub(ctx))
check("prevented stance, no HTTPS: ok", out["accounts-http"]["status"] == "ok")

# The step is in the doctor's list.
check("the step is in the doctor's list", "accounts-hub" in [x[0] for x in sd.STEPS])

sys.exit(1 if fails else 0)
