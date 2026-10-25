# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""How far an update must be vouched for (root/signing.py; the level chosen in the
update manager), against throwaway repositories: the owner's keys
checked line by line; signed releases accepted only with a trusted SSH key, the newest tag
followed; GitHub's level accepting commits signed by its key and refusing an unsigned push (a
key of our own standing in for GitHub's; skipped where gpg is missing); the root helper's check
and Install anyway refusing a version not signed as chosen. python3 tests/sim_signing.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="signing-"))
STATE, ETC, CODE = T / "state", T / "etc", T / "code"
for d in (STATE / "control" / "results", STATE / "control" / "requests", ETC, CODE / "config" / "signing"):
    d.mkdir(parents=True)
os.environ.update(
    HUB_STATE_DIR=str(STATE),
    HUB_ETC_DIR=str(ETC),
    HUB_RUN_DIR=str(T / "run"),
    HUB_CODE_DIR=str(CODE),
    HUB_UPDATE_DIR=str(T / "cache" / "src"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import M
from irate_box.root import hub_control, signing

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def git(repo, *args, env=None):
    e = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@x",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@x",
        **(env or {}),
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=e, check=True
    ).stdout.strip()


def sshkey(name):
    k = T / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)], check=True)
    return k, (T / f"{name}.pub").read_text().strip()


# --- the owner's keys, line by line ---
k_tom, pub_tom = sshkey("laptop")
k_eve, pub_eve = sshkey("eve")
line_tom = f"owner@box {pub_tom}"
check("a key line as ssh-keygen -Y reads it", signing.parse_signers(f"# mine\n{line_tom}\n\n") == [line_tom])
for bad in ("ssh-ed25519 AAAA", "owner@box ssh-dss AAAA", "owner@box ssh-ed25519 not*base64"):
    try:
        signing.parse_signers(bad)
        ok = False
    except ValueError as exc:
        ok = "Line 1" in str(exc)
    check(f"refused, with its line: {bad[:30]}", ok)
try:
    signing.save(ETC, "tags", "", lambda p, s: None)
    ok = False
except ValueError:
    ok = True
check("signed releases with no key: refused", ok)

# --- signed releases ---
src = T / "src"
src.mkdir()
git(src, "init", "-q", "-b", "main")
(src / "a").write_text("1")
git(src, "add", "a")
git(src, "commit", "-q", "-m", "one")
signed = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={k_tom}"]
git(src, *signed, "tag", "-s", "v1.9", "-m", "1.9")
(src / "a").write_text("2")
git(src, "commit", "-qam", "two")
git(src, *signed, "tag", "-s", "v1.10", "-m", "1.10")
git(src, "tag", "-a", "v1.11-unsigned", "-m", "x")
git(src, "-c", "gpg.format=ssh", "-c", f"user.signingkey={k_eve}", "tag", "-s", "v1.12-eve", "-m", "x")
check(
    "the newest release by version: v1.12-eve over v1.10 over v1.9",
    signing.newest_tag(src) == "v1.12-eve",
    signing.newest_tag(src),
)
ok, d, _ = signing.check_tag(src, "v1.10", [line_tom])
check(
    "a tag signed with a trusted key: taken, and by whom",
    ok and d["id"] == "update.signed.tag.ok" and d["args"]["signer"] == "owner@box",
    d,
)
ok, d, seen = signing.check_tag(src, "v1.12-eve", [line_tom])
check(
    "a tag signed with another key: refused, with what git said",
    not ok and d["id"] == "update.signed.tag-untrusted" and seen,
    (d, seen),
)
ok, d, _ = signing.check_tag(src, "v1.11-unsigned", [line_tom])
check("an unsigned tag: refused", not ok and d["id"] == "update.signed.tag-untrusted", d)

