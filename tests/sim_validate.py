# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The shared validators and writers (irate_box/validate.py), with the hostile values from item 87.
python3 tests/sim_validate.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box.hub import hotspot
from irate_box.msg import Refused
from irate_box.root import ap, wifijoin

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except (ValueError, Refused) as exc:
        check(name, bool(str(exc)), exc)
    else:
        check(name, False, "accepted")


# --- names: the hostile cases, and the names that must still work ---
for value in ("12;34;", "Café Wi-Fi ☕", "a b", "O'Brien", 'say "hi"', "$(touch /tmp/x)", "`id`", "#x"):
    try:
        V.ssid(value)
        check(f"ssid accepted: {value!r}", True)
    except V.Invalid as exc:
        check(f"ssid accepted: {value!r}", False, exc)
for value in ("", "x" * 33, "a\nb", "a\rb", "a\0b", "a\tb", b"bytes", None):
    refused(f"ssid refused: {value!r}", V.ssid, value)
check("ssid counts UTF-8 bytes, not characters", V.ssid("é" * 16) and len("é".encode()) == 2)

refused("hostname: newline", V.hostname, "box\nname")
refused("hostname: upper case", V.hostname, "Box")
check("hostname: a box name", V.hostname("lyra-2") == "lyra-2")
refused("iface: shell text", V.iface, "wlan0;rm")
check("iface: wlan0", V.iface("wlan0") == "wlan0")
refused("name: ../", V.name, "../etc")
refused("account: short", V.account, "ab")
refused("package: apt option", V.package, "--force-yes")
check("package: meshtasticd", V.package("meshtasticd") == "meshtasticd")
refused("version: newline", V.version, "1.2\n3")
refused("repo: no account", V.repo, "repo")
refused("branch: leading dash", V.branch, "-x")
refused("branch: ..", V.branch, "a/../b")
refused("sha256: short", V.sha256, "abc")
check("sha256 lowercases", V.sha256("A" * 64) == "a" * 64)
refused("card: not a card", V.card, "sda;x")
refused("country: not in list", V.country, "GB", ("FR",))
check("country in list", V.country("GB", ("GB", "FR")) == "GB")
refused("unit: option", V.unit, "-x.service")
refused("ip: hostname", V.ip, "example.com")
check("ip: address", V.ip("192.168.4.10") == "192.168.4.10")

# A trailing newline is still a second line: every name validator uses fullmatch, not match.
for fn, value in (
    (V.hostname, "box\n"),
    (V.iface, "wlan0\n"),
    (V.name, "name\n"),
    (V.account, "account\n"),
    (V.package, "package\n"),
    (V.version, "1.0\n"),
    (V.repo, "account/repository\n"),
    (V.branch, "main\n"),
    (V.sha256, "a" * 64 + "\n"),
    (V.card, "sda\n"),
    (V.country, "GB\n"),
    (V.unit, "irate-box.service\n"),
    (V.fs_uuid, "abcd\n"),
):
    refused(f"{fn.__name__}: a trailing newline refused", fn, value)


# --- text, choices, numbers, argv ---
refused("text: NUL", V.text, "a\0b")
refused("text: too long", V.text, "x" * 10, "The text", 5)
check("text: multiline allows a newline", V.text("a\nb", multiline=True) == "a\nb")
refused("text: multiline still refuses NUL", V.text, "a\0b", "The text", 10, True)
refused("choice: outside", V.choice, "x", ("a", "b"))
refused("number: bool", V.number, True, 0, 10)
refused("number: outside", V.number, 11, 0, 10)
refused("argv: a string command", V.argv, "rm -rf /")
refused("argv: newline argument", V.argv, ["sh", "-c", "a\nb"])
check("argv: arguments kept as a list", V.argv(["ip", "link", "show"]) == ["ip", "link", "show"])


# --- paths: inside a root, no .., no link out, no special file ---
T = Path(tempfile.mkdtemp(prefix="validate-"))
root = T / "root"
root.mkdir()
(root / "ok.txt").write_text("ok")
(root / "sub").mkdir()
(root / "sub/inside.txt").write_text("inside")
outside = T / "outside.txt"
outside.write_text("outside")
(root / "link-in").symlink_to(root / "ok.txt")
(root / "link-out").symlink_to(outside)
os.mkfifo(root / "fifo")

