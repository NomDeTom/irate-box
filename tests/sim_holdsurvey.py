# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Looking at a project's releases before holding anything (holdsurvey.py), offline, on the release lists of
five GitHub projects recorded 2026-10-27 (tests/fixtures/github-releases-*.json): Meshtastic Android, Obtainium,
meshtastic/firmware, Syncthing and scrcpy.
  shapes       the version, a build counter and a commit hash taken out of a name, a pre-release suffix however
               the file spells it
  kinds        the files grouped; checksums, signatures and update helpers attached to their file
  tags         operating system, architecture, flavour from the names; what else varies, as a variant
  eras         Meshtastic Android's two naming eras found, and not two files of one release
  twins        Obtainium's pairs of identical APKs
  holds        what the holds take; a tick turned into a hold and back; a pattern of the admin's own; the releases
               a list matches nothing in; the size of what is held
  recipe       Tom's Obtainium recipe for Meshtastic Android as the settings of a holder
python3 tests/sim_holdsurvey.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="holdsurvey-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_HOLDS_ROOT=str(T / "holds"))
sys.path.insert(0, str(REPO))
from irate_box.library import holder, holdsurvey
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


def load(name):
    return [
        holder._normalise(raw) for raw in json.loads((REPO / f"tests/fixtures/github-releases-{name}.json").read_text())
    ]


def shapes(result):
    return {kind["shape"]: kind for kind in result["kinds"]}


# --- shapes ---
check(
    "the tag, with or without its v",
    holdsurvey.shape("app-2.8.3.apk", "v2.8.3") == "app-{v}.apk"
    and holdsurvey.shape("app-v2.8.3.apk", "v2.8.3") == "app-{v}.apk",
)
check(
    "  a pre-release suffix the file spells another way",
    holdsurvey.shape("syncthing_2.1.7.rc.4_amd64.deb", "v2.1.7-rc.4") == "syncthing_{v}_amd64.deb",
)
check(
    "  a version that is not the tag's (a rolling `snapshot` tag)",
    holdsurvey.shape("meshtastic-desktop_2.8.4_amd64-29322823.deb", "snapshot")
    == "meshtastic-desktop_{v}_amd64-{build}.deb",
)
check(
    "  the commit hash the tag ends in",
    holdsurvey.shape("meshtasticd-2.7.26.54e0d8d.local54e0d8d-src.zip", "v2.7.26.54e0d8d")
    == "meshtasticd-{v}.local{sha}-src.zip",
)
check(
    "  a build counter after the version",
    holdsurvey.shape("fdroidRelease-2.6.35-35131.apk", "v2.6.35") == "fdroidRelease-{v}-{build}.apk",
)
check(
    "  a name with nothing to take out is left alone",
    holdsurvey.shape("androidApp-fdroid-arm64-v8a-release.apk", "v2.8.3") == "androidApp-fdroid-arm64-v8a-release.apk",
)
check(
    "type classes",
    [
        holdsurvey.type_class(n)
        for n in (
            "a.apk",
            "a.aab",
            "a.deb",
            "a.AppImage",
            "a.tar.gz",
            "a.sha256",
            "a.idsig",
            "a.png",
            "a.json",
            "a",
            "SHA256SUMS.txt",
        )
    ]
    == [
        "phone app",
        "phone bundle",
        "installer",
        "portable",
        "archive",
        "checksum or signature",
        "companion",
        "image",
        "data",
        "other",
        "checksum or signature",
    ],
)
check(
    "a signature of a checksum of a file belongs to the file",
    holdsurvey.parent_of("x.apk.sha256.sig", {"x.apk", "x.apk.sha256", "x.apk.sha256.sig"}) == "x.apk",
)
check("  one whose file is not there belongs to none", holdsurvey.parent_of("x.apk.sig", {"x.apk.sig"}) is None)
check("  a file is its own, whatever its ending", holdsurvey.parent_of("x.apk", {"x.apk"}) is None)

