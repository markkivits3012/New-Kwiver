# Tashua Knolls offline GPS cart map

_Package date: 2026-10-29_

Offline hole-by-hole GPS yardage map of Tashua Knolls (Trumbull, CT) for a
Raspberry Pi 4 with a 5" 800×480 touchscreen and a USB GPS. Once installed, the
Pi needs no internet. **Also included: Whitney Farms (Monroe, CT).**

**Start with [BUILDING.md](BUILDING.md)**: the short guide to adding a course,
building the package, installing on the Pi, picking the course and fixing touch.

## Quick start on the Pi

1. Raspberry Pi OS **with desktop** (Bookworm or later) on the Pi 4, touchscreen
   working, USB GPS plugged in. Internet is needed **only during install** (apt packages).
2. Copy this whole folder to the Pi (USB stick or `scp`), then:

   ```bash
   cd tashua-golf-map
   sudo bash pi/install.sh
   sudo reboot
   ```

3. After the reboot it opens full screen on the map. The GPS needs open sky for
   its first fix; `cgps -s` in a terminal shows whether it has one.

> **Not yet tested on real Pi hardware.** The app, server and sim were tested
> in a desktop browser at 800×480. `pi/install.sh` passes a syntax check but
> hasn't been run on a Pi.

## Try it on a PC first (no Pi or GPS needed)

```bash
python server/gps_server.py --sim --sim-rate 0.25
```

Open http://127.0.0.1:8080. `--sim` replays a recorded cart route around the
course at 4× speed. Needs Python 3.8+ only, with no packages to install.

## Using it

- **Touch in the wrong spot?** Hold the GPS status line (bottom right) for 3 s to
  open the touch test, tap the 5 crosses, and run the command it shows
  (`sudo bash pi/set-touch.sh ...`, then reboot). Buttons fire when you lift your
  finger, even if it slid a little.

The map is a real aerial photo of the course (USDA NAIP, about 0.35 m per
pixel, about 5 MB, stored offline in `app/aerial/`). Each hole is turned
tee-up with the yardage overlays drawn on top. Without the photo files the
app falls back to drawn shapes.

- **Right panel:** hole number, par and yardage, plus yards to the back, center
  and front of the green from the cart. With no GPS fix it measures from the tee.
  With clubs in **My Bag**, a club is suggested under the Center number: the
  shortest club whose carry reaches the pin (within 3 yd), else the longest.
  None is shown inside 20 yd.
- **Tap the map:** yards from the cart to that spot and from the spot to the pin.
  Tap the marker again to clear it.
- **Aim targets** (white rings) show yards from the cart.
- **Arrows:** change hole by hand. When the cart reaches the next hole's tee
  (within 25 m of a tee box), a banner asks **Switch to hole N?**. **Not now**
  hides it until the cart has left that tee.
- **Flyover:** plays on every hole change. Tap anywhere to skip. It uses a
  recorded Google Earth clip for the hole if there is one (see
  [FLYOVERS.md](FLYOVERS.md)). Otherwise it flies the KML tour in **3D** over our
  own terrain, or as a 2D pan over the map if the screen can't do 3D.
  Tuning is at the top of `app/app.js` (`TOUR_SPEED`, `VIDEO_RATE`, `AUTO_FLYOVER`).
- **Menu:** pick the course (it shows how far away each one is) and the tees.
  When the GPS shows the cart at another installed course, a banner offers to
  switch. Switching restarts the map server on that course and reloads the screen.
- **Score:** set your handicap index (HCP), then tap − / + / Par for each hole;
  tap a hole on the card to change it. It opens on the hole the GPS puts the cart
  on (the hole on screen without a fix). At a tee, with that hole and the one
  before still blank, it opens on the hole just finished. It shows gross, to par and net (with a
  course's `scorecard.json`, net counts handicap strokes hole by hole; see
  [BUILDING.md](BUILDING.md)). **Finish round** keeps it in the round history.
  Scores are saved on the Pi as you go, so a power cut doesn't lose them.
  **Past rounds** lists finished rounds, newest first, from every course; tap
  one to see its card. They're kept in `/var/lib/golf-map/rounds.jsonl` on the Pi.
