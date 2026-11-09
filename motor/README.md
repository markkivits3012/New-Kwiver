# Motor controller (NodeMCU + two ZS-X11H)

Drives the cart's two hoverboard wheels. A NodeMCU (ESP8266) sits between the
Pi and two ZS-X11H brushless motor boards, so the wheels stop on their own if
the Pi or the link fails: every drive command lasts 3 s at most unless it's
repeated. For now it's driven from the PC's serial monitor; the Pi comes later.

**Milestone 1:** both wheels forward from the serial monitor, wheels off the
ground.

## Safety first

- **Wheels off the ground** (cart on blocks) until the code is trusted.
- **A fuse in each board's battery lead**: the boards have none. **Check the
  power polarity twice**: reversed, it destroys a board.
- A way to cut battery power by hand (switch or connector) within reach.
- Speed is capped at 30% in the firmware (`MAX_PCT`) while testing.
- The battery is 40 V (up to about 42 V charged). LM2596 buck modules are rated
  40 V in: don't run one from it. On the bench the NodeMCU runs from USB.

## Wiring

On each ZS-X11H, **remove the jumper on its speed knob** so it takes PWM.

| NodeMCU | Left board | Right board |
|---|---|---|
| D1 (GPIO5) | speed (PWM) | |
| D5 (GPIO14) | direction (DR) | |
| D7 (GPIO13) | brake (BRAK) | |
| D2 (GPIO4) | | speed (PWM) |
| D6 (GPIO12) | | direction (DR) |
| D0 (GPIO16) | | brake (BRAK) |
| GND | signal GND | signal GND |

- **GND to each board's signal ground is required**, or nothing works.
- A 10k resistor from each speed line to GND keeps the wheels still while
  the NodeMCU boots or is unplugged.
- Motor phase and hall wires go to the boards as for any hoverboard motor. If a
  wheel judders or won't start, the hall or phase order is off (low voltage
  first while sorting that out).
- The NodeMCU is 3.3 V and the boards expect 5 V signals. Try it direct first.
  If a wheel doesn't respond to speed, add a level shifter (74AHCT125) or one
  transistor per line.

## Build, flash, drive

Same PlatformIO setup as the remote (see WORKLOG.md for the Windows details):

```
cd motor
pio run -t upload
pio device monitor
```

`pio device list` shows the NodeMCU's COM port; add `--upload-port COMx` if
there's more than one board plugged in. Then type, one per line:

| Command | Does |
|---|---|
| `F 20` | both wheels forward at 20% (for 3 s, then stop) |
| `B 20` | both backward |
| `L 20`, `R -15` | one wheel (negative = backward) |
| `S` | stop and brake now |
| `STATUS` | each wheel's target and current speed |

Start each wheel alone at 10% (`L 10`, then `R 10`). If one turns backward
for "forward", flip its `rev` in `src/main.cpp`. Speed ramps up and down
(0 to 30% in about 0.6 s), and a wheel only reverses after it has stopped.

## Not done yet

- Speed feedback from the boards' hall pulse output (odometry).
- The Pi sending commands over USB, and the remote's STOP stopping the wheels.
- Power from the battery on the cart (needs a buck converter rated for 60 V in).