# --- Meshtastic Android ---
android = holdsurvey.survey(load("meshtastic-android"), 8)
kinds = shapes(android)
check(
    "looks at eight stable and eight pre-releases; `snapshot` is not one",
    android["window"]["stable"][0] == "v2.8.3"
    and len(android["window"]["stable"]) == 8
    and "snapshot" not in android["window"]["pre-release"],
    android["window"],
)
check("the survey is JSON", bool(json.dumps(android)))
check(
    "  the F-Droid arm64 APK is one kind, in five releases, 17.9-34.1 MB",
    len(kinds["androidApp-fdroid-arm64-v8a-release.apk"]["releases"]) == 5
    and kinds["androidApp-fdroid-arm64-v8a-release.apk"]["class"] == "phone app"
    and round(kinds["androidApp-fdroid-arm64-v8a-release.apk"]["size_min"] / 1e6, 1) == 17.9,
)
check("  the older name is another kind, in three", len(kinds["app-fdroid-release.apk"]["releases"]) == 3)
check("  every file has a digest from GitHub", all(kind["digests"] == kind["files"] for kind in android["kinds"]))
check("  the mapping file's run number is a build counter", "mapping-google-{build}.txt.gz" in kinds)
check(
    "architectures, operating systems and flavours read from the names",
    {"arm64-v8a", "armeabi-v7a", "universal", "x86", "x86_64"} <= set(android["dimensions"]["architecture"])
    and {"fdroid", "google"} <= set(android["dimensions"]["flavour"]),
)
check(
    "  a kind's own tags",
    kinds["androidApp-fdroid-arm64-v8a-release.apk"]["tags"]
    == {"architecture": ["arm64-v8a"], "flavour": ["fdroid", "release"]},
)
pairs = {(pair["older"], pair["newer"]) for pair in android["eras"]}
check(
    "the eras: app-fdroid-release.apk became androidApp-fdroid-universal-release.apk",
    ("app-fdroid-release.apk", "androidApp-fdroid-universal-release.apk") in pairs,
    sorted(pairs),
)
check(
    "  and app-google-release.apk became androidApp-google-release.apk",
    ("app-google-release.apk", "androidApp-google-release.apk") in pairs,
)
check(
    "  two kinds of one release are not an era (x86 and universal)",
    not any(
        {"androidApp-fdroid-x86-release.apk", "androidApp-fdroid-universal-release.apk"} == {a, b} for a, b in pairs
    ),
)
check("  the pre-releases' build counter is one", ("fdroidRelease-{v}.apk", "fdroidRelease-{v}-{build}.apk") in pairs)
check(
    "what the desktop app adds is installers, not phone apps",
    kinds["Meshtastic.Desktop-{v}.exe"]["class"] == "installer"
    and kinds["Meshtastic_Desktop-{v}-x86_64.AppImage"]["class"] == "portable",
)

# --- Obtainium: companions and twins ---
obtainium = holdsurvey.survey(load("obtainium"), 4)
okinds = shapes(obtainium)
check(
    "a checksum, its signature and the .idsig are the APK's, not kinds of their own",
    {kind["class"] for kind in obtainium["kinds"]} == {"phone app"} and len(obtainium["kinds"]) == 8,
    sorted({kind["class"] for kind in obtainium["kinds"]}),
)
check(
    "  the APK says what it comes with",
    okinds["app-arm64-v8a-release.apk"]["companions"]
    == ["app-arm64-v8a-release.apk.idsig", "app-arm64-v8a-release.apk.sha256", "app-arm64-v8a-release.apk.sha256.sig"],
    okinds["app-arm64-v8a-release.apk"]["companions"],
)
twin_pairs = {frozenset((pair["first"], pair["second"])) for pair in obtainium["twins"]}
check(
    "twins: app-release.apk and app-fdroid-release.apk are the same size in every release",
    frozenset(("app-release.apk", "app-fdroid-release.apk")) in twin_pairs,
    twin_pairs,
)
check(
    "  and the architectures are not twins of each other",
    frozenset(("app-arm64-v8a-release.apk", "app-x86_64-release.apk")) not in twin_pairs,
)

# --- meshtastic/firmware: commit hashes, a channel with few releases ---
firmware = holdsurvey.survey(load("meshtastic-firmware"), 4)
fkinds = shapes(firmware)
check(
    "the source zip with the commit in its name is one kind across releases",
    "meshtasticd-{v}.local{sha}-src.zip" in fkinds,
)
check(
    "  there are fewer stable releases than asked for, and it says so", "stable" in firmware["short"], firmware["short"]
)
check(
    "  the platforms are variants (esp32, nrf52840, rp2040…)",
    {"esp32", "esp32s3", "nrf52840", "rp2040"} <= {word for kind in firmware["kinds"] for word in kind["variant"]},
)
check(
    "  the debug zips are archives of the same kind as the firmware zips, in name only",
    fkinds["debug-elfs-esp32-{v}.zip"]["class"] == "archive",
)

