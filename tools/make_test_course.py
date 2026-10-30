#!/usr/bin/env python3
"""Build a made-up test course (no golf data in OSM) from a small hand-written
definition, so the GPS app, yardages, auto-advance, 3D view and trolley can be
tried somewhere other than Tashua.

    python tools/make_test_course.py --course office

Reads courses/<id>/course-def.json:

    {"name": ..., "green_radius_m": 5, "advance_m": 8, "green_m": 8,
     "outline": [[lat, lon], ...],          # where the trolley may go (its geofence)
     "holes": [{"no": 1, "par": 3, "tee": [lat, lon], "pin": [lat, lon],
                "targets": [[lat, lon], ...], "fairway": true}, ...]}

and courses/<id>/data/osm_map.json (an OSM map.json download of the area)
for the buildings drawn on the map and a clearance check. Writes
courses/<id>/app/course.js and courses/<id>/data/holes.gpx (tees and pins
as waypoints, to find them on a phone).

Greens are circles, tees small boxes square to the line of play, fairways a
strip between them; yards are along the tee -> targets -> pin line. Each hole
gets a simple flyover (tee, halfway, green, back).

Then, as for Tashua: fetch_aerial, fetch_elevation, build_terrain --check
and build_routes, each with --course <id>.
"""
import argparse
import datetime
import json
import math
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import coursedir  # noqa: E402

YD = 1.09361
TEE_BOX_M = (4.0, 3.0)    # across, along the line of play
FAIRWAY_W_M = 10.0
CLEAR_M = 6.0             # warn when a tee or pin is closer than this to a building
ROAD_CLEAR_M = 20.0       # ...or to a public road
PUBLIC_ROADS = {"motorway", "motorway_link", "trunk", "primary", "secondary", "tertiary",
                "unclassified", "residential"}


