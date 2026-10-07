# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot, applied (item 2 of the current and next actions): what the root helper writes and
runs for a plan from hub/apmode.py. As the prototype on the Lyra did it (2026-10-01, plan
archive): a second interface `ap0` when the hotspot shares the radio with the box's WiFi link
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

CONNECTION = "irate-box-ap"
SHARED_IFACE = "ap0"
KEYFILE = "/etc/NetworkManager/system-connections/irate-box-ap.nmconnection"
DNSMASQ_CONF = "/etc/hub/ap-dnsmasq.conf"
DNSMASQ_UNIT = "irate-box-ap-dns.service"
LEASES = "/var/lib/misc/dnsmasq.leases"     # the hub counts guests from it (server.py LEASES)
NETWORK = ipaddress.ip_network(f"{apmode.ADDRESS}/24", strict=False)
DHCP_FIRST, DHCP_LAST = 10, 200
DEFAULT_SSID = "Irate-Box"


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
    lines = ["# Written by irate-box (root/ap.py): the hotspot. Edits are overwritten.",
             "[connection]", f"id={CONNECTION}", "type=wifi", f"interface-name={ap_iface(plan)}",
             "autoconnect=true", "autoconnect-priority=-10", "",
             "[wifi]", "mode=ap", f"ssid={_keyfile_value(net['ssid'])}", f"band={band}", f"channel={int(plan['channel'])}", ""]
    if sec:
        lines.append("[wifi-security]")
        for k, v in sec.items():
            lines.append(f"{k.split('.', 1)[1]}={_keyfile_value(v)}")
        lines.append("")
    lines += ["[ipv4]", "method=manual", f"address1={apmode.ADDRESS}/{NETWORK.prefixlen}", "never-default=true", "",
              "[ipv6]", "method=disabled", ""]
    return "\n".join(lines)


def dnsmasq_conf(plan):
    """The box's dnsmasq for the hotspot alone: DHCP for guests, and every name answered with the hub."""
    first, last = NETWORK.network_address + DHCP_FIRST, NETWORK.network_address + DHCP_LAST
    return "\n".join([
        "# Written by irate-box (root/ap.py): DHCP and DNS on the hotspot only. Edits are overwritten.",
        f"interface={ap_iface(plan)}", "bind-dynamic", "except-interface=lo", "no-resolv", "no-hosts",
        f"dhcp-range={first},{last},{NETWORK.netmask},12h",
        f"dhcp-option=option:router,{apmode.ADDRESS}", f"dhcp-option=option:dns-server,{apmode.ADDRESS}",
        f"address=/#/{apmode.ADDRESS}", f"dhcp-leasefile={LEASES}", "dhcp-authoritative", "",
    ])


def up_steps(plan):
    """The commands that bring the hotspot up, in order (the files written first). Refuses a plan
    that waits on the owner, or has no channel."""
    if plan.get("needs_choice") or plan["kind"] == "none" or not plan.get("channel"):
        raise ValueError("this plan doesn't start a hotspot: " + plan.get("text", ""))
    steps = []
    if ap_iface(plan) == SHARED_IFACE:
        steps.append(["iw", "dev", plan["iface"], "interface", "add", SHARED_IFACE, "type", "__ap"])
        steps.append(["nmcli", "device", "set", SHARED_IFACE, "managed", "yes"])
    if plan.get("drops_uplink") and plan.get("uplink"):
        steps.append(["nmcli", "device", "disconnect", plan["uplink"]])
    steps += [["nmcli", "connection", "reload"], ["nmcli", "connection", "up", CONNECTION],
              ["systemctl", "restart", DNSMASQ_UNIT]]
    return steps


def down_steps(plan):
    """The commands that take it down again, leaving the box's own links as they were."""
    steps = [["systemctl", "stop", DNSMASQ_UNIT], ["nmcli", "connection", "down", CONNECTION]]
    if ap_iface(plan) == SHARED_IFACE:
        steps.append(["iw", "dev", SHARED_IFACE, "del"])
    if plan.get("drops_uplink") and plan.get("uplink"):
        steps.append(["nmcli", "device", "connect", plan["uplink"]])
    return steps


def dnsmasq_unit():
    """The dnsmasq unit for the hotspot (dnsmasq-base's binary; NetworkManager's shared mode isn't
    used, so the DNS answers are the hub's)."""
    return "\n".join([
        "[Unit]", "Description=Irate-Box: DHCP and DNS on the hotspot (root/ap.py)",
        "After=NetworkManager.service", "",
        "[Service]", f"ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file={DNSMASQ_CONF} --pid-file=",
        "Restart=on-failure", "",
        "[Install]", "WantedBy=multi-user.target", "",
    ])


