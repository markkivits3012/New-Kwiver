#!/usr/bin/env python3
"""Add trees, slopes and structures to the trolley's keep-out map, once, on a
PC with internet.

    python tools/build_terrain.py            # fetch, build server/terrain.json
    python tools/build_terrain.py --check    # also write data/terrain_check.png
    python tools/build_terrain.py --course office --check   # a course in courses/<id>/

OSM barely maps the trees here, so they come from the aerial photo instead:
USDA NAIP has a near-infrared band, and tree canopy is darker and redder in
NIR (higher NDVI) than mown turf. Slopes come from the USGS 3DEP bare-earth
elevation model. Both are public domain. Buildings, walls, fences, wetlands
and untagged sand come from data/osm_map.json (ODbL).

The output is on the same 3 m grid as server/keepout.py builds from
app/course.js, so rerun this after rebuilding course.js. KeepOut loads it if
present; without it, routes only avoid greens, tees, bunkers and water.

Needs Pillow on the PC. The Pi only reads server/terrain.json.
"""
import argparse
import base64
import io
import json
import math
import os
import sys
import urllib.parse
import urllib.request

from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import coursedir  # noqa: E402
from keepout import CODES, KeepOut  # noqa: E402

# set per --course in main()
OUT = os.path.join(ROOT, "server", "terrain.json")
CHECK = os.path.join(ROOT, "data", "terrain_check.png")
OSM = os.path.join(ROOT, "data", "osm_map.json")
NAIP = "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage"
DEM = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
R = 6378137.0  # web mercator sphere

# Tree canopy vs. turf, measured on this course's NAIP (fairways vs. the one
# mapped wood): turf NDVI p50 0.11, woods 0.22; turf brightness p50 143, woods 106.
TREE_NDVI, TREE_BRIGHT = 0.17, 125
TREE_VOTES = 5        # of the 9 one-meter pixels in a 3 m cell
TREE_MIN_CELLS = 3    # smaller patches are shadows or single shrubs
# OSM extras: (tag, value or None for any) -> (keep-out layer, buffer m)
OSM_EXTRAS = [
    (("natural", "sand"), ("bunker", 1.0)),
    (("natural", "wetland"), ("water", 5.0)),
    (("leisure", "pitch"), ("structure", 1.0)),
    (("building", None), ("structure", 2.0)),
    (("barrier", "fence"), ("structure", 2.0)),
    (("barrier", "wall"), ("structure", 2.0)),
    (("barrier", "retaining_wall"), ("structure", 2.0)),
    # Public roads, buffered from the centerline (half the road plus a margin);
    # service roads and driveways are left open: they run through lots and
    # clubhouse aprons, and the course outline already fences those in.
    (("highway", "motorway"), ("road", 25.0)),
    (("highway", "motorway_link"), ("road", 15.0)),
    (("highway", "trunk"), ("road", 20.0)),
    (("highway", "primary"), ("road", 20.0)),
    (("highway", "secondary"), ("road", 18.0)),
    (("highway", "tertiary"), ("road", 15.0)),
    (("highway", "unclassified"), ("road", 12.0)),
    (("highway", "residential"), ("road", 12.0)),
    (("amenity", "parking"), ("road", 3.0)),
]


def merc(p):
    return R * math.radians(p[1]), R * math.log(math.tan(math.pi / 4 + math.radians(p[0]) / 2))


