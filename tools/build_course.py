#!/usr/bin/env python3
"""Turn an OpenStreetMap download into app/course.js for the cart app.

Run on a PC with internet once; the Pi only ever reads course.js.

    python build_course.py                    # Tashua Knolls, from data/osm_map.json
                                              # + its KML and recorded cart route
    python build_course.py --download        # fetch fresh OSM data, then build
    python build_course.py --course whitney --download
                                              # any course in courses/<id>/ with a
                                              # course.json (tools/new_course.py
                                              # writes one); holes, greens, tees and
                                              # hazards all from OSM
    python build_course.py --course glen      # the 9-hole Tashua Glen instead
    python build_course.py --kml data/kml/tashua_knolls.kml
                                              # holes (tees, pins, targets,
                                              # center lines, par/yards, flyover
                                              # tours) from a Google Earth KML;
                                              # OSM still supplies greens/hazards
    python build_course.py --track data/waypoints/waypoints.gpx
                                              # add a recorded cart route (GPX
                                              # waypoints/track points off the
                                              # course are dropped)

Map data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import argparse
import json
import math
import os
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import coursedir  # noqa: E402

RAW = os.path.join(ROOT, "data", "osm_map.json")   # set per course in config()
OUT = os.path.join(ROOT, "app", "course.js")

# lon_min, lat_min, lon_max, lat_max -- covers Knolls and Glen
BBOX = (-73.263, 41.279, -73.241, 41.293)
OSM_API = "https://api.openstreetmap.org/api/0.6/map.json?bbox={},{},{},{}"

# The two Tashua courses (project root). Other courses come from courses/<id>/course.json.
COURSES = {
    "knolls": {"name": "Tashua Knolls", "outline": "way/307524932",
               "hole_ref": lambda r: r.isdigit(), "hole_no": int,
               "kml": "data/kml/tashua_knolls.kml", "track": "data/waypoints/waypoints.gpx"},
    "glen": {"name": "Tashua Glen", "outline": "way/307524931",
             "hole_ref": lambda r: r.startswith("TG-"), "hole_no": lambda r: int(r[3:])},
}

# OSM tag -> layer drawn by the app (draw order is set in app.js)
LAYERS = {
    ("golf", "fairway"): "fairway",
    ("golf", "rough"): "rough",
    ("golf", "green"): "green",
    ("golf", "tee"): "tee",
    ("golf", "bunker"): "bunker",
    ("golf", "water_hazard"): "water",
    ("golf", "lateral_water_hazard"): "water",
    ("natural", "water"): "water",
    ("golf", "cartpath"): "cartpath",
    ("golf", "path"): "cartpath",
    ("golf", "clubhouse"): "building",
}


def download():
    url = OSM_API.format(*BBOX)
    print(f"downloading OpenStreetMap data for {BBOX} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "tashua-golf-map/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    os.makedirs(os.path.dirname(RAW), exist_ok=True)
    with open(RAW, "wb") as f:
        f.write(data)
    print(f"downloaded {len(data):,} bytes -> {RAW}")


def meters(a, b):
    """Rough ground distance between (lat, lon) points; fine at course scale."""
    lat0 = math.radians((a[0] + b[0]) / 2)
    dy = (b[0] - a[0]) * 110540
    dx = (b[1] - a[1]) * 111320 * math.cos(lat0)
    return math.hypot(dx, dy)


def centroid(ring):
    return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))


def inside(p, ring):
    c = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[0] > p[0]) != (b[0] > p[0]) and p[1] < a[1] + (p[0] - a[0]) * (b[1] - a[1]) / (b[0] - a[0]):
            c = not c
    return c


def outline_ring(els, ways, way_pts, ref):
    """The course boundary from 'way/<id>' or 'relation/<id>' (a multipolygon's
    outer ways joined end to end; the largest ring if there are several)."""
    kind, _, oid = ref.partition("/")
    oid = int(oid)
    if kind == "way":
        return way_pts(ways[oid]) if oid in ways else []
    rel = next((e for e in els if e["type"] == "relation" and e["id"] == oid), None)
    if not rel:
        return []
    parts = [way_pts(ways[m["ref"]]) for m in rel["members"]
             if m["type"] == "way" and m.get("role") in ("outer", "") and m["ref"] in ways]
    rings = []
    while parts:
        ring = parts.pop(0)
        while ring[0] != ring[-1]:
            nxt = next((i for i, w in enumerate(parts) if ring[-1] in (w[0], w[-1])), None)
            if nxt is None:
                break
            w = parts.pop(nxt)
            ring += (w if w[0] == ring[-1] else w[::-1])[1:]
        rings.append(ring)

    def area(r):
        return abs(sum(a[1] * b[0] - b[1] * a[0] for a, b in zip(r, r[1:] + r[:1])))
    return max(rings, key=area, default=[])


def default_tour(tee, pin, yards):
    """A simple flyover for a hole with no Google Earth tour: tee, halfway, green, back."""
    lat0 = math.radians((tee[0] + pin[0]) / 2)
    hdg = round(math.degrees(math.atan2((pin[1] - tee[1]) * math.cos(lat0), pin[0] - tee[0])), 2)
    mid = (round((tee[0] + pin[0]) / 2, 6), round((tee[1] + pin[1]) / 2, 6))
    far = 90.0 if yards > 250 else 60.0

    def step(p, tilt, rng, dur, hold):
        return {"lat": p[0], "lon": p[1], "heading": hdg, "tilt": tilt, "range": rng, "dur": dur, "hold": hold}
    return [step(tee, 70.0, far, 5.0, 1.0), step(mid, 74.0, far * 0.8, 4.0, 1.0),
            step(pin, 75.0, 60.0, 4.0, 2.0), step(tee, 70.0, far, 5.0, 3.0)]


def read_track(path):
    """GPX wpt/trkpt/rtept in file order, keeping only points inside BBOX."""
    import re
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pts = [(round(float(a), 6), round(float(b), 6)) for a, b in
           re.findall(r'<(?:wpt|trkpt|rtept)\s+lat="([-\d.]+)"\s+lon="([-\d.]+)"', text)]
    kept = [p for p in pts if BBOX[1] <= p[0] <= BBOX[3] and BBOX[0] <= p[1] <= BBOX[2]]
    print(f"track: {len(kept)} of {len(pts)} points inside the course area")
    return kept


def read_kml(path):
    """Holes from a Google Earth flyover KML: 'Hole N Tee/Pin/Target k/Center
    Line' placemarks plus one 'Hole N - Par P - Y yards' tour per hole."""
    import re
    import xml.etree.ElementTree as ET
    ns = {"k": "http://www.opengis.net/kml/2.2", "gx": "http://www.google.com/kml/ext/2.2"}
    root = ET.parse(path).getroot()

    def coords(pm):
        text = pm.findtext(".//k:coordinates", default="", namespaces=ns)
        pts = []
        for c in text.split():
            lon, lat = c.split(",")[:2]
            p = (round(float(lat), 6), round(float(lon), 6))
            if not pts or pts[-1] != p:  # tee/pin "posts" are the same point twice
                pts.append(p)
        return pts

    holes = {}
    for pm in root.iter("{%s}Placemark" % ns["k"]):
        m = re.match(r"Hole (\d+) (Tee|Pin|Target \d+|Center Line)$", pm.findtext("k:name", "", ns))
        if not m:
            continue
        h = holes.setdefault(int(m.group(1)), {"targets": []})
        pts = coords(pm)
        if m.group(2) == "Center Line":
            h["line"] = pts
        elif m.group(2).startswith("Target"):
            h["targets"].append(pts[0])
        else:
            h[m.group(2).lower()] = pts[0]

    for tour in root.iter("{%s}Tour" % ns["gx"]):
        m = re.match(r"Hole (\d+) - Par (\d+) - (\d+) yards", tour.findtext("k:name", "", ns))
        if not m or int(m.group(1)) not in holes:
            continue
        h = holes[int(m.group(1))]
        h["par"], h["yards"] = int(m.group(2)), int(m.group(3))
        steps = []
        for el in tour.find("gx:Playlist", ns):
            dur = float(el.findtext("gx:duration", "0", ns))
            if el.tag.endswith("}Wait") and steps:
                steps[-1]["hold"] += dur
            elif el.tag.endswith("}FlyTo"):
                la = el.find("k:LookAt", ns)
                steps.append({
                    "lat": round(float(la.findtext("k:latitude", namespaces=ns)), 6),
                    "lon": round(float(la.findtext("k:longitude", namespaces=ns)), 6),
                    "heading": round(float(la.findtext("k:heading", namespaces=ns)), 2),
                    "tilt": round(float(la.findtext("k:tilt", namespaces=ns)), 1),
                    "range": round(float(la.findtext("k:range", namespaces=ns)), 1),
                    "dur": dur, "hold": 0.0,
                })
        h["tour"] = steps

    out = []
    for no, h in sorted(holes.items()):
        if "line" not in h:
            h["line"] = [h["tee"], h["pin"]]
        out.append({"no": no, "par": h.get("par"), "hcp": None, "yards": h.get("yards"),
                    "line": h["line"], "tee": h.get("tee"), "pin": h.get("pin"),
                    "targets": h["targets"], "tour": h.get("tour", [])})
    print(f"kml: {len(out)} holes, {sum(len(h['tour']) for h in out)} flyover steps")
    return out


