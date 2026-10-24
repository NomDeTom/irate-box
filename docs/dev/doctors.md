<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# The three doctors

A doctor checks one part of the box, lists what is wrong and offers repairs. The box has three. When someone says "the doctor", ask which one. Name it in full in code, comments and pages: Box doctor, Security doctor or Updates doctor.

| Doctor | Answers | Code | Run it | Writes |
| --- | --- | --- | --- | --- |
| Box doctor | Is the box healthy, and if not, why? | [health.py](../../irate_box/root/health.py) | `irate-box health` | `control/health.json` |
| Security doctor | Is the box set up safely? | [secdoctor.py](../../irate_box/root/secdoctor.py) | `irate-box secdoctor` | `control/security-audit.json` |
| Updates doctor | Can the box update, and why did an update fail? | `updates_doctor_findings()` in [hub_control.py](../../irate_box/root/hub_control.py) | /admin → Updates doctor | `control/doctor.json` |

## Box doctor: overall box health

Is everything that `install.sh` set up still there and working? It covers:

- the last install, the root helper, and every unit irate-box installs, add-ons included
- Kiwix, its books and its library
- the uplink watchdog, the network inventory and the web server's configuration
- crash watch, the space left and the clock
- WiFi and radio checks, including the drivers and the logging they depend on

It is the only doctor that repairs: it restarts a unit, rebuilds the Kiwix library, or runs the install again.

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
- toolkit updates
- the hub's update of itself
- Linux updates in general (`apt`)

It is read-only apart from `apt-get update`. Each finding says what is wrong and what to do.

## Where a new check goes

| The check is about | Put it in |
| --- | --- |
| A unit, the install, Kiwix, the uplink, a radio, a driver, the clock, disk space | Box doctor |
| A setting that exposes the box, a file's permissions, a listener, a hardening gap | Security doctor |
| Fetching, verifying or installing an update, a source, the librarian | Updates doctor |

A check about WiFi belongs in the Box doctor, next to `check_uplink()`, even when the cause is a setting.

A service does not add its name to these doctors' code. Declare it in the manifest, as [adding a service](services.md) describes.