- **Settings:** GPS details (fix, satellites, accuracy, position), the handheld
  remote (not built yet) and the trolley link. **Touch calibration** is here too.
  **My Bag:** tap each club you carry and enter its carry yards (**Not in bag**
  removes it). Saved in `/var/lib/golf-map/bag.json`.
- **3D button:** a live 3D view from behind and above the cart (or the tee),
  looking down the hole, with yards to the pin and each aim target. It follows
  the GPS. Tap the view or press **2D** to go back.

### Our own 3D course

The 3D view is built from public data only, with no Google imagery: USGS 3DEP
bare-earth lidar for the ground shape (2 m samples over the course, 15 m out
to the horizon, `app/terrain3d/`, 0.9 MB) with the NAIP photo draped over it.
`app/view3d.js` draws it with plain WebGL, no libraries. On a Pi it uses every
other height sample (4 m) to keep the GPU's load down. Trees are part of the
photo, so they look flat. Refresh the ground data with (needs internet and
Pillow, and run `fetch_aerial.py` first):

```bash
python tools/fetch_elevation.py
```

## Self-driving trolley (mock for now)

The real trolley connection waits on Mark's ROS details (see
[PLAN-trolley.md](PLAN-trolley.md)). Until then, a **mock trolley** shows how it
will work:

```bash
python server/gps_server.py --sim --sim-rate 0.25 --mock-trolley --trolley-speedup 4
```

- A bar along the bottom of the map: **STOP** (one touch), **Next tee**,
  **Green side**, **Come to me**, and **To marker** after you tap the map.
  Every "go" needs a second **Send** tap and is dropped after 6 s without one.
- The trolley (orange arrow) shows its planned route and goal on the map. The
  status line shows what it's doing, the ETA and the battery.
- **The server checks every goal.** It refuses greens (+3 m), tee boxes
  (+1 m), bunkers (+1 m), water and wetlands (+5 m), anything off the course,
  anything over 800 m away, and commands sent faster than once a second. Named
  targets are parked beside the tee or green, not on it.
- With `server/terrain.json` (see below) it also refuses **tree cover**, ground
  **steeper than 20%**, buildings/walls/fences (+2 m), and small pockets of
  open ground that can't be reached from the rest of the course. Cart paths and
  the recorded cart route stay open even under trees. The 20% limit is a
  placeholder until Mark says what the trolley can climb (`STEEP_PCT` in
  `server/keepout.py`).
- Planned routes stay on cart paths and rough, climb gentle slopes rather than
  steep ones, and only cross a fairway when there's no way round. Of the 18
  tee-to-tee routes, only 16 → 17 does, for 30 m.
- **Link loss:** the screen sends a heartbeat every second. If it stops for 3 s
  while driving, the trolley stops and **stays stopped** until a new command.
- Every command, accepted or refused, is logged to `logs/trolley-commands.jsonl`
  (`/var/lib/golf-map/` on the Pi).
- Commands only work from this app: the API requires a custom header that
  other web pages can't send.

Code: `server/keepout.py` (keep-out map and route planner) and
`server/trolley.py` (the command gate and the mock trolley). A real ROS
trolley plugs in behind the same gate.

### Vetted route library

Instead of trusting the planner live on the course, a person can check every
standard move once on a PC and approve it:

```bash
python tools/build_routes.py                     # propose + score the routes
python server/gps_server.py --sim --review       # then open http://127.0.0.1:8080/review.html
```

- `build_routes.py` plans each hole's legs: tee → each aim target (parked off
  the fairway) → green side → next tee, 54 routes in all. It scores them
  (length, metres on fairway, closest pass to blocked ground, steepest grade,
  detour) and **flags** the ones worth a careful look. It writes
  `server/routes.json`.
- The review page shows each route over the photo and keep-out map. **Approve**
  or **Reject** records your name and the time. Keys: ↑/↓ (or J/K) to move,
  A to approve, R to reject, F to fit the route.
- **Export approved routes as GPX** writes `data/routes/gpx/`: one file per route
  plus `approved-routes.gpx`. Mark's trolley takes GPX.
