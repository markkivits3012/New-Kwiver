#!/usr/bin/env python3
"""Offline golf cart map server for the Pi.

Serves the static app from ../app and streams GPS fixes from gpsd to the
browser as Server-Sent Events at /gps, and the receiver's status (gpsd up,
device attached, satellites seen/used) at /gps/status. Standard library only, so nothing
has to be pip-installed on the Pi.

    python3 gps_server.py                 # gpsd on localhost:2947
    python3 gps_server.py --sim           # fake cart walking the course
    python3 gps_server.py --sim --mock-trolley --trolley-speedup 4
                                          # plus a pretend self-driving trolley
    python3 gps_server.py --port 8080
    python3 gps_server.py --sim --course office
                                          # another course from courses/<id>/
                                          # (see coursedir.py); default tashua
    python3 gps_server.py --sim --review  # on a PC: review the route library at
                                          # http://127.0.0.1:8080/review.html
    python3 gps_server.py --sim --mock-remote
                                          # plus a pretend handheld remote

Binds to 127.0.0.1 by default: the kiosk browser runs on the same Pi and
nothing else needs to reach it. The handheld remote is separate: UDP port 8091
on the hotspot address 10.42.0.1 only, signed with the pairing key in
<log-dir>/remote.key (see remote.py and remote/PROTOCOL.md).

Trolley API (only when a trolley is configured; see trolley.py):
    GET  /api/trolley            status as Server-Sent Events
    POST /api/trolley/cmd        {"cmd": "stop"} or {"cmd": "goto", "target": ...}
    POST /api/trolley/heartbeat  the screen is alive; the trolley stops without it
Handheld remote (see remote.py):
    GET  /api/remote             link state, last seen, the remote's fix and battery
Course, player and scorecard API (saved under --log-dir; see store.py):
    GET  /api/courses            installed courses, which one is showing
    POST /api/course             {"id"}: show that course (saves it, restarts the server)
    GET  /api/player             {"hcp", "tees": {course: tee}}
    POST /api/player             change either
    GET  /api/round              the round in progress, or {}
    POST /api/round              {"course", "name", "tee", "hcp", "scores": [...], "par": [...]}
    POST /api/round/finish       {"save": true} keeps it in rounds.jsonl
    GET  /api/rounds             finished rounds, newest first
    GET  /api/bag                {"clubs": {club: carry yards}}
    POST /api/bag                replace it
Route review API (only with --review, on a PC):
    GET  /api/routes             the library (server/routes.json)
    POST /api/routes/review      {"id", "status", "by", "note"}
    POST /api/routes/export      approved routes -> data/routes/gpx/*.gpx
POSTs must send JSON with the header X-Trolley-Client: 1, so another web page
can't send commands through the browser.
"""
import argparse
import json
import math
import os
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coursedir  # noqa: E402
import remote  # noqa: E402
from store import Store  # noqa: E402

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
MAX_BODY = 4096

_latest = {"fix": None, "rx": None}  # rx: receiver status for the screen
_lock = threading.Lock()
_trolley = {"gate": None}
_review = {"lib": None}
_remote = {"link": None}
_course = {"id": coursedir.DEFAULT}
_state = {"store": None, "courses": []}  # Store, and coursedir.summary() of each installed course


def load_course():
    return coursedir.load_course(_course["id"])


def _set_fix(fix):
    with _lock:
        _latest["fix"] = fix


def _get_fix():
    with _lock:
        return _latest["fix"]


def _set_rx(**kw):
    """Receiver status: gpsd reachable, a device attached, fix mode, satellites
    seen/used. A new dict only when something changed (the stream sends changes)."""
    with _lock:
        old = _latest["rx"] or {}
        new = {**old, **kw}
        if new != old:
            _latest["rx"] = new


def _get_rx():
    with _lock:
        return _latest["rx"]


