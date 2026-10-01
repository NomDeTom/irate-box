# Irate-Box

A self-contained offline hub for a small board. It runs a WiFi access point; anything that
joins gets a captive portal that opens a landing page, and from that page you reach a
whiteboard, a diagram editor, an offline encyclopedia, a serial console and a shell — with no
internet involved at any point. Spiritually a [PirateBox](https://github.com/PirateBox-Dev)
successor (the name is PirateBox with the P knocked off), but Pi-first, HTTPS-free by design,
and built from maintained, packaged components rather than a 2013 shell-script pile.

**Status: installs and runs on a real board; no access point yet.** `install.sh` puts the
whole hub on an Armbian or mPWRD-OS board in one command. It has been tested end to end on a
Luckfox Lyra Zero W (mPWRD-OS 26.05, armv7l, 512 MB). With everything running, the board
still has about 320 MB of memory free. The hub is served on whatever network the board is
already on: the access point, dnsmasq and the captive portal are not installed yet, and
neither are the `.deb` packages. Target hardware is a Raspberry Pi Zero W or a similar 512 MB
board, and that ceiling drives every design decision: static assets plus a handful of small
native daemons.

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
| Update verification (fast-forward, scripts parse, Python compiles, Caddyfile validates, release downloads prefetched and checksummed) before Install is offered; the update doctor | written and tested off the box; not yet installed |
| Apps from the forks' Actions builds: `install.sh --apps-from-actions`, then kept current from `/admin` (Apps) | written and tested off the box; not yet installed |
| Excalidraw live collaboration (`--with-collab`), hub gallery saves from Excalidraw | working (installed 2026-10-01) |
| Service dashboard, memory and disk tile | working |
| Access point, dnsmasq, captive portal | not in the installer yet |
| `.deb` packages, manifest-driven tiles | not started |

## Install on a board

On Armbian, or mPWRD-OS, which is an Armbian build:

```sh
git clone https://github.com/NomDeTom/irate-box && cd irate-box
sudo ./install.sh --with-notes --with-sync --with-mqtt --zim wikipedia_en_top_mini.zim
```

The script sets up Caddy on `:80` and the hub on loopback. The code goes in
`/opt/irate-box`, state in `/var/lib/hub` and config in `/etc/hub`. It prints the admin
password is chosen on first use: open `http://<the box>/admin/` straight after installing
(until then, anyone on the network could), or pass `--admin-password`. Running it again
upgrades in place. `--apps-from-actions` fetches the Excalidraw, Mermaid and serial-terminal
builds from the forks' Actions artifacts; `--apps DIR` takes your own desktop builds. Nothing
is compiled on the board. **[BUILDING.md](BUILDING.md)** §4b lists every option.

## Try it without a board

Needs only Python 3.

```sh
git clone <this repo> irate-box && cd irate-box
python3 server.py
```

Open <http://localhost:8000>. That gives you the landing page, the **shoutbox** and the
**board** — the parts that need nothing else. The service tiles link to paths that only exist
behind Caddy, so they show as "not running" until you add it:

```sh
caddy run --config Caddyfile        # needs :80 — sudo, setcap, or edit the port
```

Then it is <http://localhost/>. `/mermaid/`, `/draw/` and `/serial/` go live once their builds
exist, and `/wiki/`, `/notes/` and `/term/` once their backends listen on the ports the
Caddyfile names. **[BUILDING.md](BUILDING.md)** walks through all of it, clone to running.

## What is here

| File | Role |
|---|---|
| `install.sh` | One-command install on Armbian / mPWRD-OS: packages, the `hub` user, Caddy config, systemd units, and each optional service. Rerunnable. |
| `server.py` | The hub: landing page, shoutbox, board, blob store, captive-portal target, and `/status`, which reports each service as running, not running or not installed, plus memory and disk. Stdlib only, threaded. |
| `board.py` | Threaded message board: 50 threads, 200 posts each, threads fade seven days of powered-on time after their last reply. |
| `store.py` | Blob store. Reimplements `excalidraw-storage-backend`'s `/api/v2` over a directory (no NestJS, no Redis) and adds `/api/saves`, a named-save gallery with client-rendered thumbnails. Runs standalone on `:8090` for testing. |
| `tailscale-apply.sh` | Run as root by a systemd path unit: starts or stops `tailscaled` to match `$STATE/tailscale.want`, which the hub writes from the `/admin` switch (off, on, or on for N hours). The hub itself never gets root. |
| `librarian.py` | Keeps ZIM books in the Kiwix library current. Each source is GitHub releases, a workflow's Actions artifacts (with a token, or through nightly.link without one), or a URL. A new version is checked and swapped in under the same file name, so `/wiki/content/<name>/` links never change, and kiwix-serve picks it up without a restart. Old versions are archived or deleted per policy, with rollback. Run hourly by `irate-box-librarian.timer` (each source only when due) and on demand from `/admin`. Stdlib only. |
| `hub_control.py` | Root helper for `/admin`, run by `irate-box-control.path` when the hub queues a request in `$STATE/control/requests/`. Starts, stops and enables only the services on its allow-list (Caddy and the hub: restart only), and changes the admin password everywhere it is used (Caddy's `basic_auth`, ttyd, Syncthing, `/etc/hub/admin-password`). The hub itself never gets root. |
| `hubclock.py` | The clock. The boards have no RTC, so everything ages by *cumulative powered-on seconds*, never the wall clock. Messages posted an hour before the box is switched off are still an hour old when it comes back. |
| `static/` | The pages. No framework, no build step, no network fetches. The home page has the shoutbox and board as two tabs, a row of app tiles and a row for the box itself: users online, a QR code to join, memory and disk, and services. There are list pages for the tools, Meshtastic and the service dashboard. `app.html` is the hub bar: every app opens under it, it slides away on scroll-down, and its theme picker sets light or dark for every app. |
| `Caddyfile` | One origin, path-routed: `/` to the hub; static app builds at `/draw/`, `/mermaid/`, `/tools/` and `/serial/`; `/wiki/`, `/notes/`, `/mqtt`, `/sync/` and `/term/` passed to loopback ports. Plain HTTP only; see below. |
| `irate-box.service` | A hand-install systemd unit for the hub (paths under `/home/pi`). `install.sh` writes its own. |
| `dnsmasq-hotspot.conf` | DHCP + the `address=/#/192.168.4.1` hijack that makes every name resolve to the box. |

## How it hangs together

```
phone / laptop ──▶ Caddy :80 ─┬─ /              → server.py :8000  (pages, shoutbox, board, /api/*, /status)
                              ├─ /app.html      → the hub bar; every app opens inside it
                              ├─ /draw/*  /mermaid/*  /tools/*  /serial/*   → static builds on disk
                              ├─ /wiki/*        → kiwix-serve :8081
                              ├─ /notes/*       → SilverBullet :3000         (add-on)
                              ├─ /mqtt          → mosquitto WebSockets :9001 (add-on)
                              ├─ /sync/*        → Syncthing GUI :8384        (add-on, admin login)
                              ├─ /admin/*       → server.py                  (admin login)
                              └─ /term/*        → ttyd :7681                 (admin login, off by default)

Meshtastic node / phone app ──▶ mosquitto :1883  (add-on; raw MQTT, not through Caddy)
```

Everything a guest's browser touches is one origin: no ports to type, no mDNS to fail on
Android, and a captive portal that can hand out a working URL. Services bind to loopback, and
Caddy is the only listener on the network with two exceptions, both for things that are not
browsers. Syncthing's sync ports are one; mosquitto's `:1883` for Meshtastic nodes is the
other.

**Why plain HTTP.** Captive-portal probes are HTTP; intercepting HTTPS produces a certificate
error instead of a sign-in sheet. So `:80` is always up and always plain, there is never a
blanket HTTP→HTTPS redirect, and never HSTS — once sent, a browser refuses plain HTTP for that
host, which is exactly the offline mode. It tests fine at home and fails in a field six weeks
later.

**Captive portal.** dnsmasq resolves every name to the box; the OS's connectivity probe
(`captive.apple.com`, `connectivitycheck.gstatic.com`, …) hits `server.py`, gets a 302 to the
hub, and the phone pops its sign-in sheet on the landing page.

## Configuration

All by environment variable; the unit file sets the Pi values.

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8000` | hub listen port |
| `HUB_BIND` | `0.0.0.0` | `127.0.0.1` behind Caddy |
| `HUB_URL` | `/` | where captive probes are redirected; the Pi sets `http://192.168.4.1/` so the sign-in sheet shows a typeable address |
| `HUB_STATE_DIR` | repo dir | where `messages.json`, `board.json`, `clock.json` and `store/` live |
| `HUB_STORE_MAX_BODY` | 50 MB | largest single blob |
| `HUB_STORE_MAX_TOTAL` | 64 MB | store quota; oldest evicted first |
| `HUB_STORE_SAVE_TTL` | `0` (never) | gallery-save expiry, in clock ticks |
| `HUB_DRAW_ROOT`, `HUB_MERMAID_ROOT`, `HUB_TOOLS_ROOT`, `HUB_SERIAL_ROOT` | unset | where each static app lives. Caddy serves from these, and `/status` reports an app whose directory is missing as not installed. Unset (a dev checkout) counts as installed. |

Shoutbox messages last 24 hours of powered-on time, capped at 200. The `/status` endpoint
reports whether Caddy is in front, the state of each service, and memory and disk. The pages
grey out anything that is down.

## Not here

The plan — hardware notes, component roles, `.deb` packaging, lightweighting tiers, phasing —
lives in a separate notes vault, not in this repo. The related repositories:

- [excalidraw-stack](https://github.com/nomdetom/excalidraw-stack): Excalidraw fork, with a
  `hub` build that stores into `store.py` and runs inside the hub bar.
- [mermaid-live-editor](https://github.com/NomDeTom/mermaid-live-editor): `hub` branch, an
  offline build with the Mermaid Chart promotion and external services removed.
- [serial-terminal](https://github.com/nomdetom/serial-terminal): the Meshtastic log
  analyser served at `/serial/`.
- [nomdetom.github.io](https://github.com/nomdetom/nomdetom.github.io): the calculators
  served at `/tools/`.
- [docusaurus2zim](https://github.com/NomDeTom/docusaurus2zim): turns a Docusaurus site into
  a ZIM for Kiwix. Its books are relative by default, so they work under the hub's `/wiki`
  and anywhere else.

## License

The core of this repo (`server.py`, `store.py`, `board.py`, `hubclock.py`, and the
`static/` assets) is released into the public domain under the [Unlicense](LICENSE).
`static/qrcode.js` is vendored third-party code (MIT, Kazuhiko Arase) and keeps its own
license, noted in its header.
