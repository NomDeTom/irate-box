<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Adding a service to irate-box

For anyone, person or LLM, bringing a new service onto the box: a chat server, a map server, a sensor
broker. It says where the service should sit, who should reach it, what the manifest and the installer need,
what the firewall and the doctors then do by themselves, and how to test it. The IRC server (`--with-irc`,
`apps.d/65-irc.json`) is the worked example throughout.

The rules behind every choice below are irate-box's principles (README): the hub is the base unit and
everything else an add-on the core never depends on; the safe choice is the default and the open one is offered
with its warning; the owner chooses, from options laid out from the safest to the most open.

---

## 1. Decide what kind of thing it is

| It is… | Example | How guests reach it | What you write |
|---|---|---|---|
| A web page or web app | Excalidraw, the notes | through the hub's web server, behind its access levels | a manifest with a tile; the app's files; a route in the web server's config |
| A service with its own protocol, on its own port | IRC (6667), MQTT (1883), a TAK server | directly, on its port: the hub's login can't stand in front of it | a manifest with a `network` block; an installer section (`--with-NAME`) |
| Both | a server with a web admin | its page through the web server, its protocol on its port | both of the above |

Prefer the web server whenever the clients are browsers. A port of its own is for clients that are not
browsers (IRC apps, Meshtastic nodes, TAK apps), and it is a bigger promise: everyone on the network can try it.

## 2. Decide who should reach it

Two separate questions.

**Through the web server**, an app has one of the hub's access levels, chosen by the owner on /admin → Apps:

| Level | Who | Use it for |
|---|---|---|
| Public | everyone on the box's networks | things made for guests: maps, books, the chat's how-to page |
| Users | anyone signed in to an account on the box | shared work among the box's people |
| Private | the box's admins | the owner's tools (Syncthing, the terminal) |
| Off | nobody: no tile, no route | — |

Pick the safest level that still does the job as the default. The owner can move it.

**On its own port**, the hub's login does nothing. Who reaches it depends on:
- **The hotspot's floor** (Security → What a guest on the hotspot can reach). It is a default-drop firewall on
  the hotspot's interface. Your manifest says whether a new floor opens your service to hotspot guests
  (`"hotspot": "open"` or `"closed"`); the owner then has a switch for it either way.
- **The box's own network** (home or office): the floor doesn't touch it. Anything listening on `0.0.0.0` is
  reachable there. If the service has no reason to be on that network, bind it to the hotspot's address or to
  loopback.
- **The service's own authentication.** If it has accounts or keys, use them. If it has none (IRC, MQTT here),
  say so in its consent text and in `says`, and limit what one client can do (below).

## 3. The manifest (`apps.d/NN-name.json`)

Every app and add-on has one; the hub, the librarian, /status, /admin and the doctors read it. The format is
documented field by field at the top of `irate_box/hub/manifests.py`; the parts that matter for a service:

```json
{
  "api": 1,
  "id": "irc",
  "order": 65,
  "tile": { "icon": "💬🗨️", "name": "IRC chat", "desc": "Real-time chat in any IRC app", "href": "/irc.html" },
  "status": { "path": "/irc.html", "name": "IRC server (ngIRCd)", "port": 6667, "unit": "ngircd.service",
              "control": true, "note": "chat in any IRC app, on :6667" },
  "network": {
    "listen": [ { "proto": "tcp", "port": 6667 } ],
    "present": "/etc/ngircd/irate-box.conf",
    "hotspot": "open",
    "risk": "warn",
    "says": "The IRC chat: anyone on the network can join, with no accounts and nothing encrypted (limited to 5 connections an address)."
  },
  "addon": {
    "option": "--with-irc",
    "title": "IRC server (ngIRCd)",
    "summary": "A small IRC server on :6667 with a #lobby channel …",
    "consent": "The server answers anyone on the box's network on port 6667, with no accounts and no encryption … Add it?"
  }
}
```

- **`api`**: the manifest format it was written for. This irate-box reads formats `API_OLDEST` to `API`
  (`manifests.py`); a manifest outside that range is listed with the reason and not loaded (a local add-on), or
  stops the hub at start (a built-in one, which ships with the code). Leave it at the current value; it moves
  only when the format changes in a way an older hub would misread. Unlike ATAK's plugins, which declare one
  exact app version (`com.atakmap.app@4.6.0.CIV`) and must be rebuilt for every release, a manifest keeps
  working across irate-box releases until the format itself changes.
- **`status.unit`**: the systemd service that runs it. Required with `network`: the doctors watch this unit.
- **`network.listen`**: every port it answers on the network, with its protocol. Not loopback-only ports.
- **`network.present`**: a file that exists exactly when the add-on is installed: your installer writes it, your
  removal deletes it. Use your own config file, not the distribution package's.
- **`network.hotspot`**: `open` if the service is for hotspot guests (a chat, a map), `closed` if it is for the
  box's own network or its owner. Closed is the safer default when unsure.
- **`network.risk`**: `warn` if anyone who reaches the port can use it without a login or can read others'
  traffic; `ok` only if it is authenticated and encrypted.
- **`network.says`**: one plain sentence a non-expert understands: who can do what. It appears on the Security
  page, in the security doctor's item and in the floor's question before opening it.