# --- Syncthing: pre-release suffixes, combined checksums ---
syncthing = holdsurvey.survey(load("syncthing"), 4)
skinds = shapes(syncthing)
check(
    "the .deb of a release candidate and of a release are one kind",
    "syncthing_{v}_amd64.deb" in skinds and not any(".rc." in shape for shape in skinds),
    [shape for shape in skinds if ".rc." in shape],
)
check(
    "  operating systems and architectures found without a table's help (386, mips64le, s390x)",
    {"mips64le", "s390x", "386", "loong64"} <= set(syncthing["dimensions"]["architecture"]),
    syncthing["dimensions"],
)
check(
    "  the signed checksum lists are kinds of their own, in their class",
    skinds["sha256sum.txt.asc"]["class"] == "checksum or signature",
)

# --- scrcpy: one list of checksums for the release ---
scrcpy = holdsurvey.survey(load("scrcpy"), 4)
check(
    "a SHA256SUMS.txt for the whole release is a kind, and its .asc is attached to it",
    "SHA256SUMS.txt" in shapes(scrcpy) and shapes(scrcpy)["SHA256SUMS.txt"]["companions"] == ["SHA256SUMS.txt.asc"],
    shapes(scrcpy).get("SHA256SUMS.txt", {}).get("companions"),
)


# --- variants and eras, built ---
def built(tag, days, names, **flags):
    return {
        "tag": tag,
        "name": tag,
        "published": f"2026-01-{days:02d}T00:00:00Z",
        "prerelease": False,
        "draft": False,
        "notes": "",
        "assets": [{"name": n, "size": s, "url": "https://example.invalid/" + n, "digest": None} for n, s in names],
        **flags,
    }


mine = holdsurvey.survey(
    [
        built("v1.1.0", 20, [("tool-foo-1.1.0.zip", 10), ("tool-bar-1.1.0.zip", 10)]),
        built("v1.0.0", 10, [("tool-foo-1.0.0.zip", 10), ("tool-bar-1.0.0.zip", 10)]),
    ],
    2,
)
check(
    "kinds that differ in one word of the name, in one release, are variants of it",
    sorted(word for kind in mine["kinds"] for word in kind["variant"]) == ["bar", "foo"],
    [k["variant"] for k in mine["kinds"]],
)
renamed = holdsurvey.survey(
    [built("v2.0.0", 20, [("tool2-2.0.0.zip", 10)]), built("v1.0.0", 10, [("tool-1.0.0.zip", 10)])], 2
)
check(
    "  one word that changed between releases is an era, not a variant",
    renamed["eras"] and not any(kind["variant"] for kind in renamed["kinds"]),
    renamed,
)
check(
    "a draft and a tag that is not a version are not looked at",
    holdsurvey.survey([built("v1.0.0", 1, [("a.zip", 1)], draft=True), built("nightly", 2, [("a.zip", 1)])], 2)["kinds"]
    == [],
)

