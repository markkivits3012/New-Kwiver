#!/bin/sh
# Pick which course the cart map shows, then restart it.
#
#   sudo bash pi/set-course.sh whitney    # a folder in courses/ (installed by pi/install.sh)
#   sudo bash pi/set-course.sh tashua     # back to Tashua Knolls (the default)
set -e
DEST=/opt/tashua-golf-map
DROPIN=/etc/systemd/system/golf-map.service.d/course.conf
[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }
[ $# -eq 1 ] || { echo "Usage: sudo bash pi/set-course.sh <course>   (tashua, or one of: $(ls $DEST/courses 2>/dev/null | tr '\n' ' '))"; exit 1; }

C=$1
case "$C" in *[!a-z0-9_-]*|"") echo "Bad course name: $C"; exit 1 ;; esac
if [ "$C" != tashua ] && [ ! -f "$DEST/courses/$C/app/course.js" ]; then
  echo "No course '$C' in $DEST/courses. Installed: $(ls $DEST/courses 2>/dev/null | tr '\n' ' ')"
  echo "(Copy in a new package and run: sudo bash pi/install.sh)"
  exit 1
fi

rm -f /var/lib/golf-map/course.txt   # a course picked on the screen would win over this one
mkdir -p "$(dirname "$DROPIN")"
printf '[Service]\nEnvironment=GOLF_COURSE=%s\n' "$C" > "$DROPIN"
systemctl daemon-reload
systemctl restart golf-map
echo "Course set to: $C. Reboot (sudo reboot) so the screen loads it."