# --- the root helper: the level kept, what /admin sees, the check before an install ---
msg = hub_control.update_signing({"level": "tags", "signers": line_tom})
kept = json.loads((ETC / "update-signing.json").read_text())
public = json.loads((STATE / "control" / "update-signing.json").read_text())
check(
    "the level and the keys kept root's (0600)",
    kept == {"level": "tags", "signers": [line_tom], "owner": ""}
    and ((ETC / "update-signing.json").stat().st_mode & 0o077) == 0,
    oct((ETC / "update-signing.json").stat().st_mode),
)
check(
    "/admin sees the key's name, type and comment, never the key",
    public
    == {"level": "tags", "owner": "", "keys": [{"name": "owner@box", "type": "ssh-ed25519", "comment": "laptop"}]}
    and pub_tom.split()[1] not in json.dumps(public),
    public,
)
check("  and says so", msg["id"] == "update.signing.tags" and msg["args"] == {"n": 1}, msg)
git(src, "checkout", "-q", "--detach", "v1.10")
c = hub_control._signature_check(src, None, "v1.10")
check(
    "the check before an install: v1.10, signed by the owner's key, passes",
    c and c[0]["ok"] and c[0]["id"] == hub_control.SIGNED_CHECK,
    c,
)
git(src, "checkout", "-q", "--detach", "v1.12-eve")
c = hub_control._signature_check(src, None, "v1.12-eve")
check("v1.12-eve, eve's key: fails", c and not c[0]["ok"], c)
c = hub_control._signature_check(src, None)
check(
    "  with no tag named, the newest release tag on that commit is the one checked (eve's): fails",
    c and not c[0]["ok"] and c[0]["detail"]["args"]["tag"] == "v1.12-eve" and c[0]["fix"],
    c,
)
git(src, "checkout", "-q", "main")
(src / "a").write_text("3")
git(src, "commit", "-qam", "three, no tag")
c = hub_control._signature_check(src, None)
check(
    "the branch's tip, not a release tag: fails",
    c and not c[0]["ok"] and c[0]["detail"]["id"] == "update.signed.no-tag",
    c,
)

# Install anyway cannot pass a signature that failed.
upd = Path(hub_control.UPDATE_SRC)
upd.mkdir(parents=True, exist_ok=True)
(upd / "install.sh").write_text("#!/bin/bash\nexit 0\n")
subprocess.run(["git", "init", "-q", str(upd)], check=True)
git(upd, "add", "install.sh")
git(upd, "commit", "-qm", "x")
head = git(upd, "rev-parse", "--short=7", "HEAD")
hub_control._write_update_state(
    {
        "available": head,
        "checked_sha": git(upd, "rev-parse", "HEAD"),
        "checks": [
            hub_control._check(hub_control.SIGNED_CHECK, False, M("t.name", "Signed"), M("t.unsigned", "Unsigned."))
        ],
    }
)
try:
    hub_control.update_force_install({})
    ok = False
    why = "installed"
except hub_control.Refused as exc:
    ok, why = exc.message["id"] == "update.force.unsigned", exc.message
check("Install anyway refuses a version not signed as required", ok, why)
# A state kept before the checks had IDs: the signature check known by its old name, and refused the same.
hub_control._write_update_state(
    {
        "available": head,
        "checked_sha": git(upd, "rev-parse", "HEAD"),
        "checks": [{"name": hub_control.SIGNED_OLD, "ok": False, "detail": "unsigned", "warn": False}],
    }
)
try:
    hub_control.update_force_install({})
    ok = False
    why = "installed"
except hub_control.Refused as exc:
    ok, why = exc.message["id"] == "update.force.unsigned", exc.message
check("  and in a state kept before the checks had IDs", ok, why)

# --- off: nothing more than before ---
hub_control.update_signing({"level": "off", "signers": ""})
check("off: no signature check", hub_control._signature_check(src, None) == [])


# The release tags fetched whatever the level: held by the box with signing off too.
class _Quiet:
    def step(self, *a):
        pass

    def add_steps(self, *a):
        pass


REWRITES = []


def served_at(path):
    """A valid https address that git, in this test only, serves from a local repository: git's own insteadOf, set in
    the test's environment. The updater accepts only https, so its fixtures cannot be local paths, and nothing in
    production can switch that rule off."""
    url = f"https://git.invalid/{Path(path).name}"
    REWRITES.append((url, str(path)))
    os.environ["GIT_CONFIG_COUNT"] = str(len(REWRITES))
    for i, (https, local) in enumerate(REWRITES):
        os.environ[f"GIT_CONFIG_KEY_{i}"] = f"url.{local}.insteadOf"
        os.environ[f"GIT_CONFIG_VALUE_{i}"] = https
    return url