# --- the holds model ---
chosen = holdsurvey.window(load("meshtastic-android"), 8)
recipe_hold = {"label": "Obtainium's filter", "include": "fdroid-.*-release\\.apk$"}
older_hold = {"label": "older names", "include": "^app-fdroid-release\\.apk$"}
check(
    "a kind's pattern takes that kind and no other",
    holdsurvey.kind_pattern("androidApp-fdroid-arm64-v8a-release.apk") == "^androidApp-fdroid-arm64-v8a-release\\.apk$"
    and not holder.held_files(
        {"assets": [{"name": "androidApp-fdroid-universal-release.apk"}]},
        {"holds": [{"include": holdsurvey.kind_pattern("androidApp-fdroid-arm64-v8a-release.apk")}]},
    ),
)
check(
    "  with a {v} in it, any version of the name",
    len(
        holder.held_files(
            {
                "assets": [
                    {"name": n}
                    for n in (
                        "syncthing-linux-amd64-v2.1.7.tar.gz",
                        "syncthing-linux-amd64-v2.1.7-rc.4.tar.gz",
                        "syncthing-linux-arm64-v2.1.7.tar.gz",
                    )
                ]
            },
            {"holds": [{"include": holdsurvey.kind_pattern("syncthing-linux-amd64-{v}.tar.gz")}]},
        )
    )
    == 2,
)
states = holdsurvey.held_states(android, [recipe_hold])
check(
    "the recipe takes the F-Droid APKs in every release they are in, and not the older name",
    states["androidApp-fdroid-arm64-v8a-release.apk"] == "all"
    and states["app-fdroid-release.apk"] == "none"
    and states["androidApp-google-release.apk"] == "none",
    states["app-fdroid-release.apk"],
)
check(
    "  with the older pattern too, that is taken as well",
    holdsurvey.held_states(android, [recipe_hold, older_hold])["app-fdroid-release.apk"] == "all",
)
check(
    "  a pattern that takes one release's file of a kind and not another's is 'some'",
    holdsurvey.held_states(mine, [{"include": "foo-1\\.1\\.0"}])["tool-foo-{v}.zip"] == "some",
)
own = holdsurvey.own_patterns(android, [recipe_hold])
check(
    "the recipe's pattern is the admin's own, not a tick, and says how many files it takes (17)",
    own == [{"label": "Obtainium's filter", "include": recipe_hold["include"], "files": 17}],
    own,
)
ticked, note = holdsurvey.with_kind(android, [], "app-fdroid-release.apk", True)
check(
    "ticking a kind adds its pattern, and it is a tick",
    ticked == [{"label": "app-fdroid-release.apk", "include": "^app-fdroid-release\\.apk$"}]
    and note is None
    and holdsurvey.own_patterns(android, ticked) == [],
)
check(
    "  ticking it again changes nothing",
    holdsurvey.with_kind(android, ticked, "app-fdroid-release.apk", True)[0] == ticked,
)
check("  unticking takes it away", holdsurvey.with_kind(android, ticked, "app-fdroid-release.apk", False) == ([], None))
check(
    "  a kind the recipe already takes is not added",
    holdsurvey.with_kind(android, [recipe_hold], "androidApp-fdroid-arm64-v8a-release.apk", True)[0] == [recipe_hold],
)
kept_holds, why = holdsurvey.with_kind(android, [recipe_hold], "androidApp-fdroid-arm64-v8a-release.apk", False)
check(
    "  unticking one the admin's own pattern takes leaves it, and says why",
    kept_holds == [recipe_hold]
    and why
    and why["id"] == "library.holder.held-by-pattern"
    and why["args"]["pattern"] == recipe_hold["include"],
    why,
)
shown = {entry["tag"]: entry for entry in holdsurvey.preview(chosen, [recipe_hold])}
check(
    "the preview: v2.8.3 gives three files, v2.7.14 five, v2.7.13 none",
    len(shown["v2.8.3"]["files"]) == 3
    and len(shown["v2.7.14"]["files"]) == 5
    and shown["v2.7.13"]["none"]
    and shown["v2.7.13"]["channel"] == "stable",
)
check(
    "  with the older pattern added, v2.7.13 has its file",
    not {e["tag"]: e for e in holdsurvey.preview(chosen, [recipe_hold, older_hold])}["v2.7.13"]["none"],
)
size = holdsurvey.estimate(chosen, [recipe_hold], 2)
v283 = {
    asset["name"]: asset["size"]
    for asset in next(r for r in load("meshtastic-android") if r["tag"] == "v2.8.3")["assets"]
}
expected = sum(
    v283[n]
    for n in (
        "androidApp-fdroid-arm64-v8a-release.apk",
        "androidApp-fdroid-armeabi-v7a-release.apk",
        "androidApp-fdroid-universal-release.apk",
    )
)
check(
    "the size: the three files of v2.8.3, and twice that for two kept",
    size == {"tag": "v2.8.3", "per_version": expected, "total": 2 * expected},
    size,
)
check(
    "  nothing held, nothing to size", holdsurvey.estimate(chosen, [], 2) == {"tag": None, "per_version": 0, "total": 0}
)
check(
    "the text report names a kind and an era",
    "androidApp-fdroid-arm64-v8a-release.apk" in holdsurvey.report(android) and "era?" in holdsurvey.report(android),
)

