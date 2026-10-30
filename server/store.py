"""Saved choices and the scorecard, kept in the server's state directory
(--log-dir: /var/lib/golf-map on the Pi, the only place the service may write).

    course.txt     the course picked on the screen (pi/set-course.sh deletes it)
    player.json    handicap index, and the tee picked for each course
    round.json     the round in progress
    rounds.jsonl   finished rounds, one per line
    bag.json       the clubs the golfer carries and how far each goes

Every write goes to a temp file that is renamed into place, so a power cut
(the cart's key switch) leaves the old file, never half of one.
"""
import json
import os
import re
import threading
import time

ID = re.compile(r"[a-z0-9_-]{1,40}")
MAX_HOLES = 36
HCP_RANGE = (-10.0, 54.0)  # WHS handicap index limits (plus 10 to 54)
MAX_ROUNDS = 500           # history sent to the screen, newest first
MAX_CLUBS = 20


class Store:
    def __init__(self, folder):
        self.dir = os.path.abspath(folder)
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, name):
        return os.path.join(self.dir, name)

    def _read(self, name):
        try:
            with open(self._path(name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _write(self, name, obj):
        tmp = self._path(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path(name))

    # ---- course picked on the screen ----------------------------------------------
    def saved_course(self):
        try:
            with open(self._path("course.txt"), encoding="utf-8") as f:
                c = f.read().strip()
        except OSError:
            return None
        return c if ID.fullmatch(c) else None

    def save_course(self, course):
        with self._lock:
            tmp = self._path("course.txt.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(course + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path("course.txt"))

    # ---- player: handicap index and tees ----------------------------------------
    def player(self):
        p = self._read("player.json")
        return p if isinstance(p, dict) else {"hcp": None, "tees": {}}

    def save_player(self, body):
        """Merge {"hcp": 12.4 or null, "tees": {course: tee name}} into player.json."""
        with self._lock:
            p = self.player()
            if "hcp" in body:
                h = body["hcp"]
                if h is not None and not (_number(h) and HCP_RANGE[0] <= h <= HCP_RANGE[1]):
                    return False, "Handicap must be between +10 and 54."
                p["hcp"] = None if h is None else round(float(h), 1)
            if "tees" in body:
                t = body["tees"]
                if not (isinstance(t, dict) and all(ID.fullmatch(str(k)) and _name(v) for k, v in t.items())):
                    return False, "Bad tee choice."
                p["tees"] = {**p.get("tees", {}), **t}
            self._write("player.json", p)
            return True, p

    # ---- the round in progress ----------------------------------------------------
    def round(self):
        r = self._read("round.json")
        return r if isinstance(r, dict) else None

    def save_round(self, body):
        """Replace the round in progress: {"course", "name", "tee", "hcp",
        "scores": [n or null, ...], "par": [n or null, ...]}. The course name
        and pars go with it so the history can show it from any course."""
        scores, pars = body.get("scores"), body.get("par")
        if not (ID.fullmatch(str(body.get("course", "")))
                and isinstance(scores, list) and 0 < len(scores) <= MAX_HOLES
                and all(s is None or (_int(s) and 1 <= s <= 30) for s in scores)
                and (pars is None or (isinstance(pars, list) and len(pars) == len(scores)
                                      and all(p is None or (_int(p) and 2 <= p <= 7) for p in pars)))
                and (body.get("name") is None or (isinstance(body["name"], str) and len(body["name"]) <= 60))
                and (body.get("tee") is None or _name(body["tee"]))
                and (body.get("hcp") is None or (_number(body["hcp"]) and HCP_RANGE[0] <= body["hcp"] <= HCP_RANGE[1]))):
            return False, "Bad round."
        with self._lock:
            old = self.round() or {}
            r = {"course": body["course"], "name": body.get("name"), "tee": body.get("tee"),
                 "hcp": body.get("hcp"), "scores": scores, "par": pars,
                 "started": old.get("started") if old.get("course") == body["course"] else None}
            r["started"] = r["started"] or time.strftime("%Y-%m-%dT%H:%M:%S")
            self._write("round.json", r)
            return True, r

    def finish_round(self, save=True):
        """End the round in progress: append it to rounds.jsonl (if it has any
        scores and save is true), then clear it."""
        with self._lock:
            r = self.round()
            if r is None:
                return True, "No round in progress."
            kept = save and any(s is not None for s in r.get("scores", []))
            if kept:
                r["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                with open(self._path("rounds.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(r) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            os.remove(self._path("round.json"))
            return True, "Round saved." if kept else "Round cleared."

    def rounds(self):
        """Finished rounds, newest first. A line cut short by a power cut is skipped."""
        out = []
        try:
            with open(self._path("rounds.jsonl"), encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and isinstance(r.get("scores"), list):
                        out.append(r)
        except OSError:
            pass
        return out[::-1][:MAX_ROUNDS]

    # ---- my bag: club -> carry yards -------------------------------------------------
    def bag(self):
        b = self._read("bag.json")
        return b if isinstance(b, dict) and isinstance(b.get("clubs"), dict) else {"clubs": {}}

    def save_bag(self, body):
        """Replace the bag: {"clubs": {"Driver": 240, "7 iron": 150, ...}}."""
        clubs = body.get("clubs")
        if not (isinstance(clubs, dict) and len(clubs) <= MAX_CLUBS
                and all(_name(k) and _int(v) and 1 <= v <= 400 for k, v in clubs.items())):
            return False, "Bad club list."
        with self._lock:
            b = {"clubs": clubs}
            self._write("bag.json", b)
            return True, b


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _name(v):
    return isinstance(v, str) and 0 < len(v) <= 24
