<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# The MQTT explorer, a web add-on

A page that listens to the box's broker (`install.sh --with-mqtt`) at `ws://<box>/mqtt` and lists
the topics as messages arrive: how many, how big, how recent, and a topic's last 20 messages,
shown as JSON, text or hex. It only listens and never publishes. Nothing is kept once the page
closes.

Meshtastic encrypts most packets (topics with `/e/`), so for those it shows the traffic, not
what was said. Decoding them is the decoder bridge's job.

It is offered in **/admin → Add-ons → Web add-ons** (`addons/mqtt-explorer.json`). The librarian
fetches the hub's own repository at the commit the manifest pins, and
`irate_box/library/adapt_mqtt_explorer.py` keeps only this folder. That way it is served on the
add-on origin, with a Content-Security-Policy that lets it reach the broker and nothing else.

| File | What | Licence |
|---|---|---|
| `index.html`, `explorer.js`, `explorer.css` | the page: MQTT 3.1.1 over WebSocket, written for it with no library | MIT |
| `README.md` | this | CC-BY-SA-4.0 |

Tests: `tests/jsdom/dom-mqtt-explorer.cjs` (a stand-in broker), and `tests/sim_local_addons.py`
(the adapt script).
