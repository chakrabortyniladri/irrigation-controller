# ESP-01S valve firmware

MQTT-only valve controller. The Pi 4 (`nilrasp4.local`, Mosquitto) sends commands. The ESP enforces the 90 s cap, and if the Pi's heartbeat stops for 30 min it waters on its own at 08:00 for 60 s.

## Wiring
ESP-01S relay module, relay on GPIO0, active-LOW. 24 V → buck → 5 V powers the module. The relay switches the 24 V solenoid.

## Flash
- Arduino IDE board: **Generic ESP8266 Module**, Flash Size 1MB (FS: none, OTA: ~502KB).
- Libraries: WiFiManager (tzapu), PubSubClient, ArduinoJson 7.
- First flash over USB. After that, use OTA: the network port is `irr-<guid8>`, password in `irrigation_controller/secrets.h`.
- Before the first build: `cp irrigation_controller/secrets.h.example irrigation_controller/secrets.h` and set a password (gitignored).
- First boot: join Wi-Fi `Irrigation-Setup` from your phone and pick your home Wi-Fi.

## OTA from the Pi 4
One-time setup on the Pi:
```bash
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=~/.local/bin sh
~/.local/bin/arduino-cli config init
~/.local/bin/arduino-cli config add board_manager.additional_urls https://arduino.esp8266.com/stable/package_esp8266com_index.json
~/.local/bin/arduino-cli core update-index && ~/.local/bin/arduino-cli core install esp8266:esp8266@3.1.2
~/.local/bin/arduino-cli lib install WiFiManager@2.0.17 PubSubClient@2.8 ArduinoJson@7.4.3
```
Put `irrigation_controller/secrets.h` on the Pi too (gitignored, so `git pull` won't bring it).

Each update: `cd ~/irrigation-controller && git pull && firmware/ota.sh`. This targets the paired ESP; `firmware/ota.sh 192.168.0.9` targets a specific one.

## Check
```bash
mosquitto_sub -h nilrasp4.local -t 'irrigation/#' -v
mosquitto_pub -h nilrasp4.local -t 'irrigation/<guid>/command' -m '{"action":"open","seconds":15}'
```

## Host tests
`cd test && make test`
