# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A stand-in for a new Armbian board's console at first boot, for tests/sim_rkserial.py: armbian-firstlogin's
questions, in its words, then a root shell that answers the commands the box sends without running them. What it
was told goes to $FAKE_RECORD as JSON. $FAKE_START: firstlogin (default), shell, login, initramfs or silent.
$FAKE_GARBLE: how many times the first root password arrives garbled."""

import json
import os
import re
import sys

record = {"answers": {}, "commands": []}
out = sys.stdout


def say(text):
    out.write(text)
    out.flush()


def ask(question, key):
    say(question)
    answer = sys.stdin.readline().rstrip("\n")
    record["answers"].setdefault(key, []).append(answer)
    return answer


def save():
    with open(os.environ["FAKE_RECORD"], "w") as fh:
        json.dump(record, fh)


start = os.environ.get("FAKE_START", "firstlogin")
garble = int(os.environ.get("FAKE_GARBLE", "0"))
if start == "silent":
    sys.stdin.readline()
    sys.exit(0)
if start == "initramfs":
    say("Gave up waiting for root file system device.\n(initramfs) ")
    sys.stdin.readline()
    sys.exit(0)
if start == "login":
    say("\nluckfox-lyra-zero-w login: ")
    sys.stdin.readline()
    sys.exit(0)
host = "luckfox-lyra-zero-w"
if start == "firstlogin":
    say("\x1b[32mmpwrd-menu\x1b[0m - mPWRD-OS Main Menu\nWaiting for system to finish booting ...\n\n")
    while True:
        a = ask("Create root password: ", "root1")
        b = ask("Repeat root password: ", "root2")
        if garble:
            garble -= 1
            a = a[:-1]
        if a == b and a:
            break
        say("Rejected - \x1b[0;31mpasswords do not match.\x1b[0m Try again [2].\n")
    ask("\nChoose default system command shell:\n\n1) bash\n2) zsh\n", "shell")
    say("\nShell: \x1b[92mBASH\x1b[0m\n")
    say("Internet connection was \x1b[91mnot detected\x1b[0m.\n\n")
    ask("Connect via wireless? [Y/n] ", "wifi")
    ask("\nAt your location, more locales are possible:\n\n1) en_GB.UTF-8\n2) Skip generating locales\nPlease enter your choice:", "locale")
    ask("\nPlease provide a username (eg. your first name): \x1b[?25h", "user")
    user = record["answers"]["user"][-1]
    ask(f"Create user ({user}) password: ", "user1")
    ask(f"Repeat user ({user}) password: ", "user2")
    name = ask("\nPlease provide your real name: " + user.capitalize(), "realname")
    record["answers"]["realname"][-1] = name.split("\x15")[-1] if "\x15" in name else user.capitalize() + name
save()
while True:
    say(f"root@{host}:~# ")
    line = sys.stdin.readline()
    if not line:
        break
    record["commands"].append(line.rstrip("\n"))
    save()
    key = re.search(r'echo "@@B""(\w+)"', line)
    if not key:
        continue
    key = key.group(1)
    body = ""
    if (m := re.search(r'hostnamectl set-hostname "([\w-]+)"', line)) :
        host = m.group(1)
    if key == "readname":
        body = host + "\n"
    elif key == "wifi":
        body = "Device 'wlan0' successfully activated with '1f2e'.\n" if "GOODNET" in line else "Error: No network with SSID 'NONET' found.\n"
    elif key == "addr":
        body = "192.168.1.77\n"
    say(f"@@B{key}\n{body}@@E{key}\n")
