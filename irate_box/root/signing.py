# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""How far an update must be vouched for before root installs it (Tom, 2026-10-08: "give options
in the update manager for what level to choose"). Three levels:

  off     as before: the fetched branch, a fast-forward of what is installed, its checks passed.
  github  every new commit signed by GitHub's own key: what merging a pull request on GitHub
          gives (a squash merge is GitHub's commit, signed with its web-flow key). Code pushed
          straight to the branch, unsigned, is refused.
  tags    the newest release tag (v*), signed with an SSH key the owner lists here. The box
          then follows releases, not the branch; nobody without one of those keys can make it
          install anything.

The choice and the owner's keys are root's (/etc/hub/update-signing.json); the hub sees only what
public() gives. GitHub's key comes from the installed code (config/signing/), never the fetched
tree. A signature that fails blocks the install, and Install anyway too."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

LEVELS = ("off", "github", "tags")
FILE_NAME = "update-signing.json"
GITHUB_KEY = Path("config/signing/github-web-flow.asc")   # under the installed code
TAG_GLOB = "v*"
SIGNER = re.compile(r"^(\S+) ((?:sk-)?(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521))(?:@openssh\.com)?) "
                    r"([A-Za-z0-9+/]+={0,2})(?: (.{0,100}))?$")
MAX_SIGNERS = 20


def parse_signers(text):
    """allowed_signers lines (principal, key type, key, comment), checked one by one; '#' and blank
    lines skipped. Raises ValueError naming the first bad line."""
    out = []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = SIGNER.match(line)
        if not m:
            raise ValueError(f"line {n} is not \"name key-type key\" (as ssh-keygen -Y uses: e.g. "
                             "tom@box ssh-ed25519 AAAA…)")
        out.append(line)
    if len(out) > MAX_SIGNERS:
        raise ValueError(f"at most {MAX_SIGNERS} keys")
    return out


def load(etc):
    try:
        data = json.loads((Path(etc) / FILE_NAME).read_text())
    except (OSError, ValueError):
        data = {}
    level = data.get("level") if data.get("level") in LEVELS else "off"
    signers = [s for s in data.get("signers", []) if isinstance(s, str)]
    return {"level": level, "signers": signers}


def save(etc, level, signers_text, write):
    """Check and keep the choice; write(path, text) is the caller's root-safe writer."""
    if level not in LEVELS:
        raise ValueError(f"level is one of {', '.join(LEVELS)}")
    signers = parse_signers(signers_text)
    if level == "tags" and not signers:
        raise ValueError("signed releases need at least one key to trust")
    data = {"level": level, "signers": signers}
    write(Path(etc) / FILE_NAME, json.dumps(data, indent=2))
    return data


def public(data):
    """What /admin may show: the level, and each key's name, type and comment (never the key)."""
    keys = []
    for line in data["signers"]:
        m = SIGNER.match(line)
        if m:
            keys.append({"name": m.group(1), "type": m.group(2), "comment": m.group(4) or ""})
    return {"level": data["level"], "keys": keys}


def _git(src, *args, env=None, timeout=60):
    return subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True, env=env, timeout=timeout)


def newest_tag(src):
    """The highest release tag in the fetched tree, by version, or None."""
    out = _git(src, "tag", "-l", TAG_GLOB, "--sort=-v:refname")
    tags = [t for t in out.stdout.split() if t]
    return tags[0] if tags else None


def check_commits(src, revs, key_file):
    """(ok, detail): each of revs carries a good signature by the key in key_file (GitHub's)."""
    if not shutil.which("gpg"):
        return False, "gpg is not installed, so GitHub's signatures cannot be read: install the gpg package"
    if not Path(key_file).is_file():
        return False, f"GitHub's key is not in the installed code ({key_file})"
    if not revs:
        return True, "nothing new to check"
    with tempfile.TemporaryDirectory() as home:
        os.chmod(home, 0o700)
        env = dict(os.environ, GNUPGHOME=home)
        imp = subprocess.run(["gpg", "--batch", "--quiet", "--import", str(key_file)], capture_output=True, text=True, env=env, timeout=60)
        if imp.returncode != 0:
            return False, "GitHub's key could not be read: " + (imp.stderr.strip().splitlines() or ["?"])[-1]
        for rev in revs:
            out = _git(src, "verify-commit", "--raw", rev, env=env)
            if out.returncode != 0 or "[GNUPG:] VALIDSIG" not in out.stderr:
                short = rev[:7]
                why = "not signed" if "[GNUPG:]" not in out.stderr else "not signed by GitHub's key"
                return False, f"{short} is {why}: pushed straight to the branch, not merged on GitHub"
    return True, f"{len(revs)} commit{'s' if len(revs) != 1 else ''} signed by GitHub"


def check_tag(src, tag, signers):
    """(ok, detail): the tag is signed with one of the owner's SSH keys."""
    if not signers:
        return False, "no key to trust is set (Updates: what an update must carry)"
    if not shutil.which("ssh-keygen"):
        return False, "ssh-keygen is not installed, so the release's signature cannot be read"
    with tempfile.NamedTemporaryFile("w", suffix=".allowed_signers", delete=False) as f:
        f.write("\n".join(signers) + "\n")
        path = f.name
    try:
        out = _git(src, "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={path}", "verify-tag", tag)
    finally:
        os.unlink(path)
    if out.returncode != 0:
        line = next((l for l in (out.stderr or "").splitlines() if l.strip()), "no good signature")
        return False, f"{tag} is not signed with a key this box trusts ({line.strip()[:160]})"
    good = re.search(r'Good "git" signature for (\S+)', out.stderr or "")
    return True, f"{tag} signed by {good.group(1) if good else 'a trusted key'}"
