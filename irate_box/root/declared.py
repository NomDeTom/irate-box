# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The checks an add-on's manifest declares (manifests.py, "checks"), run for the Security doctor in a process of
their own: the doctor starts this file with python3 -I, gives it the checks as JSON on stdin, reads one result a
check as JSON on stdout, and stops it after a time limit (secdoctor.run_declared). So a pattern that backtracks for
ever stops only this, not the doctor. Standard library only, and nothing imported from irate-box: -I leaves the
code's folder off the path.

It only reads: a file's owner and mode, and a config file's live lines. A unit's property and a web answer are
read by the doctor, which may start programs and send requests, and given here in the check ("got"): this matches
them. A result is "pass", "fail" (the check's expectation does not hold) or "unread" (what it reads is not there or
not readable), with a short detail."""

import grp
import json
import os
import pwd
import re
import stat
import sys

MAX_FILE = 1 << 20  # a config file read at most this far
MAX_LINE = 4096  # each line cut here before a pattern sees it


def _open(path, follow):
    """The text of a regular file, at most MAX_FILE bytes; OSError otherwise. follow: a link is followed (a built-in
    manifest's path, root's to choose); not followed for a local add-on's."""
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | (0 if follow else os.O_NOFOLLOW)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(MAX_FILE).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def _under(root, rel):
    """(a descriptor of the folder holding rel's last part, that part), walked from root one folder at a time with
    O_NOFOLLOW: a link anywhere on the way, root itself included, is an OSError, never followed. For a local
    add-on's files, in a folder the hub owns."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    parts = [p for p in rel.split("/") if p]
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except OSError:
        os.close(fd)
        raise
    return fd, parts[-1]


def _read_under(root, rel):
    dfd, name = _under(root, rel)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{rel} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(MAX_FILE).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def _stat_under(root, rel):
    dfd, name = _under(root, rel)
    try:
        return os.stat(name, dir_fd=dfd, follow_symlinks=False)
    finally:
        os.close(dfd)


def _name(lookup, num):
    try:
        return lookup(num)[0]
    except KeyError:
        return str(num)


def check_file(c):
    path = c["path"]
    try:
        if c.get("root"):
            st = _stat_under(c["root"], path)
        else:
            st = os.stat(path) if c.get("follow", True) else os.lstat(path)
    except FileNotFoundError:
        return ("pass", "not there") if c.get("exists") is False else ("fail", "not there")
    except OSError as exc:
        return "unread", str(exc)
    if c.get("exists") is False:
        return "fail", "there"
    if stat.S_ISLNK(st.st_mode):
        return "fail", "a link"
    owner = _name(pwd.getpwuid, st.st_uid)
    if "owner" in c and owner != c["owner"]:
        return "fail", f"owned by {owner}"
    group = _name(grp.getgrgid, st.st_gid)
    if "group" in c and group != c["group"]:
        return "fail", f"group {group}"
    mode = stat.S_IMODE(st.st_mode)
    if "max_mode" in c and mode & ~int(c["max_mode"], 8):
        return "fail", f"mode {mode:04o}"
    return "pass", f"{owner}:{group} {mode:04o}"


def check_config(c):
    try:
        text = _read_under(c["root"], c["path"]) if c.get("root") else _open(c["path"], c.get("follow", True))
    except OSError as exc:
        return "unread", str(exc)
    flags = re.IGNORECASE if c.get("ignore_case") else 0
    comments = tuple(c.get("comments") or ())
    live = [ln[:MAX_LINE] for ln in text.splitlines() if not (comments and ln.lstrip().startswith(comments))]
    if "present" in c or "absent" in c:
        pat = re.compile(c.get("present", c.get("absent")), flags)
        hit = next((ln for ln in live if pat.search(ln)), None)
        if "present" in c:
            return ("pass", hit.strip()[:200]) if hit is not None else ("fail", "not found")
        return ("fail", hit.strip()[:200]) if hit is not None else ("pass", "not found")
    v = c["value"]
    key = re.compile(v["key"], flags)
    m = next((m for m in map(key.search, live) if m), None)
    if m is None:
        return "fail", "not set"
    got = m.group(1).strip()
    if "min" in v or "max" in v:
        try:
            n = int(got)
        except ValueError:
            return "fail", f"{got!r} is not a number"
        ok = ("min" not in v or n >= v["min"]) and ("max" not in v or n <= v["max"])
    elif "equals" in v:
        ok = got.lower() == v["equals"].lower() if flags else got == v["equals"]
    else:
        ok = got.lower() != v["not"].lower() if flags else got != v["not"]
    return ("pass" if ok else "fail"), got[:200]


def check_unit(c):
    got = c.get("got")
    if got is None:
        return "unread", "no answer from systemd"
    if "equals" in c:
        ok = got == c["equals"]
    elif "not" in c:
        ok = got != c["not"]
    else:
        hit = re.search(c.get("present", c.get("absent")), got[:MAX_LINE])
        ok = bool(hit) == ("present" in c)
    return ("pass" if ok else "fail"), f"{c['property']}={got[:200]}"


def check_http(c):
    got = c.get("got")
    if not got or not got.get("http"):
        return "unread", (got or {}).get("error", "no answer")
    said = f"{got['status']}"
    if c.get("expect") and got["status"] not in c["expect"]:
        return "fail", said
    if "header" in c:
        value = got["headers"].get(c["header"].lower())
        if value is None:
            return "fail", f"{said}, no {c['header']}"
        if not re.search(c["match"], value[:MAX_LINE]):
            return "fail", f"{said}, {c['header']}: {value[:160]}"
    return "pass", said


KINDS = {"file": check_file, "config": check_config, "unit": check_unit, "http": check_http}


def main():
    checks = json.load(sys.stdin)
    out = []
    for c in checks:
        try:
            result, detail = KINDS[c["kind"]](c)
        except Exception as exc:  # noqa: BLE001 - one bad check must not hide the rest
            result, detail = "unread", f"{type(exc).__name__}: {exc}"
        out.append({"id": c["id"], "result": result, "detail": str(detail)[:300]})
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
