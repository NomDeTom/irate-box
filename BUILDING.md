# Building and running the hub, end to end

How the pieces fit together today, and the order to do them in. This is the development setup — one machine, everything under your home directory, Caddy on a high port. The `.deb`-packaged install for a Pi is a later phase and not described here.

There are three repositories. The hub itself is tiny and needs nothing built; the two apps are forks with a **hub build mode** each, producing static files the hub's Caddy serves from disk.

```
~/irate-box/                          this repo: server, page, Caddyfile
~/mermaid-live-editor/                fork, branch `hub`      → builds to docs/
~/excalidraw-stack/excalidraw/        fork, branch `main`     → builds to excalidraw-app/build/
```

The apps are separate repos on purpose: each tracks its upstream and takes rebases, and the hub-specific parts are a handful of gated lines plus one env file per repo. Nothing about the hub is patched into the build output after the fact.

## 0. Prerequisites

- Python 3 (any recent). The hub server is stdlib only.
- Node 24 with `pnpm` (mermaid) and `yarn` (excalidraw). Both forks pin their toolchains in the usual lockfiles.
- Caddy 2. `apt install caddy`, or a single binary from <https://caddyserver.com/download>. No plugins are needed anywhere — that is a design rule, not a coincidence.

## 1. The hub

```sh
git clone https://github.com/NomDeTom/irate-box ~/irate-box
cd ~/irate-box
python3 server.py
```

That already gives you the landing page, shoutbox, board, blob store and `/status` on
<http://localhost:8000>. State (`messages.json`, `board.json`, `clock.json`, `store/`)
appears next to `server.py` unless `HUB_STATE_DIR` says otherwise. Leave it running; the apps below are served *around* it, not by it.

## 2. Mermaid

```sh
git clone -b hub https://github.com/NomDeTom/mermaid-live-editor ~/mermaid-live-editor
cd ~/mermaid-live-editor
pnpm install
pnpm build:hub
```

`build:hub` is `vite build --mode hub` with [`.env.hub`](../mermaid-live-editor/.env.hub) layered over upstream's `.env`. What the mode changes:

| Setting | Effect on the hub build |
|---|---|
| `MERMAID_IS_ENABLED_MERMAID_CHART_LINKS=''` | removes the Mermaid Chart promotion banner, "Save diagram", "Contact sales", "Repair with AI", the premium-features modal, and (a fork change) the "Edit with AI / visual / voice" button and Monaco's AI-prompt gutter icon |
| `MERMAID_RENDERER_URL`, `_KROKI_RENDERER_URL`, `_DOCS_URL`, `_ANALYTICS_URL` all `''` | no button leads off the box; PNG/SVG export still works client-side |
| `MERMAID_HUB_RETURN_SCRIPT='/hub-return.js'` | the app loads the hub's floating "⌂ Hub" link |

One trap, already handled by the script but worth knowing: upstream's `svelte.config.js` imports `dotenv/config`, which loads `.env` into `process.env` *before* Vite runs, and `process.env` outranks `.env.hub`. So `--mode hub` on its own is silently ignored; the script sets `DOTENV_CONFIG_PATH=.env.hub` as well. To check a build really is a hub build, look for the compiled flag rather than for strings:

```sh
grep -ohE 'isEnabledMermaidChartLinks:[^,]+' docs/_app/immutable/chunks/*.js   # want !1
```

