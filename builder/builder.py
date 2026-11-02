#!/usr/bin/env python3
"""Course builder: a PC web page to find a golf course by name, build it with
the tools/ pipeline and download it as a course file (.zip) for
tools/import_course.py.

    python builder/builder.py              # then open http://127.0.0.1:8090

Needs what tools/build_all.py needs: internet and Pillow. Everything it keeps
(accounts, builds, course files) goes in builder/work/ (gitignored). It is a
PC tool: pi/update.sh never installs it.

API (JSON; POSTs need the header X-Trolley-Client: 1, like the cart server):
    POST /api/register   {"user", "password"}   open unless --no-register
    POST /api/login      {"user", "password"}   sets the session cookie
    POST /api/logout
    GET  /api/me         {"user"} or {"user": null}
    GET  /api/search?q=  OSM golf courses matching the name
    POST /api/build      {"osm": "way/123", "id"}   queue a build of a search hit
    GET  /api/jobs       your builds, newest first
    GET  /api/jobs/<job>/log         build output (text)
    GET  /api/jobs/<job>/check.png   terrain check image
    GET  /api/jobs/<job>/course.zip  the course file
POST replies are {"ok": bool, "msg": ...} like the cart server's.
"""
import argparse
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
TOOLS = os.path.join(ROOT, "tools")
WEB = os.path.join(HERE, "web")
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, TOOLS)
import coursedir  # noqa: E402
import coursepack  # noqa: E402
import new_course  # noqa: E402

