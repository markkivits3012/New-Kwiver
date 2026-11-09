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

Our boards are **ZS-X11H V2**. The speed signal is PWM, so it goes to the
**P** pin on the small row along the bottom edge (G S V P G, left to right),
**not** to the `0-5V` screw terminal: that one wants a steady voltage, and PWM
on it only makes the motor hum. P takes 2.5-5 V PWM at 50 Hz-20 kHz, so the
NodeMCU's 3.3 V works directly. Before P does anything:

- **Short the "external PWM" solder pads** under the heatsink (a blob of
  solder across the two empty pads; the maker's picture marks them "usually
  off, external PWM signal input short connected").
- Turn the **blue speed knob fully anticlockwise** (minimum). V2 has no
  jumper cap.

| NodeMCU | Left board | Right board |
|---|---|---|
| D1 (GPIO5) | **P** (bottom row, soldered) | |
| D5 (GPIO14) | DIR (screw terminal) | |
| D7 (GPIO13) | BRAKE | |
| D2 (GPIO4) | | **P** |
| D6 (GPIO12) | | DIR |
| D0 (GPIO16) | | BRAKE |
| GND | GND | GND |

The screw terminals on the control side are, top to bottom: 5V (an output:
leave it), 0-5V (unused), GND, DIR, BRAKE (on at 2.5-5 V), STOP (active low;
leave it unconnected).

- **GND to each board's signal ground is required**, or nothing works.
- A 10k resistor from each speed line to GND keeps the wheels still while
  the NodeMCU boots or is unplugged.
- Hall wires (thin): the board's 5-wire plug to the motor's, name to name
  (5V, GND, Ha, Hb, Hc). Phases (thick): **MA green, MB blue, MC yellow**
  worked on the left motor (yellow/green/blue in order needed a push to
  start). With the halls fixed, the right phase order starts the wheel on its
  own, smooth and quiet at low current; try the 6 orders, power off between.
- Test with a supply that can give **5 A or more**: at 800 mA the motor only
  hummed.

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
