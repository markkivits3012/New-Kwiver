"""Where the trolley may drive: a grid over the course with keep-out areas.

Built from course.js. Each 3 m cell is free, or blocked with a reason:
outside the course, or on/near a green, tee box, bunker or water (with the
buffers below). Used to refuse goals the trolley must never be sent to, to
snap named targets ("next tee") to a legal spot beside them, and by the mock
trolley to plan a path around the blocked ground.

If server/terrain.json is present (from tools/build_terrain.py) it adds trees,
steep ground and buildings/walls/fences, and makes sloping ground costlier.
"""
import base64
import heapq
import json
import math
import os
from array import array

CELL_M = 3.0
BUFFERS_M = {"green": 3.0, "tee": 1.0, "bunker": 1.0, "water": 5.0}
CODES = {"green": 2, "tee": 3, "bunker": 4, "water": 5, "tree": 6, "steep": 7, "structure": 8, "road": 10}
REASONS = {1: "outside the course", 2: "on or too close to a green", 3: "on a tee box",
           4: "in or too close to a bunker", 5: "in or too close to water", 6: "under trees",
           7: "on ground too steep for the trolley", 8: "on or too close to a building, wall or fence",
           9: "in a spot the trolley can't drive to from the rest of the course",
           10: "on or too close to a road or parking lot"}
# Planning cost per meter by ground type: stay on cart paths, then rough,
# and only cross fairways (where people are hitting) when there's no way round.
COST_PATH, COST_ROUGH, COST_FAIRWAY = 1.0, 1.6, 6.0
# Free ground right beside blocked ground costs this much more, and straightened
# shortcuts don't run along it, so routes keep a cell's margin where they can
# (GPS is good to a few meters). Cart paths are exempt.
COST_NEAR = 2.0
# Grade limits (percent). Placeholders until Mark says what the trolley can climb:
# steeper than STEEP_PCT is blocked; above SLOPE_COST_PCT each meter costs more,
# up to double at STEEP_PCT.
STEEP_PCT, SLOPE_COST_PCT = 20, 8
TERRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terrain.json")


