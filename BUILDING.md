# Building and running the hub, end to end

How the pieces fit together today, and the order to do them in. This is the development
setup — one machine, everything under your home directory, Caddy on a high port. The
`.deb`-packaged install for a Pi is a later phase and not described here.

There are three repositories. The hub itself is tiny and needs nothing built; the two
apps are forks with a **hub build mode** each, producing static files the hub's Caddy
serves from disk.

```
~/irate-box/                          this repo: server, page, Caddyfile
~/mermaid-live-editor/                fork, branch `hub`      → builds to docs/
~/excalidraw-stack/excalidraw/        fork, branch `main`     → builds to excalidraw-app/build/
```

The apps are separate repos on purpose: each tracks its upstream and takes rebases, and the
hub-specific parts are a handful of gated lines plus one env file per repo. Nothing about the
hub is patched into the build output after the fact.

## 0. Prerequisites

- Python 3 (any recent). The hub server is stdlib only.
- Node 24 with `pnpm` (mermaid) and `yarn` (excalidraw). Both forks pin their toolchains in
  the usual lockfiles.
- Caddy 2. `apt install caddy`, or a single binary from <https://caddyserver.com/download>.
  No plugins are needed anywhere — that is a design rule, not a coincidence.

## 1. The hub

```sh
git clone https://github.com/NomDeTom/irate-box ~/irate-box
cd ~/irate-box
python3 server.py
```

That already gives you the landing page, shoutbox, board, blob store and `/status` on
<http://localhost:8000>. State (`messages.json`, `board.json`, `clock.json`, `store/`)
appears next to `server.py` unless `HUB_STATE_DIR` says otherwise. Leave it running; the
apps below are served *around* it, not by it.

## 2. Mermaid

```sh
git clone -b hub https://github.com/NomDeTom/mermaid-live-editor ~/mermaid-live-editor
cd ~/mermaid-live-editor
pnpm install
pnpm build:hub
```

`build:hub` is `vite build --mode hub` with [`.env.hub`](../mermaid-live-editor/.env.hub)
layered over upstream's `.env`. What the mode changes:

| Setting | Effect on the hub build |
|---|---|
| `MERMAID_IS_ENABLED_MERMAID_CHART_LINKS=''` | removes the Mermaid Chart promotion banner, "Save diagram", "Contact sales", "Repair with AI", the premium-features modal, and (a fork change) the "Edit with AI / visual / voice" button and Monaco's AI-prompt gutter icon |
| `MERMAID_RENDERER_URL`, `_KROKI_RENDERER_URL`, `_DOCS_URL`, `_ANALYTICS_URL` all `''` | no button leads off the box; PNG/SVG export still works client-side |
| `MERMAID_HUB_RETURN_SCRIPT='/hub-return.js'` | the app loads the hub's floating "⌂ Hub" link |

One trap, already handled by the script but worth knowing: upstream's `svelte.config.js`
imports `dotenv/config`, which loads `.env` into `process.env` *before* Vite runs, and
`process.env` outranks `.env.hub`. So `--mode hub` on its own is silently ignored; the
script sets `DOTENV_CONFIG_PATH=.env.hub` as well. To check a build really is a hub build,
look for the compiled flag rather than for strings:

```sh
grep -ohE 'isEnabledMermaidChartLinks:[^,]+' docs/_app/immutable/chunks/*.js   # want !1
```

