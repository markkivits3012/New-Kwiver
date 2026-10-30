#!/bin/bash
# Update the installed cart map from this folder: no internet needed, unlike
# pi/install.sh (which also installs packages and sets up the Pi; run that
# once on a new Pi). Backs up the current install first.
#
#   cd ~ && tar xzf /media/fampi/<stick>/golfmap-update.tgz
#   sudo bash tashua-golf-map/pi/update.sh
#   sudo reboot
#
# Undo: the command to restore the backup is printed at the end.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi
SRC=$(cd "$(dirname "$0")/.." && pwd)
DEST=/opt/tashua-golf-map
USER_HOME=$(getent passwd "${SUDO_USER:-pi}" | cut -d: -f6)
BACKUP="$USER_HOME/golfmap-backups/$(date +%Y%m%d-%H%M%S)"

[ -f "$SRC/app/index.html" ] && [ -f "$SRC/server/gps_server.py" ] || { echo "not a cart map folder: $SRC"; exit 1; }
[ -d "$DEST" ] || { echo "nothing installed at $DEST; run pi/install.sh first"; exit 1; }

echo "== backup -> $BACKUP"
mkdir -p "$BACKUP"
tar czf "$BACKUP/opt.tgz" -C /opt tashua-golf-map
cp /etc/systemd/system/golf-map.service "$BACKUP/"
chown -R "${SUDO_USER:-pi}:" "$(dirname "$BACKUP")"

echo "== new files -> $DEST"
# build the new copy beside the old one, then swap, so a failed copy leaves
# the running install untouched
rm -rf "$DEST.new"
mkdir -p "$DEST.new"
cp -r "$SRC/app" "$SRC/server" "$DEST.new/"
[ -d "$SRC/courses" ] && cp -r "$SRC/courses" "$DEST.new/"
install -m 0755 "$SRC/pi/kiosk.sh" "$DEST.new/kiosk.sh"
chown -R root:root "$DEST.new"
chmod -R u=rwX,go=rX "$DEST.new"   # service user can read, not write
rm -rf "$DEST.old"
mv "$DEST" "$DEST.old"
mv "$DEST.new" "$DEST"
rm -rf "$DEST.old"

echo "== map server"
install -m 0644 "$SRC/pi/golf-map.service" /etc/systemd/system/golf-map.service
systemctl daemon-reload
systemctl restart golf-map
for i in $(seq 1 15); do
  curl -fs -o /dev/null http://127.0.0.1:8080/api/courses && break
  sleep 1
done
if curl -fs http://127.0.0.1:8080/api/courses >/dev/null; then
  echo "   map server is up"
else
  echo "   MAP SERVER NOT ANSWERING: check with: systemctl status golf-map"
fi

echo
echo "Done. Reboot so the screen loads it: sudo reboot"
echo "To undo:"
echo "  sudo rm -rf $DEST && sudo tar xzf $BACKUP/opt.tgz -C /opt && sudo cp $BACKUP/golf-map.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo reboot"
