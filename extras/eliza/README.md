<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# ELIZA, the optional chatbot add-on

Not installed by default. `sudo ./install.sh --with-eliza` (or Add-ons on /admin) has the
librarian fetch [anthay/ELIZA](https://github.com/anthay/ELIZA), Anthony and Max Hay's
recreation of Weizenbaum's 1966 ELIZA, adapt it, and install it at `/usr/share/hub/apps/eliza/`,
served at `/eliza/`. `--remove eliza` takes it out, and its librarian source with it. It runs
in the visitor's browser: no service, nothing in memory on the box.

## What the hub's code holds, and what it does not

This folder holds only the Irate-Box's own part:

| File | What | Licence |
|---|---|---|
| `box.txt` | the box's own keywords (mesh, node, battery, firmware …), installed as `scripts/box.txt` | MIT |
| `README.md` | this | CC-BY-SA-4.0 |

and elsewhere in the code: `apps.d/65-eliza.json` (the tile, the list page and its entries,
the source and its pin), `irate_box/library/adapt_eliza.py` (the adapt script), and
`tests/eliza.cjs` and `tests/sim_pin.py`.

ELIZA itself, the page and every script, is not in the hub's code. Some of the scripts are not
openly licensed: the 1966 CACM DOCTOR is © ACM, the French DOCTOR translates it, and YAPYAP is
reproduced with the Harold Garfinkel Archive's permission. So each box fetches them from where
their publisher keeps them, and the owner agrees to that when adding ELIZA (the add-on's
consent text). ELIZA is never put in an offline kit (`--make-offline-bundle`) for the same reason.

## How it is fetched: latest and pinned

The manifest's `source` names the repository and pins a commit, the last one checked to work
with the adapt script. The librarian records both, the newest commit on `master` and the
pinned one, and installs the one the source follows: the pinned one unless the owner chooses
"Follow the newest" on /admin (Library, Apps). Following the newest, a commit that does not
fetch, adapt or check leaves the box on what it has (or, with nothing installed, the pinned
one). To move the pin: try the new commit (below), look at what changed, then change `pin`
(and the entries, if scripts were added) in `apps.d/65-eliza.json`.

## What the adapt script changes

`adapt_eliza.py` copies `src/eliza.html` to `eliza.html` with a note saying what changed, and
adds one block to its load handler: `eliza.html?script=NAME` runs `scripts/NAME.txt` as the
page's `*load` does with a file, and `&add=box` also puts `scripts/box.txt` in front of the
script's closing `()`. With no `?script=`, the page is exactly upstream's, built-in DOCTOR and
all. It adds `scripts/box.txt` and an `index.html` that sends `/eliza/` to the list page, and
drops `src/` (the C++ version) and `doc/`. If upstream's page no longer has what the block
relies on, it stops with an error rather than install a page that half works.

The list page, `/eliza.html`, is rendered by the hub from the manifest's `entries`, like the
calculators' lists, under the banner from ELIZA's own source.

## Testing a commit

    git clone -q https://github.com/anthay/ELIZA /tmp/eliza && git -C /tmp/eliza checkout -q COMMIT
    rm -rf /tmp/eliza/.git && ./irate-box adapt_eliza /tmp/eliza && node tests/eliza.cjs /tmp/eliza

`tests/eliza.cjs` runs every script on the list through the engine in the adapted page.
`python3 tests/sim_pin.py` checks the librarian's latest-and-pinned choices offline.

To add vocabulary, add keyword rules to `box.txt`:
`(KEYWORD precedence ((decomposition) (reassembly) …))`, upper case, as in the scripts.
