#!/bin/bash
# Make the cart map open full screen by itself after every boot:
#   - log in to the desktop automatically as the desktop user (no password screen)
#   - use the labwc desktop, and start /opt/tashua-golf-map/kiosk.sh from it
#   - never blank the screen
# Prints what it found first, so it also works as a check. Safe to re-run.
#
#   sudo bash pi/kiosk-setup.sh            # for the user running sudo
#   sudo bash pi/kiosk-setup.sh fampi      # or name the desktop user
#   sudo reboot
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi
SRC=$(cd "$(dirname "$0")/.." && pwd)
DEST=/opt/tashua-golf-map
# the desktop user: named, or whoever ran sudo, or whoever owns this folder
U=${1:-${SUDO_USER:-$(stat -c %U "$SRC")}}
if [ "$U" = root ] || ! H=$(getent passwd "$U" | cut -d: -f6) || [ -z "$H" ]; then
  echo "Can't tell which user the screen logs in as. Run: sudo bash pi/kiosk-setup.sh <user>"
  exit 1
fi
AUTOSTART="$H/.config/labwc/autostart"
LINE="$DEST/kiosk.sh &"

echo "== before"
echo "   desktop user:     $U"
echo "   autologin:        $(grep -rhs '^autologin-user=' /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/ | tail -1 || true)"
echo "   session:          $(grep -rhs '^\(autologin-session\|user-session\)=' /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/ | tr '\n' ' ')"
echo "   labwc autostart:  $( [ -s "$AUTOSTART" ] && tr '\n' ';' < "$AUTOSTART" || echo '(empty or missing)')"
echo "   kiosk launcher:   $( [ -x "$DEST/kiosk.sh" ] && echo present || echo MISSING)"
echo "   browser:          $(command -v chromium-browser || command -v chromium || echo NOT INSTALLED)"
echo "   map server:       $(systemctl is-active golf-map)"

echo "== kiosk launcher"
if [ -f "$SRC/pi/kiosk.sh" ]; then
  install -m 0755 "$SRC/pi/kiosk.sh" "$DEST/kiosk.sh"
  echo "   installed $DEST/kiosk.sh"
fi

echo "== desktop: labwc, autologin as $U, no blanking"
if command -v raspi-config >/dev/null; then
  export SUDO_USER="$U"   # raspi-config sets autologin for this user
  raspi-config nonint do_wayland W3 && echo "   labwc: ok" || echo "   labwc: raspi-config couldn't set it (check: sudo raspi-config > Advanced > Wayland)"
  raspi-config nonint do_boot_behaviour B4 && echo "   autologin: ok" || echo "   autologin: raspi-config failed"
  raspi-config nonint do_blanking 1 && echo "   blanking off: ok" || echo "   blanking: raspi-config failed"
else
  echo "   raspi-config not found: set desktop autologin for $U by hand"
fi

echo "== start the map at login"
mkdir -p "$(dirname "$AUTOSTART")"
touch "$AUTOSTART"
if grep -qF "$DEST/kiosk.sh" "$AUTOSTART"; then
  echo "   already in $AUTOSTART"
else
  echo "$LINE" >> "$AUTOSTART"
  echo "   added to $AUTOSTART"
fi
chown -R "$U:" "$H/.config"

echo "== after"
echo "   autologin:        $(grep -rhs '^autologin-user=' /etc/lightdm/lightdm.conf /etc/lightdm/lightdm.conf.d/ | tail -1 || true)"
echo "   labwc autostart:  $(tr '\n' ';' < "$AUTOSTART")"
echo
echo "Done. Reboot to try it: sudo reboot"
echo "If the map doesn't appear, send: cat $H/.cache/golfmap-kiosk.log"
