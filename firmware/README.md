# ESP-01S valve firmware

MQTT-only valve controller. The Pi 4 (`nilrasp4.local`, Mosquitto) sends commands. The ESP enforces the 90 s cap, and if the Pi's heartbeat stops for 30 min it waters on its own at 08:00 for 60 s.

## Wiring
ESP-01S relay module, relay on GPIO0, active-LOW. 24 V → buck → 5 V powers the module. The relay switches the 24 V solenoid.

## Flash
- Arduino IDE board: **Generic ESP8266 Module**, Flash Size 1MB (FS: none, OTA: ~502KB).
- Libraries: WiFiManager (tzapu), PubSubClient, ArduinoJson 7.
- First flash over USB. After that, use OTA: the network port is `irr-<guid8>`, password in `secrets/ota-passwords.md`.
- First boot: join Wi-Fi `Irrigation-Setup` from your phone and pick your home Wi-Fi.

## Check
```bash
mosquitto_sub -h nilrasp4.local -t 'irrigation/#' -v
mosquitto_pub -h nilrasp4.local -t 'irrigation/<guid>/command' -m '{"action":"open","seconds":15}'
```

## Host tests
`cd test && make test`
