#!/usr/bin/env python3
"""Download the aerial photo the map is drawn on, once, on a PC with internet.

    python tools/fetch_aerial.py
    python tools/fetch_aerial.py --course office   # a course in courses/<id>/

Source: USDA NAIP orthoimagery from the USGS National Map (public domain).
The course area (from app/course.js, plus a margin) is fetched as one image
up to 4000 px (~0.35 m/px), lightly toned for a 5" screen in sunlight, and
cut into a 2x2 grid of JPEG tiles (each under 2048 px, which every Pi 4 GPU
can hold). A wider, coarser photo goes underneath so views near the edge of
the course aren't empty. Writes app/aerial/*.jpg and app/aerial/aerial.js.

Needs Pillow on the PC (pip install pillow). The Pi only reads the output.
"""
import io
import json
import math
import os
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageEnhance, ImageFilter

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import coursedir  # noqa: E402
OUT = os.path.join(ROOT, "app", "aerial")  # set per --course in main()
COURSE = coursedir.DEFAULT
SERVICE = "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage"
MARGIN_M = 80        # sharp layer: course + this much
MAX_PX = 4000
GRID = 2
WIDE_MARGIN_M = 900  # coarse layer underneath, so views near the edge aren't empty
WIDE_PX = 2000
FINEST_M = 0.25     # NAIP here is ~0.3-0.6 m; finer just upsamples (small test courses)


def course_bounds(margin_m=MARGIN_M):
    c = coursedir.load_course(COURSE)
    pts = c["outline"] + [p for h in c["holes"] for p in h["line"]]
    lat0 = sum(p[0] for p in pts) / len(pts)
    dlat = margin_m / 110540
    dlon = margin_m / (111320 * math.cos(math.radians(lat0)))
    return (min(p[0] for p in pts) - dlat, max(p[0] for p in pts) + dlat,
            min(p[1] for p in pts) - dlon, max(p[1] for p in pts) + dlon, lat0)


R = 6378137.0  # web mercator sphere


def merc_y(lat):
    return R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def merc_lat(y):
    return math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def fetch_layer(margin_m, max_px, grid, prefix):
    """Download one layer (course + margin_m, longest side max_px) as a grid x grid set of tiles."""
    south, north, west, east, lat0 = course_bounds(margin_m)
    # Ask in web mercator (EPSG:3857): pixels come back square on the ground.
    # (In EPSG:4326 the server keeps pixels square in *degrees* and silently
    # stretches the area to fit, which shifted the photo ~25 m.)
    x0m, x1m = R * math.radians(west), R * math.radians(east)
    y0m, y1m = merc_y(south), merc_y(north)
    scale = max_px / max(x1m - x0m, y1m - y0m)         # px per mercator meter
    scale = min(scale, math.cos(math.radians(lat0)) / FINEST_M)
    w, h = round((x1m - x0m) * scale), round((y1m - y0m) * scale)
    ground = math.cos(math.radians(lat0)) / scale       # real meters per px
    print(f"{prefix}: {w} x {h} px ({ground:.2f} m/px on the ground)")

    q = urllib.parse.urlencode({
        "bbox": f"{x0m},{y0m},{x1m},{y1m}", "bboxSR": 3857, "imageSR": 3857,
        "size": f"{w},{h}", "format": "jpg", "compressionQuality": 92,
        "bandIds": "0,1,2", "interpolation": "RSP_BilinearInterpolation", "f": "image",
    })
    req = urllib.request.Request(f"{SERVICE}?{q}", headers={"User-Agent": "tashua-golf-map/0.1"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    print(f"downloaded {len(data) / 1e6:.1f} MB, {img.size[0]} x {img.size[1]}")

    # a touch more contrast and color so it reads on a small screen outdoors
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.15)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))

    os.makedirs(OUT, exist_ok=True)
    tiles = []
    for r in range(grid):
        for c in range(grid):
            x0, x1 = w * c // grid, w * (c + 1) // grid
            y0, y1 = h * r // grid, h * (r + 1) // grid
            name = f"{prefix}_{r}_{c}.jpg"
            img.crop((x0, y0, x1, y1)).save(os.path.join(OUT, name), quality=84, optimize=True, progressive=True)
            # exact lat at each tile edge; inside a ~600 m tile, lat is linear in
            # mercator y to well under a centimeter, so the app can map it linearly
            tiles.append({
                "src": f"aerial/{name}", "w": x1 - x0, "h": y1 - y0,
                "north": merc_lat(y1m - (y1m - y0m) * y0 / h), "south": merc_lat(y1m - (y1m - y0m) * y1 / h),
                "west": west + (east - west) * x0 / w, "east": west + (east - west) * x1 / w,
            })
    return tiles, ground


def main():
    global COURSE, OUT
    import argparse
    ap = argparse.ArgumentParser()
    coursedir.add_arg(ap)
    COURSE = ap.parse_args().course
    OUT = coursedir.path(COURSE, "app", "aerial")
    os.makedirs(OUT, exist_ok=True)
    # back to front: a wide, coarser photo so views near the edge aren't empty,
    # then the sharp course photo on top
    wide, _ = fetch_layer(WIDE_MARGIN_M, WIDE_PX, 1, "wide")
    detail, ground = fetch_layer(MARGIN_M, MAX_PX, GRID, "tile")
    tiles = wide + detail
    meta = {"source": "USDA NAIP via USGS The National Map (public domain)",
            "m_per_px": round(ground, 3), "tiles": tiles}
    with open(os.path.join(OUT, "aerial.js"), "w", encoding="utf-8") as f:
        f.write("// Generated by tools/fetch_aerial.py -- aerial photo tiles and their bounds.\n")
        f.write("window.AERIAL = " + json.dumps(meta, indent=1) + ";\n")
    total = sum(os.path.getsize(os.path.join(OUT, t["src"].split("/")[1])) for t in tiles)
    print(f"wrote {len(tiles)} tiles, {total / 1e6:.1f} MB, to {OUT}")


if __name__ == "__main__":
    main()
