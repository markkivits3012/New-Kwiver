#!/usr/bin/env python3
"""Start a new course: find it in OpenStreetMap and write courses/<id>/course.json.

    python tools/new_course.py whitney "Whitney Farms Golf Course, Monroe CT"

The id is short, lowercase letters/digits/-/_ (it becomes the folder name and
what you pass as --course). The search text is anything Nominatim understands;
it must find an OSM golf course (leisure=golf_course). Then run:

    python tools/build_all.py --course <id>
"""
import json
import math
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import coursedir  # noqa: E402

NOMINATIM = "https://nominatim.openstreetmap.org/search?"
PAD_M = 150  # around the course boundary, so tees/paths at the edge are kept


def search(text):
    q = urllib.parse.urlencode({"q": text, "format": "jsonv2", "limit": 10})
    req = urllib.request.Request(NOMINATIM + q, headers={"User-Agent": "tashua-golf-map/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        hits = json.loads(r.read().decode("utf-8"))
    return [h for h in hits if h.get("category") == "leisure" and h.get("type") == "golf_course"
            and h.get("osm_type") in ("way", "relation")]


def config(h, name):
    """course.json for a Nominatim hit; also used by builder/builder.py."""
    s, n, w, e = (float(v) for v in h["boundingbox"])
    dlat = PAD_M / 110540
    dlon = PAD_M / (111320 * math.cos(math.radians((s + n) / 2)))
    return {"name": h["name"] or name,
            "bbox": [round(w - dlon, 5), round(s - dlat, 5), round(e + dlon, 5), round(n + dlat, 5)],
            "outline": f"{h['osm_type']}/{h['osm_id']}",
            "share": True}


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    cid, text = sys.argv[1], sys.argv[2]
    if cid in (coursedir.DEFAULT, "knolls", "glen"):
        sys.exit(f"{cid!r} is a built-in course; pick another id")
    try:
        folder = coursedir.base(cid)
    except ValueError as e:
        sys.exit(str(e))
    out = os.path.join(folder, "course.json")
    if os.path.exists(out):
        sys.exit(f"{out} already exists; delete it first to start over")

    hits = search(text)
    if not hits:
        sys.exit(f"no golf course found in OpenStreetMap for {text!r}; try adding the town and state")
    for h in hits[1:]:
        print(f"  (also found: {h['display_name']})")
    h = hits[0]
    course = config(h, text.split(",")[0])
    os.makedirs(folder, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(course, f, indent=2)
        f.write("\n")
    print(f"found: {h['display_name']}")
    print(f"wrote {out}")
    print(json.dumps(course, indent=2))
    print(f"\nnext:  python tools/build_all.py --course {cid}")


if __name__ == "__main__":
    main()
