"""Course file: one course folder (courses/<id>/) as a .zip, for the course
builder (builder/) to hand out and tools/import_course.py to take in.

    golfmap-course.json      manifest: format, version, id, name, holes, par, built, osm
    <id>/course.json         what tools/build_all.py needs to rebuild it
    <id>/app/...             course.js, scorecard.json, aerial/, terrain3d/, flyovers/, review/
    <id>/server/...          terrain.json, routes.json
    <id>/data/osm_map.json   the OSM download it was built from

Review leftovers (data/terrain_check.png, data/routes/) stay out.
"""
import json
import os
import re
import shutil
import zipfile

MANIFEST = "golfmap-course.json"
FORMAT, VERSION = "golfmap-course", 1
KEEP = ("course.json", "app/", "server/", "data/osm_map.json")
MAX_BYTES = 300_000_000  # unpacked; a big 18-hole course is ~15 MB


def _kept(rel):
    return rel in KEEP or rel.startswith(tuple(k for k in KEEP if k.endswith("/")))


def _course_js(text):
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def pack(folder, cid, out, **extra):
    """Zip the built course in folder (courses/<id>/) to out; returns the manifest."""
    with open(os.path.join(folder, "app", "course.js"), encoding="utf-8") as f:
        c = _course_js(f.read())
    with open(os.path.join(folder, "course.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    man = {"format": FORMAT, "version": VERSION, "id": cid, "name": c["name"],
           "holes": len(c["holes"]), "par": sum(h.get("par") or 0 for h in c["holes"]),
           "built": c.get("built"), "osm": cfg.get("outline"), **extra}
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(man, indent=2) + "\n")
        for d, _, files in os.walk(folder):
            for name in sorted(files):
                full = os.path.join(d, name)
                rel = os.path.relpath(full, folder).replace(os.sep, "/")
                if _kept(rel):
                    z.write(full, f"{cid}/{rel}")
    os.replace(tmp, out)
    return man


def read_manifest(zpath):
    with zipfile.ZipFile(zpath) as z:
        try:
            man = json.loads(z.read(MANIFEST))
        except KeyError:
            raise ValueError(f"not a course file (no {MANIFEST})")
    if man.get("format") != FORMAT:
        raise ValueError("not a course file (wrong format)")
    if man.get("version", 0) > VERSION:
        raise ValueError(f"course file version {man['version']} is newer than this tool; update the repo")
    return man


def unpack(zpath, courses, cid=None, force=False):
    """Put the course in zpath into courses/<cid>/ (cid defaults to the
    manifest's id). Unpacks beside it first, so a bad file never leaves a
    half-written course. Returns (cid, manifest)."""
    man = read_manifest(zpath)
    src = man.get("id", "")
    cid = cid or src
    if not re.fullmatch(r"[a-z0-9_-]+", cid or "") or cid in ("tashua", "knolls", "glen"):
        raise ValueError(f"bad course id {cid!r}")
    dest = os.path.join(courses, cid)
    if os.path.exists(dest) and not force:
        raise ValueError(f"{dest} already exists; pass --force to replace it, or --id to import under another id")
    with zipfile.ZipFile(zpath) as z:
        items = []
        for info in z.infolist():
            if info.filename == MANIFEST or info.is_dir():
                continue
            rel = info.filename[len(src) + 1:] if info.filename.startswith(src + "/") else None
            parts = (rel or "").split("/")
            # no ../, absolute or drive paths, and only the course's own files
            if not rel or "" in parts or ".." in parts or ":" in rel or "\\" in rel or not _kept(rel):
                raise ValueError(f"unexpected file in course file: {info.filename!r}")
            items.append((info, rel))
        if sum(i.file_size for i, _ in items) > MAX_BYTES:
            raise ValueError("course file too big")
        if "app/course.js" not in [r for _, r in items]:
            raise ValueError("course file has no app/course.js")
        tmp = os.path.join(courses, f".{cid}.import")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            for info, rel in items:
                out = os.path.join(tmp, *rel.split("/"))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with z.open(info) as r, open(out, "wb") as w:
                    shutil.copyfileobj(r, w)
            with open(os.path.join(tmp, "app", "course.js"), encoding="utf-8") as f:
                _course_js(f.read())  # must parse, or the cart app can't load it
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    old = os.path.join(courses, f".{cid}.old")
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(dest):
        os.replace(dest, old)
    os.replace(tmp, dest)
    shutil.rmtree(old, ignore_errors=True)
    return cid, man
