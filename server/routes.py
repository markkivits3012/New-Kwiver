"""Named parking spots and the library of vetted trolley routes.

Spots: where the trolley parks for a hole -- beside the tee, beside each aim
target (off the fairway), and beside the green toward the next tee. The gate
and tools/build_routes.py both use these, so a library route ends exactly
where a "Next tee" or "Green side" command sends the trolley.

Spot keys: "tee:6", "aim:5:1" (hole 5, first aim target), "green:5".

RouteLibrary: server/routes.json, written by tools/build_routes.py and
reviewed on a PC with `gps_server.py --review` (app/review.html). Only
routes a person approved are ever used; any route whose shape changes on a
rebuild goes back to "proposed".
"""
import datetime
import heapq
import json
import math
import os
import threading
from xml.sax.saxutils import escape

LIBRARY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes.json")
STATUSES = ("proposed", "approved", "rejected")
START_NEAR_M = 15.0  # how close the trolley must be to a route's start to use it


def _offset(k, p, east_m, north_m):
    q = k.xy(p)
    return k.latlon((q[0] + east_m, q[1] + north_m))


def _unit(k, a, b):
    qa, qb = k.xy(a), k.xy(b)
    dx, dy = qb[0] - qa[0], qb[1] - qa[1]
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


class Spots:
    def __init__(self, keepout, course):
        self.k = keepout
        self.holes = {h["no"]: h for h in course["holes"]}
        self.order = sorted(self.holes)

    def next_hole(self, no):
        i = self.order.index(no)
        return self.holes[self.order[(i + 1) % len(self.order)]]

    def tee(self, no):
        """Behind and beside hole no's tee, either side; else the nearest reachable ground."""
        h = self.holes[no]
        tee = h.get("tee") or h["line"][0]
        aim = h["targets"][0] if h.get("targets") else h["pin"]
        ux, uy = _unit(self.k, tee, aim)                    # line of play off the tee
        for side in (1, -1):
            cand = _offset(self.k, tee, -ux * 6 + side * uy * 8, -uy * 6 - side * ux * 8)
            spot = self.k.nearest_free(cand, 12)
            if spot:
                return spot
        return self.k.nearest_free(tee, 30)                 # tee boxed in by trees/banks

    def green(self, no):
        """Beside hole no's green, on the side toward the next tee."""
        h, nxt = self.holes[no], self.next_hole(no)
        pin = h["pin"]
        ux, uy = _unit(self.k, pin, nxt.get("tee") or nxt["line"][0])
        radius = max((self.k.length_m([pin, p]) for p in h.get("green") or []), default=12.0)
        return (self.k.nearest_free(_offset(self.k, pin, ux * (radius + 6), uy * (radius + 6)), 20)
                or self.k.nearest_free(pin, 60))            # green boxed in by water/trees

    def aim(self, no, i):
        """Off the fairway beside hole no's aim target i (1-based), where nobody's hitting."""
        return self.k.nearest_free(self.holes[no]["targets"][i - 1], 40, off_fairway=True)

    def get(self, key):
        kind, *rest = key.split(":")
        no = int(rest[0])
        return self.aim(no, int(rest[1])) if kind == "aim" else getattr(self, kind)(no)

    def label(self, key):
        kind, *rest = key.split(":")
        return {"tee": f"hole {rest[0]} tee", "green": f"hole {rest[0]} green",
                "aim": f"hole {rest[0]} target {rest[-1]}"}[kind]


class RouteLibrary:
    def __init__(self, k, path=LIBRARY):
        self.k = k
        self.path = path
        self.lock = threading.Lock()
        self.data = {"routes": []}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)

    def routes(self):
        return self.data["routes"]

    def approved(self):
        return [r for r in self.data["routes"] if r["status"] == "approved"]

    def chain(self, start, to_key):
        """Cheapest run of approved routes from near lat/lon start to spot to_key,
        as one list of lat/lon points, or None."""
        ok = self.approved()
        by_from = {}
        for r in ok:
            by_from.setdefault(r["from"], []).append(r)
        heap = [(r["length_m"], i, [r]) for i, r in enumerate(ok)
                if self.k.length_m([start, r["pts"][0]]) <= START_NEAR_M]
        heapq.heapify(heap)
        seen, n = set(), len(heap)
        while heap:
            cost, _, run = heapq.heappop(heap)
            last = run[-1]
            if last["to"] == to_key:
                return [p for i, r in enumerate(run) for p in (r["pts"] if i == 0 else r["pts"][1:])]
            if last["to"] in seen:
                continue
            seen.add(last["to"])
            for r in by_from.get(last["to"], []):
                n += 1
                heapq.heappush(heap, (cost + r["length_m"], n, run + [r]))
        return None

    def review(self, route_id, status, by, note=""):
        """Approve/reject/reset one route. -> (ok, message)."""
        by, note = str(by or "").strip()[:40], str(note or "").strip()[:200]
        if status not in STATUSES:
            return False, "Unknown status."
        if not by:
            return False, "Enter your name first."
        with self.lock:
            r = next((r for r in self.data["routes"] if r["id"] == route_id), None)
            if not r:
                return False, "Unknown route."
            r.update({"status": status, "by": by, "note": note,
                      "at": datetime.datetime.now().isoformat(timespec="seconds")})
            self._save()
        return True, f"{route_id}: {status}."

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    def export_gpx(self, out_dir):
        """Approved routes as GPX: one file per route plus approved-routes.gpx
        with all of them. Old exports in out_dir are replaced. -> file count."""
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(out_dir):
            if f.endswith(".gpx"):
                os.remove(os.path.join(out_dir, f))
        ok = self.approved()

        def rte(r):
            pts = "".join(f'  <rtept lat="{p[0]:.7f}" lon="{p[1]:.7f}"/>\n' for p in r["pts"])
            desc = f'{r["from_label"]} to {r["to_label"]}; approved by {r["by"]} {r["at"]}'
            return f"<rte>\n  <name>{escape(r['id'])}</name>\n  <desc>{escape(desc)}</desc>\n{pts}</rte>\n"

        head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="tashua-golf-map" xmlns="http://www.topografix.com/GPX/1/1">\n')
        for r in ok:
            with open(os.path.join(out_dir, f"{r['id']}.gpx"), "w", encoding="utf-8") as f:
                f.write(head + rte(r) + "</gpx>\n")
        with open(os.path.join(out_dir, "approved-routes.gpx"), "w", encoding="utf-8") as f:
            f.write(head + "".join(rte(r) for r in ok) + "</gpx>\n")
        return len(ok)