# --- running it (the root helper) ------------------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "ap.json"            # what is up, from which plan, since when, and the owner's choices
TRIED = ETC / "ap-tried.json"       # {phy: True|False}: whether a channel of its own beside the link worked
DISPATCHER = Path(os.environ.get("HUB_NM_DISPATCHER", "/etc/NetworkManager/dispatcher.d")) / "90-irate-box-ap"
UNITS = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
TRY_WAIT = 20                        # seconds the hotspot must hold its own channel in the try
DEADMAN = 300                        # seconds before a hotspot that took the box's link undoes itself


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


def dispatcher_hook():
    """NetworkManager runs this on every link change: when the hotspot follows the link's channel
    (rung 3), a link that came up somewhere new moves it."""
    return "\n".join([
        "#!/bin/sh", "# Written by irate-box (root/ap.py): the hotspot follows the WiFi link's channel when it must.",
        '[ "$2" = up ] || exit 0', f'exec "{CODE}/irate-box" hub_control ap-follow "$1" >/dev/null 2>&1', ""])


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
    _put(Path(keyfile_path or KEYFILE), keyfile(plan, settings, ssid), 0o600)
    _put(Path(conf_path or DNSMASQ_CONF), dnsmasq_conf(plan))
    unit = UNITS / DNSMASQ_UNIT
    if not unit.exists() or unit.read_text() != dnsmasq_unit():
        _put(unit, dnsmasq_unit())
        run("systemctl", "daemon-reload")
    _put(DISPATCHER, dispatcher_hook(), 0o755)
    try:
        _run_all(run, up_steps(plan))
    except RuntimeError:
        _run_all(run, down_steps(plan), check=False)
        raise
    if plan.get("drops_uplink"):
        # The owner gave up the box's link: unless confirmed from the hotspot in time, it comes back.
        run("systemd-run", f"--on-active={DEADMAN}", "--unit=irate-box-ap-deadman", "--timer-property=AccuracySec=1s",
            f"{CODE}/irate-box", "hub_control", "ap-revert")
    _put(RECORD, json.dumps({"up": True, "plan": plan, "owner": owner, "since": time.time(),
                             "confirmed": not plan.get("drops_uplink")}))
    return plan


def stop(run):
    rec = _load(RECORD, {})
    if rec.get("up"):
        _run_all(run, down_steps(rec["plan"]), check=False)
    run("systemctl", "stop", "irate-box-ap-deadman.timer")
    DISPATCHER.unlink(missing_ok=True)
    _put(RECORD, json.dumps({"up": False, "owner": rec.get("owner") or {}, "since": time.time()}))
    return "the hotspot is off"


def confirm(run):
    """The owner reached the box through the hotspot after giving up its link: keep it so."""
    run("systemctl", "stop", "irate-box-ap-deadman.timer")
    rec = _load(RECORD, {})
    rec["confirmed"] = True
    _put(RECORD, json.dumps(rec))
    return "kept: the hotspot stays, the box's WiFi link stays off"


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
    other = next((c["channel"] for c in chans if c["channel"] in apmode.PREFERRED_24 and c["channel"] != link_ch), None) \
        or next((c["channel"] for c in chans if c["channel"] != link_ch), None)
    if other is None:
        return None, plan
    start(run, inv, settings, {"channel": other}, ssid)
    sleep(wait)
    now = channels_now(run)
    worked = now.get(ap_iface(plan)) == other and now.get(link) == link_ch and link_ch is not None
    tried = _load(TRIED, {})
    tried[plan["phy"]] = worked
    _put(TRIED, json.dumps(tried))
    return worked, start(run, inv, settings, _load(RECORD, {}).get("owner") or {}, ssid)


def follow(run, inv, settings, iface, ssid=DEFAULT_SSID):
    """The dispatcher's call when a link comes up: a hotspot that follows the link moves to its channel."""
    rec = _load(RECORD, {})
    if not rec.get("up") or not (rec.get("plan") or {}).get("follows_uplink") or rec["plan"].get("uplink") != iface:
        return "nothing to follow"
    plan = apmode.plan(inv, inv.get("ap") or [], rec.get("owner") or {}, _load(TRIED, {}))
    if plan.get("channel") == rec["plan"].get("channel"):
        return f"still on channel {plan.get('channel')}"
    start(run, inv, settings, rec.get("owner") or {}, ssid)
    return f"moved to channel {plan.get('channel')} with the link"
