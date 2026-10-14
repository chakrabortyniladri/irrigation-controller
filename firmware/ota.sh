#!/bin/sh
# firmware/ota.sh — build the firmware and push it over Wi-Fi (OTA). Run on the Pi 4.
# Usage: ./ota.sh [host-or-ip]   (default: the ESP the server is paired with)
set -e
PATH=$HOME/.local/bin:$PATH   # arduino-cli install location
cd "$(dirname "$0")"
SK=irrigation_controller
OUT=/tmp/irr-build
CORE=3.1.2   # keep in step with `arduino-cli core install esp8266:esp8266@...`
ESPOTA=~/.arduino15/packages/esp8266/hardware/esp8266/$CORE/tools/espota.py

[ -f $SK/secrets.h ] || { echo "missing $SK/secrets.h: cp $SK/secrets.h.example $SK/secrets.h and set the OTA password"; exit 1; }
PASS=$(sed -n 's/.*OTA_PASSWORD *= *"\(.*\)".*/\1/p' $SK/secrets.h)

HOST=$1
if [ -z "$HOST" ]; then
  GUID=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.local/share/irrigation/state.json")))["guid"] or "")')
  [ -n "$GUID" ] || { echo "no paired ESP in state.json; pass a host or IP"; exit 1; }
  HOST=irr-$(echo "$GUID" | cut -c1-8).local
fi

arduino-cli compile -b esp8266:esp8266:generic:eesz=1M --output-dir $OUT $SK
echo "uploading to $HOST"
python3 $ESPOTA -i "$HOST" -p 8266 -a "$PASS" -f $OUT/$SK.ino.bin -r
