"""Trolley commands: the safety gate every command passes through, and a mock
trolley for testing on a PC until Mark's ROS trolley is connected.

The gate is the only way a command reaches a trolley. It:
  - accepts only "stop" and "goto" with a known target,
  - turns named targets (next tee, green side, golfer) into a legal parking
    spot using the keep-out map, and refuses goals on greens, tees, bunkers,
    water or off the course,
  - drives named targets along approved library routes when the trolley is
    at a route's start (routes.py), and with library_only refuses them otherwise,
  - refuses goals more than MAX_GOAL_M from the trolley and repeated "goto"
    faster than once a second,
  - writes every command, accepted or refused, to an audit log.

A real trolley backend (ROS, later) needs the same four methods as
MockTrolley: status(), goto(lat, lon, label, route), stop(why), heartbeat().
"""
import datetime
import json
import math
import os
import threading
import time

from routes import Spots

MAX_GOAL_M = 800.0
GOTO_MIN_INTERVAL_S = 1.0
GOLFER_STALE_S = 10.0
TARGETS = ("next_tee", "green_side", "golfer", "point")


class TrolleyGate:
    def __init__(self, backend, keepout, course, log_dir, golfer_fix, library=None, library_only=False):
        self.t = backend
        self.k = keepout
        self.spots = Spots(keepout, course)
        self.library = library            # RouteLibrary, or None
        self.library_only = library_only  # refuse named moves with no approved route
        self.golfer_fix = golfer_fix
        self.last_goto = 0.0
        self.log_path = os.path.join(log_dir, "trolley-commands.jsonl")
        os.makedirs(log_dir, exist_ok=True)
        self.log_lock = threading.Lock()

    # ---- named targets ---------------------------------------------------------
    def _resolve(self, body):
        """-> (lat, lon, label, spot key or None) or raise ValueError(reason)."""
        target = body.get("target")
        if target == "point":
            try:
                p = (float(body["lat"]), float(body["lon"]))
            except (KeyError, TypeError, ValueError):
                raise ValueError("Bad position.")
            if not (math.isfinite(p[0]) and math.isfinite(p[1])):
                raise ValueError("Bad position.")
            ok, why = self.k.check(p)
            if not ok:
                raise ValueError(f"Can't send it there: that spot is {why}.")
            return p[0], p[1], "the marker", None

        if target == "golfer":
            fix = self.golfer_fix()
            if not fix or time.time() - fix["t"] > GOLFER_STALE_S:
                raise ValueError("No current golfer position.")
            p = self.k.nearest_free((fix["lat"], fix["lon"]), 15)
            if not p:
                raise ValueError("You're not near any ground the trolley may drive on.")
            return p[0], p[1], "you", None

        no = int(body.get("hole", 0))
        if no not in self.spots.holes:
            raise ValueError("Unknown hole.")
        if target == "next_tee":
            nxt = self.spots.next_hole(no)["no"]
            spot = self.spots.tee(nxt)
            if not spot:
                raise ValueError(f"No legal parking spot found near the hole {nxt} tee.")
            return spot[0], spot[1], f"the hole {nxt} tee", f"tee:{nxt}"

        if target == "green_side":
            spot = self.spots.green(no)
            if not spot:
                raise ValueError(f"No legal parking spot found beside the hole {no} green.")
            return spot[0], spot[1], f"beside the hole {no} green", f"green:{no}"

        raise ValueError("Unknown target.")

    # ---- commands ----------------------------------------------------------------
    def command(self, body, by="screen"):
        """Handle one command from the screen (or STOP from the remote). -> (ok, message)."""
        cmd = body.get("cmd")
        status = self.t.status()
        entry = {"by": by, "cmd": cmd, "target": body.get("target"), "hole": body.get("hole"),
                 "request": [body.get("lat"), body.get("lon")],
                 "trolley": [status["lat"], status["lon"]], "state_before": status["state"]}
        try:
            if cmd == "stop":
                self.t.stop(f"Stopped from the {by}")
                ok, msg = True, "Stopped."
            elif cmd == "goto":
                if body.get("target") not in TARGETS:
                    raise ValueError("Unknown target.")
                now = time.time()
                if now - self.last_goto < GOTO_MIN_INTERVAL_S:
                    raise ValueError("Too many commands; wait a second.")
                lat, lon, label, key = self._resolve(body)
                path = None
                if key and self.library:
                    path = self.library.chain((status["lat"], status["lon"]), key)
                    if path:
                        lat, lon = path[-1]  # the vetted spot the route ends at
                    elif self.library_only:
                        raise ValueError(f"No approved route from here to {label}.")
                entry.update({"goal": [lat, lon], "vetted_route": bool(path)})
                far = self.k.length_m([(status["lat"], status["lon"]), (lat, lon)])
                if far > MAX_GOAL_M:
                    raise ValueError(f"That's {far:.0f} m away; the limit is {MAX_GOAL_M:.0f} m.")
                self.last_goto = now
                ok, msg = self.t.goto(lat, lon, label, path)
                if ok and path:
                    msg += " (approved route)"
            else:
                raise ValueError("Unknown command.")
        except (ValueError, KeyError, TypeError) as e:
            ok, msg = False, str(e) or "Bad command."
        entry.update({"ok": ok, "msg": msg})
        self._audit(entry)
        return ok, msg

    def heartbeat(self):
        self.t.heartbeat()

    def status(self):
        return self.t.status()

    def _audit(self, entry):
        entry = {"t": datetime.datetime.now().isoformat(timespec="seconds"), **entry}
        with self.log_lock:
            try:
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > 5_000_000:
                    os.replace(self.log_path, self.log_path + ".1")  # keep one old file
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError as e:
                # logging must never block a command (STOP above all); say so and carry on
                print(f"audit log write failed: {e}", flush=True)


