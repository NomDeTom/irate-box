# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The App store's keeper (holder.py), offline: GitHub's release list is a recorded one
(tests/fixtures/github-releases-meshtastic-android.json, read 2026-10-27) or built here, and the downloads are
files made here.
  order        newest first by the date published, not by the numbers in the tags
  patterns     a Dart pattern as Python reads it; the ones the box cannot read are refused in words
  settings     a holder is validated; GitHub is the one source for now
  plan         the recipe's pattern keeps v2.8.3..v2.7.14 and passes over v2.7.13 and older; a second hold for
               the older names takes them in; `snapshot`, drafts, revoked and too-young releases never count;
               a channel by title; the budget
  sync         files checked against GitHub's digest; a second run downloads nothing; the oldest version and
               its files go when a newer one pushes it out; a file with no digest, or the wrong one, is left out
               and said; the network failing stops the run
  librarian    the policy pair, the scheduled run and the command
python3 tests/sim_holder.py"""

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="holder-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_HOLDS_ROOT=str(T / "holds"))
sys.path.insert(0, str(REPO))
from irate_box.library import holder, librarian
from irate_box.library.librarian import LibrarianError
from irate_box.msg import held

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(call, message_id):
    try:
        call()
    except LibrarianError as exc:
        return any(m["id"] == message_id for m in held(exc.message))
    return False


# --- order ---
dated = [
    {"tag": "v2.7.9", "published": "2025-12-11T00:00:00Z"},
    {"tag": "v2.7.14", "published": "2026-06-03T00:00:00Z"},
    {"tag": "nightly", "published": None},
    {"tag": "v2.7.10", "published": "2025-12-27T00:00:00Z"},
    {"tag": "v2.8.0", "published": "2026-07-29T00:00:00Z"},
]
check(
    "newest first, by the date published; one with no date last",
    [r["tag"] for r in holder.newest_first(dated)] == ["v2.8.0", "v2.7.14", "v2.7.10", "v2.7.9", "nightly"],
)
hashed = [
    {"tag": "v2.8.0.47db0e3", "published": "2026-08-02T00:00:00Z"},
    {"tag": "v2.8.0.7239fe8", "published": "2026-07-30T00:00:00Z"},
]
check(
    "  the same numbers and a commit hash after them: the date decides, not the hash's digits",
    [r["tag"] for r in holder.newest_first(hashed)] == ["v2.8.0.47db0e3", "v2.8.0.7239fe8"],
)

# --- patterns ---
check(
    "a Dart named group becomes Python's", holder.translate_regex("(?<ver>\\d+)-\\k<ver>") == "(?P<ver>\\d+)-(?P=ver)"
)
check("  a lookbehind is left alone", holder.translate_regex("(?<=a)b") == "(?<=a)b")
check(
    "  Obtainium's pattern for Meshtastic is read as it is",
    holder.translate_regex("fdroid-.*-release\\.apk$") == "fdroid-.*-release\\.apk$",
)
check(
    "a \\p{L} class is refused, in words",
    refused(lambda: holder.translate_regex("\\p{L}+"), "library.holder.pattern-classes"),
)
check("  an unbalanced one", refused(lambda: holder.translate_regex("(abc"), "library.holder.pattern-invalid"))
check("  an empty one", refused(lambda: holder.translate_regex(""), "library.holder.pattern-empty"))
check("  one too long", refused(lambda: holder.translate_regex("a" * 201), "library.holder.pattern-long"))

# --- settings ---
good = {
    "id": "meshtastic-android",
    "repo": "meshtastic/Meshtastic-Android",
    "holds": [{"label": "F-Droid", "include": "fdroid-.*-release\\.apk$"}],
}
holder_ok = holder.validate_holder(good)
check(
    "a holder gets the defaults: two stable, no pre-release, public",
    holder_ok["channels"] == {"stable": 2, "pre-release": 0}
    and holder_ok["visibility"] == "public"
    and holder_ok["provider"] == "github",
    holder_ok,
)
check(
    "  a bad name is refused",
    refused(lambda: holder.validate_holder({**good, "id": "../x"}), "library.holder.id-invalid"),
)
check(
    "  a bad repository",
    refused(lambda: holder.validate_holder({**good, "repo": "nope"}), "library.holder.repo-invalid"),
)
check(
    "  GitLab not yet",
    refused(lambda: holder.validate_holder({**good, "provider": "gitlab"}), "library.holder.provider-invalid"),
)
check(
    "  six of a channel",
    refused(lambda: holder.validate_holder({**good, "channels": {"stable": 6}}), "library.holder.count-invalid"),
)
check(
    "  a hold's pattern is checked",
    refused(lambda: holder.validate_holder({**good, "holds": [{"include": "("}]}), "library.holder.pattern-invalid"),
)
check(
    "  an extra channel may not take a built-in name",
    refused(
        lambda: holder.validate_holder({**good, "extra_channels": [{"name": "stable", "pattern": "x", "keep": 1}]}),
        "library.holder.extras-repeated",
    ),
)
check(
    "  visibility is public or members",
    refused(lambda: holder.validate_holder({**good, "visibility": "me"}), "library.holder.visibility-invalid"),
)
check("off until switched on", holder.settings()["enabled"] is False and holder.active() is False)

# --- plan: the recorded Meshtastic Android list ---
recorded = json.loads((REPO / "tests/fixtures/github-releases-meshtastic-android.json").read_text())
releases = [holder._normalise(raw) for raw in recorded]
recipe = {"label": "F-Droid", "include": "fdroid-.*-release\\.apk$"}
older = {"label": "F-Droid, older names", "include": "^app-fdroid-release\\.apk$"}


def tags(kept):
    return [entry["tag"] for entry in kept]


def mine(**changes):
    return holder.validate_holder({**good, "holds": [recipe], **changes})


kept = holder.plan(mine(channels={"stable": 2, "pre-release": 0}), releases)
check(
    "the recipe, two stable: v2.8.3 and v2.8.2, three files each",
    tags(kept) == ["v2.8.3", "v2.8.2"] and [len(e["files"]) for e in kept] == [3, 3],
    tags(kept),
)
check(
    "  the files are the three F-Droid APKs (no Google build, no bundle)",
    sorted(f["name"] for f in kept[0]["files"])
    == [
        "androidApp-fdroid-arm64-v8a-release.apk",
        "androidApp-fdroid-armeabi-v7a-release.apk",
        "androidApp-fdroid-universal-release.apk",
    ],
)
kept = holder.plan(mine(channels={"stable": 5, "pre-release": 0}), releases)
check(
    "  five stable: down to v2.7.14, with its five files",
    tags(kept) == ["v2.8.3", "v2.8.2", "v2.8.1", "v2.8.0", "v2.7.14"] and len(kept[-1]["files"]) == 5,
    tags(kept),
)
kept = holder.plan(mine(channels={"stable": 5, "pre-release": 0}), releases[:3])
check("  a release list without a match keeps what there is", tags(kept) == ["v2.8.3", "v2.8.2"], tags(kept))
without_newest = [r for r in releases if r["tag"] != "v2.8.3"]
two_names = holder.plan(mine(channels={"stable": 5, "pre-release": 0}, holds=[recipe, older]), without_newest)
check(
    "a second hold for the older names reaches past v2.7.14 (v2.7.13 has only app-fdroid-release.apk)",
    tags(two_names) == ["v2.8.2", "v2.8.1", "v2.8.0", "v2.7.14", "v2.7.13"],
    tags(two_names),
)
check(
    "  and holds the new names of v2.8.2 and the old one of v2.7.13",
    len(two_names[0]["files"]) == 3 and len(two_names[-1]["files"]) == 1,
)
check(
    "`snapshot` never counts, however many pre-releases are asked for",
    "snapshot"
    not in tags(holder.plan(mine(channels={"stable": 0, "pre-release": 5}, holds=[{"include": "."}]), releases)),
)
pre = holder.plan(mine(channels={"stable": 0, "pre-release": 1}, holds=[{"include": "."}]), releases)
check(
    "  one pre-release: the newest tagged one, v2.6.35",
    tags(pre) == ["v2.6.35"] and pre[0]["channel"] == "pre-release",
    tags(pre),
)
check(
    "  with its tag_pattern widened, `snapshot` is the newest",
    tags(
        holder.plan(mine(channels={"stable": 0, "pre-release": 1}, holds=[{"include": "."}], tag_pattern="."), releases)
    )
    == ["snapshot"],
)
check(
    "a hold's exclude: no universal APK",
    all(
        "universal" not in f["name"]
        for f in holder.plan(mine(holds=[{**recipe, "exclude": "universal"}]), releases)[0]["files"]
    ),
)
sided = {
    "assets": [
        {"name": "app.apk", "size": 10, "url": "", "digest": "sha256:" + "a" * 64},
        {"name": "app.apk.sha256", "size": 1, "url": "", "digest": "sha256:" + "b" * 64},
        {"name": "app.apk.sha256.sig", "size": 1, "url": "", "digest": "sha256:" + "c" * 64},
        {"name": "other.apk.sha256", "size": 1, "url": "", "digest": "sha256:" + "d" * 64},
    ]
}
plain = holder.held_files(sided, {"holds": [{"include": "\\.apk$"}]})
check("companions are not held unless asked", [a["name"] for a in plain] == ["app.apk"], plain)
sided_held = holder.held_files(sided, {"holds": [{"include": "\\.apk$"}], "companions": True})
check(
    "  asked, a held file's checksum and signature come too, saying whose they are",
    [(a["name"], a.get("of")) for a in sided_held]
    == [
        ("app.apk", None),
        ("app.apk.sha256", "app.apk"),
        ("app.apk.sha256.sig", "app.apk"),
    ],
    sided_held,
)
check("nothing chosen to hold: nothing kept", holder.plan(mine(holds=[]), releases) == [])
rec = {}
holder.plan(mine(channels={"stable": 5, "pre-release": 0}), releases, record=rec)
check(
    "the record: the newest release that counts, and whether it carries any held file",
    rec["newest"] == {"tag": "v2.8.3", "held": True} and rec["over_budget"] == [],
    rec,
)
rec = {}
holder.plan(mine(holds=[{"include": "\\.bin$"}]), releases, record=rec)
check("  a newest release with none of the held files says so", rec["newest"] == {"tag": "v2.8.3", "held": False}, rec)

# --- plan: built releases ---
now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def stamp(days_ago):
    return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def release(tag, days_ago, *, name=None, pre=False, draft=False, files=(("app.apk", 1 << 20),)):
    return {
        "tag": tag,
        "name": name if name is not None else tag,
        "published": stamp(days_ago),
        "prerelease": pre,
        "draft": draft,
        "notes": "",
        "assets": [
            {
                "name": n,
                "size": s,
                "url": f"https://example.invalid/{tag}/{n}",
                "digest": "sha256:" + hashlib.sha256(f"{tag}{n}".encode()).hexdigest(),
            }
            for n, s in files
        ],
    }


simple = holder.validate_holder(
    {**good, "holds": [{"include": "\\.apk$"}], "channels": {"stable": 3, "pre-release": 1}}
)
built = [
    release("v1.4.0", 1),
    release("v1.3.0", 10, draft=True),
    release("v1.2.0", 20, name="Release 1.2.0 (Revoked)"),
    release("v1.1.0", 30),
    release("v1.1.0-rc1", 25, pre=True),
    release("v1.0.0", 60, files=(("notes.txt", 10),)),
    release("v0.9.0", 90),
]
check(
    "a draft and a revoked release never count; one without a held file is passed over for an older one",
    tags(holder.plan(simple, built, now)) == ["v1.4.0", "v1.1.0-rc1", "v1.1.0", "v0.9.0"],
    tags(holder.plan(simple, built, now)),
)
young = holder.validate_holder({**simple, "min_age_days": 7})
check(
    "a release younger than min_age_days does not count yet; the older ones fill its place",
    tags(holder.plan(young, built, now))[0] == "v1.1.0-rc1",
    tags(holder.plan(young, built, now)),
)
extras = holder.validate_holder(
    {
        **simple,
        "channels": {"stable": 1, "pre-release": 0},
        "extra_channels": [{"name": "alpha", "pattern": "(?i)alpha", "keep": 2}],
    }
)
alphas = [
    release("v2.0.0", 2, name="Release 2.0.0 Alpha", pre=True),
    release("v2.0.1", 1, name="Release 2.0.1 Alpha", pre=True),
    release("v1.9.0", 3),
]
got = holder.plan(extras, alphas, now)
check(
    "a channel by title: two alphas and the newest stable",
    [(e["tag"], e["channel"]) for e in got] == [("v2.0.1", "alpha"), ("v2.0.0", "alpha"), ("v1.9.0", "stable")],
    got and tags(got),
)
sized = [
    release("v3.0", 1, files=(("a.apk", 6 << 20),)),
    release("v2.0", 2, files=(("a.apk", 6 << 20),)),
    release("v1.0", 3, files=(("a.apk", 6 << 20),)),
]
tight = holder.validate_holder({**simple, "channels": {"stable": 3, "pre-release": 0}, "budget_mb": 13})
check(
    "a budget: the oldest goes first",
    tags(holder.plan(tight, sized, now)) == ["v3.0", "v2.0"],
    tags(holder.plan(tight, sized, now)),
)
tiny = holder.validate_holder({**tight, "budget_mb": 1})
check("  but the newest of a channel always stays", tags(holder.plan(tiny, sized, now)) == ["v3.0"])
rec = {}
holder.plan(tight, sized, now, record=rec)
check("  and the record names the versions the budget dropped", rec["over_budget"] == ["v1.0"], rec)
check("a tag that cannot be a folder is passed over", holder.plan(simple, [release("v1/../2", 1)], now) == [])

# --- sync ---
served = {"releases": [], "downloads": [], "net": True}


def fake_api(path, auth=None):
    served["asked"] = path
    return [] if "page=2" in path else served["releases"]


def fake_download(url, dest, auth=None, name=None, expected=0, resume=False):
    if not served["net"]:
        raise LibrarianError(
            librarian.M(
                "library.download.unreachable", "Download failed. {host} cannot be reached.", host="example.invalid"
            )
        )
    served["downloads"].append(url)
    Path(dest).write_bytes(served["bodies"][url])


def raw_release(tag, days_ago, files, **kw):
    out = {
        "tag_name": tag,
        "name": tag,
        "published_at": stamp(days_ago),
        "prerelease": False,
        "draft": False,
        "body": f"notes for {tag}",
        "assets": [],
    }
    out.update(kw)
    for name, body, digest in files:
        url = f"https://example.invalid/{tag}/{name}"
        served["bodies"][url] = body
        out["assets"].append(
            {
                "name": name,
                "size": len(body),
                "browser_download_url": url,
                "digest": digest if digest != "ok" else "sha256:" + hashlib.sha256(body).hexdigest(),
            }
        )
    return out


librarian._api = fake_api
librarian._download = fake_download
served["bodies"] = {}
library_dir = T / "state" / "library"
library_dir.mkdir(parents=True)
live = holder.validate_holder(
    {
        **good,
        "id": "demo",
        "title": "Demo",
        "holds": [{"include": "\\.apk$"}],
        "channels": {"stable": 2, "pre-release": 0},
    }
)
holder.set_settings(enabled=True, holders=[live])
check("settings saved and read back", holder.active() and holder.settings()["holders"][0]["id"] == "demo")
served["releases"] = [
    raw_release(
        "v1.2.0",
        1,
        [
            ("app.apk", b"A" * 5000, "ok"),
            ("nodigest.apk", b"B" * 10, None),
            ("wrong.apk", b"C" * 10, "sha256:" + "0" * 64),
        ],
    ),
    raw_release("v1.1.0", 5, [("app.apk", b"D" * 4000, "ok")]),
    raw_release("v1.0.0", 9, [("app.apk", b"E" * 3000, "ok")]),
]
outcome = holder.sync(log=lambda *_: None)
folder = T / "holds" / "demo"
index = json.loads((folder / "index.json").read_text())
check("first run: two versions held, and said", outcome.startswith("demo: 2 versions"), outcome)
check(
    "  newest first, with channel, date, notes and the files with their SHA-256",
    [v["tag"] for v in index["versions"]] == ["v1.2.0", "v1.1.0"]
    and index["versions"][0]["notes"] == "notes for v1.2.0"
    and index["versions"][0]["channel"] == "stable",
)
check(
    "  a file with no digest and one with the wrong digest are left out, the right one held",
    [f["name"] for f in index["versions"][0]["files"]] == ["app.apk"]
    and (folder / "v1.2.0" / "app.apk").read_bytes() == b"A" * 5000
    and not (folder / "v1.2.0" / "wrong.apk").exists()
    and not list(folder.glob("*/*.part")),
)
check(
    "  the hash in the index is the file's",
    index["versions"][0]["files"][0]["sha256"] == hashlib.sha256(b"A" * 5000).hexdigest(),
)
check("  the older version's folder is not there", not (folder / "v1.0.0").exists())
check("  index.json is world-readable", (folder / "index.json").stat().st_mode & 0o004)
downloads_before = len(served["downloads"])
holder.sync(log=lambda *_: None)
check("a second run downloads nothing", len(served["downloads"]) == downloads_before)
check(
    "  the file that did not match is remembered, and so is why it is not fetched",
    list(json.loads((folder / "v1.2.0" / "refused.json").read_text())) == ["wrong.apk"],
)
wrong_url = "https://example.invalid/v1.2.0/wrong.apk"
check("  it was tried once", served["downloads"].count(wrong_url) == 1, served["downloads"])
(folder / "v1.2.0" / "refused.json").write_text(json.dumps({"wrong.apk": ["0" * 64, 0]}))
holder.sync(log=lambda *_: None)
check("  and tried again once a day has passed", served["downloads"].count(wrong_url) == 2)
(folder / "v1.1.0" / "app.apk").write_bytes(b"tampered, same length?")
holder.sync(log=lambda *_: None)
check("  a held file changed on the card is fetched again", (folder / "v1.1.0" / "app.apk").read_bytes() == b"D" * 4000)
served["releases"].insert(0, raw_release("v1.3.0", 0, [("app.apk", b"F" * 2000, "ok")]))
holder.sync(log=lambda *_: None)
check(
    "a newer release pushes the oldest out, files and all",
    sorted(p.name for p in folder.iterdir() if p.is_dir()) == ["v1.2.0", "v1.3.0"]
    and json.loads((folder / "index.json").read_text())["versions"][0]["tag"] == "v1.3.0",
)
(folder / "v1.3.0" / "stray.bin").write_bytes(b"x")
holder.sync(log=lambda *_: None)
check("  a file in a kept folder that nothing holds is removed", not (folder / "v1.3.0" / "stray.bin").exists())
status = json.loads((folder / "status.json").read_text())
check(
    "status says when it was checked and what it kept",
    status["last_check"] and status["versions"] == {"v1.3.0": "stable", "v1.2.0": "stable"} and "error" not in status,
    status,
)
check(
    "  the newest is recorded, nothing dropped and nothing changed",
    status["newest"] == {"tag": "v1.3.0", "held": True} and status["over_budget"] == [] and status["changed"] == [],
    status,
)
old_sha = hashlib.sha256(b"F" * 2000).hexdigest()
served["releases"][0] = raw_release("v1.3.0", 0, [("app.apk", b"F" * 2001, "ok")])
holder.sync(log=lambda *_: None)
st2 = json.loads((folder / "status.json").read_text())
check(
    "GitHub giving a different digest for a kept file: fetched again and said",
    st2["changed"]
    == [{"tag": "v1.3.0", "name": "app.apk", "was": old_sha[:12], "now": hashlib.sha256(b"F" * 2001).hexdigest()[:12]}]
    and (folder / "v1.3.0" / "app.apk").read_bytes() == b"F" * 2001,
    st2.get("changed"),
)
holder.sync(log=lambda *_: None)
check(
    "  the next run still says it, while that version is kept",
    json.loads((folder / "status.json").read_text())["changed"] == st2["changed"],
)
st3 = json.loads((folder / "status.json").read_text())
st3["changed"].append({"tag": "v0.9.0", "name": "app.apk", "was": "a" * 12, "now": "b" * 12})
(folder / "status.json").write_text(json.dumps(st3))
holder.sync(log=lambda *_: None)
check(
    "  and not once the version has gone",
    [change["tag"] for change in json.loads((folder / "status.json").read_text())["changed"]] == ["v1.3.0"],
)
served["net"] = False
served["releases"].insert(0, raw_release("v1.4.0", 0, [("app.apk", b"G" * 100, "ok")]))
outcome = holder.sync(log=lambda *_: None)
check(
    "the network failing stops the run, said, and the kept versions are untouched",
    outcome.startswith("demo: error:")
    and (folder / "v1.3.0" / "app.apk").exists()
    and "error" in json.loads((folder / "status.json").read_text()),
    outcome,
)
served["net"] = True
check(
    "--check records the newest and downloads nothing",
    holder.sync(check_only=True, log=lambda *_: None).startswith("demo: newest: v1.4.0 (stable), v1.3.0")
    and not list((folder / "v1.4.0").glob("*.apk")),
)
free_before = librarian._free_bytes
librarian._free_bytes = lambda path: 100
outcome = holder.sync(log=lambda *_: None)
librarian._free_bytes = free_before
check(
    "too little room: stopped, said, and the old versions stay",
    "Insufficient storage" in outcome and (folder / "v1.3.0" / "app.apk").exists(),
    outcome,
)
nothing = holder.validate_holder({**live, "id": "empty", "holds": []})
check(
    "a holder with nothing chosen to hold fetches nothing",
    holder.sync_holder(nothing, log=lambda *_: None) == "nothing chosen to hold",
)
holder.remove_files("demo")
check("removing a holder's files takes the folder", not folder.exists())
check(
    "  and a name that is not a name takes nothing",
    (holder.remove_files("../holds") or True) and (T / "holds").exists(),
)

# --- librarian ---
policy = librarian.load_config()["policy"]
check(
    "the policy pair: daily, and it fetches", (policy["holders_every_hours"], policy["holders_auto"]) == (24, 1), policy
)
check("  holders_auto 2 is refused", refused(lambda: librarian.set_policy(holders_auto=2), "library.policy.invalid"))
librarian.set_policy(holders_every_hours=6)
check("  and 6 hours is kept", librarian.load_config()["policy"]["holders_every_hours"] == 6)
served["releases"] = [raw_release("v2.0.0", 0, [("app.apk", b"H" * 100, "ok")])]
holder.set_settings(holders=[live])
results = librarian.update(names=["holders"], log=lambda *_: None)
check(
    "librarian.update runs the holders",
    "holders" in results and results["holders"].startswith("demo: 1 versions"),
    results,
)
holder.set_settings(enabled=False)
check("  and not when off", "holders" not in librarian.update(names=["holders"], log=lambda *_: None))
holder.set_settings(enabled=True)
check(
    "  a scheduled run not yet due leaves them alone",
    "holders" not in librarian.update(names=["holders"], scheduled=True, log=lambda *_: None),
)
check("the command: `holders --check`", librarian.main(["holders", "--check", "demo"]) == 0)

print(f"{fails} failures" if fails else "ok")
sys.exit(1 if fails else 0)
