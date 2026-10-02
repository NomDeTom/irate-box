# Irate-Box

A self-contained offline hub for a small board. It runs a WiFi access point; anything that joins gets a captive portal that opens a landing page, and from that page you reach a whiteboard, a diagram editor, an offline encyclopedia, a serial console and a shell — with no internet involved at any point. Spiritually a [PirateBox](https://github.com/PirateBox-Dev) successor (hence the name), but Pi-first, HTTPS-free by design, and built from maintained, packaged components rather than a 2013 shell-script pile.

**Status: installs and runs on a real board; no access point yet.** `install.sh` puts the whole hub on an Armbian or mPWRD-OS board in one command. It has been tested end to end on a Luckfox Lyra Zero W (mPWRD-OS 26.05, armv7l, 512 MB). With everything running, the board still has about 320 MB of memory free. The hub is served on whatever network the board is already on: the access point, dnsmasq and the captive portal are not installed yet, and neither are the `.deb` packages. Target hardware is a Raspberry Pi Zero W or a similar 512 MB board, and that ceiling drives every design decision: static assets plus a handful of small native daemons.

| | State |
|---|---|
| Landing page, shoutbox, board, blob store, `/status` | working |
| Excalidraw, Mermaid (static builds of the two forks) | working |
| Kiwix offline library (`--zim`), library rebuilt from the ZIM folder on every install | working |
| Calculators, in three lists: RF & LoRa, general, electronics | working |
| Meshtastic log analyser (serial-terminal build) | working with saved logs; live serial needs HTTPS, which the hub does not have |
| SilverBullet notes, Syncthing (`--with-notes`, `--with-sync`) | working |
| ttyd terminal (`--with-term`) | working, off by default |
| MQTT broker for Meshtastic (`--with-mqtt`) | broker working; decoder, traffic page and map not started |
| Tailscale remote-access switch on `/admin` (only if Tailscale is already installed) | working (installed 2026-10-01) |
| Librarian: keeps ZIM books current from GitHub releases, Actions artifacts (token or nightly.link) or a URL; settings on `/admin` | working (installed 2026-10-01; both docs books updated through it) |
| `/admin`: box and services (start, stop, start at boot), moderation (shoutbox, board), saved work (gallery, store quota and expiry), admin password, version, state backup (Syncthing's keys only on request, flagged) | working (installed 2026-10-01) |
| Admin password chosen in the browser on first use (`/admin/` until set); `hub_control.py reset-password` from the console | written and tested off the box; not yet installed |
| Quick help (`/help.html`, 🛟 in page headers, the hub bar and standalone apps) | working (installed 2026-10-01) |
| Updates from `/admin`: fetch into a root-owned cache, then rerun `install.sh` with the options recorded at install time | working (installed 2026-10-01) |
| Updates as three steps, **Check**, **Fetch** (verify and download) and **Install**, each with a progress bar (step n of m, a download's bytes, `install.sh`'s own steps); a bar for the librarian's downloads; button errors shown under the button, "already running" cleared when the librarian is free; no terminal codes in the install log | written and tested off the box; not yet installed |
| `/admin` as a sidebar of pages (Box, Library, Hub content, System), one pane at a time, with status badges; every answer under the control that asked; apps and books as **Check**, **Fetch**, **Update** (a fetched book or bundle waits beside the one in use), **Keep current** on each untracked app | written and tested off the box; not yet installed |
| Security page on `/admin` (System → Security): every listener a guest can reach, named by its service; SSH root and password login; security updates waiting; what plain HTTP and one shared origin mean. Fixes only on the owner's say-so (Cockpit loopback or off, LLMNR off, SSH drop-in, a unit switched off, security updates), each undoable and undone by `uninstall.sh` | written and tested in a systemd Debian trixie container; not yet installed |
| Port 80 taken by another service (nginx, Apache…): flagged by `install.sh` and worked around — the hub on the first free of :8080, :8088, :8888 (recorded, so updates keep it), the other service left alone; `--take-port-80` stops it instead, recorded so the Security page or `uninstall.sh` gives the port back. `install.sh` ends by repeating what it flagged and listing what the Security page found | tested in a systemd Debian trixie container with nginx on :80 (install, take-over, refused undo, uninstall) |
| nginx as the web server in front, Caddy as the fallback (`--web nginx\|caddy`, recorded): the Caddyfile's routes ported to `irate-box.nginx`, in `/etc/nginx/conf.d/irate-box.conf`; the admin login as SHA-512-crypt (nginx checks it on every request: 38 ms on the Lyra, against ~5.8 s for Caddy's bcrypt); an owner's nginx used as installed, the hub on its own port if theirs serves :80; a box set up with Caddy stays on it until `--web nginx`, and switching either way turns irate-box's copy of the other off; Caddy if nginx cannot be installed; the hub's listen queue raised from 5 to 64 | tested in systemd Debian trixie containers (fresh install, first-use claim and password change, Caddy and back, a box installed with Caddy upgraded (stays) then switched, an owner's nginx serving :80 (hub on :8080, their files byte-for-byte after uninstall), Caddy when nginx has no candidate, uninstall and purge); on the Lyra 2026-10-02: the update (stays on Caddy) and `--web nginx`, all checks passing |
| Git servers, part of every install: `/git/` (public: browse with cgit and clone for everyone, push with the admin login unless guest push is on) and `/git-private/` (everything behind the login); `git http-backend` and cgit run on demand by the hub's own fcgiwrap as the hub user (nothing resident until used), pushes up to 64 MB; **Git** on `/admin` creates, describes and deletes repositories, shows sizes and last commits, and switches guest push; a Git tile on the hub. Mirrors of the forks and updates from them are not built yet | tested in a systemd Debian trixie container behind nginx and behind Caddy (clone, push with and without the login, private clone and push, guest push on and off, cgit pages and clone URLs, an 80 MB push refused, the admin page; uninstall and purge leave nothing); on the Lyra 2026-10-02 behind Caddy and nginx, all checks passing (it found the Caddy stuck-push bug, since fixed by `git-http-guard.py`) |
| Meshtastic web flasher, hub side (`/flasher/`): the fork's one-file build offered as a download with the hub's own address filled in (`flasher.py`), its device list, event list and firmware list answered by the hub, the bundle's photos and data served with CORS for a page opened from disk; installed and kept current like the other apps once the fork `NomDeTom/web-flasher` exists | tested in a systemd Debian trixie container with a local build of the fork: downloaded, opened from `file://` in Chromium, renders with the device list and photos from the hub, no CORS errors; the fork is not on GitHub yet |
| Firmware mirror for the flasher (`firmware.py`, Library → Firmware on `/admin`): Meshtastic's releases, two alphas and one beta (revoked ones skipped), only the ticked boards' flash files (no `.elf`), each checked against its manifest's size and MD5, in release.meshtastic.org's layout; optionally the newest release's PlatformIO build cache (the `native` subset or the whole zip) for builds with no internet (`CI_PIO_DEPS` in builds on push) | tested in a systemd Debian trixie container: two boards over three releases (31 MB), the list matching Meshtastic's own, served to the flasher |
| Builds on push (experimental): a push to a **private** repository whose commit has a `.irate-ci.sh` at its top queues a build (`git-hooks/post-receive`); `irate-box-ci.service` runs it as `hubci`, a user of its own, at idle priority with memory caps: a fresh clone, `bash .irate-ci.sh` with `CI_*` variables, a persistent `HOME` for tool caches, up to 6 hours; log and artifacts kept (5 runs a repository), listed under Builds on `/admin`'s Git page. Public repositories never build | tested in a systemd Debian trixie container (queued on push, passed and failed runs, artifact and log downloads behind the login, caches kept between builds, a public push builds nothing); on the Lyra 2026-10-02 a test build passed, and meshtasticd (`native`) built through it in 2 h 46 min |
| An owner's own Caddy (with `--web caddy`, or when the box runs Caddy and no nginx): used as installed (no Caddy repository added, no upgrade); their Caddyfile stays theirs — the hub's site goes in `/etc/caddy/irate-box.caddy`, pulled in by one `import` line, on its own port if theirs already serves :80; password changes go to the hub's file and the whole config is validated; `uninstall.sh` takes out the file and the line | tested in a systemd Debian trixie container with Debian's Caddy 2.6 serving :80 and :8090 (their sites kept, password claim, uninstall leaves their Caddyfile byte-for-byte as it was) |
| Add-ons on `/admin` (Box → Add-ons): notes, terminal, Syncthing, MQTT and live collaboration added or removed after install, each with what it exposes said before it is added; the root helper reruns `install.sh` from a copy of the installed code with the option added or `--remove NAME`, with the update's progress bar; removing keeps the add-on's data | tested in a systemd Debian trixie container through the admin API (notes added, MQTT removed, the core and the other add-ons unaffected) |
| Setup steps: the first-use password page is step 1 of 5, then `/admin` opens on the rest — how guests reach the box (warned if it is not on port 80), what it exposes, add-ons, books — each linking to its page, skippable, until **Finish setup**; Overview brings them back | written and tested off the box; not yet installed |
| Shoutbox and board cleared at boot, if chosen (Moderation → Starting fresh): only a real boot (the kernel's `boot_id` changing) clears them, not the hub restarting for an update | written and tested off the box; not yet installed |
| File drop (`/drop.html`, its own tile): guests leave files for each other, uploads streamed to disk in 64 kB chunks with a bar per file; 25 MB a file (the web server refuses more before reading it), its own 256 MB budget with oldest-first eviction, 24 hours of powered-on time; served only as attachments (`octet-stream`, `nosniff`, a sandboxing CSP); listed and deleted under Moderation, budget and lifetime under Saved work | written and tested off the box; not yet installed |
| Books from and to a USB stick (Books → From or to a USB stick): scan (each stick mounted read-only, `nosuid,nodev,noexec`, only for the scan), import a book (free space checked against the librarian's margin, refused over an existing name, the library rebuilt), export a book to the stick's `irate-box/` | tested in a systemd Debian trixie container with a FAT image on a loop device; not yet with a real stick |
| Serial terminal as a download for live USB (Meshtastic and RF & LoRa lists): the fork's bundle also builds `serial-terminal.html` (one file, ~740 kB, with its `.sha256`), served as an attachment; opened from disk it is a secure context, so Web Serial works | built locally and opened from `file://` in headless Chromium (renders, Web Serial native); the fork's CI build and a real radio not yet checked |
| Update verification (fast-forward, scripts parse, Python compiles, the new nginx site passes `nginx -t` (or the Caddyfile validates, on Caddy), release downloads prefetched and checksummed) before Install is offered; the update doctor | written and tested off the box; not yet installed |
| Apps from the forks' Actions builds: `install.sh --apps-from-actions`, then kept current from `/admin` (Apps) | working (installed 2026-10-01) |
| App manifests (`apps.d/`): every tile, the list pages, `/status`, installs and update sources from one file per app; the calculators tracked from git, and new ones listed from the site's index | written and tested off the box; not yet installed |
| Excalidraw live collaboration (`--with-collab`), hub gallery saves from Excalidraw | working (installed 2026-10-01) |
| Service dashboard, memory and disk tile | working |
| Network inventory (`netinv.py`): each radio's driver and bus, who runs it (NetworkManager, iwd, connman, wpa_supplicant under ifupdown, networkd or init scripts; netplan above them), whether it can run a hotspot beside its client link and on how many channels, the client profile's reconnect settings, and what is in the way (mPWRD-OS's wifisync, another hotspot profile, USB autosuspend, no WiFi country, NetworkManager's give-up-after-3-handshakes setting). Run at install (its verdicts in the closing summary), from `/admin` (System → Network, "Look again", for all devices or one), or on any board before installing (`python3 netinv.py [--iface wlan1]`) | tested on the Lyra 2026-10-02 (read-only, every finding matching a hand check) and in a systemd Debian trixie container (wired, no radio) |
| Uplink watchdog (`uplink.py`, `irate-box-uplink.service`, `--uplink`): checks the gateway (ping, then ARP), and repairs a lost link as eagerly as chosen — **eagerness** off, patient (default), standard, persistent, stubborn (reconnect with back-off → restart the network service → reset the radio → reboot, capped per day) and **forgiveness** tolerant, normal (default), strict (failed checks and grace before acting; a link that keeps dropping is noted, locked to the strongest access point, or repaired); every value overridable; no radio reset or reboot while guests are on the hotspot (unless stubborn), while a build or update runs, or soon after boot; a hold for owners working on the network; "Keep retrying" sets the owner's WiFi profile's `autoconnect-retries` and `auth-retries` to 0 by consent, undone here or by `uninstall.sh` | decisions tested against a simulated clock (every level, flapping, guards, overrides); on the Lyra 2026-10-02 as a dry run (no false alarms over several minutes at 10 s checks); install, settings, hold, refusals and uninstall in a systemd Debian trixie container. Repairs not yet tried on a real outage; "Keep retrying" not yet tried on a real profile |
| Box doctor (`health.py`, `/admin` → System → Health, `sudo python3 /opt/irate-box/health.py`): the last install (finished? stopped at which step and command?), the root helper, every unit and add-on (failed and why, with journal lines), Kiwix in layers (package, unit, each ZIM's header and readability, the library against the books on disk, a crash loop; the same check, `zimcheck.py`, guards every download, rollback and USB import, and `/status` says why `/wiki/` is down), the watchdog, the web server's config, the hub, space; each finding with what to do by hand, the safe repairs as buttons (start a unit, rebuild the library, set a book aside, stop Kiwix, run the installer again). `install.sh` fails gracefully: output in `/var/log/irate-box/install.log`, progress in `install-state.json`, a unit that will not start reported instead of aborting, and an unexpected stop explained (step, line, command, what to run next). A banner on every `/admin` pane when the root helper stops answering, with the commands to bring it back | tested in a systemd Debian trixie container (unreadable and truncated ZIMs, a refused and an interrupted install, a stopped root helper, the installer rerun from the page) and on the Lyra 2026-10-02 (installed, all healthy, the page's scan through the root helper) |
| Clock: the doctor reports what keeps the time, what happened at this boot (the Lyra, with no RTC, booted 45.7 h behind after two days off) and a clock that is certainly behind, and offers "Set the clock from this browser" for an offline box. A clock module on I2C (`rtc.py`, `--rtc auto`): found by probing (DS3231, DS1307, MCP7940, RV-8803, RX8130, PCF8563/HYM8563; PCF8523, PCF85063, RV-3028 with a kernel driver), driven by the kernel's driver where it has one (declared through sysfs) or by irate-box's own register driver where it has not; no device-tree changes; the clock set from it at boot and saved to it only when trusted; charging never switched on | simulated chips (37 checks, register maps from the kernel's drivers); the doctor's route and an install in a systemd Debian trixie container; not yet on a real module |
| Access point, dnsmasq, captive portal | not in the installer yet |
| `.deb` packages, manifest-driven tiles | not started |

## Install on a board

On Armbian, or mPWRD-OS, which is an Armbian build:

```sh
git clone https://github.com/NomDeTom/irate-box && cd irate-box
sudo ./install.sh --with-notes --with-sync --with-mqtt --zim wikipedia_en_top_mini.zim
```

The script sets up nginx on `:80` (`--web caddy` for Caddy, the fallback) and the hub on loopback. The code goes in `/opt/irate-box`, state in `/var/lib/hub` and config in `/etc/hub`. It prints the admin password is chosen on first use: open `http://<the box>/admin/` straight after installing (until then, anyone on the network could), or pass `--admin-password`. Running it again upgrades in place. `--apps-from-actions` fetches the Excalidraw, Mermaid and serial-terminal builds from the forks' Actions artifacts; `--apps DIR` takes your own desktop builds. Nothing is compiled on the board. **[BUILDING.md](BUILDING.md)** §4b lists every option.

## Try it without a board

Needs only Python 3.

```sh
git clone <this repo> irate-box && cd irate-box
python3 server.py
```

Open <http://localhost:8000>. That gives you the landing page, the **shoutbox** and the **board** — the parts that need nothing else. The service tiles link to paths that only exist behind the web server, so they show as "not running" until you add one. For a dev checkout Caddy is the quick one, since its Caddyfile runs as it is (the board uses nginx with the same routes, from `irate-box.nginx`):

```sh
caddy run --config Caddyfile        # needs :80 — sudo, setcap, or edit the port
```

Then it is <http://localhost/>. `/mermaid/`, `/draw/` and `/serial/` go live once their builds exist, and `/wiki/`, `/notes/` and `/term/` once their backends listen on the ports the routes name. **[BUILDING.md](BUILDING.md)** walks through all of it, clone to running.

## What is here

| File | Role |
|---|---|
| `install.sh` | One-command install on Armbian / mPWRD-OS: packages, the `hub` user, the web server's config (nginx, or Caddy), systemd units, and each optional service. Rerunnable. |
| `server.py` | The hub: landing page, shoutbox, board, blob store, captive-portal target, and `/status`, which reports each service as running, not running or not installed, plus memory and disk. Stdlib only, threaded. |
| `board.py` | Threaded message board: 50 threads, 200 posts each, threads fade seven days of powered-on time after their last reply. |
| `store.py` | Blob store. Reimplements `excalidraw-storage-backend`'s `/api/v2` over a directory (no NestJS, no Redis) and adds `/api/saves`, a named-save gallery with client-rendered thumbnails. Runs standalone on `:8090` for testing. |
| `tailscale-apply.sh` | Run as root by a systemd path unit: starts or stops `tailscaled` to match `$STATE/tailscale.want`, which the hub writes from the `/admin` switch (off, on, or on for N hours). The hub itself never gets root. |
| `apps.d/`, `manifests.py` | Every tile on the hub comes from here: one manifest per app or tile says its home-page tile (or one of the hub's live tiles), the list page it opens (Meshtastic, RF & LoRa, Calculators, Electronics) and the lines it adds to list pages, how `/status` sees it, where it installs, and where updates come from (a fork's Actions bundle, or a git repository plus an adapt script). `server.py` renders the home page, the list pages and `/status` from them; the librarian and the root helper install from them. A calculator added to the site's index appears on its group's list page by itself. |
| `librarian.py` | Keeps ZIM books in the Kiwix library current. Each source is GitHub releases, a workflow's Actions artifacts (with a token, or through nightly.link without one), or a URL. A new version is checked and swapped in under the same file name, so `/wiki/content/<name>/` links never change, and kiwix-serve picks it up without a restart. Old versions are archived or deleted per policy, with rollback. Run hourly by `irate-box-librarian.timer` (each source only when due) and on demand from `/admin`. Stdlib only. |
| `hub_control.py` | Root helper for `/admin`, run by `irate-box-control.path` when the hub queues a request in `$STATE/control/requests/`. Starts, stops and enables only the services on its allow-list (the web server and the hub: restart only), and changes the admin password everywhere it is used (nginx's login file or Caddy's `basic_auth`, ttyd, Syncthing, `/etc/hub/admin-password`). The hub itself never gets root. |
| `gitrepos.py` | The git repositories behind `/git/` and `/git-private/`: what `/admin`'s Git page lists, creates, describes and deletes, and the guest-push switch. Runs in the hub, as the hub user, who owns the repositories. Stdlib only. |
| `git-http-guard.py` | `git http-backend` behind Caddy: takes a push's body in full (to a file beside the repositories) before git runs, as nginx does. Caddy streams bodies, and a push it cut off at the 64 MB cap left `git http-backend` spinning at 100% CPU with the pack held in memory. Stdlib only. |
| `flasher.py` | The web flasher's hub side: `/flasher/flasher.html` as a download with this hub's address filled in, and the small API it reads (device list, event list, firmware list), all with CORS, since the page runs from the guest's disk. Stdlib only. |
| `firmware.py` | The firmware mirror for the flasher, run by the librarian: chosen boards of the kept Meshtastic releases, verified against their manifests, `index.json` for the flasher, and the optional build cache. Settings on `/admin` (Library → Firmware). Stdlib only. |
| `ci.py`, `git-hooks/` | Builds on push for the private repositories: `post-receive` (the hooks path of every private repository) queues a build when the commit has a `.irate-ci.sh`; `ci.py run`, as `hubci` from `irate-box-ci.service`, builds each in a fresh clone and keeps its log and artifacts for `/admin`. Stdlib only. |
| `rtc.py` | A clock module on I2C: `find` (probe, identify), `setup CHIP BUS ADDR`, `boot` and `save` (from `irate-box-rtc.service` and an hourly timer), `status`, `remove`, `auto` (install.sh). The kernel's driver when there is one, otherwise its own register driver over `/dev/i2c-N`. `HUB_RTC_FAKE` stands in for the buses in tests. Stdlib only. |
| `zimcheck.py` | Is a file a whole book Kiwix can read? The ZIM header (signature, format version, length against the header: a truncated download shows here) and then `kiwix-manage` on a scratch library. The librarian (downloads, rollbacks), the USB import (on the stick, then the copy) and the doctor all ask it before a book is put in place. Stdlib only. |
| `health.py` | The box doctor, root: `scan()` (findings with `fix` text and `actions`) and `fix(choice)` for the safe repairs; `summary` is install.sh's closing lines when an install had problems. Run by `hub_control.py` for `/admin` ("health-scan", "health-fix"; "rerun-install" is the helper's own), and from a shell when `/admin` cannot reach the helper. Stdlib only. |
| `netinv.py` | What the box has for networking, read-only: radios from `iw` (modes, interface combinations, bands), driver and bus from sysfs, the stack that runs each interface, NetworkManager's profiles, netplan, and the hazards and verdicts above. `--write` puts it in `control/netinv.json` for `/admin`; `summary` is install.sh's closing lines. Stdlib only, and no other irate-box module, so it runs on a board on its own. |
| `uplink.py` | The uplink watchdog, root, from `irate-box-uplink.service`: the levels (`presets` prints them as numbers), a decision core (`Watch`) kept apart from looking and acting so it can be driven by a test clock, and the backends for NetworkManager, wpa_supplicant, ifupdown, networkd and dhcpcd. Settings in `/etc/hub/uplink.json`, its report in `control/uplink.json`, the by-consent profile change recorded in `/etc/hub/uplink-changes.json`. `check` and `run --dry-run` only look. Stdlib only. |
| `security.py` | The Security page's root side, run by `hub_control.py`: a scan of what the box exposes (listeners from `ss` with their systemd units, `sshd -T`, keys on the box, `apt-get -s upgrade`'s security packages) and the fixes the owner can choose. Each fix is a drop-in or a unit switched off, recorded in `/etc/hub/security-changes.json` so it can be undone, here or by `uninstall.sh`. Stdlib only. |
| `secdoctor.py` | The security doctor: a passive, read-only audit of the system underneath (the checkable half of the 2026-10-02 security review, each line tagged F1..F30/S1..S14). Ten steps: the notes add-on's shell and login, the front's (nginx or Caddy) keep-alive, body limit, forwarded address and logins, links and root-written files in hub-owned folders, installed-code ownership and allow-lists, unit sandboxing and the build unit's reach to loopback, secrets on command lines and `/proc`, secrets at rest, git mimetypes and public-repo protections, sudo rules and accounts, kernel protections. Changes nothing but its own report (`control/security-audit.json`, written without following links). `hub_control.py` runs it for `/admin` → Security → Security doctor ("security-audit"); `sudo python3 /opt/irate-box/secdoctor.py [summary\|json]` from a shell. Stdlib only. |
| `usbstick.py` | Books to and from a USB stick, run as root by `hub_control.py`: finds removable or USB partitions (`lsblk`, with `blkid` where udev has not said), mounts each only for a scan or a copy, imports into the ZIM folder as the hub user and has the librarian rebuild the library, exports to `irate-box/` on the stick. Stdlib only. |
| `hubclock.py` | The clock. The boards have no RTC, so everything ages by *cumulative powered-on seconds*, never the wall clock. Messages posted an hour before the box is switched off are still an hour old when it comes back. |
| `static/` | The pages. No framework, no build step, no network fetches. The home page has the shoutbox and board as two tabs, a row of app tiles and a row for the box itself: users online, a QR code to join, memory and disk, and services. There are list pages for the tools, Meshtastic and the service dashboard. `app.html` is the hub bar: every app opens under it, it slides away on scroll-down, and its theme picker sets light or dark for every app. |
| `irate-box.nginx` | The front, for nginx: one origin, path-routed. `/` to the hub; static app builds at `/draw/`, `/mermaid/`, `/tools/` and `/serial/`; `/wiki/`, `/notes/`, `/mqtt`, `/socket.io/`, `/sync/` and `/term/` passed to loopback ports. Plain HTTP only; see below. `install.sh` fills in the paths, port and login file. |
| `Caddyfile` | The same routes for Caddy, the fallback. A route changed in one is changed in the other. |
| `irate-box.service` | A hand-install systemd unit for the hub (paths under `/home/pi`). `install.sh` writes its own. |
| `dnsmasq-hotspot.conf` | DHCP + the `address=/#/192.168.4.1` hijack that makes every name resolve to the box. |

## How it hangs together

```
phone / laptop ──▶ nginx :80 ─┬─ /              → server.py :8000  (pages, shoutbox, board, /api/*, /status)
                              ├─ /app.html      → the hub bar; every app opens inside it
                              ├─ /draw/*  /mermaid/*  /tools/*  /serial/*   → static builds on disk
                              ├─ /wiki/*        → kiwix-serve :8081
                              ├─ /notes/*       → SilverBullet :3000         (add-on)
                              ├─ /mqtt          → mosquitto WebSockets :9001 (add-on)
                              ├─ /sync/*        → Syncthing GUI :8384        (add-on, admin login)
                              ├─ /git/*  /git-private/*  → git http-backend, cgit (fcgiwrap; private: admin login)
                              ├─ /admin/*       → server.py                  (admin login)
                              └─ /term/*        → ttyd :7681                 (admin login, off by default)

Meshtastic node / phone app ──▶ mosquitto :1883  (add-on; raw MQTT, not through the web server)
```

Everything a guest's browser touches is one origin: no ports to type, no mDNS to fail on Android, and a captive portal that can hand out a working URL. Services bind to loopback, and the web server (nginx, or Caddy) is the only listener on the network with two exceptions, both for things that are not browsers. Syncthing's sync ports are one; mosquitto's `:1883` for Meshtastic nodes is the other.

**Why plain HTTP.** Captive-portal probes are HTTP; intercepting HTTPS produces a certificate error instead of a sign-in sheet. So `:80` is always up and always plain, there is never a blanket HTTP→HTTPS redirect, and never HSTS — once sent, a browser refuses plain HTTP for that host, which is exactly the offline mode. It tests fine at home and fails in a field six weeks later.

**Captive portal.** dnsmasq resolves every name to the box; the OS's connectivity probe (`captive.apple.com`, `connectivitycheck.gstatic.com`, …) hits `server.py`, gets a 302 to the hub, and the phone pops its sign-in sheet on the landing page.

## Configuration

All by environment variable; the unit file sets the Pi values.

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8000` | hub listen port |
| `HUB_BIND` | `0.0.0.0` | `127.0.0.1` behind the web server |
| `HUB_WEB_SERVER` | `nginx` | the web server in front, `nginx` or `caddy`: names it on the service dashboard and decides which one `/admin` may restart |
| `HUB_FLASHER_ROOT`, `HUB_FIRMWARE_ROOT` | `/usr/share/hub/apps/flasher`, `/var/lib/hub/firmware` | the web flasher's bundle, and the firmware the librarian keeps for it |
| `HUB_UNCLAIMED_FILE` | `/etc/$HUB_WEB_SERVER/irate-box-unclaimed` | present while no admin password has been chosen; the web server and the hub then offer `/admin/` as the set-the-password page |
| `HUB_URL` | `/` | where captive probes are redirected; the Pi sets `http://192.168.4.1/` so the sign-in sheet shows a typeable address |
| `HUB_STATE_DIR` | repo dir | where `messages.json`, `board.json`, `clock.json` and `store/` live |
| `HUB_STORE_MAX_BODY` | 50 MB | largest single blob |
| `HUB_STORE_MAX_TOTAL` | 64 MB | store quota; oldest evicted first |
| `HUB_STORE_SAVE_TTL` | `0` (never) | gallery-save expiry, in clock ticks |
| `HUB_DRAW_ROOT`, `HUB_MERMAID_ROOT`, `HUB_TOOLS_ROOT`, `HUB_SERIAL_ROOT` | unset | where each static app lives. The web server serves from these, and `/status` reports an app whose directory is missing as not installed. Unset (a dev checkout) counts as installed. |

Shoutbox messages last 24 hours of powered-on time, capped at 200. The `/status` endpoint reports whether the web server is in front, the state of each service, and memory and disk. The pages grey out anything that is down.

## Not here

The plan — hardware notes, component roles, `.deb` packaging, lightweighting tiers, phasing — lives in a separate notes vault, not in this repo. The related repositories:

- [excalidraw-stack](https://github.com/nomdetom/excalidraw-stack): Excalidraw fork, with a `hub` build that stores into `store.py` and runs inside the hub bar.
- [mermaid-live-editor](https://github.com/NomDeTom/mermaid-live-editor): `hub` branch, an offline build with the Mermaid Chart promotion and external services removed.
- [serial-terminal](https://github.com/nomdetom/serial-terminal): the Meshtastic log analyser served at `/serial/`.
- [nomdetom.github.io](https://github.com/nomdetom/nomdetom.github.io): the calculators served at `/tools/`.
- [docusaurus2zim](https://github.com/NomDeTom/docusaurus2zim): turns a Docusaurus site into a ZIM for Kiwix. Its books are relative by default, so they work under the hub's `/wiki` and anywhere else.

## License

The core of this repo (`server.py`, `store.py`, `board.py`, `hubclock.py`, and the `static/` assets) is released into the public domain under the [Unlicense](LICENSE). `static/qrcode.js` is vendored third-party code (MIT, Kazuhiko Arase) and keeps its own license, noted in its header.
