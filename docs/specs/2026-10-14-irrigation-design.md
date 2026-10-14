# Irrigation Controller (Pi 4 + ESP-01S): Design

**Date:** 2026-10-14
**Status:** Approved 2026-10-14
**Supersedes:** `2026-10-10-irrigation-controller-design.md` (ESP-only phase 1)
**Repo:** private GitHub repo `chakrabortyniladri/irrigation-controller`

## Goal

Water one zone through a 24 V solenoid valve. You control it from a single web page on
the Pi 4, reached over Tailscale, with live camera video on one side and irrigation
controls on the other. Scheduled watering survives power cuts, and watering keeps
going (at 08:00) even when the Pi 4 is dead.

## Context and constraints

- **Pi 4 (`nilrasp4`)** hosts everything: web server, MQTT broker, camera, scheduler.
- **Pi 1 (`raspi1nil`)** is switched off for now, not retired. The fan project stays on it untouched. The irrigation project has no dependency on the Pi 1.
- **Remote access is Tailscale only.** The ESP is not on the tailnet, so away from home the Pi 4 is the only control path.
- **Overflow limit:** a single run must never exceed **90 s**.
- **Clock:** neither device has a battery-backed clock. Both need internet NTP before making any schedule decision.

## Architecture

```
 iPhone / browser ──Tailscale──▶ Pi 4 (nilrasp4)
                                 ┌─────────────────────────────────┐
                                 │ irrigation-server (Python 3)    │
                                 │  web page + JSON API (Basic auth)│
                                 │  on-demand MJPEG camera         │
                                 │  scheduler + same-day catch-up  │
                                 │  state.json (atomic writes)     │
                                 │ mosquitto (MQTT broker :1883)   │
                                 └───────────────┬─────────────────┘
                                                 │ irrigation/<guid>/{command,state,availability}
                                 ESP-01S relay ──┘ → 24 V solenoid
                                 (90 s cap, own timer, 08:00 fallback)
```

### Repo layout

```
firmware/irrigation_controller/   ESP-01S sketch (moved from iot-home-automation, then changed)
firmware/test/                    host tests (g++)
server/irrigation_server.py       HTTP + MQTT + camera wiring
server/schedule.py                pure schedule rule (no I/O)
server/state_store.py             atomic JSON load/save
server/camera.py                  on-demand camera (viewer refcount)
server/static/index.html          single page (vanilla HTML/JS)
server/test_*.py                  unittest
systemd/irrigation.service
docs/
```

Dependencies are installed from Raspberry Pi OS `apt` only: `python3-paho-mqtt`,
`python3-picamera2`, `mosquitto`. The HTTP server is Python's stdlib `http.server`.

## Safety limits (both devices)

| Constant | Value | Where |
|----------|-------|-------|
| `MAX_RUN_S` | 90 | ESP `config.h` (authoritative) and server config |
| `DEFAULT_RUN_S` | 60 | both |
| `CATCHUP_CUTOFF` | 22:00 | both |
| `FALLBACK_TIME` | 08:00 | ESP `config.h` |
| `FALLBACK_S` | 60 | ESP `config.h` |
| `PI_DEAD_AFTER` | 30 min without MQTT | ESP `config.h` |

The ESP clamps every open to `1..MAX_RUN_S`, whatever sends it. The server rejects
anything over 90 s with HTTP 400, so the UI shows the error instead of a silent clamp.

## Schedule rule (shared by Pi and ESP)

> Run if **enabled**, **clock synced**, **start ≤ now < CATCHUP_CUTOFF** (local time, same day), and **today's scheduled run has not happened**.

- This one rule covers both the normal on-time run and catch-up after a power cut.
- Due 08:00, Pi back at 21:30 → runs. Back at 22:15 → today is skipped, next run is tomorrow 08:00.
- A schedule start time must be earlier than `CATCHUP_CUTOFF`. The server and the page reject 22:00 or later.
- "Clock synced": on the Pi, `timedatectl show -p NTPSynchronized --value` is `yes`. On the ESP, year ≥ 2024 after `configTime`.

### Single record of "watered today": kept on the ESP

