// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// Device locks for saves and dropped files (store.py, "locks"): the browser side.
//
// No password is ever typed or sent. The page picks a random seed, keeps it in this browser's
// storage, and sends the hub only head = SHA-256 applied n times to it. Each later change sends
// the value one step earlier, which the hub checks and keeps; someone listening on the open
// WiFi sees only values already used, and cannot work back to the next one. SHA-256 is written
// out here because the browser's own crypto (crypto.subtle) is not available over plain HTTP;
// crypto.getRandomValues is, and makes the seed.
//
//   const lock = HubLock.create('drop');           // before upload: {seed, header}
//   xhr.setRequestHeader('X-Lock-New', lock.header);
//   HubLock.keep('drop', id, lock.seed);            // once the hub has given the id
//   const c = HubLock.change('drop', id, lockN);    // lockN: the hub's "lock_n" for the item
//   fetch(url, { method: 'DELETE', headers: c.headers }); then c.done() if it succeeded
//
// The same file works for the Mermaid and Excalidraw galleries (kind 'saves').
(() => {
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  const W = new Uint32Array(64);

  // SHA-256 of exactly 32 bytes (all this chain ever hashes): one padded 64-byte block.
  function sha256x32(bytes) {
    const block = new Uint8Array(64);
    block.set(bytes);
    block[32] = 0x80;
    block[62] = 0x01; // message length: 256 bits
    const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    for (let i = 0; i < 16; i++) {
      W[i] = (block[i * 4] << 24) | (block[i * 4 + 1] << 16) | (block[i * 4 + 2] << 8) | block[i * 4 + 3];
    }
    for (let i = 16; i < 64; i++) {
      const a = W[i - 15], b = W[i - 2];
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
      W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, k] = h;
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const t1 = (k + S1 + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      k = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    const out = new Uint8Array(32);
    [a, b, c, d, e, f, g, k].forEach((v, i) => {
      const x = (v + h[i]) >>> 0;
      out[i * 4] = x >>> 24; out[i * 4 + 1] = (x >>> 16) & 255; out[i * 4 + 2] = (x >>> 8) & 255; out[i * 4 + 3] = x & 255;
    });
    return out;
  }

  const hex = (b) => [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  const unhex = (s) => Uint8Array.from(s.match(/../g).map((x) => parseInt(x, 16)));

  // SHA-256 applied `times` times to the seed.
  function walk(seedHex, times) {
    let v = unhex(seedHex);
    for (let i = 0; i < times; i++) v = sha256x32(v);
    return hex(v);
  }

  const N = 4096;        // changes one chain allows; a new chain is started well before the end
  const RENEW_AT = 64;
  const slot = (kind, id) => `hublock:${kind}:${id}`;

  function newSeed() {
    return hex(crypto.getRandomValues(new Uint8Array(32)));
  }

  const HubLock = {
    N,
    sha256x32, walk,  // exposed for tests
    // A new lock: keep `seed` (HubLock.keep) once the item has an id; send `header` as X-Lock-New.
    create() {
      const seed = newSeed();
      return { seed, header: `${walk(seed, N)}:${N}` };
    },
    keep(kind, id, seed) {
      try { localStorage.setItem(slot(kind, id), seed); } catch (_) { /* private mode: the lock is lost with the tab */ }
    },
    seedOf(kind, id) {
      try { return localStorage.getItem(slot(kind, id)); } catch (_) { return null; }
    },
    has(kind, id) { return !!HubLock.seedOf(kind, id); },
    forget(kind, id) {
      try { localStorage.removeItem(slot(kind, id)); } catch (_) { /* nothing to forget */ }
    },
    // Headers for one change to a locked item. lockN: the hub's "lock_n" for it. Call done()
    // after the hub accepted the change, so a chain renewed on the way is kept.
    change(kind, id, lockN) {
      const seed = HubLock.seedOf(kind, id);
      if (!seed || !(lockN >= 1)) return null;
      const headers = { 'X-Lock': walk(seed, lockN - 1) };
      let next = null;
      if (lockN <= RENEW_AT) {
        next = HubLock.create();
        headers['X-Lock-Next'] = next.header;
      }
      return { headers, done: () => { if (next) HubLock.keep(kind, id, next.seed); } };
    },
    // The seed as text, to carry the lock to another device (whoever has it can change the item).
    exportKey(kind, id) { return HubLock.seedOf(kind, id); },
    importKey(kind, id, text) {
      const t = String(text || '').trim().toLowerCase();
      if (!/^[0-9a-f]{64}$/.test(t)) return false;
      HubLock.keep(kind, id, t);
      return true;
    },
  };
  window.HubLock = HubLock;
})();
