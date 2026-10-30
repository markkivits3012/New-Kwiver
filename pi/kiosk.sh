#!/bin/sh
# Full-screen Chromium on the 5" touchscreen, started at desktop login
# (hooked up by pi/install.sh or pi/kiosk-setup.sh). Waits for the map server before opening it.
URL=http://127.0.0.1:8080/

# keep a short log for when the screen doesn't come up (last run only)
mkdir -p "$HOME/.cache"
exec >"$HOME/.cache/golfmap-kiosk.log" 2>&1
echo "$(date) kiosk starting (WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-})"

for i in $(seq 1 60); do
  curl -fs -o /dev/null "$URL" && break
  sleep 1
done
echo "$(date) map server $(curl -fs -o /dev/null "$URL" && echo up || echo 'NOT answering after 60 s')"

BROWSER=$(command -v chromium-browser || command -v chromium)
[ -n "$BROWSER" ] || { echo "no chromium installed"; exit 1; }
echo "$(date) starting $BROWSER"
exec "$BROWSER" \
  --kiosk "$URL" \
  --noerrdialogs --disable-infobars --no-first-run \
  --incognito --disable-session-crashed-bubble --disable-translate \
  --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required \
  --overscroll-history-navigation=0 --disable-pinch \
  --password-store=basic
