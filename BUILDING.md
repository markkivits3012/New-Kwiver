# Building and installing: a short guide

Everything you need to add a course, build the package and set up the Pi.
Run the PC commands from inside the `tashua-golf-map` folder.

## What you need

- **On the PC:** Python 3.8 or newer, plus Pillow (`pip install pillow`), and internet.
- **On the Pi:** Raspberry Pi OS with desktop, the touchscreen, a USB GPS, and
  internet during the install only.

## 1. Add a course (PC)

The course must be mapped in OpenStreetMap (most US courses are: look it up at
openstreetmap.org and check that it shows holes and greens).

```bash
python tools/new_course.py <id> "<course name, town state>"
python tools/build_all.py --course <id>
```

`<id>` is a short lowercase name, for example `whitney`.

**Several courses at one site** (one OSM golf course, e.g. Fairchild Wheeler's
Red and Black): run `new_course.py` once per course with its own id, then in
each `courses/<id>/course.json` set its `name` and pick its holes by tag:

```json
"name": "Fairchild Wheeler Red",
"hole_tags": {"golf:course:name": "Red"}
```

Tee boxes go to the nearest hole of either course, so each course keeps only
its own.

Example:

```bash
python tools/new_course.py whitney "Whitney Farms Golf Course, Monroe CT"
python tools/build_all.py --course whitney
```

`build_all.py` takes a few minutes. It stops with `STOPPED:` if a step fails.
When it finishes, check three things:

1. **WARNING lines** near the top: holes, greens or tees it couldn't match.
2. **`courses/<id>/data/terrain_check.png`**: trees should be green, steep
   ground red, buildings blue and roads orange. The trolley avoids all of them.
3. **Flagged routes and NO ROUTE** in the list at the end. Review the routes
   in the review page (see the README, "Vetted route library") with
   `--course <id>` added.

Try the course on the PC:

```bash
python server/gps_server.py --course <id> --sim --mock-trolley
```

Then open http://127.0.0.1:8080. Press Ctrl+C to stop.

### Or use the course builder (web page on the PC)

The course builder does step 1 from a browser and hands you a course file
(a `.zip`) to import, so courses can be built by someone without the repo
checked out the same way, or kept and imported later.

```bash
python builder/builder.py
```

Open http://127.0.0.1:8090, register (user name + password, kept on this PC
in `builder/work/users.json`, hashed) and sign in. Search by the course name,
adding the town and state if it finds nothing. Each hit shows the course id it
will use (edit it if you like) and whether the repo already has that course.
Press **Build**: it runs `build_all.py` in `builder/work/jobs/` (not in
`courses/`), one build at a time, a few minutes each. When it says **done**,
look at **Log** (WARNING lines) and **Terrain check**, then **Download**.

Import the course file into the repo, try it, then commit and ship it as usual:

```bash
python tools/import_course.py longshore.zip
python server/gps_server.py --sim --course longshore
```

`--id <other>` imports it under another id; `--force` replaces an existing
`courses/<id>/`. Add a scorecard afterwards as below.

The course file holds `golfmap-course.json` (format, id, name, holes, par,
build date, OSM id, who built it) and the `courses/<id>/` files the cart needs:
`course.json`, `app/`, `server/` and `data/osm_map.json` (format in
`tools/coursepack.py`). Builder options: `--port`, `--work <folder>`,
`--no-register` (only existing users can sign in). It listens on 127.0.0.1
only. It is a PC tool: `pi/update.sh` never installs it on the Pi.

## 2. Build the package (PC)

```bash
python tools/make_package.py
```

It writes `dist/tashua-golf-map-<date>.zip` with Tashua plus every course in
`courses/` whose `course.json` has `"share": true` (`new_course.py` sets that).

## 3. Install on the Pi

Copy the zip to the Pi, then:

```bash
unzip tashua-golf-map-<date>.zip
cd tashua-golf-map
sudo bash pi/install.sh
sudo reboot
```

Run it the same way to update. It's safe to run again.

### Updating by USB stick (no internet needed)

On the PC, commit your changes, then pack them onto the stick:

```bash
python tools/make_update.py D:```

On the Pi (the stick shows up under `/media/fampi/`):

```bash
cd ~ && tar xzf /media/fampi/<stick>/golfmap-update.tgz
sudo bash tashua-golf-map/pi/update.sh
sudo reboot
```

`update.sh` backs up the running install to `~/golfmap-backups/` first and
prints the command to undo it. Unlike `install.sh` it installs no packages, so
it works without internet; use `install.sh` for a new Pi.

## 4. Pick the course (Pi)

On the screen: **Menu**, then tap the course. The app also offers to switch
when the GPS shows the cart at another installed course. Or from a terminal
(this also clears the course picked on the screen):

```bash
sudo bash pi/set-course.sh whitney
sudo reboot
```

Use `tashua` to go back. Run it with no name to list the installed courses.

## Scorecard: tees, yards, slope/rating, stroke index (optional)

Without one, the **Menu** offers Back/Middle/Forward tees estimated from the
mapped tee boxes, and the score shows net only for a full round. With the
course's real scorecard it shows the exact yards per tee and handicap strokes
hole by hole. Put it in `app/scorecard.json` (Tashua) or
`courses/<id>/app/scorecard.json`:

```json
{
  "tees": [
    {"name": "Blue",  "color": "#2f6fe0", "rating": 71.2, "slope": 128, "yards": [552, 365, 154, ...]},
    {"name": "White", "color": "#ffffff", "rating": 69.4, "slope": 124, "yards": [530, 350, 140, ...]}
  ],
  "hcp": [7, 15, 3, ...],
  "par": [5, 4, 3, ...]
}
```

- `tees` run back to front. Each `yards` list has one number per hole.
  `rating`, `slope` and `color` are optional.
- `hcp` is each hole's stroke index (1 = hardest). `par` is optional and
  overrides the mapped par.
- A tee can carry its own `par` and `hcp` lists when they differ by tee (on
  many cards the forward tees play to a different par and stroke index). They
  win over the course-wide ones.
- `default_tee` (optional) names the tee picked until the golfer chooses one;
  otherwise the middle one.
- The tees' total yards pick which mapped tee box the app measures from when
  there's no GPS fix (longest = farthest back; equal lengths share a box).

Example with per-tee par and stroke index:
`courses/fairchild-black/app/scorecard.json`.

## 5. Fix touch (Pi)

If taps land in the wrong spot:

1. On the map, **hold the GPS status line** (bottom right) for 3 seconds. The
   touch test opens.
2. Tap the 5 crosses. The page shows a command like
   `sudo bash pi/set-touch.sh 1.02 0.01 -0.03 ...`.
3. Run that command on the Pi, then `sudo reboot`.
4. Open the touch test again. It should say the touch is accurate. If not,
   repeat (each fix builds on the last). `sudo bash pi/set-touch.sh reset`
   undoes it.
