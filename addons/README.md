<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Web add-ons: the catalogue, and how to write one

A **web add-on** is a static web app (it runs in the visitor's browser) that the box's owner adds
from **/admin → Add-ons → Web add-ons**, with no installer, no root and no restart. Each
`*.json` in this folder is one the hub offers there (its *catalogue*). The owner can also paste
a manifest that isn't here, behind a heavy warning.

What happens when one is added:

1. The hub writes its manifest to `/var/lib/hub/apps.d/<id>.json` and records the owner's consent.
2. The librarian fetches the source **at the pinned commit**, runs the adapt script if it names
   one, checks the result, and unpacks it into `/var/lib/hub/addons/<id>/`. All of this runs as
   the hub user.
3. It starts **off**. The owner switches it public or private like any app.
4. It is served on the **add-on origin**, port 8090 of the same box. `/addons/<id>/` on the hub
   redirects there, and its tile frames it under the hub bar. Being a different origin, its pages
   can't reach `/admin`, read the hub's storage or script its pages, and the web server sends each
   one a Content-Security-Policy built from its manifest.
5. The Library pane's Apps keeps it current: at the pin, unless the owner chooses
   "Follow the newest".


## The manifest

```json
{
  "id": "eliza",
  "order": 65,
  "tile": {"icon": "🗨️🛋️", "name": "ELIZA", "desc": "…", "path": "index.html"},
  "menu": {"title": "…", "subtitle": "…", "about": "…", "banner": ["…"]},
  "entries": [{"menus": {"eliza": 10}, "name": "…", "desc": "…", "path": "eliza.html?script=…"}],
  "source": {"type": "git", "repo": "https://github.com/…", "branch": "main", "pin": "<40 hex>", "adapt": "adapt_eliza.py"},
  "needs": "eliza.html",
  "addon": {"title": "…", "summary": "…", "consent": "…"},
  "capabilities": {"connect": ["ws://{box}/mqtt"], "storage": true}
}
```

| Part | What it says | Rules (checked by `manifests.check_local`) |
|---|---|---|
| `id` | the add-on's name: its folder, its address `/addons/<id>/`, the file's name | lower-case letters, digits, `-`; not a built-in app's, nor a name the hub's pages use (`admin`, `themes`, …) |
| `order` | where its tile sits among the others | an integer |
| `tile` | the home page tile; `path` is the page it opens, inside the add-on | `path` has no scheme, no host, no `..`, no leading `/` |
| `menu`, `entries` | optional: a list page at `/<id>.html` (like the calculators' lists), and its lines, each a `path` inside the add-on | entries only on its own list |
| `source` | where it comes from: `git` (a repository, **pinned** to a full commit hash, optionally run through an adapt script) or `bundle` (a fork's `irate-box-bundle.yml` artifact) | `pin` is required; `adapt` must be one of the hub's own `irate_box/library/adapt_*.py`, so a manifest can never bring code that runs on the box |
| `needs` | the file that proves an install whole | a path inside the add-on |
| `addon` | its title, a one-line summary, and the **consent** text the owner agrees to | all three required; say plainly what it downloads, from where, and what it does with the network |
| `capabilities` | `connect`: what its pages may reach besides their own address (`{box}` is the hub itself), at most 8; `storage`: whether it keeps data in the visitor's browser | `ws://`, `wss://`, `http://`, `https://` only. Everything else it tries is blocked by its Content-Security-Policy |

Nothing else is allowed in a web add-on's manifest: no services, ports, units, install options
or restarts. Those are the built-in add-ons in `apps.d/`, which need the installer and root.

## The hub's theme

An add-on's pages can't read the hub's storage, being on another origin. When the hub bar
frames one, it adds the theme's base to the address instead: `hub-theme=light`, `dark` or
`auto` (auto: follow the device). The add-on's own query is kept. A page that wants to match
reads it on load:

```js
const theme = new URLSearchParams(location.search).get('hub-theme'); // light, dark, auto or null
if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
```

When the owner changes theme, the bar reloads the add-on with the new value. Opened on its own
(without the bar), there's no `hub-theme`; follow `prefers-color-scheme` then.

## A worked example: the calculators, as a plain copy

[`templates/calculators.json`](templates/calculators.json) is a complete, valid web add-on for
`nomdetom.github.io` (the site the hub's own Calculators come from), copied **as published**,
with no adapt script. It's in `templates/` rather than the catalogue because the hub already
has the calculators, adapted to its look. It shows the steps for any static site:

1. **Pick the id and the tile.** `nomdetom-calculators`, opening the site's own `index.html`.
2. **Pin a commit.** `git ls-remote https://github.com/nomdetom/nomdetom.github.io refs/heads/main`
   gives the newest; read what changed before pinning it.
3. **Say what it needs.** `index.html`. If that file is missing, the install is refused.
4. **Find what it reaches beyond itself.** Search the site for `fetch(`, `XMLHttpRequest`,
   `new WebSocket(`, and `src="https://…"` (scripts, styles, fonts, images). Links (`href`) don't
   count: they navigate, they don't load. Here one page fetches from `https://api.github.com`,
   so that goes in `connect`. Anything left out is blocked, and the page that needed it partly
   fails. Say it in the consent text too.
5. **Say whether it keeps data** (`localStorage`, IndexedDB): the calculators remember inputs.
6. **Mind the size.** A plain copy takes the whole repository but `.git`: here about 41 MB,
   nearly all one page. An adapt script (like `adapt_tools.py` for the hub's own calculators)
   can keep only what visitors open, and patch what needs it.
7. **Test it**, as below.

When a site needs changing to work here (paths, a look, a hook such as ELIZA's `?script=`),
write an adapt script in `irate_box/library/adapt_<name>.py`. See `adapt_eliza.py`: it fails
loudly when upstream changes under it, so the librarian keeps what the box has (or the pin)
rather than install something half working.

## Testing one

```sh
python3 -c "import json,sys; sys.path.insert(0,'.'); from irate_box.hub import manifests as m; m.check_local(json.load(open('addons/templates/calculators.json')))"
git clone -q https://github.com/nomdetom/nomdetom.github.io /tmp/a && git -C /tmp/a checkout -q <pin> && rm -rf /tmp/a/.git
# with an adapt script: ./irate-box adapt_<name> /tmp/a
```

`python3 tests/sim_local_addons.py` checks every manifest here, the catalogue's and the
templates', against the rules.