Output is `docs/` (gitignored), ~23 MB on disk, ~5 MB gzipped, most of it lazy-loaded (Monaco for desktop widths, ZenUML's icon pack, ELK).

## 3. Excalidraw

```sh
git clone https://github.com/nomdetom/excalidraw-stack ~/excalidraw-stack
cd ~/excalidraw-stack && git submodule update --init excalidraw
cd excalidraw
yarn
yarn build:hub
```

(Only the `excalidraw` submodule is needed for drawing and saving. `excalidraw-room` is the optional live-collaboration relay, built in §3a. `excalidraw-storage-backend` is for the Docker stack's legacy `full` and `sqlite` modes, which the hub does not use.)

The build checks nothing in parallel (see the last two lines of `.env.hub`): run `yarn test:typecheck` on its own. Together they peak near 3 GB.

`build:hub` is `vite build --mode hub --base /draw/` with [`.env.hub`](../excalidraw-stack/excalidraw/.env.hub):

| Setting | Effect |
|---|---|
| `--base /draw/` | asset URLs are rooted at `/draw/`, so the app can live under that path on the hub's origin |
| `VITE_APP_STORAGE_BACKEND=http`, `VITE_APP_HTTP_STORAGE_BACKEND_URL=/api/v2` | shareable links, collaboration rooms and pasted images go to the hub's `store.py`, on the same origin — no NestJS, no Redis, no CORS |
| `VITE_APP_BACKEND_V2_GET_URL` / `_POST_URL` `=/api/v2/scenes/` | "Export → shareable link" uses the same store |
| `VITE_APP_WS_SERVER_URL=''` | empty means "this origin": live collaboration connects to `/socket.io/`. At load the app probes that path, and shows collaboration only if a room relay answers (§3a) |
| `VITE_APP_OFFLINE=true` | hides Excalidraw+ promos and sign-up, AI, social links, library browsing and the analytics loader; fonts load from `/draw/` only; adds "Save to / Open from hub gallery" to the menu (`/api/saves`, with a thumbnail) |
| Firebase, libraries, Sentry, tracking | all blanked |
| `sourcemap: mode !== "hub"` (in `vite.config.mts`) | no `.map` files: halves the output |
| hub-only Vite plugin | injects `<script src="/hub-return.js">` for the "⌂ Hub" link |

Output is `excalidraw-app/build/` (gitignored), ~24 MB on disk, ~3 MB gzipped, of which half is fonts loaded on demand.

## 3a. Excalidraw live collaboration (optional)

```sh
cd ~/excalidraw-stack && git submodule update --init excalidraw-room
cd excalidraw-room && yarn && yarn build                     # → dist/index.js
mkdir -p ~/hub-apps/room && cp -r dist ~/hub-apps/room/
cd ~/hub-apps/room && npm init -y >/dev/null &&
  npm install --omit=dev debug@4.3.1 dotenv@10 express@4.17.1 socket.io@4.6.1
```

That is the relay plus its four runtime dependencies (~8 MB, all plain JS, so it runs on any architecture). `install.sh --with-collab --apps ~/hub-apps` copies it to `/usr/share/hub/room` and runs it with Debian's `nodejs` as `excalidraw-room.service`, bound to `127.0.0.1:3002` (`HOST`, in the fork). Caddy routes `/socket.io/*` to it. Measured on the Lyra: ~8 MB anon idle, ~19 MB and about one core with twelve busy clients, and Kiwix search latency barely moved (see `plans/offline-storage-plan.md` in the notes).

## 4. Caddy in front

```sh
cd ~/irate-box
caddy run --config Caddyfile
```

The Caddyfile listens on `:80`, which needs root or `setcap cap_net_bind_service=+ep` on the binary. For development, copy it and change the one line:

```sh
sed 's/^:80 {/:8088 {/' Caddyfile > /tmp/Caddyfile.dev && caddy run --config /tmp/Caddyfile.dev
```

Now everything is on one origin:

| Path | Comes from |
|---|---|
| `/` | `server.py` — page, probe redirects |
| `/*.js`, `/*.css`, `/board.html`, `/hub-return.js` | `static/` via Caddy's file server |
| `/messages`, `/board/*`, `/api/*`, `/status` | `server.py` |
| `/mermaid/` | `~/mermaid-live-editor/docs` |
| `/draw/` | `~/excalidraw-stack/excalidraw/excalidraw-app/build` |
| `/wiki/`, `/serial/`, `/term/`, `/notes/`, `/sync/` | reverse-proxied to loopback ports; 502 until something listens |

If your checkouts are elsewhere, the roots are environment placeholders, not edits:

```sh
HUB_MERMAID_ROOT=/srv/mermaid HUB_DRAW_ROOT=/srv/draw HUB_STATIC=/srv/hub caddy run --config Caddyfile
```

The landing page asks `/status` every 15 s and greys out any tile whose backend is not there. Served without Caddy at all (plain `:8000`), every tile is greyed and the page says so — that is the expected look of step 1 on its own.

## 4a. Optional add-ons: SilverBullet and Syncthing

Neither is built from source; both are single upstream binaries. Neither is part of any profile. Without them, the Notes card is greyed out and `/sync/` returns 502.

**SilverBullet** (`/notes/`, a Markdown notebook over a plain folder). Take the `silverbullet-server-linux-<arch>.zip` from its GitHub releases. Only x86_64, armv7 and aarch64 builds exist, so this runs on the Lyra but not on the Zero W.

```sh
mkdir -p ~/hub-notes
SB_URL_PREFIX=/notes SB_PORT=3000 SB_FOLDER=~/hub-notes ./silverbullet
```

It binds `127.0.0.1` by default. Keep `SB_URL_PREFIX` in step with the Caddy route, which passes the path through unstripped. Guests can edit by default. To stop that, set `SB_USER=user:pass` or `SB_READ_ONLY=true`.

**Syncthing** (`/sync/`, operator only, behind the same password as `/admin`). `apt install syncthing`. It runs on every board, including the ARMv6 Zero W.

```sh
syncthing serve --no-browser --gui-address=127.0.0.1:8384
```

Then in its GUI: turn off global discovery, relays, NAT traversal and usage reporting (the box is offline, so none of them can reach anything). Keep local discovery. Share `~/hub-notes` so the notebook syncs to your own machines while they are on the box's WiFi. Syncthing's own listeners (`:22000` tcp/udp, `:21027/udp`) do not go through Caddy.

## 4b. Installing on a board: `install.sh`

For an Armbian board, or mPWRD-OS (an Armbian build), there is one script, instead of steps 1, 4 and 4a by hand. Tested on a Luckfox Lyra Zero W (mPWRD-OS 26.05, trixie, armv7l).

```sh
git clone https://github.com/NomDeTom/irate-box && cd irate-box
sudo ./install.sh --with-notes --with-sync --apps ~/hub-apps
```

It installs Caddy from Caddy's own apt repository. Distro packages lag behind: Debian trixie ships 2.6, and the Caddyfile is written for 2.8+. ARMv6 is the exception, because that repository's armhf build targets ARMv7 and would crash on a Zero W. There the script uses the distro's genuine ARMv6 build and rewrites `basic_auth` to the 2.6 spelling, `basicauth`. It also sets up a `hub` system user and `irate-box.service` on loopback. The code goes in `/opt/irate-box`, state in `/var/lib/hub` and config in `/etc/hub`. The generated Caddyfile replaces `/etc/caddy/Caddyfile`, and the original is kept as `Caddyfile.pre-irate-box`. `--with-notes` and `--with-sync` add SilverBullet and `syncthing@hub`. Syncthing is set up for an offline box as described in 4a, with the same admin login as Caddy's gate, and shares `/var/lib/hub/notes` as the folder `hub-notes`.

The apps are not built on the board. `--apps-from-actions` fetches the newest bundle of `draw`, `mermaid` and `serial` (and `room`, with `--with-collab`) from the forks' `irate-box-bundle.yml` artifacts through nightly.link, no token needed, and adds them to `/admin`'s Apps section, which keeps them current from then on. Or `--apps DIR` copies your own prebuilt `DIR/mermaid`, `DIR/draw`, `DIR/tools` and `DIR/serial` into `/usr/share/hub/apps/`. `serial` is the `npm run build` output of `nomdetom/serial-terminal`. `--tools` clones the calculators.

`--zim FILE|URL`, which can be repeated, installs `kiwix-serve` at `/wiki/`. It copies the file, or downloads the URL on the box, into `/var/lib/hub/zim` and registers it in `library.xml`. Kiwix is mounted at `/wiki`, so a book must not depend on where it is mounted. openZIM books, and docusaurus2zim's by default, use relative paths and work as they are. A book built for one prefix (docusaurus2zim `--base-url`) works only there; one built for `/content/<name>/` shows "did not load properly" on the hub.

`--with-mqtt` installs the mosquitto broker. Nodes, and the phone app's MQTT Client Proxy, connect to port 1883. Pages connect with WebSockets at `/mqtt`, which Caddy passes to a listener on loopback. Clients are anonymous, and an ACL limits them to `msh/#`. Messages are capped at 4 kB, connections at 64 per listener, and nothing is persisted, so retained messages are gone after a restart. Port 1883 listens on every interface for now; once the access point exists it should listen on the AP address only. `mosquitto_sub -t 'msh/#' -v` on the box shows traffic.

`--with-collab` adds live collaboration to `/draw/`: Debian's `nodejs` and the room relay from `--apps DIR/room` (§3a). ARMv7 and up; Node has no ARMv6 build.

If Tailscale is already on the board, the script puts it under a switch on `/admin` (off / on / on for N hours) instead of running it at every boot. `tailscale-apply.sh`, run as root by a path unit, does the starting and stopping. The script never changes Tailscale's current state, so it is safe to run over Tailscale.

The librarian (`librarian.py`, settings under Library on `/admin`) keeps ZIM books current from where they are published: a project's GitHub releases, a fork's Actions artifacts (GitHub requires a token to download those, even from public repositories) or the same artifacts through nightly.link (no token), or a plain URL. A new version replaces `<name>.zim` in place, so links into the book never change, and `library.xml` is rebuilt for kiwix-serve's `--monitorLibrary` to pick up: no restart and no root. Old versions are archived under `/var/lib/hub/library/archive/` (keep 0–3) and can be rolled back. It runs hourly from `irate-box-librarian.timer`, checking each source only when the policy says it is due, at idle CPU and I/O priority. The same is available from the command line: `sudo -u hub HUB_STATE_DIR=/var/lib/hub python3 /opt/irate-box/librarian.py status`.

ttyd is always installed at `/term/`: the upstream static binary, checked against its published SHA256SUMS. It only runs with `--with-term`, and stays on for later runs once enabled. The admin login gets you past Caddy, and after that `/bin/login` asks for a real account on the box.

Notes, Kiwix, Tools and Meshtastic open inside `app.html`, a hub bar with an iframe, because they cannot load `hub-return.js` themselves. The address keeps the app's own path (`/app.html#/wiki/…`), so reloads and shared links work. ↗ drops the bar. There is no admin password until the owner chooses one: right after a first install, `/admin/` is a set-the-password page, open to whoever reaches it first, so open it straight away (or pass `--admin-password` for a scripted install). It is then kept in `/etc/hub/admin-password`; `sudo python3 /opt/irate-box/hub_control.py reset-password` from the console starts over. Run the script again to upgrade. State and the password survive. It does not touch the network: there is no AP, no dnsmasq and no captive portal yet. The hub is at `http://<the board's address>/` on whatever network the board is on.

## 5. Rebuilding after a change

- Hub page or server: nothing to build. Caddy reads `static/` off disk; restart `server.py` for Python changes.
- Mermaid: `pnpm build:hub` again. The `docs/` tree is replaced wholesale.
- Excalidraw: `yarn build:hub` again.
- Caddyfile: `caddy reload --config Caddyfile` (or restart the dev copy).

Guests never see a stale app after a rebuild: hashed chunks are served `immutable`, everything else `no-cache`, so the next page load revalidates.

## 6. Keeping the forks current

Both forks carry an `upstream` remote. The hub-specific commits are few and sit on top:

```sh
cd ~/mermaid-live-editor && git fetch upstream && git rebase upstream/develop   # on branch hub
cd ~/excalidraw-stack/excalidraw && git fetch upstream && git rebase upstream/master
```

Expect the occasional snapshot-test conflict in Excalidraw (regenerate with `npx vitest run <file> -u`) and, in mermaid, an unmaintained `eslint-plugin-sort-keys` that crashes ESLint 10's fixer if you ever leave an object literal unsorted.

## What this is not yet

- Not the Pi install. That is `hub-core`/`hub-app-*` `.deb`s with the builds under `/usr/share/hub/apps/`, `systemd` units, and Caddy on `:80` with the AP and dnsmasq hijack underneath. The Caddyfile already has the shape; the packaging does not exist.
- Not Kiwix, the serial terminal or ttyd — the Caddyfile routes exist, the backends have to be installed separately and are not built from these repos.
- Not precompressed. `file_server { precompressed br gzip }` plus a compression step in each build is the cheap next win for a single-core board.