class MockTrolley:
    """Pretend trolley: drives planned paths at walking pace, drains a battery,
    and stops itself if the screen's heartbeat stops while it's moving."""
    SPEED_MS = 1.4
    HEARTBEAT_TIMEOUT_S = 3.0

    def __init__(self, keepout, start, speedup=1.0):
        self.k = keepout
        self.speedup = speedup
        self.lock = threading.Lock()
        self.pos = start
        self.heading = 0.0
        self.state, self.msg = "idle", "Ready"
        self.goal, self.label, self.path = None, None, []
        self.battery = 100.0
        self.last_hb = 0.0
        threading.Thread(target=self._run, daemon=True).start()

    def status(self):
        with self.lock:
            left = self.k.length_m([self.pos] + self.path) if self.path else 0.0
            return {"lat": self.pos[0], "lon": self.pos[1], "heading": round(self.heading, 1),
                    "state": self.state, "msg": self.msg, "label": self.label,
                    "goal": self.goal, "path": [self.pos] + self.path if self.path else [],
                    "eta_s": round(left / (self.SPEED_MS * self.speedup)) if self.path else None,
                    "battery": round(self.battery), "mock": True, "t": time.time()}

    def goto(self, lat, lon, label, route=None):
        """route: an approved library route starting near here; else plan one."""
        if route:
            lead = self.k.plan(self.pos, route[0]) or [self.pos, route[0]]
            path = lead + route[1:]
        else:
            path = self.k.plan(self.pos, (lat, lon))
        if not path:
            return False, f"No route to {label}."
        with self.lock:
            self.goal, self.label, self.path = (lat, lon), label, path[1:]
            self.state, self.msg = "driving", f"Driving to {label}"
            self.last_hb = max(self.last_hb, time.time())
        return True, f"On the way to {label}."

    def stop(self, why):
        with self.lock:
            self._halt(why)

    def heartbeat(self):
        with self.lock:
            self.last_hb = time.time()

    def _halt(self, why):
        self.goal, self.label, self.path = None, None, []
        self.state, self.msg = "stopped", why

    def _run(self):
        dt = 0.2
        while True:
            time.sleep(dt)
            with self.lock:
                if self.state != "driving":
                    continue
                if time.time() - self.last_hb > self.HEARTBEAT_TIMEOUT_S:
                    self._halt("Stopped: lost contact with the screen")  # never auto-resumes
                    continue
                step = self.SPEED_MS * self.speedup * dt
                while step > 0 and self.path:
                    a, b = self.k.xy(self.pos), self.k.xy(self.path[0])
                    d = math.hypot(b[0] - a[0], b[1] - a[1])
                    if d > 0:
                        self.heading = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360
                    if d <= step:
                        self.pos = self.path.pop(0)
                        step -= d
                    else:
                        k = step / d
                        self.pos = self.k.latlon((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
                        step = 0
                self.battery = max(0.0, self.battery - 0.0015 * dt * self.speedup)
                if not self.path:
                    self.state, self.msg = "idle", f"Arrived at {self.label}"
                    self.goal, self.label = None, None