- The ESP stores `last_sched_day` (year·1000 + yday) in EEPROM. It updates it whenever a **scheduled** open starts: one from the Pi (`"scheduled":true`) or its own fallback.
- The ESP reports `last_sched_day` in its retained state.
- **Pi scheduler:** when the rule says run and the ESP is online, the Pi checks the ESP's `last_sched_day`. If it's already today, the Pi skips. Otherwise it sends `open` with `"scheduled":true` and retries every 10 s until the state shows the valve open with today's `last_sched_day`. The Pi then also writes today into its own `state.json` for display and history.
- The ESP **ignores** a scheduled open if its own clock is unsynced or `last_sched_day` is already today. This makes the Pi's 10 s retries harmless, and the ESP never restarts a scheduled run.
- A power cut mid-run still counts as today's run, because the ESP wrote the day when the run started. The run is not restarted.
- Manual Start/Stop never touches `last_sched_day`.
- Failure bias: a rare double run (each one ≤ 90 s) is preferred over a lost run.

### ESP fallback (Pi dead)

- "Pi dead" means no server heartbeat (`irrigation/server/heartbeat`, published by the server every 60 s) for ≥ `PI_DEAD_AFTER` (30 min), counted from boot or the last heartbeat. A heartbeat is used instead of the broker connection so that a crashed server with Mosquitto still running also triggers the fallback. Once set, "dead" stays latched until the next heartbeat, so `millis()` rollover can't clear it.
- When the Pi is dead, the ESP applies the same rule with its fixed `FALLBACK_TIME` = 08:00 and `FALLBACK_S` = 60, ending at `CATCHUP_CUTOFF`.
- The 30-minute grace period covers a normal power cut where both boot together and the ESP connects first.
- Example: Pi dead since yesterday → the ESP waters at exactly 08:00.
- The fallback values are fixed in firmware. Editing the Pi schedule does not change them.

## ESP-01S firmware changes (from the 2026-10-10 version)

- **Remove standalone mode completely:** no web server, Basic auth, UDP beacon or HTML. The ESP stays in MQTT mode and reconnects forever with backoff (1 s → 30 s). This also removes the "stuck in standalone after boot" failure.
- `MQTT_BROKER_HOST = "nilrasp4.local"`.
- Units change from minutes to seconds (`MAX_RUN_S`, `DEFAULT_RUN_S`).
- `schedule.cpp`: the user-editable slot is replaced by the fixed fallback, plus `last_sched_day` persistence and the "Pi dead" check. The Pi-side schedule never lives on the ESP.
- Kept: relay on GPIO0 active-LOW, closed first thing on boot, the on-device auto-off timer (works with Wi-Fi/MQTT down), OTA (`irr-<guid8>`), WiFiManager hotspot `Irrigation-Setup`, GUID in EEPROM.

### MQTT contract

- **Command** `irrigation/<guid>/command`:
  - `{"action":"open","seconds":45}`
  - `{"action":"open","seconds":60,"scheduled":true}`
  - `{"action":"close"}`
- **State** `irrigation/<guid>/state` (retained). Published on change and every 2 s while open, so the page countdown moves smoothly:
  `{"valve":"open","remaining_s":42,"max_s":90,"synced":true,"last_sched_day":2026287,"fallback":false}`
  `fallback` is true while the ESP considers the Pi dead.
- **Availability** `irrigation/<guid>/availability`: LWT `{"status":"offline"}`, `{"status":"online",...}` on connect.
- The server learns the GUID from the first availability or state message (whichever arrives first) and stores it in `state.json`. One ESP only; if a different GUID appears, it is logged and ignored.

## Pi 4 server

### HTTP API (all routes need Basic auth; credentials from `~/.irrigation.env`, user + password ≥ 12 chars, server refuses to start otherwise)

| Route | Purpose |
|-------|---------|
| `GET /` | the page |
| `GET /api/state` | `{"esp_online":true,"valve":"open","remaining_s":42,"max_s":90,"synced":true,"clock":"14:32","schedule":{"enabled":true,"time":"08:00","seconds":60},"last_run":"2026-10-14 08:00","next_run":"tomorrow 08:00"}` |
| `POST /api/open` | `{"seconds":N}`, 1..90, else 400. 503 if the ESP is offline |
| `POST /api/close` | 503 if the ESP is offline |
| `PUT /api/schedule` | `{"enabled":bool,"time":"HH:MM","seconds":N}`. 400 if time ≥ 22:00 or seconds outside 1..90 |
| `GET /stream.mjpg` | live MJPEG, 640×480 |

The constant-time auth check and the 1 s delay on failure are reused from `pi/camera_server.py`.

### On-demand camera

