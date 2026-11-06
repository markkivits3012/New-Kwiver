#!/bin/bash
# Wi-Fi hotspot for the handheld remote, and its pairing key.
#
#   sudo bash pi/hotspot.sh                       # set up (or check) and start it
#   sudo bash pi/hotspot.sh /media/fampi/7CC9-3CC7  # also write the pairing line to the stick
#   sudo bash pi/hotspot.sh off                   # stop it and don't start it at boot
#
# The Pi's Wi-Fi becomes an access point at 10.42.0.1 (NetworkManager shared
# mode); the remote joins it and sends to UDP 8091 (remote/PROTOCOL.md). While
# it's on, the Pi can't also join another Wi-Fi network; updates come on the
# USB stick anyway.
#
# Safe to re-run: keeps the existing network name, password and pairing key,
# so a paired remote stays paired. Delete /var/lib/golf-map/remote.key and
# re-run to make a new key (then pair the remote again).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi
command -v nmcli >/dev/null || { echo "NetworkManager (nmcli) not found"; exit 1; }
CON=golfmap-hotspot
IFACE=wlan0
STATE=/var/lib/golf-map
KEY=$STATE/remote.key

if [ "${1:-}" = "off" ]; then
  if nmcli -t -f NAME connection show | grep -qx "$CON"; then
    nmcli connection modify "$CON" connection.autoconnect no
    nmcli connection down "$CON" >/dev/null 2>&1 || true
    echo "hotspot off; it won't start at boot. Turn it back on with: sudo bash pi/hotspot.sh"
  else
    echo "no hotspot set up; nothing to do"
  fi
  exit 0
fi
OUT=${1:-}
[ -z "$OUT" ] || [ -d "$OUT" ] || { echo "no such folder: $OUT"; exit 1; }

# NetworkManager's shared mode hands out addresses with dnsmasq
command -v dnsmasq >/dev/null || echo "WARNING: dnsmasq not found; the remote won't get an address. Install dnsmasq-base (needs internet)."

echo "== pairing key"
mkdir -p "$STATE"
if [ -s "$KEY" ]; then
  echo "   kept $KEY"
else
  head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEY"
  echo "   made a new key in $KEY (pair the remote again)"
fi
id golfmap >/dev/null 2>&1 && chown golfmap:golfmap "$STATE" "$KEY"
chmod 600 "$KEY"

echo "== hotspot ($CON on $IFACE)"
rfkill unblock wifi 2>/dev/null || true
if nmcli -t -f NAME connection show | grep -qx "$CON"; then
  SSID=$(nmcli -g 802-11-wireless.ssid connection show "$CON")
  PASS=$(nmcli -s -g 802-11-wireless-security.psk connection show "$CON")
  echo "   kept network $SSID and its password"
else
  MAC=$(tr -d ':' < "/sys/class/net/$IFACE/address" 2>/dev/null || echo 0000)
  SSID="golfmap-${MAC: -4}"
  # no l, o, 0, 1: easy to type. `|| true`: tr dies of SIGPIPE when head has
  # its 12 bytes, and pipefail would otherwise end the script right here
  PASS=$(tr -dc 'a-km-np-z2-9' < /dev/urandom | head -c 12 || true)
  nmcli connection add type wifi ifname "$IFACE" con-name "$CON" ssid "$SSID" >/dev/null
  echo "   made network $SSID"
fi
# (re)apply every setting, so a re-run repairs a hand-edited connection
nmcli connection modify "$CON" \
  connection.autoconnect yes connection.autoconnect-priority 100 \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
  ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled \
  wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
  wifi-sec.psk "$PASS"
if nmcli connection up "$CON" >/dev/null; then
  echo "   hotspot up at 10.42.0.1"
else
  echo "   HOTSPOT DIDN'T START: check the Wi-Fi country (sudo raspi-config, Localisation) and: nmcli device status"
fi

LINE="PAIR $SSID $PASS $(cat "$KEY")"
if [ -n "$OUT" ]; then
  printf '%s\n' "$LINE" > "$OUT/golfmap-remote.txt"
  sync
  echo "   wrote the pairing line to $OUT/golfmap-remote.txt"
fi
echo
echo "Pair the remote (remote/README.md): send it this line over USB serial:"
echo "  $LINE"
echo "The map server picks the key up by itself within a few seconds."
