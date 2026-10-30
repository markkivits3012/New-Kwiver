#!/usr/bin/env python3
"""Propose the standard trolley routes for a round, score them, and flag the
risky ones for a person to review.

    python tools/build_routes.py
    python tools/build_routes.py --course office   # a course in courses/<id>/

For each hole: tee -> each aim target (parked off the fairway) -> green side
-> next tee, planned on the keep-out map (server/keepout.py, with
server/terrain.json if present). Writes server/routes.json and the review
page's keep-out overlay, app/review/keepout.png.

Review and approve them on the PC:

    python server/gps_server.py --sim --review     # then open /review.html

Rerunning keeps each route's review if its shape didn't change; a route that
changed goes back to "proposed" with a note. Needs Pillow (for the overlay).
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import sys

from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import coursedir  # noqa: E402
from keepout import CODES, REASONS, STEEP_PCT, KeepOut  # noqa: E402
from routes import LIBRARY, Spots  # noqa: E402

OVERLAY = os.path.join(ROOT, "app", "review", "keepout.png")  # set per --course in main()
# clearance isn't counted near the ends (parking spots sit beside tees and greens)
# or on cart paths (they run beside trees and banks by design)
END_SKIP_M = 15
FLAG = {"fairway_m": 10, "clearance_m": 1.0, "slope_pct": 15, "steep_m": 10, "detour": 1.8, "spot_m": 25}
# overlay colors (RGBA) per keep-out code; free ground is left clear
COLORS = {1: (0, 0, 0, 110), CODES["green"]: (255, 255, 255, 70), CODES["tee"]: (255, 255, 255, 70),
          CODES["bunker"]: (255, 230, 150, 90), CODES["water"]: (60, 140, 255, 90),
          CODES["tree"]: (40, 220, 60, 110), CODES["steep"]: (240, 50, 40, 130),
          CODES["structure"]: (80, 120, 255, 140), 9: (190, 80, 255, 110),
          CODES["road"]: (255, 150, 0, 140)}


def nearest_block(k, q, r_m=6.0):
    """(meters from x/y point q to the nearest blocked cell's edge, its code), within r_m."""
    cl = k._cell(q)
    best = (r_m, None)
    rc = int(r_m / k.c) + 1
    for i in range(cl[0] - rc, cl[0] + rc + 1):
        for j in range(cl[1] - rc, cl[1] + rc + 1):
            if 0 <= i < k.w and 0 <= j < k.h and k.code[j * k.w + i]:
                x0, y0 = k.x0 + i * k.c, k.y0 + j * k.c
                d = math.hypot(max(x0 - q[0], 0, q[0] - x0 - k.c), max(y0 - q[1], 0, q[1] - y0 - k.c))
                if d < best[0]:
                    best = (d, k.code[j * k.w + i])
    return best


def what(code):
    return REASONS[code].replace("on or too close to ", "").replace("in or too close to ", "").replace("on ", "")


def score(k, pts):
    q = [k.xy(p) for p in pts]
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(q, q[1:]))
    fairway = steep_m = 0.0
    max_slope, clear, run = 0, (math.inf, None), 0.0
    for a, b in zip(q, q[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(seg))
        for s in range(n):
            p = (a[0] + (b[0] - a[0]) * (s + 0.5) / n, a[1] + (b[1] - a[1]) * (s + 0.5) / n)
            pos = run + seg * (s + 0.5) / n
            cl = k._cell(p)
            kk = cl[1] * k.w + cl[0]
            fairway += seg / n * k.fairway[kk]
            max_slope = max(max_slope, k.slope[kk])
            steep_m += seg / n * (k.slope[kk] >= FLAG["slope_pct"])
            if END_SKIP_M < pos < length - END_SKIP_M and not k.path[kk]:
                clear = min(clear, nearest_block(k, p))
        run += seg
    straight = math.hypot(q[-1][0] - q[0][0], q[-1][1] - q[0][1])
    return {"length_m": round(length, 1), "straight_m": round(straight, 1), "fairway_m": round(fairway, 1),
            "clearance_m": None if clear[0] == math.inf else round(clear[0], 1), "max_slope_pct": max_slope,
            "steep_m": round(steep_m, 1)}, clear[1]


def flags_for(r, clear_code, spot_m):
    out = []
    if r["fairway_m"] > FLAG["fairway_m"]:
        out.append(f"crosses fairway for {r['fairway_m']:.0f} m")
    if r["clearance_m"] is not None and r["clearance_m"] < FLAG["clearance_m"]:
        out.append(f"passes within {r['clearance_m']:.1f} m of {what(clear_code)}")
    if r["steep_m"] >= FLAG["steep_m"]:
        out.append(f"grade up to {r['max_slope_pct']}% ({r['steep_m']:.0f} m at {FLAG['slope_pct']}%+)")
    if r["straight_m"] > 30 and r["length_m"] > FLAG["detour"] * r["straight_m"]:
        out.append(f"long way round ({r['length_m'] / r['straight_m']:.1f}x the straight line)")
    for where, d in spot_m:
        if d > FLAG["spot_m"]:
            out.append(f"parks {d:.0f} m from the {where}")
    return out


def spot_distance(k, spots, key, p):
    """How far parking spot p is from what it's for: the tee, the aim target, or the green's edge."""
    kind, *rest = key.split(":")
    h = spots.holes[int(rest[0])]
    if kind == "green" and h.get("green"):
        return min(k.length_m([q, p]) for q in h["green"])
    if kind == "aim":
        return k.length_m([h["targets"][int(rest[1]) - 1], p])
    return k.length_m([h.get("tee") or h["line"][0] if kind == "tee" else h["pin"], p])


def short(key, hole):
    kind, *rest = key.split(":")
    name = {"tee": "tee", "green": "green", "aim": f"aim{rest[-1]}"}[kind]
    return name if int(rest[0]) == hole else f"h{int(rest[0]):02d}-{name}"


def overlay(k):
    img = Image.new("RGBA", (k.w, k.h))
    px = img.load()
    for j in range(k.h):
        for i in range(k.w):
            c = k.code[j * k.w + i]
            if c:
                px[i, k.h - 1 - j] = COLORS.get(c, (0, 0, 0, 0))
    os.makedirs(os.path.dirname(OVERLAY), exist_ok=True)
    img.save(OVERLAY, optimize=True)


def main():
    global LIBRARY, OVERLAY
    ap = argparse.ArgumentParser()
    coursedir.add_arg(ap)
    args = ap.parse_args()
    LIBRARY = coursedir.path(args.course, "server", "routes.json")
    OVERLAY = coursedir.path(args.course, "app", "review", "keepout.png")
    course = coursedir.load_course(args.course)
    k = KeepOut(course, terrain=coursedir.path(args.course, "server", "terrain.json"))
    spots = Spots(k, course)
    old = {}
    if os.path.exists(LIBRARY):
        with open(LIBRARY, encoding="utf-8") as f:
            old = {r["id"]: r for r in json.load(f)["routes"]}
    today = datetime.date.today().isoformat()

    routes, failed = [], []
    for no in spots.order:
        nxt = spots.next_hole(no)["no"]
        keys = [f"tee:{no}"] + [f"aim:{no}:{i + 1}" for i in range(len(spots.holes[no].get("targets") or []))] \
            + [f"green:{no}", f"tee:{nxt}"]
        for a, b in zip(keys, keys[1:]):
            rid = f"h{no:02d}-{short(a, no)}-{short(b, no)}"
            pa, pb = spots.get(a), spots.get(b)
            pts = k.plan(pa, pb) if pa and pb else None
            if not pts:
                failed.append(rid)
                continue
            pts = [list(p) for p in pts]
            r = {"id": rid, "hole": no, "from": a, "to": b,
                 "from_label": spots.label(a), "to_label": spots.label(b), "pts": pts}
            sc, clear_code = score(k, pts)
            r.update(sc)
            spot_m = [(spots.label(x).split(" ", 2)[-1], spot_distance(k, spots, x, p))
                      for x, p in ((a, pa), (b, pb))]
            r["flags"] = flags_for(r, clear_code, spot_m)
            r["geom"] = hashlib.sha1(json.dumps(pts).encode()).hexdigest()[:12]
            prev = old.get(rid)
            if prev and prev.get("geom") == r["geom"]:
                r.update({x: prev.get(x) for x in ("status", "by", "at", "note")})
            elif prev and prev.get("status") != "proposed":
                r.update({"status": "proposed", "by": None, "at": None,
                          "note": f"Route changed on rebuild {today}; was {prev['status']} by {prev.get('by')}."})
            else:
                r.update({"status": "proposed", "by": None, "at": None, "note": ""})
            routes.append(r)

    g = k.grid_key()
    lib = {"built": today, "terrain": k.terrain, "steep_pct": STEEP_PCT, "flag_limits": FLAG,
           "grid": {**g, "kx": k.kx, "ky": k.ky, "overlay": "review/keepout.png"}, "routes": routes}
    tmp = LIBRARY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, indent=1)
    os.replace(tmp, LIBRARY)
    overlay(k)

    flagged = [r for r in routes if r["flags"]]
    print(f"{len(routes)} routes, {sum(r['length_m'] for r in routes) / 1000:.1f} km, "
          f"{len(flagged)} flagged, {len(failed)} could not be planned"
          f"{'' if k.terrain else ' (no terrain.json: trees/slopes not avoided)'}")
    for r in flagged:
        print(f"  {r['id']:<22} {'; '.join(r['flags'])}")
    for rid in failed:
        print(f"  {rid:<22} NO ROUTE")
    status = {}
    for r in routes:
        status[r["status"]] = status.get(r["status"], 0) + 1
    print("status:", ", ".join(f"{n} {s}" for s, n in sorted(status.items())))
    print(f"wrote {LIBRARY} and {OVERLAY}")


if __name__ == "__main__":
    main()
