# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot, applied: what the root helper writes and runs for a plan from hub/apmode.py: the
interface `ap0`, always, added on the radio (`iw dev <radio> interface add ap0 type __ap`); a
NetworkManager connection on it with the address 192.168.4.1/24 (which the box's certificate
authority permits); and the box's own dnsmasq answering on that address alone, handing out
.10 to .200 and answering every name with 192.168.4.1, so whatever a guest types reaches the hub.

ap* carries the hotspot and nothing else; wlan* carries WiFi links and never the hotspot. A radio
the hotspot has to itself (rung 1), or takes from the link (rung 4), is held while it does: its own
interface out of NetworkManager's hands and down, so nothing joins a network on it meanwhile, and
given back after. A driver that can't add a second interface has that interface renamed ap0 for as
long, and its name given back after. NetworkManager's drop-in (nm_rules) makes it so for every
profile, whoever made it: a client profile with no interface of its own can't be activated on ap*,
nor the hotspot on wlan*.
Forwarding stays off: nothing passes between the guests and the box's other networks (guests'
onward internet is a separate, owner-consented choice, not made here).

Pure functions here (the files and the steps, tested offline); the root helper runs them.
"""

import ipaddress
import os
from pathlib import Path

from irate_box import validate
from irate_box.hub import apmode, hotspot
from irate_box.msg import M, Refused
from irate_box.root import secmanifest

CONNECTION = "irate-box-ap"
SHARED_IFACE = apmode.HOTSPOT_IFACE
KEYFILE = "/etc/NetworkManager/system-connections/irate-box-ap.nmconnection"
DNSMASQ_CONF = "/etc/hub/ap-dnsmasq.conf"
DNSMASQ_UNIT = "irate-box-ap-dns.service"
LEASES = "/var/lib/misc/dnsmasq.leases"  # the hub counts guests from it (server.py LEASES)
NETWORK = ipaddress.ip_network(f"{apmode.ADDRESS}/24", strict=False)
DHCP_FIRST, DHCP_LAST = 10, 200
DEFAULT_SSID = hotspot.DEFAULT_SSID
HOSTAPD_CONF = "/etc/hub/ap-hostapd.conf"
HOSTAPD_UNIT = "irate-box-ap-hostapd.service"
HOSTAPD_BIN = Path(os.environ.get("HUB_HOSTAPD_BIN", "/usr/sbin/hostapd"))
NM_RULES = Path(os.environ.get("HUB_NM_CONF_D", "/etc/NetworkManager/conf.d")) / "zz-80-irate-box-ap.conf"
HOLDS_RADIO = ("own-radio", "choose")  # the plans whose radio is the hotspot's alone while it runs


def nm_rules():
    """NetworkManager's drop-in: ap* activates only the hotspot's connection, wlan* never it. By name: the
    hotspot's keyfile has had that id from the first, so a hotspot set up by an older version stays allowed."""
    return "\n".join(
        [
            "# Written by irate-box (root/ap.py): ap* carries only the hotspot, wlan* never it. Edits are overwritten.",
            "[device-irate-box-hotspot]",
            f"match-device=interface-name:{SHARED_IFACE[:2]}*",
            f"allowed-connections=id:{CONNECTION}",
            "",
            "[device-irate-box-client]",
            "match-device=interface-name:wlan*",
            f"allowed-connections=except:id:{CONNECTION}",
            "",
        ]
    )


def per_account(plan, settings, caps, binary=None):
    """Could this hotspot give each account a WiFi password of its own (hostapd's wpa_psk_file, one keyid per
    account, reported when a device joins)? {"possible": bool, "said": message, "notes": [messages]}. Read
    from what runs it and how it is set up; nothing is changed, and no device has joined that way yet."""
    binary = HOSTAPD_BIN if binary is None else binary
    try:
        keyids = b" keyid=" in Path(binary).read_bytes()
    except OSError:
        keyids = None
    notes = []
    if plan.get("backend") != "hostapd":
        said = M(
            "hotspot-per-account.nm",
            "Not on this hotspot now: NetworkManager runs it. A password for each account needs hostapd to run the "
            "hotspot.",
        )
        if keyids:
            notes.append(M("hotspot-per-account.hostapd-here", "The hostapd on this box has what it needs."))
        return {"possible": False, "said": said, "notes": notes}
    if not keyids:
        return {
            "possible": False,
            "said": M(
                "hotspot-per-account.old",
                "Not on this hotspot: the hostapd on this box cannot tell which password a device joined with.",
            ),
            "notes": notes,
        }
    mode = settings.get("mode")
    if mode == "sae" and settings.get("allow_wpa2"):
        notes.append(
            M("hotspot-per-account.wpa2", "The hotspot accepts WPA2, so every device can join with its own password.")
        )
    elif mode == "sae":
        notes.append(
            M(
                "hotspot-per-account.wpa3",
                "The hotspot is WPA3 only. Each account's password would also be tied to its device's address.",
            )
        )
    elif caps.get("ap_slots", 0) >= 2:
        notes.append(
            M(
                "hotspot-per-account.second",
                "The radio can run a second network: guests keep this one, and accounts join the second with their "
                "own passwords.",
            )
        )
    else:
        return {
            "possible": False,
            "said": M(
                "hotspot-per-account.one-net",
                "Not on this hotspot: it has no password, and the radio runs one network only.",
            ),
            "notes": notes,
        }
    notes.append(M("hotspot-per-account.untested", "No device has joined this radio that way yet. Test it first."))
    return {
        "possible": True,
        "said": M(
            "hotspot-per-account.possible",
            "Possible on this hotspot: hostapd runs it and can tell the passwords apart.",
        ),
        "notes": notes,
    }