# git's allow-list (hub_control._source_protocols) lets the fixtures' own file transport through for these two runs only;
# the rule itself is asserted, unchanged, further down.
REAL_PROTOCOLS = hub_control._source_protocols
hub_control._source_protocols = lambda level=None: [*REAL_PROTOCOLS(level), "-c", "protocol.file.allow=always"]
(Path(hub_control.ETC) / "install-options").write_text(f"--repo\n{served_at(src)}\n--branch\nmain\n")
shutil.rmtree(hub_control.UPDATE_SRC, ignore_errors=True)
st, _ = hub_control._check_for_update(_Quiet())
held = git(hub_control.UPDATE_SRC, "tag", "-l", "v*").split()
check(
    "off: the release tags fetched all the same, the newest recorded; the branch's tip what is offered",
    {"v1.9", "v1.10", "v1.12-eve"} <= set(held)
    and st["release_tag"] == "v1.12-eve"
    and st["tag"] is None
    and not st["tags_problem"],
    (held, st.get("release_tag")),
)
bare = T / "no-tags"
subprocess.run(["git", "clone", "-q", "--bare", "--no-tags", str(src), str(bare)], check=True)
for t in git(bare, "tag", "-l").split():
    git(bare, "tag", "-d", t)
(Path(hub_control.ETC) / "install-options").write_text(f"--repo\n{served_at(bare)}\n--branch\nmain\n")
shutil.rmtree(hub_control.UPDATE_SRC, ignore_errors=True)
st, _ = hub_control._check_for_update(_Quiet())
check(
    "  a repository with no release tag: noted, not in the way while signing is off",
    st["release_tag"] is None and (st["tags_problem"] or {}).get("id") == "update.check.no-tags-yet",
    st.get("tags_problem"),
)

# What may be an update source depends on the address and the signing level (update-source-policy): the same
# function decides at every place a source is set or used; a refusal runs no git command and leaves the cache alone.
from unittest import mock

hub_control._source_protocols = REAL_PROTOCOLS  # the fixtures' file transport (above) is for the fixtures only
OPTIONS_FILE = Path(hub_control.ETC) / "install-options"
SIGNING_FILE = Path(hub_control.ETC) / signing.FILE_NAME
keep = hub_control.UPDATE_SRC
shutil.rmtree(keep, ignore_errors=True)
keep.mkdir(parents=True)
(keep / "marker").write_text("cache")
ALWAYS = [
    "ext::sh -c 'touch /tmp/x'",
    "ext::touch",
    "fd::17",
    "-oProxyCommand=touch",
    "--upload-pack=x",
    "https://user@example.invalid/irate-box",
    "https://user:pw@example.invalid/irate-box",
    "https://example.invalid/irate-box?x=1",
    "https://example.invalid/irate-box#frag",
    "http://user:pw@box.local/irate-box",
    "ssh://git@example.invalid/irate-box",
    "git+ssh://example.invalid/irate-box",
    "git@example.invalid:owner/irate-box.git",
    "example.invalid:owner/irate-box",
    "../irate-box",
    "irate-box",
    "https://",
    "https:///irate-box",
    "file://host/srv/irate-box",
    "/srv/irate box",
]
SIGNED_ONLY = [
    "http://box.local:8000/irate-box",
    "http://192.168.4.7/irate-box",
    "git://box.local/irate-box",
    "/srv/irate-box",
    "file:///srv/irate-box",
]
HTTPS = ["https://example.invalid/irate-box", "https://[2001:db8::1]:8443/owner/irate-box.git"]
ran = []


def no_git(*cmd, **kw):
    ran.append(cmd)
    raise AssertionError(f"ran {cmd}")


def level_is(level):
    SIGNING_FILE.write_text(json.dumps({"level": level, "signers": [line_tom] if level == "tags" else []}))


