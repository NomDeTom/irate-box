# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Experiments the box runs on its own radios, at the owner's asking, to answer what the hardware tab
would otherwise leave as "not tested": short, put back as they were, refused while guests are on the
hotspot. Each answer is kept per radio (its USB id or driver, and the driver's version) with when it
was found, and asked again when the driver changes.

  follows-roam   Does the hotspot stay on its channel, or follow, when the WiFi link moves to an access
                 point on another channel? The link's profile is held to one (of the same network) for
                 the test, the hotspot's channel read, and the profile given back what it had.
  radio-reset    How long a radio reset takes (root/radio.py, as the uplink watchdog does it), and
                 whether the hotspot comes back.
  driver-reset   Where the driver resets its own device on a command timeout and offers a fake one
                 (the patched AIC8800's fake_cmd_timeout): how long until the link is back.
  own-channel    Whether the hotspot can keep a channel of its own beside the link (root/ap.py's
                 try_own_channel, run as an experiment).

Results go into the radio's capability record (root/radiocaps.py), as its `test` rows, keyed by the radio
and its driver; the old radio-tests.json is read once by that module's migration. Root only; stdlib only.
"""

import os
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from irate_box.hub import netinv
from irate_box.msg import M, Refused, duration, render, sentences
from irate_box.root import radiocaps

SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
QUESTION_MSG = {
    "follows-roam": M(
        "radio-test.q.follows-roam",
        "Whether the hotspot keeps its channel or follows the link, when your WiFi roams to another channel.",
    ),
    "radio-reset": M(
        "radio-test.q.radio-reset", "How long a radio reset takes, and whether the hotspot comes back after it."
    ),
    "driver-reset": M(
        "radio-test.q.driver-reset",
        "Whether the driver reset brings the WiFi back without help, and how many seconds it takes.",
    ),
    "own-channel": M(
        "radio-test.q.own-channel",
        "Whether the hotspot can keep a channel of its own beside your WiFi link.",
    ),
    "csa": M(
        "radio-test.q.csa",
        "Whether the hotspot can change channel in place, without dropping its guests.",
    ),
}
QUESTIONS = {e: render(m) for e, m in QUESTION_MSG.items()}  # in English, as netinv gives them to the page


def run(*cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, (p.stdout + p.stderr).strip()


def results(radio):
    """This radio's test answers from the capability record, keyed by experiment: {exp: {answer, detail, at}}."""
    from irate_box.root import radiocaps as caps

    reverse = {rid: exp for exp, (rid, _values) in caps.ANSWER_ROW.items()}
    out = {}
    for rid, row in caps.stored(radio).items():
        exp = reverse.get(rid)
        if exp and row.get("source") == "test":
            out[exp] = {"answer": row.get("value"), "detail": row.get("detail"), "at": row.get("at")}
    return out


def _keep(radio, exp, answer, detail):
    radiocaps.record_test(radio, exp, answer, render(detail))


def radio_key(radio):
    """A radio's identity for its answers: its USB id and port, and its driver's identity."""
    return radiocaps.key(radio)


def _channels():
    """{iface: channel} from iw dev."""
    code, out = run("iw", "dev")
    chans, cur = {}, None
    for line in out.splitlines() if code == 0 else []:
        m = re.match(r"\s*Interface (\S+)", line)
        if m:
            cur = m.group(1)
        m = re.match(r"\s*channel (\d+)", line)
        if m and cur:
            chans[cur] = int(m.group(1))
    return chans


def _guests(ap):
    code, out = run("iw", "dev", ap, "station", "dump")
    return sum(1 for ln in out.splitlines() if ln.startswith("Station")) if code == 0 else 0


def _link_up(iface, wait, sleep):
    for _ in range(int(wait)):
        code, out = run("iw", "dev", iface, "link")
        if code == 0 and out.startswith("Connected"):
            return True
        sleep(1)
    return False


def check_ready(inv, iface, exp):
    """(radio, ap iface or None) to run `exp` on, or Refused saying why not."""
    if exp not in QUESTIONS:
        raise Refused(
            M("radio-test.unknown", "The experiment {exp} is not available. The box does not know it.", exp=str(exp))
        )
    if not IFACE_RE.fullmatch(str(iface or "")):
        raise Refused(M("radio-test.request.iface", "The request is not valid: the interface name is not valid."))
    radio = next((r for r in inv.get("radios", []) if r.get("iface") == iface and r.get("type") == "managed"), None)
    if not radio:
        raise Refused(M("radio-test.not-link", "{iface} is not a WiFi link on this box.", iface=iface))
    ap = next((r["iface"] for r in inv["radios"] if r.get("type") == "AP" and r.get("phy") == radio.get("phy")), None)
    if ap and _guests(ap):
        raise Refused(
            M(
                "radio-test.guests",
                "Devices are connected to the hotspot. Select a time when no device is connected to it.",
            )
        )
    if exp == "follows-roam":
        if not netinv.nm_runs_wifi(inv):
            raise Refused(
                M(
                    "radio-test.no-nm",
                    "NetworkManager does not run the WiFi of this box, so the test cannot start a roam.",
                )
            )
        if not ap:
            raise Refused(
                M(
                    "radio-test.no-hotspot",
                    "There is no hotspot on this radio, so the hotspot has no channel to follow.",
                )
            )
        aps = (radio.get("roaming") or {}).get("aps") or []
        here = (radio.get("link") or {}).get("bssid")
        here_ch = (radio.get("link") or {}).get("channel")
        if not [a for a in aps if a.get("bssid") != here and a.get("channel") and a["channel"] != here_ch]:
            raise Refused(
                M(
                    "radio-test.no-roam",
                    "Your network has no access point on another channel in reach, so the test cannot start a roam.",
                )
            )
    if (
        exp == "driver-reset"
        and not (
            SYS / "module" / (radio.get("driver") or "?").replace("-", "_") / "parameters" / "fake_cmd_timeout"
        ).exists()
    ):
        raise Refused(
            M(
                "radio-test.no-fake",
                "This driver has no fake command timeout, so the test cannot start the driver reset.",
            )
        )
    if exp == "own-channel" and not (
        radio.get("ap") and radio.get("ap_beside_client") and radio.get("channels_at_once", 1) >= 2
    ):
        raise Refused(
            M(
                "radio-test.no-own-channel",
                "This radio cannot run the hotspot on a channel of its own beside your WiFi link.",
            )
        )
    if exp == "csa":
        if "channel_switch" not in (radio.get("commands") or []):
            raise Refused(
                M(
                    "radio-test.no-csa",
                    "This driver does not declare channel switch, so the test cannot start it.",
                )
            )
        if not ap:
            raise Refused(
                M(
                    "radio-test.csa.no-hotspot",
                    "There is no hotspot on this radio, so there is no channel to switch.",
                ),
            )
    return radio, ap


def follows_roam(radio, ap, sleep=time.sleep):
    iface = radio["iface"]
    prof = radio.get("profile") or {}
    here = (radio.get("link") or {}).get("bssid")
    here_ch = (radio.get("link") or {}).get("channel")
    target = next(
        a
        for a in sorted((radio.get("roaming") or {}).get("aps") or [], key=lambda a: -(a.get("signal") or 0))
        if a.get("bssid") != here and a.get("channel") and a["channel"] != here_ch
    )
    before = _channels().get(ap)
    # "nmcli con up ... ap BSSID" is only a hint (the supplicant may pick the strongest instead), so the
    # profile is held to the target for the test, and given back what it had after.
    code, was = run("nmcli", "-g", "802-11-wireless.bssid", "con", "show", "uuid", prof["uuid"])
    was = (was or "").replace("\\:", ":").strip() if code == 0 else ""
    t0 = time.time()
    try:
        run("nmcli", "con", "modify", "uuid", prof["uuid"], "802-11-wireless.bssid", target["bssid"])
        code, out = run("nmcli", "con", "up", "uuid", prof["uuid"], timeout=60)
        took = round(time.time() - t0)
        sleep(5)
        chans = _channels()
        after, link_ch = chans.get(ap), chans.get(iface)
    finally:
        run("nmcli", "con", "modify", "uuid", prof["uuid"], "802-11-wireless.bssid", was)
        back = run("nmcli", "con", "up", "uuid", prof["uuid"], timeout=60)[0] == 0
    if code != 0:
        return "unknown", M(
            "radio-test.roam.failed",
            "The move to {bssid} on channel {channel} failed: {detail}",
            bssid=target["bssid"],
            channel=target["channel"],
            detail=out[-120:],
        )
    if link_ch == here_ch:
        return "unknown", M(
            "radio-test.roam.stuck",
            "The link did not move off channel {channel}. The test asked for {bssid} on channel {target}.",
            channel=here_ch,
            bssid=target["bssid"],
            target=target["channel"],
        )
    if after is None:
        from irate_box.root import radio as radio_mod

        return "doesn't", sentences(
            M(
                "radio-test.roam.down",
                "The hotspot went down when the link moved to channel {channel}.",
                channel=link_ch,
            ),
            radio_mod.restore_hotspot() or None,
        )
    put_back = None if back else M("radio-test.roam.not-back", "The box could not bring the link back up afterwards.")
    if after == link_ch and after != before:
        return "follows", sentences(
            M(
                "radio-test.roam.follows",
                "The hotspot moved with the link from channel {before} to {after}. The move took {took}.",
                before=before,
                after=after,
                took=duration(took),
            ),
            put_back,
        )
    if after == before:
        return "stays", sentences(
            M(
                "radio-test.roam.stays",
                "The hotspot stayed on channel {before} while the link moved to {link}. This radio runs both at once.",
                before=before,
                link=link_ch,
            ),
            put_back,
        )
    return "unknown", sentences(
        M(
            "radio-test.roam.other",
            "The hotspot went from channel {before} to {after} and the link went to {link}.",
            before=before,
            after=after,
            link=link_ch,
        ),
        put_back,
    )


def radio_reset(radio, ap, sleep=time.sleep):
    from irate_box.root import radio as radio_mod

    usb = radio.get("usb") or {}
    t0 = time.time()
    said = radio_mod.reset(radio["iface"], usb.get("port"), radio.get("driver"), usb.get("product"))
    up = _link_up(radio["iface"], 60, sleep)
    took = round(time.time() - t0)
    hotspot = (not ap) or ap in _channels()
    answer = "works" if up and hotspot else "doesn't"
    link = (
        M("radio-test.reset.link-back", "The link was back {took} after the reset began.", took=duration(took))
        if up
        else M(
            "radio-test.reset.link-not-back", "The link was not back {took} after the reset began.", took=duration(took)
        )
    )
    if not ap:
        return answer, sentences(said, link)
    if hotspot:
        return answer, sentences(said, link, M("radio-test.reset.hotspot-back", "The hotspot was back."))
    return answer, sentences(said, link, M("radio-test.reset.hotspot-not-back", "The hotspot was not back."))


def driver_reset(radio, ap, sleep=time.sleep):
    params = SYS / "module" / radio["driver"].replace("-", "_") / "parameters"
    try:
        before = int((params / "recoveries").read_text().strip())
    except (OSError, ValueError):
        before = None
    (params / "fake_cmd_timeout").write_text("1")
    t0 = time.time()
    run("iw", "dev", radio["iface"], "scan", "trigger", timeout=15)
    sleep(3)
    up = _link_up(radio["iface"], 90, sleep)
    took = round(time.time() - t0)
    try:
        after = int((params / "recoveries").read_text().strip())
    except (OSError, ValueError):
        after = None
    reset = before is not None and after is not None and after > before
    if ap and ap not in _channels():
        from irate_box.root import radio as radio_mod

        radio_mod.restore_hotspot()
    if not reset and before is not None:
        return "doesn't", M(
            "radio-test.driver.no-reset", "The driver did not reset its device. Its count of resets did not change."
        )
    if up:
        return "works", M(
            "radio-test.driver.back",
            "The driver reset its device. The link was back {took} later.",
            took=duration(took),
        )
    return "doesn't", M(
        "radio-test.driver.not-back",
        "The driver reset its device. The link was not back {took} later.",
        took=duration(took),
    )


def csa(inv, radio, ap, sleep=time.sleep):
    """A channel switch in place: to another allowed channel and back, with the link still joined and no
    command timeout in the driver's log. The hotspot is put back by starting it again if a switch fails."""
    from irate_box.hub import apmode, hotspot
    from irate_box.root import ap as ap_mod

    rec = ap_mod._load(ap_mod.RECORD, {})
    plan = rec.get("plan") or {}
    if not rec.get("up") or not plan.get("channel"):
        return "unknown", M("radio-test.csa.off", "The hotspot is not running, so there is no channel to switch.")
    iface = ap_mod.ap_iface(plan)
    now_ch = ap_mod.channels_now(_proc_run).get(iface)
    if now_ch is None:
        return "unknown", M(
            "radio-test.csa.unread", "The hotspot's channel cannot be read, so there is nothing to switch."
        )
    chans = apmode.allowed(radio, inv.get("country"))
    other = next((c for c in chans if c["channel"] != now_ch), None)
    if other is None:
        return "unknown", M("radio-test.csa.no-channel", "There is no other channel this radio can use.")
    started = _link_up(radio["iface"], 1, sleep)
    target = {"channel": other["channel"], "freq": other["freq"]}
    ok, why = ap_mod.channel_switch(_proc_run, plan, target)
    back = {"channel": now_ch, "freq": plan.get("freq") or 0}
    if ok:
        ok, why = ap_mod.channel_switch(_proc_run, plan, back)
    if not ok:
        try:
            ap_mod.start(_proc_run, inv, hotspot.load())
        except RuntimeError:
            pass
        return "doesn't", M(
            "radio-test.csa.failed",
            "The hotspot did not switch channel in place: {why}. The box started it again.",
            why=why,
        )
    still = _link_up(radio["iface"], 1, sleep)
    code, log = run("journalctl", "-k", "--since", "-30s", timeout=10)
    timed_out = code == 0 and "cmd timed-out" in log
    if not started or not still:
        return "doesn't", M("radio-test.csa.link", "The WiFi link did not stay joined during the channel switch.")
    if timed_out:
        return "doesn't", M("radio-test.csa.timeout", "The driver logged a command timeout during the channel switch.")
    return "works", M(
        "radio-test.csa.works",
        "The hotspot switched to channel {channel} and back, and the link stayed joined.",
        channel=other["channel"],
    )


def _proc_run(*cmd, timeout=60):
    """root/ap.py's runner shape (returncode, stdout, stderr) over this module's run()."""
    code, out = run(*cmd, timeout=timeout)
    return SimpleNamespace(returncode=code, stdout=out, stderr="")


def own_channel(inv, radio, sleep=time.sleep):
    """A channel of its own beside the link, tried for a few seconds: today's try_own_channel, as a radio test."""
    from irate_box.hub import hotspot
    from irate_box.root import ap as ap_mod

    try:
        worked, plan = ap_mod.try_own_channel(_proc_run, inv, hotspot.load(), sleep=sleep)
    except RuntimeError as exc:
        return "doesn't", M(
            "radio-test.own.failed", "The hotspot could not start on a channel of its own: {why}", why=str(exc)[-160:]
        )
    if worked is None:
        return "unknown", M(
            "radio-test.own.nothing",
            "There is no other channel this radio can run the hotspot on, so there is nothing to try.",
        )
    if worked:
        return "works", M("radio-test.own.works", "The hotspot held a channel of its own beside the link.")
    return "doesn't", M("radio-test.own.doesnt", "The hotspot did not hold a channel of its own beside the link.")


def experiment(inv, iface, exp, sleep=time.sleep):
    radio, ap = check_ready(inv, iface, exp)
    if exp == "own-channel":
        answer, detail = own_channel(inv, radio, sleep)
    elif exp == "csa":
        answer, detail = csa(inv, radio, ap, sleep)
    else:
        answer, detail = {"follows-roam": follows_roam, "radio-reset": radio_reset, "driver-reset": driver_reset}[exp](
            radio, ap, sleep
        )
    _keep(radio, exp, answer, detail)
    return sentences(QUESTION_MSG[exp], detail)