def gpsd_reader(host, port):
    """Keep a connection to gpsd and record the newest TPV fix, plus the
    receiver status (device attached, satellites) for the screen."""
    while True:
        try:
            with socket.create_connection((host, port), timeout=10) as s:
                s.sendall(b'?WATCH={"enable":true,"json":true};\n')
                _set_rx(gpsd=True)
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            continue
                        cls = msg.get("class")
                        if cls == "DEVICES":
                            _set_rx(device=bool(msg.get("devices")))
                        elif cls == "DEVICE" and "activated" in msg:
                            _set_rx(device=bool(msg["activated"]))  # 0 = unplugged
                        elif cls == "SKY" and ("nSat" in msg or msg.get("satellites")):
                            # (some SKY reports carry only DOPs, no satellites: skip those)
                            sats = msg.get("satellites") or []
                            _set_rx(seen=msg.get("nSat", len(sats)),
                                    used=msg.get("uSat", sum(1 for x in sats if x.get("used"))))
                        elif cls == "TPV":
                            _set_rx(device=True, mode=msg.get("mode", 0))
                        if cls != "TPV" or msg.get("mode", 0) < 2:
                            continue
                        if "lat" not in msg or "lon" not in msg:
                            continue
                        _set_fix({
                            "lat": msg["lat"],
                            "lon": msg["lon"],
                            "acc": msg.get("eph") or msg.get("epx"),
                            "speed": msg.get("speed"),
                            "t": time.time(),
                        })
        except OSError:
            pass
        _set_rx(gpsd=False, device=False, mode=0)
        time.sleep(3)  # gpsd not up yet, or dropped; retry