def hostapd_conf(plan, settings, ssid=None, country=None, second_ssid=None):
    """hostapd's configuration, for a box whose radio NetworkManager doesn't run (Pi OS Lite and the
    like). One network, as the NetworkManager route; the security lines from hotspot.py."""
    ssid, second_ssid = _names(settings, ssid, second_ssid)
    nets = hotspot.networks(settings, ssid, second_ssid)
    _, net = nets[0]
    lines = [
        "# Written by irate-box (root/ap.py): the hotspot through hostapd. Edits are overwritten.",
        validate.config_line("interface", SHARED_IFACE),
        "driver=nl80211",
        # hostapd_cli's socket, for a channel switch in place (root/ap.py's follow); hostapd creates the folder.
        "ctrl_interface=/run/hostapd",
        validate.config_line("hw_mode", "a" if plan.get("band") == "5 GHz" else "g"),
        validate.config_line("channel", str(int(plan["channel"]))),
        "wmm_enabled=1",
        "ieee80211n=1",
    ]
    if country and len(country) == 2 and country.isalpha():
        lines += [validate.config_line("country_code", country.upper()), "ieee80211d=1"]
    lines += hotspot.hostapd_lines(settings, net["kind"], net["ssid"])
    return "\n".join(lines) + "\n"


# A root unit's sandbox (the security doctor's units-root); each unit adds the paths it writes.
SANDBOX = ("ProtectSystem=strict", "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectHome=yes", "ProtectProc=invisible")


