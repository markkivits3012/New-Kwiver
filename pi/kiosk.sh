#!/bin/sh
# Full-screen Chromium on the 5" touchscreen, started at desktop login
# (hooked up by pi/install.sh). Waits for the map server before opening it.
URL=http://127.0.0.1:8080/

for i in $(seq 1 60); do
  curl -fs -o /dev/null "$URL" && break
  sleep 1
done

BROWSER=$(command -v chromium-browser || command -v chromium)
exec "$BROWSER" \
  --kiosk "$URL" \
  --noerrdialogs --disable-infobars --no-first-run \
  --incognito --disable-session-crashed-bubble --disable-translate \
  --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required \
  --overscroll-history-navigation=0 --disable-pinch \
  --password-store=basic
