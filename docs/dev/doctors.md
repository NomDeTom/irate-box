<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# The four doctors

A doctor checks one part of the box, lists what is wrong and offers repairs. The box has four. When someone says "the doctor", ask which one. Name it in full in code, comments and pages: Box doctor, Security doctor, Updates doctor or Housecall doctor.

| Doctor | Answers | Code | Run it | Writes |
| --- | --- | --- | --- | --- |
| Box doctor | Is the box healthy, and if not, why? | [health.py](../../irate_box/root/health.py) | `irate-box health`, and `health run` from its timer | `control/health.json` |
| Security doctor | Is the box set up safely? | [secdoctor.py](../../irate_box/root/secdoctor.py) | `irate-box secdoctor` | `control/security-audit.json` |
| Updates doctor | Can the box update, and why did an update fail? | `updates_doctor_findings()` in [hub_control.py](../../irate_box/root/hub_control.py) | /admin → Updates doctor | `control/doctor.json` |
| Housecall doctor | Is that card, image or this box's own files healthy? | [housecall.py](../../irate_box/root/housecall.py) | `irate-box housecall --image PATH` (also `--device`, `--root`, `--this-card`) | `control/housecall.json` |

## Box doctor: overall box health

Is everything that `install.sh` set up still there and working? It covers:

- the last install, the root helper, and every unit irate-box installs, add-ons included
- Kiwix, its books and its library
- the uplink watchdog, the network inventory and the web server's configuration
- crash watch, the space left and the clock
- WiFi and radio checks, including the drivers and the logging they depend on
- loses writes: the often-written files on the card, read past the cache at every boot, and an optional 4 KB
  test file where the card holds none (`irate-box-lostwrites.service`, `irate-box-canary.timer`)
- the onboard flash's health, where `nand-logs.sh` uses it: corrected bits, uncorrectable reads, bad blocks,
  erase counts and UBIFS data checksum errors

It is the only doctor that repairs by itself: it restarts a unit, rebuilds the Kiwix library, or runs the install again.

## Housecall doctor: a card, an image, or this box's own files

It is the examination box 190's case did by hand, on a target the owner points it at: a disk image (`--image`), a card in a reader (`--device`), this box's own files (`--root`), or the files on this box's card (`--this-card`). It reads only, and never mounts or writes the image. Each file gets a verdict: **genuine** (matches a reference), **damaged** (all 0xFF, or differs from its reference), **suspect** (all zero and no reference), or **unknown** (nothing wrong on its face; a partial 0xFF block can be genuine padding). A reference is a JSON file of path to a digest (`--reference`), the packages' MD5 sums (`--dpkg`), irate-box's own tree (`--git-tree`, from the updater's git checkout at the installed commit), or Box doctor's protected-files manifest (`--manifest`, and this box's own automatically for `--this-card`). From an image or a card the package and tree references are read from that image or card itself, through `debugfs`. The report is `control/housecall.json`.

A card checked on the Rockchip boards page has its result here too. Another irate-box can be paired once from the page (`--peer NAME`): its three doctors' summaries and its version are read over SSH, read only, and an unreachable box is a finding. A damaged file with a way back offers **Restore**: Box doctor's copy, irate-box's git tree, or its package. Each is asked first, puts the new file back through the shared restore code, and keeps the damaged one as `name.damaged`. Something damaged from outside is imaged first when there is room (`--image-first`). For a card of this box's own, `--this-card` reads only the files whose mount is the card; the Lyra's NAND-held state is the NAND's to check.

`irate-box-boxdoctor.timer` runs `health run` every 6 hours, at a randomised delay, and keeps the report where
/admin reads it. A run is quick except once a week, when it opens every book in Kiwix: `kiwix-manage` on a large
library took the Lyra's run from about 8 s to 151 s. Each check's time is in the report's `timings`, so the slow one
is named on the box. The timer, the boot check and the test file are each a setting on Recovery and monitoring;
switched off, their units and files are gone.

## Security doctor: our checks, Lynis and others

A read-only audit of how the system is set up. It changes nothing except its own report. It has several parts:

- our own checks, each named for what it looks at (`secdoctor.py`)
- the deep audit: Lynis and the CIS checks of debian-cis, run weekly or on demand (`deepaudit.py`)
- the cross-reference between those and the hotspot firewall (`secdoctor_xref.py`)
- the checks that a service's manifest asks for with a `network` block (see [adding a service](services.md))

The Security page (`security.py`) is not a doctor. It shows what a guest can reach and offers fixes. The Security doctor reports on the rest.

## Updates doctor: what an update needs

It looks at what an update needs from outside the box, and runs when a check for updates fails. It covers:

- the librarian: its sources, its access token and its checks
- add-ons and sources, one finding each: a source's error (a problem after three intervals), a source
  with no successful check, a newer release the policy would install, a held local add-on, and a
  packaged add-on's waiting packages (one `apt-get -s upgrade` read). `librarian.py` keeps `last_ok`
  and `error_since` so "failing for three intervals" can be told from "has not checked"
- the App store's holders: a failing or overdue check, a newest release with none of the held files,
  a kept file whose digest GitHub says differently now, a space limit that dropped versions
  (what the holders recorded, read from their state — no network of its own)
- toolkit updates
- the hub's update of itself
- Linux updates in general (`apt`)

It is read-only apart from `apt-get update`. Each finding says what is wrong and what to do.

## Where a new check goes

| The check is about | Put it in |
| --- | --- |
| A unit, the install, Kiwix, the uplink, a radio, a driver, the clock, disk space | Box doctor |
| A card in a reader, a card image, this box's own files in full | Housecall doctor |
| A setting that exposes the box, a file's permissions, a listener, a hardening gap | Security doctor |
| Fetching, verifying or installing an update, a source, the librarian | Updates doctor |

A check about WiFi belongs in the Box doctor, next to `check_uplink()`, even when the cause is a setting.

A service does not add its name to these doctors' code. Declare it in the manifest, as [adding a service](services.md) describes.