- Rebuilding keeps each review if the route's shape didn't change. A route that
  changed goes back to "proposed" with a note saying it was approved before.
- On the course, **Next tee** and **Green side** follow approved routes when the
  trolley is within 15 m of an approved route's start. They chain several, e.g.
  tee → target 1 → target 2 → green. Otherwise the server plans a route live.
  Start the server with `--library-only` to refuse moves no approved route covers.
  The audit log notes which commands used a vetted route.
- `--review` is for the PC only. Without it, the review API doesn't exist, so the
  Pi can't change approvals.

## What's in here

| Path | What |
|---|---|
| `app/` | The touchscreen app (plain HTML/JS canvas, no libraries, no internet) |
| `app/course.js` | Generated course data. Don't hand-edit |
| `app/flyovers/` | Put `hole01.mp4` … `hole18.mp4` here ([FLYOVERS.md](FLYOVERS.md)) |
| `server/gps_server.py` | Serves `app/` on 127.0.0.1 and relays gpsd fixes to it. Python standard library only |
| `pi/` | Pi installer, systemd service (runs as an unprivileged `golfmap` user, sandboxed), kiosk launcher |
| `server/terrain.json` | Generated trees/slopes/structures layer for the trolley's keep-out map |
| `server/routes.json` | Route library: proposed routes, scores, flags and who approved/rejected each |
| `app/review.html` | Route review page (works only with `--review`, on a PC) |
| `tools/build_routes.py` | Proposes and scores the route library |
| `BUILDING.md` | Short guide: add a course, build the package, install, pick the course, fix touch |
| `courses/whitney/` | Whitney Farms: the same files as Tashua's, for `--course whitney` |
| `tools/new_course.py`, `tools/build_all.py` | Start a course from OSM; build all its data in one go |
| `app/touchtest.html`, `pi/set-touch.sh` | Touch calibration page and the script that applies it |
| `pi/set-course.sh` | Picks the course the Pi shows |
| `tools/make_test_course.py` | Builds a made-up test course from `courses/<id>/course-def.json` |
| `app/menus.js`, `server/store.py` | Menu, Score and Settings screens; saved course, handicap, tees and rounds (`/var/lib/golf-map/` on the Pi) |
| `pi/update.sh`, `tools/make_update.py` | Update the Pi from a USB stick, no internet needed |
| `server/coursedir.py` | Where each course's files live (`--course`; Tashua is the default) |
| `app/view3d.js`, `app/terrain3d/` | 3D view (WebGL) and its lidar heightmaps |
| `tools/fetch_elevation.py` | Downloads the heightmaps for the 3D view |
| `tools/build_course.py` | Rebuilds `course.js` from the data below |
| `tools/build_terrain.py` | Rebuilds `server/terrain.json` (NAIP trees, 3DEP slopes, OSM structures) |
| `tools/prep_flyovers.py` | Converts Google Earth Pro recordings into the app's clips (needs ffmpeg) |
| `data/kml/tashua_knolls.kml` | Google Earth flyover KML: tees, pins, aim targets, center lines, par/yards, tours |
| `data/osm_map.json` | OpenStreetMap download: greens, fairways, bunkers, water, cart paths |
| `data/waypoints/waypoints.gpx` | Recorded cart route around the course (sim mode, and drawn faintly on the map) |

## Rebuilding the course data

Only needed if the KML or map data changes:

```bash
python tools/build_course.py --kml data/kml/tashua_knolls.kml --track data/waypoints/waypoints.gpx
```

Add `--download` to refresh the OpenStreetMap data first (needs internet).

To refresh the aerial photo (needs internet and Pillow, `pip install pillow`):

```bash
python tools/fetch_aerial.py
```

To rebuild the trolley's trees/slopes/structures layer (needs internet and
Pillow). **Rerun it after rebuilding `course.js`**: the server ignores a
`terrain.json` built for a different course grid and says so.

```bash
python tools/build_terrain.py --check
```

`--check` also writes `data/terrain_check.png`: the photo with trees tinted
green, steep ground red and structures blue. Look at it before trusting a new
build. Trees are found from NAIP near-infrared (canopy is darker and has a
higher NDVI than mown turf); slopes come from USGS 3DEP lidar elevation.
Public roads (buffered) and mapped parking lots are tinted orange and blocked too.