class Local:
    """Flat x/y meters (east, north) around a point; fine at test-course scale."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx, self.ky = 111320 * math.cos(math.radians(lat0)), 110540

    def xy(self, p):
        return ((p[1] - self.lon0) * self.kx, (p[0] - self.lat0) * self.ky)

    def ll(self, q):
        return [round(self.lat0 + q[1] / self.ky, 7), round(self.lon0 + q[0] / self.kx, 7)]


def seg_dist(q, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / ((dx * dx + dy * dy) or 1)))
    return math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dy)


def inside(q, ring):
    c = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > q[1]) != (b[1] > q[1]) and q[0] < a[0] + (q[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            c = not c
    return c


def ring_dist(q, ring, closed=True):
    """Meters from q to a polygon (0 inside) or a line."""
    if closed and inside(q, ring):
        return 0.0
    pairs = list(zip(ring, ring[1:] + (ring[:1] if closed else [])))
    return min(seg_dist(q, a, b) for a, b in pairs)


def bearing(a, b):
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))


def osm_shapes(path, loc):
    """Buildings (closed rings) and public roads (lines) from an OSM map.json, in local x/y."""
    if not os.path.exists(path):
        print(f"  (no {path}: no buildings drawn, no clearance check)")
        return [], []
    with open(path, encoding="utf-8") as f:
        els = json.load(f)["elements"]
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node"}
    buildings, roads = [], []
    for e in els:
        tags = e.get("tags", {})
        if e["type"] != "way":
            continue
        pts = [nodes[n] for n in e["nodes"] if n in nodes]
        if "building" in tags and len(pts) >= 4 and e["nodes"][0] == e["nodes"][-1]:
            buildings.append(pts[:-1])
        elif tags.get("highway") in PUBLIC_ROADS and len(pts) >= 2:
            roads.append((tags.get("name") or tags["highway"], pts))
    return buildings, roads


def build(course_id):
    with open(coursedir.path(course_id, "course-def.json"), encoding="utf-8") as f:
        d = json.load(f)
    pts = d["outline"] + [p for h in d["holes"] for p in [h["tee"], h["pin"]]]
    loc = Local(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    r_green = d.get("green_radius_m", 5.0)

    holes, features, warnings = [], [], []
    for h in d["holes"]:
        tee, pin = loc.xy(h["tee"]), loc.xy(h["pin"])
        targets = [loc.xy(t) for t in h.get("targets", [])]
        line = [tee] + targets + [pin]
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:]))
        green = [loc.ll((pin[0] + r_green * math.cos(a), pin[1] + r_green * math.sin(a)))
                 for a in (2 * math.pi * i / 20 for i in range(21))]
        # tee box: square to the first leg
        first = line[1]
        ux, uy = (first[0] - tee[0]), (first[1] - tee[1])
        n = math.hypot(ux, uy) or 1
        ux, uy = ux / n, uy / n
        w, l = TEE_BOX_M[0] / 2, TEE_BOX_M[1] / 2
        box = [loc.ll((tee[0] + sx * w * uy + sy * l * ux, tee[1] - sx * w * ux + sy * l * uy))
               for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1))]
        features += [{"layer": "tee", "kind": "poly", "rings": [box]},
                     {"layer": "green", "kind": "poly", "rings": [green]}]
        if h.get("fairway", True):
            # strip along the last leg, from ~a third of the way out to short of the green
            a, b = line[-2], pin
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            fx, fy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
            s0 = min(15.0, 0.3 * seg) if a == tee else 0.0
            s1 = seg - r_green - 2
            if s1 - s0 > 8:
                hw = FAIRWAY_W_M / 2
                strip = [loc.ll((a[0] + fx * s + sx * hw * fy, a[1] + fy * s - sx * hw * fx))
                         for s, sx in ((s0, -1), (s1, -1), (s1, 1), (s0, 1), (s0, -1))]
                features.append({"layer": "fairway", "kind": "poly", "rings": [strip]})

        hdg = round(bearing(tee, pin), 2)
        mid = loc.ll(((tee[0] + pin[0]) / 2, (tee[1] + pin[1]) / 2))
        rng = max(40.0, round(length * 0.9))
        tour = [
            {"lat": h["tee"][0], "lon": h["tee"][1], "heading": hdg, "tilt": 70.0, "range": rng, "dur": 4.0, "hold": 1.0},
            {"lat": mid[0], "lon": mid[1], "heading": hdg, "tilt": 72.0, "range": max(30.0, round(length * 0.6)),
             "dur": 4.0, "hold": 1.0},
            {"lat": h["pin"][0], "lon": h["pin"][1], "heading": hdg, "tilt": 65.0, "range": 30.0, "dur": 4.0, "hold": 2.0},
            {"lat": h["tee"][0], "lon": h["tee"][1], "heading": hdg, "tilt": 70.0, "range": rng, "dur": 4.0, "hold": 3.0},
        ]
        holes.append({"no": h["no"], "par": h.get("par", 3), "hcp": None, "yards": round(length * YD),
                      "line": [loc.ll(q) for q in line], "tee": h["tee"], "pin": h["pin"],
                      "targets": h.get("targets", []), "tour": tour, "green": green, "tees": [h["tee"]]})

    # buildings for the map, and how close each tee and pin is to them and to roads
    buildings, roads = osm_shapes(coursedir.path(course_id, "data", "osm_map.json"), loc)
    ring = [loc.xy(p) for p in d["outline"]]
    xs, ys = [q[0] for q in ring], [q[1] for q in ring]
    near = [b for b in buildings
            if any(min(xs) - 80 < q[0] < max(xs) + 80 and min(ys) - 80 < q[1] < max(ys) + 80
                   for q in map(loc.xy, b))]
    features += [{"layer": "building", "kind": "poly", "rings": [[[round(a, 7), round(o, 7)] for a, o in b] + [list(b[0])]]}
                 for b in near]
    b_local = [[loc.xy(p) for p in b] for b in near]
    r_local = [(name, [loc.xy(p) for p in pts]) for name, pts in roads]
    print(f"{'spot':<10} {'building m':>10} {'road m':>8} {'edge m':>7}")
    for h in holes:
        for kind in ("tee", "pin"):
            q = loc.xy(h[kind])
            db = min((ring_dist(q, b) for b in b_local), default=float("inf"))
            road = min(((ring_dist(q, p, closed=False), name) for name, p in r_local), default=(float("inf"), ""))
            de = min(seg_dist(q, a, b) for a, b in zip(ring, ring[1:] + ring[:1]))
            de = de if inside(q, ring) else -de
            print(f"h{h['no']} {kind:<7} {db:>10.1f} {road[0]:>8.1f} {de:>7.1f}")
            limit = r_green + 1 if kind == "pin" else 2
            if db < CLEAR_M:
                warnings.append(f"hole {h['no']} {kind}: {db:.1f} m from a building")
            if road[0] < ROAD_CLEAR_M:
                warnings.append(f"hole {h['no']} {kind}: {road[0]:.1f} m from {road[1]}")
            if de < limit:
                warnings.append(f"hole {h['no']} {kind}: {'outside' if de < 0 else f'{de:.1f} m inside'} the outline")
    for q in ring:
        if any(inside(q, b) for b in b_local):
            warnings.append(f"outline point {loc.ll(q)} is inside a building")
    # another hole's line shouldn't run across a green (its first 15 m, by the tee, may)
    for h in holes:
        for o in holes:
            if o is h:
                continue
            line = [loc.xy(p) for p in o["line"]]
            pin = loc.xy(h["pin"])
            dd = min(seg_dist(pin, a, b) for a, b in zip(line, line[1:]))
            if dd < r_green + 2 and math.dist(pin, line[0]) > 15:
                warnings.append(f"hole {o['no']}'s line passes {dd:.1f} m from hole {h['no']}'s pin")

    course = {
        "name": d["name"],
        "source": "Hand-made test layout; buildings: OpenStreetMap contributors (ODbL 1.0)",
        "built": datetime.date.today().isoformat(),
        "advance_m": d.get("advance_m"), "green_m": d.get("green_m"),
        "outline": d["outline"], "holes": holes, "features": features, "track": [],
    }
    out = coursedir.path(course_id, "app", "course.js")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("// Generated by tools/make_test_course.py from course-def.json -- do not edit by hand.\n")
        f.write("// Buildings (c) OpenStreetMap contributors, ODbL 1.0.\n")
        f.write("window.COURSE = ")
        json.dump(course, f, separators=(",", ":"))
        f.write(";\n")

    gpx = coursedir.path(course_id, "data", "holes.gpx")
    os.makedirs(os.path.dirname(gpx), exist_ok=True)
    with open(gpx, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="tashua-golf-map make_test_course.py" '
                'xmlns="http://www.topografix.com/GPX/1/1">\n')
        for h in holes:
            for kind in ("tee", "pin"):
                f.write(f'<wpt lat="{h[kind][0]}" lon="{h[kind][1]}"><name>H{h["no"]} {kind.title()}</name></wpt>\n')
        f.write("</gpx>\n")

    print(f"{d['name']}: {len(holes)} holes, par {sum(h['par'] for h in holes)}, "
          f"{sum(h['yards'] for h in holes)} yds ({', '.join(str(h['yards']) for h in holes)}), "
          f"{len(near)} buildings")
    print(f"wrote {out} and {gpx}")
    for w in warnings:
        print("  WARNING", w)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    coursedir.add_arg(ap)
    build(ap.parse_args().course)
