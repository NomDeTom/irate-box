# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's "image underneath" step (stance review 2026-10-08 §2): what an Armbian or
mPWRD-OS image ships that the hub inherits, against a copy of the Lyra's files as they were on
2026-10-08: repository keys trusted for everything, sources with no Signed-By, a daily channel, a
vendor kernel, an access-point profile the image left in NetworkManager. Then a clean box, where
every line must be ok. python3 tests/sim_image_step.py"""
import os, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="image-step-"))
APT, NM = T / "apt", T / "nm"
(APT / "sources.list.d").mkdir(parents=True); (APT / "trusted.gpg.d").mkdir(); NM.mkdir()
os.environ.update(HUB_APT_DIR=str(APT), HUB_PROC_VERSION=str(T / "version"), HUB_NM_DIR=str(NM), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor as sd  # noqa: E402
sd._sc = lambda *a: ""  # no systemd here: bluetooth is "not running" either way
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

# As the Lyra had them.
(APT / "sources.list.d" / "home:mPWRD:OS.list").write_text("deb http://download.opensuse.org/repositories/home:/mPWRD:/OS/Debian_13/ /\n")
(APT / "sources.list.d" / "network:Meshtastic:daily.list").write_text("deb http://download.opensuse.org/repositories/network:/Meshtastic:/daily/Debian_13/ /\n")
(APT / "sources.list.d" / "tailscale.list").write_text("deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/debian trixie main\n")
(APT / "sources.list.d" / "debian.sources").write_text("Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie trixie-updates trixie-backports\nComponents: main\n"
                                                      "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: http://security.debian.org/\n"
                                                      "Suites: trixie-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n")
for k in ("debian-archive-trixie-stable.asc", "debian-ports-archive-2026.asc", "home_mPWRD_OS.gpg", "network_Meshtastic_daily.gpg"):
    (APT / "trusted.gpg.d" / k).write_text("key\n")
(T / "version").write_text("Linux version 6.1.115-vendor-rockchip (build@armbian) (arm-linux-gnueabihf-gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, "
                           "GNU ld (GNU Binutils for Ubuntu) 2.42) #1 SMP PREEMPT Wed Jul 15 20:32:36 UTC 2026\n")
(NM / "Hotspot.nmconnection").write_text("[connection]\nid=Hotspot\ntype=wifi\nautoconnect=false\ninterface-name=wlan0\n\n[wifi]\nmode=ap\nssid=armbiansetup-luckfox\n\n"
                                         "[wifi-security]\nkey-mgmt=wpa-psk\npsk=secret\n\n[ipv4]\nmethod=shared\n")
(NM / "irate-box-ap.nmconnection").write_text("# Written by irate-box (root/ap.py): the hotspot.\n[connection]\nid=irate-box-ap\ntype=wifi\n\n[wifi]\nmode=ap\nssid=Irate-Box\n")
(NM / "Open.nmconnection").write_text("[connection]\nid=Open AP\ntype=wifi\n\n[wifi]\nmode=ap\nssid=free\n\n[ipv4]\nmethod=shared\n")
(NM / "Home.nmconnection").write_text("[connection]\nid=Home\ntype=wifi\n\n[wifi]\nmode=infrastructure\nssid=home\n")

got = {f["id"]: f for f in sd.step_image({})}
trust = got["image-apt-trust"]
check("keys in trusted.gpg.d that are not Debian's, and one-line sources with no signed-by: a warning naming them",
      trust["status"] == "warn" and "home_mPWRD_OS.gpg, network_Meshtastic_daily.gpg" in trust["detail"] and "debian-archive" not in trust["detail"]
      and "home:mPWRD:OS.list" in trust["detail"] and "network:Meshtastic:daily.list" in trust["detail"]
      and "tailscale.list" not in trust["detail"] and "debian.sources" not in trust["detail"], trust["detail"])
check("a daily channel: said", got["image-apt-daily"]["status"] == "warn" and "network:Meshtastic:daily.list" in got["image-apt-daily"]["detail"]
      and "debian.sources" not in got["image-apt-daily"]["detail"], got["image-apt-daily"]["detail"])
check("a vendor kernel, with its build date", got["image-kernel"]["status"] == "warn" and "6.1.115-vendor-rockchip" in got["image-kernel"]["detail"]
      and "2026-Jul-15" in got["image-kernel"]["detail"], got["image-kernel"]["detail"])
aps = got["image-ap-profiles"]
check("the image's access-point profiles, not the hub's nor a client profile: encrypted/open, and whether they come up on their own",
      aps["status"] == "warn" and "Hotspot (encrypted)" in aps["detail"] and "Open AP (open, brought up on its own)" in aps["detail"]
      and "irate-box" not in aps["detail"] and "Home" not in aps["detail"], aps["detail"])
check("bluetooth not running here: ok", got["image-bluetooth"]["status"] == "ok")
check("the step is in the doctor's list, after accounts", [s[0] for s in sd.STEPS].index("image") == [s[0] for s in sd.STEPS].index("accounts") + 1)

# A clean box: Debian's sources with Signed-By, Debian's keys only, Debian's kernel, no stray AP.
for p in (APT / "sources.list.d").iterdir():
    if p.suffix == ".list":
        p.unlink()
for p in (APT / "trusted.gpg.d").iterdir():
    if not p.name.startswith("debian-"):
        p.unlink()
(T / "version").write_text("Linux version 6.12.41+deb13-armmp (debian-kernel@lists.debian.org) (gcc-14 (Debian 14.2.0-19) 14.2.0) #1 SMP Debian 6.12.41-1 (2026-08-10)\n")
for p in NM.iterdir():
    if p.name in ("Hotspot.nmconnection", "Open.nmconnection"):
        p.unlink()
got = {f["id"]: f for f in sd.step_image({})}
check("a clean box: every line ok", all(f["status"] == "ok" for f in got.values()) and "image-apt-daily" not in got, {k: v["status"] for k, v in got.items()})
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
