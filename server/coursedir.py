"""Where a course's files live.

Tashua Knolls (the default) keeps the original layout at the project root.
Any other course mirrors that layout under courses/<id>/:

    courses/<id>/app/course.js, aerial/, terrain3d/, flyovers/, review/keepout.png
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
COURSE_FILES = ("course.js", "aerial/", "terrain3d/", "flyovers/", "review/keepout.png")


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


def add_arg(ap):
    ap.add_argument("--course", default=DEFAULT,
                    help=f"course id: {DEFAULT} (default, project root) or a folder in courses/")
