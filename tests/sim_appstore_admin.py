# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The App store's admin side (hub/appstore_admin.py) in the process, offline: the snapshot, the settings
actions, and a holder's Look again that stores a survey — all on the recorded release list of Meshtastic
Android, no network, no hub started. python3 tests/sim_appstore_admin.py"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="appstore-admin-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_HOLDS_ROOT=str(T / "holds"))
sys.path.insert(0, str(REPO))
from irate_box.hub import appstore_admin
from irate_box.library import holder, holdsurvey, librarian

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(body, message_id):
    """What the action answered when it refused: (400, {"error", "message" or "error" from answer()})."""
    code, out = post(body)
    if code != 400:
        return False
    m = out.get("message")
    if isinstance(m, dict):
        return m.get("id") == message_id
    return message_id.split(".")[-1] in str(out.get("error", ""))


raw = json.loads((REPO / "tests/fixtures/github-releases-meshtastic-android.json").read_text())
RELEASES = [holder._normalise(r) for r in raw if isinstance(r, dict)]
holder.fetch_releases = lambda repo: RELEASES

# --- snapshot with nothing ---
snap = appstore_admin.snapshot()
check(
    "empty: off, no holders, the presets shipped",
    snap["enabled"] is False
    and snap["holders"] == []
    and any(p["id"] == "meshtastic-android" for p in snap["presets"]),
)
check("  the policy pair is read", snap["policy"]["holders_every_hours"] == 24 and "min_free_mb" in snap["policy"])


def post(body):
    code, out = appstore_admin.action(body)
    return code, out


# --- enable, add from the preset, add by hand ---
code, out = post({"action": "enable", "enabled": True})
check("enable saves", code == 200 and out["enabled"] is True)
code, out = post({"action": "add-holder", "preset": "meshtastic-android"})
made = out["holders"][0]
check(
    "a preset added: its repo and two holds",
    code == 200 and made["repo"] == "meshtastic/Meshtastic-Android" and len(made["holds"]) == 2,
    made,
)
check("  its survey is not there yet", made["survey"] is None and made["status"]["survey_error"] is None)
check(
    "  a preset twice: refused",
    refused({"action": "add-holder", "preset": "meshtastic-android"}, "library.holder.id-taken"),
)
code, out = post({"action": "add-holder", "id": "by-hand", "repo": "Genymobile/scrcpy", "title": "Scrcpy"})
check("added by hand: nothing held yet", code == 200 and out["holders"][1]["holds"] == [], out["holders"][1])
check(
    "  a bad repository: said so",
    refused({"action": "add-holder", "id": "x", "repo": "not-a-repo"}, "library.holder.repo-invalid"),
)
code, out = post(
    {
        "action": "add-holder",
        "recipe": json.dumps(
            {
                "url": "https://github.com/GetObtainium/Obtainium",
                "name": "Obtainium",
                "additionalSettings": json.dumps(
                    {"apkFilterRegEx": "fdroid-.*-release\\.apk$", "autoApkFilterByArch": True}
                ),
            }
        ),
    }
)
check(
    "a recipe from Obtainium, read as a holder",
    code == 200
    and out["holders"][-1]["repo"] == "GetObtainium/Obtainium"
    and out["holders"][-1]["id"] == "obtainium"
    and len(out["holders"][-1]["holds"]) == 1,
    out["holders"][-1],
)
check(
    "  what the recipe asks that the box does not do is said on adding",
    [n["args"]["setting"] for n in out.get("notes", [])] == ["autoApkFilterByArch"],
    out.get("notes"),
)
check(
    "  a recipe that is not one: refused",
    refused({"action": "add-holder", "recipe": "hello"}, "library.holder.recipe-invalid"),
)
post({"action": "remove-holder", "id": "obtainium"})

# --- a holder's Look again stores a survey the page can draw ---
code, out = appstore_admin.action({"action": "noop"})  # (unknown actions are server.py's; this one is refused here)
check("an action this is not: said so", code == 400, out)
outcome = appstore_admin.survey_job("meshtastic-android")
snap = appstore_admin.snapshot()
one = next(h for h in snap["holders"] if h["id"] == "meshtastic-android")
sv = one["survey"]
check("Look again stored a survey", sv and sv["window"]["stable"][0] == "v2.8.3", one["status"])
check(
    "  a kind per shape, quiet classes left out",
    all(k["class"] not in ("checksum or signature", "companion") for k in sv["kinds"]) and len(sv["kinds"]) > 5,
)
check(
    "  the F-Droid universal kind, its state none (nothing matched yet holds the google name)",
    "androidApp-fdroid-universal-release.apk" in {k["shape"] for k in sv["kinds"]},
)
check("  sizes and counts for the table", all("size_max" in k and "releases" in k for k in sv["kinds"]))
check("  the estimate names a release and its bytes", sv["estimate"]["per_version"] > 1 << 20, sv["estimate"])
check("  its survey date is kept", sv["surveyed"])
check("  a survey that fails says so of the holder", "looked at" in outcome)
holder.fetch_releases = lambda repo: (_ for _ in ()).throw(
    librarian.LibrarianError(librarian.M("library.download.unreachable", "Down."))
)
check(
    "  and the error reaches the page",
    "failed" in appstore_admin.survey_job("by-hand")
    and appstore_admin.snapshot()["holders"][1]["status"]["survey_error"],
)
holder.fetch_releases = lambda repo: RELEASES