check("path: a file in the root", V.path("ok.txt", root) == root / "ok.txt")
check("path: a missing leaf under the root", V.path("new/file.txt", root) == root / "new/file.txt")
refused("path: ../ out", V.path, "../outside.txt", root)
refused("path: absolute out", V.path, str(outside), root)
refused("path: link out", V.path, "link-out", root)
refused("path: a link inside the root", V.path, "link-in", root)
refused("path: a FIFO", V.path, "fifo", root)
refused("path: missing and must exist", V.path, "gone.txt", root, "The path", True)
check("path: a folder", V.path("sub", root) == root / "sub")
check("path: the root itself", V.path(".", root) == Path(os.path.realpath(root)))
check("path: the root, as an absolute name", V.path(str(root), root) == Path(os.path.realpath(root)))
# A root that runs through a link (the state folder on a box often does): the name through the link
# and the name through the resolved folder are the same place, and an escape is still one.
(T / "real-runs").mkdir()
(T / "real-runs" / "1").mkdir()
(T / "real-runs" / "1" / "log.txt").write_text("log")
(T / "link-to-runs").symlink_to(T / "real-runs")
linked_root = T / "link-to-runs"
check(
    "path: an absolute name through a linked root",
    V.path(str(linked_root / "1/log.txt"), linked_root, must_exist=True) == linked_root / "1/log.txt",
)
check(
    "path: an absolute name through the resolved root of a linked root",
    V.path(str(T / "real-runs/1/log.txt"), linked_root, must_exist=True).name == "log.txt",
)
check("path: a relative name under a linked root", V.path("1/log.txt", linked_root, must_exist=True).name == "log.txt")
check("path: a linked root itself", V.path(".", linked_root, must_exist=True) == linked_root)
refused("path: ../ out of a linked root", V.path, "../outside.txt", linked_root)
refused("path: a sibling of the linked root", V.path, str(T / "outside.txt"), linked_root)
(T / "real-runs" / "out-link").symlink_to(outside)
refused("path: a link out of a linked root", V.path, "out-link", linked_root)
(T / (root.name + "-sibling")).mkdir()
refused("path: a sibling folder that starts with the root's name", V.path, f"../{root.name}-sibling/x", root)
refused("path: a folder that is not there as the root, must exist", V.path, ".", T / "no-such-root", "The path", True)


# --- URLs: scheme, host, credentials, IP literals, whitespace ---
check("url: https", V.url("https://example.com/a?b=1") == "https://example.com/a?b=1")
check("url: allowed host and its subdomain", V.url("https://api.example.com/x", hosts=("example.com",)))
refused("url: file:", V.url, "file:///etc/passwd")
refused("url: javascript:", V.url, "javascript:alert(1)")
refused("url: plain http", V.url, "http://example.com/x")
refused("url: another host", V.url, "https://evil.example/x", hosts=("example.com",))
refused("url: credentials", V.url, "https://user:pass@example.com/x")
refused("url: IP literal", V.url, "https://127.0.0.1/x")
refused("url: a space", V.url, "https://example.com/a b")
refused("url: no host", V.url, "https:///x")


# --- the writers: every format gets its own spelling ---
check("hostapd: ssid2 hex", V.hostapd_ssid("12;34;") == "ssid2=31323b33343b")
line = V.keyfile_ssid("12;34;")
check("keyfile: SSID as a byte list", line == "ssid=49;50;59;51;52;59;", line)
check(
    "keyfile: the byte list reads back as the name",
    bytes(int(part) for part in line.removeprefix("ssid=").rstrip(";").split(";")).decode() == "12;34;",
)
check(
    "keyfile: leading spaces and line breaks escaped", V.keyfile_value("  a\\b\nc\rd\te") == "\\s\\sa\\\\b\\nc\\rd\\te"
)
refused("keyfile: NUL", V.keyfile_value, "a\0b")
check("header_value: a plain value", V.header_value("/appstore/files/x/v1/a.apk") == "/appstore/files/x/v1/a.apk")
for bad in ("a\r\nX-Evil: 1", "a\n", "a\x00", "a\x7f"):
    refused(f"header_value: {bad!r}", V.header_value, bad)
refused("header_value: not text", V.header_value, None)
refused("config line: newline", V.config_line, "ssid", "a\nb")
refused("config line: bad key", V.config_line, "ss id", "x")
refused("config line: a key with a trailing newline", V.config_line, "ssid\n", "x")
refused("config line: an empty key", V.config_line, "", "x")
check("config line: a plain line", V.config_line("channel", "6") == "channel=6")
check("html: text, never markup", V.html_text("<b>&") == "&lt;b&gt;&amp;")


# --- the hotspot and wifi-join writers use the shared rules ---
plan = {"kind": "own-radio", "iface": "wlan0", "band": "2.4 GHz", "channel": 6}
settings = {"mode": "open"}
text = ap.keyfile(plan, settings, "12;34;")
check("ap.keyfile: the SSID is the byte list", "ssid=49;50;59;51;52;59;" in text and "ssid=12;34;" not in text, text)
conf = ap.hostapd_conf(plan, settings, "12;34;")
check("ap.hostapd_conf: the SSID is ssid2 hex", "ssid2=31323b33343b" in conf, conf)
joined = wifijoin.keyfile("irate-box 12;34;", "1111", "12;34;", "wpa-psk", "p;ss#word", False)
check("wifijoin.keyfile: the SSID is the byte list", "ssid=49;50;59;51;52;59;" in joined, joined)
try:
    wifijoin.validate("12;34;", "wpa-psk", "correct horse", False)
    check("wifijoin.validate: the hostile name is accepted and written safely", True)
except Refused as exc:
    check("wifijoin.validate: the hostile name is accepted and written safely", False, exc)
for bad in ("a\nb", "a\0b", "a\\b"):
    try:
        wifijoin.validate(bad, "wpa-psk", "correct horse", False)
        check(f"wifijoin.validate: {bad!r} refused", False)
    except Refused:
        check(f"wifijoin.validate: {bad!r} refused", True)

check(
    "hotspot.networks still accepts the same name for one network",
    hotspot.networks(settings, "12;34;")[0][1]["ssid"] == "12;34;",
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