- The camera is off by default. The server starts Picamera2 when the first client opens `/stream.mjpg` and stops it when the last one disconnects. All viewers share one encoder.
- While the camera is off, `rpicam-*` commands are free to use it.
- If the camera fails to start, the stream returns 503 and the page shows "Camera unavailable". Irrigation is unaffected.

### State file

`/home/nilrasp/.local/share/irrigation/state.json`: schedule, ESP GUID, last scheduled
run. Writes use a temp file + `fsync` + `os.replace`, so a power cut leaves either the
old or the new file, never a torn one. A missing or corrupt file means defaults:
schedule disabled, 08:00, 60 s.

### systemd

The server listens on port 8000 and replaces the old `camera.service`, which must be disabled because only one process can hold the camera.
Mosquitto listens on 1883 without authentication. Anyone on the LAN or tailnet can publish to it, but the ESP still caps every run at 90 s.
`irrigation.service`: `After=network-online.target mosquitto.service`, `Restart=always`,
`EnvironmentFile=/home/nilrasp/.irrigation.env`. The scheduler loop runs every 5 s,
so catch-up starts within seconds of NTP sync after boot.

## Web page

Two panels side by side on wide screens, stacked on a phone. Vanilla HTML/JS. Polls
`/api/state` every 2 s.

```
┌──────────────────────────┬──────────────────────────┐
│ VIDEO                    │ VALVE        ● ESP online│
│ ┌──────────────────────┐ │   ● OPEN   0:42          │
│ │  (camera off)        │ │ [15][30][45][60][90] [__]s│
│ │                      │ │ [     START / STOP     ] │
│ └──────────────────────┘ │ Safety cutoff: 90 s max  │
│ [ ▶ Start video ]        ├──────────────────────────┤
│ auto-stops after 10 min  │ SCHEDULE  [x] daily      │
│                          │ at [08:00] for [60] s    │
│                          │ Next: tomorrow 08:00     │
│                          │ Last: today 08:00        │
│                          │ [ Save ]                 │
│                          │ Clock 14:32 · synced     │
└──────────────────────────┴──────────────────────────┘
```

- **Video:** Start sets `<img src="/stream.mjpg">`. Stop clears the `src`, which closes the connection. Auto-stops after 10 minutes.
- **Valve:** presets 15/30/45/60/90 s and a custom field (1..90). One Start/Stop button that follows the state. Controls are disabled with a "Valve offline" label while the ESP is offline.
- **Fallback indicator:** if the ESP reports `fallback:true`, a banner reads "ESP running on its own 08:00 fallback". This can only be seen after the Pi recovers.

## Error handling

| Situation | Behaviour |
|-----------|-----------|
| ESP offline | UI shows offline, open/close → 503, a due schedule waits (same day, < 22:00) |
| Pi down | No remote control. ESP fallback waters at 08:00 after 30 min without a server heartbeat |
| Power cut, both devices | Both boot. The Pi applies catch-up once NTP is synced, deduplicated by the ESP's `last_sched_day` |
| No internet after a power cut | No clock on either device, so no scheduled run until NTP. Optional DS3231 RTC on the Pi removes this (out of scope) |
| Camera busy/missing | Stream 503 + UI message. Irrigation unaffected |
| Bad API input | 400 with a message |
| state.json corrupt | Defaults, logged |

## Testing

- **Python (`unittest`, no hardware):**
  - schedule rule: on time, catch-up before 22:00, skip at or after 22:00, unsynced clock, already run today (via the ESP's `last_sched_day`), disabled, start ≥ 22:00 rejected
  - atomic state store: round trip, corrupt file → defaults
  - auth check
  - camera viewer refcount: start on first viewer, stop on last
  - API validation for seconds and time bounds
- **Firmware host tests (g++):** seconds clamp to 90, auto-close, rollover, the fallback rule (Pi dead/alive, 08:00–22:00, once per day, year boundary), `last_sched_day` byte round trip.
- **On the Pi, end to end:**
  1. Schedule a run 2 min ahead and confirm it fires.
  2. Pull the power before the run time and restore it after; confirm the catch-up and that it waters only once.
  3. Stop `irrigation.service` for more than 30 min across `FALLBACK_TIME` (temporarily set a near time) and confirm the ESP fallback.

## Out of scope

Multiple zones, multiple schedule slots, day-of-week, soil moisture, DS3231 RTC,
integration with the fan dashboard, Alexa.
