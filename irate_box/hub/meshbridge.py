# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The decoder bridge: a thread in the hub that listens to the box's own
MQTT broker (install.sh --with-mqtt), decodes Meshtastic's packets with the channel keys the
owner gave (meshdecode.py), and keeps what the traffic page shows.

  - each node's newest: its name, hardware, position, battery and telemetry, when last heard,
    how it was heard (gateway, SNR, RSSI, hops); $HUB_STATE_DIR/mesh/nodes.json, saved every
    minute, a node dropped after 7 days unheard
  - the last 300 packets, in memory only (what kind, from whom, on which channel)
  - text messages are guests' words: kept with the packets, in memory, shown on /admin only

It speaks just enough MQTT 3.1.1 to subscribe (the broker is on this box: 127.0.0.1:1883, which
allows anyone msh/#). It never publishes. Keys: $HUB_STATE_DIR/mesh/channels.json, the hub's,
0600, never sent back to a page. Stdlib only.
"""

import base64
import collections
import json
import os
import socket
import struct
import threading
import time
from pathlib import Path

from irate_box.hub import meshdecode

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
DIR = STATE / "mesh"
NODES = DIR / "nodes.json"
CHANNELS = DIR / "channels.json"
BROKER = (os.environ.get("HUB_MQTT_HOST", "127.0.0.1"), int(os.environ.get("HUB_MQTT_PORT", "1883")))
TOPIC = "msh/#"
KEEP_PACKETS = 300
NODE_DAYS = 7
SAVE_EVERY = 60


# --- the owner's channels ---------------------------------------------------------------------------

def channels():
    try:
        data = json.loads(CHANNELS.read_text())
        return [c for c in data.get("channels", []) if isinstance(c, dict) and c.get("name")]
    except (OSError, ValueError, AttributeError):
        return []


def keys():
    out = {}
    for c in channels():
        try:
            out[c["name"]] = meshdecode.expand_key(c.get("key", ""))
        except ValueError:
            continue
    return out


def _save_channels(chs):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHANNELS.with_name(".channels.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"channels": chs}, fh)
    os.replace(tmp, CHANNELS)


def add_channel(name, key):
    name = str(name or "").strip()
    if not (1 <= len(name) <= 11) or any(c in name for c in "/#+\0"):
        raise ValueError("name: the channel's name as Meshtastic shows it (up to 11 characters)")
    try:
        meshdecode.expand_key(key)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"key: base64, as Meshtastic shows it ({exc})")
    chs = [c for c in channels() if c["name"] != name] + [{"name": name, "key": key}]
    if len(chs) > 16:
        raise ValueError("at most 16 channels")
    _save_channels(chs)


def remove_channel(name):
    _save_channels([c for c in channels() if c["name"] != name])


def channels_view():
    """The channels for a page: names, and whether the key is the public default, never a key."""
    return [{"name": c["name"], "public_key": c.get("key") == "AQ==", "no_key": not c.get("key")} for c in channels()]


# --- what was heard ------------------------------------------------------------------------------------

class Heard:
    def __init__(self, now=time.time):
        self.now = now
        self.lock = threading.Lock()
        self.packets = collections.deque(maxlen=KEEP_PACKETS)
        self.counts = collections.Counter()
        self.started = now()
        try:
            self.nodes = json.loads(NODES.read_text()).get("nodes", {})
        except (OSError, ValueError, AttributeError):
            self.nodes = {}
        self.saved = 0

    def add(self, topic, d):
        t = self.now()
        with self.lock:
            port = d.get("port") or ("encrypted" if d.get("encrypted") else "other")
            self.counts[port] += 1
            n = self.nodes.setdefault(d["from"], {"first": t})
            n.update(last=t, gateway=d.get("gateway"), snr=d.get("rx_snr"), rssi=d.get("rx_rssi"), hops=d.get("hops"),
                     channel=d.get("channel_id"), packets=n.get("packets", 0) + 1)
            if port == "nodeinfo":
                n.update({k: d[k] for k in ("long_name", "short_name", "hw_model", "role") if k in d})
            elif port == "position" and "lat" in d and "lon" in d:
                n["position"] = {"lat": d["lat"], "lon": d["lon"], "alt": d.get("alt"), "at": t}
            elif port == "telemetry":
                n["telemetry"] = dict({k: d[k] for k in ("battery", "voltage", "channel_util", "air_util_tx", "uptime",
                                                          "temperature", "humidity", "pressure") if k in d}, at=t)
            p = {"at": t, "from": d["from"], "to": d.get("to"), "port": port, "channel": d.get("channel_id"),
                 "topic": topic[:120], "gateway": d.get("gateway")}
            if port == "text":
                p["text"] = d.get("text", "")[:240]
            self.packets.append(p)

    def prune_and_save(self, force=False):
        t = self.now()
        if not force and t - self.saved < SAVE_EVERY:
            return
        with self.lock:
            for k in [k for k, n in self.nodes.items() if t - n.get("last", 0) > NODE_DAYS * 86400]:
                del self.nodes[k]
            data = json.dumps({"nodes": self.nodes})
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = NODES.with_name(".nodes.json.tmp")
        tmp.write_text(data)
        os.replace(tmp, NODES)
        self.saved = t

    def view(self, texts=False):
        """What the traffic page shows. texts: the messages too (/admin only)."""
        with self.lock:
            nodes = sorted(({"id": k, **n} for k, n in self.nodes.items()), key=lambda n: -n.get("last", 0))
            packets = [dict(p) for p in self.packets]
            counts = dict(self.counts)
        if not texts:
            for p in packets:
                p.pop("text", None)
        return {"nodes": nodes, "packets": packets[-100:][::-1], "counts": counts, "since": self.started}


# --- the MQTT side --------------------------------------------------------------------------------------

def _string(s):
    b = s.encode()
    return struct.pack(">H", len(b)) + b


def _packet(kind, body):
    n, length = len(body), bytearray()
    while True:
        byte, n = n % 128, n // 128
        length.append(byte | (0x80 if n else 0))
        if not n:
            break
    return bytes([kind]) + bytes(length) + body


def _read_packet(sock):
    head = sock.recv(1)
    if not head:
        raise ConnectionError("the broker closed the connection")
    length = mult = 0
    for _ in range(4):
        b = sock.recv(1)
        if not b:
            raise ConnectionError("the broker closed the connection")
        length += (b[0] & 127) * (128 ** mult)
        mult += 1
        if not b[0] & 128:
            break
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            raise ConnectionError("the broker closed the connection")
        body += chunk
    return head[0], body


def topic_and_payload(flags, body):
    n = struct.unpack(">H", body[:2])[0]
    topic = body[2:2 + n].decode("utf-8", "replace")
    start = 2 + n + (2 if (flags >> 1) & 3 else 0)
    return topic, body[start:]


class Bridge(threading.Thread):
    """Connects, subscribes, decodes; reconnects after a pause when the broker goes or isn't there."""

    def __init__(self, heard=None):
        super().__init__(daemon=True, name="meshbridge")
        self.heard = heard or Heard()
        self.state = "starting"
        self.error = None
        self.stopping = threading.Event()

    def handle(self, topic, payload):
        if "/2/e/" not in topic and "/2/c/" not in topic:
            return  # the JSON and map topics: another format, not decoded here
        try:
            d = meshdecode.decode_envelope(payload, keys())
        except (ValueError, struct.error):
            self.heard.counts["undecodable"] += 1
            return
        self.heard.add(topic, d)

    def once(self):
        with socket.create_connection(BROKER, timeout=10) as sock:
            sock.sendall(_packet(0x10, _string("MQTT") + bytes([4, 0x02]) + struct.pack(">H", 60) + _string(f"irate-box-bridge-{os.getpid()}")))
            kind, body = _read_packet(sock)
            if kind >> 4 != 2 or len(body) < 2 or body[1] != 0:
                raise ConnectionError("the broker refused the connection")
            sock.sendall(_packet(0x82, struct.pack(">H", 1) + _string(TOPIC) + b"\0"))
            self.state, self.error = "listening", None
            sock.settimeout(30)
            last_ping = time.time()
            while not self.stopping.is_set():
                try:
                    kind, body = _read_packet(sock)
                except socket.timeout:
                    kind = None
                if kind is not None and kind >> 4 == 3:
                    self.handle(*topic_and_payload(kind & 15, body))
                if time.time() - last_ping > 30:
                    sock.sendall(b"\xc0\x00")
                    last_ping = time.time()
                self.heard.prune_and_save()

    def run(self):
        wait = 5
        while not self.stopping.is_set():
            try:
                self.once()
            except (OSError, ConnectionError, ValueError) as exc:
                # After a connection that worked, start the back-off again from the shortest wait.
                wait = 5 if self.state == "listening" else min(wait * 2, 300)
                self.state, self.error = "waiting", str(exc)
            self.stopping.wait(wait)
