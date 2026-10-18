# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A watchdog per link (hub/uplink.py), offline: the links found and their kinds, which are watched (the route's, the
owner's, every one, none; one in trouble kept), the old single "watch this" carried over, steps that reach every link
held while another works, a cable unplugged waited for (or reconnected, or met with the ladder), a cable link's words,
the connections that would start by themselves on an unwatched link, and the report with each link's own. No network
is touched. python3 tests/sim_links.py"""

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import netinv
from irate_box.hub import uplink as U

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


T = Path(tempfile.mkdtemp(prefix="links-"))
U.SYS_NET = T / "class/net"


def iface(name, kind_type="1", wifi=False, device=True, uevent="", carrier=None):
    d = U.SYS_NET / name
    d.mkdir(parents=True)
    (d / "type").write_text(kind_type + "\n")
    (d / "uevent").write_text(uevent)
    if wifi:
        (d / "phy80211").mkdir()
    if device:
        (d / "device").mkdir()
    if carrier is not None:
        (d / "carrier").write_text(("1" if carrier else "0") + "\n")


# A Pi 3B+ with a dongle or two: WiFi on SDIO, the cable, the hotspot, a phone tethered, a modem, a bridge.
iface("lo", "772", device=False)
iface("wlan0", wifi=True)
iface("ap0", wifi=True)
iface("eth0", carrier=True)
iface("usb0")
iface("wwan0", uevent="DEVTYPE=wwan\n")
iface("br0", device=False)

check(
    "kinds: WiFi, a cable (and a tethered phone's USB link), a cellular modem as other",
    [U.link_kind(i) for i in ("wlan0", "eth0", "usb0", "wwan0")] == ["wifi", "ethernet", "ethernet", "other"],
)
c = U.candidate_links({"wlan0": "managed", "ap0": "AP"})
check(
    "the links that could carry the box: not the loopback, the hotspot's radio or a bridge",
    c == {"eth0": "ethernet", "usb0": "ethernet", "wlan0": "wifi", "wwan0": "other"},
    c,
)
check("carrier read as it is", U.carrier("eth0") is True and U.carrier("wlan0") is None)


# --- which are watched ---
def S(**kw):
    return U.validate(kw)


W = lambda *a, **k: set(U.watched_links(*a, **k))  # noqa: E731  which links, whatever how
check(
    "the route's link (the default): eth0 holds the route, so eth0 only: the Pi's case, wlan0 unwatched",
    W(S(), "eth0", c) == {"eth0"},
)
check("  wlan0 set to be watched too: both", W(S(watch={"wlan0": True}), "eth0", c) == {"eth0", "wlan0"})
check(
    "  the route's link set not to be watched: not watched even holding the route",
    W(S(watch={"eth0": False}), "eth0", c) == set(),
)
check(
    "  a link in trouble stays watched when the route moves off it",
    W(S(), "eth0", c, troubled={"wlan0"}) == {"eth0", "wlan0"},
)
check("  no route, nothing watched yet: the first WiFi client", W(S(), None, c) == {"wlan0"})
check(
    "every link: all of them but one set not to be",
    W(S(watch_new="all", watch={"wwan0": False}), "eth0", c) == {"eth0", "usb0", "wlan0"},
)
check(
    "none: only those set",
    W(S(watch_new="none", watch={"wlan0": True}), "eth0", c) == {"wlan0"}
    and W(S(watch_new="none"), "eth0", c) == set(),
)
check(
    "  one set but not there now (a radio gone with its driver) is still watched",
    W(S(watch_new="none", watch={"wlan1": True}), "eth0", c) == {"wlan1"},
)

H = U.watched_links
check(
    "looked at only: the route's link in full, every other looked at, one set not to be left out",
    H(S(watch_new="look", watch={"wwan0": False}), "eth0", c) == {"eth0": "full", "usb0": "look", "wlan0": "look"},
)
check(
    "  one set to be watched is watched in full, whatever the rest",
    H(S(watch_new="look", watch={"wlan0": True}), "eth0", c)["wlan0"] == "full",
)
check(
    "  a link in trouble keeps how it was watched when the route moves off it",
    H(S(), "usb0", c, troubled={"eth0": "full"}) == {"usb0": "full", "eth0": "full"}
    and H(S(watch_new="look"), "eth0", c, troubled={"wlan0": "look"})["wlan0"] == "look",
)

# --- a link known by its name, then by its hardware ---
hw = {
    "enx001122334455": {"mac": "00:11:22:33:44:55", "path": "usb:1-1.3:1.0"},
    "eth1": {"mac": None, "path": "usb:1-1.2:1.0"},
    "usb0": {"mac": "aa:aa:aa:aa:aa:aa", "path": "usb:1-1.4:1.0"},
}
saved = S(
    watch={"enx0": True, "eth9": False},
    watch_hw={
        "enx0": {"mac": "00:11:22:33:44:55", "path": "usb:1-1.1:1.0"},
        "eth9": {"mac": None, "path": "usb:1-1.2:1.0"},
    },
)
cc = {"eth0": "ethernet", "enx001122334455": "ethernet", "eth1": "ethernet", "usb0": "ethernet"}
al = U.aliases(saved, cc, hw=lambda i: hw.get(i, {}))
check(
    "a USB adapter back under another name: known by its own address first, then by its place on the bus",
    al == {"enx001122334455": "enx0", "eth1": "eth9"},
    al,
)
check(
    "  its settings follow it: watched, or not, as it was set",
    H(saved, "eth0", cc, alias=al) == {"eth0": "full", "enx001122334455": "full"},
)
check(
    "  while the old name is there, nothing is taken for it",
    U.aliases(saved, {**cc, "enx0": "ethernet"}, hw=lambda i: hw.get(i, {})) == {"eth1": "eth9"},
)
check("  no hardware recorded: names only", U.aliases(S(watch={"enx0": True}), cc, hw=lambda i: hw.get(i, {})) == {})
for bad in (
    {"watch_hw": {"x": {"mac": "zz"}}},
    {"watch_hw": {"x": {"path": "no spaces allowed"}}},
    {"watch_hw": {"a;b": {}}},
):
    try:
        U.validate(bad)
        check("  bad hardware refused", False)
    except ValueError:
        check("  bad hardware refused", True)
(U.SYS_NET / "eth0" / "address").write_text("b8:27:eb:1e:3a:13\n")
(U.SYS_NET / "eth0" / "addr_assign_type").write_text("0\n")
check(
    "a link's hardware read: its own address",
    U.link_hw("eth0", run=lambda *a, **k: (1, ""))["mac"] == "b8:27:eb:1e:3a:13",
)
(U.SYS_NET / "wlan0" / "address").write_text("12:34:56:78:9a:bc\n")
(U.SYS_NET / "wlan0" / "addr_assign_type").write_text("3\n")
check(
    "  an address given to it: its own asked for (ethtool -P)",
    U.link_hw("wlan0", run=lambda *a, **k: (0, "Permanent address: b8:27:eb:4b:6f:46"))["mac"] == "b8:27:eb:4b:6f:46",
)

# --- the old "Watch" choice carried over ---
check(
    "an interface chosen before: that one watched, no other",
    (S(iface="wlan0")["watch"], S(iface="wlan0")["watch_new"]) == ({"wlan0": True}, "none"),
)
check("  'auto' before: the route's link", S(iface="auto")["watch_new"] == "uplink" and S()["watch"] == {})
check(
    "  saved again with the new choices: kept as they are",
    S(watch={"eth0": True}, watch_new="all")["watch_new"] == "all",
)
for bad in (
    {"watch": {"a;b": True}},
    {"watch": {"eth0": "yes"}},
    {"watch_new": "some"},
    {"box_steps": "never"},
    {"cable_out": "panic"},
    {"watch": {f"x{n}": True for n in range(20)}},
):
    try:
        U.validate(bad)
        check(f"  {bad if len(str(bad)) < 60 else 'too many links'} refused", False)
    except ValueError:
        check(f"  {bad if len(str(bad)) < 60 else 'too many links'} refused", True)
check(
    "said: which links are watched",
    U.watching_words(S(watch={"wlan0": True, "eth0": False}))
    == "the link holding the box's route and wlan0 (never eth0)",
)
check(
    "  and the new choices in the settings' words when not the default",
    "for the route's link even with another up" in U.words(S(box_steps="uplink"))
    and "cable unplugged reconnected" in U.words(S(cable_out="reconnect"))
    and "cable" not in U.words(S()),
)

# --- steps that reach every link ---
ALL = {"reconnect", "restart", "radio", "reboot"}


def run_outage(
    box_steps="nothing-works", others=("eth0",), is_uplink=False, secs=8000, cause=None, cable_out="wait", labels=None
):
    w = U.Watch(U.effective(S(pace="gentle", reach="reboot", box_steps=box_steps, cable_out=cable_out)), labels=labels)
    acts = []
    for t in range(0, secs, 60):
        obs = {
            "link": False,
            "gateway": None,
            "drops": [],
            "guests": 0,
            "uptime": 1e9,
            "can": ALL,
            "others_up": list(others),
            "is_uplink": is_uplink,
            "cause": cause,
        }
        acts += [(t, a) for a in w.tick(1000 + t, obs)]
    return acts, w


a, w = run_outage()
check(
    "nothing works but this, the default: with eth0 up, wlan0's ladder never restarts the network service nor reboots",
    {x for _, x in a} == {"reconnect", "radio"},
    a,
)
held = [e["text"] for e in w.events if e["kind"] == "held"]
check(
    "  held, and said why",
    any("eth0 is up, and the network service and the box are restarted only when nothing works" in h for h in held),
    held,
)
a, w = run_outage(others=())
check("  nothing else up: the whole ladder", {x for _, x in a} == ALL, a)
a, w = run_outage(box_steps="uplink", is_uplink=False)
check("the route's link only: not for a link without the route", {x for _, x in a} == {"reconnect", "radio"})
a, w = run_outage(box_steps="uplink", is_uplink=True)
check("  for the route's link, even with another up", {x for _, x in a} == ALL)
a, w = run_outage(box_steps="any")
check("any watched link: as before links", {x for _, x in a} == ALL)
w = U.Watch(U.effective(S()))
got, why = w.by_hand(1000, {"can": ALL, "others_up": ["eth0"], "uptime": 1e9}, "reboot")
check("a reboot asked for by hand: the owner's call, not held for another link", got == ["reboot"] and why is None, why)

# --- a cable unplugged ---
cable = {"radio": "reset the adapter"}
a, w = run_outage(others=(), cause="cable", labels=cable)
check("no cable, wait (the default): nothing done", a == [], a)
check(
    "  said: waiting, and why",
    w.stall(9000)["text"].startswith("Waiting: no cable")
    and any("plugging it in, or switching the other end on" in e["text"] for e in w.events if e["kind"] == "cause"),
    list(w.events)[-3:],
)
a, w = run_outage(others=(), cause="cable", cable_out="reconnect", labels=cable)
check("  reconnect: only reconnects, now and then", a and {x for _, x in a} == {"reconnect"}, a)
a, w = run_outage(others=(), cause="cable", cable_out="ladder", labels=cable)
check("  the ladder: every step", {x for _, x in a} == ALL, a)
check(
    "  a cable link's words: the adapter reset, not the radio",
    any(e["text"].startswith("Reset the adapter") for e in w.events),
    [e["text"] for e in w.events][:8],
)


# A cable plugged back in, and the link not up with it: reconnected at once, the adapter reset soon after, the steps
# counted from the re-plug (not all due at once after a long unplug).
def replug(others=()):
    w = U.Watch(U.effective(S()), labels=cable)
    acts = []
    for t in range(0, 3600 + 8000, 60):
        obs = {
            "link": False,
            "gateway": None,
            "drops": [],
            "guests": 0,
            "uptime": 1e9,
            "can": ALL,
            "others_up": list(others),
            "cause": "cable" if t < 3600 else "plugged",
        }
        acts += [(t - 3600, a) for a in w.tick(1000 + t, obs)]
    return acts, w


a, w = replug()
check(
    "a cable back after an hour, no link: a reconnect at once, the adapter reset 3 min later, the rest from the re-plug",
    a[:2] == [(0, "reconnect"), (180, "radio")]
    and [x for x in a if x[1] not in ("reconnect", "radio")] == [(1800, "restart"), (7200, "reboot")],
    a,
)
check("  said why", any("the cable is back" in e["text"] for e in w.events if e["kind"] == "repair"))
a, w = replug(others=["wlan0"])
check("  with another link up: never restarted or rebooted for", {x for _, x in a} == {"reconnect", "radio"}, a)
# --- a cable link's own reach: by default it only reconnects ---
steps = lambda s: sorted(U.Link("eth0", s, None, []).w.eff["steps"])  # noqa: E731
check(
    "a cable link by default: reconnects only (a lost cable is most often pulled, which no step mends)",
    steps(S()) == ["reconnect"],
)
check(
    "  the WiFi beside it keeps the whole ladder",
    sorted(U.Link("wlan0", S(), None, []).w.eff["steps"]) == ["radio", "reboot", "reconnect", "restart"],
)
check(
    "  how far on a cable, raised: as far as that, and never past the box's reach",
    steps(S(cable_reach="radio")) == ["radio", "reconnect", "restart"]
    and steps(S(reach="reconnect", cable_reach="reboot")) == ["reconnect"],
)
check(
    "  a reach set for that cable alone wins",
    steps(S(reach="reboot", per_iface={"eth0": {"reach": "radio"}})) == ["radio", "reconnect", "restart"],
)
check(
    "  said in the settings' words when not the default",
    "on a cable as far as to reset the adapter" in U.words(S(cable_reach="radio")),
)
outage = U.outage_cause
check(
    "the cause: carrier back and the link not up, plugged",
    outage("eth0", False, None, False, True, None, None, None, carrier=True) == "plugged",
)
check(
    "the cause on a cable: no carrier, no cable; gone, a failed adapter; up with the gateway silent, the gateway",
    outage("eth0", False, None, False, True, None, None, None, carrier=False) == "cable"
    and outage("eth0", False, None, False, False, None, None, None, carrier=False) == "radio"
    and outage("eth0", True, False, False, True, None, None, None, carrier=True) == "gateway"
    and outage("eth0", False, None, False, True, None, None, None) is None,
)


# --- what an unwatched link would carry ---
def nm(*cmd, timeout=60):
    if cmd[:4] == ("nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,AUTOCONNECT,ACTIVE"):
        return 0, "\n".join(
            [
                "netplan-eth0:u1:802-3-ethernet:eth0:yes:yes",
                "netplan-wlan0-SKYTB1H1:u2:802-11-wireless::yes:no",
                "Hotspot:u3:802-11-wireless::no:no",
                "other-radio:u4:802-11-wireless::yes:no",
            ]
        )
    if cmd[:3] == ("nmcli", "-g", "connection.interface-name"):
        return 0, {"u2": "", "u4": "wlan1"}.get(cmd[-1], "")
    return 1, ""


U.shutil.which = lambda name: "/usr/bin/" + name
check(
    "connections that start by themselves: the WiFi profile (not up), the cable's (up); not one off, nor one bound to another link",
    U.autoconnect_for("wlan0", "wifi", run=nm) == ["netplan-wlan0-SKYTB1H1"]
    and U.autoconnect_for("eth0", "ethernet", run=nm) == ["netplan-eth0"]
    and U.autoconnect_for("wwan0", "other", run=nm) == [],
)

# --- a cable link's steps ---
facts = {"bus": "usb", "driver": "lan78xx", "usb": {"id": "0424:7800", "port": "1-1.1.1"}}
netinv.device_facts = lambda i: facts
check("a cable link on USB can have its adapter reset", "radio" in U.repairs_for("networkmanager", "eth0"))
check("  a cellular modem: no adapter reset known", "radio" not in U.repairs_for("networkmanager", "wwan0"))

# --- the report ---
U.netinv.run = lambda *a, **k: (1, "")
lw, le = U.Link("wlan0", S(), None, []), U.Link("eth0", S(), None, [])
check(
    "each link its own watchdog, its kind and words",
    lw.kind == "wifi"
    and le.kind == "ethernet"
    and le.w.labels["radio"] == "reset the adapter"
    and lw.w.labels["radio"] == "reset the radio",
)
shared = []
lw, le = U.Link("wlan0", S(), None, shared), U.Link("eth0", S(), None, shared)
check(
    "  the reboot record shared, so the daily cap counts every link's",
    lw.w.reboots is shared and le.w.reboots is shared,
)
lw.w.log(100, "down", "Link lost.")
le.w.log(50, "info", "Watching eth0.")
for lk, up in ((lw, False), (le, True)):
    lk.up, lk.gw, lk.answers, lk.obs, lk.backend = (
        up,
        "192.168.1.1" if up else None,
        up or None,
        {"guests": 0},
        "networkmanager",
    )
rep = U._report(
    {"wlan0": lw, "eth0": le},
    {"usb0": {"state": "up", "watched": False}},
    {"wlan0": {"kind": "wifi"}},
    "eth0",
    S(),
    False,
    [],
    200,
)
check(
    "the report: the route's link at the top, as before; each link's own; the one no longer watched; the inventory",
    rep["iface"] == "eth0"
    and rep["primary"] == "eth0"
    and set(rep["links"]) == {"wlan0", "eth0", "usb0"}
    and rep["links"]["wlan0"]["watched"]
    and rep["links"]["usb0"]["watched"] is False
    and rep["inventory"] == {"wlan0": {"kind": "wifi"}},
)
check(
    "  every link's events in one time line, each with its link",
    [(e["iface"], e["text"]) for e in rep["events"]] == [("eth0", "Watching eth0."), ("wlan0", "Link lost.")],
)
check(
    "  a link's own report without the box's settings and records",
    "chosen" not in rep["links"]["wlan0"] and "chosen" in rep,
)
check("  healthy: up with a gateway that answers", le.healthy() and not lw.healthy())

# --- the box doctor: the links nobody watches ---
from irate_box.finding import message_args as msg_args
from irate_box.finding import message_ids as msg_ids
from irate_box.msg import render
from irate_box.root import health

pi = {
    "iface": "eth0",
    "chosen": S(),
    "watching": "the link holding the box's route",
    "links": {"eth0": {"watched": True, "kind": "ethernet", "state": "up"}},
    "inventory": {
        "eth0": {"kind": "ethernet", "watched": True, "up": True, "autoconnect": ["netplan-eth0"]},
        "wlan0": {"kind": "wifi", "watched": False, "up": False, "autoconnect": ["netplan-wlan0-SKYTB1H1"]},
    },
}
f = health.link_findings(pi)
check(
    "the Pi's case: wlan0 down and nobody watching it, a problem, with a reconnect and the way to watch it",
    len(f) == 1
    and f[0]["id"] == "uplink-uncovered-wlan0"
    and f[0]["status"] == "problem"
    and [(a["choice"], render(a["label"])) for a in f[0]["actions"]] == [("uplink-reconnect:wlan0", "Reconnect wlan0")]
    and f[0]["go"]["mark"] == "up-watch-wlan0"
    and "netplan-wlan0-SKYTB1H1" in f[0]["evidence"],
    f,
)
up = dict(pi, inventory={**pi["inventory"], "wlan0": dict(pi["inventory"]["wlan0"], up=True)})
f = health.link_findings(up)
check(
    "  up but not watched: worth a look, no button",
    f[0]["status"] == "warn" and not f[0]["actions"] and "uplink-uncovered.why" in msg_ids(f[0]),
    f,
)
check(
    "  set not to be watched: the owner's, not said",
    health.link_findings(dict(pi, chosen=S(watch={"wlan0": False}))) == [],
)
check(
    "  a link with nothing set to start on it (a spare port): not said",
    health.link_findings(
        dict(
            pi,
            inventory={
                "eth0": pi["inventory"]["eth0"],
                "usb0": {"kind": "ethernet", "watched": False, "up": False, "autoconnect": []},
            },
        )
    )
    == [],
)
f = health.link_findings(
    {
        "iface": None,
        "state": "no-link",
        "chosen": S(watch_new="none"),
        "watching": "nothing: no link is set to be watched",
        "links": {},
        "inventory": pi["inventory"],
    }
)
check(
    "none watched: a problem, said with what it is watching",
    f[0]["id"] == "uplink-none-watched"
    and f[0]["status"] == "problem"
    and f[0]["evidence"] == ["nothing: no link is set to be watched"],
    f,
)
check("  a report from before links: nothing said", health.link_findings({"iface": "wlan0", "state": "up"}) == [])
two = dict(
    pi,
    links={
        **pi["links"],
        "wlan0": {
            "watched": True,
            "kind": "wifi",
            "state": "stalled",
            "stall": {"needs": "reboot", "label": "reboot", "text": "Stalled: what could help now is to reboot."},
        },
    },
    inventory={**pi["inventory"], "wlan0": dict(pi["inventory"]["wlan0"], watched=True)},
)
f = health.link_findings(two)
check(
    "a second watched link stalled: said for it, as the first link's is",
    [x["id"] for x in f] == ["uplink-stalled-wlan0"] and msg_args(f[0], "uplink-stalled-link.fix")["step"] == "reboot",
    f,
)
look = dict(pi, inventory={**pi["inventory"], "wlan0": dict(pi["inventory"]["wlan0"], watched=True, look=True)})
f = health.link_findings(look)
check(
    "looked at only and down: said, with a reconnect, and the way to watch it in full",
    [x["id"] for x in f] == ["uplink-look-wlan0"]
    and f[0]["status"] == "warn"
    and f[0]["actions"][0]["choice"] == "uplink-reconnect:wlan0",
    f,
)
renamed = dict(
    pi,
    chosen=S(watch={"wlan9": False}),
    inventory={**pi["inventory"], "wlan0": dict(pi["inventory"]["wlan0"], **{"as": "wlan9"})},
)
check(
    "  set not to be watched under the name it had (the same hardware): not said", health.link_findings(renamed) == []
)
for good in ("uplink-reconnect:wlan0", "uplink-reconnect:enx00e04c680001"):
    check(f"the doctor takes {good}", bool(health.CHOICE_RE.match(good)))
for bad in ("uplink-reconnect:a;b", "uplink-reconnect:", "uplink-reconnect:-x"):
    check(f"  and refuses {bad}", not health.CHOICE_RE.match(bad))

# --- saved on /admin: each named link's hardware recorded ---
from irate_box.hub import roaming
from irate_box.root import hub_control as HC

kept = {}
HC.uplink.load_settings = lambda: S(
    watch_hw={"gone0": {"mac": "aa:bb:cc:dd:ee:01", "path": None}}, watch={"gone0": True}
)
HC.uplink.save_settings = lambda s: kept.update(s) or s
HC._uplink_running = lambda: None
roaming.apply = lambda s, old, iface: ""
U.link_hw = lambda i, run=None: {"mac": {"eth0": "b8:27:eb:1e:3a:13"}.get(i), "path": f"usb:{i}"}
said = HC.uplink_set({"settings": dict(S(), watch={"eth0": True, "gone0": True, "wlan0": False})})
check(
    "saved: each link named, its hardware as it is now; one not here keeps what was recorded",
    kept["watch_hw"]
    == {
        "eth0": {"mac": "b8:27:eb:1e:3a:13", "path": "usb:eth0"},
        "gone0": {"mac": "aa:bb:cc:dd:ee:01", "path": None},
        "wlan0": {"mac": None, "path": "usb:wlan0"},
    }
    and said["id"] == "control.uplink.saved"
    and said["args"]["links"] == "the link holding the box's route, eth0 and gone0 (never wlan0)",
    (kept.get("watch_hw"), said),
)

# --- resetting a PCI card and a device on the SoC (root/radio.py) ---
from irate_box.root import radio

R = T / "radio-sys"
radio.SYS = R


def device(iface, bus, name, driver, reset=True):
    dev = R / "devices" / ("pci0000:00" if bus == "pci" else "platform") / name
    drv = R / "bus" / bus / "drivers" / driver
    for d in (dev, drv, R / "class/net" / iface):
        d.mkdir(parents=True, exist_ok=True)
    for f in ("bind", "unbind"):
        (drv / f).write_text("")
    (dev / "subsystem").symlink_to(R / "bus" / bus)
    (dev / "driver").symlink_to(drv)
    if reset:
        (dev / "reset").write_text("")
        (R / "bus/pci/devices").mkdir(parents=True, exist_ok=True)
        (R / "bus/pci/devices" / name).symlink_to(dev)
    (R / "class/net" / iface / "device").symlink_to(dev)
    return dev, drv


dev, drv = device("wlp1s0", "pci", "0000:01:00.0", "iwlwifi")
(R / "bus/pci/rescan").write_text("")
calls = []


def quiet(*c, timeout=60):
    return (calls.append(c), (1, ""))[1]  # NetworkManager not running here


check(
    "a PCI card found as one",
    radio._bus_target("wlp1s0") == {"bus": "pci", "name": "0000:01:00.0", "driver": "iwlwifi"},
)
said = radio.reset("wlp1s0", None, "iwlwifi", None, run=quiet, sleep=lambda s: None, wait=1)
check(
    "  reset: its function reset first, and back",
    (dev / "reset").read_text() == "1" and "PCI 0000:01:00.0 (iwlwifi) reset" in said,
    said,
)
dev2, drv2 = device("wlp2s0", "pci", "0000:02:00.0", "mt7921e")
(dev2 / "reset").unlink()
said = radio.reset("wlp2s0", None, "mt7921e", None, run=quiet, sleep=lambda s: None, wait=1)
check(
    "  one with no function reset: unbound and bound again",
    (drv2 / "unbind").read_text() == "0000:02:00.0"
    and (drv2 / "bind").read_text() == "0000:02:00.0"
    and "unbound and bound again" in said,
    said,
)
dev3, drv3 = device("end0", "platform", "fe300000.ethernet", "rk_gmac-dwmac", reset=False)
check(
    "a device on the SoC found as one",
    radio._bus_target("end0") == {"bus": "platform", "name": "fe300000.ethernet", "driver": "rk_gmac-dwmac"},
)
said = radio.reset("end0", None, "stmmac", None, run=quiet, sleep=lambda s: None, wait=1)
check(
    "  reset: unbound from its driver and bound again",
    (drv3 / "bind").read_text() == "fe300000.ethernet" and "fe300000.ethernet (rk_gmac-dwmac) unbound" in said,
    said,
)
check("  neither for a USB or SDIO device, nor a name that isn't a PCI address", radio._bus_target("nothere") is None)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
