#!/bin/sh
# Fix touch that lands in the wrong spot (offset, stretched, mirrored or rotated).
# Installs a libinput calibration matrix for the touchscreen (works under both
# Wayland and X11 on Raspberry Pi OS). The six numbers come from the touch test
# page: hold the GPS status line on the map for 3 s, tap the 5 crosses.
#
#   sudo bash pi/set-touch.sh a b c d e f   # apply (stacks on top of an earlier fix)
#   sudo bash pi/set-touch.sh reset         # back to the screen's own default
#
# Then reboot and run the touch test again; it should say "Touch is accurate".
set -e
RULE=/etc/udev/rules.d/99-golf-map-touch.rules
[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }

if [ "$1" = reset ]; then
  rm -f "$RULE"
  udevadm control --reload-rules
  echo "Touch calibration removed. Reboot to apply."
  exit 0
fi
[ $# -eq 6 ] || { echo "Usage: sudo bash pi/set-touch.sh a b c d e f   (or: reset)"; exit 1; }

# The test page measures touches after any fix already installed, so combine:
# new = page's correction x previous matrix.
OLD="1 0 0 0 1 0"
if [ -f "$RULE" ]; then
  OLD=$(sed -n 's/.*LIBINPUT_CALIBRATION_MATRIX}="\(.*\) 0 0 1".*/\1/p' "$RULE")
fi
NEW=$(python3 -c '
import sys
c = [float(v) for v in sys.argv[1:7]]
o = [float(v) for v in sys.argv[7:13]]
C = [c[0:3], c[3:6], [0, 0, 1]]
O = [o[0:3], o[3:6], [0, 0, 1]]
M = [[sum(C[r][k] * O[k][j] for k in range(3)) for j in range(3)] for r in range(2)]
print(" ".join(f"{v:.5f}" for row in M for v in row))
' "$@" $OLD)

cat > "$RULE" <<EOF
# Written by tashua-golf-map pi/set-touch.sh: touchscreen calibration for libinput.
# Remove with: sudo bash pi/set-touch.sh reset
ACTION=="add|change", KERNEL=="event[0-9]*", ENV{ID_INPUT_TOUCHSCREEN}=="1", ENV{LIBINPUT_CALIBRATION_MATRIX}="$NEW 0 0 1"
EOF
udevadm control --reload-rules
udevadm trigger --subsystem-match=input --action=change
echo "Touch calibration set to: $NEW"
echo "Reboot, then run the touch test again to check."
