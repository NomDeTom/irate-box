# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Syncthing's GUI password from the root helper (hub_control.syncthing_gui_password) against a
stand-in Syncthing that isn't listening yet, as just after install.sh's `gui user set` restarts its
GUI (the Lyra, 2026-10-07: /sync/ kept its old password). python3 tests/sim_syncthing_gui.py"""
import json, os, socket, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="syncthing-gui-"))
(T / "state" / ".local/state/syncthing").mkdir(parents=True)
(T / "state" / ".local/state/syncthing/config.xml").write_text("<configuration><gui><apikey>KEY123</apikey></gui></configuration>")
(T / "etc").mkdir()
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO))
from irate_box.root import hub_control as H  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

got = []
class Syncthing(BaseHTTPRequestHandler):
    def do_PATCH(self):
        got.append((self.path, self.headers.get("X-API-Key"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # a port with nothing on it, yet
H.SYNCTHING_GUI = f"http://127.0.0.1:{port}"
srv = None
def later(seconds):
    global srv
    if len(waits) == 3:   # Syncthing's GUI back after the third wait
        srv = HTTPServer(("127.0.0.1", port), Syncthing); threading.Thread(target=srv.serve_forever, daemon=True).start()
    waits.append(seconds)
waits = []
ok = H.syncthing_gui_password("pw-from-stdin", sleep=later)
check("nothing listening at first: tried again until Syncthing's GUI is back, then set",
      ok is True and len(waits) == 4 and got == [("/rest/config/gui", "KEY123", {"password": "pw-from-stdin"})], (ok, waits, got))
srv.shutdown(); srv.server_close()
waits.clear()
check("never back: gives up after its tries (install.sh then says so)",
      H.syncthing_gui_password("x", tries=3, sleep=later) is False and len(waits) == 2, waits)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