class KeepOut:
    def __init__(self, course, cell=CELL_M, terrain=TERRAIN):
        outline = course["outline"] or [p for h in course["holes"] for p in h["line"]]
        self.lat0 = sum(p[0] for p in outline) / len(outline)
        self.lon0 = sum(p[1] for p in outline) / len(outline)
        self.kx = 111320 * math.cos(math.radians(self.lat0))
        self.ky = 110540
        ring = [self.xy(p) for p in outline]
        pad = 10
        self.x0 = min(p[0] for p in ring) - pad
        self.y0 = min(p[1] for p in ring) - pad
        self.c = cell
        self.w = int((max(p[0] for p in ring) + pad - self.x0) / cell) + 1
        self.h = int((max(p[1] for p in ring) + pad - self.y0) / cell) + 1
        self.code = bytearray([1]) * (self.w * self.h)   # 1 = outside until filled
        self.path = bytearray(self.w * self.h)           # 1 = on/next to a cart path
        self.fairway = bytearray(self.w * self.h)        # 1 = fairway

        self._fill(ring, 0, only_free=False)             # inside the course boundary
        for f in course["features"]:
            if f["layer"] == "fairway" and f["kind"] == "poly":
                for rg in f["rings"]:
                    self._fill([self.xy(p) for p in rg], 1, only_free=False, grid=self.fairway)
            if f["layer"] in CODES and f["kind"] == "poly":
                code, r = CODES[f["layer"]], BUFFERS_M[f["layer"]]
                for rg in f["rings"]:
                    pts = [self.xy(p) for p in rg]
                    self._fill(pts, code)
                    self._buffer(pts, code, r)
            elif f["layer"] == "cartpath" and f["kind"] == "line":
                self._buffer([self.xy(p) for p in f["pts"]], 1, cell, grid=self.path)
        # the recorded cart route is ground a cart has driven: treat it as a path
        # (skipping jumps where the GPS lost the cart)
        track = [self.xy(p) for p in course.get("track", [])]
        for qa, qb in zip(track, track[1:]):
            if math.hypot(qb[0] - qa[0], qb[1] - qa[1]) < 30:
                self._buffer([qa, qb], 1, cell, grid=self.path)

        self.slope = bytearray(self.w * self.h)          # grade in percent
        self.terrain = bool(terrain) and self._load_terrain(terrain)
        self._block_pockets()
        self.near = bytearray(self.w * self.h)           # 1 = free, off-path, next to blocked ground
        for k in range(self.w * self.h):
            if self.code[k] == 0 and not self.path[k]:
                i, j = k % self.w, k // self.w
                self.near[k] = any(not (0 <= i + di < self.w and 0 <= j + dj < self.h)
                                   or self.code[(j + dj) * self.w + i + di]
                                   for di in (-1, 0, 1) for dj in (-1, 0, 1))

    def _block_pockets(self):
        """Block free cells not connected to the largest free area, so any goal
        that passes check() can actually be driven to."""
        W, n = self.w, self.w * self.h
        label = array("i", [0]) * n
        sizes = [0]
        for s in range(n):
            if self.code[s] != 0 or label[s]:
                continue
            sizes.append(0)
            lab = len(sizes) - 1
            label[s] = lab
            stack = [s]
            while stack:
                k = stack.pop()
                sizes[lab] += 1
                i, j = k % W, k // W
                # 4-neighbors: plan() never squeezes diagonally between blocked cells
                for ni, nj in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                    if 0 <= ni < W and 0 <= nj < self.h:
                        nk = nj * W + ni
                        if self.code[nk] == 0 and not label[nk]:
                            label[nk] = lab
                            stack.append(nk)
        main = max(range(1, len(sizes)), key=sizes.__getitem__, default=0)
        for k in range(n):
            if label[k] and label[k] != main:
                self.code[k] = 9

    def grid_key(self):
        """What a terrain file must match to line up with this grid."""
        return {"lat0": round(self.lat0, 7), "lon0": round(self.lon0, 7), "x0": round(self.x0, 2),
                "y0": round(self.y0, 2), "w": self.w, "h": self.h, "cell": self.c}

    def _load_terrain(self, path):
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
        if t["grid"] != self.grid_key():
            print(f"keepout: {path} was built for a different course.js; ignoring it "
                  "(rerun tools/build_terrain.py)")
            return False
        block, slope = base64.b64decode(t["block"]), base64.b64decode(t["slope_pct"])
        # cart paths stay open: they're built for carts, and a 3 m slope cell
        # beside a path picks up the bank next to it (slope still adds cost)
        for k in range(self.w * self.h):
            if self.code[k] == 0 and not self.path[k]:
                self.code[k] = block[k] or (CODES["steep"] if slope[k] > STEEP_PCT else 0)
        self.slope = bytearray(slope)
        return True

    # ---- coordinates -------------------------------------------------------------
    def xy(self, p):
        return ((p[1] - self.lon0) * self.kx, (p[0] - self.lat0) * self.ky)

    def latlon(self, q):
        return (round(self.lat0 + q[1] / self.ky, 7), round(self.lon0 + q[0] / self.kx, 7))

    def _cell(self, q):
        i, j = int((q[0] - self.x0) / self.c), int((q[1] - self.y0) / self.c)
        return (i, j) if 0 <= i < self.w and 0 <= j < self.h else None

    def _center(self, i, j):
        return (self.x0 + (i + 0.5) * self.c, self.y0 + (j + 0.5) * self.c)

    def _code_at(self, q):
        cl = self._cell(q)
        return 1 if cl is None else self.code[cl[1] * self.w + cl[0]]

    # ---- rasterizing ---------------------------------------------------------------
    def _fill(self, ring, code, only_free=True, grid=None):
        """Scanline-fill a polygon ring."""
        grid = self.code if grid is None else grid
        n = len(ring)
        ys = [p[1] for p in ring]
        j0 = max(int((min(ys) - self.y0) / self.c), 0)
        j1 = min(int((max(ys) - self.y0) / self.c) + 1, self.h - 1)
        for j in range(j0, j1 + 1):
            yc = self.y0 + (j + 0.5) * self.c
            xs = []
            for k in range(n):
                (xa, ya), (xb, yb) = ring[k], ring[(k + 1) % n]
                if (ya <= yc < yb) or (yb <= yc < ya):
                    xs.append(xa + (yc - ya) * (xb - xa) / (yb - ya))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                i0 = max(math.ceil((a - self.x0) / self.c - 0.5), 0)
                i1 = min(math.floor((b - self.x0) / self.c - 0.5), self.w - 1)
                row = j * self.w
                for i in range(i0, i1 + 1):
                    if not only_free or grid[row + i] == 0:
                        grid[row + i] = code

    def _buffer(self, pts, code, r, grid=None):
        """Mark every free cell within r meters of the polyline/ring edges."""
        grid = self.code if grid is None else grid
        only_free = grid is self.code
        rc = int(math.ceil(r / self.c))
        reach2 = (r + self.c * 0.5) ** 2
        disk = [(di, dj) for di in range(-rc, rc + 1) for dj in range(-rc, rc + 1)
                if (di * di + dj * dj) * self.c * self.c <= reach2]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:] + pts[:1] if grid is self.code else pts[1:]):
            steps = max(1, int(math.hypot(xb - xa, yb - ya) / (self.c / 2)))
            for s in range(steps + 1):
                cl = self._cell((xa + (xb - xa) * s / steps, ya + (yb - ya) * s / steps))
                if cl is None:
                    continue
                for di, dj in disk:
                    i, j = cl[0] + di, cl[1] + dj
                    if 0 <= i < self.w and 0 <= j < self.h:
                        k = j * self.w + i
                        if not only_free or grid[k] == 0:
                            grid[k] = code

    # ---- queries --------------------------------------------------------------------
    def check(self, p):
        """(True, None) if the trolley may be sent to lat/lon p, else (False, reason)."""
        code = self._code_at(self.xy(p))
        return (True, None) if code == 0 else (False, REASONS[code])

    def nearest_free(self, p, max_m=25.0, off_fairway=False):
        """Closest legal spot to lat/lon p, within max_m meters, or None.
        off_fairway: also skip fairway cells (for parking where people hit)."""
        q = self.xy(p)
        cl = self._cell(q)
        if cl is None:
            return None
        best = None
        for ring_r in range(int(max_m / self.c) + 1):
            for i in range(cl[0] - ring_r, cl[0] + ring_r + 1):
                for j in (cl[1] - ring_r, cl[1] + ring_r) if ring_r else (cl[1],):
                    best = self._closer(best, q, i, j, off_fairway)
            for j in range(cl[1] - ring_r + 1, cl[1] + ring_r):
                for i in (cl[0] - ring_r, cl[0] + ring_r) if ring_r else ():
                    best = self._closer(best, q, i, j, off_fairway)
            if best:
                return self.latlon(best[1])
        return None

    def _closer(self, best, q, i, j, off_fairway=False):
        if (0 <= i < self.w and 0 <= j < self.h and self.code[j * self.w + i] == 0
                and not (off_fairway and self.fairway[j * self.w + i])):
            c = self._center(i, j)
            d = math.hypot(c[0] - q[0], c[1] - q[1])
            if best is None or d < best[0]:
                return (d, c)
        return best

    def cells_on(self, qa, qb):
        """Every grid cell the straight line between two x/y points touches
        (grid traversal; through an exact corner, both side cells count too)."""
        x0, y0 = (qa[0] - self.x0) / self.c, (qa[1] - self.y0) / self.c
        x1, y1 = (qb[0] - self.x0) / self.c, (qb[1] - self.y0) / self.c
        i, j, i1, j1 = math.floor(x0), math.floor(y0), math.floor(x1), math.floor(y1)
        dx, dy = x1 - x0, y1 - y0
        si, sj = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        tdx = abs(1 / dx) if dx else math.inf
        tdy = abs(1 / dy) if dy else math.inf
        tx = ((i + 1 - x0) if dx > 0 else (x0 - i)) * tdx if dx else math.inf
        ty = ((j + 1 - y0) if dy > 0 else (y0 - j)) * tdy if dy else math.inf
        yield i, j
        for _ in range(abs(i1 - i) + abs(j1 - j)):
            if (i, j) == (i1, j1):
                return
            if abs(tx - ty) < 1e-9:
                yield i + si, j
                yield i, j + sj
                i, j, tx, ty = i + si, j + sj, tx + tdx, ty + tdy
            elif tx < ty:
                i, tx = i + si, tx + tdx
            else:
                j, ty = j + sj, ty + tdy
            yield i, j

    def clear(self, qa, qb, avoid_fairway=False, avoid_near=False):
        """True if the straight line between two x/y points crosses only free
        cells (and, if asked, no fairway / no cells beside blocked ground)."""
        for i, j in self.cells_on(qa, qb):
            if not (0 <= i < self.w and 0 <= j < self.h):
                return False
            k = j * self.w + i
            if self.code[k] != 0 or (avoid_fairway and self.fairway[k]) or (avoid_near and self.near[k]):
                return False
        return True

    def plan(self, a, b, max_expand=400000):
        """Path from lat/lon a to lat/lon b around the keep-out areas (A* on the
        grid, then straightened). Returns a list of lat/lon points, or None."""
        qa, qb = self.xy(a), self.xy(b)
        # the trolley may be parked inside a buffer (e.g. beside a tee): start from the nearest free cell
        sa = self.nearest_free(a, 15)
        ca, cb = self._cell(self.xy(sa)) if sa else None, self._cell(qb)
        if ca is None or cb is None or self.code[cb[1] * self.w + cb[0]] != 0:
            return None
        W, start, goal = self.w, ca[1] * self.w + ca[0], cb[1] * self.w + cb[0]
        g = array("d", [math.inf]) * (W * self.h)
        came = array("i", [-1]) * (W * self.h)
        g[start] = 0.0
        gx, gy = cb
        heap = [(0.0, 0.0, start)]
        moves = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
        slope_cost = [1.0 + max(0, s - SLOPE_COST_PCT) / (STEEP_PCT - SLOPE_COST_PCT) for s in range(256)]
        expanded = 0
        while heap:
            f, gk, k = heapq.heappop(heap)
            if k == goal:
                break
            if gk > g[k] + 1e-6:
                continue  # stale entry: a cheaper way here was already found
            expanded += 1
            if expanded > max_expand:
                return None
            i, j = k % W, k // W
            for di, dj, step in moves:
                ni, nj = i + di, j + dj
                if not (0 <= ni < W and 0 <= nj < self.h):
                    continue
                nk = nj * W + ni
                if self.code[nk] != 0:
                    continue
                if di and dj and (self.code[j * W + ni] or self.code[nj * W + i]):
                    continue  # no squeezing diagonally past a blocked corner
                ng = gk + step * (COST_PATH if self.path[nk] else
                                  COST_FAIRWAY if self.fairway[nk] else COST_ROUGH) * slope_cost[self.slope[nk]]                     * (COST_NEAR if self.near[nk] else 1.0)
                if ng < g[nk]:
                    g[nk] = ng
                    came[nk] = k
                    heapq.heappush(heap, (ng + math.hypot(ni - gx, nj - gy), ng, nk))
        else:
            return None
        if came[goal] == -1 and goal != start:
            return None
        cells = [goal]
        while cells[-1] != start:
            cells.append(came[cells[-1]])
        pts = [qa] + [self._center(k % W, k // W) for k in reversed(cells)] + [qb]
        # straighten: keep only the corners the trolley actually needs, but never
        # shortcut across a fairway the planned path went around, or along the
        # edge of blocked ground (the grid path itself may, where it must)
        out, i = [pts[0]], 0
        while i < len(pts) - 1:
            j = i + 1
            while j + 1 < len(pts) and self.clear(pts[i], pts[j + 1], avoid_fairway=True, avoid_near=True):
                j += 1
            out.append(pts[j])
            i = j
        return [self.latlon(q) for q in out]

    def length_m(self, pts):
        q = [self.xy(p) for p in pts]
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(q, q[1:]))