# --- Tom's recipe ---
tom = '{"id":"com.geeksville.mesh","url":"https://github.com/meshtastic/Meshtastic-Android","author":"meshtastic","name":"Meshtastic","additionalSettings":"{\\"apkFilterRegEx\\":\\"fdroid-.*-release\\\\\\\\.apk$\\",\\"autoApkFilterByArch\\":true,\\"appName\\":\\"Meshtastic\\",\\"appAuthor\\":\\"meshtastic\\",\\"about\\":\\"The official app for Meshtastic, an open-source, off-grid, mesh radio.\\"}"}'
parsed = holdsurvey.parse_recipe(tom)
check(
    "the recipe: repository, title, the filter as a hold",
    parsed["repo"] == "meshtastic/Meshtastic-Android"
    and parsed["title"] == "Meshtastic"
    and parsed["holds"] == [{"label": "Obtainium's filter", "include": "fdroid-.*-release\\.apk$"}],
    parsed,
)
check(
    "  it keeps no pre-releases, and the one setting the box does not use is said",
    parsed["channels"] == {"stable": 2, "pre-release": 0}
    and [n["args"]["setting"] for n in parsed["notes"]] == ["autoApkFilterByArch"],
    parsed["notes"],
)
check(
    "  the holds make a holder",
    holder.validate_holder({"id": "meshtastic-android", "repo": parsed["repo"], "holds": parsed["holds"]})["holds"]
    == parsed["holds"],
)
check(
    "  and the plan on the recorded releases is the one the keeper tests expect",
    [
        e["tag"]
        for e in holder.plan(
            holder.validate_holder({"id": "x", "repo": parsed["repo"], "holds": parsed["holds"]}),
            load("meshtastic-android"),
        )
    ]
    == ["v2.8.3", "v2.8.2"],
)
inverted = holdsurvey.parse_recipe(
    json.dumps(
        {
            "url": "https://github.com/a/b",
            "additionalSettings": json.dumps(
                {"apkFilterRegEx": "debug", "invertAPKFilter": True, "includePrereleases": True}
            ),
        }
    )
)
check(
    "an inverted filter holds everything but what it names; pre-releases asked for are one kept",
    inverted["holds"] == [{"label": "Everything but the filter", "include": ".", "exclude": "debug"}]
    and inverted["channels"]["pre-release"] == 1,
)
check(
    "no filter: the phone apps",
    "apk" in holdsurvey.parse_recipe(json.dumps({"url": "https://github.com/a/b"}))["holds"][0]["include"],
)
import urllib.parse

check(
    "a link carrying the recipe",
    holdsurvey.parse_recipe("obtainium://app/" + urllib.parse.quote(tom))["repo"] == "meshtastic/Meshtastic-Android",
)
check("not JSON: refused, in words", refused(lambda: holdsurvey.parse_recipe("hello"), "library.holder.recipe-invalid"))
check(
    "  not GitHub: said so",
    refused(
        lambda: holdsurvey.parse_recipe(json.dumps({"url": "https://gitlab.com/a/b"})),
        "library.holder.recipe-not-github",
    ),
)

# --- the presets shipped in holders/ ---
obtainium_preset = holdsurvey.find_preset("obtainium")
newest_obtainium = holder.newest_first([r for r in load("obtainium") if not r["prerelease"]])[0]
check(
    "the Obtainium preset holds the plain build's two phone APKs, not the fdroid ones",
    sorted(a["name"] for a in holder.held_files(newest_obtainium, {"holds": obtainium_preset["holds"]}))
    == ["app-arm64-v8a-release.apk", "app-armeabi-v7a-release.apk"],
    [a["name"] for a in holder.held_files(newest_obtainium, {"holds": obtainium_preset["holds"]})],
)
android_preset = holdsurvey.find_preset("meshtastic-android")
check(
    "the Meshtastic Android preset holds the Play Store build, under its new name and its old one",
    {
        a["name"]
        for release in load("meshtastic-android")
        for a in holder.held_files(release, {"holds": android_preset["holds"]})
    }
    == {"androidApp-google-release.apk", "app-google-release.apk"},
)

print(f"{fails} failures" if fails else "ok")
sys.exit(1 if fails else 0)
