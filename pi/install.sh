#!/bin/bash
# Install the Tashua Knolls cart map on a Raspberry Pi 4 running Raspberry Pi
# OS (Bookworm or later, Desktop edition) with a USB GPS and a 5" touchscreen.
#
#   cd tashua-golf-map
#   sudo bash pi/install.sh      # "bash" because zips made on Windows drop the +x bit
#   sudo reboot
#
# Safe to re-run (e.g. after copying in new flyover clips).
# Written 2026-10-27 and NOT yet tested on real Pi hardware.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi
SRC=$(cd "$(dirname "$0")/.." && pwd)
# the user the screen logs in as: whoever ran sudo, else whoever owns this folder
DESKTOP_USER=${SUDO_USER:-$(stat -c %U "$SRC")}
DESKTOP_HOME=$(getent passwd "$DESKTOP_USER" | cut -d: -f6)
DEST=/opt/tashua-golf-map

echo "== packages"
apt-get update
apt-get install -y gpsd gpsd-clients python3 curl
apt-get install -y chromium-browser || apt-get install -y chromium

echo "== gpsd (USB GPS)"
GPS_DEV=""
for d in /dev/serial/by-id/* /dev/ttyACM0 /dev/ttyUSB0; do
  [ -e "$d" ] && { GPS_DEV=$d; break; }
done
# No GPS plugged in right now: keep the device an earlier install (or someone)
# set, rather than wiping it. gpsd's USB auto-detect skips generic serial
# adapters (CP2102, CH340), so an empty DEVICES means no GPS for those.
if [ -z "$GPS_DEV" ] && [ -f /etc/default/gpsd ]; then
  GPS_DEV=$(sed -n 's/^DEVICES="\(.*\)"/\1/p' /etc/default/gpsd)
fi
cat > /etc/default/gpsd <<EOF
# Written by tashua-golf-map pi/install.sh
DEVICES="$GPS_DEV"
GPSD_OPTIONS="-n"
USBAUTO="true"
EOF
systemctl enable --now gpsd.socket
systemctl restart gpsd || true
echo "   GPS device: ${GPS_DEV:-none found yet (plug it in; USBAUTO will pick it up)}"

echo "== app -> $DEST"
id golfmap >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin golfmap
mkdir -p "$DEST"
cp -r "$SRC/app" "$SRC/server" "$DEST/"
[ -d "$SRC/courses" ] && cp -r "$SRC/courses" "$DEST/"   # extra courses (test courses), if any
chown -R root:root "$DEST"
chmod -R u=rwX,go=rX "$DEST"   # service user can read, not write

echo "== map server service"
install -m 0644 "$SRC/pi/golf-map.service" /etc/systemd/system/golf-map.service
systemctl daemon-reload
systemctl enable --now golf-map.service

echo "== kiosk browser at login for $DESKTOP_USER"
install -m 0755 "$SRC/pi/kiosk.sh" "$DEST/kiosk.sh"
# labwc (current Raspberry Pi OS desktop)
mkdir -p "$DESKTOP_HOME/.config/labwc"
touch "$DESKTOP_HOME/.config/labwc/autostart"
grep -q "$DEST/kiosk.sh" "$DESKTOP_HOME/.config/labwc/autostart" ||
  echo "$DEST/kiosk.sh &" >> "$DESKTOP_HOME/.config/labwc/autostart"
# wayfire (earlier Bookworm desktop)
if [ -f "$DESKTOP_HOME/.config/wayfire.ini" ] && ! grep -q "$DEST/kiosk.sh" "$DESKTOP_HOME/.config/wayfire.ini"; then
  printf '\n[autostart]\ngolfmap = %s/kiosk.sh\n' "$DEST" >> "$DESKTOP_HOME/.config/wayfire.ini"
fi
chown -R "$DESKTOP_USER:" "$DESKTOP_HOME/.config"

echo "== screen: auto-login to desktop, no blanking"
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_boot_behaviour B4 || true   # desktop autologin
  raspi-config nonint do_blanking 1 || true          # 1 = disable blanking
fi

echo
echo "Done. Check with:"
echo "  systemctl status golf-map     # server running"
echo "  cgps -s                       # GPS has a fix (needs open sky)"
echo "  curl -s http://127.0.0.1:8080/ | head -3"
echo "Then: sudo reboot"
