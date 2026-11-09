# Work log

Where things stand, so work can pick up on another computer. Newest first.
Update it at the end of a working session (what changed, what's untested,
what's next). Details belong in the docs it links to; keep this short.

## Wheel motors (NodeMCU + two ZS-X11H), started Nov 9 2026

- Decision: the wheels are driven by a NodeMCU (ESP8266), not the Pi's GPIO,
  so they stop on their own if the Pi or the link fails (commands time out
  after 3 s). The Pi will send the same serial commands over USB later.
- Hardware: two "6-60V 400W" ZS-X11H boards, hoverboard motors, 40 V battery.
  No level shifter yet: trying 3.3 V signals direct first.
- **Written, not yet compiled or run:** `motor/` firmware and README (wiring,
  commands). Milestone 1: both wheels forward from the serial monitor, wheels
  off the ground.
- The Pi is reachable at home over Ethernet (`ssh fampi@192.168.6.65`, or
  `kwiver.local`); the hotspot only uses Wi-Fi, so both work at once.

## Handheld remote (ESP32-S3), Nov 6–9 2026

### Where it stands

- Hardware: Waveshare ESP32-S3 1.69" touch board, 1000 mAh LiPo, BN-880 GPS
  (TX→GPIO44, RX→GPIO43, VCC→3V3, SDA/SCL unused). See [remote/README.md](remote/README.md).
- **Works on the real board with the Pi:** screen, touch, GPS fix, battery,
  pairing, joining the hotspot after remote resets and Pi restarts, the link,
  STOP reaching the cart (shows *No trolley*: none connected yet).
- **Pushed but not yet confirmed on the board** (last two commits, `3af02d6`
  and `4bc850d`): the yards screen (swipe right or tap above STOP: center
  yards big, F / B, cart distance) and STOP shrunk to a bottom strip. The Pi
  side of the yards was simulator-tested and the Pi was updated from USB with
  `3af02d6`; whether the firmware compiled and the swipes work on the panel
  wasn't reported yet. **First thing: flash `main` and check.**
- The Pi's hotspot is on **channel 1** (set by hand, now kept by `hotspot.sh`).
  PMF is off on it (set by hand, not in the script; harmless). Hotspot
  password was changed by hand to a simple one; the key is unchanged.

### Next

1. Confirm the yards screen and swipes on the board (above).
2. At the course: `sudo CHANNEL=auto bash tashua-golf-map/pi/hotspot.sh` once,
   then a range test (how far from the cart before it drops from *Linked*).
3. Not yet tested: STOP against a real trolley, battery % against a real
   discharge, range.

### Lessons (so they aren't relearned)

- **Remote refused by the hotspot with reason 202 / 2, strong signal, right
  password, phone gets in:** a crowded channel (two smart bulbs on 6). Moving
  to channel 1 fixed it. The firmware prints join/drop reasons on USB serial
  and has `SCAN`; see remote/README.md.
- **Running `hotspot.sh` drops a Wi-Fi SSH session** (the Pi leaves the home
  Wi-Fi). Use the Pi's keyboard or Ethernet; `sudo bash pi/hotspot.sh off`
  undoes it. Alt+F4 leaves the kiosk; `setsid /opt/tashua-golf-map/kiosk.sh
  >/dev/null 2>&1 &` brings the map back without a reboot.
- `rmmod brcmfmac` to reset the Pi's Wi-Fi chip left it without Wi-Fi until a
  reboot. Don't.
- `pio device monitor` doesn't echo typing: use `--echo`; paste with a
  right-click. After a PAIR the remote restarts itself.

### Windows PC setup for flashing (ws10-prod99)

That PC's Anaconda Python (3.8) is broken (`unknown opcode` errors) and a
stray `PYTHONPATH` points at another Python, so PlatformIO lives in its own
Python 3.12 venv at `C:\pio`. In a plain Command Prompt, every new window:

```
set PYTHONPATH=
set PLATFORMIO_BUILD_DIR=C:\pb
C:\pio\Scripts\activate
cd C:\Python\New-Kwiver\New-Kwiver\remote
pio run -t upload --upload-port COM25
```

Builds there sometimes stop at a random step with no error (security
software, probably): just run it again. The USB package also needs that
Python: `python tools\make_update.py D:\` from the repo root. The Pi mounts
that stick as `/media/fampi/CC72006E72006016` (Tab-complete it).

Another computer needs its own setup: `python -m pip install platformio`,
then `pio run -t upload` in `remote/`; `pio device list` shows the port.
