# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Which checks of each source ask the same question: the doctor's own
findings, the Security page's scan, debian-cis's checks and Lynis's tests. A finding listed here is
"about" the shared key, so the joint report merges what several sources say about one setting into
one item. Unlisted checks stand alone; the table grows as they are met. tests/sim_secdoctor_joint.py
checks every id here still exists where it can (the doctor's and the Security page's).

Also here: what each CIS section means on a box like this (CIS), and where each finding is put
right (DO), so every item on the page ends in one thing to do."""

import re

from irate_box.msg import M

# key: {title, ref (the security review's finding), and each source's ids}. debian-cis's by the check's
# name without its number (the benchmark renumbers: 1.9_install_updates is 1.9.1_install_updates in
# some versions, so numbered ids never match). Lynis's by its test, or for the two tests that report each setting
# apart (KRNL-6000: a sysctl key; SSH-7408: an sshd option) by "test:setting", from its report's details[].
XREF = {
    "kernel-links": {
        "title": M("xref.kernel-links", "Kernel link protections (protected_symlinks, protected_hardlinks)"),
        "ref": "F3",
        "doctor": ["kernel-links"],
        "security-page": ["kernel-links"],
        "lynis": ["KRNL-6000:fs.protected_symlinks", "KRNL-6000:fs.protected_hardlinks"],
    },
    "kernel-regular": {
        "title": M(
            "xref.kernel-regular",
            "Kernel protections for files and FIFOs in shared folders (protected_regular, protected_fifos)",
        ),
        "doctor": ["kernel-regular"],
        "lynis": ["KRNL-6000:fs.protected_regular", "KRNL-6000:fs.protected_fifos"],
    },
    "kernel-info": {
        "title": M("xref.kernel-info", "Kernel addresses and log hidden from ordinary accounts"),
        "ref": "F9",
        "doctor": ["kernel-info"],
        "security-page": ["kernel-info"],
        "lynis": ["KRNL-6000:kernel.kptr_restrict", "KRNL-6000:kernel.dmesg_restrict"],
    },
    "kernel-ptrace": {
        "title": M("xref.kernel-ptrace", "Process tracing between processes (Yama)"),
        "ref": "F9",
        "doctor": ["kernel-ptrace"],
        "lynis": ["KRNL-6000:kernel.yama.ptrace_scope"],
    },
    "core-dumps": {
        "title": M("xref.core-dumps", "Core dumps restricted"),
        "debian-cis": ["restrict_core_dumps"],
        "lynis": ["KRNL-5820", "KRNL-6000:fs.suid_dumpable", "KRNL-6000:kernel.suid_dumpable"],
    },
    "aslr": {
        "title": M("xref.aslr", "Address space randomisation"),
        "debian-cis": ["enable_randomized_vm_placement"],
        "lynis": ["KRNL-6000:kernel.randomize_va_space"],
    },
    "ssh-root-login": {
        "title": M("xref.ssh-root-login", "SSH: signing in as root"),
        "security-page": ["ssh-root"],
        "debian-cis": ["disable_root_login"],
        "lynis": ["SSH-7408:PermitRootLogin"],
    },
    "ssh-password": {
        "title": M("xref.ssh-password", "SSH: passwords rather than keys"),
        "security-page": ["ssh-password"],
        "debian-cis": ["ssh_auth_pubk_only"],
    },
    "ssh-forwarding": {
        "title": M("xref.ssh-forwarding", "SSH: forwarding"),
        "security-page": ["ssh-forwarding"],
        "debian-cis": ["disable_x11_forwarding", "disable_ssh_allow_tcp_forwarding"],
        "lynis": ["SSH-7408:AllowTcpForwarding", "SSH-7408:X11Forwarding", "SSH-7408:AllowAgentForwarding"],
    },
    "firewall": {
        "title": M("xref.firewall", "Hotspot firewall (what a guest on the hotspot can reach)"),
        "doctor": ["firewall"],
        "security-page": ["firewall"],
        "debian-cis": ["net_fw_default_policy_drop"],
        "lynis": ["FIRE-4512", "FIRE-4590"],
    },
    "security-updates": {
        "title": M("xref.security-updates", "Debian's security updates"),
        "security-page": ["security-updates"],
        "debian-cis": ["install_updates"],
        "debsecan": [],
        "lynis": ["PKGS-7392"],
    },
    "unattended": {
        "title": M("xref.unattended", "Automatic security updates"),
        "security-page": ["unattended"],
        "lynis": ["PKGS-7420"],
    },
    "sudo-all": {
        "title": M("xref.sudo-all", "Passwordless sudo rules"),
        "doctor": ["acct-sudo"],
        "security-page": ["sudo-nopasswd"],
        "debian-cis": ["acc_sudoers_no_all", "sudo_no_nopasswd"],
    },
    "empty-passwords": {
        "title": M("xref.empty-passwords", "Accounts with an empty password"),
        "doctor": ["acct-nopw"],
        "debian-cis": ["remove_empty_password_field", "etc_shadow_fields_not_empty"],
        "lynis": ["AUTH-9283"],
    },
    "apt-trust": {
        "title": M("xref.apt-trust", "Repository keys trusted for every repository"),
        "doctor": ["image-apt-trust"],
        "security-page": ["apt-trust"],
    },
    "logs": {
        "title": M("xref.logs", "Logs kept only in memory"),
        "security-page": ["logs-ram"],
        "debian-cis": ["journald_write_persistent"],
    },
    "proc-hidden": {
        "title": M("xref.proc-hidden", "Process visibility between accounts"),
        "ref": "F9",
        "doctor": ["cmdlines-proc"],
        "security-page": ["proc-hidden"],
    },
    "tls": {
        "title": M("xref.tls", "HTTPS: the certificate and the web server"),
        "doctor": ["tls"],
        "lynis": ["HTTP-6710"],
    },
}
_BY = {(src, i): key for key, e in XREF.items() for src, ids in e.items() if isinstance(ids, list) for i in ids}

# Lynis's tests that ask what a CIS section asks (its key is that section's, "cis-5.3"), so the two
# merge into one item; and, for the settings Lynis reports one by one, the section the rest fall in.
LYNIS_CIS = {
    "BOOT-5122": "1.5",
    "FINT-4350": "1.4",
    "BANN-7126": "1.8",
    "BANN-7130": "1.8",
    "TIME-3104": "2.2",
    "TIME-3185": "2.2",
    "ACCT-9628": "4.1",
    "ACCT-9630": "4.1",
    "LOGG-2154": "4.2",
    "LOGG-2146": "4.4",
    "SCHD-7704": "5.1",
    "AUTH-9262": "5.3",
    "AUTH-9286": "5.4",
    "AUTH-9282": "5.4",
    "AUTH-9328": "5.4",
    "HOME-9304": "6.2",
    "FILE-7524": "6.1",
    "USB-1000": "99.1",
    "STRG-1846": "99.2",
    "NETW-3200": "3.4",
}
SYSCTL_REDIRECTS = re.compile(r"^net\.ipv[46]\.conf\.(all|default)\.send_redirects$")


def _lynis_section(check):
    """The CIS section a Lynis test (or "test:setting") falls in, or None."""
    test, _, setting = check.partition(":")
    if test in LYNIS_CIS:
        return LYNIS_CIS[test]
    if test == "SSH-7408":
        return "5.2"
    if test == "KRNL-6000" and setting.startswith(("net.ipv4.", "net.ipv6.")):
        return "3.2" if SYSCTL_REDIRECTS.match(setting) else "3.3"
    return None


# A doctor finding about a port the Security page lists: the same thing.
SERVICE_OF = {("doctor", "addon-mqtt"): "tcp/1883"}  # and each declared service's add-on finding: _declared_about

# Findings that are a problem together, each alone only a warning:
# every named (source, id) must be present and not ok for the joint report to add the item.
COMPOUND = [
    {
        "key": "sudo-and-passwords",
        "title": M("compound.sudo-ssh.title", "Passwordless sudo, with SSH passwords"),
        "needs": [("security-page", "page-sudo-nopasswd"), ("security-page", "page-ssh-password")],
        "summary": M("compound.sudo-ssh", "An account has a passwordless sudo rule, and SSH accepts passwords."),
        "why": M("compound.sudo-ssh.why", "One password guessed over the network gives root, with no further step."),
        "fix": M("compound.sudo-ssh.fix", "On Security, disable SSH password sign-in, or remove the sudo rule."),
    },
    {
        "key": "disk-and-passwords",
        "title": M("compound.disk-ssh.title", "Disk group, with SSH passwords"),
        "needs": [("security-page", "page-group-disk"), ("security-page", "page-ssh-password")],
        "summary": M("compound.disk-ssh", "An account is in the disk group, and SSH accepts passwords."),
        "why": M(
            "compound.disk-ssh.why",
            "One guessed password lets an attacker read the raw card. This includes the password file, the CA key "
            "and the hash of the administrator password.",
        ),
        "fix": M(
            "compound.disk-ssh.fix",
            "On Security, remove the account from the disk group, or disable SSH password sign-in.",
        ),
    },
]
PORT_RE = re.compile(r"^port-(tcp|udp)-(\d+)$")
PORT_ANY_RE = re.compile(r"^(page-)?port-(tcp|udp)-\d+$")


def about(source, check):
    """The shared {kind, key} for a source's check id, or None. debian-cis's ids are matched by name
    (1.9.1_install_updates → install_updates)."""
    key = _BY.get((source, check.split("_", 1)[1] if source == "debian-cis" and "_" in (check or "") else check))
    if key:
        return {"kind": "setting", "key": key}
    if source == "lynis" and _lynis_section(check or ""):
        return {"kind": "setting", "key": f"cis-{_lynis_section(check)}"}
    if (source, check) in SERVICE_OF:
        return {"kind": "service", "key": SERVICE_OF[(source, check)]}
    decl = _declared_about(source, check)
    if decl:
        return {"kind": "service", "key": "{}/{}".format(*decl["listen"][0])}
    m = PORT_RE.match(check or "")
    if m:
        return {"kind": "service", "key": f"{m.group(1)}/{m.group(2)}"}
    return None


def title(key):
    """The title of a shared key: a message, or the key itself when the table does not know it."""
    return XREF.get(key, {}).get("title", key)


# What each source covers and cannot see, said once on the report.
COVERAGE = {
    "doctor": M(
        "coverage.doctor",
        "The Security doctor checks the design of this box against the security review of irate-box. It does not "
        "check general hardening.",
    ),
    "security-page": M(
        "coverage.security-page",
        "The Security page checks what listens on the network, SSH, waiting security updates, the "
        "kernel settings and the groups of root.",
    ),
    "debsecan": M(
        "coverage.debsecan",
        "debsecan checks Debian packages only. It cannot see the vendor kernel, ttyd, SilverBullet or the "
        "Caddy release build.",
    ),
    "debian-cis": M(
        "coverage.debian-cis",
        "debian-cis compares settings with the CIS benchmark for general servers, not for a hotspot. It "
        "does not check software versions.",
    ),
    "lynis": M(
        "coverage.lynis",
        "Lynis is a second audit of settings, with its own tests. It does not check software versions.",
    ),
    "openvas": M(
        "coverage.openvas",
        "OpenVAS sees only what the network shows, from the computer where it ran. It can find software that "
        "debsecan cannot see.",
    ),
    "nmap": M("coverage.nmap", "nmap sees only which ports answer, from the computer where it ran."),
}


# --- the CIS benchmark's sections, said for a box like this --------------------------------------
# debian-cis groups its checks by the benchmark's sections; a section's number alone ("cis-4.1") told
# the owner nothing. Each: its name, what it asks in plain words, and how it stands on a small board
# serving a hotspot: "suggest" (worth doing, not urgent: the benchmark is a general server's), or
# "not-here" (the board can't, or it doesn't apply), with why. Sections not listed stay as found.
CIS = {
    "1.1": (
        M("cis.1-1.title", "Separate partitions for /tmp, /var, /home"),
        "suggest",
        M("cis.1-1.what", "The benchmark wants each on a partition of its own, mounted nodev, nosuid, noexec."),
        M(
            "cis.1-1.here",
            "This board runs from one SD card, and the image puts the system on one partition. Separate partitions need a "
            "different image, written to the card from the start. The box cannot change this while it runs.",
        ),
    ),
    "1.3": (
        M("cis.1-3.title", "sudo's own log"),
        "suggest",
        M("cis.1-3.what", "sudo can keep a log of every command that it runs, apart from the journal."),
        M(
            "cis.1-3.here",
            "This is optional. The journal already records each use of sudo. If you keep logs on the card (Security), "
            "these records remain after a power cut.",
        ),
    ),
    "1.4": (
        M("cis.1-4.title", "File integrity checking (Tripwire, AIDE)"),
        "suggest",
        M("cis.1-4.what", "A tool that records a checksum of each system file and reports any change."),
        M(
            "cis.1-4.here",
            "A full check of every file takes a long time and much card storage on a small board. The doctor's step "
            '"Installed code ownership" already checks the hub\'s own files.',
        ),
    ),
    "1.5": (
        M("cis.1-5.title", "Bootloader password"),
        "not-here",
        M("cis.1-5.what", "The benchmark sets a password on the GRUB boot menu."),
        M(
            "cis.1-5.here",
            "This board starts with U-Boot, not GRUB, so there is no GRUB menu to lock. A bootloader password also does "
            "not stop a person who removes the SD card. That person can read the card on another computer.",
        ),
    ),
    "1.6": (
        M("cis.1-6.title", "Process hardening (NX, address randomisation, core dumps)"),
        "suggest",
        M(
            "cis.1-6.what",
            "Memory protections that make an attack on a program fault less reliable. Privileged programs also write no "
            "core dumps.",
        ),
        M(
            "cis.1-6.here",
            "Address randomisation and core dumps are kernel settings (sysctl). NX is a feature of the processor, and the 32-bit ARM kernel reports it in an unusual way.",
        ),
    ),
    "1.7": (
        M("cis.1-7.title", "AppArmor"),
        "not-here",
        M("cis.1-7.what", "Mandatory access control: profiles that limit what each program can touch."),
        M(
            "cis.1-7.here",
            "The board's maker builds the kernel without AppArmor, and the box cannot add it. The hub's units use the "
            "sandbox of systemd instead. The Security doctor checks those sandbox lines unit by unit.",
        ),
    ),
    "1.8": (
        M("cis.1-8.title", "Sign-in banners"),
        "suggest",
        M("cis.1-8.what", "A warning text shown before sign-in (/etc/motd, /etc/issue)."),
        M(
            "cis.1-8.here",
            "A banner changes only the text shown before sign-in. It does not change who can sign in. It is optional.",
        ),
    ),
    "1.9": (
        M("cis.1-9.title", "Package updates"),
        "suggest",
        M("cis.1-9.what", "Updates waiting to be installed."),
        M("cis.1-9.here", "See Debian's security updates (Updates)."),
    ),
    "2.2": (
        M("cis.2-2.title", "Time synchronisation"),
        "suggest",
        M("cis.2-2.what", "A time service (systemd-timesyncd or chrony) keeping the clock right."),
        M(
            "cis.2-2.here",
            "The box keeps its own time (Clock). With no internet connection, there is nothing to synchronise with.",
        ),
    ),
    "3.1": (
        M("cis.3-1.title", "IPv6"),
        "suggest",
        M("cis.3-1.what", "The benchmark turns IPv6 off where it is not used."),
        M(
            "cis.3-1.here",
            'Your network can use IPv6. The doctor\'s step "Reached from the internet" checks what IPv6 exposes.',
        ),
    ),
    "3.2": (
        M("cis.3-2.title", "Sending ICMP redirects"),
        "suggest",
        M(
            "cis.3-2.what",
            "The benchmark stops a computer that is not a router from sending routing advice (ICMP redirects) to other "
            "computers.",
        ),
        M("cis.3-2.here", "You can disable them on the box. The internet access of hotspot guests continues to work."),
    ),
    "3.3": (
        M("cis.3-3.title", "Network parameters (source routing, redirects, martians, router adverts)"),
        "suggest",
        M("cis.3-3.what", "Kernel network settings that refuse forged routes and log impossible addresses."),
        M(
            "cis.3-3.here",
            "You can set most of them with sysctl, with no effect on the hub or its guests. If your network gives IPv6 "
            "addresses by router advertisements, keep accept_ra enabled.",
        ),
    ),
    "3.4": (
        M("cis.3-4.title", "Uncommon network protocols (DCCP, SCTP, RDS, TIPC)"),
        "suggest",
        M(
            "cis.3-4.what",
            "Kernel modules for transport protocols that nothing on the box uses. A crafted packet can reach the code of each one.",
        ),
        M("cis.3-4.here", "Optional. A file in /etc/modprobe.d can stop them from loading."),
    ),
    "4.1": (
        M("cis.4-1.title", "Auditing (auditd)"),
        "not-here",
        M("cis.4-1.what", "The kernel's audit system, recording security events in detail."),
        M(
            "cis.4-1.here",
            "The board's maker builds the kernel without CONFIG_AUDIT, so auditd cannot run on this board. Only a kernel "
            "from the maker with that option can change this. Meanwhile, the journal records sign-ins and each use of "
            "sudo.",
        ),
    ),
    "4.2": (
        M("cis.4-2.title", "Logging (syslog-ng, a persistent journal)"),
        "suggest",
        M("cis.4-2.what", "Logs kept on disk, with tight permissions, and sent to another machine."),
        M(
            "cis.4-2.here",
            "On Security, keep logs on the card, so that the logs remain after a restart. A remote log server needs a "
            "second computer.",
        ),
    ),
    "4.4": (
        M("cis.4-4.title", "logrotate's permissions"),
        "suggest",
        M("cis.4-4.what", "Rotated logs created readable by root only."),
        M("cis.4-4.here", "Change it by hand: set create 0640 in /etc/logrotate.conf."),
    ),
    "5.1": (
        M("cis.5-1.title", "cron's files' permissions"),
        "suggest",
        M("cis.5-1.what", "/etc/crontab and /etc/cron.* readable by root only."),
        M("cis.5-1.here", "Make these files readable by root only. The command below does this."),
    ),
    "5.2": (
        M("cis.5-2.title", "SSH server settings"),
        "suggest",
        M("cis.5-2.what", "sshd's settings: who can sign in, how, for how long, with which ciphers."),
        M(
            "cis.5-2.here",
            "The settings that matter (root sign-in, passwords, forwarding) are choices on Security. The rest are fine-tuning in /etc/ssh/sshd_config.d.",
        ),
    ),
    "5.3": (
        M("cis.5-3.title", "Password quality and lockout (PAM)"),
        "suggest",
        M("cis.5-3.what", "Rules for strong passwords and locking an account after failed tries."),
        M(
            "cis.5-3.here",
            "These rules apply only where a person signs in with a password. If SSH accepts keys only, that place is the "
            "console.",
        ),
    ),
    "5.4": (
        M("cis.5-4.title", "Password ageing, umask, shell timeout"),
        "suggest",
        M("cis.5-4.what", "Passwords that expire, new files private by default, and idle shells that are closed."),
        M(
            "cis.5-4.here",
            "This is optional on a box with one administrator. Passwords that expire often cause people to write them "
            "down.",
        ),
    ),
    "5.6": (
        M("cis.5-6.title", "Who can use su"),
        "suggest",
        M("cis.5-6.what", "su limited to the members of one group."),
        M(
            "cis.5-6.here",
            "This is optional. The password of root is locked, so su cannot open a root shell with a password.",
        ),
    ),
    "6.1": (
        M("cis.6-1.title", "World-writable, unowned and setuid files"),
        "suggest",
        M("cis.6-1.what", "Files anyone can change, files with no owning user, and programs that run as root."),
        M("cis.6-1.here", "Run the command below to list these files. Then examine each file in the list."),
    ),
    "6.2": (
        M("cis.6-2.title", "Users' own files' permissions"),
        "suggest",
        M("cis.6-2.what", "Dot files in home folders writable by others."),
        M(
            "cis.6-2.here",
            "For each file that the audit names, remove the write permission for other users (chmod o-w).",
        ),
    ),
    "99.1": (
        M("cis.99-1.title", "USB devices"),
        "not-here",
        M("cis.99-1.what", "The benchmark blocks USB storage."),
        M(
            "cis.99-1.here",
            "The box reads books, toolkits and updates from USB drives. If you block USB storage, these features stop.",
        ),
    ),
    "99.2": (
        M("cis.99-2.title", "FireWire storage"),
        "not-here",
        M("cis.99-2.what", "The benchmark blocks FireWire storage (firewire-ohci, firewire-sbp2)."),
        M("cis.99-2.here", "The boards irate-box runs on have no FireWire port."),
    ),
    "99.3": (
        M("cis.99-3.title", "TCP wrappers (hosts.deny)"),
        "not-here",
        M("cis.99-3.what", "An old access list for network services."),
        M(
            "cis.99-3.here",
            "The sshd of Debian 13 does not read it any more. The hotspot firewall on Security is what filters the hotspot.",
        ),
    ),
    "99.4": (
        M("cis.99-4.title", "Kernel audit support"),
        "not-here",
        M("cis.99-4.what", "CONFIG_AUDIT in the kernel, for auditd."),
        M(
            "cis.99-4.here",
            "The board's maker builds the kernel without it, so auditd cannot run. Only a kernel from the maker with "
            "CONFIG_AUDIT can change this.",
        ),
    ),
    "99.5": (
        M("cis.99-5.title", "More SSH settings"),
        "suggest",
        M("cis.99-5.what", "Further sshd hardening: keys only, rekeying, strict modes, where keys can come from."),
        M("cis.99-5.here", "Keys only is a choice on Security. The rest are fine-tuning in /etc/ssh/sshd_config.d."),
    ),
}
# The command for a section, where there is one to copy (shown apart from the words).
CIS_CMD = {
    "3.2": "printf 'net.ipv4.conf.all.send_redirects=0\\nnet.ipv4.conf.default.send_redirects=0\\n' | sudo tee /etc/sysctl.d/61-redirects.conf && sudo sysctl --system",
    "3.4": "printf 'install %s /bin/true\\n' dccp sctp rds tipc | sudo tee /etc/modprobe.d/60-rare-protocols.conf",
    "5.1": "sudo chmod og-rwx /etc/crontab /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /etc/cron.d",
    "6.1": "sudo find / -xdev \\( -perm -0002 -type f -o -nouser -o -nogroup \\) -ls",
}


def cis(section):
    """(title, tier, what, here) for a CIS section, or None."""
    return CIS.get(section)


# --- where each finding is put right ------------------------------------------------------------
# One answer per finding, so the page ends every item in one thing to do: the Security page's own
# buttons (found through the shared key at the time), else a place on /admin ({go: element id, where}),
# a command ({cmd}; a finding may carry its own exact one), the owner's words for it ({say}), the deep audit again ({act: "deep"}), the box doctor's repair ({repair: its choice},
# "Run the installer again"), or the hub's own work ({hub: why}): what an update of irate-box fixes. Matched by source and a pattern on the finding's id; the
# first match wins; anything unmatched shows its fix text as it is.
HUB_SANDBOX = M(
    "do.hub-sandbox",
    "These units are part of irate-box, and you cannot set their sandbox lines from the administration pages. "
    "irate-box adds the lines in its updates, one unit at a time, because each change needs a restart and a "
    "test of that unit. Keep the hub updated, so that the box gets each change.",
)
HUB_SETUP = M(
    "do.hub-setup",
    "The hub's own setup differs from what its installer writes. Run the installer again, with the version of this "
    "box and its recorded options. The installer then writes the setup again. If this finding comes back, a program"
    " or a person on the box changed the setup again. Find that change first.",
)
HUB_SANDBOX_TERM = M(
    "do.hub-sandbox-term",
    "The terminal's unit is part of irate-box, and you cannot set its sandbox lines from the "
    "administration pages. irate-box adds the lines in its updates. If you do not use the terminal, "
    "remove its add-on.",
)
UPDATES_DEBIAN = M("go.updates-debian", "Updates → Debian's security updates")
DO = [
    # Bluetooth: switched off by the Box doctor's own action (health.py idle_switch), which keeps how it was, so its
    # finding there offers Switch it back on.
    (
        "*",
        r"^image-bluetooth$",
        {
            "health": {"choice": "idle-off:bluetooth.service", "label": M("do.bluetooth.label", "Disable Bluetooth")},
            "say": M(
                "do.bluetooth",
                "Nothing on the hub uses Bluetooth. Disable it: it stops now and at boot. The "
                "Box doctor then lists it, with a button to enable it again.",
            ),
        },
    ),
    ("*", r"^(page-)?firewall$", {"go": "sec-firewall", "where": M("go.sec-firewall", "Security → Hotspot firewall")}),
    ("*", r"^tls|^accounts-http$", {"go": "security-https", "where": M("go.security-https", "Security → HTTPS")}),
    ("*", r"^(page-)?unattended$", {"go": "updates-debian", "where": UPDATES_DEBIAN}),
    (
        "*",
        r"^(page-)?security-updates$|^debsecan-(?!kit-|data$|tool$|unfixed$)",
        {"go": "updates-debian", "where": UPDATES_DEBIAN},
    ),
    ("debian-cis", r"^cis-1\.9", {"go": "updates-debian", "where": UPDATES_DEBIAN}),
    (
        "*",
        r"^debsecan-(kit-|data$|tool$)|^offline-(kits|lists)$",
        {"go": "toolkits", "where": M("go.toolkits", "Toolkits")},
    ),
    (
        "*",
        r"^offline-bundle$",
        {"go": "backup-kit", "where": M("go.backup-kit", "Updates and backup → Content export")},
    ),
    ("*", r"^offline-update$", {"go": "updates", "where": M("go.updates", "Updates")}),
    ("*", r"^git-(everyone|readonly)$", {"go": "git", "where": M("go.git", "Git")}),
    ("*", r"^accounts-hash$", {"go": "accounts", "where": M("go.accounts", "Accounts")}),
    ("*", r"^accounts-gate$", {"go": "apps", "where": M("go.apps", "Apps")}),
    ("*", r"^addons-", {"go": "addons", "where": M("go.addons", "Add-ons")}),
    (
        "*",
        r"^image-apt-daily$",
        {"go": "pkg-makers", "where": M("go.pkg-makers", "Updates → Packages from their makers")},
    ),
    (
        "*",
        r"^reach-ssh$",
        {"go": "sec-ssh-password", "where": M("go.sec-ssh-password", "Security → SSH password sign-in")},
    ),
    ("*", r"^reach-web$", {"go": "access", "where": M("go.network-tailscale", "Access → Remote access")}),
    # ASLR and core dumps, from whichever source said so (the CIS checks' own scripts state no plain
    # setting for core dumps' limits line).
    (
        "*",
        r"_enable_randomized_vm_placement$|^lynis-KRNL-6000-kernel\.randomize_va_space$",
        {"cmd": "echo kernel.randomize_va_space=2 | sudo tee /etc/sysctl.d/61-aslr.conf && sudo sysctl --system"},
    ),
    (
        "*",
        r"_restrict_core_dumps$|^lynis-KRNL-5820|^lynis-KRNL-6000-(fs|kernel)\.suid_dumpable$",
        {
            "cmd": "echo '* hard core 0' | sudo tee /etc/security/limits.d/60-no-core.conf && echo fs.suid_dumpable=0 | sudo tee /etc/sysctl.d/61-no-core.conf && sudo sysctl --system"
        },
    ),
    ("*", r"^image-kernel$", {"cmd": "apt list --upgradable 2>/dev/null | grep linux-image"}),
    ("*", r"^(debian-cis|lynis)-old$", {"act": "deep", "where": M("do.deep", "Run the deep audit again")}),
    # Lynis's tests met on a real board's deep audit with no answer of their own.
    (
        "lynis",
        r"^lynis-HTTP-6710$",
        {
            "go": "security-https",
            "where": M("go.security-https", "Security → HTTPS"),
            "say": M(
                "do.lynis-http",
                "Lynis looks for HTTPS in the sites of nginx. The hub's HTTPS is its "
                "own web server, set on Security → HTTPS.",
            ),
        },
    ),
    (
        "lynis",
        r"^lynis-NETW-3200",
        {
            "cmd": "printf 'install %s /bin/true\\n' dccp sctp rds tipc | sudo tee /etc/modprobe.d/60-rare-protocols.conf"
        },
    ),
    (
        "lynis",
        r"^lynis-STRG-1846$",
        {
            "say": M(
                "do.lynis-firewire", "The boards that irate-box runs on have no FireWire port. There is nothing to do."
            )
        },
    ),
    ("*", r"^notes-login$", {"go": "apps", "where": M("go.apps-notes", "Apps (Notes: who can open it)")}),
    (
        "*",
        r"^addon-tailscale(-|$)",
        {
            "go": "access",
            "where": M("go.access-remote", "Access → Remote access"),
            "say": M(
                "do.tailscale",
                "The image includes Tailscale. Its service settings, its sandbox too, come from"
                " Tailscale, and irate-box does not change them. If you do not use Tailscale "
                "for remote access, disable it on Access → Remote access.",
            ),
        },
    ),
    (
        "*",
        r"^addon-mqtt(-|$)|^(page-)?port-tcp-1883$",
        {
            "go": "addons",
            "where": M("go.addons", "Add-ons"),
            "say": M(
                "do.mqtt",
                "Meshtastic nodes on your network publish to the broker. So the "
                "broker that the box installs accepts connections from the "
                "network with no sign-in. It accepts the topic msh/# only. If no"
                " node of yours uses it, remove the MQTT add-on. If your nodes "
                "use it, select Accept risk.",
            ),
        },
    ),
    ("*", r"^addon-term(-|$)", {"hub": HUB_SANDBOX_TERM, "go": "addons", "where": M("go.addons-term", "Add-ons")}),
    ("*", r"^units-|^cmdlines-", {"hub": HUB_SANDBOX}),
    (
        "*",
        r"^(notes-(user|shell|proxy)|admin-loopback|addons-origin|front-|git-public|folders-control|folders-ci|code-)",
        {"hub": HUB_SETUP, "repair": "rerun-install"},
    ),
    (
        "*",
        r"^(hub-body|admin-csrf)$",
        {
            "hub": M(
                "do.hub-flaw",
                "This is a fault in the hub itself. You cannot correct it from the "
                "administration pages. An update of irate-box corrects it. Install it from "
                "Updates.",
            ),
            "go": "updates",
            "where": M("go.updates", "Updates"),
        },
    ),
]
_DO = [(src, re.compile(pat), do) for src, pat, do in DO]


def _declared_about(source, fid):
    """The declared service (an add-on's manifest network block) a finding is about: the doctor's addon-<id>,
    or a listener finding on one of its ports. None otherwise."""
    from irate_box.hub import services

    m = re.match(r"^addon-([a-z0-9-]+)$", fid or "")
    if m and source == "doctor":
        # addon-<id>, or addon-<id>-<point> (step_addons: one finding for each point its checks find).
        sid = m.group(1)
        while sid:
            found = services.by_id(sid)
            if found:
                return found
            sid = sid.rpartition("-")[0]
        return None
    m = re.match(r"^(?:page-)?port-(tcp|udp)-(\d+)$", fid or "")
    return services.by_port(m.group(1), int(m.group(2))) if m else None


# Findings nothing the owner can change on the box puts right: irate-box's own work still to do, or a limit of the
# board or its OS. They are said, but they leave the review count for a fold of their own ("For information"), each
# with why. (The plan's items, for whoever fixes them: units-root 27a, units-hub 129, units-ci-loopback 130. 131,
# cmdlines-proc, has its fix on the Security page: the key proc-hidden.)
INFO = {
    "units-root": M(
        "info.units-root",
        "The root units of the hub are part of irate-box. A later update of irate-box adds the sandbox lines "
        "that they are missing. Until then, a fault in one of these units runs as root with fewer limits than "
        "it can have. The finding lists the missing lines for each unit. Keep the hub updated, so that the box"
        " gets the change.",
    ),
    "units-hub": M(
        "info.units-hub",
        "These units are part of irate-box. A later update of irate-box removes the root capabilities that the "
        "build unit does not use. Until then, a fault in one of these services can reach more of the box than it"
        " needs. Keep the hub updated, so that the box gets the change.",
    ),
    "units-ci-loopback": M(
        "info.units-ci",
        "The build unit is part of irate-box. A later update of irate-box keeps builds off the loopback "
        "address of the box, and still lets them download from the internet. Until then, a build can "
        "connect to the hub on the box itself. The finding says what a build can reach there.",
    ),
    "image-kernel": M(
        "info.image-kernel",
        "This is a limit of the board. The board's maker builds its kernel, and Debian's security tracker "
        "does not cover vendor kernels. So debsecan cannot list the known vulnerabilities of this kernel. "
        "Fixes come only from the maker, as a new kernel package or a new image. When the box is online, "
        "install the maker's kernel updates with apt.",
    ),
}


def info(source, fid):
    """Why a finding is information only (INFO), or None. Only the doctor's own findings are."""
    return INFO.get(fid) if source == "doctor" else None


def do_for(source, fid):
    """Where a finding is put right (DO), or None. A declared service's (its add-on's state, or what listens):
    its own words, on Add-ons."""
    decl = _declared_about(source, fid)
    # Its state, what listens, or the port: not every point of it (addon-<id>-<point> has a fix of its own).
    if decl and fid not in (f"addon-{decl['id']}", f"addon-{decl['id']}-listen") and not PORT_ANY_RE.match(fid or ""):
        decl = None
    if decl and decl["risk"] != "ok":
        return {
            "go": "addons",
            "where": M("go.addons", "Add-ons"),
            "say": M(
                "do.declared",
                "{says} If nobody uses it, remove the add-on. If people use it, select Accept risk. The hotspot "
                "firewall on Security controls whether hotspot guests can reach it.",
                says=decl["says"],
            ),
        }
    return next((do for src, pat, do in _DO if src in ("*", source) and pat.search(fid or "")), None)
