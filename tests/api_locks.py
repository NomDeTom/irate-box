# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Device locks through the API of a running hub (PORT=… python3 tests/api_locks.py), or start
one: tests/run.sh does. Checks saves, renames, deletes, renewal and the drop, and that no lock
value leaks into a listing."""
import hashlib, json, os, secrets, sys, urllib.request, urllib.error
H = f"http://127.0.0.1:{os.environ['PORT']}"
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def req(method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = dict(headers or {})
    if method == "POST" and path.startswith("/admin"):
        headers.setdefault("X-Irate-Admin", "1")  # as the /admin page sends it (server.py _forged)
    r = urllib.request.Request(H + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
def walk(seed, n):
    v = bytes.fromhex(seed)
    for _ in range(n): v = hashlib.sha256(v).digest()
    return v.hex()
def new_lock(n=4096):
    seed = secrets.token_hex(32); return seed, f"{walk(seed, n)}:{n}"
def leaks(obj):
    return "lock" in json.dumps(obj).replace('"locked"', "").replace('"lock_n"', "") or "head" in json.dumps(obj)

# saves: unlocked behave as before
st, m = req("POST", "/api/saves", {"id": "plain1", "kind": "t", "name": "a", "state": {"x": 1}})
st2, _ = req("POST", "/api/saves", {"id": "plain1", "kind": "t", "name": "b", "state": {"x": 2}})
check("unlocked save: anyone can overwrite (as before)", st == 201 and st2 == 201 and m["locked"] is False)
# locked
seed, hdr = new_lock()
st, m = req("POST", "/api/saves", {"id": "mine1", "kind": "t", "name": "mine", "state": {"v": 1}}, {"X-Lock-New": hdr})
check("locked save created", st == 201 and m["locked"] and m["lock_n"] == 4096 and not leaks(m), m)
st, g = req("GET", "/api/saves/mine1"); check("GET shows locked, no lock value", g["locked"] and g["lock_n"] == 4096 and not leaks(g), g)
st, l = req("GET", "/api/saves"); check("listing shows locked, no lock value", any(x["id"] == "mine1" and x["locked"] for x in l["saves"]) and not leaks(l))
st, e = req("POST", "/api/saves", {"id": "mine1", "kind": "t", "name": "vandal", "state": {"v": 666}})
check("overwrite without proof refused (403, lock_n given)", st == 403 and e.get("locked") and e.get("lock_n") == 4096, (st, e))
st, _ = req("PATCH", "/api/saves/mine1", {"name": "vandal"}); check("rename without proof refused", st == 403)
st, _ = req("DELETE", "/api/saves/mine1"); check("delete without proof refused", st == 403)
st, _ = req("DELETE", "/api/saves/mine1", headers={"X-Lock": "00" * 32}); check("delete with a wrong proof refused", st == 403)
p1 = walk(seed, 4095)
st, m = req("POST", "/api/saves", {"id": "mine1", "kind": "t", "name": "mine v2", "state": {"v": 2}}, {"X-Lock": p1})
check("overwrite with the proof accepted; chain advances", st == 201 and m["lock_n"] == 4095 and m["locked"], m)
st, _ = req("PATCH", "/api/saves/mine1", {"name": "replayed"}, {"X-Lock": p1}); check("replaying a seen proof refused", st == 403)
st, m = req("PATCH", "/api/saves/mine1", {"name": "renamed"}, {"X-Lock": walk(seed, 4094)}); check("rename with the next proof", st == 200 and m["name"] == "renamed" and m["lock_n"] == 4094)
# renewal from a short chain
seed2, hdr2 = new_lock(3)
req("POST", "/api/saves", {"id": "short", "kind": "t", "name": "s", "state": {}}, {"X-Lock-New": hdr2})
seed3, hdr3 = new_lock()
st, m = req("PATCH", "/api/saves/short", {"name": "s2"}, {"X-Lock": walk(seed2, 2), "X-Lock-Next": hdr3})
check("renewal: a valid proof installs a new chain", st == 200 and m["lock_n"] == 4096, m)
st, m = req("PATCH", "/api/saves/short", {"name": "s3"}, {"X-Lock": walk(seed3, 4095)}); check("the new chain works", st == 200 and m["lock_n"] == 4095)
st, _ = req("PATCH", "/api/saves/short", {"name": "s4"}, {"X-Lock": walk(seed2, 1)}); check("the old chain no longer works", st == 403)
# unlock
st, m = req("PATCH", "/api/saves/short", {"unlock": True}, {"X-Lock": walk(seed3, 4094)}); check("unlock with a proof", st == 200 and m["locked"] is False)
# admin moderation passes locks (hub reached directly: no front in this test)
st, a = req("GET", "/admin/store"); check("admin listing: locked shown, no lock value", any(x["id"] == "mine1" and x["locked"] for x in a["saves"]) and not leaks(a))
st, _ = req("POST", "/admin/store", {"action": "rename", "id": "mine1", "name": "by admin"}); check("admin renames a locked save", st == 200)
st, _ = req("POST", "/admin/store", {"action": "delete", "id": "mine1"}); st2, _ = req("GET", "/api/saves/mine1")
check("admin deletes a locked save", st == 200 and st2 == 404)
# drop
dseed, dhdr = new_lock()
st, d = req("POST", "/api/drop", raw=b"hello locked", headers={"X-Drop-Name": "mine.txt", "X-Lock-New": dhdr, "Content-Type": "application/octet-stream"})
check("locked drop upload", st == 201 and d["locked"], d)
st, d2 = req("POST", "/api/drop", raw=b"hello open", headers={"X-Drop-Name": "open.txt", "Content-Type": "application/octet-stream"})
st, lst = req("GET", "/api/drop")
mine = next(f for f in lst["files"] if f["id"] == d["id"])
check("drop listing: locked with lock_n, no lock value", mine["locked"] and mine["lock_n"] == 4096 and not leaks(lst), mine)
st, e = req("DELETE", f"/api/drop/{d2['id']}"); check("an unlocked file: guests cannot remove it", st == 403 and e.get("locked") is False, e)
st, _ = req("DELETE", f"/api/drop/{d['id']}"); check("a locked file without proof: refused", st == 403)
st, _ = req("DELETE", f"/api/drop/{d['id']}", headers={"X-Lock": walk(dseed, 4095)}); st2, _ = req("GET", "/api/drop")
check("a locked file with the proof: removed", st == 200 and not any(f["id"] == d["id"] for f in req("GET", "/api/drop")[1]["files"]))
st, mod = req("GET", "/admin/moderation"); check("moderation listing: no lock value", not leaks(mod))
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