def hostapd_unit():
    return "\n".join(
        [
            "[Unit]",
            "Description=Irate-Box: the hotspot through hostapd (root/ap.py)",
            "After=network.target",
            "",
            "[Service]",
            f"ExecStart=/usr/sbin/hostapd {HOSTAPD_CONF}",
            "Restart=on-failure",
            *secmanifest.unit_lines(
                HOSTAPD_UNIT,
                ("ProtectSystem", "NoNewPrivileges", "PrivateTmp", "ProtectHome", "ProtectProc", "ReadWritePaths"),
                fallback="\n".join((*SANDBOX, "ReadWritePaths=-/run/hostapd")) + "\n",
            ).splitlines(),
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def legacy(plan):
    """A plan recorded by a version before every hotspot ran on ap0 (it names no hotspot_iface): one with the radio
    to itself ran on the radio's own interface, with nothing held or renamed. Still the truth for a hotspot it
    started, until the hotspot is started again."""
    return bool(plan) and "hotspot_iface" not in plan


def ap_iface(plan):
    """The interface the hotspot runs on: ap0, whichever radio it is on; for a legacy plan, where that version put
    it (the radio's own interface when it had the radio to itself)."""
    if legacy(plan):
        return plan["iface"] if plan.get("kind") == "own-radio" or plan.get("drops_uplink") else SHARED_IFACE
    return SHARED_IFACE


def holds_radio(plan):
    """Whether the hotspot has its radio to itself while it runs (rung 1, or rung 4 with the link given up)."""
    return not legacy(plan) and (plan.get("kind") in HOLDS_RADIO or bool(plan.get("drops_uplink")))


def _names(settings, ssid=None, second_ssid=None):
    """The names this hotspot uses, checked here: never a raw string from the hub into hostapd or a
    keyfile. An empty second name is the first with " (encrypted)" after it, trimmed to 32 bytes."""
    given = ssid if ssid is not None else settings.get("ssid")
    ssid = hotspot._ssid_ok(str(given if given is not None else DEFAULT_SSID))
    second = str(second_ssid if second_ssid is not None else settings.get("second_ssid") or "")
    if second:
        second = hotspot._ssid_ok(second)
    if settings.get("mode") == "two" and hotspot._ssid_ok(second or hotspot._with_suffix(ssid)) == ssid:
        raise Refused(hotspot.SAME_SSID)
    return ssid, second


def _keyfile_value(v):
    return validate.keyfile_value(v)


def keyfile(plan, settings, ssid=None, second_ssid=None):
    """The NetworkManager connection, as a keyfile (root's, 600). One network: the "two networks"
    choice needs a second access point interface, which comes later."""
    ssid, second_ssid = _names(settings, ssid, second_ssid)
    nets = hotspot.networks(settings, ssid, second_ssid)
    role, net = nets[0]
    sec = hotspot.nm_properties(settings, net["kind"])
    band = "a" if plan.get("band") == "5 GHz" else "bg"
    lines = [
        "# Written by irate-box (root/ap.py): the hotspot. Edits are overwritten.",
        "[connection]",
        f"id={CONNECTION}",
        "type=wifi",
        f"interface-name={SHARED_IFACE}",
        "autoconnect=true",
        "autoconnect-priority=-10",
        "",
        "[wifi]",
        "mode=ap",
        validate.keyfile_ssid(net["ssid"]),
        f"band={band}",
        f"channel={int(plan['channel'])}",
        "",
    ]
    if sec:
        lines.append("[wifi-security]")
        for k, v in sec.items():
            lines.append(f"{k.split('.', 1)[1]}={_keyfile_value(v)}")
        lines.append("")
    lines += [
        "[ipv4]",
        "method=manual",
        f"address1={apmode.ADDRESS}/{NETWORK.prefixlen}",
        "never-default=true",
        "",
        "[ipv6]",
        "method=disabled",
        "",
    ]
    return "\n".join(lines)


def dnsmasq_conf(plan):
    """The box's dnsmasq for the hotspot alone: DHCP for guests, and every name answered with the hub. On the
    hotspot's address, not its interface: should ap0 ever carry another network's address, nothing there is
    answered (DHCP goes with the address's interface)."""
    first, last = NETWORK.network_address + DHCP_FIRST, NETWORK.network_address + DHCP_LAST
    return "\n".join(
        [
            "# Written by irate-box (root/ap.py): DHCP and DNS on the hotspot only. Edits are overwritten.",
            validate.config_line("listen-address", apmode.ADDRESS),
            "bind-dynamic",
            "except-interface=lo",
            "no-resolv",
            "no-hosts",
            validate.config_line("dhcp-range", f"{first},{last},{NETWORK.netmask},12h"),
            validate.config_line("dhcp-option", f"option:router,{apmode.ADDRESS}"),
            validate.config_line("dhcp-option", f"option:dns-server,{apmode.ADDRESS}"),
            validate.config_line("address", f"/#/{apmode.ADDRESS}"),
            validate.config_line("dhcp-leasefile", LEASES),
            "dhcp-authoritative",
            "",
        ]
    )


def hold_steps(plan):
    """A radio the hotspot has to itself: its own interface out of NetworkManager's hands and down, so nothing
    joins a network on it (and the driver has room for ap0). Nothing for a radio it shares with the link."""
    if not holds_radio(plan):
        return []
    radio = plan["iface"]
    nm = [] if plan.get("backend") == "hostapd" else [["nmcli", "device", "set", radio, "managed", "no"]]
    return [*nm, ["ip", "link", "set", radio, "down"]]


def add_step(plan):
    return ["iw", "dev", plan["iface"], "interface", "add", SHARED_IFACE, "type", "__ap"]


def rename_steps(plan):
    """For a driver that can't add ap0 to a radio the hotspot holds: the radio's own interface named ap0."""
    return [["ip", "link", "set", plan["iface"], "name", SHARED_IFACE]]


def up_steps(plan):
    """The commands that bring the hotspot up, in order (the files written first). Refuses a plan
    that waits on the owner, or has no channel."""
    if plan.get("needs_choice") or plan["kind"] == "none" or not plan.get("channel"):
        raise Refused(M("ap.plan.no-start", "This plan does not start a hotspot. {plan}", plan=plan.get("text", "")))
    return [*hold_steps(plan), add_step(plan), *start_steps(plan)]


def start_steps(plan):
    """The commands after ap0 is there: the access point and its DHCP and DNS."""
    iface = SHARED_IFACE
    steps = []
    if plan.get("backend") == "hostapd":
        # No NetworkManager: the address set by hand, hostapd runs the access point.
        if plan.get("drops_uplink") and plan.get("uplink"):
            raise Refused(
                M(
                    "ap.plan.needs-nm",
                    "The hotspot can take the WiFi link only if NetworkManager runs the link on this box.",
                )
            )
        steps += [
            ["ip", "addr", "replace", f"{apmode.ADDRESS}/{NETWORK.prefixlen}", "dev", iface],
            ["ip", "link", "set", iface, "up"],
            ["systemctl", "restart", HOSTAPD_UNIT],
            ["systemctl", "restart", DNSMASQ_UNIT],
        ]
        return steps
    steps.append(["nmcli", "device", "set", SHARED_IFACE, "managed", "yes"])
    steps += [
        ["nmcli", "connection", "reload"],
        ["nmcli", "connection", "up", CONNECTION],
        ["systemctl", "restart", DNSMASQ_UNIT],
    ]
    return steps


def down_steps(plan):
    """The commands that take it down again, leaving the box's own links as they were: ap0 removed (or, renamed
    from the radio's own interface, given its name back), a radio it held given back. A legacy plan comes down as
    that version brought it up: its address off the interface it was on, ap0 removed only where it made one."""
    hostapd = plan.get("backend") == "hostapd"
    if legacy(plan):
        iface = ap_iface(plan)
        if hostapd:
            steps = [
                ["systemctl", "stop", DNSMASQ_UNIT],
                ["systemctl", "stop", HOSTAPD_UNIT],
                ["ip", "addr", "del", f"{apmode.ADDRESS}/{NETWORK.prefixlen}", "dev", iface],
            ]
        else:
            steps = [["systemctl", "stop", DNSMASQ_UNIT], ["nmcli", "connection", "down", CONNECTION]]
        if iface == SHARED_IFACE:
            steps.append(["iw", "dev", SHARED_IFACE, "del"])
        if plan.get("drops_uplink") and plan.get("uplink"):
            steps.append(["nmcli", "device", "connect", plan["uplink"]])
        return steps
    if hostapd:
        steps = [
            ["systemctl", "stop", DNSMASQ_UNIT],
            ["systemctl", "stop", HOSTAPD_UNIT],
            ["ip", "addr", "flush", "dev", SHARED_IFACE],
        ]
    else:
        steps = [["systemctl", "stop", DNSMASQ_UNIT], ["nmcli", "connection", "down", CONNECTION]]
    radio = plan.get("renamed_from")
    if radio:
        steps += [["ip", "link", "set", SHARED_IFACE, "down"], ["ip", "link", "set", SHARED_IFACE, "name", radio]]
    elif plan.get("iface") != SHARED_IFACE:  # ap0 the radio's own interface: never deleted
        steps.append(["iw", "dev", SHARED_IFACE, "del"])
    if holds_radio(plan):
        radio = plan["iface"]
        steps.append(
            ["ip", "link", "set", radio, "up"] if hostapd else ["nmcli", "device", "set", radio, "managed", "yes"]
        )
        if plan.get("drops_uplink") and plan.get("uplink") and not hostapd:
            steps.append(["nmcli", "device", "connect", plan["uplink"]])
    return steps


def dnsmasq_unit():
    """The dnsmasq unit for the hotspot (dnsmasq-base's binary; NetworkManager's shared mode isn't
    used, so the DNS answers are the hub's)."""
    return "\n".join(
        [
            "[Unit]",
            "Description=Irate-Box: DHCP and DNS on the hotspot (root/ap.py)",
            "After=NetworkManager.service",
            "",
            "[Service]",
            f"ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file={DNSMASQ_CONF} --pid-file=",
            "Restart=on-failure",
            *secmanifest.unit_lines(
                DNSMASQ_UNIT,
                ("ProtectSystem", "NoNewPrivileges", "PrivateTmp", "ProtectHome", "ProtectProc", "ReadWritePaths"),
                fallback="\n".join((*SANDBOX, "ReadWritePaths=-/var/lib/misc")) + "\n",
            ).splitlines(),
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


# --- running it (the root helper) ------------------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "ap.json"  # what is up, from which plan, since when, and the owner's choices
TRIED = ETC / "ap-tried.json"  # {phy: True|False}: whether a channel of its own beside the link worked
DISPATCHER = Path(os.environ.get("HUB_NM_DISPATCHER", "/etc/NetworkManager/dispatcher.d")) / "90-irate-box-ap"
UNITS = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
LINKS = Path(os.environ.get("HUB_LINK_DIR", "/etc/systemd/network"))
LINK_FILE = "10-irate-box-ap.link"
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
TRY_WAIT = 20  # seconds the hotspot must hold its own channel in the try
DEADMAN = 300  # seconds before a hotspot that took the box's link undoes itself
MOVE_INTERVAL = 300  # seconds between two moves of a following hotspot, unless the owner sets it lower


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _put(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


BOOT_UNIT = "irate-box-ap.service"


def link_file():
    """udev keeps ap0's name. Without it a USB radio's second interface is renamed by its address
    (wlx…) as soon as it appears, and the hotspot's profile, made for ap0, finds no device."""
    return "\n".join(
        [
            "# Written by irate-box (root/ap.py): the hotspot's interface keeps the name it was given.",
            "[Match]",
            f"OriginalName={SHARED_IFACE}",
            "",
            "[Link]",
            "NamePolicy=",
            f"Name={SHARED_IFACE}",
            "",
        ]
    )


def boot_unit():
    """At boot, the hotspot as it was: ap0 is gone after a reboot, so it is planned and started again
    (the box's links may have changed meanwhile). Enabled while the hotspot is on, disabled when off."""
    return "\n".join(
        [
            "[Unit]",
            "Description=Irate-Box: the hotspot, again after a reboot (root/ap.py)",
            "After=NetworkManager.service",
            "Wants=NetworkManager.service",
            "",
            "[Service]",
            "Type=oneshot",
            f"ExecStart={CODE}/irate-box hub_control ap-boot",
            # It starts the hotspot as the root helper does: its record and the hub's, NetworkManager's connections,
            # the guests' rules, their resolver's unit and forwarding's sysctl file.
            *secmanifest.unit_lines(
                BOOT_UNIT,
                ("ProtectSystem", "NoNewPrivileges", "PrivateTmp", "ProtectHome", "ProtectProc", "ReadWritePaths"),
                fallback="\n".join(
                    (
                        *SANDBOX,
                        "ReadWritePaths=/etc/hub /var/lib/hub -/etc/NetworkManager /etc/systemd/system /etc/sysctl.d",
                    )
                )
                + "\n",
            ).splitlines(),
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def refresh_units():
    """The hotspot's units brought up to this version's text, for each one already written here (an
    update changes them; only a hotspot set up again would otherwise). Their names, for a restart.
    Also where an update's install reads the two old capability stores into the record (radiocaps.py)."""
    from irate_box.hub import netinv
    from irate_box.root import radiocaps

    if not radiocaps.STORE.exists():
        try:
            radiocaps.migrate(netinv.scan())
        except (OSError, ValueError):
            pass
    changed = []
    for name, text in ((DNSMASQ_UNIT, dnsmasq_unit()), (HOSTAPD_UNIT, hostapd_unit()), (BOOT_UNIT, boot_unit())):
        path = UNITS / name
        try:
            if path.read_text() == text:
                continue
        except FileNotFoundError:
            continue
        _put(path, text)
        changed.append(name)
    rec = _load(RECORD, {})
    conf = Path(DNSMASQ_CONF)
    if rec.get("up") and rec.get("plan") and conf.exists() and conf.read_text() != dnsmasq_conf(rec["plan"]):
        # The running hotspot's DHCP and DNS in this version's form: restarted by the install, as its unit would be.
        _put(conf, dnsmasq_conf(rec["plan"]))
        if DNSMASQ_UNIT not in changed:
            changed.append(DNSMASQ_UNIT)
    return changed


def refresh_nm_rules(only_if_set_up=False):
    """NetworkManager's drop-in (nm_rules) in this version's form; with only_if_set_up, only where a hotspot was
    set up through NetworkManager (its keyfile is there). Whether it changed: NetworkManager then reads its
    configuration again (`nmcli general reload conf`), which applies it to every device at once."""
    if only_if_set_up and not Path(KEYFILE).exists():
        return False
    try:
        if NM_RULES.read_text() == nm_rules():
            return False
    except OSError:
        pass
    _put(NM_RULES, nm_rules())
    return True


def boot(run, inv, settings, ssid=None):
    """The boot unit's call. Off: nothing. On and confirmed (or never needing it): planned and started
    again. A hotspot that took the box's own link and was never confirmed: the link comes back instead."""
    rec = _load(RECORD, {})
    if not rec.get("up"):
        return "The hotspot is off."
    if not rec.get("confirmed", True):
        return (
            stop(run) + ". The hotspot took the box's WiFi link and nobody confirmed it, so the box restored the link."
        )
    from irate_box.root import radio

    # Not during a WiFi link's join: a hotspot started then can stop an AIC8800's firmware (radio.py).
    waited = radio.settle_before_hotspot()
    after = f" (after {waited} s for the WiFi link to finish joining)" if waited else ""
    plan = start(run, inv, settings, rec.get("owner") or {}, ssid)
    if plan.get("needs_choice") or plan["kind"] == "none":
        return "The hotspot did not start again. " + plan["text"]
    return f"The hotspot started again on {ap_iface(plan)}, channel {plan['channel']}{after}."


def dispatcher_hook():
    """NetworkManager runs this on every link change: when the hotspot follows the link's channel
    (rung 3), a link that came up somewhere new moves it."""
    return "\n".join(
        [
            "#!/bin/sh",
            "# Written by irate-box (root/ap.py): the hotspot follows the WiFi link's channel when it must.",
            '[ "$2" = up ] || exit 0',
            f'exec "{CODE}/irate-box" hub_control ap-follow "$1" >/dev/null 2>&1',
            "",
        ]
    )


def _run_all(run, steps, check=True):
    for s in steps:
        out = run(*s)
        if check and out.returncode != 0:
            raise RuntimeError(f"{' '.join(s)}: {(out.stderr or out.stdout or '').strip()[-200:]}")


def _hostapd_present(inv):
    """hostapd on the box: the binary, or the inventory's word for it (a test's stand-in)."""
    return Path(HOSTAPD_BIN).is_file() or bool(((inv.get("stacks") or {}).get("hostapd") or {}).get("installed"))


def _install_hostapd(run, log=print):
    """hostapd for the hotspot, from the Network toolkit's cache when it has it, else from Debian. It is
    needed where NetworkManager does not run the radio, only when the hotspot is switched on."""
    from irate_box.root import kits

    try:
        kits.install_from_cache(["hostapd"], run=run)
        log("installed hostapd from the toolkits' cache")
        return
    except Refused as exc:
        # Only a cache without it falls back to Debian; another install's policy-rc.d, say, is not ours to overrule.
        if (getattr(exc, "message", None) or {}).get("id") not in (
            "kits.install-packages.not-cached",
            "kits.install-packages.no-index",
        ):
            raise
    out = run("apt-get", "install", "-y", "-q", "--no-install-recommends", "hostapd")
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip().splitlines()
        raise Refused(
            M(
                "ap.hostapd.install-failed",
                "The hotspot needs hostapd, and the box could not install it. {detail}",
                detail=detail[-1][:200] if detail else "apt-get install hostapd failed",
            )
        )
    log("installed hostapd from Debian")


def start(run, inv, settings, owner=None, ssid=None, keyfile_path=None, conf_path=None):
    """Plan for this box and bring the hotspot up. Returns the plan; a plan that waits on the owner, or
    none, is returned without starting anything. On a failed step, what was done is undone."""
    owner = owner or {}
    ssid, second_ssid = _names(settings, ssid)
    from irate_box.root import radiocaps

    plan = apmode.plan(inv, inv.get("ap") or [], owner, radiocaps.stored_by_phy(inv))
    if plan.get("needs_choice") or plan["kind"] == "none":
        return plan
    if plan.get("backend") == "hostapd" and not _hostapd_present(inv):
        _install_hostapd(run)
    old = _load(RECORD, {})
    if old.get("up"):
        _run_all(run, down_steps(old["plan"]), check=False)
    was = old.get("plan") or old.get("last_plan") or {}
    if plan.get("iface") == SHARED_IFACE and was.get("renamed_from"):
        # Planned from a look taken while the radio's own interface was named ap0: its own name again now (or still
        # ap0, should the name not have come back: then the plan names it and nothing deletes it).
        own = was["renamed_from"]
        plan = dict(plan, iface=own if run("ip", "link", "show", own).returncode == 0 else SHARED_IFACE)
    if plan.get("iface") == SHARED_IFACE and not was.get("renamed_from"):
        raise Refused(
            M(
                "ap.radio-named-ap0",
                "The radio's own interface is named ap0, and the box has no record of its name before. "
                "Restart the box, then enable the hotspot again.",
            )
        )
    if plan.get("backend") == "hostapd":
        # hostapd gets a control socket, so the watchdog's follower can move a following hotspot by channel
        # switch (follow() below) as it does on NetworkManager's boxes; the dispatcher hook is NetworkManager's.
        _put(Path(HOSTAPD_CONF), hostapd_conf(plan, settings, ssid, inv.get("country"), second_ssid), 0o600)
        units = {DNSMASQ_UNIT: dnsmasq_unit(), HOSTAPD_UNIT: hostapd_unit()}
    else:
        _put(Path(keyfile_path or KEYFILE), keyfile(plan, settings, ssid, second_ssid), 0o600)
        units = {DNSMASQ_UNIT: dnsmasq_unit()}
    _put(Path(conf_path or DNSMASQ_CONF), dnsmasq_conf(plan))
    for name, text in units.items():
        unit = UNITS / name
        if not unit.exists() or unit.read_text() != text:
            _put(unit, text)
            run("systemctl", "daemon-reload")
    _put(DISPATCHER, dispatcher_hook(), 0o755)
    link = LINKS / LINK_FILE
    if not link.exists() or link.read_text() != link_file():
        _put(link, link_file())
        run("udevadm", "control", "--reload")
    if NM_RULES.parent.parent.exists() and refresh_nm_rules():
        # Wherever NetworkManager is installed, even on the hostapd route: it must not take ap0 for a client.
        run("nmcli", "general", "reload", "conf")
    boot = UNITS / BOOT_UNIT
    if not boot.exists() or boot.read_text() != boot_unit():
        _put(boot, boot_unit())
        run("systemctl", "daemon-reload")
    up_steps(plan)  # a plan that can't start is refused here, before anything runs
    rest = start_steps(plan)
    try:
        _run_all(run, hold_steps(plan))
        added = run(*add_step(plan))
        if added.returncode != 0:
            if not holds_radio(plan):
                raise RuntimeError(f"{' '.join(add_step(plan))}: {(added.stderr or added.stdout or '').strip()[-200:]}")
            # The driver offers no second interface: the radio's own takes the name while the hotspot holds it.
            _run_all(run, rename_steps(plan))
            plan = dict(plan, renamed_from=plan["iface"])
        if plan.get("backend") == "hostapd":
            # hostapd runs ap0, NetworkManager (where it runs at all) keeps off it.
            run("nmcli", "device", "set", SHARED_IFACE, "managed", "no")
        _run_all(run, rest)
    except RuntimeError:
        _run_all(run, down_steps(plan), check=False)
        raise
    if plan.get("drops_uplink"):
        # The owner gave up the box's link: unless confirmed from the hotspot in time, it comes back.
        run(
            "systemd-run",
            f"--on-active={DEADMAN}",
            "--unit=irate-box-ap-deadman",
            "--timer-property=AccuracySec=1s",
            f"{CODE}/irate-box",
            "hub_control",
            "ap-revert",
        )
    run("systemctl", "enable", BOOT_UNIT)
    _put(
        RECORD,
        json.dumps(
            {"up": True, "plan": plan, "owner": owner, "since": time.time(), "confirmed": not plan.get("drops_uplink")}
        ),
    )
    return plan


def stop(run):
    """Off. The plan is kept as last_plan: should a renamed radio not get its name back, a later start or remove()
    still knows ap0 is the radio's own interface, and names it back rather than deleting it."""
    rec = _load(RECORD, {})
    plan = rec.get("plan") or rec.get("last_plan") or {}
    if rec.get("up"):
        _run_all(run, down_steps(rec["plan"]), check=False)
    run("systemctl", "stop", "irate-box-ap-deadman.timer")
    run("systemctl", "disable", BOOT_UNIT)
    DISPATCHER.unlink(missing_ok=True)
    _put(RECORD, json.dumps({"up": False, "owner": rec.get("owner") or {}, "since": time.time(), "last_plan": plan}))
    own = plan.get("renamed_from")
    if own and run("ip", "link", "show", SHARED_IFACE).returncode == 0 and run("ip", "link", "show", own).returncode:
        return (
            f"The hotspot is off, but the radio's interface is still named {SHARED_IFACE}, not {own}. Restart the box."
        )
    return "The hotspot is off."


DEADMAN_UNITS = ("irate-box-ap-deadman.timer", "irate-box-ap-deadman.service")


def remove(run):
    """Everything of irate-box's hotspot off and gone, whatever its record says: for the uninstaller, run
    while this code is still installed and before the hotspot's firewall goes. stop() undoes only what the record
    says is up and keeps the files; this also covers a stale or partial install. Only irate-box's own: its units, the
    connection irate-box-ap, ap0 (the interface it adds), its files; never another hotspot or a joined network.
    Returns (off, problems): off only when nothing of it is still running."""
    for unit in (*DEADMAN_UNITS, BOOT_UNIT, DNSMASQ_UNIT, HOSTAPD_UNIT):
        run("systemctl", "disable", "--now", unit)
    run("nmcli", "connection", "down", CONNECTION)
    run("nmcli", "connection", "delete", CONNECTION)
    rec = _load(RECORD, {})
    plan = rec.get("plan") or rec.get("last_plan") or {}
    if legacy(plan) and plan.get("backend") == "hostapd" and ap_iface(plan) != SHARED_IFACE:
        run("ip", "addr", "del", f"{apmode.ADDRESS}/{NETWORK.prefixlen}", "dev", ap_iface(plan))
    if run("ip", "link", "show", SHARED_IFACE).returncode == 0:
        if plan.get("renamed_from"):  # the radio's own interface: its name back, never deleted
            run("ip", "link", "set", SHARED_IFACE, "down")
            run("ip", "link", "set", SHARED_IFACE, "name", plan["renamed_from"])
        else:
            run("iw", "dev", SHARED_IFACE, "del")
    if holds_radio(plan) and plan.get("iface"):
        run("nmcli", "device", "set", plan["iface"], "managed", "yes")
    for f in (
        Path(KEYFILE),
        Path(DNSMASQ_CONF),
        Path(HOSTAPD_CONF),
        UNITS / BOOT_UNIT,
        UNITS / DNSMASQ_UNIT,
        UNITS / HOSTAPD_UNIT,
        DISPATCHER,
        LINKS / LINK_FILE,
        NM_RULES,
        RECORD,
        TRIED,
    ):
        f.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    run("udevadm", "control", "--reload")
    run("nmcli", "general", "reload", "conf")
    run("nmcli", "connection", "reload")
    problems = [
        f"{unit} is {state}"
        for unit in (BOOT_UNIT, DNSMASQ_UNIT, HOSTAPD_UNIT)
        if (state := run("systemctl", "is-active", unit).stdout.strip()) in ("active", "activating", "reloading")
    ]
    active = run("nmcli", "-t", "-f", "NAME", "connection", "show", "--active")
    if active.returncode == 0 and CONNECTION in active.stdout.split():
        problems.append(f"the connection {CONNECTION} is still active")
    if run("ip", "link", "show", SHARED_IFACE).returncode == 0:
        problems.append(f"the interface {SHARED_IFACE} is still there")
    return not problems, problems


def confirm(run):
    """The owner reached the box through the hotspot after giving up its link: keep it so."""
    run("systemctl", "stop", "irate-box-ap-deadman.timer")
    rec = _load(RECORD, {})
    rec["confirmed"] = True
    _put(RECORD, json.dumps(rec))
    return "The hotspot stays on and the box's WiFi link stays off."


def channels_now(run):
    """{iface: channel} from `iw dev`."""
    from irate_box.hub import netinv

    out = run("iw", "dev")
    return {i: d.get("channel") for i, d in netinv.parse_iw_dev(out.stdout or "").items()}


def try_own_channel(run, inv, settings, ssid=None, wait=TRY_WAIT, sleep=time.sleep):
    """Rung 2 or 3, tried on this box: the hotspot up on a channel other than the link's, held for a
    while with the link still up there? Recorded per radio; the hotspot left running as the plan
    then says. Returns (worked, plan)."""
    from irate_box.root import radiocaps

    plan = apmode.plan(inv, inv.get("ap") or [], {}, radiocaps.stored_by_phy(inv))
    if plan["kind"] != "own-channel":
        return None, plan
    link = plan.get("uplink")
    link_ch = channels_now(run).get(link)
    chans = apmode.allowed(next(r for r in inv["radios"] if r["phy"] == plan["phy"]), inv.get("country"))
    other = next(
        (c["channel"] for c in chans if c["channel"] in apmode.PREFERRED_24 and c["channel"] != link_ch), None
    ) or next((c["channel"] for c in chans if c["channel"] != link_ch), None)
    if other is None:
        return None, plan
    owner = _load(RECORD, {}).get("owner") or {}  # the owner's own choices, not the try's channel
    try:
        start(run, inv, settings, {"channel": other}, ssid)
    except RuntimeError:
        worked = False  # it wouldn't even start on a channel of its own: that's the answer
    else:
        sleep(wait)
        now = channels_now(run)
        worked = now.get(ap_iface(plan)) == other and now.get(link) == link_ch and link_ch is not None
    from irate_box.root import radiocaps

    radio = next((r for r in inv.get("radios") or [] if r.get("phy") == plan["phy"]), {})
    radiocaps.record_test(
        radio,
        "own-channel",
        worked,
        "The hotspot held a channel of its own beside the link."
        if worked
        else "The hotspot did not hold a channel of its own beside the link.",
    )
    return worked, start(run, inv, settings, owner, ssid)


def move_interval():
    """Seconds between two moves of a following hotspot: the owner's setting in /etc/hub/uplink.json, else 300."""
    try:
        from irate_box.hub import uplink

        return max(30, int(uplink.load_settings().get("hotspot_move", MOVE_INTERVAL)))
    except (OSError, ValueError, TypeError):
        return MOVE_INTERVAL


def channel_switch(run, plan, new, wait=3, sleep=time.sleep):
    """Switch the running hotspot to `new`'s channel in place, and check it moved. Returns (ok, why)."""
    iface = ap_iface(plan)
    cli = "hostapd_cli" if plan.get("backend") == "hostapd" else "wpa_cli"
    try:
        out = run(cli, "-i", iface, "chan_switch", "5", str(int(new["freq"])), "ht")
    except OSError as exc:
        return False, str(exc)[-160:]
    if out.returncode != 0:
        why = (out.stderr or out.stdout or "").strip()
        return False, why[-160:] if why else f"{cli} refused the channel switch"
    deadline = time.time() + wait
    while time.time() < deadline:
        if channels_now(run).get(iface) == new["channel"]:
            if plan.get("backend") != "hostapd":
                try:
                    status = run("wpa_cli", "-i", iface, "status")
                except OSError as exc:
                    return False, str(exc)[-160:]
                said = (status.stdout or "") if status.returncode == 0 else ""
                if "wpa_state=COMPLETED" not in said or "mode=AP" not in said:
                    return False, "The hotspot did not stay up after the channel switch"
            return True, ""
        sleep(1)
    return False, "The hotspot did not move to the new channel"


def _retune_files(plan, settings, ssid, second_ssid, country):
    if plan.get("backend") == "hostapd":
        _put(Path(HOSTAPD_CONF), hostapd_conf(plan, settings, ssid, country, second_ssid), 0o600)
    else:
        _put(Path(KEYFILE), keyfile(plan, settings, ssid, second_ssid), 0o600)


def _mark_moved(plan, method):
    rec = _load(RECORD, {})
    if not rec.get("up"):
        return
    rec["plan"] = dict(rec.get("plan") or {}, channel=plan["channel"], freq=plan.get("freq"), band=plan.get("band"))
    rec["moved_at"] = time.time()
    rec["moved_by"] = method
    _put(RECORD, json.dumps(rec))


def follow(run, inv, settings, iface, ssid=None):
    """A following hotspot moves to the link's channel: in place by channel switch where the radio has it,
    else by starting the hotspot again. Both triggers (NetworkManager's dispatcher and the watchdog's
    follower) come here, so the move interval is obeyed by both. (moved, note): whether it moved, and what
    happened in words."""
    rec = _load(RECORD, {})
    if not rec.get("up") or not (rec.get("plan") or {}).get("follows_uplink") or rec["plan"].get("uplink") != iface:
        return False, "The hotspot has no link to follow."
    from irate_box.root import radiocaps

    plan = apmode.plan(inv, inv.get("ap") or [], rec.get("owner") or {}, radiocaps.stored_by_phy(inv))
    if plan.get("channel") == rec["plan"].get("channel"):
        return False, f"The hotspot is still on channel {plan.get('channel')}."
    now, hold = time.time(), move_interval()
    moved_at = rec.get("moved_at") or 0
    if moved_at and now - moved_at < hold:
        return False, f"The hotspot moved {round(now - moved_at)} s ago; it moves again after {hold} s."
    radio = next((r for r in inv.get("radios") or [] if r.get("iface") == iface), {})
    csa = (radiocaps.rows(radio, radiocaps.stored(radio)).get("csa") or {}).get("value")
    if csa == "yes":
        ssid, second_ssid = _names(settings, ssid)
        ok, why = channel_switch(run, rec["plan"], plan)
        if ok:
            _retune_files(plan, settings, ssid, second_ssid, inv.get("country"))
            _mark_moved(plan, "switch")
            return True, f"The hotspot moved to channel {plan['channel']} with the link (channel switch)."
        radiocaps.record_learned(radio, "csa", "no", f"The channel switch failed: {why}.")
        fallback = " (restarted: channel switch failed)"
    else:
        fallback = ""
    start(run, inv, settings, rec.get("owner") or {}, ssid)
    _mark_moved(plan, "restart")
    return True, f"The hotspot moved to channel {plan['channel']} with the link{fallback}."
