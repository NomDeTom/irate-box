# Irate-Box

A self-contained offline hub for a small board. It runs a WiFi access point; anything that
joins gets a captive portal that opens a landing page, and from that page you reach a
whiteboard, a diagram editor, an offline encyclopedia, a serial console and a shell — with no
internet involved at any point. Spiritually a [PirateBox](https://github.com/PirateBox-Dev)
successor (the name is PirateBox with the P knocked off), but Pi-first, HTTPS-free by design,
and built from maintained, packaged components rather than a 2013 shell-script pile.

**Status: early.** The hub server, its page, the blob store and the Caddy front all work and
are what you see here. The access-point layer, packaging and most of the apps are still a
plan. Target hardware is a Raspberry Pi Zero W or similar 512 MB board, and that ceiling
drives every design decision: static assets plus a handful of small native daemons.

## Try it

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

Then it is <http://localhost/>, with `/mermaid/` and `/draw/` live once the two app forks are
built — **[BUILDING.md](BUILDING.md)** walks through all of it, clone to running — and
`/wiki/`, `/serial/`, `/term/` waiting for their backends on the ports the Caddyfile names.

## What is here

| File | Role |
|---|---|
| `server.py` | The hub: landing page, shoutbox, board, blob store, `/status`, captive-portal target. Stdlib only, threaded. |
| `board.py` | Threaded message board: 50 threads, 200 posts each, threads fade seven days of powered-on time after their last reply. |
| `store.py` | Blob store. Reimplements `excalidraw-storage-backend`'s `/api/v2` over a directory (no NestJS, no Redis) and adds `/api/saves`, a named-save gallery with client-rendered thumbnails. Runs standalone on `:8090` for testing. |
| `hubclock.py` | The clock. The boards have no RTC, so everything ages by *cumulative powered-on seconds*, never the wall clock. Messages posted an hour before the box is switched off are still an hour old when it comes back. |
| `static/` | The page. No framework, no build step, no network fetches. Shoutbox and board as two tabs, collapsible service tiles, emoji picker, light/dark/auto theme. |
| `Caddyfile` | One origin, path-routed: `/` → hub, `/mermaid/` static, `/wiki/` `/draw/` `/serial/` `/term/` reverse-proxied to loopback ports. Plain HTTP only — see below. |
| `irate-box.service` | systemd unit for the hub on the Pi. |
| `dnsmasq-hotspot.conf` | DHCP + the `address=/#/192.168.4.1` hijack that makes every name resolve to the box. |

## How it hangs together

```
phone / laptop ──▶ Caddy :80 ─┬─ /            → server.py :8000  (page, shoutbox, board, /api/*, /status)
                              ├─ /mermaid/*   → static files
                              ├─ /wiki/*      → kiwix-serve :8081
                              ├─ /draw/*      → excalidraw :3000
                              ├─ /serial/*    → serial terminal :8080
                              └─ /term/*      → ttyd :7681  (basic-auth, shipped disabled)
```

Everything a guest touches is one origin — no ports to type, no mDNS to fail on Android, and
a captive portal that can hand out a working URL. Services bind to loopback; Caddy is the sole
listener on the AP interface.

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

Shoutbox messages last 24 hours of powered-on time, capped at 200. The `/status` endpoint
reports whether Caddy is in front and which backends are listening; the page greys out tiles
accordingly.

## Not here

The plan — hardware notes, component roles, `.deb` packaging, lightweighting tiers, phasing —
lives in a separate notes vault, not in this repo. The related app forks are
[excalidraw-stack](https://github.com/nomdetom/excalidraw-stack) (with a `hub` storage mode
that points at `store.py`) and
[mermaid-live-editor](https://github.com/NomDeTom/mermaid-live-editor) (`hub` branch:
offline build with the Mermaid Chart promotion and external services removed).

## License

Not chosen yet.
