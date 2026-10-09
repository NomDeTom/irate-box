# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""How far an update must be vouched for (root/signing.py; the level chosen in the
update manager), against throwaway repositories: the owner's keys
checked line by line; signed releases accepted only with a trusted SSH key, the newest tag
followed; GitHub's level accepting commits signed by its key and refusing an unsigned push (a
key of our own standing in for GitHub's; skipped where gpg is missing); the root helper's check
and Install anyway refusing a version not signed as chosen. python3 tests/sim_signing.py"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="signing-"))
STATE, ETC, CODE = T / "state", T / "etc", T / "code"
for d in (STATE / "control" / "results", STATE / "control" / "requests", ETC, CODE / "config" / "signing"):
    d.mkdir(parents=True)
os.environ.update(HUB_STATE_DIR=str(STATE), HUB_ETC_DIR=str(ETC), HUB_RUN_DIR=str(T / "run"), HUB_CODE_DIR=str(CODE),
                  HUB_UPDATE_DIR=str(T / "cache" / "src"))
sys.path.insert(0, str(REPO))
from irate_box.root import hub_control, signing  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

def git(repo, *args, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x", **(env or {}))
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=e, check=True).stdout.strip()

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
        signing.parse_signers(bad); ok = False
    except ValueError as exc:
        ok = "line 1" in str(exc)
    check(f"refused, with its line: {bad[:30]}", ok)
try:
    signing.save(ETC, "tags", "", lambda p, s: None); ok = False
except ValueError:
    ok = True
check("signed releases with no key: refused", ok)

# --- signed releases ---
src = T / "src"
src.mkdir()
git(src, "init", "-q", "-b", "main")
(src / "a").write_text("1")
git(src, "add", "a"); git(src, "commit", "-q", "-m", "one")
signed = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={k_tom}"]
git(src, *signed, "tag", "-s", "v1.9", "-m", "1.9")
(src / "a").write_text("2"); git(src, "commit", "-qam", "two")
git(src, *signed, "tag", "-s", "v1.10", "-m", "1.10")
git(src, "tag", "-a", "v1.11-unsigned", "-m", "x")
git(src, "-c", "gpg.format=ssh", "-c", f"user.signingkey={k_eve}", "tag", "-s", "v1.12-eve", "-m", "x")
check("the newest release by version: v1.12-eve over v1.10 over v1.9", signing.newest_tag(src) == "v1.12-eve", signing.newest_tag(src))
ok, d = signing.check_tag(src, "v1.10", [line_tom])
check("a tag signed with a trusted key: taken, and by whom", ok and "owner@box" in d, d)
ok, d = signing.check_tag(src, "v1.12-eve", [line_tom])
check("a tag signed with another key: refused", not ok, d)
ok, d = signing.check_tag(src, "v1.11-unsigned", [line_tom])
check("an unsigned tag: refused", not ok, d)

# --- the root helper: the level kept, what /admin sees, the check before an install ---
msg = hub_control.update_signing({"level": "tags", "signers": line_tom})
kept = json.loads((ETC / "update-signing.json").read_text())
public = json.loads((STATE / "control" / "update-signing.json").read_text())
check("the level and the keys kept root's (0600)", kept == {"level": "tags", "signers": [line_tom]}
      and ((ETC / "update-signing.json").stat().st_mode & 0o077) == 0, oct((ETC / "update-signing.json").stat().st_mode))
check("/admin sees the key's name, type and comment, never the key", public == {"level": "tags", "keys": [{"name": "owner@box", "type": "ssh-ed25519", "comment": "laptop"}]}
      and pub_tom.split()[1] not in json.dumps(public), public)
