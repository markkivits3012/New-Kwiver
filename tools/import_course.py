#!/usr/bin/env python3
"""Import a course file (a .zip from the course builder, builder/) into courses/<id>/.

    python tools/import_course.py whitney-farms.zip
    python tools/import_course.py whitney-farms.zip --id whitney2   # under another id
    python tools/import_course.py whitney-farms.zip --force         # replace courses/<id>/

Then try it on the PC, commit, and ship it with tools/make_update.py:

    python server/gps_server.py --sim --course <id>
"""
import argparse
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TOOLS, "..", "server"))
import coursedir  # noqa: E402
import coursepack  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", help="course file (.zip) from the course builder")
    ap.add_argument("--id", help="course id to import as (default: the one in the file)")
    ap.add_argument("--force", action="store_true", help="replace courses/<id>/ if it exists")
    args = ap.parse_args()
    os.makedirs(coursedir.COURSES, exist_ok=True)
    try:
        cid, man = coursepack.unpack(args.zip, coursedir.COURSES, args.id, args.force)
    except (ValueError, OSError) as e:
        sys.exit(f"not imported: {e}")
    except Exception as e:  # zipfile.BadZipFile and friends
        sys.exit(f"not imported: {e.__class__.__name__}: {e}")
    par = f"par {man['par']}" if man.get("par") else "no pars in OSM: add a scorecard"
    print(f"imported {man['name']} ({man['holes']} holes, {par}, built {man.get('built')})")
    print(f"     -> {coursedir.base(cid)}")
    print(f"\ntry it:  python server/gps_server.py --sim --course {cid}")
    print("then commit and pack:  python tools/make_update.py D:\\")


if __name__ == "__main__":
    main()
