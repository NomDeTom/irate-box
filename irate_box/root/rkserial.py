# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A new board set up over its serial console, root's half (Rockchip boards: the serial route). At its first boot an
Armbian board (mPWRD-OS among them) logs root in on its console and runs armbian-firstlogin, which asks its questions
there with `read`: root's password twice, the shell, whether to connect over WiFi, a locale (and, when the time zone
was not found from the internet, tzselect's menus), then the user's name, password twice and real name. Each is
answered here as it comes, in whatever order; "Skip generating locales" is chosen, which skips the locale and time
zone block (tzselect with it). At the root shell that follows, the rest is set with commands, each answer read back:
the WiFi (nmcli, or netplan where there is no NetworkManager), the name, the time zone, the locale, the keys, the setup page stopped, root's password locked if
chosen.

The console is a USB serial adapter at 1 500 000 baud (a Rockchip board's debug console). Characters are sent one at
a time with a pause, and the board's answers are read between markers, as the line can drop characters coming back.
Every value has passed hub/rksetup.problem (no quote, dollar sign, backtick, backslash or control character), so it
is safe inside double quotes in a command. Stdlib only.
"""

import os
import re
import secrets
import select
import string
import termios
import time

from irate_box.msg import M, Refused

BAUD = 1500000
PACE = 0.004  # seconds between characters sent
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-Za-z]|\x1b[=>78]")
SHELL_RE = re.compile(r"root@[\w.-]+:[^\r\n#]*#\s*$")
LOGIN_RE = re.compile(r"login:\s*$")
INITRAMFS_RE = re.compile(r"\(initramfs\)\s*$")
# Armbian 26.11's first login on an autologin console waits for an Enter before its first question
# (armbian-firstlogin: `until read -r -t 1 _`, which Ctrl-C does not end). Only this line asks for it.
ENTER_RE = re.compile(r"Press Enter to start the first login setup")
FIRST = {
    "root1": re.compile(r"Create root password: "),
    "root2": re.compile(r"Repeat root password: "),
    "shell": re.compile(r"Choose default system command shell:[\s\S]*?(\d+)\) bash"),
    "wifi": re.compile(r"Connect via wireless\? \[Y/n\] "),
    "locale": re.compile(r"Please enter your choice:"),
    "user": re.compile(r"Please provide a username \(eg\. your first name\): "),
    "user1": re.compile(r"Create user \([^)]*\) password: "),
    "user2": re.compile(r"Repeat user \([^)]*\) password: "),
    "realname": re.compile(r"Please provide your real name: "),
    "mismatch": re.compile(r"passwords do not match"),
    "illegal": re.compile(r"illegal characters in username"),
    "shellprompt": SHELL_RE,
}


def generated(length=16):
    """A password of letters and digits only (nothing Armbian's setup or a shell treats specially)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Console:
    """A board's console: bytes sent paced, the board's answers read into a buffer and matched."""

    def __init__(self, fd):
        self.fd = fd
        self.buf = ""

    @classmethod
    def open(cls, path, baud=BAUD):
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        speed = getattr(termios, f"B{baud}")
        attrs[0] = attrs[1] = attrs[3] = 0  # raw: no input or output processing, no echo, no canonical mode
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        return cls(fd)

    def close(self):
        os.close(self.fd)

    def send(self, text, pace=None):
        pace = PACE if pace is None else pace
        for ch in text:
            os.write(self.fd, ch.encode())
            if pace:
                time.sleep(pace)

    def _read(self, seconds):
        ready, _, _ = select.select([self.fd], [], [], seconds)
        if not ready:
            return False
        try:
            chunk = os.read(self.fd, 4096)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            return False
        if not chunk:
            return False
        self.buf += ANSI_RE.sub("", chunk.decode("utf-8", "replace")).replace("\r", "")
        return True

    def expect(self, patterns, timeout):
        """The first of {name: regex} found in what the board has said since the last match: (name, match), and the
        buffer kept from after it; (None, None) when nothing matched within timeout."""
        end = time.time() + timeout
        while True:
            hits = [(m.start(), name, m) for name, rx in patterns.items() if (m := rx.search(self.buf))]
            if hits:
                _, name, m = min(hits, key=lambda h: h[0])
                self.buf = self.buf[m.end() :]
                return name, m
            left = end - time.time()
            if left <= 0:
                return None, None
            self._read(min(left, 0.5))

    def run(self, command, key, timeout=30):
        """A command at the root shell; its output (between markers printed as two words, so the echo of the typed
        command never matches) or None. Asked again once if the answer did not come back whole."""
        for _ in range(2):
            self.buf = ""
            self.send(f'echo "@@B""{key}"; {command}; echo "@@E""{key}"\r')
            name, m = self.expect({"out": re.compile(rf"@@B{key}\n([\s\S]*?)@@E{key}")}, timeout)
            if name:
                return m.group(1)
        return None


def where(console, listen=300):
    """What the console shows: firstlogin (a first-login question asked), shell (root's prompt), login, initramfs,
    or None. It listens first, up to `listen` seconds (the board powered after Set up is pressed: its first question
    comes as it boots). Heard nothing, it sends Ctrl-C, which the first login ignores (it traps it) and which brings
    a shell's, a login's or the emergency shell's prompt back. An Enter only at Armbian 26.11's "Press Enter to start
    the first login setup" (found on a Lyra Zero W, 2026-10-29), nowhere else: at "Connect via wireless? [Y/n]" an
    Enter is a yes, and at the real name it takes the name filled in. A first-login question found is left in the
    buffer for first_login to answer."""
    questions = {k: v for k, v in FIRST.items() if k not in ("mismatch", "illegal", "shellprompt")}
    prompts = {"initramfs": INITRAMFS_RE, "login": LOGIN_RE, "shell": SHELL_RE}
    end = time.time() + listen
    while time.time() < end:
        if any(rx.search(console.buf) for rx in questions.values()):
            return "firstlogin"
        enter = ENTER_RE.search(console.buf)
        if enter:
            console.buf = console.buf[enter.end() :]  # answered once: the questions follow it
            console.send("\r", pace=0)
            continue
        for name, rx in prompts.items():
            if rx.search(console.buf):
                return name
        console._read(min(1.0, max(0.0, end - time.time())))
    console.send("\x03", pace=0)
    name, _ = console.expect(prompts, 5)
    return name


def skip_locales(console, menu):
    """ "Skip generating locales" chosen in armbian-firstlogin's locale menu (bash's select), whose number is its last.
    With no country found, the menu lists every locale (some 340 lines), and characters are lost on the line coming
    back (a Lyra Zero W at 1 500 000 baud, 2026-10-29), so the Skip line itself may not arrive. Then the numbers are
    tried downwards from above the highest one seen: select asks again for a number past its end, and the first one
    it takes is the last entry, Skip. A real locale's number is never sent: choosing one starts tzselect's menus."""
    found = re.search(r"(\d+)\) Skip generating locales", menu)
    if found:
        console.send(found.group(1) + "\r")
        return
    seen = {int(n) for n in re.findall(r"(?<![\d)])(\d{1,4})\) ", menu)}
    # A number among its neighbours, not a garbled one ("61)" and "233)" run together read as "6133)").
    solid = [n for n in seen if sum(k in seen for k in (n - 1, n - 2, n - 3)) >= 2]
    top = (max(solid) if solid else 500) + 30
    others = {k: v for k, v in FIRST.items() if k not in ("locale", "shellprompt")}
    for number in range(top, 0, -1):
        console.send(f"{number}\r")
        end = time.time() + 8
        again = False
        while time.time() < end:
            asked = FIRST["locale"].search(console.buf)
            if asked:
                console.buf = console.buf[asked.end() :]
                again = True
                break
            if any(rx.search(console.buf) for rx in others.values()):
                return  # taken: the next question has come
            console._read(0.3)
        if not again:
            return  # taken: nothing asked again (the next question is still on its way)
    raise Refused(
        M(
            "rkserial.locales",
            "The board's locale menu did not take any answer: characters were lost on the serial line.",
        )
    )


def first_login(console, root_password, user, user_password, real_name, timeout=180):
    """armbian-firstlogin answered to the end, its root shell reached. Refused when the board says the passwords did
    not match or the user name was not taken three times over (characters lost on the way in)."""
    misses = 0
    end = time.time() + timeout
    while time.time() < end:
        name, m = console.expect(FIRST, max(1, end - time.time()))
        if name is None:
            break
        if name in ("root1", "root2"):
            console.send(root_password + "\r")
        elif name in ("user1", "user2"):
            console.send(user_password + "\r")
        elif name == "shell":
            console.send(m.group(1) + "\r")
        elif name == "wifi":
            console.send("n\r")  # the WiFi is set at the shell after, with nmcli
        elif name == "locale":
            skip_locales(console, m.string[: m.start()])  # the locale and time zone are set at the shell after
        elif name == "user":
            console.send(user + "\r")
        elif name == "realname":
            console.send("\x15" + real_name + "\r")  # Ctrl-U first: the question comes with the user's name filled in
        elif name in ("mismatch", "illegal"):
            misses += 1
            if misses >= 3:
                raise Refused(
                    M(
                        "rkserial.garbled",
                        "The board did not take the answers as sent ({what}): characters were lost on the serial line. "
                        "Check its wiring and try again.",
                        what="the passwords did not match" if name == "mismatch" else "the user name",
                    )
                )
        elif name == "shellprompt":
            return
    raise Refused(
        M("rkserial.stuck", "The board stopped answering its first-boot questions before its setup was finished.")
    )


def _netplan_wifi(do, choices):
    """The WiFi on an image without NetworkManager (Armbian's minimal images: netplan and systemd-networkd), as
    armbian-firstlogin does it there: /etc/netplan/30-wifis-dhcp.yaml (0600), the password as a block scalar so YAML
    takes it as written, the device up, then netplan apply. Every value has passed rksetup.problem, so it is safe
    inside double quotes."""
    device = do("iw dev | awk '$1==\"Interface\"{print $2; exit}'", "wifidev").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", device):
        raise Refused(M("rkserial.no-wifi-device", "The board has no WiFi device that it can name."))
    point = (
        '        \\"%s\\":\\n          password: |-\\n            %s\\n'
        if choices.get("wifi_password")
        else '        \\"%s\\": {}\\n'
    )
    values = f'"{choices["ssid"]}"' + (f' "{choices["wifi_password"]}"' if choices.get("wifi_password") else "")
    return do(
        'umask 077; printf "# Written by irate-box over the serial console\\nnetwork:\\n  wifis:\\n    %s:\\n'
        "      dhcp4: yes\\n      dhcp6: yes\\n      access-points:\\n" + point + f'" "{device}" {values}'
        " > /etc/netplan/30-wifis-dhcp.yaml && chmod 600 /etc/netplan/30-wifis-dhcp.yaml"
        f" && ip link set {device} up && netplan apply 2>&1 && echo netplan applied",
        "wifi",
        timeout=90,
    )


def configure(console, choices):
    """At the root shell: the rest of the choices set, each read back. {said: [...]} of what was done, and the
    board's address once on the network."""
    said = []

    def do(command, key, timeout=60):
        out = console.run(command, key, timeout)
        if out is None:
            raise Refused(M("rkserial.no-answer", "The board did not answer at its shell ({step}).", step=key))
        return out

    # The console 500 columns wide first, sent bare (a line too short to wrap): bash's line editor redraws a line
    # that passes column 80, and on this console a wrapped command was then often not run at all (a Lyra Zero W,
    # 2026-10-29: 8 long lines of 8 went through once it was wide, about half before).
    console.buf = ""
    console.send("stty cols 500\r")
    console.expect({"shell": SHELL_RE}, 10)
    # History expansion off next: the console's bash is interactive, so a "!" inside double quotes (a WiFi password
    # such as "S!dp…") is expanded, and the command fails with "event not found" (the same board).
    do("set +H", "nohistory")
    name = choices["hostname"]
    do(f'hostnamectl set-hostname "{name}" && sed -i "s/^127\\.0\\.1\\.1.*/127.0.1.1\\t{name}/" /etc/hosts', "name")
    if do("hostname", "readname").strip() != name:
        raise Refused(M("rkserial.name", "The board did not take its name {name}.", name=name))
    said.append(f"name {name}")
    if choices.get("timezone"):
        do(f'timedatectl set-timezone "{choices["timezone"]}"', "tz")
        said.append(f"time zone {choices['timezone']}")
    if choices.get("locale"):
        loc = choices["locale"]
        do(
            f'sed -i "s/^# *\\({loc} \\)/\\1/" /etc/locale.gen; locale-gen >/dev/null 2>&1; update-locale LANG="{loc}"',
            "locale",
            timeout=300,
        )
        said.append(f"locale {loc}")
    for mark, who, home in (("keyroot", "root", "/root"), ("keyuser", choices["user"], f"/home/{choices['user']}")):
        for key in choices.get("keys", []):
            do(
                f'mkdir -p {home}/.ssh && grep -qxF "{key}" {home}/.ssh/authorized_keys 2>/dev/null '
                f'|| echo "{key}" >> {home}/.ssh/authorized_keys; chown -R "{who}:" {home}/.ssh; chmod 700 {home}/.ssh; '
                f"chmod 600 {home}/.ssh/authorized_keys",
                mark,
            )
    if choices.get("keys"):
        said.append("keys added")
    do("systemctl disable --now armbian-web-config 2>/dev/null; true", "webconfig")
    said.append("setup page stopped")
    if choices.get("ssid"):
        country = choices.get("country") or ""
        if country:
            do(f'iw reg set "{country}" 2>/dev/null; true', "country")
        manager = do("command -v nmcli >/dev/null && echo nm || echo netplan", "manager").strip()
        if manager == "netplan":
            out = _netplan_wifi(do, choices)
        else:
            pw = f' password "{choices["wifi_password"]}"' if choices.get("wifi_password") else ""
            out = do(f'nmcli dev wifi connect "{choices["ssid"]}"{pw} 2>&1', "wifi", timeout=90)
        if "successfully activated" not in out and "netplan applied" not in out:
            raise Refused(
                M(
                    "rkserial.wifi",
                    "The board did not join {ssid}: {detail}",
                    ssid=choices["ssid"],
                    detail=out.strip()[-160:],
                )
            )
        said.append(f"joined {choices['ssid']}")
    if choices.get("lock_root") and choices.get("keys"):
        do("passwd -l root", "lock")
        said.append("root's password locked")
    address = ""
    for _ in range(10):
        out = do("ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1", "addr")
        address = out.strip()
        if address:
            break
        time.sleep(3)
    return {"said": said, "address": address}


def setup(console, choices):
    """The whole of a first boot's setup over the console, from its first-login questions to its address. choices:
    {user, real_name, root_password, user_password (empty: generated), hostname, timezone, locale, ssid,
    wifi_password, country, keys, lock_root}. Returns {address, said, root_password, user_password}."""
    state = where(console, choices.get("listen", 300))
    if state == "initramfs":
        raise Refused(M("rkserial.initramfs", "The board stopped at its emergency shell: its SD card is not readable."))
    if state == "login":
        raise Refused(M("rkserial.login", "The board asks for a user to sign in: its first setup is done already."))
    if state is None:
        raise Refused(
            M(
                "rkserial.silent",
                "Nothing came from the board's console. Check the wiring (TX to RX, RX to TX, GND). If the board is "
                "already waiting at its first-boot questions, restart it with the console connected and Set up again: the box "
                "answers its questions as it boots.",
            )
        )
    if state == "shell":
        # A root shell already (its first login done): the rest is set, and no password is changed or said.
        return {**configure(console, choices), "root_password": None, "user_password": None}  # nosec B105
    root_password = choices.get("root_password") or generated()
    user_password = choices.get("user_password") or generated()
    first_login(console, root_password, choices["user"], user_password, choices.get("real_name") or choices["user"])
    done = configure(console, choices)
    return {**done, "root_password": root_password, "user_password": user_password}