check("  and says so", "release tags signed by one of 1 key" in msg, msg)
git(src, "checkout", "-q", "--detach", "v1.10")
c = hub_control._signature_check(src, None, "v1.10")
check("the check before an install: v1.10, signed by the owner's key, passes", c and c[0]["ok"] and c[0]["name"] == hub_control.SIGNED_CHECK, c)
git(src, "checkout", "-q", "--detach", "v1.12-eve")
c = hub_control._signature_check(src, None, "v1.12-eve")
check("v1.12-eve, eve's key: fails", c and not c[0]["ok"], c)
c = hub_control._signature_check(src, None)
check("  with no tag named, the newest release tag on that commit is the one checked (eve's): fails", c and not c[0]["ok"] and "v1.12-eve" in c[0]["detail"], c)
git(src, "checkout", "-q", "main")
(src / "a").write_text("3"); git(src, "commit", "-qam", "three, no tag")
c = hub_control._signature_check(src, None)
check("the branch's tip, not a release tag: fails", c and not c[0]["ok"] and "not a release tag" in c[0]["detail"], c)

# Install anyway cannot pass a signature that failed.
upd = Path(hub_control.UPDATE_SRC)
upd.mkdir(parents=True, exist_ok=True)
(upd / "install.sh").write_text("#!/bin/bash\nexit 0\n")
subprocess.run(["git", "init", "-q", str(upd)], check=True)
git(upd, "add", "install.sh"); git(upd, "commit", "-qm", "x")
head = git(upd, "rev-parse", "--short=7", "HEAD")
hub_control._write_update_state({"available": head, "checks": [hub_control._check(hub_control.SIGNED_CHECK, False, "unsigned")]})
try:
    hub_control.update_force_install({}); ok = False; why = "installed"
except ValueError as exc:
    ok, why = "Install anyway cannot pass" in str(exc), str(exc)
check("Install anyway refuses a version not signed as required", ok, why)

# --- off: nothing more than before ---
hub_control.update_signing({"level": "off", "signers": ""})
check("off: no signature check", hub_control._signature_check(src, None) == [])

# --- merged on GitHub: GitHub's key, a key of our own standing in for it ---
if not (shutil.which("gpg") and shutil.which("gpgv")):
    print("SKIP GitHub's level: gpg or gpgv is not installed here (CI's runner has both)")
else:
    home = T / "gnupg"
    home.mkdir(mode=0o700)
    genv = dict(os.environ, GNUPGHOME=str(home))
    subprocess.run(["gpg", "--batch", "--quiet", "--passphrase", "", "--quick-gen-key", "Stand-in GitHub <noreply@github.test>", "ed25519", "sign", "never"],
                   env=genv, check=True, capture_output=True)
    fpr = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys"], env=genv, capture_output=True, text=True).stdout
    fpr = next(l.split(":")[9] for l in fpr.splitlines() if l.startswith("fpr:"))
    key = CODE / signing.GITHUB_KEY
    key.write_text(subprocess.run(["gpg", "--batch", "--armor", "--export", fpr], env=genv, capture_output=True, text=True).stdout)
    gh = T / "gh"
    gh.mkdir()
    git(gh, "init", "-q", "-b", "main")
    (gh / "a").write_text("1"); git(gh, "add", "a"); git(gh, "commit", "-qm", "installed")
    installed = git(gh, "rev-parse", "HEAD")
    (gh / "a").write_text("2"); git(gh, "-c", f"user.signingkey={fpr}", "commit", "-S", "-qam", "merged on GitHub", env={"GNUPGHOME": str(home)})
    hub_control.update_signing({"level": "github", "signers": ""})
    c = hub_control._signature_check(gh, installed)
    check("GitHub's level: a commit signed by GitHub's key passes", c and c[0]["ok"], c)
    (gh / "a").write_text("3"); git(gh, "commit", "-qam", "pushed straight to main")
    c = hub_control._signature_check(gh, installed)
    check("  an unsigned commit pushed on top: refused, said so", c and not c[0]["ok"] and "not signed" in c[0]["detail"], c)
    (CODE / signing.GITHUB_KEY).unlink()
    c = hub_control._signature_check(gh, installed)
    check("  no GitHub key in the installed code: refused, not waved through", c and not c[0]["ok"], c)
check("the shipped key is GitHub's (its current key, B5690EEEBB952194)", "BEGIN PGP PUBLIC KEY BLOCK" in (REPO / signing.GITHUB_KEY).read_text())

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