def refusals(addr):
    """The message IDs of the check for updates, the branch question and the update-source form for a recorded
    address, with every way of running git or removing the cache stood in by a failure."""
    OPTIONS_FILE.write_text(f"--repo\n{addr}\n--branch\nmain\n")
    got = []
    with (
        mock.patch.object(hub_control, "run", no_git),
        mock.patch.object(hub_control.subprocess, "run", no_git),
        mock.patch.object(hub_control.shutil, "rmtree", no_git),
    ):
        for call in (
            lambda: hub_control._check_for_update(_Quiet()),
            lambda: hub_control.update_poll_target({}),
            lambda: hub_control.update_set_source({"repo": addr, "branch": "main", "confirmed": True}),
        ):
            try:
                call()
                got.append(None)
            except hub_control.Refused as exc:
                got.append(exc.message["id"])
            except AssertionError as exc:
                got.append(str(exc))
    return got


BAD_ID = {"update.check.source-bad", "update.source.repo.bad"}
matrix = {}
for level in signing.LEVELS:
    level_is(level)
    for addr in ALWAYS:
        matrix[(level, addr)] = refusals(addr)
level_is("off")
for addr in SIGNED_ONLY:
    matrix[("off", addr)] = refusals(addr)
check(
    "update source: refused at every level - a sign-in, query or fragment, a leading -, ext:: and other helpers, ssh, scp-style, relative",
    all(set(matrix[(lv, a)]) <= BAD_ID and len(set(matrix[(lv, a)])) == 2 for lv in signing.LEVELS for a in ALWAYS),
    {k: v for k, v in matrix.items() if k[1] in ALWAYS and not set(v) <= BAD_ID},
)
check(
    "  signing off: http://, git://, a local path and file:// are refused, said to be a matter of signing",
    all(
        matrix[("off", a)] == ["update.check.source-level", "update.check.source-level", "update.source.level"]
        for a in SIGNED_ONLY
    ),
    {a: matrix[("off", a)] for a in SIGNED_ONLY},
)
check(
    "  and none of it runs git or touches the cache",
    not ran and (keep / "marker").read_text() == "cache",
    ran,
)
for level in ("github", "tags"):
    level_is(level)
    got = {}
    seen = []

    def spy(*cmd, **kw):
        seen.append(cmd)  # noqa: B023
        return subprocess.CompletedProcess(cmd, 1, "", "stand-in")

    for addr in SIGNED_ONLY + HTTPS:
        OPTIONS_FILE.write_text(f"--repo\n{addr}\n--branch\nmain\n")
        seen.clear()
        shutil.rmtree(keep, ignore_errors=True)  # no cache yet: the first command is the clone
        with mock.patch.object(hub_control, "run", spy):
            try:
                hub_control._check_for_update(_Quiet())
            except hub_control.Refused as exc:
                got[addr] = (exc.message["id"], seen[0] if seen else None)
    allowed = ["git", "-c", "protocol.allow=never"] + [
        a for s in ("https", "http", "git", "file") for a in ("-c", f"protocol.{s}.allow=always")
    ]
    check(
        f"  signing {level}: http://, git://, a local path, file:// and https:// reach git, with git's own allow-list",
        all(
            v[0] == "update.git.failed" and list(v[1][: len(allowed)]) == allowed and v[1][len(allowed)] == "clone"
            for v in got.values()
        )
        and len(got) == len(SIGNED_ONLY + HTTPS),
        got,
    )
level_is("off")
OPTIONS_FILE.write_text(f"--repo\n{HTTPS[0]}\n--branch\nmain\n")
seen = []


def spy_off(*cmd, **kw):
    seen.append(cmd)
    return subprocess.CompletedProcess(cmd, 1, "", "stand-in")


shutil.rmtree(keep, ignore_errors=True)
with mock.patch.object(hub_control, "run", spy_off):
    try:
        hub_control._check_for_update(_Quiet())
    except hub_control.Refused:
        pass
