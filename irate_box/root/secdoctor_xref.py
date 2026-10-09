# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Which checks of each source ask the same question (security-doctor-plan §5.2): the doctor's own
findings, the Security page's scan, debian-cis's checks and Lynis's tests. A finding listed here is
"about" the shared key, so the joint report merges what several sources say about one setting into
one item. Unlisted checks stand alone; the table grows as they are met. tests/sim_secdoctor_joint.py
checks every id here still exists where it can (the doctor's and the Security page's).

Also here (Tom, 2026-10-09: "lists of emoji checks or warnings with vague information is totally
unactionable"): what each CIS section means on a box like this (CIS), and where each finding is put
right (DO), so every item on the page ends in one thing to do."""
import re

# key: {title, ref (the security review's finding), and each source's ids}. debian-cis's by the check's
# name without its number (the benchmark renumbers: 1.9_install_updates was 1.9.1_install_updates on the
# Lyra, so numbered ids never matched). Lynis's by its test, or for the two tests that report each setting
# apart (KRNL-6000: a sysctl key; SSH-7408: an sshd option) by "test:setting", from its report's details[].
XREF = {
    "kernel-links": {"title": "Kernel link protections (protected_symlinks, protected_hardlinks)", "ref": "F3",
                     "doctor": ["kernel-links"], "security-page": ["kernel-links"],
                     "lynis": ["KRNL-6000:fs.protected_symlinks", "KRNL-6000:fs.protected_hardlinks"]},
    "kernel-regular": {"title": "Kernel protections for files and FIFOs in shared folders (protected_regular, protected_fifos)",
                       "doctor": ["kernel-regular"], "lynis": ["KRNL-6000:fs.protected_regular", "KRNL-6000:fs.protected_fifos"]},
    "kernel-info": {"title": "Kernel addresses and log hidden from ordinary accounts", "ref": "F9",
                    "doctor": ["kernel-info"], "security-page": ["kernel-info"],
                    "lynis": ["KRNL-6000:kernel.kptr_restrict", "KRNL-6000:kernel.dmesg_restrict"]},
    "kernel-ptrace": {"title": "ptrace between processes (Yama)", "ref": "F9", "doctor": ["kernel-ptrace"],
                      "lynis": ["KRNL-6000:kernel.yama.ptrace_scope"]},
    "core-dumps": {"title": "Core dumps restricted", "debian-cis": ["restrict_core_dumps"],
                   "lynis": ["KRNL-5820", "KRNL-6000:fs.suid_dumpable", "KRNL-6000:kernel.suid_dumpable"]},
    "aslr": {"title": "Address space randomisation", "debian-cis": ["enable_randomized_vm_placement"],
             "lynis": ["KRNL-6000:kernel.randomize_va_space"]},
    "ssh-root-login": {"title": "SSH: logging in as root", "security-page": ["ssh-root"], "debian-cis": ["disable_root_login"],
                       "lynis": ["SSH-7408:PermitRootLogin"]},
    "ssh-password": {"title": "SSH: passwords rather than keys", "security-page": ["ssh-password"],
                     "debian-cis": ["ssh_auth_pubk_only"]},
    "ssh-forwarding": {"title": "SSH: forwarding", "security-page": ["ssh-forwarding"],
                       "debian-cis": ["disable_x11_forwarding", "disable_ssh_allow_tcp_forwarding"],
                       "lynis": ["SSH-7408:AllowTcpForwarding", "SSH-7408:X11Forwarding", "SSH-7408:AllowAgentForwarding"]},
    "firewall": {"title": "What a guest on the hotspot can reach (the floor)", "doctor": ["firewall"], "security-page": ["firewall"],
                 "debian-cis": ["net_fw_default_policy_drop"], "lynis": ["FIRE-4512", "FIRE-4590"]},
    "security-updates": {"title": "Debian's security updates", "security-page": ["security-updates"], "debian-cis": ["install_updates"],
                         "debsecan": [], "lynis": ["PKGS-7392"]},
    "unattended": {"title": "Automatic security updates", "security-page": ["unattended"], "lynis": ["PKGS-7420"]},
    "sudo-all": {"title": "Passwordless sudo rules", "doctor": ["acct-sudo"], "security-page": ["sudo-nopasswd"],
                 "debian-cis": ["acc_sudoers_no_all", "sudo_no_nopasswd"]},
    "empty-passwords": {"title": "Accounts with an empty password", "doctor": ["acct-nopw"],
                        "debian-cis": ["remove_empty_password_field", "etc_shadow_fields_not_empty"], "lynis": ["AUTH-9283"]},
    "apt-trust": {"title": "Repository keys trusted for every repository", "doctor": ["image-apt-trust"], "security-page": ["apt-trust"]},
    "logs": {"title": "Logs kept only in RAM", "security-page": ["logs-ram"], "debian-cis": ["journald_write_persistent"]},
}
_BY = {(src, i): key for key, e in XREF.items() for src, ids in e.items() if isinstance(ids, list) for i in ids}

# Lynis's tests that ask what a CIS section asks (its key is that section's, "cis-5.3"), so the two
# merge into one item; and, for the settings Lynis reports one by one, the section the rest fall in.
LYNIS_CIS = {
    "BOOT-5122": "1.5", "FINT-4350": "1.4", "BANN-7126": "1.8", "BANN-7130": "1.8",
    "TIME-3104": "2.2", "TIME-3185": "2.2",
    "ACCT-9628": "4.1", "ACCT-9630": "4.1", "LOGG-2154": "4.2", "LOGG-2146": "4.4",
    "SCHD-7704": "5.1", "AUTH-9262": "5.3", "AUTH-9286": "5.4", "AUTH-9282": "5.4", "AUTH-9328": "5.4",
    "HOME-9304": "6.2", "FILE-7524": "6.1", "USB-1000": "99.1",
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
SERVICE_OF = {("doctor", "addon-mqtt"): "tcp/1883"}   # and each declared service's add-on finding: _declared_about

# Findings that are a problem together, each alone only a warning (stance review 2026-10-08 §2):
# every named (source, id) must be present and not ok for the joint report to add the item.
COMPOUND = [
    {"key": "sudo-and-passwords", "title": "A passwordless sudo rule, and SSH takes passwords",
     "needs": [("security-page", "page-sudo-nopasswd"), ("security-page", "page-ssh-password")],
     "detail": "One guessed password on the network is root with no step in between.",
     "fix": "Turn SSH password logins off, or take the rule out (Security)."},
    {"key": "disk-and-passwords", "title": "An account in the disk group, and SSH takes passwords",
     "needs": [("security-page", "page-group-disk"), ("security-page", "page-ssh-password")],
     "detail": "One guessed password reads the raw card: the password file, the CA's key, the admin's hash.",
     "fix": "Take the account out of the disk group, or turn SSH password logins off (Security)."},
]
PORT_RE = re.compile(r"^port-(tcp|udp)-(\d+)$")


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
    return XREF.get(key, {}).get("title", key)


# What each source covers and cannot see (§5.5), said once on the report.
COVERAGE = {
    "doctor": "The box's own design, as its security review found it: not general hardening.",
    "security-page": "What listens, SSH, pending security updates, the kernel's settings, root's groups.",
    "debsecan": "Debian's packages only: blind to the vendor kernel, ttyd, SilverBullet, Caddy's release build.",
    "debian-cis": "Settings against the CIS benchmark (a general server's, not a hotspot's), not software versions.",
    "lynis": "A second audit of settings, with its own tests; not software versions.",
    "openvas": "Only what the network shows, from where it ran; including software debsecan can't see.",
    "nmap": "Only which ports answer, from where it ran.",
}


# --- the CIS benchmark's sections, said for a box like this --------------------------------------
# debian-cis groups its checks by the benchmark's sections; a section's number alone ("cis-4.1") told
# the owner nothing. Each: its name, what it asks in plain words, and how it stands on a small board
# serving a hotspot: "suggest" (worth doing, not urgent: the benchmark is a general server's), or
# "not-here" (the board can't, or it doesn't apply), with why. Sections not listed stay as found.
CIS = {
    "1.1": ("Separate partitions for /tmp, /var, /home", "suggest", "The benchmark wants each on a partition of its own, mounted nodev, nosuid, noexec.",
            "A board on one SD card has one partition; doing it means re-imaging."),
    "1.3": ("sudo's own log", "suggest", "sudo can keep a log of every command run through it, apart from the journal.",
            "Optional: the journal already records each use of sudo (with logs on the card, they survive a power cut)."),
    "1.4": ("File integrity checking (Tripwire, AIDE)", "suggest", "A tool that fingerprints the system's files and reports any change.",
            "Heavy for a small board; the doctor's \"Installed code\" step already checks the hub's own files and their owners."),
    "1.5": ("Bootloader password", "not-here", "The benchmark protects GRUB's menu with a password.",
            "This board boots with U-Boot, not GRUB; anyone holding the SD card owns the box regardless."),
    "1.6": ("Process hardening (NX, address randomisation, core dumps)", "suggest", "Memory protections that make an exploit harder to aim, and no core dumps from privileged programs.",
            "Address randomisation and core dumps are kernel settings (sysctl); NX is the CPU's, reported oddly by the 32-bit ARM kernel."),
    "1.7": ("AppArmor", "not-here", "Mandatory access control: profiles that confine what each program may touch.",
            "The board's vendor kernel is built without AppArmor; the hub's units use systemd's sandboxing instead."),
    "1.8": ("Login banners", "suggest", "A warning text shown before login (/etc/motd, /etc/issue).", "Cosmetic; optional."),
    "1.9": ("Package updates", "suggest", "Updates waiting to be installed.", "See Debian's security updates (Updates)."),
    "2.2": ("Time synchronisation", "suggest", "A time service (systemd-timesyncd or chrony) keeping the clock right.",
            "The box keeps its own time (the Clock pane): with no internet there is nothing to synchronise with."),
    "3.1": ("IPv6", "suggest", "The benchmark turns IPv6 off where it is not used.",
            "Your network may use it; the doctor's \"Reached from the internet\" step checks what IPv6 exposes."),
    "3.2": ("Sending ICMP redirects", "suggest", "A host that is not a router should not tell others how to route.",
            "sysctl net.ipv4.conf.all.send_redirects=0 (and .default): safe on the box, guests' internet included."),
    "3.3": ("Network parameters (source routing, redirects, martians, router adverts)", "suggest",
            "Kernel network settings that refuse forged routes and log impossible addresses.",
            "Mostly safe to set by sysctl; leave accept_ra on if your network gives IPv6 addresses by router adverts."),
    "4.1": ("Auditing (auditd)", "not-here", "The kernel's audit system, recording security events in detail.",
            "The vendor kernel is built without CONFIG_AUDIT, so auditd cannot run on this board."),
    "4.2": ("Logging (syslog-ng, a persistent journal)", "suggest", "Logs kept on disk, with tight permissions, and sent to another machine.",
            "Keep logs on the card (Security) covers what matters here; a remote log server needs a second machine."),
    "4.4": ("logrotate's permissions", "suggest", "Rotated logs created readable by root only.", "By hand: create 0640 in /etc/logrotate.conf."),
    "5.1": ("cron's files' permissions", "suggest", "/etc/crontab and /etc/cron.* readable by root only.",
            "By hand: sudo chmod og-rwx /etc/crontab /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /etc/cron.d"),
    "5.2": ("SSH server settings", "suggest", "sshd's settings: who may log in, how, for how long, with which ciphers.",
            "The ones that matter (root login, passwords, forwarding) are choices on Security; the rest are fine-tuning in /etc/ssh/sshd_config.d."),
    "5.3": ("Password quality and lockout (PAM)", "suggest", "Rules for strong passwords and locking an account after failed tries.",
            "They matter only where a password logs in: with SSH on keys only, that is the console."),
    "5.4": ("Password ageing, umask, shell timeout", "suggest", "Passwords that expire, new files private by default, idle shells logged out.",
            "Optional on a single-owner box; expiring passwords mostly make people write them down."),
    "5.6": ("Who may use su", "suggest", "su limited to the members of one group.", "Optional: root's password is locked, so su leads nowhere anyway."),
    "6.1": ("World-writable, unowned and setuid files", "suggest", "Files anyone may change, files with no owner, and programs that run as root.",
            "Look at the list from a shell: sudo find / -xdev \\( -perm -0002 -type f -o -nouser -o -nogroup \\) -ls"),
    "6.2": ("Users' own files' permissions", "suggest", "Dot files in home folders writable by others.", "By hand: chmod go-w on each file named."),
    "99.1": ("USB devices", "not-here", "The benchmark blocks USB storage.", "The box reads books and updates from USB sticks on purpose."),
    "99.3": ("TCP wrappers (hosts.deny)", "not-here", "An old access list for network services.",
            "Debian 13's sshd no longer reads it; the floor (Security) is what filters the hotspot."),
    "99.4": ("Kernel audit support", "not-here", "CONFIG_AUDIT in the kernel, for auditd.", "The vendor kernel is built without it."),
    "99.5": ("More SSH settings", "suggest", "Further sshd hardening: keys only, rekeying, strict modes, where keys may come from.",
             "Keys only is a choice on Security; the rest are fine-tuning in /etc/ssh/sshd_config.d."),
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
HUB_SANDBOX = "The hub's own units: hardening them is irate-box's work (its plan, item 27), done one unit at a time with a reboot each. Keep the hub updated."
HUB_SETUP = ("The hub's own setup is not as its installer leaves it. Running the installer again (this box's version, "
             "with its recorded options) puts it back; if it comes back after that, something on the box is changing it.")
DO = [
    ("*", r"^(page-)?firewall$", {"go": "sec-firewall", "where": "Security → What a guest on the hotspot can reach"}),
    ("*", r"^tls|^accounts-http$", {"go": "security-https", "where": "Security → HTTPS"}),
    ("*", r"^(page-)?unattended$", {"go": "updates-debian", "where": "Updates → Debian's security updates (off, download, or install them daily)"}),
    ("*", r"^(page-)?security-updates$|^debsecan-(?!kit-|data$|tool$|unfixed$)", {"go": "updates-debian", "where": "Updates → Debian's security updates"}),
    ("debian-cis", r"^cis-1\.9", {"go": "updates-debian", "where": "Updates → Debian's security updates"}),
    ("*", r"^debsecan-(kit-|data$|tool$)|^offline-(kits|lists)$", {"go": "toolkits", "where": "Toolkits"}),
    ("*", r"^offline-bundle$", {"go": "backup-kit", "where": "Updates and backup → Content export"}),
    ("*", r"^offline-update$", {"go": "updates", "where": "Updates"}),
    ("*", r"^git-(everyone|readonly)$", {"go": "git", "where": "Git"}),
    ("*", r"^accounts-hash$", {"go": "accounts", "where": "Accounts & users"}),
    ("*", r"^accounts-gate$", {"go": "apps", "where": "Apps"}),
    ("*", r"^addons-", {"go": "addons", "where": "Add-ons"}),
    ("*", r"^image-apt-daily$", {"go": "pkg-makers", "where": "Updates → Packages from their makers"}),
    ("*", r"^reach-ssh$", {"go": "sec-ssh-password", "where": "Security → SSH password login"}),
    ("*", r"^reach-web$", {"go": "network", "where": "Network (Tailscale, to reach the box from afar instead)"}),
    # ASLR and core dumps, from whichever source said so (the Lyra's deep audit, 2026-10-09: the CIS
    # checks' own scripts state no plain setting for core dumps' limits line).
    ("*", r"_enable_randomized_vm_placement$|^lynis-KRNL-6000-kernel\.randomize_va_space$",
     {"cmd": "echo kernel.randomize_va_space=2 | sudo tee /etc/sysctl.d/61-aslr.conf && sudo sysctl --system"}),
    ("*", r"_restrict_core_dumps$|^lynis-KRNL-5820|^lynis-KRNL-6000-(fs|kernel)\.suid_dumpable$",
     {"cmd": "echo '* hard core 0' | sudo tee /etc/security/limits.d/60-no-core.conf && echo fs.suid_dumpable=0 | sudo tee /etc/sysctl.d/61-no-core.conf && sudo sysctl --system"}),
    ("*", r"^kernel-ptrace$", {"cmd": "echo kernel.yama.ptrace_scope=1 | sudo tee /etc/sysctl.d/61-ptrace.conf && sudo sysctl --system"}),
    ("*", r"^kernel-regular$", {"cmd": "printf 'fs.protected_regular=2\\nfs.protected_fifos=2\\n' | sudo tee /etc/sysctl.d/61-regular.conf && sudo sysctl --system"}),
    ("*", r"^image-bluetooth$", {"cmd": "sudo systemctl disable --now bluetooth.service"}),
    ("*", r"^image-kernel$", {"cmd": "apt list --upgradable 2>/dev/null | grep linux-image"}),
    ("*", r"^(debian-cis|lynis)-old$", {"act": "deep", "where": "Run the deep audit again"}),
    ("*", r"^notes-login$", {"go": "apps", "where": "Apps (Notes: who may open it)"}),
    ("*", r"^addon-tailscale$", {"go": "access", "where": "Access → Remote access",
                                 "say": "Tailscale came with the OS, and its unit's sandbox is its makers'. If you don't use it to reach the box from afar, switch it off."}),
    ("*", r"^addon-mqtt$|^(page-)?port-tcp-1883$", {"go": "addons", "where": "Add-ons",
                                           "say": "Meshtastic nodes on your network publish to it, so it answers the network on purpose, with no login (topics limited to msh/#). If no node of yours uses it, remove the MQTT add-on; if they do, accept it as it is."}),
    ("*", r"^addon-term$", {"hub": HUB_SANDBOX, "go": "addons", "where": "Add-ons (the terminal can be removed if you don't use it)"}),
    ("*", r"^units-|^cmdlines-", {"hub": HUB_SANDBOX}),
    ("*", r"^(notes-(user|shell|proxy)|admin-loopback|addons-origin|front-|git-public|folders-control|folders-ci|code-)", {"hub": HUB_SETUP, "repair": "rerun-install"}),
    ("*", r"^(hub-body|admin-csrf)$", {"hub": "A flaw in the hub itself: an update of irate-box fixes it.", "go": "updates", "where": "Updates"}),
]
_DO = [(src, re.compile(pat), do) for src, pat, do in DO]


def _declared_about(source, fid):
    """The declared service (an add-on's manifest network block) a finding is about: the doctor's addon-<id>,
    or a listener finding on one of its ports. None otherwise."""
    from irate_box.hub import services
    m = re.match(r"^addon-([a-z0-9-]+)$", fid or "")
    if m and source == "doctor":
        return services.by_id(m.group(1))
    m = re.match(r"^(?:page-)?port-(tcp|udp)-(\d+)$", fid or "")
    return services.by_port(m.group(1), int(m.group(2))) if m else None


def do_for(source, fid):
    """Where a finding is put right (DO), or None. A declared service's: its own words, on Add-ons."""
    decl = _declared_about(source, fid)
    if decl and decl["risk"] != "ok":
        return {"go": "addons", "where": "Add-ons",
                "say": f"{decl['says']} If nobody uses it, remove the add-on; if they do, accept it as it is. "
                       "Whether hotspot guests reach it is a switch on the floor (Security)."}
    return next((do for src, pat, do in _DO if src in ("*", source) and pat.search(fid or "")), None)
