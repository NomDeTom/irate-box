# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Reports (menu overhaul M10; Tom, 2026-10-08: "anyone can report a post - admin decides what
counts"), against a hub it starts: anyone may report a shoutbox message or a forum post, once per
visitor; only the owner's reasons are taken; what counts (how many reports) reaches the queue on
/admin; hidden meanwhile if the owner chose; kept, it leaves the queue and shows again; deleted,
its report goes; a flood of reports from one visitor is stopped. python3 tests/sim_reports.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="reports-")
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
try:
    for _ in range(50):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status"); break
        except OSError:
            time.sleep(0.1)
    def go(method, path, body=None, admin=False, who="10.0.0.5"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-Forwarded-For": who,
             "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
        if admin:
            h.update({"X-Irate-Front": SECRET, "X-Irate-Admin": "1"})
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse(); raw = r.read().decode()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw
    go("POST", "/messages", {"name": "Moth", "text": "buy cheap stuff"})
    go("POST", "/messages", {"name": "Ann", "text": "hello all"})
    _, msgs = go("GET", "/messages")
    spam = next(m for m in msgs["messages"] if m["name"] == "Moth")
    check("the page is told the reasons offered", msgs.get("report_reasons") == ["spam", "unkind", "personal details", "other"], msgs.get("report_reasons"))
    ref = {"created": spam["created"], "name": "Moth"}
    st, r = go("POST", "/api/report", {"app": "shoutbox", "ref": ref, "reason": "spam"})
    check("anyone may report a message", st == 200 and "thank" in r.get("message", ""), (st, r))
    st, r = go("POST", "/api/report", {"app": "shoutbox", "ref": ref, "reason": "spam"})
    check("  once per visitor", st == 200 and "already" in r.get("message", ""), r)
    st, _ = go("POST", "/api/report", {"app": "shoutbox", "ref": ref, "reason": "illegal"})
    check("a reason the owner doesn't offer: refused", st == 400, st)
    st, _ = go("POST", "/api/report", {"app": "notes", "ref": ref, "reason": "spam"})
    check("only the shoutbox and the forum", st == 400, st)
    _, mod = go("GET", "/admin/moderation", admin=True)
    check("one report counts by default: in the queue", [q["text"] for q in mod["queue"]] == ["buy cheap stuff"], mod["queue"])
    go("POST", "/admin/settings", {"report_threshold": 2, "report_hide": True}, admin=True)
    _, mod = go("GET", "/admin/moderation", admin=True)
    check("the owner asks for two: not yet", mod["queue"] == [] and mod["reports"]["threshold"] == 2, mod["queue"])
    _, msgs = go("GET", "/messages")
    check("  and not hidden yet", any(m["name"] == "Moth" for m in msgs["messages"]))
    go("POST", "/api/report", {"app": "shoutbox", "ref": ref, "reason": "unkind"}, who="10.0.0.6")
    _, mod = go("GET", "/admin/moderation", admin=True)
    q = mod["queue"][0] if mod["queue"] else {}
    check("a second visitor: it counts, with both reasons", q.get("count") == 2 and q.get("reasons") == {"spam": 1, "unkind": 1}, q)
    _, msgs = go("GET", "/messages")
    check("hidden from everyone else while waiting (the owner chose so)", not any(m["name"] == "Moth" for m in msgs["messages"]))
    go("POST", "/admin/moderation", {"action": "keep", "key": q["key"]}, admin=True)
    _, mod = go("GET", "/admin/moderation", admin=True)
    _, msgs = go("GET", "/messages")
    check("kept: out of the queue, and shown again", mod["queue"] == [] and any(m["name"] == "Moth" for m in msgs["messages"]))
    # A forum post: reported, hidden, deleted.
    go("POST", "/board/threads", {"name": "Rook", "title": "Meet up", "text": "Saturday at the gate"})
    _, threads = go("GET", "/board/threads")
    tid = threads["threads"][0]["id"]
    _, th = go("GET", f"/board/thread/{tid}")
    created = th["thread"]["posts"][0]["created"]
    for who in ("10.0.0.7", "10.0.0.8"):
        go("POST", "/api/report", {"app": "board", "ref": {"thread": tid, "created": created}, "reason": "personal details"}, who=who)
    _, th = go("GET", f"/board/thread/{tid}")
    check("a forum post that counts: hidden, its words not sent", th["thread"]["posts"][0].get("hidden") is True and "gate" not in json.dumps(th), th["thread"]["posts"][0])
    _, mod = go("GET", "/admin/moderation", admin=True)
    post = next((x for x in mod["queue"] if x["key"].startswith("board:")), {})
    check("  in the queue for the owner, with what it said", post.get("text") == "Saturday at the gate" and post.get("where") == "Board: Meet up", post)
    go("POST", "/admin/moderation", {"action": "delete_thread", "id": tid}, admin=True)
    go("POST", "/admin/moderation", {"action": "delete_message", "created": spam["created"], "name": "Moth"}, admin=True)
    _, mod = go("GET", "/admin/moderation", admin=True)
    check("deleted: gone from the queue", not any(x["key"].startswith("shoutbox:") for x in mod["queue"]), mod["queue"])
    # A flood from one visitor is stopped.
    codes = [go("POST", "/api/report", {"app": "shoutbox", "ref": {"created": 1000 + i, "name": "x"}, "reason": "spam"}, who="10.0.0.9")[0] for i in range(22)]
    check("at most 20 reports an hour from one visitor", codes[:20] == [200] * 20 and codes[20:] == [429, 429], codes[18:])
    for bad in ({"report_threshold": 0}, {"report_threshold": 50}, {"report_reasons": []}, {"report_reasons": ["rude words"]}):
        _, cur = go("POST", "/admin/settings", bad, admin=True)
        check(f"refused setting: {bad}", cur.get("report_threshold") == 2 and cur.get("report_reasons") == ["spam", "unkind", "personal details", "other"], cur)
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
