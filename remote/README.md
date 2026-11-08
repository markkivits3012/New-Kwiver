# Handheld remote

A pocket remote for the cart. It shows its own GPS fix and whether it can
reach the cart, sends the golfer's position to the Pi every second, and has a
big **STOP** button for the trolley. Later the cart will use the position to
follow the golfer (not built yet; see [PROTOCOL.md](PROTOCOL.md)).

| Part | What |
|---|---|
| Board | Waveshare **ESP32-S3-Touch-LCD-1.69**: ESP32-S3, 240×280 ST7789V2 LCD, CST816T touch, LiPo charger |
| GPS | Beitian **BK-880**: u-blox chip (M10 on current units, M8 on older), NMEA over a 3.3 V UART |
| Link | Wi-Fi to the Pi's own hotspot (`pi/hotspot.sh`), UDP, signed with a pairing key |

The firmware is Arduino C++ built with PlatformIO, in [`src/main.cpp`](src/main.cpp);
the pins are in [`src/board.h`](src/board.h).

## Wiring the GPS

The GPS lead goes to the board's extension header. A BN-880 has six wires:
leave SDA and SCL (its compass) unconnected.

| BK-880 | Board |
|---|---|
| TX | GPIO44 (U0RXD) |
| RX | GPIO43 (U0TXD) |
| VCC | 3V3 |
| GND | GND |

(Leads differ between batches; go by the labels on the module, not the colours.
Power it from 3V3, not 5V: it runs on either, but the ESP32's pins take 3.3 V at
most and 3V3 stays on when the remote runs on battery.) The firmware finds the GPS's baud rate by itself: it tries 38400, 9600
and 115200 until it sees good NMEA. Current M10 units ship at 38400, older ones
at 9600. Put the GPS where it sees the sky, not under the screen or a hand.

## Building and flashing (PC)

1. Install PlatformIO once (a Python tool; it downloads the ESP32 compiler on
   the first build, so do that with internet):

   ```bash
   python -m pip install platformio
   ```

2. Plug the board into the PC by USB-C, then build and flash:

   ```bash
   cd remote
   pio run -t upload
   ```

   If upload can't find the board: hold **BOOT**, tap **RESET**, let go of
   BOOT, and run it again. Windows: if the build fails on a long path, build
   somewhere shorter with `set PLATFORMIO_BUILD_DIR=C:\pb` (PowerShell:
   `$env:PLATFORMIO_BUILD_DIR="C:\pb"`) first.

3. The screen shows **Not paired**, the GPS line and the STOP button.

## Pairing

The Pi makes the hotspot, its password and a pairing key; the remote needs all
three. Only a remote with the key can talk to the cart: anything else is
ignored, so nobody else can stop the trolley, fake your position or (later)
make the cart follow them.

1. **On the Pi**, with the USB stick in:

   ```bash
   sudo bash tashua-golf-map/pi/hotspot.sh /media/fampi/7CC9-3CC7
   ```

   It starts the hotspot (also at every boot) and writes one line to
   `golfmap-remote.txt` on the stick:

   ```
   PAIR golfmap-ab12 <password> <32-character key>
   ```

   Re-running it keeps the same network, password and key, so a paired remote
   stays paired. While the hotspot is on, the Pi can't join your home Wi-Fi;
   `sudo bash pi/hotspot.sh off` turns it off.

2. **On the PC**, with the remote plugged in by USB:

   ```bash
   cd remote
   pio device monitor
   ```

   `pio device monitor` doesn't show what you type: add `--echo`. On Windows,
   paste with a right-click. Paste the whole `PAIR ...` line and press Enter.
   The remote answers `OK paired with golfmap-ab12, restarting` and restarts so
   the new settings take effect (the monitor may drop: reopen it). Type
   `STATUS` to see its state; `wifi=` is 3 when it's on the hotspot, 1 if it
   can't see it, 4 if the hotspot refused it (wrong password); `rssi=` is the
   signal in dBm (about -70 or better is fine). Close the monitor with Ctrl+C.

If it won't stay on the hotspot, leave the monitor open: every join, address
and drop is printed with the Wi-Fi chip's reason (`wifi: not connected, reason
15 handshake timed out ...`). `SCAN` lists the networks it can see, with
channel, signal and security.

If it keeps getting `reason 202` or `reason 2` with a strong signal and the
right password (a phone gets in fine), the hotspot's channel is too crowded:
two smart bulbs on channel 6 did this at home. `SCAN` shows who else is on
each channel. Move the hotspot with `sudo CHANNEL=auto bash pi/hotspot.sh`
(picks the quietest of 1, 6 and 11; drops a Wi-Fi SSH session) or
`sudo CHANNEL=1 bash pi/hotspot.sh`. Worth doing once at the course too.

3. Delete `golfmap-remote.txt` from the stick: it holds the key.

Within a few seconds the remote shows **Linked**, and the cart's
**Settings → Remote control** card shows **Connected** with the remote's GPS,
its distance from the cart and its battery.

To pair with a new key (a lost remote): on the Pi,
`sudo rm /var/lib/golf-map/remote.key && sudo bash tashua-golf-map/pi/hotspot.sh <stick>`,
then pair the remote again. `UNPAIR` on the serial monitor wipes a remote.

## Using it

- **Top line:** *Linked* (green) talks to the cart; *Finding cart* is on the
  hotspot but not answered yet; *No hotspot* can't see the cart's Wi-Fi;
  *Not paired*.
- **GPS line:** satellites used and a rough accuracy, or *No GPS fix* (data
  but no fix yet: give it open sky) or *No GPS data* (check the wiring).
- **STOP:** acts the moment you touch it. Shows *STOP sent*, then
  *Trolley stopped* once the cart confirms. It works whether or not the
  trolley was told to move from the screen.
- **Yards screen:** swipe right (or tap) above STOP. A big number to the
  middle of the green from where you stand, front (F) and back (B) under it,
  and how far the cart is. The cart works these out (it knows the hole on its
  screen and the green), so they follow the hole the cart is showing. *Off
  course*: you're more than 2 km from the course; *Waiting for cart*: the
  cart's map isn't sending them (is its screen running?). Swipe left (or tap)
  for the status lines. STOP stays at the bottom on both, and a swipe that
  slides onto it doesn't press it.
- **PWR key:** hold 2 s to switch off (on battery).

## Testing without the hardware

The PC simulator has a mock remote that speaks the same protocol:

```bash
python server/gps_server.py --sim --sim-rate 0.25 --mock-trolley --mock-remote
```

Open http://127.0.0.1:8080, then **Settings**: the Remote control card shows
*Mock (testing)* with a position walking beside the cart.

## What's only checked on real hardware

Checked on a real board with a BN-880 and the Pi: screen, touch, GPS fix, running
on battery, pairing, joining the hotspot (also when the cart starts after the
remote), the link and STOP reaching the cart. Not yet: the yards screen and
swipes (simulator-tested on the cart's side only), STOP against a real
trolley (none is connected, so the remote shows *No trolley*), the battery
percentage against a real discharge, and range on the course.