# --- the survey's table turned back into holds ---
shape = "androidApp-fdroid-universal-release.apk"
code, out = post({"action": "holds", "id": "meshtastic-android", "kinds": {shape: True}})
one = next(h for h in out["holders"] if h["id"] == "meshtastic-android")
check(
    "ticking a kind adds its pattern",
    code == 200 and one["holds"][-1]["include"] == holdsurvey.kind_pattern(shape),
    one["holds"],
)
check("  and its state is all", next(k for k in one["survey"]["kinds"] if k["shape"] == shape)["state"] == "all")
check(
    "  a kind the preset already holds needs no tick of its own",
    next(k for k in one["survey"]["kinds"] if k["shape"] == "androidApp-google-release.apk")["state"] == "all",
)
code, out = post({"action": "holds", "id": "meshtastic-android", "kinds": {shape: False}})
one = next(h for h in out["holders"] if h["id"] == "meshtastic-android")
check("unticking takes it away again", holdsurvey.kind_pattern(shape) not in [hold["include"] for hold in one["holds"]])
check(
    "a kind the survey does not know: refused",
    refused(
        {"action": "holds", "id": "meshtastic-android", "kinds": {"no-such-kind": True}}, "library.holder.shape-none"
    ),
)
check(
    "a holder with no survey: asked to look first",
    refused({"action": "holds", "id": "by-hand", "kinds": {"x": True}}, "library.holder.no-survey"),
)
check(
    "nothing of that name: said so",
    refused({"action": "edit-holder", "id": "nowhere", "title": "x"}, "library.holder.none"),
)

# --- edit, kept files, remove ---
code, out = post(
    {
        "action": "edit-holder",
        "id": "by-hand",
        "title": "Scrcpy again",
        "channels": {"stable": 3, "pre-release": 1},
        "budget_mb": 500,
    }
)
one = next(h for h in out["holders"] if h["id"] == "by-hand")
check(
    "edit changes the fields, keeps the id",
    code == 200 and one["title"] == "Scrcpy again" and one["channels"]["stable"] == 3 and one["budget_mb"] == 500,
    one,
)
check(
    "  a bad count: refused",
    refused({"action": "edit-holder", "id": "by-hand", "channels": {"stable": 9}}, "library.holder.count-invalid"),
)

# put files on disk so the kept sizes and remove-files are real
folder = T / "holds" / "by-hand" / "v5.0.1"
folder.mkdir(parents=True)
data = b"an apk of some bytes" * 8
name = "scrcpy-win64-v5.0.1.zip"
(folder / name).write_bytes(data)
(folder / "sha256.json").write_text(json.dumps({name: [len(data), 1, hashlib.sha256(data).hexdigest()]}))
(holds := T / "holds" / "by-hand" / "index.json").write_text(
    json.dumps(
        {
            "id": "by-hand",
            "title": "x",
            "versions": [
                {
                    "tag": "v5.0.1",
                    "channel": "stable",
                    "published": "p",
                    "files": [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
                }
            ],
        }
    )
)
snap = appstore_admin.snapshot()
one = next(h for h in snap["holders"] if h["id"] == "by-hand")
check(
    "the kept versions and their bytes come off the disk",
    one["bytes"] == len(data) and one["kept"][0]["tag"] == "v5.0.1",
    one,
)
(folder / "gone.zip").write_bytes(b"x")
one2 = next(h for h in appstore_admin.snapshot()["holders"] if h["id"] == "by-hand")
check(
    "  a file with no sha256.json entry is not counted",
    one2["bytes"] == len(data) and all(f["name"] != "gone.zip" for v in one2["kept"] for f in v["files"]),
)
post({"action": "remove-files", "id": "by-hand"})
check(
    "remove-files clears the folder, keeps the holder",
    not folder.exists() and any(h["id"] == "by-hand" for h in appstore_admin.snapshot()["holders"]),
)
post({"action": "remove-holder", "id": "by-hand"})
check(
    "remove takes the settings and the folder",
    not any(h["id"] == "by-hand" for h in appstore_admin.snapshot()["holders"])
    and not (T / "holds" / "by-hand").exists(),
)

print("ok" if not fails else f"{fails} failures")
sys.exit(1 if fails else 0)
