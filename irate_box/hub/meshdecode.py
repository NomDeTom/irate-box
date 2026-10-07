# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Meshtastic's MQTT packets, decoded on the box (next-work plan step 18, the decoder bridge).

A node or a phone's MQTT proxy publishes each packet it hears as a ServiceEnvelope protobuf to
msh/<region>/2/e/<channel>/<gateway>. The packet inside is usually encrypted: AES-CTR with the
channel's key, the nonce being the packet's id (8 bytes, little-endian) and its sender (4 bytes,
little-endian) and four zero bytes. Decrypted, it is a Data message: a port number (what kind of
packet) and its payload (a text message, a position, a node's name, its telemetry …).

Stdlib only, on purpose: the protobuf wire format is read by hand for the few messages that
matter here (field numbers from meshtastic/protobufs, mesh.proto, mqtt.proto, telemetry.proto),
and AES (FIPS-197, the encrypting direction, which is all CTR needs) is written out below, checked
against the standard's test vectors. A box needs no python3-protobuf, generated code, or
python3-cryptography for it; a few packets a second is nothing for it even on the Lyra.

Keys are the owner's to give (/admin): a channel's name and its key, as Meshtastic shows it
(base64; "AQ==" means Meshtastic's public default key, LongFast's, which anyone can read).
"""

import base64
import struct

# --- the protobuf wire format ------------------------------------------------------------------

def _varint(buf, i):
    out = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        out |= (b & 0x7F) << shift
        if not b & 0x80:
            return out, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def fields(buf):
    """[(field number, value)]: varints as ints, 32/64-bit fixed as raw bytes, and
    length-delimited as bytes. A malformed message raises ValueError."""
    out, i = [], 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if num == 0:
            raise ValueError("field 0")
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        elif wt == 2:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
        else:
            raise ValueError(f"wire type {wt}")
        if i > len(buf):
            raise ValueError("truncated field")
        out.append((num, v))
    return out


def _u32(b):
    return struct.unpack("<I", b)[0]


def _i32(b):
    return struct.unpack("<i", b)[0]


def _f32(b):
    return round(struct.unpack("<f", b)[0], 3)


def _sint(v):
    return v - (1 << 64) if v >= 1 << 63 else v


def _text(b):
    return b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else ""


# --- AES (FIPS-197), the encrypting direction -----------------------------------------------------

def _sbox():
    sbox, p, q = [0] * 256, 1, 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) & 0xFF ^ ((q << 2) | (q >> 6)) & 0xFF ^ ((q << 3) | (q >> 5)) & 0xFF ^ ((q << 4) | (q >> 4)) & 0xFF
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


SBOX = _sbox()


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand(key):
    nk = len(key) // 4
    rounds = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (rounds + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xt(rcon)
        elif nk > 6 and i % nk == 4:
            t = [SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(rounds + 1)]


def aes_encrypt_block(round_keys, block):
    s = [b ^ k for b, k in zip(block, round_keys[0])]
    last = len(round_keys) - 1
    for r in range(1, last + 1):
        s = [SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]  # ShiftRows (column-major state)
        if r != last:
            m = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                m += [a[i] ^ t ^ _xt(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            s = m
        s = [b ^ k for b, k in zip(s, round_keys[r])]
    return bytes(s)


def aes_ctr(key, nonce, data):
    """AES-CTR as Meshtastic uses it: the 16-byte counter block starts at `nonce` and counts up
    big-endian in its last four bytes."""
    rk = _expand(key)
    out = bytearray()
    counter = int.from_bytes(nonce, "big")
    for i in range(0, len(data), 16):
        stream = aes_encrypt_block(rk, counter.to_bytes(16, "big"))
        out += bytes(a ^ b for a, b in zip(data[i:i + 16], stream))
        counter = (counter & ~0xFFFFFFFF) | ((counter + 1) & 0xFFFFFFFF)
    return bytes(out)


# --- keys and channels -------------------------------------------------------------------------------

DEFAULT_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")  # "AQ==": Meshtastic's public default


def expand_key(b64):
    """A channel's key as Meshtastic gives it (base64): empty for none, one byte n for the default
    key's variant n ("AQ==" is the default itself), or 16 or 32 bytes as they are."""
    raw = base64.b64decode(b64 or "", validate=True)
    if not raw or raw == b"\x00":
        return b""
    if len(raw) == 1:
        return DEFAULT_PSK[:-1] + bytes([(DEFAULT_PSK[-1] + raw[0] - 1) & 0xFF])
    if len(raw) not in (16, 32):
        raise ValueError("a channel key is 0, 1, 16 or 32 bytes")
    return raw


def channel_hash(name, key):
    """The one-byte hash a packet carries in place of its channel's name: the XOR of the name's
    bytes and the key's."""
    h = 0
    for b in name.encode() + key:
        h ^= b
    return h


def nonce(packet_id, sender):
    return struct.pack("<QI", packet_id, sender) + b"\0\0\0\0"


# --- the messages ---------------------------------------------------------------------------------------

PORTS = {1: "text", 3: "position", 4: "nodeinfo", 5: "routing", 6: "admin", 8: "waypoint", 32: "reply",
         34: "paxcounter", 64: "serial", 65: "store_forward", 66: "range_test", 67: "telemetry", 70: "traceroute",
         71: "neighborinfo", 73: "map_report"}


def decode_data(buf):
    d = {}
    for num, v in fields(buf):
        if num == 1:
            d["portnum"] = v
        elif num == 2:
            d["payload"] = v
        elif num == 4:
            d["dest"] = _u32(v)
        elif num == 5:
            d["source"] = _u32(v)
        elif num == 6:
            d["request_id"] = _u32(v)
        elif num == 7:
            d["reply_id"] = _u32(v)
        elif num == 8:
            d["emoji"] = _u32(v)
    if "portnum" not in d:
        raise ValueError("no port number: not a Data message (a wrong key?)")
    return d


def decode_payload(port, buf):
    """What a Data payload says, for the ports worth showing."""
    if port == 1:
        return {"text": _text(buf)}
    f = fields(buf)
    if port == 3:
        out = {}
        for num, v in f:
            if num == 1:
                out["lat"] = _i32(v) / 1e7
            elif num == 2:
                out["lon"] = _i32(v) / 1e7
            elif num == 3:
                out["alt"] = _sint(v)
            elif num == 4:
                out["time"] = _u32(v)
            elif num == 14:
                out["sats"] = v
        return out
    if port == 4:
        out = {}
        for num, v in f:
            if num == 1:
                out["id"] = _text(v)
            elif num == 2:
                out["long_name"] = _text(v)[:40]
            elif num == 3:
                out["short_name"] = _text(v)[:8]
            elif num == 5:
                out["hw_model"] = v
            elif num == 7:
                out["role"] = v
        return out
    if port == 67:
        out = {}
        for num, v in f:
            if num == 2 and isinstance(v, bytes):     # device metrics
                for n2, v2 in fields(v):
                    key = {1: "battery", 2: "voltage", 3: "channel_util", 4: "air_util_tx", 5: "uptime"}.get(n2)
                    if key:
                        out[key] = _f32(v2) if isinstance(v2, bytes) and len(v2) == 4 else v2
            elif num == 3 and isinstance(v, bytes):   # environment metrics
                for n2, v2 in fields(v):
                    key = {1: "temperature", 2: "humidity", 3: "pressure"}.get(n2)
                    if key and isinstance(v2, bytes) and len(v2) == 4:
                        out[key] = _f32(v2)
        return out
    return {}


def decode_packet(buf):
    p = {}
    for num, v in fields(buf):
        if num == 1:
            p["from"] = _u32(v)
        elif num == 2:
            p["to"] = _u32(v)
        elif num == 3:
            p["channel"] = v
        elif num == 4:
            p["decoded"] = v
        elif num == 5:
            p["encrypted"] = v
        elif num == 6:
            p["id"] = _u32(v)
        elif num == 7:
            p["rx_time"] = _u32(v)
        elif num == 8:
            p["rx_snr"] = _f32(v)
        elif num == 9:
            p["hop_limit"] = v
        elif num == 12:
            p["rx_rssi"] = _sint(v)
        elif num == 15:
            p["hop_start"] = v
    return p


def decode_envelope(buf, keys):
    """A ServiceEnvelope off MQTT, decoded as far as the keys allow: {from, to, id, channel_id,
    gateway, hops, port, (what the payload says) | encrypted: True}. keys: {channel name: key
    bytes}; the packet's channel hash picks the key, and a key that yields no Data is not taken."""
    env = {}
    for num, v in fields(buf):
        if num == 1:
            env["packet"] = decode_packet(v)
        elif num == 2:
            env["channel_id"] = _text(v)
        elif num == 3:
            env["gateway"] = _text(v)
    p = env.get("packet")
    if not p:
        raise ValueError("no packet in the envelope")
    out = {"from": f"!{p.get('from', 0):08x}", "to": "all" if p.get("to") == 0xFFFFFFFF else f"!{p.get('to', 0):08x}",
           "id": p.get("id"), "channel_id": env.get("channel_id", ""), "gateway": env.get("gateway", ""),
           "rx_snr": p.get("rx_snr"), "rx_rssi": p.get("rx_rssi"),
           "hops": (p["hop_start"] - p.get("hop_limit", 0)) if p.get("hop_start") else None}
    data = None
    if "decoded" in p:
        data = decode_data(p["decoded"])
    elif "encrypted" in p:
        named = keys.get(out["channel_id"])
        tries = ([named] if named is not None else []) + [k for name, k in keys.items() if channel_hash(name, k) == p.get("channel") and k != named]
        for key in tries:
            if not key:
                continue
            try:
                data = decode_data(aes_ctr(key, nonce(p.get("id", 0), p.get("from", 0)), p["encrypted"]))
                break
            except ValueError:
                data = None
        if data is None:
            out["encrypted"] = True
            return out
    out["port"] = PORTS.get(data["portnum"], f"port {data['portnum']}")
    try:
        out.update(decode_payload(data["portnum"], data.get("payload", b"")))
    except ValueError:
        out["undecodable"] = True
    return out