def sim_reader(rate, sim_course=None):
    """Replay the recorded cart route (or walk each hole's tee-to-green line
    if there is none), for bench testing. rate = seconds per step."""
    _set_rx(sim=True)
    course = coursedir.load_course(sim_course or _course["id"])
    if course.get("track"):
        legs, steps = [course["track"]], 2
    else:
        legs, steps = [h["line"] for h in course["holes"]], 30
    while True:
        for line in legs:
            for (a, b) in zip(line, line[1:]):
                for i in range(steps):
                    k = i / steps
                    _set_fix({
                        "lat": a[0] + (b[0] - a[0]) * k,
                        "lon": a[1] + (b[1] - a[1]) * k,
                        "acc": 3.0, "speed": 2.0, "t": time.time(), "sim": True,
                    })
                    time.sleep(rate)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=APP_DIR, **kw)

    def log_message(self, fmt, *args):
        pass  # keep the SD card quiet

    def translate_path(self, path):
        """The course's own files (course.js, aerial/, ...) come from
        courses/<id>/app for --course; never fall back to Tashua's."""
        full = super().translate_path(path)
        if _course["id"] == coursedir.DEFAULT:
            return full
        rel = os.path.relpath(full, APP_DIR).replace(os.sep, "/")
        if rel.startswith(coursedir.COURSE_FILES):
            return coursedir.path(_course["id"], "app", *rel.split("/"))
        return full

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        """Stock file serving plus single byte ranges, which Chromium uses to
        stream and rewind the flyover .mp4 clips."""
        rng = self.headers.get("Range", "")
        path = self.translate_path(self.path)
        if not rng.startswith("bytes=") or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start, _, end = rng[6:].split(",")[0].partition("-")
        try:
            if start:
                start, end = int(start), int(end) if end else size - 1
            else:  # suffix range: last N bytes
                start, end = max(size - int(end), 0), size - 1
        except ValueError:
            return super().send_head()
        end = min(end, size - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._range_left = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        left = getattr(self, "_range_left", None)
        if left is None:
            return super().copyfile(source, outputfile)
        self._range_left = None
        while left > 0:
            chunk = source.read(min(64 * 1024, left))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/gps":
            return self._stream(lambda: _get_fix())
        if route == "/gps/status":
            return self._stream(lambda: _get_rx())
        if route == "/api/trolley":
            gate = _trolley["gate"]
            if not gate:
                return self.send_error(404, "No trolley configured")
            return self._stream(gate.status, every_tick=True)
        if route == "/api/remote":
            link = _remote["link"]
            return self._json(200, link.status() if link else {"state": "off"})
        if route == "/api/courses":
            return self._json(200, {"current": _course["id"], "courses": _state["courses"]})
        if route == "/api/player":
            return self._json(200, _state["store"].player())
        if route == "/api/round":
            return self._json(200, _state["store"].round() or {})
        if route == "/api/rounds":
            return self._json(200, _state["store"].rounds())
        if route == "/api/bag":
            return self._json(200, _state["store"].bag())
        if route == "/api/routes":
            if not _review["lib"]:
                return self.send_error(404, "Start the server with --review")
            return self._json(200, _review["lib"].data)
        try:
            return super().do_GET()
        except ConnectionError:  # client went away (BrokenPipe/Reset/Aborted)
            return  # browser dropped a video request mid-stream (normal on skip)

    def _stream(self, get, every_tick=False):
        """Server-Sent Events: push get()'s value every half second (or only when it changes)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        last = None
        try:
            while True:
                val = get()
                if val and (every_tick or val is not last):
                    self.wfile.write(b"data: " + json.dumps(val).encode() + b"\n\n")
                    self.wfile.flush()
                    last = val
                time.sleep(0.5)
        except ConnectionError:  # client went away (BrokenPipe/Reset/Aborted)
            return

    def do_POST(self):
        route = self.path.split("?")[0]
        gate, lib = _trolley["gate"], _review["lib"]
        if not ((route in ("/api/trolley/cmd", "/api/trolley/heartbeat") and gate)
                or (route in ("/api/routes/review", "/api/routes/export") and lib)
                or route in ("/api/course", "/api/player", "/api/round", "/api/round/finish", "/api/bag")):
            return self.send_error(404)
        # Custom header + JSON type: a cross-site form or fetch can't send these
        # without a CORS preflight, which this server never approves.
        if (self.headers.get("X-Trolley-Client") != "1"
                or not self.headers.get("Content-Type", "").startswith("application/json")):
            return self.send_error(403)
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_error(400)
        if not 0 <= n <= MAX_BODY:
            return self.send_error(413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send_error(400)
        store = _state["store"]
        if route == "/api/course":
            ok, msg = switch_course(body.get("id"))
        elif route == "/api/player":
            ok, msg = store.save_player(body)
        elif route == "/api/round":
            ok, msg = store.save_round(body)
        elif route == "/api/round/finish":
            ok, msg = store.finish_round(body.get("save", True) is not False)
        elif route == "/api/bag":
            ok, msg = store.save_bag(body)
        elif route == "/api/trolley/heartbeat":
            gate.heartbeat()
            ok, msg = True, "ok"
        elif route == "/api/routes/review":
            ok, msg = lib.review(body.get("id"), body.get("status"), body.get("by"), body.get("note"))
        elif route == "/api/routes/export":
            gpx_dir = coursedir.path(_course["id"], "data", "routes", "gpx")
            n = lib.export_gpx(gpx_dir)
            rel = os.path.relpath(gpx_dir, coursedir.ROOT).replace(os.sep, "/")
            ok, msg = True, f"Wrote {n} approved route{'s' * (n != 1)} to {rel}/."
        else:
            ok, msg = gate.command(body)
        self._json(200 if ok else 422, {"ok": ok, "msg": msg})

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def switch_course(cid):
    """Save the course picked on the screen and restart the server on it. The
    course decides the map files, the sim route and the trolley's keep-out map,
    so a fresh start is simpler and safer than swapping them under a live trolley."""
    if cid not in [c["id"] for c in _state["courses"]]:
        return False, "No such course."
    if cid == _course["id"]:
        return True, "Already showing it."
    _state["store"].save_course(cid)

    def restart():
        time.sleep(0.5)  # let the reply go out first
        gate = _trolley["gate"]
        if gate:
            gate.t.stop("course changed")
        argv = getattr(sys, "orig_argv", None) or [sys.executable] + sys.argv
        os.execv(sys.executable, [sys.executable] + argv[1:])
    threading.Thread(target=restart, daemon=True).start()
    return True, "Switching course."


def start_remote(args):
    """The handheld remote's listener. Always on: it waits quietly until
    pi/hotspot.sh has written the pairing key and brought the hotspot up."""
    def stop():
        gate = _trolley["gate"]
        if gate:
            gate.command({"cmd": "stop"}, by="remote")

    def trolley_state():
        gate = _trolley["gate"]
        return gate.status()["state"] if gate else "none"

    key = remote.mock_key() if args.mock_remote else None
    bind = args.remote_bind or ("127.0.0.1" if args.mock_remote else remote.HOTSPOT_IP)
    _remote["link"] = remote.RemoteLink(os.path.join(args.log_dir, remote.KEY_FILE), bind,
                                        on_stop=stop, trolley_state=trolley_state,
                                        any_source=args.mock_remote, key=key)
    if args.mock_remote:
        remote.MockRemote(key, remote.PORT, _get_fix)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--gpsd-host", default="127.0.0.1")
    ap.add_argument("--gpsd-port", type=int, default=2947)
    ap.add_argument("--sim", action="store_true", help="simulate a cart instead of reading gpsd")
    ap.add_argument("--sim-rate", type=float, default=1.0, help="seconds per sim step (smaller = faster)")
    ap.add_argument("--sim-course", help="sim: drive another course's route (to test course detection)")
    ap.add_argument("--mock-trolley", action="store_true", help="add a pretend self-driving trolley")
    ap.add_argument("--trolley-speedup", type=float, default=1.0, help="mock trolley speed x walking pace")
    ap.add_argument("--library-only", action="store_true",
                    help="refuse Next tee / Green side unless an approved route covers it")
    ap.add_argument("--mock-remote", action="store_true",
                    help="add a pretend handheld remote talking to the listener on 127.0.0.1")
    ap.add_argument("--remote-bind", default=None,
                    help=f"address the remote listener binds (default {remote.HOTSPOT_IP}, the hotspot)")
    ap.add_argument("--review", action="store_true",
                    help="enable the route review page and its API (PC only, not the Pi)")
    ap.add_argument("--log-dir", default=os.path.join(APP_DIR, "..", "logs"),
                    help="where the trolley command audit log goes")
    coursedir.add_arg(ap)
    args = ap.parse_args()
    _state["store"] = Store(args.log_dir)
    _state["courses"] = [coursedir.summary(c) for c in coursedir.ids()]
    saved = _state["store"].saved_course()  # picked on the screen; beats --course
    if saved and saved in [c["id"] for c in _state["courses"]]:
        args.course = saved
    if not os.path.isfile(coursedir.path(args.course, "app", "course.js")):
        ap.error(f"no course.js for course {args.course!r} in {coursedir.base(args.course)}")
    _course["id"] = args.course

    target = (lambda: sim_reader(args.sim_rate, args.sim_course)) if args.sim else (lambda: gpsd_reader(args.gpsd_host, args.gpsd_port))
    threading.Thread(target=target, daemon=True).start()

    if args.mock_trolley or args.review:
        from keepout import KeepOut
        from routes import RouteLibrary
        course = load_course()
        k = KeepOut(course, terrain=coursedir.path(args.course, "server", "terrain.json"))
        lib = RouteLibrary(k, coursedir.path(args.course, "server", "routes.json"))
        print(f"route library: {len(lib.approved())} approved of {len(lib.routes())}")
        if args.review:
            _review["lib"] = lib
    if args.mock_trolley:
        from trolley import MockTrolley, TrolleyGate
        first = course["holes"][0]
        start = k.nearest_free(first.get("tee") or first["line"][0], 30)
        _trolley["gate"] = TrolleyGate(MockTrolley(k, start, args.trolley_speedup), k, course,
                                       os.path.abspath(args.log_dir), _get_fix,
                                       library=lib, library_only=args.library_only)

    start_remote(args)
    print(f"Serving {os.path.abspath(APP_DIR)} [{load_course()['name']}] on http://{args.bind}:{args.port}"
          f" ({'SIM' if args.sim else 'gpsd'}{', mock trolley' if args.mock_trolley else ''}"
          f"{', mock remote' if args.mock_remote else ''}{', route review' if args.review else ''})")
    for attempt in range(20):  # after a course switch the old process may still hold the port
        try:
            server = ThreadingHTTPServer((args.bind, args.port), Handler)
            break
        except OSError:
            if attempt == 19:
                raise
            time.sleep(0.5)
    server.serve_forever()


if __name__ == "__main__":
    main()