def build(cfg, track_path=None, kml_path=None):
    with open(RAW, encoding="utf-8") as f:
        els = json.load(f)["elements"]

    nodes = {e["id"]: (round(e["lat"], 6), round(e["lon"], 6)) for e in els if e["type"] == "node"}
    ways = {e["id"]: e for e in els if e["type"] == "way"}

    def way_pts(w):
        return [nodes[n] for n in w["nodes"] if n in nodes]

    outline = outline_ring(els, ways, way_pts, cfg["outline"])
    if not outline:
        print(f"  WARNING course boundary {cfg['outline']} not found in the OSM download")

    features = []  # {"layer", "kind": "poly"|"line", "rings"|"pts"}
    for e in els:
        tags = e.get("tags", {})
        layer = next((v for (k, val), v in LAYERS.items() if tags.get(k) == val), None)
        if not layer:
            continue
        if e["type"] == "way":
            pts = way_pts(e)
            if len(pts) < 2:
                continue
            closed = e["nodes"][0] == e["nodes"][-1] and layer != "cartpath"
            features.append({"layer": layer, "kind": "poly", "rings": [pts]} if closed
                            else {"layer": layer, "kind": "line", "pts": pts})
        elif e["type"] == "relation" and tags.get("type") == "multipolygon":
            rings = [way_pts(ways[m["ref"]]) for m in e["members"]
                     if m["type"] == "way" and m["ref"] in ways]
            rings = [r for r in rings if len(r) >= 3]
            if rings:
                features.append({"layer": layer, "kind": "poly", "rings": rings})

    greens = [f["rings"][0] for f in features if f["layer"] == "green" and f["kind"] == "poly"]
    tees = [f["rings"][0] for f in features if f["layer"] == "tee" and f["kind"] == "poly"]

    warnings = []
    if kml_path:
        src = read_kml(kml_path)
    else:
        src = []
        for e in els:
            tags = e.get("tags", {})
            if e["type"] != "way" or tags.get("golf") != "hole" or not cfg["hole_ref"](tags.get("ref", "")):
                continue
            line = way_pts(e)
            if cfg.get("inside_only") and outline and not inside(line[len(line) // 2], outline):
                continue  # another course's hole in the same download
            par = int(tags["par"]) if tags.get("par", "").isdigit() else None
            src.append({
                "no": cfg["hole_no"](tags["ref"]),
                "par": par,
                "hcp": int(tags["handicap"]) if tags.get("handicap", "").isdigit() else None,
                "yards": round(sum(meters(a, b) for a, b in zip(line, line[1:])) * 1.09361),
                "line": line,
            })

    # Each tee box belongs to the one hole whose line starts nearest it; tees
    # near the clubhouse sit within 80 m of several holes' starts.
    tee_owner = {}
    for i, t in enumerate(tees):
        no, d = min(((h["no"], meters(centroid(t), h["line"][0])) for h in src), key=lambda x: x[1])
        if d < 80:
            tee_owner[i] = no

    def near_green(p):
        g = min(greens, key=lambda g: meters(centroid(g), p), default=None)
        return g if g and meters(centroid(g), p) <= 60 else None

    # An OSM hole line sometimes stops short of its green. Extend it to a green
    # no other hole claims, if one lies ahead within 250 m.
    claimed = {id(near_green(h.get("pin") or h["line"][-1])) for h in src}
    for h in src:
        line = h["line"]
        if h.get("pin") or near_green(line[-1]) or len(line) < 2:
            continue
        a, b = line[-2], line[-1]
        ahead = [g for g in greens if id(g) not in claimed and meters(b, centroid(g)) < 250
                 and (centroid(g)[0] - b[0]) * (b[0] - a[0]) + (centroid(g)[1] - b[1]) * (b[1] - a[1]) > 0]
        if ahead:
            g = min(ahead, key=lambda g: meters(b, centroid(g)))
            c = tuple(round(v, 6) for v in centroid(g))
            claimed.add(id(g))
            warnings.append(f"hole {h['no']}: OSM hole line stops {meters(b, c):.0f} m short of a green; "
                            f"extended it to that green -- check on the map")
            h["line"] = line + [c]
            h["yards"] = round(sum(meters(p, q) for p, q in zip(h["line"], h["line"][1:])) * 1.09361)

    holes = []
    for h in src:
        no, line = h["no"], h["line"]
        pin = h.get("pin") or line[-1]
        green = near_green(pin)
        if green is None:
            warnings.append(f"hole {no}: no green polygon near the pin")
            green = None
        hole_tees = [centroid(t) for i, t in enumerate(tees) if tee_owner.get(i) == no]
        if h.get("tee"):
            hole_tees.insert(0, h["tee"])
        if not h.get("tour"):
            h["tour"] = default_tour(line[0], pin, h["yards"] or 0)
        if not hole_tees:
            warnings.append(f"hole {no}: no tee box mapped near the start of the hole line")
        if h["par"] not in (3, 4, 5, 6):
            warnings.append(f"hole {no}: par is {h['par']!r} -- check the scorecard")
        # OSM-only holes: tee, pin and aim points (dogleg bends) from the hole
        # line, skipping bend nodes within 40 m of the last one or of the pin
        targets = []
        for p in line[1:-1]:
            if meters(p, (targets or line)[-1 if targets else 0]) >= 40 and meters(p, pin) >= 40:
                targets.append(p)
        holes.append({"tee": line[0], "pin": pin, "targets": targets,
                      **h, "green": green, "tees": hole_tees})
    holes.sort(key=lambda h: h["no"])

    missing = sorted(set(range(1, len(holes) + 1)) - {h["no"] for h in holes})
    warnings += [f"hole {n}: no hole line" for n in missing]

    course = {
        "name": cfg["name"],
        "source": "OpenStreetMap contributors (ODbL 1.0)",
        "built": __import__("datetime").date.today().isoformat(),
        "outline": outline,
        "holes": holes,
        "features": features,
        "track": read_track(track_path) if track_path else [],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// Generated by tools/build_course.py -- do not edit by hand.\n")
        f.write("// Map data (c) OpenStreetMap contributors, ODbL 1.0.\n")
        f.write("window.COURSE = ")
        json.dump(course, f, separators=(",", ":"))
        f.write(";\n")

    print(f"{cfg['name']}: {len(holes)} holes, par {sum(h['par'] or 0 for h in holes)}, "
          f"{sum(h['yards'] for h in holes):,} yds (tee-line), {len(features)} features")
    print(f"wrote {OUT} ({os.path.getsize(OUT):,} bytes)")
    for w in warnings:
        print("  WARNING", w)


def config(course_id):
    """Build settings for a course id: the Tashua courses above, or courses/<id>/course.json."""
    global RAW, OUT, BBOX
    if course_id in ("tashua", "knolls", "glen"):
        return COURSES["knolls" if course_id == "tashua" else course_id]
    with open(coursedir.path(course_id, "course.json"), encoding="utf-8") as f:
        c = json.load(f)
    RAW = coursedir.path(course_id, "data", "osm_map.json")
    OUT = coursedir.path(course_id, "app", "course.js")
    BBOX = tuple(c["bbox"])
    return {"name": c["name"], "outline": c["outline"], "inside_only": True,
            "hole_ref": lambda r: r.isdigit(), "hole_no": int,
            "kml": c.get("kml"), "track": c.get("track")}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="fetch fresh OpenStreetMap data first")
    ap.add_argument("--course", default="tashua",
                    help="tashua (default), glen, or a course id in courses/ with a course.json")
    ap.add_argument("--track", help="GPX of a recorded cart route")
    ap.add_argument("--kml", help="Google Earth flyover KML: tees, pins, targets, tours")
    args = ap.parse_args()
    cfg = config(args.course)
    base = coursedir.base(None if args.course in ("tashua", "knolls", "glen") else args.course)
    kml = args.kml or (cfg.get("kml") and os.path.join(base, cfg["kml"]))
    track = args.track or (cfg.get("track") and os.path.join(base, cfg["track"]))
    if args.download or not os.path.exists(RAW):
        download()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    build(cfg, track, kml)
