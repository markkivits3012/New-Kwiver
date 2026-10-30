"""Where a course's files live.

Tashua Knolls (the default) keeps the original layout at the project root.
Any other course mirrors that layout under courses/<id>/:

    courses/<id>/app/course.js, scorecard.json, aerial/, terrain3d/, flyovers/, review/keepout.png
    courses/<id>/server/terrain.json, routes.json
    courses/<id>/data/osm_map.json, terrain_check.png, routes/gpx/

The app, the review page and all shared code stay in app/ and server/; the
server swaps in only the course's own files (COURSE_FILES) for --course.
"""
import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT = "tashua"
# URL paths under app/ that belong to a course, not to the app
COURSE_FILES = ("course.js", "scorecard.json", "aerial/", "terrain3d/", "flyovers/", "review/keepout.png")


def base(course=DEFAULT):
    if course in (None, DEFAULT):
        return ROOT
    if not re.fullmatch(r"[a-z0-9_-]+", course):
        raise ValueError(f"bad course id {course!r}")
    return os.path.join(ROOT, "courses", course)


def path(course, *parts):
    return os.path.join(base(course), *parts)


def load_course(course=DEFAULT):
    with open(path(course, "app", "course.js"), encoding="utf-8") as f:
        text = f.read()
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def ids():
    """Every installed course: tashua, then each courses/<id>/ that has a course.js."""
    found = [DEFAULT] if os.path.isfile(path(DEFAULT, "app", "course.js")) else []
    folder = os.path.join(ROOT, "courses")
    for c in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if re.fullmatch(r"[a-z0-9_-]+", c) and os.path.isfile(path(c, "app", "course.js")):
            found.append(c)
    return found


def summary(course):
    """What the course menu and course detection need: name, size and a
    lat/lon box [south, west, north, east] around the course."""
    c = load_course(course)
    pts = c["outline"] or [p for h in c["holes"] for p in h["line"]]
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return {"id": course, "name": c["name"], "holes": len(c["holes"]),
            "par": sum(h.get("par") or 0 for h in c["holes"]),
            "bbox": [min(lats), min(lons), max(lats), max(lons)]}


def add_arg(ap):
    ap.add_argument("--course", default=DEFAULT,
                    help=f"course id: {DEFAULT} (default, project root) or a folder in courses/")