## Other courses

**Real courses** that are mapped in OpenStreetMap take two commands (see
[BUILDING.md](BUILDING.md)):

```bash
python tools/new_course.py whitney "Whitney Farms Golf Course, Monroe CT"
python tools/build_all.py --course whitney
```

`new_course.py` writes `courses/<id>/course.json` (name, area, the OSM course
boundary, `"share": true`). Holes, pars, greens, tees and hazards come from OSM.
Aim targets are the bends in each OSM hole line. Flyovers are generated (tee,
halfway, green, back). If an OSM hole line stops short of its green,
`build_course.py` extends it to the unclaimed green ahead and prints a WARNING.
`make_package.py` ships every course marked `"share": true`.

**Fairchild Wheeler Red and Black** (Fairfield, CT; built 2026-10-30): one
OSM golf course with two 18-hole layouts, split by `hole_tags` (see
[BUILDING.md](BUILDING.md)). Red is par 72, about 6,460 yd; Black is par 71,
about 6,410 yd. OSM has every hole's par and stroke index, so net scores count
handicap strokes hole by hole. Routes: Red 54 (22 flagged), Black 53 (27
flagged), all still proposed.

**Whitney Farms** (built 2026-10-29): 18 holes, par 72, about 6,180 yd on the
hole lines. Hole 3's OSM line stopped 158 m short and was extended. The hole 15
green sits on a peninsula between two ponds that the trolley can't reach, so
its routes park on the far bank, 65 m away (flagged for review). 52 routes,
30 flagged, all still proposed.

Tashua is the default and lives at the project root. Any other course mirrors
the same layout in `courses/<id>/` (`app/course.js`, `app/aerial/`,
`app/terrain3d/`, `server/terrain.json`, `server/routes.json`, `data/`; see
`server/coursedir.py`). The server and every data tool take `--course <id>`:

```bash
python server/gps_server.py --sim --mock-trolley --course <id>
```

A made-up test course (somewhere with no golf data in OSM) starts from a small
hand-written `courses/<id>/course-def.json` (tees, pins, green size, outline)
plus an OSM `map.json` download of the area in `courses/<id>/data/osm_map.json`:

```bash
python tools/make_test_course.py --course <id>   # course.js + holes.gpx, clearance check
python tools/fetch_aerial.py --course <id>
python tools/fetch_elevation.py --course <id>
python tools/build_terrain.py --course <id> --check
python tools/build_routes.py --course <id>
```

The outline is the trolley's geofence: everything outside it is refused. A
course can set `advance_m` for short holes (how close to the next tee the
"Switch to hole N?" banner appears; default 25 m). On the Pi, pick the course with
`sudo bash pi/set-course.sh <id>` and reboot.

## Where the data comes from, and known limits

- **Holes** (tees, pins, aim targets, center lines, par 72 / 6,440 yd) come from
  the Google Earth KML. Its pins sit within 0–5 m of the mapped green centers
  (hole 4 is 12 m off, probably a back pin).
- **Greens, fairways, bunkers, water and cart paths** come from OpenStreetMap,
  which volunteers draw. They're close but not survey-grade.
- **Pins are fixed** at the KML positions; daily pin placements aren't tracked.
  Front/back yardages use the green outline.
- Yardages haven't been checked on the course yet. Compare a few against
  sprinkler heads or yardage plates before relying on them.
- **Security:** the server listens on 127.0.0.1 only (not reachable from the
  network) and runs as a no-login system user with a read-only view of the system.

## Credits and licenses

- Map data © OpenStreetMap contributors, ODbL 1.0. The app shows this credit on screen.
- Aerial imagery: USDA NAIP via the USGS National Map (public domain).
- Elevation: USGS 3D Elevation Program (public domain).
- Font: Inter, SIL Open Font License 1.1 (`app/fonts/OFL.txt`).
- Google Earth clips, if you add any, are subject to Google's terms; see the
  licensing note in [FLYOVERS.md](FLYOVERS.md).