def fetch(service, bbox, size, **params):
    q = urllib.parse.urlencode({"bbox": ",".join(map(str, bbox)), "bboxSR": 3857, "imageSR": 3857,
                                "size": f"{size[0]},{size[1]}", "f": "image", **params})
    req = urllib.request.Request(f"{service}?{q}", headers={"User-Agent": "tashua-golf-map/0.1"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode != "F":
        img = img.convert("RGB")
    print(f"  {service.split('/services/')[1].split('/')[0]} bands={params.get('bandIds', '-')}: "
          f"{img.size[0]} x {img.size[1]} {img.mode}, {len(data) / 1e6:.1f} MB")
    return img


class Raster:
    """An image fetched over the keep-out grid's area, sampled by lat/lon."""

    def __init__(self, k, service, ground_m, **params):
        corners = [k.latlon((k.x0 + a * k.w * k.c, k.y0 + b * k.h * k.c)) for a in (0, 1) for b in (0, 1)]
        ms = [merc(p) for p in corners]
        pad = 20 / math.cos(math.radians(k.lat0))
        self.bbox = (min(m[0] for m in ms) - pad, min(m[1] for m in ms) - pad,
                     max(m[0] for m in ms) + pad, max(m[1] for m in ms) + pad)
        per_px = ground_m / math.cos(math.radians(k.lat0))  # mercator m per px
        self.size = (round((self.bbox[2] - self.bbox[0]) / per_px), round((self.bbox[3] - self.bbox[1]) / per_px))
        self.img = fetch(service, self.bbox, self.size, **params)
        self.px = self.img.load()

    def pixel(self, p):
        """Fractional pixel position of lat/lon p (pixel centers at .0)."""
        mx, my = merc(p)
        return ((mx - self.bbox[0]) / (self.bbox[2] - self.bbox[0]) * self.size[0] - 0.5,
                (self.bbox[3] - my) / (self.bbox[3] - self.bbox[1]) * self.size[1] - 0.5)

    def near(self, p, d=0):
        x, y = self.pixel(p)
        x = min(max(round(x) + d, 0), self.size[0] - 1)
        return x, min(max(round(y), 0), self.size[1] - 1)

    def bilinear(self, p):
        x, y = self.pixel(p)
        x = min(max(x, 0), self.size[0] - 1.001)
        y = min(max(y, 0), self.size[1] - 1.001)
        i, j = int(x), int(y)
        fx, fy = x - i, y - j
        a, b = self.px[i, j], self.px[i + 1, j]
        c, d = self.px[i, j + 1], self.px[i + 1, j + 1]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def is_tree(nrg, rgb):
    nir, r = nrg[0], nrg[1]
    ndvi = (nir - r) / (nir + r) if nir + r else 0.0
    return ndvi > TREE_NDVI and sum(rgb) / 3 < TREE_BRIGHT


def trees(k, nrg, rgb):
    """1 per 3 m cell that is mostly tree canopy, off fairways, greens, tees and cart paths."""
    out = bytearray(k.w * k.h)
    for j in range(k.h):
        for i in range(k.w):
            kk = j * k.w + i
            if k.code[kk] == 1 or k.fairway[kk] or k.path[kk] or k.code[kk] in (CODES["green"], CODES["tee"]):
                continue  # outside the course anyway, or turf/paths where shadows aren't trees
            cx, cy = k._center(i, j)
            votes = 0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    p = k.latlon((cx + dx, cy + dy))
                    votes += is_tree(nrg.px[nrg.near(p)], rgb.px[rgb.near(p)])
            out[kk] = votes >= TREE_VOTES
    # drop small patches (8-connected)
    seen = bytearray(k.w * k.h)
    dropped = 0
    for s in range(k.w * k.h):
        if not out[s] or seen[s]:
            continue
        blob, stack = [], [s]
        seen[s] = 1
        while stack:
            kk = stack.pop()
            blob.append(kk)
            i, j = kk % k.w, kk // k.w
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    ni, nj = i + di, j + dj
                    if 0 <= ni < k.w and 0 <= nj < k.h:
                        nk = nj * k.w + ni
                        if out[nk] and not seen[nk]:
                            seen[nk] = 1
                            stack.append(nk)
        if len(blob) < TREE_MIN_CELLS:
            for kk in blob:
                out[kk] = 0
            dropped += 1
    print(f"trees: {sum(out):,} cells ({sum(out) * k.c * k.c / 1e4:.1f} ha), {dropped} small patches dropped")
    return out


def slopes(k, dem):
    """Grade in percent per cell (central differences over the 3 m grid), capped at 255."""
    z = [dem.bilinear(k.latlon(k._center(i, j))) for j in range(k.h) for i in range(k.w)]
    print(f"elevation: {min(z):.1f} to {max(z):.1f} m")
    out = bytearray(k.w * k.h)
    for j in range(k.h):
        for i in range(k.w):
            i0, i1 = max(i - 1, 0), min(i + 1, k.w - 1)
            j0, j1 = max(j - 1, 0), min(j + 1, k.h - 1)
            gx = (z[j * k.w + i1] - z[j * k.w + i0]) / ((i1 - i0) * k.c)
            gy = (z[j1 * k.w + i] - z[j0 * k.w + i]) / ((j1 - j0) * k.c)
            out[j * k.w + i] = min(255, round(math.hypot(gx, gy) * 100))
    return out


def osm_extras(k):
    """Keep-out codes for OSM features course.js doesn't carry: structures, wetlands, sand."""
    with open(OSM, encoding="utf-8") as f:
        els = json.load(f)["elements"]
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node"}
    ways = {e["id"]: e for e in els if e["type"] == "way"}
    out = bytearray(k.w * k.h)
    counts = {}
    for (tag, val), (layer, buf) in OSM_EXTRAS:  # later entries win where they overlap
        for e in els:
            have = e.get("tags", {}).get(tag)
            if have is None or (val is not None and have != val):
                continue
            if e["type"] == "way":
                lines = [e["nodes"]]
            elif e["type"] == "relation":
                lines = [ways[m["ref"]]["nodes"] for m in e["members"] if m["type"] == "way" and m["ref"] in ways]
            else:
                continue
            for ids in lines:
                pts = [k.xy(nodes[n]) for n in ids if n in nodes]
                if len(pts) < 2:
                    continue
                if ids[0] == ids[-1] and len(pts) >= 4:
                    k._fill(pts, CODES[layer], only_free=False, grid=out)
                k._buffer(pts, CODES[layer], buf, grid=out)
            counts[f"{tag}={val or '*'}"] = counts.get(f"{tag}={val or '*'}", 0) + 1
    print("osm extras:", ", ".join(f"{n} {t}" for t, n in counts.items()))
    return out


def check_image(k, rgb, tree, slope, extra, steep):
    """The photo on the keep-out grid (1 m/px), tinted: trees green, steep red,
    OSM structures blue, roads/parking orange, existing keep-out grey. North up."""
    img = Image.new("RGB", (k.w, k.h))
    px = img.load()
    for j in range(k.h):
        for i in range(k.w):
            kk = j * k.w + i
            r, g, b = rgb.px[rgb.near(k.latlon(k._center(i, j)))][:3]
            tint = ((40, 220, 60) if tree[kk] else (240, 40, 40) if slope[kk] > steep else
                    (60, 120, 255) if extra[kk] == CODES["structure"] else
                    (255, 150, 0) if extra[kk] == CODES["road"] else
                    (150, 150, 150) if k.code[kk] not in (0, 1) else None)
            if tint:
                r, g, b = ((c * 4 + t * 6) // 10 for c, t in zip((r, g, b), tint))
            elif k.code[kk] == 1:
                r, g, b = r // 2, g // 2, b // 2
            px[i, k.h - 1 - j] = (r, g, b)
    img = img.resize((k.w * 3, k.h * 3), Image.NEAREST)
    img.save(CHECK)
    print(f"wrote {CHECK}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="also write data/terrain_check.png")
    coursedir.add_arg(ap)
    args = ap.parse_args()
    global OUT, CHECK, OSM
    OUT = coursedir.path(args.course, "server", "terrain.json")
    CHECK = coursedir.path(args.course, "data", "terrain_check.png")
    OSM = coursedir.path(args.course, "data", "osm_map.json")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    course = coursedir.load_course(args.course)
    k = KeepOut(course, terrain=None)
    print(f"grid: {k.w} x {k.h} cells of {k.c:g} m")

    print("fetching (public domain: USDA NAIP, USGS 3DEP)")
    rgb = Raster(k, NAIP, 1.0, format="png", bandIds="0,1,2")
    nrg = Raster(k, NAIP, 1.0, format="png", bandIds="3,0,1")
    dem = Raster(k, DEM, k.c, format="tiff", pixelType="F32")

    tree = trees(k, nrg, rgb)
    slope = slopes(k, dem)
    extra = osm_extras(k)
    block = bytearray(max(t * CODES["tree"], e) for t, e in zip(tree, extra))

    data = {"source": "USDA NAIP + USGS 3DEP (public domain); OpenStreetMap contributors (ODbL 1.0)",
            "course_built": course["built"], "grid": k.grid_key(),
            "block": base64.b64encode(block).decode(), "slope_pct": base64.b64encode(slope).decode()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"wrote {OUT} ({os.path.getsize(OUT):,} bytes)")

    from keepout import STEEP_PCT
    if args.check:
        check_image(k, rgb, tree, slope, extra, STEEP_PCT)
    k2 = KeepOut(course, terrain=OUT)
    free0, free1 = k.code.count(0), k2.code.count(0)
    print(f"free cells: {free0:,} -> {free1:,} ({100 * (free0 - free1) / free0:.0f}% newly blocked)")
    for code in (CODES["tree"], CODES["steep"], CODES["structure"], CODES["road"]):
        print(f"  {k2.code.count(code):>7,}  {next(n for n, c in CODES.items() if c == code)}")


if __name__ == "__main__":
    main()
