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

`<id>` is a short lowercase name, for example `whitney`. Example:

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

## 4. Pick the course (Pi)

```bash
sudo bash pi/set-course.sh whitney
sudo reboot
```

Use `tashua` to go back. Run it with no name to list the installed courses.

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