MAX_BODY = 4096
SESSION_S = 12 * 3600
PBKDF2_ITERS = 200_000
RESERVED = (coursedir.DEFAULT, "knolls", "glen")
STATIC = {"/": ("index.html", "text/html"), "/builder.js": ("builder.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}

_cfg = {"work": None, "register": True}
_lock = threading.Lock()
_users = {}      # name -> {"salt", "hash", "iters", "created"}
_sessions = {}   # token -> (user, expires)
_jobs = {}       # job id -> job dict (also saved as jobs/<id>/job.json)
_hits = {}       # "way/123" -> Nominatim hit, from searches this run
_queue = queue.Queue()
_nominatim = {"last": 0.0, "cache": {}, "lock": threading.Lock()}


def write_json(path, data):
    """write-tmp, fsync, replace, like server/store.py"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def work(*parts):
    return os.path.join(_cfg["work"], *parts)


# --- accounts -------------------------------------------------------------

def _hash(pw, salt, iters):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iters).hex()


def register(name, pw):
    if not _cfg["register"]:
        return False, "registration is closed"
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_.-]{3,32}", name):
        return False, "user name: 3-32 lowercase letters, digits, . _ -"
    if not isinstance(pw, str) or not 8 <= len(pw) <= 200:
        return False, "password: at least 8 characters"
    salt = secrets.token_hex(16)
    with _lock:
        if name in _users:
            return False, "that user name is taken"
        _users[name] = {"salt": salt, "hash": _hash(pw, salt, PBKDF2_ITERS), "iters": PBKDF2_ITERS,
                        "created": time.strftime("%Y-%m-%d %H:%M")}
        write_json(work("users.json"), _users)
    return True, name


def check_password(name, pw):
    u = _users.get(name) if isinstance(name, str) else None
    if not u or not isinstance(pw, str):
        time.sleep(0.5)  # same cost for unknown users; slows guessing
        return False
    return hmac.compare_digest(u["hash"], _hash(pw, u["salt"], u["iters"]))


def new_session(name):
    token = secrets.token_urlsafe(32)
    with _lock:
        now = time.time()
        for t in [t for t, (_, exp) in _sessions.items() if exp < now]:
            del _sessions[t]
        _sessions[token] = (name, now + SESSION_S)
    return token


# --- search ---------------------------------------------------------------

def slug(name):
    s = re.sub(r"\b(golf|country|club|course|links|gc|cc)\b", " ", name.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40].strip("-")
    return s if s and s not in RESERVED else "course"


def repo_courses():
    """OSM outline -> course id, for courses already in the repo's courses/."""
    found = {}
    folder = os.path.join(ROOT, "courses")
    for c in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        try:
            with open(os.path.join(folder, c, "course.json"), encoding="utf-8") as f:
                found.setdefault(json.load(f).get("outline"), []).append(c)
        except (OSError, ValueError):
            pass
    return found


def search(q):
    q = " ".join(q.split())[:120]
    if len(q) < 3:
        return False, "type at least 3 letters"
    with _nominatim["lock"]:  # Nominatim's policy: at most 1 request a second, and cache
        hits = _nominatim["cache"].get(q.lower())
        if hits is None:
            try:
                tries = [q] if "golf" in q.lower() else [q, q + " golf course"]
                for t in tries:
                    time.sleep(max(0.0, _nominatim["last"] + 1.1 - time.time()))
                    _nominatim["last"] = time.time()
                    hits = new_course.search(t)
                    if hits:
                        break
            except OSError as e:
                return False, f"OpenStreetMap search failed: {e}"
            _nominatim["cache"][q.lower()] = hits
        for h in hits:
            _hits[f"{h['osm_type']}/{h['osm_id']}"] = h
    in_repo = repo_courses()
    out = []
    for h in hits:
        osm = f"{h['osm_type']}/{h['osm_id']}"
        name = h.get("name") or h["display_name"].split(",")[0]
        out.append({"osm": osm, "name": name, "place": h["display_name"], "id": slug(name),
                    "in_repo": in_repo.get(osm, [])})
    return True, out


# --- builds ---------------------------------------------------------------

def save_job(j):
    write_json(work("jobs", j["id"], "job.json"), j)


def queue_build(user, osm, cid):
    h = _hits.get(osm) if isinstance(osm, str) else None
    if not h:
        return False, "search for the course again, then build"
    if not isinstance(cid, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", cid) or cid in RESERVED:
        return False, "course id: up to 40 lowercase letters, digits, - or _"
    with _lock:
        if sum(j["status"] in ("queued", "running") for j in _jobs.values() if j["user"] == user) >= 3:
            return False, "you already have 3 builds waiting; let one finish"
        jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        name = h.get("name") or h["display_name"].split(",")[0]
        j = {"id": jid, "user": user, "course": cid, "name": name, "place": h["display_name"],
             "osm": osm, "status": "queued", "step": "", "created": time.strftime("%Y-%m-%d %H:%M"),
             "finished": None, "error": None, "manifest": None}
        folder = work("jobs", jid, "courses", cid)
        os.makedirs(folder)
        write_json(os.path.join(folder, "course.json"), new_course.config(h, name))
        _jobs[jid] = j
        save_job(j)
    _queue.put(jid)
    return True, public(j)


def worker():
    """One build at a time: the fetchers lean on free public services."""
    while True:
        j = _jobs[_queue.get()]
        try:
            run_build(j)
        except Exception as e:  # keep the worker alive whatever happens
            j.update(status="failed", error=f"{e.__class__.__name__}: {e}")
        j["finished"] = time.strftime("%Y-%m-%d %H:%M")
        with _lock:
            save_job(j)


def run_build(j):
    jdir = work("jobs", j["id"])
    j["status"] = "running"
    save_job(j)
    env = dict(os.environ, GOLFMAP_COURSES_DIR=os.path.join(jdir, "courses"),
               PYTHONUNBUFFERED="1", NO_COLOR="1")
    cmd = [sys.executable, os.path.join(TOOLS, "build_all.py"), "--course", j["course"]]
    with open(os.path.join(jdir, "build.log"), "w", encoding="utf-8") as log:
        p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            log.write(line)
            log.flush()
            if line.startswith("=== "):
                j["step"] = line[4:].split()[0]
            elif "WARNING" in line:
                j["warnings"] = j.get("warnings", 0) + 1
        code = p.wait()
    if code != 0:
        j.update(status="failed", error="build stopped; see the log")
        return
    folder = os.path.join(jdir, "courses", j["course"])
    j["manifest"] = coursepack.pack(folder, j["course"], os.path.join(jdir, "course.zip"),
                                    built_by=j["user"], builder="tashua-golf-map builder")
    j.update(status="done", step="")


def load_jobs():
    os.makedirs(work("jobs"), exist_ok=True)
    for jid in sorted(os.listdir(work("jobs"))):
        try:
            with open(work("jobs", jid, "job.json"), encoding="utf-8") as f:
                j = json.load(f)
        except (OSError, ValueError):
            continue
        if j["status"] in ("queued", "running"):
            j.update(status="failed", error="builder was stopped during the build; build it again")
            save_job(j)
        _jobs[jid] = j


def public(j):
    return {k: j.get(k) for k in ("id", "course", "name", "place", "osm", "status", "step",
                                  "created", "finished", "error", "manifest", "warnings")}


# --- HTTP -----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _user(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "gmb_session":
                s = _sessions.get(v)
                if s and s[1] > time.time():
                    return s[0]
        return None

    def _send(self, code, body, ctype, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, data, extra=()):
        self._send(code, json.dumps(data).encode(), "application/json", extra)

    def _file(self, path, ctype, extra=()):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self.send_error(404)
        self._send(200, body, ctype, extra)

    def do_GET(self):
        url = urlparse(self.path)
        route = url.path
        if route in STATIC:
            name, ctype = STATIC[route]
            return self._file(os.path.join(WEB, name), ctype + "; charset=utf-8")
        if route == "/api/me":
            return self._json(200, {"user": self._user(), "register": _cfg["register"]})
        user = self._user()
        if not user:
            return self._json(401, {"ok": False, "msg": "sign in first"})
        if route == "/api/search":
            ok, msg = search(parse_qs(url.query).get("q", [""])[0])
            return self._json(200, {"ok": ok, "msg": msg})
        if route == "/api/jobs":
            mine = [public(j) for j in _jobs.values() if j["user"] == user]
            return self._json(200, sorted(mine, key=lambda j: j["id"], reverse=True))
        m = re.fullmatch(r"/api/jobs/([0-9a-f-]+)/(log|check\.png|course\.zip)", route)
        j = _jobs.get(m.group(1)) if m else None
        if not j or j["user"] != user:
            return self.send_error(404)
        jdir = work("jobs", j["id"])
        if m.group(2) == "log":
            return self._file(os.path.join(jdir, "build.log"), "text/plain; charset=utf-8")
        if m.group(2) == "check.png":
            return self._file(os.path.join(jdir, "courses", j["course"], "data", "terrain_check.png"), "image/png")
        return self._file(os.path.join(jdir, "course.zip"), "application/zip",
                          [("Content-Disposition", f'attachment; filename="{j["course"]}.zip"')])

    def do_POST(self):
        route = self.path.split("?")[0]
        if route not in ("/api/register", "/api/login", "/api/logout", "/api/build"):
            return self.send_error(404)
        # custom header + JSON type: another site can't send these without a
        # CORS preflight, which this server never approves
        if (self.headers.get("X-Trolley-Client") != "1"
                or not self.headers.get("Content-Type", "").startswith("application/json")):
            return self.send_error(403)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 <= n <= MAX_BODY:
                return self.send_error(413)
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send_error(400)
        cookie = "gmb_session={}; Path=/; HttpOnly; SameSite=Strict; Max-Age={}"
        if route == "/api/register":
            ok, msg = register(body.get("user"), body.get("password"))
            extra = [("Set-Cookie", cookie.format(new_session(msg), SESSION_S))] if ok else []
            return self._json(200, {"ok": ok, "msg": msg}, extra)
        if route == "/api/login":
            name = body.get("user")
            if not check_password(name, body.get("password")):
                return self._json(200, {"ok": False, "msg": "wrong user name or password"})
            return self._json(200, {"ok": True, "msg": name},
                              [("Set-Cookie", cookie.format(new_session(name), SESSION_S))])
        if route == "/api/logout":
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "gmb_session":
                    _sessions.pop(v, None)
            return self._json(200, {"ok": True, "msg": None}, [("Set-Cookie", cookie.format("", 0))])
        user = self._user()
        if not user:
            return self._json(401, {"ok": False, "msg": "sign in first"})
        ok, msg = queue_build(user, body.get("osm"), body.get("id"))
        return self._json(200, {"ok": ok, "msg": msg})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--work", default=os.path.join(HERE, "work"), help="where accounts and builds are kept")
    ap.add_argument("--no-register", action="store_true", help="only existing users can sign in")
    args = ap.parse_args()
    _cfg.update(work=os.path.abspath(args.work), register=not args.no_register)
    os.makedirs(_cfg["work"], exist_ok=True)
    try:
        with open(work("users.json"), encoding="utf-8") as f:
            _users.update(json.load(f))
    except FileNotFoundError:
        pass
    load_jobs()
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"course builder on http://{args.bind}:{args.port}  (work folder {_cfg['work']})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