check(
    "  signing off: https:// reaches git with nothing but https allowed",
    seen
    and list(seen[0][:5]) == ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    and seen[0][5] == "clone",
    seen,
)
# Used again every time: lowering signing under an http:// source refuses the next check, and the doctor says so.
level_is("github")
OPTIONS_FILE.write_text(f"--repo\n{SIGNED_ONLY[0]}\n--branch\nmain\n")
keep.mkdir(parents=True, exist_ok=True)
(keep / "marker").write_text("cache")
reply = hub_control.update_signing({"level": "off", "signers": ""})
check(
    "lowering signing under a recorded http:// source: saved, with a warning that names the source",
    reply["id"] == "update.signing.source-refused"
    and reply["args"]["repo"] == SIGNED_ONLY[0]
    and json.loads(SIGNING_FILE.read_text())["level"] == "off",
    reply,
)
check(
    "  the next check refuses, with no git command and the cache untouched",
    refusals(SIGNED_ONLY[0])[0] == "update.check.source-level" and not ran and (keep / "marker").read_text() == "cache",
    ran,
)
reply = hub_control.update_signing({"level": "github", "signers": ""})
check("  raising it again: no warning", reply["id"] == "update.signing.github", reply)
OPTIONS_FILE.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")
reply = hub_control.update_signing({"level": "off", "signers": ""})
check("  an https:// source is never in the way", reply["id"] == "update.signing.off", reply)
shutil.rmtree(keep, ignore_errors=True)

# --- merged on GitHub: GitHub's key, a key of our own standing in for it ---
if not (shutil.which("gpg") and shutil.which("gpgv")):
    print("SKIP GitHub's level: gpg or gpgv is not installed here (CI's runner has both)")
else:
    home = T / "gnupg"
    home.mkdir(mode=0o700)
    genv = dict(os.environ, GNUPGHOME=str(home))
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--quiet",
            "--passphrase",
            "",
            "--quick-gen-key",
            "Stand-in GitHub <noreply@github.test>",
            "ed25519",
            "sign",
            "never",
        ],
        env=genv,
        check=True,
        capture_output=True,
    )
    fpr = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-keys"], env=genv, capture_output=True, text=True
    ).stdout
    fpr = next(ln.split(":")[9] for ln in fpr.splitlines() if ln.startswith("fpr:"))
    key = CODE / signing.GITHUB_KEY
    key.write_text(
        subprocess.run(["gpg", "--batch", "--armor", "--export", fpr], env=genv, capture_output=True, text=True).stdout
    )
    gh = T / "gh"
    gh.mkdir()
    git(gh, "init", "-q", "-b", "main")
    (gh / "a").write_text("1")
    git(gh, "add", "a")
    git(gh, "commit", "-qm", "installed")
    installed = git(gh, "rev-parse", "HEAD")
    (gh / "a").write_text("2")
    git(gh, "-c", f"user.signingkey={fpr}", "commit", "-S", "-qam", "merged on GitHub", env={"GNUPGHOME": str(home)})
    hub_control.update_signing({"level": "github", "signers": ""})
    c = hub_control._signature_check(gh, installed)
    check(
        "GitHub's level: a commit signed by GitHub's key passes, said with what it does not prove",
        c and c[0]["ok"] and c[0]["detail"]["id"] == "update.signed.github.ok" and c[0]["detail"]["args"] == {"n": 1},
        c,
    )
    (gh / "a").write_text("3")
    git(gh, "commit", "-qam", "pushed straight to main")
    c = hub_control._signature_check(gh, installed)
    check(
        "  an unsigned commit pushed on top: refused, said so",
        c and not c[0]["ok"] and c[0]["detail"]["id"] == "update.signed.unsigned",
        c,
    )
    (CODE / signing.GITHUB_KEY).unlink()
    c = hub_control._signature_check(gh, installed)
    check(
        "  no GitHub key in the installed code: refused, not waved through",
        c and not c[0]["ok"] and c[0]["detail"]["id"] == "update.signed.github-key-missing",
        c,
    )
check(
    "the shipped key is GitHub's (its current key, B5690EEEBB952194)",
    "BEGIN PGP PUBLIC KEY BLOCK" in (REPO / signing.GITHUB_KEY).read_text(),
)

