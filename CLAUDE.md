# CLAUDE.md

Rules and conventions for working on this repo. User-facing docs live in
README.md (using the app) and BUILDING.md (adding courses, packaging, installing).

## What this is

An offline GPS yardage map for a golf cart: a Raspberry Pi 4 (Raspberry Pi OS,
Debian 13 trixie, labwc desktop) with a 5" 800×480 resistive touchscreen and a
USB GPS read through gpsd. Chromium runs full screen (kiosk) against a local
Python server. **The Pi has no internet on the course.**

| Path | What |
|---|---|
| `app/` | Front end: `index.html`, `app.js` (map, holes, tees, next-hole prompt), `menus.js` (Menu / Score / Settings), `style.css` |
| `server/` | `gps_server.py` (HTTP + SSE), `store.py` (saved state), `coursedir.py` (where a course's files live) |
| `courses/<id>/` | Every course except Tashua Knolls, which stays at the project root |
| `pi/` | `install.sh`, `update.sh`, `kiosk.sh`, `kiosk-setup.sh`, `set-course.sh`, `golf-map.service` |
| `tools/` | Course building (`new_course.py`, `build_all.py`, `build_course.py`, fetchers), `make_update.py` (USB package) |
| `_pi-snapshot/` | Gitignored raw copy taken from the Pi. Reference only; never edit |

## Hard constraints

- **Server: Python standard library only.** No pip packages; the Pi installs
  nothing beyond apt's `python3`, `gpsd`, `chromium`, `curl`.
- **Front end: plain JS, no build step, no CDN.** Everything must load offline
  from `app/`.
- **Design for 800×480.** Check every screen change at that size; nothing may
  cover the Flyover button or the hole header.
- **Touch is resistive.** Buttons act on pointer release (use the `onRelease`
  helper in `menus.js`), not `click`. Keep targets large (≥ 44 px).
- **Survive power cuts.** Any file the server writes goes through write-tmp,
  fsync, `os.replace` (see `store.py`). State lives in `/var/lib/golf-map`.
- **The service is sandboxed** (user `golfmap`, read-only `/opt/tashua-golf-map`).
  Don't write anywhere except the state folder.
- **Never switch course or hole without asking.** GPS may *suggest* (course
  banner, "Switch to next hole?" prompt); the player confirms.

## API conventions

- POST requests must send header `X-Trolley-Client: 1` and a JSON body.
- POST replies are `{"ok": bool, "msg": ...}`; `msg` is the saved data or an
  error message. `api()` in `menus.js` unwraps it.
- Live data (`/gps`, `/gps/status`) is Server-Sent Events.
- New endpoints go in the `gps_server.py` docstring.

## Coding style

- Match the surrounding code: short functions, terse names, comments only where
  the *why* isn't obvious (hardware quirks, golf rules, Pi gotchas).
- Python: 4-space indent, no type-hint sprawl, f-strings.
- JS: `const`/`let`, arrow functions, no frameworks, no classes unless needed.
- Validate anything the client sends (see `Store.save_player` / `save_round` ranges).
- Course data is generated: edit `courses/<id>/course.json` (config) or
  `app/scorecard.json`, then rebuild with `tools/build_course.py`. Don't hand-edit
  built `course.js`.
- Scorecards: real card values win over map estimates. Per-tee `par` and `hcp`
  arrays override course-wide ones. Format is in BUILDING.md.
- Handicaps follow WHS: course handicap = index × slope/113 + (rating − par).

## Files and line endings

- `.gitattributes` forces LF (`* text=auto eol=lf`); `core.autocrlf` is false.
  Shell scripts with CR break on the Pi. After editing `pi/*.sh`, check with
  `tr -cd '\r' < file | wc -c` (must be 0).
- Scripts are run as `sudo bash pi/x.sh` because the USB copy may lose the +x bit.
- Pi scripts must be safe to re-run and print what they changed.

## Testing (PC, before anything goes to the Pi)

```bash
python server/gps_server.py --sim --sim-rate 0.25 --mock-trolley
python server/gps_server.py --sim --sim-course fairchild-black --course fairchild-black
```

- Open http://127.0.0.1:8080 (launch config `golf-map-sim` in `.claude/launch.json`).
- In the browser pane, the display's 1.5 DPR crops screenshots: set the viewport to
  1200×720 and force `#screen` (and `#sheet`, `#modal`) to 800×480 with injected CSS.
  Drive the UI with synthetic PointerEvents (pointerdown + pointerup), not clicks.
- Say plainly what was only tested in the simulator: real GPS, touch, systemd
  restarts and autostart can only be checked on the Pi.

## Getting changes to the Pi

The work PC can't reach the Pi over the network (SSH is blocked). Use a USB stick:

1. Commit (the package is built from HEAD only).
2. `python tools/make_update.py D:\` writes `golfmap-update.tgz` to the stick.
3. On the Pi (stick at `/media/fampi/7CC9-3CC7`):
   `cd ~ && tar xzf /media/fampi/7CC9-3CC7/golfmap-update.tgz`, then
   `sudo bash tashua-golf-map/pi/update.sh` (backs up first and prints the undo command).

## Workflow

- Commit only when asked; the user usually says "commit and pack" after testing
  a feature. Do both: commit, then build the USB package and verify its contents.
- Keep README.md and BUILDING.md current when behaviour or file formats change.