- **`addon.consent`**: what the owner is agreeing to, said before it is added. Name the port, who can reach it,
  and what is missing (accounts, encryption).

## 4. The installer (`install.sh --with-NAME`, `--remove NAME`)

An add-on is installed and removed by `install.sh`, which /admin's Add-ons page runs for the owner. Follow the
IRC section (search `WITH_IRC`) and change each of these:

1. The usage text, the `WITH_NAME=0` default, the `--with-NAME` case and the `--remove` list.
2. `keep_addon NAME WITH_NAME <its config file> <unit>`, so a rerun without the option keeps it.
3. The Debian packages it needs, added to `pkgs` only with the option.
4. Its configuration, in a file of irate-box's own (`/etc/<pkg>/irate-box.conf`), never the package's own
   conffile: point the unit at it with a systemd drop-in
   (`/etc/systemd/system/<unit>.d/irate-box.conf`). A config the service rejects is said with `problem`, not
   left to fail at start (`ngircd --configtest`).
5. The unit in `units+=(…)` with the option, and its line in the end summary.
6. Its `--remove` branch: stop and disable the unit, delete the config and the drop-in; keep the package and
   any data.
7. `uninstall.sh`: the same, and stop the unit even when its config is gone (a package started on install
   would otherwise come back with its own example config).
8. The option in `manifests.py`'s `addon.option` check, `packaging/deb/control`'s Suggests, the README table and
   `about.html`'s licences.

## 5. Security: what the service should do itself

- **Run as its own unprivileged user**, with a sandbox: `ProtectSystem=strict` and only the paths it writes,
  `NoNewPrivileges`, `PrivateTmp`, `ProtectHome`, `ProtectProc=invisible`, a narrow `CapabilityBoundingSet`.
  A Debian unit that already has these is fine as it is; the security doctor names any line missing.
- **No default passwords, no admin account by default.** If it has an operator or admin role, leave it unset;
  the security doctor warns when one is defined (`probe_ngircd`).
- **Limits on an open service**: connections in all and per address, message sizes, what a client may join or
  publish (IRC: 64 connections, 5 per address, 10 channels; MQTT: an ACL to `msh/#`, 4 kB messages).
- **No waiting on the internet**: a box often has none. Turn off reverse DNS and ident lookups, update checks
  and telemetry.
- **Hide guests from each other**: cloak addresses, don't show user names that leak device names.
- **Nothing stored that it doesn't need**: no history by default; if it stores anything, the backup's
  settings and data levels must know where (`hub/backup.py`).
- A check of its own in the security doctor is welcome where the generic ones can't see a risk: add a
  `probe_<name>` to `secdoctor.py`'s `ADDON_PROBES` (the IRC one reads its config for an operator block, the
  limits, cloaking and DNS).

## 6. What the box then does by itself

With the `network` block in the manifest, and no code of your own:

- **The floor** knows the service by its id: a new floor opens it to hotspot guests if `hotspot` is `open` and it
  is installed; the floor's finding has an Open / Close switch for it; added after the floor was set, it shows
  as closed until opened (the rules are written only when the floor changes).
- **The Security page** names its port, rates the listener by `risk` and says `says`; it offers no "stop",
  since an add-on is removed on Add-ons.
- **The security doctor** merges its add-on finding and the page's port finding into one item, pointing at
  Add-ons with your `says`; its unit is among those checked for a sandbox.
- **The box doctor** treats its unit as irate-box's: expected running when its `--with-` option is in the
  install record, restartable from the page.

**Services nobody declared** (the owner's own, or the image's) are handled too, with options from the safest:
the Security page offers to stop and disable one, keeps it off the hotspot while the floor is on (and says so),
or opens its port to hotspot guests if the owner chooses; the box doctor gives each failed one a finding of its
own with the command to see why and a "Start it again" that asks first.

## 7. Tests

- `tests/sim_declared_services.py` shows the pattern: a made-up service in a manifests folder of the test's
  own, carried through the floor, the Security page, the cross-reference and both doctors. Copy it for a
  service with anything unusual.
- A container test, `tests/container/ct-NAME.sh`: install with the option, check the unit runs as its user from
  irate-box's config, the distribution's conffile untouched, a client really connects, a rerun keeps it,
  `--remove` takes it all away and the port closes (`ct-irc.sh`). Run it with `tests/container/run.sh`.
- A jsdom test for any page it adds (`tests/jsdom/dom-irc.cjs`).
- Before a pull request: `bash tests/run.sh`, the jsdom tests, `tests/local-ci.sh` (REUSE, the `.deb`).

## 8. For LLMs working on this

- Read `apps.d/65-irc.json`, the `WITH_IRC` parts of `install.sh` and `uninstall.sh`, and this file before
  starting; match them.
- Don't add a service's name to lists in `firewall.py`, `security.py`, `health.py`, `secdoctor.py` or
  `secdoctor_xref.py`: declare it in the manifest. Those lists hold only what predates the `network` block
  (MQTT, Syncthing) and the box's own parts.
- Never edit a distribution package's conffile, and never open a port by default that the owner hasn't been
  told about in `consent`.
- Comments say why the code is as it is, briefly. No names, dates, plan or issue references in code, page text
  or comments; those belong in the pull request.
- Keep the defaults the safest that still work, and give the owner the switch for the rest.
