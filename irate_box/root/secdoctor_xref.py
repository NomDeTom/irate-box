# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Which checks of each source ask the same question (security-doctor-plan §5.2): the doctor's own
findings, the Security page's scan, debian-cis's checks and Lynis's tests. A finding listed here is
"about" the shared key, so the joint report merges what several sources say about one setting into
one item. Unlisted checks stand alone; the table grows as they are met. tests/sim_secdoctor_joint.py
checks every id here still exists where it can (the doctor's and the Security page's)."""
import re

# key: {title, ref (the security review's finding), and each source's ids}
XREF = {
    "kernel-links": {"title": "Kernel link protections (protected_symlinks, protected_hardlinks)", "ref": "F3",
                     "doctor": ["kernel-links"], "security-page": ["kernel-links"]},
    "kernel-info": {"title": "Kernel addresses and log hidden from ordinary accounts", "ref": "F9",
                    "doctor": ["kernel-info"], "security-page": ["kernel-info"]},
    "kernel-ptrace": {"title": "ptrace between processes (Yama)", "ref": "F9", "doctor": ["kernel-ptrace"]},
    "core-dumps": {"title": "Core dumps restricted", "debian-cis": ["1.6.4_restrict_core_dumps"], "lynis": ["KRNL-5820"]},
    "aslr": {"title": "Address space randomisation", "debian-cis": ["1.6.2_enable_randomized_vm_placement"]},
    "ssh-root-login": {"title": "SSH: logging in as root", "security-page": ["ssh-root"], "debian-cis": ["5.2.10_disable_root_login"]},
    "ssh-password": {"title": "SSH: passwords rather than keys", "security-page": ["ssh-password"],
                     "debian-cis": ["99.5.2.1_ssh_auth_pubk_only"]},
    "firewall": {"title": "Firewall rules", "debian-cis": ["3.5.4.1.1_net_fw_default_policy_drop"], "lynis": ["FIRE-4512"]},
    "security-updates": {"title": "Security updates installed", "security-page": ["security-updates"], "debian-cis": ["1.9_install_updates"]},
    "sudo-all": {"title": "sudo rules that allow everything", "doctor": ["acct-sudo"], "debian-cis": ["99.1.3_acc_sudoers_no_all"]},
}
_BY = {(src, i): key for key, e in XREF.items() for src, ids in e.items() if isinstance(ids, list) for i in ids}
PORT_RE = re.compile(r"^port-(tcp|udp)-(\d+)$")


def about(source, check):
    """The shared {kind, key} for a source's check id, or None."""
    key = _BY.get((source, check))
    if key:
        return {"kind": "setting", "key": key}
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