Output is `docs/` (gitignored), ~23 MB on disk, ~5 MB gzipped, most of it lazy-loaded
(Monaco for desktop widths, ZenUML's icon pack, ELK).

## 3. Excalidraw

```sh
git clone https://github.com/nomdetom/excalidraw-stack ~/excalidraw-stack
cd ~/excalidraw-stack && git submodule update --init excalidraw
cd excalidraw
yarn
yarn build:hub
```

(Only the `excalidraw` submodule is needed. `excalidraw-room` and
`excalidraw-storage-backend` are for the Docker stack's `full` and `sqlite` modes, which the
hub does not use — see `excalidraw.sh --mode` in the stack's README.)

`build:hub` is `vite build --mode hub --base /draw/` with
[`.env.hub`](../excalidraw-stack/excalidraw/.env.hub):

| Setting | Effect |
|---|---|
| `--base /draw/` | asset URLs are rooted at `/draw/`, so the app can live under that path on the hub's origin |
| `VITE_APP_STORAGE_BACKEND=http`, `VITE_APP_HTTP_STORAGE_BACKEND_URL=/api/v2` | shareable links, collaboration rooms and pasted images go to the hub's `store.py`, on the same origin — no NestJS, no Redis, no CORS |
| `VITE_APP_BACKEND_V2_GET_URL` / `_POST_URL` `=/api/v2/scenes/` | "Export → shareable link" uses the same store |
| `VITE_APP_WS_SERVER_URL=''` | live collaboration is off (it needs the room server; not part of the hub today) |
| Firebase, libraries, Sentry, tracking | all blanked |
| `sourcemap: mode !== "hub"` (in `vite.config.mts`) | no `.map` files: halves the output |
| hub-only Vite plugin | injects `<script src="/hub-return.js">` for the "⌂ Hub" link |

Output is `excalidraw-app/build/` (gitignored), ~24 MB on disk, ~3 MB gzipped, of which
half is fonts loaded on demand.

## 4. Caddy in front

```sh
cd ~/irate-box
caddy run --config Caddyfile
```

The Caddyfile listens on `:80`, which needs root or `setcap cap_net_bind_service=+ep` on
the binary. For development, copy it and change the one line:

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
| `/wiki/`, `/serial/`, `/term/` | reverse-proxied to loopback ports; 502 until something listens |

If your checkouts are elsewhere, the roots are environment placeholders, not edits:

```sh
HUB_MERMAID_ROOT=/srv/mermaid HUB_DRAW_ROOT=/srv/draw HUB_STATIC=/srv/hub caddy run --config Caddyfile
```

The landing page asks `/status` every 15 s and greys out any tile whose backend is not
there. Served without Caddy at all (plain `:8000`), every tile is greyed and the page says
so — that is the expected look of step 1 on its own.

## 5. Rebuilding after a change

- Hub page or server: nothing to build. Caddy reads `static/` off disk; restart
  `server.py` for Python changes.
- Mermaid: `pnpm build:hub` again. The `docs/` tree is replaced wholesale.
- Excalidraw: `yarn build:hub` again.
- Caddyfile: `caddy reload --config Caddyfile` (or restart the dev copy).

Guests never see a stale app after a rebuild: hashed chunks are served
`immutable`, everything else `no-cache`, so the next page load revalidates.

## 6. Keeping the forks current

Both forks carry an `upstream` remote. The hub-specific commits are few and sit on top:

```sh
cd ~/mermaid-live-editor && git fetch upstream && git rebase upstream/develop   # on branch hub
cd ~/excalidraw-stack/excalidraw && git fetch upstream && git rebase upstream/master
```

Expect the occasional snapshot-test conflict in Excalidraw (regenerate with
`npx vitest run <file> -u`) and, in mermaid, an unmaintained `eslint-plugin-sort-keys` that
crashes ESLint 10's fixer if you ever leave an object literal unsorted.

## What this is not yet

- Not the Pi install. That is `hub-core`/`hub-app-*` `.deb`s with the builds under
  `/usr/share/hub/apps/`, `systemd` units, and Caddy on `:80` with the AP and dnsmasq
  hijack underneath. The Caddyfile already has the shape; the packaging does not exist.
- Not Kiwix, the serial terminal or ttyd — the Caddyfile routes exist, the backends have to
  be installed separately and are not built from these repos.
- Not precompressed. `file_server { precompressed br gzip }` plus a compression step in
  each build is the cheap next win for a single-core board.
