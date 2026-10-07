# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The decoder bridge (next-work plan step 18), offline: AES against FIPS-197 and NIST SP 800-38A's
CTR vectors; Meshtastic's keys ("AQ==" and its variants, 16 and 32 bytes) and channel hashes;
packets built here as the firmware builds them (a Data message, AES-CTR with the packet's id and
sender as nonce, in a MeshPacket in a ServiceEnvelope) decoded back: text, position, node info,
telemetry; a wrong key left as "encrypted"; what the bridge keeps (nodes, packets, texts for /admin
only, 7 days); the channels file private and never shown; and the MQTT side against a stand-in
broker on a local socket. python3 tests/sim_mesh.py"""
import base64, json, os, socket, stat, struct, sys, tempfile, threading, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="mesh-"))
os.environ["HUB_STATE_DIR"] = str(T)
sys.path.insert(0, str(REPO))
from irate_box.hub import meshdecode as M, meshbridge as B  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

pt = bytes.fromhex("00112233445566778899aabbccddeeff")
check("AES-128 and AES-256: FIPS-197's vectors", M.aes_encrypt_block(M._expand(bytes(range(16))), pt).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"
      and M.aes_encrypt_block(M._expand(bytes(range(32))), pt).hex() == "8ea2b7ca516745bfeafc49904b496089")
ctr = M.aes_ctr(bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c"), bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"),
                bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51"))
check("AES-CTR: NIST SP 800-38A F.5.1, two blocks", ctr.hex() == "874d6191b620e3261bef6864990db6ce9806f66b7970fdff8617187bb9fffdff", ctr.hex())
check("keys: AQ== is the public default, AQ variants, none, 16 and 32 bytes", M.expand_key("AQ==") == M.DEFAULT_PSK
      and M.expand_key(base64.b64encode(b"\x02").decode())[-1] == M.DEFAULT_PSK[-1] + 1 and M.expand_key("") == b""
      and len(M.expand_key(base64.b64encode(bytes(16)).decode())) == 16 and len(M.expand_key(base64.b64encode(bytes(32)).decode())) == 32)
check("  a channel's hash: XOR of its name and key (LongFast with the default key is 8)", M.channel_hash("LongFast", M.DEFAULT_PSK) == 8)

# Packets, built as the firmware builds them.
def vint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)
def fld(num, v):
    if isinstance(v, bytes):
        return vint(num << 3 | 2) + vint(len(v)) + v
    if isinstance(v, tuple):  # (fixed32 bytes)
        return vint(num << 3 | 5) + v[0]
    return vint(num << 3) + vint(v & ((1 << 64) - 1))
fx = lambda n: (struct.pack("<I", n),)  # noqa: E731
fs = lambda n: (struct.pack("<i", n),)  # noqa: E731
ff = lambda x: (struct.pack("<f", x),)  # noqa: E731
def envelope(port, payload, key, sender=0x433d0a1c, pid=0x1234abcd, channel="LongFast", plain=False, hashed_as=None):
    data = fld(1, port) + fld(2, payload)
    packet = fld(1, fx(sender)) + fld(2, fx(0xFFFFFFFF)) + fld(6, fx(pid)) + fld(8, ff(6.25)) + fld(9, 2) + fld(12, -97) + fld(15, 3)
    if plain:
        packet += fld(4, data)
    else:
        packet += fld(3, M.channel_hash(hashed_as or channel, key)) + fld(5, M.aes_ctr(key, M.nonce(pid, sender), data))
    return fld(1, packet) + fld(2, channel.encode()) + fld(3, b"!aabbccdd")
K = {"LongFast": M.DEFAULT_PSK}
d = M.decode_envelope(envelope(1, "hello mesh 👋".encode(), M.DEFAULT_PSK), K)
check("a text message on LongFast: decrypted with the default key", d["port"] == "text" and d["text"] == "hello mesh 👋" and d["from"] == "!433d0a1c"
      and d["to"] == "all" and d["gateway"] == "!aabbccdd" and d["hops"] == 1 and d["rx_snr"] == 6.25 and d["rx_rssi"] == -97, d)
pos = fld(1, fs(int(51.5007 * 1e7))) + fld(2, fs(int(-0.1246 * 1e7))) + fld(3, 25) + fld(14, 9)
d = M.decode_envelope(envelope(3, pos, M.DEFAULT_PSK), K)
check("a position", d["port"] == "position" and abs(d["lat"] - 51.5007) < 1e-6 and abs(d["lon"] + 0.1246) < 1e-6 and d["alt"] == 25 and d["sats"] == 9, d)
user = fld(1, b"!433d0a1c") + fld(2, b"Base camp") + fld(3, b"BASE") + fld(5, 43) + fld(7, 2)
d = M.decode_envelope(envelope(4, user, M.DEFAULT_PSK), K)
check("a node's name and hardware", d["port"] == "nodeinfo" and d["long_name"] == "Base camp" and d["short_name"] == "BASE" and d["hw_model"] == 43, d)
tel = fld(2, fld(1, 87) + fld(2, ff(4.05)) + fld(3, ff(12.5)) + fld(5, 3600))
d = M.decode_envelope(envelope(67, tel, M.DEFAULT_PSK), K)
check("telemetry", d["port"] == "telemetry" and d["battery"] == 87 and d["voltage"] == 4.05 and d["uptime"] == 3600, d)
mine = os.urandom(32)
d = M.decode_envelope(envelope(1, b"secret", mine, channel="Family"), {"Family": mine})
check("a private channel's 32-byte key", d.get("text") == "secret", d)
d = M.decode_envelope(envelope(1, b"secret", mine, channel="Family"), K)
check("  without its key: left encrypted, nothing guessed", d.get("encrypted") and "text" not in d, d)
d = M.decode_envelope(envelope(1, b"x", mine, channel="Renamed", hashed_as="Family"), {"Family": mine})
check("  the envelope naming it otherwise: found by the packet's channel hash", d.get("text") == "x", d)
check("not encrypted (a node sending decoded packets): read as it is", M.decode_envelope(envelope(1, b"plain", b"", plain=True), {})["text"] == "plain")
for bad in (b"\xff\xff", b"", fld(2, b"x")):
    try:
        M.decode_envelope(bad, K); check(f"garbage refused: {bad!r}", False)
    except ValueError:
        check(f"garbage refused: {bad!r}", True)

# What the bridge keeps.
clock = {"t": 1_800_000_000.0}
h = B.Heard(now=lambda: clock["t"])
br = B.Bridge(heard=h)
B.add_channel("LongFast", "AQ==")
br.handle("msh/EU_868/2/e/LongFast/!aabbccdd", envelope(4, user, M.DEFAULT_PSK))
br.handle("msh/EU_868/2/e/LongFast/!aabbccdd", envelope(3, pos, M.DEFAULT_PSK))
br.handle("msh/EU_868/2/e/LongFast/!aabbccdd", envelope(1, b"meet at the gate", M.DEFAULT_PSK))
br.handle("msh/EU_868/2/e/Family/!aabbccdd", envelope(1, b"secret", mine, channel="Family"))
br.handle("msh/EU_868/2/json/LongFast/!aabbccdd", b'{"x":1}')
v = h.view(texts=False)
node = v["nodes"][0]
check("a node: its name, position, how it was heard, its packets", node["id"] == "!433d0a1c" and node["long_name"] == "Base camp" and node["position"]["lat"] == 51.5007
      and node["snr"] == 6.25 and node["packets"] == 4, node)
check("  counts by kind, the unreadable as encrypted; JSON topics not this format", v["counts"] == {"nodeinfo": 1, "position": 1, "text": 1, "encrypted": 1}, v["counts"])
check("  the public view: no message text", all("text" not in p for p in v["packets"]))
check("  /admin's: the message", any(p.get("text") == "meet at the gate" for p in h.view(texts=True)["packets"]))
h.prune_and_save(force=True)
check("nodes saved; a node unheard for 7 days dropped", json.loads(B.NODES.read_text())["nodes"] and (clock.update(t=clock["t"] + 8 * 86400), h.prune_and_save(force=True),
      json.loads(B.NODES.read_text())["nodes"] == {})[-1])
check("the channels: private to the hub, never a key in what a page gets", stat.S_IMODE(B.CHANNELS.stat().st_mode) == 0o600
      and B.channels_view() == [{"name": "LongFast", "public_key": True, "no_key": False}] and "AQ==" not in json.dumps(B.channels_view()))
for bad in (("", "AQ=="), ("a/b", "AQ=="), ("x", "not base64!"), ("x", base64.b64encode(bytes(5)).decode())):
    try:
        B.add_channel(*bad); check(f"a channel refused: {bad}", False)
    except ValueError:
        check(f"a channel refused: {bad}", True)

# The MQTT side, against a stand-in broker.
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
B.BROKER = srv.getsockname()
got = {}
def broker():
    c, _ = srv.accept()
    kind, body = B._read_packet(c); got["connect"] = (kind, body)
    c.sendall(b"\x20\x02\x00\x00")
    kind, body = B._read_packet(c); got["subscribe"] = (kind, body)
    c.sendall(b"\x90\x03\x00\x01\x00")
    topic = b"msh/EU_868/2/e/LongFast/!aabbccdd"
    pub = struct.pack(">H", len(topic)) + topic + envelope(1, b"over the wire", M.DEFAULT_PSK)
    c.sendall(B._packet(0x30, pub))
    time.sleep(0.3); c.close()
threading.Thread(target=broker, daemon=True).start()
h2 = B.Heard()
br2 = B.Bridge(heard=h2)
try:
    br2.once()
except ConnectionError:
    pass
check("MQTT: CONNECT as 3.1.1, SUBSCRIBE to msh/#, a PUBLISH decoded", got["connect"][0] == 0x10 and b"MQTT" in got["connect"][1]
      and got["subscribe"][0] == 0x82 and b"msh/#" in got["subscribe"][1] and any(p.get("text") == "over the wire" for p in h2.view(texts=True)["packets"]), got)
srv.close()
src = (REPO / "irate_box/hub/meshbridge.py").read_text()
check("it never publishes", "0x30" not in src.split("class Bridge")[1])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