# The owner trusted at "merged on GitHub" (review 2026-10-23, F1): GitHub signs commits in anyone's repository with
# the same key, so the box keeps whose repository it follows, shows it, and moves only when the administrator
# confirms another owner.
check(
    "a repository's owner: host and first path part, lower case; a path on the box, its folder",
    [
        signing.owner_of(a)
        for a in (
            "https://github.com/NomDeTom/irate-box",
            "https://github.com/nomdetom/irate-box.git",
            "http://box.local:8000/mirrors/irate-box",
            "git@github.com:Eve/irate-box.git",
            "/srv/mirrors/irate-box",
            "file:///srv/mirrors/irate-box",
        )
    ]
    == [
        "github.com/nomdetom",
        "github.com/nomdetom",
        "box.local/mirrors",
        "github.com/eve",
        "/srv/mirrors",
        "/srv/mirrors",
    ],
)
OPTIONS_FILE.write_text("--repo\nhttps://github.com/NomDeTom/irate-box\n--branch\nmain\n")
hub_control.update_signing({"level": "github", "signers": ""})
public = json.loads((STATE / "control" / "update-signing.json").read_text())
check(
    "choosing the level keeps the recorded source's owner as the one trusted, and /admin sees it",
    signing.load(hub_control.ETC)["owner"] == "github.com/nomdetom" and public["owner"] == "github.com/nomdetom",
    public,
)
ls_remote = subprocess.CompletedProcess([], 0, "abc\trefs/heads/main\nabd\trefs/heads/dev\n", "")
with (
    mock.patch.object(hub_control, "run", lambda *a, **k: ls_remote),
    mock.patch.object(hub_control, "update_clear_cache", lambda req: None),
    mock.patch.object(hub_control, "update_poll_target", lambda req: None),
    mock.patch.object(hub_control, "_doctor_again", lambda: None),
):
    try:
        hub_control.update_set_source({"repo": "https://github.com/Eve/irate-box", "branch": "main", "confirmed": True})
        refused = None
    except hub_control.Refused as exc:
        refused = exc.message
    check(
        "a source of another owner's, not confirmed: refused, naming both owners; nothing recorded",
        refused
        and refused["id"] == "update.source.other-owner"
        and refused["args"]["account"] == "github.com/eve"
        and refused["args"]["trusted"] == "github.com/nomdetom"
        and "NomDeTom" in OPTIONS_FILE.read_text(),
        refused,
    )
    hub_control.update_set_source(
        {"repo": "https://github.com/Eve/irate-box", "branch": "main", "confirmed": True, "trust_owner": True}
    )
    check(
        "  confirmed: recorded, and eve is the owner trusted from now on",
        "github.com/Eve/irate-box" in OPTIONS_FILE.read_text()
        and signing.load(hub_control.ETC)["owner"] == "github.com/eve",
    )
    hub_control.update_set_source({"repo": "https://github.com/eve/irate-box", "branch": "dev", "confirmed": True})
    check("  another branch of the same owner's: no question", "dev" in OPTIONS_FILE.read_text())
# The source recorded some other way than Updates (the file edited): the check before an install refuses it.
OPTIONS_FILE.write_text("--repo\nhttps://github.com/mallory/irate-box\n--branch\nmain\n")
c = hub_control._signature_check(src, None)
check(
    "the check before an install: a recorded source not of the trusted owner fails, saying how to trust it",
    c and not c[0]["ok"] and c[0]["detail"]["id"] == "update.signed.other-owner" and c[0]["fix"],
    c,
)
# A box set to this level before owners were kept: the source it follows is the one it trusts from then on.
SIGNING_FILE.write_text(json.dumps({"level": "github", "signers": []}))
OPTIONS_FILE.write_text("--repo\nhttps://github.com/NomDeTom/irate-box\n--branch\nmain\n")
with mock.patch.object(signing, "check_commits", lambda src, revs, key: (True, M("t.ok", "Signed."), [])):
    c = hub_control._signature_check(src, None)
check(
    "  a box from before owners were kept: its source's owner pinned at the first check, which goes on",
    signing.load(hub_control.ETC)["owner"] == "github.com/nomdetom" and c and c[0]["ok"],
    c,
)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
