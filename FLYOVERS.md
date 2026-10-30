# Recording the Google Earth flyover clips

_Written 2026-10-27. Menu labels can differ a little between Google Earth Pro
versions. The settings are what matter._

Without clips, the app plays a 2D flyover drawn over the map. With a clip for a
hole, it plays the real Google Earth video at 2x speed every time that hole
comes up, with the yardage panel still live beside it. Holes without a clip keep
the 2D version, so you can record a few at a time.

## 1. One-time setup in Google Earth Pro (Windows or Mac, free)

1. Install **Google Earth Pro** from https://www.google.com/earth/about/versions/
   (the desktop "Pro" version; the web version has no Movie Maker).
2. **File → Open** → `data/kml/tashua_knolls.kml`. In the **Places** panel you'll
   see a *Tashua Knolls* folder with 19 tours: *Complete Tour* and
   *Hole 1 - Par 5 - 552 yards* through *Hole 18 - Par 4 - 425 yards*.
3. Clean up the 3D view so only the course gets recorded:
   - **View → Show Navigation → Never**
   - **View →** turn off **Status Bar**, **Overview Map**, **Scale Legend** and **Grid**
   - **Layers** panel: turn **on** *Terrain* and *3D Buildings* (gives 3D trees
     where Google has them); turn **off** *Borders and Labels*, *Places*, *Roads* and *Photos*
   - **Tools → Options → 3D View**: *Elevation Exaggeration* = 1
   - In **Places**, under *Tashua Knolls*: leave **Show Pins** checked; uncheck
     **Show Center Lines** and **Show Profile Line** (the app draws its own)
4. Play one tour (double-click *Hole 1 …*) once before recording, so Earth Pro
   caches the imagery and the recording doesn't catch blurry tiles.

## 2. Record each hole: Tools → Movie Maker

| Setting | Value |
|---|---|
| Picture size | **Custom: 1100 × 960** (fills the 5" screen's map area exactly). If there's no Custom option, use **1280 × 720 (HD)**; the sides will be cropped |
| Frames per second | **30** |
| Picture quality | **Maximum** (or the highest offered) |
| Record from | **A saved tour** → pick *Hole N - Par … - … yards* |
| File type | **H.264 (.mp4)** if listed; otherwise **MJPEG (.mp4 / .mov)**. The prep script converts either |
| Save to | `data/flyovers_raw/` in this folder |
| File name | **`hole01.mp4` … `hole18.mp4`** (any name with the hole number works, but use these to avoid mix-ups) |

Click **Create Movie** and wait. It renders frame by frame (each hole is a
49–61 s tour, about 1,500–1,800 frames). Repeat for all 18 holes.

**Don't crop out or cover the Google logo or the imagery credits** Earth Pro
puts at the bottom of the frame. Google requires them. That's also why 1100 × 960
is the preferred size: nothing gets cropped.

## 3. Convert and install

On a PC with ffmpeg (`winget install Gyan.FFmpeg`), from this folder:

```bash
python tools/prep_flyovers.py
```

This writes `app/flyovers/hole01.mp4` … `hole18.mp4` (H.264, 1100×960, no audio,
about 5–10 MB each) and lists any hole still missing a clip. Copy the
`app/flyovers/` folder to the Pi: `/opt/tashua-golf-map/app/flyovers/`.

To check on the PC: `python server/gps_server.py --sim`, open http://127.0.0.1:8080
and press **Flyover**.

## Licensing: check before using commercially

Google allows recorded Google Earth video for **personal, non-commercial use**,
with the attribution left visible. If the carts will be used commercially (for
example, carts rented to golfers, or a course-branded product), get permission
from Google first: https://about.google/brand-resource-center/products-and-services/geo-guidelines/
The 2D flyover and the map itself (OpenStreetMap, ODbL) don't have this
restriction.
