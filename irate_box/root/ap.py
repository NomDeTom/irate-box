# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot, applied: what the root helper writes and runs for a plan from hub/apmode.py: a
second interface `ap0` when the hotspot shares the radio with the box's WiFi link
(`iw dev <link> interface add ap0 type __ap`), the radio's own interface when it has one to itself;
a NetworkManager connection on it with the address 192.168.4.1/24 (which the box's certificate
authority permits); and the box's own dnsmasq bound to that interface alone, handing out
.10 to .200 and answering every name with 192.168.4.1, so whatever a guest types reaches the hub.
Forwarding stays off: nothing passes between the guests and the box's other networks (guests'
onward internet is a separate, owner-consented choice, not made here).

Pure functions here (the files and the steps, tested offline); the root helper runs them.
"""

import ipaddress

from irate_box.hub import apmode, hotspot
from irate_box.msg import M, Refused

CONNECTION = "irate-box-ap"
SHARED_IFACE = "ap0"
KEYFILE = "/etc/NetworkManager/system-connections/irate-box-ap.nmconnection"
DNSMASQ_CONF = "/etc/hub/ap-dnsmasq.conf"
DNSMASQ_UNIT = "irate-box-ap-dns.service"
LEASES = "/var/lib/misc/dnsmasq.leases"  # the hub counts guests from it (server.py LEASES)
NETWORK = ipaddress.ip_network(f"{apmode.ADDRESS}/24", strict=False)
DHCP_FIRST, DHCP_LAST = 10, 200
DEFAULT_SSID = "Irate-Box"
HOSTAPD_CONF = "/etc/hub/ap-hostapd.conf"
HOSTAPD_UNIT = "irate-box-ap-hostapd.service"


def hostapd_conf(plan, settings, ssid=DEFAULT_SSID, country=None):
    """hostapd's configuration, for a box whose radio NetworkManager doesn't run (Pi OS Lite and the
    like). One network, as the NetworkManager route; the security lines from hotspot.py."""
    nets = hotspot.networks(settings, ssid)
    _, net = nets[0]
    lines = [
        "# Written by irate-box (root/ap.py): the hotspot through hostapd. Edits are overwritten.",
        f"interface={ap_iface(plan)}",
        "driver=nl80211",
        f"hw_mode={'a' if plan.get('band') == '5 GHz' else 'g'}",
        f"channel={int(plan['channel'])}",
        "wmm_enabled=1",
        "ieee80211n=1",
    ]
    if country and len(country) == 2 and country.isalpha():
        lines += [f"country_code={country.upper()}", "ieee80211d=1"]
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
            *SANDBOX,
            "ReadWritePaths=-/run/hostapd",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def ap_iface(plan):
    """The interface the hotspot runs on: its own when the radio is its alone, else ap0 beside the link."""
    return plan["iface"] if plan["kind"] == "own-radio" or plan.get("drops_uplink") else SHARED_IFACE


def _keyfile_value(v):
    return str(v).replace("\n", "")


def keyfile(plan, settings, ssid=DEFAULT_SSID):
    """The NetworkManager connection, as a keyfile (root's, 600). One network: the "two networks"
    choice needs a second access point interface, which comes later."""
    nets = hotspot.networks(settings, ssid)
    role, net = nets[0]
    sec = hotspot.nm_properties(settings, net["kind"])
    band = "a" if plan.get("band") == "5 GHz" else "bg"
    lines = [
        "# Written by irate-box (root/ap.py): the hotspot. Edits are overwritten.",
        "[connection]",
        f"id={CONNECTION}",
        "type=wifi",
        f"interface-name={ap_iface(plan)}",
        "autoconnect=true",
        "autoconnect-priority=-10",
        "",
        "[wifi]",
        "mode=ap",
        f"ssid={_keyfile_value(net['ssid'])}",
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
    """The box's dnsmasq for the hotspot alone: DHCP for guests, and every name answered with the hub."""
    first, last = NETWORK.network_address + DHCP_FIRST, NETWORK.network_address + DHCP_LAST
    return "\n".join(
        [
            "# Written by irate-box (root/ap.py): DHCP and DNS on the hotspot only. Edits are overwritten.",
            f"interface={ap_iface(plan)}",
            "bind-dynamic",
            "except-interface=lo",
            "no-resolv",
            "no-hosts",
            f"dhcp-range={first},{last},{NETWORK.netmask},12h",
            f"dhcp-option=option:router,{apmode.ADDRESS}",
            f"dhcp-option=option:dns-server,{apmode.ADDRESS}",
            f"address=/#/{apmode.ADDRESS}",
            f"dhcp-leasefile={LEASES}",
            "dhcp-authoritative",
            "",
        ]
    )


def up_steps(plan):
    """The commands that bring the hotspot up, in order (the files written first). Refuses a plan
    that waits on the owner, or has no channel."""
    if plan.get("needs_choice") or plan["kind"] == "none" or not plan.get("channel"):
        raise Refused(M("ap.plan.no-start", "This plan does not start a hotspot. {plan}", plan=plan.get("text", "")))
    steps, iface = [], ap_iface(plan)
    if iface == SHARED_IFACE:
        steps.append(["iw", "dev", plan["iface"], "interface", "add", SHARED_IFACE, "type", "__ap"])
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
    if iface == SHARED_IFACE:
        steps.append(["nmcli", "device", "set", SHARED_IFACE, "managed", "yes"])
    if plan.get("drops_uplink") and plan.get("uplink"):
        steps.append(["nmcli", "device", "disconnect", plan["uplink"]])
    steps += [
        ["nmcli", "connection", "reload"],
        ["nmcli", "connection", "up", CONNECTION],
        ["systemctl", "restart", DNSMASQ_UNIT],
    ]
    return steps


def down_steps(plan):
    """The commands that take it down again, leaving the box's own links as they were."""
    if plan.get("backend") == "hostapd":
        steps = [
            ["systemctl", "stop", DNSMASQ_UNIT],
            ["systemctl", "stop", HOSTAPD_UNIT],
            ["ip", "addr", "flush", "dev", ap_iface(plan)],
        ]
    else:
        steps = [["systemctl", "stop", DNSMASQ_UNIT], ["nmcli", "connection", "down", CONNECTION]]
    if ap_iface(plan) == SHARED_IFACE:
        steps.append(["iw", "dev", SHARED_IFACE, "del"])
    if plan.get("drops_uplink") and plan.get("uplink"):
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
            *SANDBOX,
            "ReadWritePaths=-/var/lib/misc",
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
            *SANDBOX,
            # It starts the hotspot as the root helper does: its record and the hub's, NetworkManager's connections,
            # the guests' rules, their resolver's unit and forwarding's sysctl file.
            "ReadWritePaths=/etc/hub /var/lib/hub -/etc/NetworkManager /etc/systemd/system /etc/sysctl.d",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def refresh_units():
    """The hotspot's units brought up to this version's text, for each one already written here (an
    update changes them; only a hotspot set up again would otherwise). Their names, for a restart."""
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
    return changed


def boot(run, inv, settings, ssid=DEFAULT_SSID):
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


def start(run, inv, settings, owner=None, ssid=DEFAULT_SSID, keyfile_path=None, conf_path=None):
    """Plan for this box and bring the hotspot up. Returns the plan; a plan that waits on the owner, or
    none, is returned without starting anything. On a failed step, what was done is undone."""
    owner = owner or {}
    plan = apmode.plan(inv, inv.get("ap") or [], owner, _load(TRIED, {}))
    if plan.get("needs_choice") or plan["kind"] == "none":
        return plan
    old = _load(RECORD, {})
    if old.get("up"):
        _run_all(run, down_steps(old["plan"]), check=False)
    if plan.get("backend") == "hostapd":
        # Following the link's channel needs NetworkManager's hook: here a following hotspot takes the
        # link's channel when it starts (and after a reboot), not when the link roams.
        _put(Path(HOSTAPD_CONF), hostapd_conf(plan, settings, ssid, inv.get("country")), 0o600)
        units = {DNSMASQ_UNIT: dnsmasq_unit(), HOSTAPD_UNIT: hostapd_unit()}
    else:
        _put(Path(keyfile_path or KEYFILE), keyfile(plan, settings, ssid), 0o600)
        units = {DNSMASQ_UNIT: dnsmasq_unit()}
    _put(Path(conf_path or DNSMASQ_CONF), dnsmasq_conf(plan))
    for name, text in units.items():
        unit = UNITS / name
        if not unit.exists() or unit.read_text() != text:
            _put(unit, text)
            run("systemctl", "daemon-reload")
    _put(DISPATCHER, dispatcher_hook(), 0o755)
    if ap_iface(plan) == SHARED_IFACE:
        link = LINKS / LINK_FILE
        if not link.exists() or link.read_text() != link_file():
            _put(link, link_file())
            run("udevadm", "control", "--reload")
    boot = UNITS / BOOT_UNIT
    if not boot.exists() or boot.read_text() != boot_unit():
        _put(boot, boot_unit())
        run("systemctl", "daemon-reload")
    try:
        _run_all(run, up_steps(plan))
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
    rec = _load(RECORD, {})
    if rec.get("up"):
        _run_all(run, down_steps(rec["plan"]), check=False)
    run("systemctl", "stop", "irate-box-ap-deadman.timer")
    run("systemctl", "disable", BOOT_UNIT)
    DISPATCHER.unlink(missing_ok=True)
    _put(RECORD, json.dumps({"up": False, "owner": rec.get("owner") or {}, "since": time.time()}))
    return "The hotspot is off."


DEADMAN_UNITS = ("irate-box-ap-deadman.timer", "irate-box-ap-deadman.service")


def remove(run):
    """Everything of irate-box's hotspot off and gone, whatever its record says: for the uninstaller (audit M6), run
    while this code is still installed and before the hotspot's firewall goes. stop() undoes only what the record
    says is up and keeps the files; this also covers a stale or partial install. Only irate-box's own: its units, the
    connection irate-box-ap, ap0 (the interface it adds), its files; never another hotspot or a joined network.
    Returns (off, problems): off only when nothing of it is still running."""
    for unit in (*DEADMAN_UNITS, BOOT_UNIT, DNSMASQ_UNIT, HOSTAPD_UNIT):
        run("systemctl", "disable", "--now", unit)
    run("nmcli", "connection", "down", CONNECTION)
    run("nmcli", "connection", "delete", CONNECTION)
    if run("ip", "link", "show", SHARED_IFACE).returncode == 0:
        run("iw", "dev", SHARED_IFACE, "del")
    for f in (
        Path(KEYFILE),
        Path(DNSMASQ_CONF),
        Path(HOSTAPD_CONF),
        UNITS / BOOT_UNIT,
        UNITS / DNSMASQ_UNIT,
        UNITS / HOSTAPD_UNIT,
        DISPATCHER,
        LINKS / LINK_FILE,
        RECORD,
        TRIED,
    ):
        f.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    run("udevadm", "control", "--reload")
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


def try_own_channel(run, inv, settings, ssid=DEFAULT_SSID, wait=TRY_WAIT, sleep=time.sleep):
    """Rung 2 or 3, tried on this box: the hotspot up on a channel other than the link's, held for a
    while with the link still up there? Recorded per radio; the hotspot left running as the plan
    then says. Returns (worked, plan)."""
    plan = apmode.plan(inv, inv.get("ap") or [], {}, {})
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
    tried = _load(TRIED, {})
    tried[plan["phy"]] = worked
    _put(TRIED, json.dumps(tried))
    return worked, start(run, inv, settings, owner, ssid)


def follow(run, inv, settings, iface, ssid=DEFAULT_SSID):
    """The dispatcher's call when a link comes up: a hotspot that follows the link moves to its channel.
    (moved, note): whether it moved, and what happened in words."""
    rec = _load(RECORD, {})
    if not rec.get("up") or not (rec.get("plan") or {}).get("follows_uplink") or rec["plan"].get("uplink") != iface:
        return False, "The hotspot has no link to follow."
    plan = apmode.plan(inv, inv.get("ap") or [], rec.get("owner") or {}, _load(TRIED, {}))
    if plan.get("channel") == rec["plan"].get("channel"):
        return False, f"The hotspot is still on channel {plan.get('channel')}."
    start(run, inv, settings, rec.get("owner") or {}, ssid)
    return True, f"The hotspot moved to channel {plan.get('channel')} with the link."
