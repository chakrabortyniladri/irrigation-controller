# Irrigation Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one web page on the Pi 4 with on-demand live video and controls for the garden valve. Scheduled watering must survive a power cut. When the Pi is dead, the ESP-01S waters by itself at 08:00.

**Architecture:** The Pi 4 runs one Python service (stdlib `http.server`): the page, a JSON API, the camera and the scheduler. It talks to the ESP-01S over a local Mosquitto broker. The ESP is MQTT-only. It owns the hard 90 s cap and the "watered today" record (`last_sched_day` in EEPROM), and runs its own 08:00 fallback if the server's heartbeat stops for 30 min.

**Tech Stack:**
- **ESP:** ESP8266 Arduino core, WiFiManager, PubSubClient, ArduinoOTA, ArduinoJson v7.
- **Pi:** Python 3.11 (Raspberry Pi OS Bookworm), `python3-paho-mqtt`, `python3-picamera2`, `mosquitto`, all from apt.
- **Tests:** `unittest` on the Python side; `g++` host tests on the firmware side.

**Spec:** `docs/specs/2026-10-14-irrigation-design.md`

## Global Constraints

### Limits and schedule
- `MAX_RUN_S = 90`. The ESP clamps every open to 1..90; the server rejects anything outside 1..90 with HTTP 400.
- `DEFAULT_RUN_S = 60`, `FALLBACK_S = 60`, `FALLBACK_TIME = 08:00`, default Pi schedule `08:00`, 60 s, disabled.
- `CATCHUP_CUTOFF = 22:00`. Schedule rule: enabled AND clock synced AND start ≤ now < 22:00 AND not already run today. A start time ≥ 22:00 is rejected.
- Day key = `year*1000 + tm_yday`, where `tm_yday` is **0-based** (C `struct tm`). Python: `dt.year*1000 + dt.timetuple().tm_yday - 1`.
- Pi dead = no `irrigation/server/heartbeat` for 30 min (latched). The server publishes the heartbeat every 60 s, not retained.

### MQTT contract
- Topics:
  - `irrigation/<guid>/command`, `irrigation/<guid>/state` (retained), `irrigation/<guid>/availability` (retained, LWT offline)
  - `irrigation/server/heartbeat`
- Commands are never retained. A retained `open` would replay on every ESP reconnect.
- Command JSON:
  - `{"action":"open","seconds":N}`
  - `{"action":"open","seconds":N,"scheduled":true}`
  - `{"action":"close"}`
- State JSON: `{"valve":"open|closed","remaining_s":N,"max_s":90,"synced":bool,"last_sched_day":N,"fallback":bool}`.
- The ESP ignores `scheduled:true` opens when it is unsynced or `last_sched_day` is already today. The day is recorded when the run **starts**.

### Pi service
- HTTP on port 8000, Basic auth on every route (`IRRIGATION_USER`, `IRRIGATION_PASS` ≥ 12 chars from `~/.irrigation.env`). It refuses to start otherwise.
- POST/PUT require `Content-Type: application/json`, which blocks cross-site form posts.
- Pi timezone must be `Asia/Kolkata`. ESP TZ is `IST-5:30`.
- No new pip dependencies. Python code must run on 3.11 (no 3.12+ syntax). Module-level imports must not need paho/picamera2, so tests run on the Mac.
- State file `~/.local/share/irrigation/state.json`, written as temp + fsync + `os.replace` + directory fsync.

### Firmware
- Relay on GPIO0, active-LOW. Set the relay off before `pinMode(OUTPUT)`. Close the valve when an OTA update starts.

## Review Focus

1. **Valve stays open longer than 90 s while the ESP loop is blocked.** MQTT reconnects, DNS lookups and OTA all stall `loop()`. Expected: DNS and TCP timeouts ≤ 1 s, and the relay closes when OTA starts. Covered by the timeouts and `onStart` in Task 3, plus manual check 6 in Task 10.
2. **Python `tm_yday` is 1-based, C's is 0-based.** An off-by-one makes the Pi think today never ran and water twice. Pinned by `test_day_key_matches_esp` in Task 4.
3. **Pi retries while the ESP is unsynced or has already run.** Expected: the ESP ignores the open and the valve never restarts. Pinned by `test_accept_scheduled` (Task 2) and `test_retry_spacing` (Task 7).
4. **First boot ordering of retained messages.** The ESP's `state` may arrive before `availability`. Expected: the GUID is learned from whichever arrives first. Pinned by `test_learns_guid_from_state_first` (Task 7).
5. **Bad input from the browser:** `seconds` as `true`, `"30"`, `null`, or 91; time `"22:00"` or `"8:00"`. Expected: 400 with a message and nothing published. Pinned by `test_open_validates` and `test_validate_schedule` (Tasks 4 and 7).

---

## File Structure

```
firmware/irrigation_controller/config.h         constants (Global Constraints values)
firmware/irrigation_controller/valve.h/.cpp     pure: run timer + 90 s clamp + command parse
firmware/irrigation_controller/fallback.h/.cpp  pure: clock/day/window rule, PiWatch, EEPROM day bytes, state JSON
firmware/irrigation_controller/network.h/.cpp   Wi-Fi, MQTT (reconnect forever), OTA
firmware/irrigation_controller/irrigation_controller.ino  wiring: relay, EEPROM, commands, loop
firmware/test/{Makefile,test_runner.h,test_valve.cpp,test_fallback.cpp}
firmware/README.md
server/schedule.py        pure: limits, day_key, due, next_run, validation
server/state_store.py     atomic load/save of state.json
server/camera.py          FrameBuffer, Camera (viewer refcount), picamera() start/stop pair
server/irrigation_server.py  App (all logic), Handler (HTTP), main (wiring)
server/static/index.html  the page
server/test_schedule.py, test_state_store.py, test_camera.py, test_app.py, test_http.py
systemd/irrigation.service, systemd/mosquitto-irrigation.conf
README.md                 deploy + end-to-end checks
```

All Python commands run from `server/`, and all firmware test commands from `firmware/test/`.

---

### Task 1: Firmware import, valve in seconds

**Files:**
- Copy from `../iot-home-automation/devices/irrigation-controller/firmware/test/test_runner.h` to `firmware/test/test_runner.h` (unchanged).
- Create: `firmware/irrigation_controller/config.h`, `valve.h`, `valve.cpp`
- Create: `firmware/test/Makefile`, `firmware/test/test_valve.cpp`

**Interfaces:**
- Produces:
  - `enum class Cmd { NONE, OPEN, CLOSE }`, `Cmd parseCmd(const char*)`, `uint16_t clampSeconds(long)`
  - `struct Valve { bool isOpen; uint32_t startMs, durationMs; void open(long seconds, uint32_t nowMs); bool close(); bool tick(uint32_t nowMs); uint32_t remainingS(uint32_t nowMs) const; }`
  - All constants in `config.h`.

- [ ] **Step 1: Copy the test harness and write `config.h`**

```bash
mkdir -p firmware/irrigation_controller firmware/test
cp ../iot-home-automation/devices/irrigation-controller/firmware/test/test_runner.h firmware/test/
```

`firmware/irrigation_controller/config.h`:

```cpp
// firmware/irrigation_controller/config.h
#pragma once
#include <stdint.h>

// ---- GPIO ----
constexpr uint8_t PIN_RELAY        = 0;     // GPIO0 on ESP-01S relay module
constexpr bool    RELAY_ACTIVE_LOW = true;  // module energises relay when GPIO0 is LOW

// ---- Valve safety: more than 90 s of water overflows ----
constexpr uint16_t MAX_RUN_S     = 90;   // hard cap, every open request is clamped to this
constexpr uint16_t DEFAULT_RUN_S = 60;   // used when seconds is 0/missing/negative

// ---- Schedule window (same rule as the Pi) ----
constexpr uint8_t  CUTOFF_H   = 22, CUTOFF_M   = 0;   // no scheduled/catch-up run at or after this
constexpr uint8_t  FALLBACK_H = 8,  FALLBACK_M = 0;   // ESP's own daily run while the Pi is dead
constexpr uint16_t FALLBACK_S = 60;
constexpr uint32_t PI_DEAD_AFTER_MS = 30UL * 60UL * 1000UL;   // no server heartbeat this long = Pi dead

// ---- Clock (NTP) ----
constexpr const char* TZ_INFO     = "IST-5:30";   // POSIX TZ string
constexpr const char* NTP_SERVER1 = "pool.ntp.org";
constexpr const char* NTP_SERVER2 = "time.google.com";

// ---- Device identity ----
#define DEVICE_LABEL "Garden Valve"

// ---- MQTT (broker = mosquitto on the Pi 4) ----
constexpr const char* MQTT_BROKER_HOST = "nilrasp4.local";
constexpr uint16_t    MQTT_BROKER_PORT = 1883;
constexpr const char* TOPIC_HEARTBEAT  = "irrigation/server/heartbeat";

// irrigation/<guid>/availability = 56 chars + NUL, so 64
extern char TOPIC_STATE[64];
extern char TOPIC_COMMAND[64];
extern char TOPIC_AVAIL[64];

// ---- Timings ----
constexpr unsigned long MQTT_RECONNECT_MIN = 1000UL;
constexpr unsigned long MQTT_RECONNECT_MAX = 30000UL;
constexpr unsigned long NET_TIMEOUT_MS     = 1000UL;  // DNS/TCP; keeps loop() (and valve timing) responsive
constexpr unsigned long OPEN_PUBLISH_MS    = 2000UL;  // state heartbeat while open (page countdown)

// ---- Wi-Fi first-boot setup portal / OTA ----
constexpr const char* WIFI_AP_NAME = "Irrigation-Setup";
constexpr const char* OTA_PASSWORD = "water1234";
```

- [ ] **Step 2: Write the failing test `firmware/test/test_valve.cpp`**

```cpp
#include "test_runner.h"
#include "valve.h"
#include "config.h"

void test_initial_closed() {
  Valve v;
  ASSERT_FALSE(v.isOpen);
  ASSERT_EQ_INT(0, v.remainingS(0));
}

void test_clamp() {
  ASSERT_EQ_INT(90, MAX_RUN_S);                       // spec: overflow above 90 s
  ASSERT_EQ_INT(DEFAULT_RUN_S, clampSeconds(0));
  ASSERT_EQ_INT(DEFAULT_RUN_S, clampSeconds(-5));
  ASSERT_EQ_INT(MAX_RUN_S, clampSeconds(91));
  ASSERT_EQ_INT(MAX_RUN_S, clampSeconds(100000L));
  ASSERT_EQ_INT(1, clampSeconds(1));
  ASSERT_EQ_INT(90, clampSeconds(90));
  Valve v;
  v.open(1800, 0);                                    // old "30 min" value must not get through
  ASSERT_EQ_INT(90000, v.durationMs);
  v.open(0, 0);
  ASSERT_EQ_INT(DEFAULT_RUN_S * 1000, v.durationMs);
}

void test_auto_close_at_deadline() {
  Valve v;
  v.open(45, 1000);
  ASSERT_FALSE(v.tick(1000 + 45000 - 1));
  ASSERT_TRUE(v.isOpen);
  ASSERT_TRUE(v.tick(1000 + 45000));
  ASSERT_FALSE(v.isOpen);
  ASSERT_FALSE(v.tick(1000 + 45001));   // already closed, no second event
}

void test_reopen_restarts() {
  Valve v;
  v.open(30, 0);
  v.open(30, 20000);
  ASSERT_FALSE(v.tick(45000));
  ASSERT_TRUE(v.tick(50000));
}

void test_close() {
  Valve v;
  ASSERT_FALSE(v.close());
  v.open(10, 0);
  ASSERT_TRUE(v.close());
  ASSERT_FALSE(v.isOpen);
  ASSERT_FALSE(v.tick(999999));
}

void test_remaining() {
  Valve v;
  v.open(45, 0);
  ASSERT_EQ_INT(45, v.remainingS(0));
  ASSERT_EQ_INT(45, v.remainingS(1));       // rounds up
  ASSERT_EQ_INT(1, v.remainingS(44001));
  ASSERT_EQ_INT(0, v.remainingS(45000));
}

void test_rollover() {
  Valve v;
  uint32_t start = 0xFFFFFFFFUL - 10000;
  v.open(30, start);
  ASSERT_FALSE(v.tick(start + 29999));      // wraps past 0
  ASSERT_TRUE(v.tick(start + 30000));
}

void test_parse_cmd() {
  ASSERT_TRUE(parseCmd("open") == Cmd::OPEN);
  ASSERT_TRUE(parseCmd("close") == Cmd::CLOSE);
  ASSERT_TRUE(parseCmd("schedule") == Cmd::NONE);   // schedule lives on the Pi now
  ASSERT_TRUE(parseCmd("OPEN") == Cmd::NONE);
  ASSERT_TRUE(parseCmd("") == Cmd::NONE);
  ASSERT_TRUE(parseCmd(nullptr) == Cmd::NONE);
}

int main() {
  test_initial_closed();
  test_clamp();
  test_auto_close_at_deadline();
  test_reopen_restarts();
  test_close();
  test_remaining();
  test_rollover();
  test_parse_cmd();
  return test_summary();
}
```

`firmware/test/Makefile` (recipe lines start with a TAB):

```make
# firmware/test/Makefile — host-side build for pure-logic modules
CXX      := g++
CXXFLAGS := -std=gnu++17 -O0 -g -Wall -Wextra -Wpedantic -I../irrigation_controller -I.
BUILD    := build
SRC      := ../irrigation_controller
HDRS     := test_runner.h $(SRC)/config.h $(SRC)/valve.h $(SRC)/fallback.h

SRCS_VALVE    := $(SRC)/valve.cpp test_valve.cpp
SRCS_FALLBACK := $(SRC)/fallback.cpp $(SRC)/valve.cpp test_fallback.cpp

.PHONY: test test-valve test-fallback clean

test: test-valve test-fallback

test-valve: $(BUILD)/test_valve
	./$<

test-fallback: $(BUILD)/test_fallback
	./$<

$(BUILD)/test_valve: $(SRCS_VALVE) $(HDRS) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(SRCS_VALVE) -o $@

$(BUILD)/test_fallback: $(SRCS_FALLBACK) $(HDRS) | $(BUILD)
	$(CXX) $(CXXFLAGS) $(SRCS_FALLBACK) -o $@

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `cd firmware/test && touch ../irrigation_controller/fallback.h && make test-valve`
Expected: compile error, `valve.h: No such file or directory`. The empty `fallback.h` only satisfies the Makefile's `HDRS` until Task 2.

- [ ] **Step 4: Write `valve.h` and `valve.cpp`**

`firmware/irrigation_controller/valve.h`:

```cpp
// firmware/irrigation_controller/valve.h — pure logic, no Arduino deps
#pragma once
#include <stdint.h>

enum class Cmd { NONE, OPEN, CLOSE };
Cmd parseCmd(const char* s);   // "open" / "close", anything else NONE

// <=0 -> DEFAULT_RUN_S, >MAX_RUN_S -> MAX_RUN_S
uint16_t clampSeconds(long seconds);

struct Valve {
  bool     isOpen     = false;
  uint32_t startMs    = 0;
  uint32_t durationMs = 0;

  void open(long seconds, uint32_t nowMs);   // (re)starts the run, seconds clamped
  bool close();                              // true if it was open
  bool tick(uint32_t nowMs);                 // true if it auto-closed on this call
  uint32_t remainingS(uint32_t nowMs) const;
};
```

`firmware/irrigation_controller/valve.cpp`:

```cpp
// firmware/irrigation_controller/valve.cpp
#include "valve.h"
#include "config.h"
#include <string.h>

Cmd parseCmd(const char* s) {
  if (!s) return Cmd::NONE;
  if (strcmp(s, "open") == 0)  return Cmd::OPEN;
  if (strcmp(s, "close") == 0) return Cmd::CLOSE;
  return Cmd::NONE;
}

uint16_t clampSeconds(long seconds) {
  if (seconds <= 0) return DEFAULT_RUN_S;
  if (seconds > MAX_RUN_S) return MAX_RUN_S;
  return (uint16_t)seconds;
}

void Valve::open(long seconds, uint32_t nowMs) {
  isOpen     = true;
  startMs    = nowMs;
  durationMs = (uint32_t)clampSeconds(seconds) * 1000UL;
}

bool Valve::close() {
  bool was = isOpen;
  isOpen = false;
  return was;
}

bool Valve::tick(uint32_t nowMs) {
  if (!isOpen || nowMs - startMs < durationMs) return false;  // unsigned: rollover-safe
  isOpen = false;
  return true;
}

uint32_t Valve::remainingS(uint32_t nowMs) const {
  if (!isOpen) return 0;
  uint32_t elapsed = nowMs - startMs;
  if (elapsed >= durationMs) return 0;
  return (durationMs - elapsed + 999) / 1000;
}
```

- [ ] **Step 5: Run it and confirm it passes**

Run: `cd firmware/test && make test-valve`
Expected: the runner reports every test passed (exit code 0), with no compiler warnings.

- [ ] **Step 6: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): valve timer in seconds with 90 s cap"
```

---

### Task 2: Firmware fallback rule, day record, state JSON

**Files:**
- Create (overwrite the empty stub): `firmware/irrigation_controller/fallback.h`, `fallback.cpp`
- Test: `firmware/test/test_fallback.cpp`

**Interfaces:**
- Consumes: `Valve`, constants from `config.h`.
- Produces:
  - Clock and day: `bool clockSynced(const tm&)`, `long dayKey(const tm&)`, `bool inWindow(const tm&, uint8_t h, uint8_t m)`.
  - Scheduling: `bool acceptScheduled(const tm&, long lastSchedDay)`, `bool fallbackDue(const tm&, bool piDead, long lastSchedDay)`.
  - `struct PiWatch { uint32_t lastSeenMs; bool dead; void seen(uint32_t); bool update(uint32_t); }`
  - EEPROM day record: `constexpr size_t DAY_BYTES = 5`, `void dayToBytes(long, uint8_t[5])`, `long dayFromBytes(const uint8_t[5])` (returns -1 when blank or corrupt).
  - `size_t stateJson(char*, size_t, const Valve&, uint32_t nowMs, bool synced, long lastSchedDay, bool fallback)`

- [ ] **Step 1: Write the failing test `firmware/test/test_fallback.cpp`**

```cpp
#include "test_runner.h"
#include "fallback.h"
#include "config.h"

static struct tm at(int year, int yday, int h, int m) {
  struct tm t = {};
  t.tm_year = year - 1900; t.tm_yday = yday; t.tm_hour = h; t.tm_min = m;
  return t;
}
static const int  Y = 2026, D = 286;          // 2026-10-14, tm_yday is 0-based
static const long TODAY = 2026L * 1000 + 286;

void test_clock_synced() {
  ASSERT_FALSE(clockSynced(at(1970, 0, 8, 0)));
  ASSERT_TRUE(clockSynced(at(2026, 0, 0, 0)));
}

void test_day_key() {
  ASSERT_EQ_INT(TODAY, dayKey(at(Y, D, 13, 0)));
  ASSERT_TRUE(dayKey(at(2026, 364, 23, 59)) < dayKey(at(2027, 0, 0, 0)));  // year boundary
}

void test_window() {
  ASSERT_FALSE(inWindow(at(Y, D, 7, 59), 8, 0));
  ASSERT_TRUE(inWindow(at(Y, D, 8, 0), 8, 0));
  ASSERT_TRUE(inWindow(at(Y, D, 21, 59), 8, 0));
  ASSERT_FALSE(inWindow(at(Y, D, 22, 0), 8, 0));
  ASSERT_FALSE(inWindow(at(Y, D, 23, 30), 8, 0));
  ASSERT_FALSE(inWindow(at(Y, D, 0, 5), 8, 0));
}

void test_accept_scheduled() {
  ASSERT_TRUE(acceptScheduled(at(Y, D, 8, 0), TODAY - 1));
  ASSERT_TRUE(acceptScheduled(at(Y, D, 8, 0), -1));
  ASSERT_FALSE(acceptScheduled(at(Y, D, 8, 0), TODAY));        // Pi retry after the run started
  ASSERT_FALSE(acceptScheduled(at(1970, 0, 8, 0), -1));        // ESP clock not synced yet
}

void test_fallback_due() {
  ASSERT_EQ_INT(8, FALLBACK_H);
  ASSERT_EQ_INT(0, FALLBACK_M);
  ASSERT_TRUE(fallbackDue(at(Y, D, 8, 0), true, TODAY - 1));
  ASSERT_TRUE(fallbackDue(at(Y, D, 15, 0), true, -1));         // power back mid-day: catch up
  ASSERT_FALSE(fallbackDue(at(Y, D, 8, 0), false, TODAY - 1)); // Pi alive: Pi owns the schedule
  ASSERT_FALSE(fallbackDue(at(Y, D, 8, 0), true, TODAY));      // already watered today
  ASSERT_FALSE(fallbackDue(at(Y, D, 7, 59), true, -1));
  ASSERT_FALSE(fallbackDue(at(Y, D, 22, 0), true, -1));
  ASSERT_FALSE(fallbackDue(at(1970, 0, 8, 0), true, -1));      // no NTP, no idea what time it is
}

void test_pi_watch() {
  PiWatch w;
  ASSERT_FALSE(w.update(PI_DEAD_AFTER_MS - 1));
  ASSERT_TRUE(w.update(PI_DEAD_AFTER_MS));          // no heartbeat since boot
  w.seen(PI_DEAD_AFTER_MS + 5);
  ASSERT_FALSE(w.dead);
  ASSERT_FALSE(w.update(PI_DEAD_AFTER_MS + 6));

  PiWatch r;
  r.seen(0xFFFFFFFFUL - 1000);
  ASSERT_TRUE(r.update(0xFFFFFFFFUL - 1000 + PI_DEAD_AFTER_MS));   // wraps past 0
  // ~49.7 days later millis() comes back near lastSeenMs; the latch must hold
  ASSERT_TRUE(r.update(r.lastSeenMs + 5));
}

void test_day_bytes() {
  uint8_t b[DAY_BYTES];
  dayToBytes(TODAY, b);
  ASSERT_EQ_INT(TODAY, dayFromBytes(b));
  uint8_t blank[DAY_BYTES] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
  ASSERT_EQ_INT(-1, dayFromBytes(blank));
  uint8_t oldSchedule[DAY_BYTES] = {0x5C, 1, 6, 0, 10};   // phase-1 schedule block at the same address
  ASSERT_EQ_INT(-1, dayFromBytes(oldSchedule));
}

void test_state_json() {
  Valve v;
  v.open(45, 0);
  char buf[200];
  ASSERT_TRUE(stateJson(buf, sizeof(buf), v, 1000, true, TODAY, false) > 0);
  ASSERT_CONTAINS(buf, "\"valve\":\"open\"");
  ASSERT_CONTAINS(buf, "\"remaining_s\":44");
  ASSERT_CONTAINS(buf, "\"max_s\":90");
  ASSERT_CONTAINS(buf, "\"synced\":true");
  ASSERT_CONTAINS(buf, "\"last_sched_day\":2026286");
  ASSERT_CONTAINS(buf, "\"fallback\":false");

  v.close();
  ASSERT_TRUE(stateJson(buf, sizeof(buf), v, 1000, false, -1, true) > 0);
  ASSERT_CONTAINS(buf, "\"valve\":\"closed\"");
  ASSERT_CONTAINS(buf, "\"remaining_s\":0");
  ASSERT_CONTAINS(buf, "\"synced\":false");
  ASSERT_CONTAINS(buf, "\"last_sched_day\":-1");
  ASSERT_CONTAINS(buf, "\"fallback\":true");

  char tiny[10];
  ASSERT_EQ_INT(0, stateJson(tiny, sizeof(tiny), v, 0, true, TODAY, false));
}

int main() {
  test_clock_synced();
  test_day_key();
  test_window();
  test_accept_scheduled();
  test_fallback_due();
  test_pi_watch();
  test_day_bytes();
  test_state_json();
  return test_summary();
}
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd firmware/test && make test-fallback`
Expected: compile errors such as `'clockSynced' was not declared` (the `fallback.h` stub is empty).

- [ ] **Step 3: Write `fallback.h` and `fallback.cpp`**

`firmware/irrigation_controller/fallback.h`:

```cpp
// firmware/irrigation_controller/fallback.h — pure logic, no Arduino deps
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <time.h>
#include "valve.h"

constexpr size_t DAY_BYTES = 5;

bool clockSynced(const struct tm& local);   // false until NTP has set a real date
long dayKey(const struct tm& local);        // year*1000 + tm_yday (0-based); the Pi computes the same
// start <= now < CUTOFF, same local day
bool inWindow(const struct tm& local, uint8_t startH, uint8_t startM);
// A scheduled open (Pi or fallback) runs only once per day and only with a real clock.
bool acceptScheduled(const struct tm& local, long lastSchedDay);
// ESP's own run: Pi dead, inside the FALLBACK..CUTOFF window, not yet run today.
bool fallbackDue(const struct tm& local, bool piDead, long lastSchedDay);

// Pi is "dead" after PI_DEAD_AFTER_MS without a server heartbeat. Latched until the next one,
// so millis() rollover can't make a long-dead Pi look alive.
struct PiWatch {
  uint32_t lastSeenMs = 0;   // boot counts as "seen"
  bool     dead       = false;
  void seen(uint32_t nowMs);
  bool update(uint32_t nowMs);
};

// EEPROM record of the last scheduled day: magic + uint32 LE. -1 if blank/corrupt.
void dayToBytes(long day, uint8_t out[DAY_BYTES]);
long dayFromBytes(const uint8_t in[DAY_BYTES]);

// State JSON for MQTT. Returns bytes written, 0 on overflow.
size_t stateJson(char* out, size_t cap, const Valve& v, uint32_t nowMs,
                 bool synced, long lastSchedDay, bool fallback);
```

`firmware/irrigation_controller/fallback.cpp`:

```cpp
// firmware/irrigation_controller/fallback.cpp
#include "fallback.h"
#include "config.h"
#include <stdio.h>

static constexpr uint8_t DAY_MAGIC = 0x5D;   // phase-1 schedule used 0x5C here, so old EEPROM reads as blank

bool clockSynced(const struct tm& l) { return l.tm_year + 1900 >= 2024; }

long dayKey(const struct tm& l) { return (long)(l.tm_year + 1900) * 1000 + l.tm_yday; }

bool inWindow(const struct tm& l, uint8_t startH, uint8_t startM) {
  int now = l.tm_hour * 60 + l.tm_min;
  return now >= startH * 60 + startM && now < CUTOFF_H * 60 + CUTOFF_M;
}

bool acceptScheduled(const struct tm& l, long lastSchedDay) {
  return clockSynced(l) && dayKey(l) != lastSchedDay;
}

bool fallbackDue(const struct tm& l, bool piDead, long lastSchedDay) {
  return piDead && acceptScheduled(l, lastSchedDay) && inWindow(l, FALLBACK_H, FALLBACK_M);
}

void PiWatch::seen(uint32_t nowMs) {
  lastSeenMs = nowMs;
  dead = false;
}

bool PiWatch::update(uint32_t nowMs) {
  if (!dead && nowMs - lastSeenMs >= PI_DEAD_AFTER_MS) dead = true;
  return dead;
}

void dayToBytes(long day, uint8_t out[DAY_BYTES]) {
  uint32_t d = (uint32_t)day;
  out[0] = DAY_MAGIC;
  for (int i = 0; i < 4; i++) out[1 + i] = (uint8_t)(d >> (8 * i));
}

long dayFromBytes(const uint8_t in[DAY_BYTES]) {
  if (in[0] != DAY_MAGIC) return -1;
  uint32_t d = 0;
  for (int i = 0; i < 4; i++) d |= (uint32_t)in[1 + i] << (8 * i);
  return (long)d;
}

size_t stateJson(char* out, size_t cap, const Valve& v, uint32_t nowMs,
                 bool synced, long lastSchedDay, bool fallback) {
  int n = snprintf(out, cap,
      "{\"valve\":\"%s\",\"remaining_s\":%lu,\"max_s\":%u,\"synced\":%s,"
      "\"last_sched_day\":%ld,\"fallback\":%s}",
      v.isOpen ? "open" : "closed", (unsigned long)v.remainingS(nowMs), (unsigned)MAX_RUN_S,
      synced ? "true" : "false", lastSchedDay, fallback ? "true" : "false");
  return (n > 0 && (size_t)n < cap) ? (size_t)n : 0;
}
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd firmware/test && make test`
Expected: both binaries report every test passed (exit code 0), with no compiler warnings.

- [ ] **Step 5: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): 08:00 fallback rule, Pi heartbeat watch, EEPROM day record"
```

---

### Task 3: Firmware network (MQTT-only) and sketch

These files depend on the Arduino core, so there are no host tests. Verify by compiling in the Arduino IDE, then by watching serial and MQTT on the device.

**Files:**
- Create: `firmware/irrigation_controller/network.h`, `network.cpp`, `irrigation_controller.ino`, `firmware/README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces:
  - `network::begin(const char* guid, const char* label, CommandHandler onCommand, Notify onHeartbeat)`
  - `network::tick()`, `network::connected()`, `network::publish(topic, payload, retained)`
  - `CommandHandler = void(*)(const char*)`, `Notify = void(*)()`

- [ ] **Step 1: Write `network.h`**

```cpp
// firmware/irrigation_controller/network.h
#pragma once

namespace network {
  using CommandHandler = void (*)(const char* payloadJson);
  using Notify         = void (*)();

  // Blocks until Wi-Fi is up (WiFiManager portal "Irrigation-Setup" on first boot).
  // MQTT connects and reconnects forever from tick().
  void begin(const char* guid, const char* label, CommandHandler onCommand, Notify onHeartbeat);
  void tick();
  bool connected();
  bool publish(const char* topic, const char* payload, bool retained);
}
```

- [ ] **Step 2: Write `network.cpp`**

```cpp
// firmware/irrigation_controller/network.cpp — Wi-Fi + MQTT + OTA.
// MQTT-only: the Pi is the only remote path (Tailscale), so there is no on-device web page.
#include "network.h"
#include "config.h"
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>
#include <ArduinoOTA.h>
#include <string.h>
#include <stdio.h>

char TOPIC_STATE[64];
char TOPIC_COMMAND[64];
char TOPIC_AVAIL[64];

namespace {
  network::CommandHandler g_onCommand   = nullptr;
  network::Notify         g_onHeartbeat = nullptr;

  char g_clientId[20];
  char g_onlinePayload[128];
  char g_offlinePayload[128];

  WiFiClient    wifiClient;
  PubSubClient  mqtt(wifiClient);
  unsigned long nextReconnect = 0;
  unsigned long backoff       = MQTT_RECONNECT_MIN;

  void mqttCallback(char* topic, byte* payload, unsigned int length) {
    if (strcmp(topic, TOPIC_HEARTBEAT) == 0) { if (g_onHeartbeat) g_onHeartbeat(); return; }
    if (strcmp(topic, TOPIC_COMMAND) != 0 || length >= 256) return;
    char buf[256];
    memcpy(buf, payload, length);
    buf[length] = '\0';
    if (g_onCommand) g_onCommand(buf);
  }

  bool connectMqtt() {
    IPAddress ip;
    // Resolve every attempt: the Pi's DHCP address can change while the ESP keeps running.
    if (!WiFi.hostByName(MQTT_BROKER_HOST, ip, NET_TIMEOUT_MS)) {
      Serial.printf("[net] DNS failed for %s\n", MQTT_BROKER_HOST);
      return false;
    }
    mqtt.setServer(ip, MQTT_BROKER_PORT);
    if (!mqtt.connect(g_clientId, nullptr, nullptr, TOPIC_AVAIL, 1, true, g_offlinePayload)) {
      Serial.printf("[net] mqtt connect failed rc=%d\n", mqtt.state());
      return false;
    }
    mqtt.publish(TOPIC_AVAIL, g_onlinePayload, true);
    mqtt.subscribe(TOPIC_COMMAND);
    mqtt.subscribe(TOPIC_HEARTBEAT);
    Serial.println("[net] mqtt connected");
    return true;
  }
}

namespace network {

void begin(const char* guid, const char* label, CommandHandler onCommand, Notify onHeartbeat) {
  g_onCommand   = onCommand;
  g_onHeartbeat = onHeartbeat;

  snprintf(TOPIC_STATE,   sizeof(TOPIC_STATE),   "irrigation/%s/state",        guid);
  snprintf(TOPIC_COMMAND, sizeof(TOPIC_COMMAND), "irrigation/%s/command",      guid);
  snprintf(TOPIC_AVAIL,   sizeof(TOPIC_AVAIL),   "irrigation/%s/availability", guid);
  snprintf(g_clientId,    sizeof(g_clientId),    "irr-%.8s", guid);
  snprintf(g_onlinePayload,  sizeof(g_onlinePayload),
           "{\"label\":\"%s\",\"guid\":\"%s\",\"status\":\"online\"}", label, guid);
  snprintf(g_offlinePayload, sizeof(g_offlinePayload),
           "{\"label\":\"%s\",\"guid\":\"%s\",\"status\":\"offline\"}", label, guid);

  WiFi.mode(WIFI_STA);
  WiFiManager wm;
  wm.setConfigPortalTimeout(180);
  if (!wm.autoConnect(WIFI_AP_NAME)) {
    Serial.println("[net] WiFi failed — restarting");
    delay(500);
    ESP.restart();
  }
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  Serial.printf("[net] wifi ok: %s\n", WiFi.localIP().toString().c_str());

  wifiClient.setTimeout(NET_TIMEOUT_MS);   // TCP connect to a dead Pi must not stall loop()
  mqtt.setSocketTimeout(1);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);                 // state JSON + topic exceeds PubSubClient's 256 default

  ArduinoOTA.setHostname(g_clientId);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  // Flashing stops loop() (and the auto-off timer) for ~30 s: close the valve first.
  ArduinoOTA.onStart([] { digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW); });
  ArduinoOTA.begin();
}

void tick() {
  ArduinoOTA.handle();
  if (mqtt.connected()) { mqtt.loop(); return; }
  unsigned long now = millis();
  if ((long)(now - nextReconnect) < 0) return;   // rollover-safe
  if (connectMqtt()) {
    backoff = MQTT_RECONNECT_MIN;
  } else {
    nextReconnect = now + backoff;
    backoff = min(backoff * 2, MQTT_RECONNECT_MAX);
  }
}

bool connected() { return mqtt.connected(); }

bool publish(const char* topic, const char* payload, bool retained) {
  if (!mqtt.connected()) return false;
  return mqtt.publish(topic, payload, retained);
}

} // namespace network
```

- [ ] **Step 3: Write `irrigation_controller.ino`**

```cpp
// firmware/irrigation_controller/irrigation_controller.ino
#include <Arduino.h>
#include <ArduinoJson.h>
#include <EEPROM.h>
#include <time.h>
#include "config.h"
#include "valve.h"
#include "fallback.h"
#include "network.h"

namespace {
  Valve    valve;
  PiWatch  pi;
  long     lastSchedDay = -1;
  bool     wasConnected = false;
  unsigned long lastPublish = 0;
  char g_guid[33];  // 32 hex chars + NUL

  // EEPROM layout: 0-31 GUID hex, 32 GUID marker 0xAB, 33-37 last scheduled day
  static constexpr int EEPROM_SIZE   = 33 + DAY_BYTES;
  static constexpr int EEPROM_MARKER = 32;
  static constexpr int EEPROM_DAY    = 33;
  static constexpr uint8_t MAGIC     = 0xAB;

  void setRelay(bool on) {
    bool level = RELAY_ACTIVE_LOW ? !on : on;
    digitalWrite(PIN_RELAY, level ? HIGH : LOW);
  }

  struct tm localNow() {
    time_t t = time(nullptr);
    struct tm l;
    localtime_r(&t, &l);
    return l;
  }

  void loadOrCreateGuid() {
    EEPROM.begin(EEPROM_SIZE);
    if (EEPROM.read(EEPROM_MARKER) == MAGIC) {
      for (int i = 0; i < 32; i++) g_guid[i] = (char)EEPROM.read(i);
      g_guid[32] = '\0';
      Serial.printf("[guid] loaded: %s\n", g_guid);
    } else {
      uint32_t id = ESP.getChipId();
      uint32_t r1 = random(0x10000000, 0x7FFFFFFF);
      uint32_t r2 = random(0x10000000, 0x7FFFFFFF);
      uint32_t r3 = random(0x10000000, 0x7FFFFFFF);
      snprintf(g_guid, sizeof(g_guid), "%08x%08x%08x%08x", id, r1, r2, r3);
      for (int i = 0; i < 32; i++) EEPROM.write(i, (uint8_t)g_guid[i]);
      EEPROM.write(EEPROM_MARKER, MAGIC);
      EEPROM.commit();
      Serial.printf("[guid] generated: %s\n", g_guid);
    }
    EEPROM.end();
  }

  void loadDay() {
    uint8_t b[DAY_BYTES];
    EEPROM.begin(EEPROM_SIZE);
    for (size_t i = 0; i < DAY_BYTES; i++) b[i] = EEPROM.read(EEPROM_DAY + i);
    EEPROM.end();
    lastSchedDay = dayFromBytes(b);
    Serial.printf("[sched] last scheduled day: %ld\n", lastSchedDay);
  }

  void saveDay(long day) {
    lastSchedDay = day;
    uint8_t b[DAY_BYTES];
    dayToBytes(day, b);
    EEPROM.begin(EEPROM_SIZE);
    for (size_t i = 0; i < DAY_BYTES; i++) EEPROM.write(EEPROM_DAY + i, b[i]);
    EEPROM.commit();
    EEPROM.end();
  }

  void publishState() {
    lastPublish = millis();
    char buf[200];
    stateJson(buf, sizeof(buf), valve, millis(), clockSynced(localNow()), lastSchedDay, pi.dead);
    network::publish(TOPIC_STATE, buf, /*retained=*/true);  // no-op unless MQTT connected
  }

  void startRun(long seconds, bool scheduled) {
    if (scheduled) {
      struct tm l = localNow();
      if (!acceptScheduled(l, lastSchedDay)) {
        Serial.println("[sched] ignored: clock unsynced or already ran today");
        return;
      }
      saveDay(dayKey(l));   // recorded at start: a power cut mid-run still counts as today's run
    }
    valve.open(seconds, millis());
    setRelay(true);
    Serial.printf("[valve] open %lu s%s\n", (unsigned long)(valve.durationMs / 1000),
                  scheduled ? " (scheduled)" : "");
    publishState();
  }

  void onCommand(const char* payloadJson) {
    JsonDocument doc;
    if (deserializeJson(doc, payloadJson)) { Serial.println("[cmd] bad json"); return; }
    const char* act = doc["action"] | "";
    switch (parseCmd(act)) {
      case Cmd::OPEN:
        startRun(doc["seconds"] | 0L, doc["scheduled"] | false);
        break;
      case Cmd::CLOSE:
        valve.close();
        setRelay(false);
        Serial.println("[valve] closed");
        publishState();
        break;
      default:
        Serial.printf("[cmd] unknown action: %s\n", act);
    }
  }

  void onHeartbeat() { pi.seen(millis()); }
}

void setup() {
  // Valve closed before anything else (pin is already HIGH via module pull-up).
  setRelay(false);
  pinMode(PIN_RELAY, OUTPUT);

  Serial.begin(115200);
  delay(50);
  Serial.println("\n[boot] irrigation controller starting");
  randomSeed(analogRead(A0) ^ (uint32_t)micros());
  loadOrCreateGuid();
  loadDay();
  network::begin(g_guid, DEVICE_LABEL, onCommand, onHeartbeat);   // blocks until Wi-Fi is up
  configTime(TZ_INFO, NTP_SERVER1, NTP_SERVER2);
}

void loop() {
  network::tick();
  uint32_t now = millis();

  if (fallbackDue(localNow(), pi.update(now), lastSchedDay)) {
    Serial.println("[fallback] no Pi heartbeat — running own 08:00 schedule");
    startRun(FALLBACK_S, /*scheduled=*/true);
  }

  if (valve.tick(now)) {
    setRelay(false);
    Serial.println("[valve] auto-closed");
    publishState();
  } else if (valve.isOpen && now - lastPublish >= OPEN_PUBLISH_MS) {
    publishState();
  }

  bool conn = network::connected();
  if (conn && !wasConnected) publishState();
  wasConnected = conn;

  yield();
}
```

- [ ] **Step 4: Write `firmware/README.md`**

````markdown
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
````

- [ ] **Step 5: Compile**

In the Arduino IDE, open `firmware/irrigation_controller/irrigation_controller.ino`, select **Generic ESP8266 Module**, then **Verify**.
Expected: `Done compiling` with no errors. If `hostByName(..., timeout)` is missing (ESP8266 core older than 3.0), update the core in Boards Manager.

- [ ] **Step 6: Commit**

```bash
git add firmware/
git commit -m "feat(firmware): MQTT-only network, heartbeat fallback, scheduled-run dedupe"
```

---

### Task 4: Server schedule rule

**Files:**
- Create: `server/schedule.py`
- Test: `server/test_schedule.py`

**Interfaces:**
- Produces:
  - Constants: `MAX_RUN_S = 90`, `DEFAULT_RUN_S = 60`, `CUTOFF = (22, 0)`, `DEFAULT_SCHEDULE = {'enabled': False, 'time': '08:00', 'seconds': 60}`
  - `day_key(dt) -> int`
  - `due(sched: dict, now: datetime, synced: bool, esp_last_day: int|None) -> bool`
  - `next_run(sched, now, ran_today: bool) -> str`
  - `validate_seconds(v) -> int` and `validate_schedule(d) -> dict`; both raise `ValueError(msg)`.

- [ ] **Step 1: Write the failing test `server/test_schedule.py`**

```python
#!/usr/bin/env python3
"""Tests for the pure schedule rule."""
import unittest
from datetime import datetime

import schedule as s

TODAY = 2026286          # 2026-10-14, matches the ESP's year*1000 + 0-based tm_yday
ON = {'enabled': True, 'time': '08:00', 'seconds': 60}


def at(h, m):
    return datetime(2026, 10, 14, h, m)


class TestDayKey(unittest.TestCase):
    def test_day_key_matches_esp(self):
        self.assertEqual(s.day_key(at(13, 0)), TODAY)
        self.assertEqual(s.day_key(datetime(2026, 1, 1)), 2026000)
        self.assertEqual(s.day_key(datetime(2026, 12, 31)), 2026364)


class TestDue(unittest.TestCase):
    def test_fires_at_start_time(self):
        self.assertTrue(s.due(ON, at(8, 0), True, TODAY - 1))

    def test_catch_up_same_day_before_cutoff(self):
        self.assertTrue(s.due(ON, at(21, 59), True, TODAY - 1))
        self.assertTrue(s.due(ON, at(15, 0), True, -1))

    def test_dropped_at_or_after_cutoff(self):
        self.assertFalse(s.due(ON, at(22, 0), True, TODAY - 1))
        self.assertFalse(s.due(ON, at(23, 59), True, TODAY - 1))

    def test_not_before_start(self):
        self.assertFalse(s.due(ON, at(7, 59), True, TODAY - 1))

    def test_not_twice(self):
        self.assertFalse(s.due(ON, at(9, 0), True, TODAY))

    def test_needs_ntp_and_esp_state_and_enabled(self):
        self.assertFalse(s.due(ON, at(8, 0), False, TODAY - 1))
        self.assertFalse(s.due(ON, at(8, 0), True, None))
        self.assertFalse(s.due({**ON, 'enabled': False}, at(8, 0), True, TODAY - 1))


class TestNextRun(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(s.next_run({**ON, 'enabled': False}, at(7, 0), False), 'Schedule off')
        self.assertEqual(s.next_run(ON, at(7, 0), False), 'today 08:00')
        self.assertEqual(s.next_run(ON, at(9, 0), False), 'now (catching up)')
        self.assertEqual(s.next_run(ON, at(9, 0), True), 'tomorrow 08:00')
        self.assertEqual(s.next_run(ON, at(22, 30), False), 'tomorrow 08:00')


class TestValidation(unittest.TestCase):
    def test_validate_seconds(self):
        for ok in (1, 45, 90):
            self.assertEqual(s.validate_seconds(ok), ok)
        for bad in (0, 91, -1, True, '30', None, 30.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                s.validate_seconds(bad)

    def test_validate_schedule(self):
        self.assertEqual(s.validate_schedule({'enabled': True, 'time': '21:59', 'seconds': 90}),
                         {'enabled': True, 'time': '21:59', 'seconds': 90})
        bad = [
            None, [], {},
            {'enabled': 'yes', 'time': '08:00', 'seconds': 60},
            {'enabled': True, 'time': '22:00', 'seconds': 60},   # cutoff
            {'enabled': True, 'time': '8:00', 'seconds': 60},
            {'enabled': True, 'time': '24:00', 'seconds': 60},
            {'enabled': True, 'time': '08:00', 'seconds': 91},
        ]
        for d in bad:
            with self.assertRaises(ValueError, msg=repr(d)):
                s.validate_schedule(d)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd server && python3 -m unittest test_schedule -v`
Expected: `ModuleNotFoundError: No module named 'schedule'`.

- [ ] **Step 3: Write `server/schedule.py`**

```python
"""Schedule rule shared with the ESP firmware. Pure: no I/O, testable off-Pi.

Run if enabled, clock synced, start <= now < 22:00 (same local day) and the ESP hasn't
recorded a scheduled run today. That one rule covers on-time runs and power-cut catch-up.
"""
import re

MAX_RUN_S = 90                 # more than this overflows; the ESP clamps to the same value
DEFAULT_RUN_S = 60
CUTOFF = (22, 0)               # missed runs are dropped from 22:00
DEFAULT_SCHEDULE = {'enabled': False, 'time': '08:00', 'seconds': DEFAULT_RUN_S}
_TIME = re.compile(r'([01]\d|2[0-3]):[0-5]\d')


def day_key(dt):
    """Same value the ESP stores: year*1000 + C tm_yday (0-based; Python's is 1-based)."""
    return dt.year * 1000 + dt.timetuple().tm_yday - 1


def _hm(text):
    return int(text[:2]), int(text[3:])


def due(sched, now, synced, esp_last_day):
    if not sched['enabled'] or not synced or esp_last_day is None:
        return False
    return (_hm(sched['time']) <= (now.hour, now.minute) < CUTOFF
            and esp_last_day != day_key(now))


def next_run(sched, now, ran_today):
    if not sched['enabled']:
        return 'Schedule off'
    t = sched['time']
    if ran_today or (now.hour, now.minute) >= CUTOFF:
        return f'tomorrow {t}'
    if (now.hour, now.minute) < _hm(t):
        return f'today {t}'
    return 'now (catching up)'


def validate_seconds(v):
    # type() not isinstance(): bool is an int subclass and True must not mean 1 s
    if type(v) is not int or not 1 <= v <= MAX_RUN_S:
        raise ValueError(f'seconds must be a whole number 1..{MAX_RUN_S}')
    return v


def validate_schedule(d):
    if not isinstance(d, dict) or type(d.get('enabled')) is not bool:
        raise ValueError('enabled must be true or false')
    t = d.get('time')
    if not isinstance(t, str) or not _TIME.fullmatch(t):
        raise ValueError('time must be HH:MM')
    if _hm(t) >= CUTOFF:
        raise ValueError('start time must be before 22:00')
    return {'enabled': d['enabled'], 'time': t, 'seconds': validate_seconds(d.get('seconds'))}
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd server && python3 -m unittest test_schedule -v`
Expected: all 11 tests `ok`.

- [ ] **Step 5: Commit**

```bash
git add server/schedule.py server/test_schedule.py
git commit -m "feat(server): schedule rule with 22:00 catch-up cutoff"
```

---

### Task 5: Atomic state store

**Files:**
- Create: `server/state_store.py`
- Test: `server/test_state_store.py`

**Interfaces:**
- Consumes: `schedule.DEFAULT_SCHEDULE`, `schedule.validate_schedule`
- Produces: `defaults() -> dict` (`{'schedule': {...}, 'guid': None, 'last_run': None}`), `load(path) -> dict`, `save(path, data) -> None`

- [ ] **Step 1: Write the failing test `server/test_state_store.py`**

```python
#!/usr/bin/env python3
"""Tests for the atomic state file."""
import os
import tempfile
import unittest

import state_store as st


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'sub', 'state.json')   # parent dir doesn't exist yet

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_gives_defaults(self):
        d = st.load(self.path)
        self.assertEqual(d, {'schedule': {'enabled': False, 'time': '08:00', 'seconds': 60},
                             'guid': None, 'last_run': None})

    def test_defaults_are_fresh_copies(self):
        st.load(self.path)['schedule']['enabled'] = True
        self.assertFalse(st.load(self.path)['schedule']['enabled'])

    def test_round_trip(self):
        d = st.defaults()
        d['schedule'] = {'enabled': True, 'time': '07:30', 'seconds': 45}
        d['guid'] = 'a' * 32
        d['last_run'] = '2026-10-14 08:00'
        st.save(self.path, d)
        self.assertEqual(st.load(self.path), d)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ['state.json'])   # no temp left

    def test_corrupt_or_invalid_gives_defaults(self):
        os.makedirs(os.path.dirname(self.path))
        for junk in ('{"schedule": {"enab', '[1, 2]', '{}',
                     '{"schedule": {"enabled": true, "time": "23:00", "seconds": 60}}'):
            with open(self.path, 'w') as f:
                f.write(junk)
            self.assertEqual(st.load(self.path), st.defaults(), junk)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd server && python3 -m unittest test_state_store -v`
Expected: `ModuleNotFoundError: No module named 'state_store'`.

- [ ] **Step 3: Write `server/state_store.py`**

```python
"""state.json: schedule, ESP GUID, last scheduled run. Survives power cuts.

save() writes a temp file, fsyncs it, renames it over the old one and fsyncs the
directory, so a cut leaves either the old or the new file, never a torn one.
"""
import json
import logging
import os
import tempfile

from schedule import DEFAULT_SCHEDULE, validate_schedule

log = logging.getLogger(__name__)


def defaults():
    return {'schedule': dict(DEFAULT_SCHEDULE), 'guid': None, 'last_run': None}


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        data['schedule'] = validate_schedule(data['schedule'])
    except FileNotFoundError:
        return defaults()
    except (ValueError, KeyError, TypeError) as e:     # JSONDecodeError is a ValueError
        log.warning('state file %s unusable (%s), using defaults', path, e)
        return defaults()
    out = defaults()
    out.update({k: data.get(k, v) for k, v in out.items()})
    return out


def save(path, data):
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)          # make the rename itself durable
    finally:
        os.close(dfd)
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd server && python3 -m unittest test_state_store -v`
Expected: 4 tests `ok`. The corrupt-file test logs warnings; that's expected.

- [ ] **Step 5: Commit**

```bash
git add server/state_store.py server/test_state_store.py
git commit -m "feat(server): power-cut-safe state file"
```

---

### Task 6: On-demand camera

**Files:**
- Create: `server/camera.py`
- Test: `server/test_camera.py`

**Interfaces:**
- Produces:
  - `FrameBuffer` (`.frame`, `.condition`, `.write(buf)`)
  - `Camera(start, stop)` (`.acquire()`, `.release()`, `.viewers`)
  - `picamera(frames) -> (start, stop)`

- [ ] **Step 1: Write the failing test `server/test_camera.py`**

```python
#!/usr/bin/env python3
"""Camera runs only while someone watches. No picamera2 needed."""
import unittest

from camera import Camera, FrameBuffer


class TestCamera(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.cam = Camera(lambda: self.calls.append('start'), lambda: self.calls.append('stop'))

    def test_first_viewer_starts_last_viewer_stops(self):
        self.cam.acquire()
        self.cam.acquire()
        self.assertEqual(self.calls, ['start'])
        self.cam.release()
        self.assertEqual(self.calls, ['start'])
        self.cam.release()
        self.assertEqual(self.calls, ['start', 'stop'])
        self.assertEqual(self.cam.viewers, 0)

    def test_restart_after_stop(self):
        self.cam.acquire(); self.cam.release(); self.cam.acquire()
        self.assertEqual(self.calls, ['start', 'stop', 'start'])

    def test_failed_start_leaves_no_viewer(self):
        def boom():
            raise RuntimeError('camera busy')
        cam = Camera(boom, lambda: self.calls.append('stop'))
        with self.assertRaises(RuntimeError):
            cam.acquire()
        self.assertEqual(cam.viewers, 0)


class TestFrameBuffer(unittest.TestCase):
    def test_write_keeps_latest(self):
        fb = FrameBuffer()
        fb.write(b'a'); fb.write(b'b')
        self.assertEqual(fb.frame, b'b')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd server && python3 -m unittest test_camera -v`
Expected: `ModuleNotFoundError: No module named 'camera'`.

- [ ] **Step 3: Write `server/camera.py`**

```python
"""On-demand Pi camera: opened for the first viewer, closed after the last one leaves,
so rpicam-* commands can use the camera whenever nobody is watching.
ponytail: one shared encoder fanned out to all viewers; fine for a handful of clients.
"""
import io
import threading

SIZE = (640, 480)
FLIP = False          # True if the camera is mounted upside down


class FrameBuffer(io.BufferedIOBase):
    """Holds the latest JPEG frame; wakes every viewer when a new one arrives."""

    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class Camera:
    """Viewer refcount around a start/stop pair."""

    def __init__(self, start, stop):
        self._start, self._stop = start, stop
        self._lock = threading.Lock()
        self.viewers = 0

    def acquire(self):
        with self._lock:
            if self.viewers == 0:
                self._start()          # raises -> viewers stays 0
            self.viewers += 1

    def release(self):
        with self._lock:
            self.viewers -= 1
            if self.viewers == 0:
                self._stop()


def picamera(frames):
    """(start, stop) for the real camera. Imports live here so tests run off-Pi."""
    held = {}

    def start():
        from libcamera import Transform
        from picamera2 import Picamera2
        from picamera2.encoders import MJPEGEncoder
        from picamera2.outputs import FileOutput
        cam = Picamera2()
        try:
            cam.configure(cam.create_video_configuration(
                main={'size': SIZE}, transform=Transform(hflip=FLIP, vflip=FLIP)))
            cam.start_recording(MJPEGEncoder(), FileOutput(frames))
        except Exception:
            cam.close()                # don't keep the camera locked after a failed start
            raise
        held['cam'] = cam

    def stop():
        cam = held.pop('cam')
        cam.stop_recording()
        cam.close()

    return start, stop
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd server && python3 -m unittest test_camera -v`
Expected: 4 tests `ok`.

- [ ] **Step 5: Commit**

```bash
git add server/camera.py server/test_camera.py
git commit -m "feat(server): on-demand camera with viewer refcount"
```

---

### Task 7: App logic (MQTT in/out, API, scheduler)

**Files:**
- Create: `server/irrigation_server.py`. This task writes the module docstring, constants, `check_auth`, `ntp_synced` and `App`. Task 8 adds `Handler` and `main`.
- Test: `server/test_app.py`

**Interfaces:**
- Consumes: `schedule.*`, `state_store.load/save`
- Produces:
  - `check_auth(header, user, password) -> bool`
  - `App(store_path, publish, clock=datetime.now, synced=ntp_synced, mono=time.monotonic)`, where `publish(topic: str, payload: str)`
  - `App.on_message(topic: str, payload: bytes)`, `App.on_broker_lost()`, `App.tick()`
  - API methods returning `(status: int, body: dict)`: `App.open(body)`, `App.close(body=None)`, `App.set_schedule(body)`
  - `App.state() -> dict`, `App.data`, `App.esp_online`
  - Module constants: `TOPIC_HEARTBEAT`, `HEARTBEAT_S = 60`, `RETRY_S = 10`, `TICK_S = 5`

- [ ] **Step 1: Write the failing test `server/test_app.py`**

```python
#!/usr/bin/env python3
"""App logic without HTTP, MQTT or a camera."""
import base64
import json
import os
import tempfile
import unittest
from datetime import datetime

import irrigation_server as srv
import state_store

G = 'a' * 32
CMD = f'irrigation/{G}/command'
TODAY = 2026286     # 2026-10-14


class Rig:
    def __init__(self, now=datetime(2026, 10, 14, 8, 0), synced=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'state.json')
        self.now, self.synced, self.mono = now, synced, 0.0
        self.sent = []
        self.app = srv.App(self.path, lambda t, p: self.sent.append((t, json.loads(p))),
                           clock=lambda: self.now, synced=lambda: self.synced,
                           mono=lambda: self.mono)

    def esp(self, last_day=TODAY - 1, valve='closed', guid=G):
        self.app.on_message(f'irrigation/{guid}/availability', b'{"status":"online"}')
        self.app.on_message(f'irrigation/{guid}/state', json.dumps(
            {'valve': valve, 'remaining_s': 0, 'max_s': 90, 'synced': True,
             'last_sched_day': last_day, 'fallback': False}).encode())

    def commands(self):
        return [p for t, p in self.sent if t == CMD]

    def schedule_on(self, time='08:00', seconds=60):
        self.assertok(self.app.set_schedule({'enabled': True, 'time': time, 'seconds': seconds}))

    @staticmethod
    def assertok(result):
        assert result[0] == 200, result


class TestAuth(unittest.TestCase):
    U, P = 'nilrasp', 'k7#Qv9!xLp2wZr'

    def basic(self, s):
        return 'Basic ' + base64.b64encode(s.encode()).decode()

    def test_check_auth(self):
        self.assertTrue(srv.check_auth(self.basic(f'{self.U}:{self.P}'), self.U, self.P))
        self.assertFalse(srv.check_auth(self.basic(f'{self.U}:nope'), self.U, self.P))
        for h in (None, '', 'Bearer x', 'Basic', 'Basic !!!'):
            self.assertFalse(srv.check_auth(h, self.U, self.P), h)


class TestEsp(unittest.TestCase):
    def test_learns_guid_from_availability(self):
        r = Rig(); r.esp()
        self.assertEqual(r.app.data['guid'], G)
        self.assertTrue(r.app.esp_online)
        self.assertEqual(state_store.load(r.path)['guid'], G)

    def test_learns_guid_from_state_first(self):
        r = Rig()
        r.app.on_message(f'irrigation/{G}/state', b'{"valve":"closed","last_sched_day":5}')
        self.assertEqual(r.app.data['guid'], G)
        self.assertEqual(r.app.esp['last_sched_day'], 5)

    def test_ignores_second_esp(self):
        r = Rig(); r.esp()
        r.app.on_message(f'irrigation/{"b" * 32}/availability', b'{"status":"offline"}')
        self.assertTrue(r.app.esp_online)

    def test_offline_and_broker_lost(self):
        r = Rig(); r.esp()
        r.app.on_message(f'irrigation/{G}/availability', b'{"status":"offline"}')
        self.assertFalse(r.app.esp_online)
        r.esp(); r.app.on_broker_lost()
        self.assertFalse(r.app.esp_online)

    def test_bad_payloads_ignored(self):
        r = Rig()
        for topic, payload in ((f'irrigation/{G}/state', b'not json'),
                               (f'irrigation/{G}/state', b'[1]'),
                               ('foo', b'{}'),
                               ('irrigation/server/heartbeat', b'1')):
            r.app.on_message(topic, payload)
        self.assertIsNone(r.app.data['guid'])


class TestApi(unittest.TestCase):
    def test_open_validates(self):
        r = Rig(); r.esp()
        for body in ({'seconds': 91}, {'seconds': 0}, {'seconds': True}, {'seconds': '30'},
                     {'seconds': None}, {}, None, [45]):
            status, out = r.app.open(body)
            self.assertEqual(status, 400, body)
            self.assertIn('error', out)
        self.assertEqual(r.commands(), [])
        self.assertEqual(r.app.open({'seconds': 45})[0], 200)
        self.assertEqual(r.commands(), [{'action': 'open', 'seconds': 45}])   # never 'scheduled'

    def test_open_close_offline_503(self):
        r = Rig()
        self.assertEqual(r.app.open({'seconds': 30})[0], 503)
        self.assertEqual(r.app.close()[0], 503)
        self.assertEqual(r.sent, [])

    def test_close(self):
        r = Rig(); r.esp()
        self.assertEqual(r.app.close({})[0], 200)
        self.assertEqual(r.commands(), [{'action': 'close'}])

    def test_set_schedule(self):
        r = Rig()
        self.assertEqual(r.app.set_schedule({'enabled': True, 'time': '22:00', 'seconds': 60})[0], 400)
        self.assertEqual(r.app.set_schedule({'enabled': True, 'time': '07:15', 'seconds': 45})[0], 200)
        self.assertEqual(state_store.load(r.path)['schedule'],
                         {'enabled': True, 'time': '07:15', 'seconds': 45})

    def test_state_view(self):
        r = Rig(now=datetime(2026, 10, 14, 7, 0)); r.esp(); r.schedule_on()
        r.app.tick()
        v = r.app.state()
        self.assertEqual(v['esp_online'], True)
        self.assertEqual(v['valve'], 'closed')
        self.assertEqual(v['max_s'], 90)
        self.assertEqual(v['synced'], True)
        self.assertEqual(v['clock'], '07:00')
        self.assertEqual(v['next_run'], 'today 08:00')
        self.assertEqual(v['schedule'], {'enabled': True, 'time': '08:00', 'seconds': 60})
        self.assertIsNone(v['last_run'])
        self.assertFalse(v['fallback'])


class TestScheduler(unittest.TestCase):
    SCHED = {'action': 'open', 'seconds': 60, 'scheduled': True}

    def test_fires_at_time(self):
        r = Rig(); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [self.SCHED])

    def test_catch_up_after_power_cut(self):
        r = Rig(now=datetime(2026, 10, 14, 15, 30)); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [self.SCHED])

    def test_dropped_after_cutoff(self):
        r = Rig(now=datetime(2026, 10, 14, 22, 0)); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [])

    def test_skips_when_esp_already_ran_today(self):
        r = Rig(); r.esp(last_day=TODAY); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [])

    def test_waits_for_ntp_and_esp(self):
        r = Rig(synced=False); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [])
        r2 = Rig(); r2.schedule_on()
        r2.app.on_message(f'irrigation/{G}/availability', b'{"status":"online"}')   # no state yet
        r2.app.tick()
        self.assertEqual(r2.commands(), [])

    def test_retry_spacing(self):
        r = Rig(); r.esp(); r.schedule_on()
        r.app.tick()                          # mono 0: send
        r.mono = 5; r.app.tick()              # too soon
        r.mono = 10; r.app.tick()             # ESP didn't confirm: resend (ESP dedupes)
        self.assertEqual(len(r.commands()), 2)
        r.esp(last_day=TODAY, valve='open')   # confirmed
        r.mono = 20; r.app.tick()
        self.assertEqual(len(r.commands()), 2)

    def test_last_run_recorded(self):
        r = Rig(); r.esp(); r.schedule_on()
        r.esp(last_day=TODAY, valve='open')
        self.assertEqual(r.app.data['last_run'], '2026-10-14 08:00')
        self.assertEqual(state_store.load(r.path)['last_run'], '2026-10-14 08:00')
        r.now = datetime(2026, 10, 14, 8, 1)
        r.esp(last_day=TODAY, valve='closed')
        self.assertEqual(r.app.data['last_run'], '2026-10-14 08:00')   # not bumped by later states

    def test_heartbeat(self):
        r = Rig()
        r.app.tick()
        r.mono = 30; r.app.tick()
        r.mono = 60; r.app.tick()
        beats = [t for t, _ in r.sent if t == srv.TOPIC_HEARTBEAT]
        self.assertEqual(len(beats), 2)


class TestStartupGuard(unittest.TestCase):
    def run_main(self, env):
        old = {k: os.environ.pop(k, None) for k in ('IRRIGATION_USER', 'IRRIGATION_PASS')}
        os.environ.update(env)
        try:
            with self.assertRaises(SystemExit):
                srv.main()
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_refuses_unset_short_or_missing_user(self):
        self.run_main({})
        self.run_main({'IRRIGATION_USER': 'nilrasp', 'IRRIGATION_PASS': 'short'})
        self.run_main({'IRRIGATION_PASS': 'a-long-enough-password'})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd server && python3 -m unittest test_app -v`
Expected: `ModuleNotFoundError: No module named 'irrigation_server'`.

- [ ] **Step 3: Write `server/irrigation_server.py` (App part)**

```python
#!/usr/bin/env python3
"""Irrigation on the Pi 4: page + JSON API + on-demand camera + scheduler on :8000.

Talks to the ESP-01S valve over MQTT on the local mosquitto. HTTP Basic auth on every
route; creds from env IRRIGATION_USER / IRRIGATION_PASS (systemd loads ~/.irrigation.env).
Plain HTTP is fine over Tailscale (WireGuard encrypts it).
"""
import base64
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import server
from pathlib import Path

import schedule
import state_store
from camera import Camera, FrameBuffer, picamera

PORT = 8000
MQTT_HOST, MQTT_PORT = 'localhost', 1883
STATE_PATH = os.path.expanduser('~/.local/share/irrigation/state.json')
PAGE = Path(__file__).parent / 'static' / 'index.html'
TOPIC_HEARTBEAT = 'irrigation/server/heartbeat'
TICK_S = 5            # scheduler period; catch-up starts within seconds of NTP sync
RETRY_S = 10          # resend a scheduled open until the ESP confirms it
HEARTBEAT_S = 60      # ESP falls back to its own 08:00 run after 30 min without one
MIN_PASS_LEN = 12
FAIL_DELAY_S = 1.0    # slows password guessing

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [irrigation] %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
log = logging.getLogger(__name__)


def check_auth(header, user, password):
    """True if an Authorization header carries exactly user:password (constant-time)."""
    if not header or not header.startswith('Basic '):
        return False
    try:
        given = base64.b64decode(header[6:], validate=True)
    except ValueError:
        return False
    return hmac.compare_digest(given, f'{user}:{password}'.encode())


def ntp_synced():
    try:
        out = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return out.strip() == 'yes'


class App:
    """All irrigation logic. HTTP and MQTT are thin shells around it."""

    def __init__(self, store_path, publish, clock=datetime.now, synced=ntp_synced,
                 mono=time.monotonic):
        self.path = store_path
        self.data = state_store.load(store_path)
        self.publish = publish            # publish(topic, payload_str), never retained
        self.now, self.synced, self.mono = clock, synced, mono
        self.is_synced = False
        self.esp = {}                     # latest ESP state message
        self.esp_online = False
        self.last_attempt = self.last_beat = float('-inf')
        self.lock = threading.Lock()

    def _save(self):
        state_store.save(self.path, self.data)

    def _topic(self, kind):
        return f"irrigation/{self.data['guid']}/{kind}"

    # ---- MQTT in ----
    def on_message(self, topic, payload):
        parts = topic.split('/')
        if len(parts) != 3 or parts[0] != 'irrigation' or parts[1] == 'server':
            return
        guid, kind = parts[1], parts[2]
        try:
            msg = json.loads(payload)
        except ValueError:
            log.warning('bad JSON on %s', topic)
            return
        if not isinstance(msg, dict):
            return
        with self.lock:
            if self.data['guid'] is None:
                self.data['guid'] = guid
                self._save()
                log.info('paired with ESP %s', guid)
            if guid != self.data['guid']:
                log.warning('ignoring unknown ESP %s', guid)
                return
            if kind == 'availability':
                self.esp_online = msg.get('status') == 'online'
            elif kind == 'state':
                self.esp = msg
                self._note_run()

    def _note_run(self):
        now = self.now()
        today = now.strftime('%Y-%m-%d')
        if (self.esp.get('last_sched_day') == schedule.day_key(now)
                and not (self.data['last_run'] or '').startswith(today)):
            self.data['last_run'] = now.strftime('%Y-%m-%d %H:%M')
            self._save()

    def on_broker_lost(self):
        with self.lock:
            self.esp_online = False

    # ---- API: each returns (status, body) ----
    def open(self, body):
        try:
            secs = schedule.validate_seconds(body.get('seconds') if isinstance(body, dict) else None)
        except ValueError as e:
            return 400, {'error': str(e)}
        return self._command({'action': 'open', 'seconds': secs})

    def close(self, body=None):
        return self._command({'action': 'close'})

    def _command(self, cmd):
        with self.lock:
            if not self.esp_online or not self.data['guid']:
                return 503, {'error': 'valve offline'}
            self.publish(self._topic('command'), json.dumps(cmd))
        return 200, {'ok': True}

    def set_schedule(self, body):
        try:
            s = schedule.validate_schedule(body)
        except ValueError as e:
            return 400, {'error': str(e)}
        with self.lock:
            self.data['schedule'] = s
            self._save()
        log.info('schedule saved: %s', s)
        return 200, {'ok': True}

    def state(self):
        with self.lock:
            now, s, e = self.now(), self.data['schedule'], self.esp
            ran = e.get('last_sched_day') == schedule.day_key(now)
            return {
                'esp_online': self.esp_online,
                'valve': e.get('valve', 'closed'),
                'remaining_s': e.get('remaining_s', 0),
                'max_s': schedule.MAX_RUN_S,
                'fallback': bool(e.get('fallback', False)),
                'synced': self.is_synced,
                'clock': now.strftime('%H:%M'),
                'schedule': dict(s),
                'last_run': self.data['last_run'],
                'next_run': schedule.next_run(s, now, ran),
            }

    # ---- scheduler, every TICK_S ----
    def tick(self):
        self.is_synced = self.synced()
        mono = self.mono()
        if mono - self.last_beat >= HEARTBEAT_S:
            self.last_beat = mono
            self.publish(TOPIC_HEARTBEAT, '1')
        with self.lock:
            s = self.data['schedule']
            if not self.esp_online or not schedule.due(
                    s, self.now(), self.is_synced, self.esp.get('last_sched_day')):
                return
            if mono - self.last_attempt < RETRY_S:
                return
            self.last_attempt = mono
            log.info('scheduled run: %d s', s['seconds'])
            self.publish(self._topic('command'),
                         json.dumps({'action': 'open', 'seconds': s['seconds'], 'scheduled': True}))


def main():
    user = os.environ.get('IRRIGATION_USER', '')
    password = os.environ.get('IRRIGATION_PASS', '')
    if not user or len(password) < MIN_PASS_LEN:
        sys.exit(f'set IRRIGATION_USER and IRRIGATION_PASS (>= {MIN_PASS_LEN} chars), see ~/.irrigation.env')
    raise NotImplementedError('wired in Task 8')


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd server && python3 -m unittest test_app -v`
Expected: all tests `ok`.

- [ ] **Step 5: Commit**

```bash
git add server/irrigation_server.py server/test_app.py
git commit -m "feat(server): app logic, scheduler with ESP-confirmed catch-up, heartbeat"
```

---

### Task 8: HTTP handler and wiring

**Files:**
- Modify: `server/irrigation_server.py`. Add `Handler` above `main`, and replace the `raise NotImplementedError` line in `main`.
- Create: `server/static/index.html` (placeholder `<!DOCTYPE html><title>Irrigation</title>` until Task 9)
- Test: `server/test_http.py`

**Interfaces:**
- Consumes: `App` methods from Task 7, `Camera`/`FrameBuffer`/`picamera` from Task 6.
- Produces:
  - `Handler(server.BaseHTTPRequestHandler)` with class attributes `app`, `camera`, `frames`, `user`, `password`.
  - Routes: `GET /`, `GET /api/state`, `GET /stream.mjpg`, `POST /api/open`, `POST /api/close`, `PUT /api/schedule`.

- [ ] **Step 1: Write the failing test `server/test_http.py`**

```python
#!/usr/bin/env python3
"""HTTP shell over a fake App: auth, routing, JSON-only writes, camera failure."""
import base64
import http.client
import json
import threading
import unittest
from http import server

import irrigation_server as srv

U, P = 'nilrasp', 'k7#Qv9!xLp2wZr'
AUTH = 'Basic ' + base64.b64encode(f'{U}:{P}'.encode()).decode()


class FakeApp:
    def __init__(self):
        self.calls = []

    def state(self):
        return {'valve': 'closed'}

    def open(self, body):
        self.calls.append(('open', body)); return 200, {'ok': True}

    def close(self, body=None):
        self.calls.append(('close', body)); return 503, {'error': 'valve offline'}

    def set_schedule(self, body):
        self.calls.append(('schedule', body)); return 400, {'error': 'bad'}


class BrokenCamera:
    def acquire(self):
        raise RuntimeError('no camera')

    def release(self):
        raise AssertionError('release without acquire')


class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv.FAIL_DELAY_S = 0
        srv.Handler.user, srv.Handler.password = U, P
        srv.Handler.camera = BrokenCamera()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        srv.Handler.app = self.app = FakeApp()

    def req(self, method, path, body=None, auth=AUTH, ctype='application/json'):
        c = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=5)
        headers = {'Authorization': auth} if auth else {}
        if body is not None:
            headers['Content-Type'] = ctype
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_auth_required_everywhere(self):
        for m, p in (('GET', '/'), ('GET', '/api/state'), ('GET', '/stream.mjpg'),
                     ('POST', '/api/open'), ('PUT', '/api/schedule')):
            r, _ = self.req(m, p, auth=None)
            self.assertEqual(r.status, 401, p)
            self.assertIn('Basic', r.getheader('WWW-Authenticate'))
        r, _ = self.req('GET', '/', auth='Basic ' + base64.b64encode(b'nilrasp:nope').decode())
        self.assertEqual(r.status, 401)
        self.assertEqual(self.app.calls, [])

    def test_page_and_state(self):
        r, data = self.req('GET', '/')
        self.assertEqual(r.status, 200)
        self.assertIn(b'<!DOCTYPE html>', data)
        r, data = self.req('GET', '/api/state')
        self.assertEqual(json.loads(data), {'valve': 'closed'})

    def test_writes_pass_body_and_status(self):
        r, _ = self.req('POST', '/api/open', b'{"seconds": 45}')
        self.assertEqual(r.status, 200)
        r, _ = self.req('POST', '/api/close', b'{}')
        self.assertEqual(r.status, 503)
        r, data = self.req('PUT', '/api/schedule', b'{"time": "x"}')
        self.assertEqual(r.status, 400)
        self.assertEqual(json.loads(data), {'error': 'bad'})
        self.assertEqual(self.app.calls, [('open', {'seconds': 45}), ('close', {}),
                                          ('schedule', {'time': 'x'})])

    def test_bad_json_becomes_none(self):
        self.req('POST', '/api/open', b'{not json')
        self.assertEqual(self.app.calls, [('open', None)])

    def test_non_json_content_type_rejected(self):
        r, _ = self.req('POST', '/api/open', b'{"seconds": 45}', ctype='text/plain')
        self.assertEqual(r.status, 415)
        self.assertEqual(self.app.calls, [])

    def test_camera_failure_is_503(self):
        r, _ = self.req('GET', '/stream.mjpg')
        self.assertEqual(r.status, 503)

    def test_unknown_404(self):
        self.assertEqual(self.req('GET', '/nope')[0].status, 404)
        self.assertEqual(self.req('POST', '/nope', b'{}')[0].status, 404)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd server && mkdir -p static && echo '<!DOCTYPE html><title>Irrigation</title>' > static/index.html && python3 -m unittest test_http -v`
Expected: `AttributeError: module 'irrigation_server' has no attribute 'Handler'`.

- [ ] **Step 3: Add `Handler` (insert above `def main`)**

```python
class Handler(server.BaseHTTPRequestHandler):
    app = camera = frames = None          # set by main()
    user = password = ''

    def _authed(self):
        if check_auth(self.headers.get('Authorization'), self.user, self.password):
            return True
        log.warning('auth failed from %s', self.client_address[0])
        time.sleep(FAIL_DELAY_S)
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Irrigation"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode(), 'application/json')

    def _write(self, routes):
        """POST/PUT: JSON only. A non-simple Content-Type forces a CORS preflight, so
        another website can't drive the valve using the browser's saved password."""
        if not self._authed():
            return
        fn = routes.get(self.path)
        if fn is None:
            return self.send_error(404)
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self._json(415, {'error': 'Content-Type must be application/json'})
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n)) if 0 < n <= 4096 else None
        except ValueError:
            body = None
        self._json(*fn(body))

    def do_GET(self):
        if not self._authed():
            return
        if self.path == '/':
            self._send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/api/state':
            self._json(200, self.app.state())
        elif self.path == '/stream.mjpg':
            self._stream()
        else:
            self.send_error(404)

    def do_POST(self):
        self._write({'/api/open': self.app.open, '/api/close': self.app.close})

    def do_PUT(self):
        self._write({'/api/schedule': self.app.set_schedule})

    def _stream(self):
        try:
            self.camera.acquire()
        except Exception:
            log.exception('camera failed to start')
            return self._json(503, {'error': 'camera unavailable'})
        log.info('viewer connected: %s', self.client_address[0])
        try:
            self.send_response(200)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            while True:
                with self.frames.condition:
                    if not self.frames.condition.wait(timeout=5):
                        log.warning('no frame from camera for 5 s')
                        continue
                    frame = self.frames.frame
                self.wfile.write(b'--FRAME\r\n')
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
                self.wfile.write(b'\r\n')
        except (BrokenPipeError, ConnectionResetError):
            log.info('viewer left: %s', self.client_address[0])
        finally:
            self.camera.release()

    def log_message(self, fmt, *args):   # silence per-request stderr noise
        pass


def scheduler_loop(app):
    while True:
        try:
            app.tick()
        except Exception:
            log.exception('tick failed')
        time.sleep(TICK_S)
```

- [ ] **Step 4: Replace the `raise NotImplementedError('wired in Task 8')` line in `main` with the wiring**

```python
    import paho.mqtt.client as mqtt    # here so tests run without paho
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)   # paho >= 2.0
    except AttributeError:
        client = mqtt.Client()                                   # paho 1.x (Bookworm apt)
    app = App(STATE_PATH, lambda t, p: client.publish(t, p, qos=1, retain=False))
    client.on_connect = lambda c, u, f, rc: c.subscribe(
        [('irrigation/+/availability', 1), ('irrigation/+/state', 1)])
    client.on_message = lambda c, u, m: app.on_message(m.topic, m.payload)
    client.on_disconnect = lambda c, u, rc: app.on_broker_lost()
    client.connect_async(MQTT_HOST, MQTT_PORT)   # retries in the background if mosquitto is late
    client.loop_start()
    threading.Thread(target=scheduler_loop, args=(app,), daemon=True).start()

    frames = FrameBuffer()
    Handler.app, Handler.camera, Handler.frames = app, Camera(*picamera(frames)), frames
    Handler.user, Handler.password = user, password
    log.info('serving on http://0.0.0.0:%d/', PORT)
    server.ThreadingHTTPServer(('', PORT), Handler).serve_forever()
```

- [ ] **Step 5: Run all the server tests and confirm they pass**

Run: `cd server && python3 -m unittest -v`
Expected: every test in `test_schedule`, `test_state_store`, `test_camera`, `test_app` and `test_http` passes. The startup guard still exits before importing paho.

- [ ] **Step 6: Commit**

```bash
git add server/
git commit -m "feat(server): HTTP API with auth, JSON-only writes, on-demand MJPEG stream"
```

---

### Task 9: The page

**Files:**
- Replace: `server/static/index.html`

**Interfaces:**
- Consumes: `GET /api/state` (fields from `App.state()`), `POST /api/open {seconds}`, `POST /api/close {}`, `PUT /api/schedule {enabled,time,seconds}`, `GET /stream.mjpg`. Writes send `Content-Type: application/json`.

- [ ] **Step 1: Write `server/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Irrigation</title>
<style>
body{background:#0f1117;color:#e8eaf0;font-family:-apple-system,sans-serif;margin:0;padding:1rem}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem;max-width:1100px;margin:0 auto}
.card{background:#1a1d27;border:1px solid #2e3248;border-radius:14px;padding:1.2rem;margin-bottom:1rem}
.cl{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:#7b82a0;margin-bottom:.8rem;display:flex;justify-content:space-between}
#vid{width:100%;aspect-ratio:4/3;background:#000;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#7b82a0;overflow:hidden}
#vid img{width:100%;height:100%;object-fit:contain}
button{padding:.8rem 0;border-radius:8px;border:1.5px solid #2e3248;background:#22263a;color:#e8eaf0;font-size:1rem;font-weight:600;width:100%;margin-top:.8rem}
button:disabled{opacity:.4}
button.sel{border-color:#4f7ef8;color:#4f7ef8}
.g{display:grid;grid-template-columns:repeat(5,1fr);gap:.4rem}
.g button{margin-top:0}
input{background:#22263a;border:1px solid #2e3248;color:#e8eaf0;border-radius:6px;padding:.4rem;font-size:1rem;width:4.5rem}
input[type=time]{width:7rem}input[type=checkbox]{width:auto;transform:scale(1.4)}
.row{display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-top:.8rem}
.ct{text-align:center}
#st{font-size:1.5rem;font-weight:700}.open{color:#34d399}.closed{color:#f87171}
#rm{font-size:2.4rem;font-variant-numeric:tabular-nums;margin:.3rem 0 .8rem}
#ss{font-size:1.1rem;padding:1rem 0}
#ss.start{background:#0d3326;border-color:#34d399;color:#34d399}
#ss.stop{background:#3b1212;border-color:#f87171;color:#f87171}
.sf{margin-top:.8rem;font-size:.8rem;color:#fbbf24;text-align:center}
.mu{font-size:.8rem;color:#7b82a0;margin-top:.6rem}
#fbk{display:none;background:#3a2e0d;border:1px solid #fbbf24;color:#fbbf24;border-radius:8px;padding:.6rem;margin-bottom:1rem;font-size:.85rem}
#msg{min-height:1.2em;color:#fbbf24;font-size:.85rem;margin-bottom:.6rem}
</style></head><body>
<main>
<section>
 <div class="card">
  <div class="cl">Video</div>
  <div id="vid">Camera off</div>
  <button id="vb" onclick="vid()">&#9654; Start video</button>
  <div class="mu">Auto-stops after 10 min</div>
 </div>
</section>
<section>
 <div id="fbk">ESP is running its own 08:00 fallback (it lost contact with the Pi)</div>
 <div id="msg"></div>
 <div class="card ct">
  <div class="cl"><span>Valve</span><span id="on">&#9679; offline</span></div>
  <div id="st" class="closed">CLOSED</div>
  <div id="rm">--:--</div>
  <div class="g" id="pre"></div>
  <div class="row"><span>Run for</span><span><input id="sec" type="number" min="1" max="90" value="60" oninput="hl()"> s</span></div>
  <button id="ss" class="start" onclick="ss()" disabled>START</button>
  <div class="sf">&#128737; Safety cutoff: <b id="mx">90</b> s max</div>
 </div>
 <div class="card">
  <div class="cl">Daily schedule</div>
  <div class="row"><span>Enabled</span><input type="checkbox" id="se"></div>
  <div class="row"><span>Start at</span><input type="time" id="stt" value="08:00" max="21:59"></div>
  <div class="row"><span>Run for</span><span><input type="number" id="ssec" min="1" max="90" value="60"> s</span></div>
  <div class="mu" id="nx"></div>
  <div class="mu" id="lr"></div>
  <div class="mu">Missed runs catch up when the Pi is back, until 22:00.</div>
  <button onclick="save()">Save schedule</button>
  <div class="mu" id="ck"></div>
 </div>
</section>
</main>
<script>
var S=null,loaded=false,vt=null;
function $(i){return document.getElementById(i)}
function msg(t){$('msg').textContent=t}
function mmss(s){return Math.floor(s/60)+':'+('0'+s%60).slice(-2)}
[15,30,45,60,90].forEach(function(s){var b=document.createElement('button');b.textContent=s;
 b.onclick=function(){$('sec').value=s;hl()};$('pre').appendChild(b)});
function hl(){var v=$('sec').value;document.querySelectorAll('#pre button').forEach(function(b){b.classList.toggle('sel',b.textContent==v)})}
function vid(){var img=$('vid').querySelector('img');clearTimeout(vt);
 if(img){img.onerror=null;img.src='';$('vid').textContent='Camera off';$('vb').innerHTML='&#9654; Start video';return}
 img=new Image();img.alt='Live camera';
 img.onerror=function(){$('vid').textContent='Camera unavailable';$('vb').innerHTML='&#9654; Start video'};
 img.src='/stream.mjpg';$('vid').textContent='';$('vid').appendChild(img);
 $('vb').innerHTML='&#9632; Stop video';vt=setTimeout(vid,600000)}
function sync(t){S=JSON.parse(t);var o=S.valve==='open',on=S.esp_online;
 $('on').textContent=on?'● ESP online':'● Valve offline';$('on').style.color=on?'#34d399':'#f87171';
 $('st').className=o?'open':'closed';$('st').textContent=o?'OPEN':'CLOSED';
 $('rm').textContent=o?mmss(S.remaining_s):'--:--';
 $('ss').className=o?'stop':'start';$('ss').textContent=o?'STOP':'START';$('ss').disabled=!on;
 $('mx').textContent=S.max_s;$('sec').max=$('ssec').max=S.max_s;
 $('fbk').style.display=S.fallback?'block':'none';
 if(!loaded){$('se').checked=S.schedule.enabled;$('stt').value=S.schedule.time;$('ssec').value=S.schedule.seconds;loaded=true}
 $('nx').textContent='Next: '+S.next_run;$('lr').textContent='Last: '+(S.last_run||'never');
 $('ck').textContent='Clock '+S.clock+(S.synced?' · synced':' · NOT synced, schedule paused')}
function req(m,u,b,ok,then){var x=new XMLHttpRequest();x.open(m,u);
 if(m!=='GET')x.setRequestHeader('Content-Type','application/json');
 x.onload=function(){if(x.status===200){if(then)then(x.responseText);if(ok)msg(ok)}
  else{var e='Error '+x.status;try{e=JSON.parse(x.responseText).error||e}catch(_){}msg(e)}};
 x.onerror=function(){msg('No response from Pi')};x.send(b?JSON.stringify(b):null)}
function poll(){req('GET','/api/state',null,null,sync)}
function ss(){if(S&&S.valve==='open')req('POST','/api/close',{},'Stopped',poll);
 else req('POST','/api/open',{seconds:parseInt($('sec').value,10)},'Started',poll)}
function save(){req('PUT','/api/schedule',{enabled:$('se').checked,time:$('stt').value,
 seconds:parseInt($('ssec').value,10)},'Schedule saved',function(){loaded=false;poll()})}
hl();poll();setInterval(poll,2000);
</script></body></html>
```

- [ ] **Step 2: Check it against a stub server on the Mac**

Run: `cd server && python3 -m unittest test_http -v`
Expected: passes. The page test checks that `<!DOCTYPE html>` is still present.

Then open the page without the Pi:

```bash
cd server && python3 -c "
import irrigation_server as s, http.server as h
class A:
    def state(self): return {'esp_online':True,'valve':'open','remaining_s':42,'max_s':90,'fallback':True,'synced':True,'clock':'14:32','schedule':{'enabled':True,'time':'08:00','seconds':60},'last_run':'2026-10-14 08:00','next_run':'tomorrow 08:00'}
    def open(self,b): return 400,{'error':'seconds must be a whole number 1..90'}
    close=set_schedule=open
class C:
    def acquire(self): raise RuntimeError
s.Handler.app,s.Handler.camera,s.Handler.user,s.Handler.password=A(),C(),'u','p'
h.ThreadingHTTPServer(('127.0.0.1',8765),s.Handler).serve_forever()"
```

Open `http://127.0.0.1:8765/` and log in as `u` / `p`. Expected:
- On a wide window, the two panels sit side by side; on a narrow one, they stack.
- The valve shows OPEN 0:42 with a red STOP button, and the fallback banner is visible.
- Clicking STOP shows "seconds must be a whole number 1..90".
- Start video shows "Camera unavailable".

Stop the server with Ctrl-C.

- [ ] **Step 3: Commit**

```bash
git add server/static/index.html
git commit -m "feat(server): page with on-demand video and valve/schedule controls"
```

---

### Task 10: Deploy files, README, end-to-end on the Pi

**Files:**
- Create: `systemd/irrigation.service`, `systemd/mosquitto-irrigation.conf`
- Replace: `README.md`

- [ ] **Step 1: Write `systemd/irrigation.service`**

```ini
[Unit]
Description=Irrigation page, API, camera and scheduler (:8000)
Wants=network-online.target
After=network-online.target mosquitto.service

[Service]
Type=simple
User=nilrasp
WorkingDirectory=/home/nilrasp/irrigation-controller/server
# IRRIGATION_USER=... / IRRIGATION_PASS=... ; chmod 600, never committed
EnvironmentFile=/home/nilrasp/.irrigation.env
ExecStart=/usr/bin/python3 irrigation_server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `systemd/mosquitto-irrigation.conf`**

```conf
# /etc/mosquitto/conf.d/irrigation.conf — let the ESP connect from the LAN.
# ponytail: anonymous; anyone on the LAN/tailnet can publish, but the ESP caps every run at 90 s.
# Add password_file + ESP credentials if that ever matters.
listener 1883
allow_anonymous true
```

- [ ] **Step 3: Write `README.md`**

````markdown
# Irrigation Controller

One page on the Pi 4 (over Tailscale): live video on demand plus garden valve control.
An ESP-01S drives the 24 V solenoid and caps every run at 90 s. Scheduled runs missed
because of a power cut catch up when the Pi is back, until 22:00. If the Pi is dead,
the ESP waters on its own at 08:00 for 60 s.

Design: [docs/specs/2026-10-14-irrigation-design.md](docs/specs/2026-10-14-irrigation-design.md)
Firmware: [firmware/README.md](firmware/README.md)

## Deploy on the Pi 4 (SSH as nilrasp)

```bash
sudo apt update && sudo apt install -y git mosquitto mosquitto-clients python3-paho-mqtt python3-picamera2
sudo timedatectl set-timezone Asia/Kolkata
git clone https://github.com/chakrabortyniladri/irrigation-controller.git ~/irrigation-controller
cd ~/irrigation-controller

sudo cp systemd/mosquitto-irrigation.conf /etc/mosquitto/conf.d/irrigation.conf
sudo systemctl enable mosquitto && sudo systemctl restart mosquitto

# Reuse the camera page login (12+ char password), or create a new one with nano
sed 's/^CAMERA_/IRRIGATION_/' ~/.camera.env > ~/.irrigation.env && chmod 600 ~/.irrigation.env

sudo systemctl disable --now camera        # old camera page: frees the camera and port 8000
sudo cp systemd/irrigation.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now irrigation
journalctl -u irrigation -f
```

Open `http://<pi-tailscale-name>:8000/`.

Update: `cd ~/irrigation-controller && git pull && sudo systemctl restart irrigation`

## Tests

```bash
cd server && python3 -m unittest -v
cd firmware/test && make test
```
````

- [ ] **Step 4: Commit and push**

```bash
git add systemd/ README.md
git commit -m "docs: deploy units and README"
git push -u origin main
```

- [ ] **Step 5: End-to-end checks on the real hardware**

Run `mosquitto_sub -h localhost -t 'irrigation/#' -v` in a second SSH session for all of these.

1. **Pairing:** flash the ESP. Within 30 s the page shows "ESP online", and `state.json` contains the GUID.
2. **Manual run:** press 15 then START. The relay clicks, the countdown runs, and the valve closes at 0. Then try STOP mid-run. Then type 91 and press START: you get an error and the relay doesn't move.
3. **Schedule:** set the start 2 min ahead for 15 s and enable it. It runs once, and "Last" shows today.
4. **Power cut:** set the start 5 min ahead. Turn off the Pi and the ESP, and wait until 2 min after the start time. Turn both back on. The run happens once, within about 1 min of NTP sync. Then power-cycle again: no second run.
5. **Fallback:** temporarily set `FALLBACK_H/M` to 40 min from now and flash it. Run `sudo systemctl stop irrigation`. After 30 min with no heartbeat, the ESP runs 60 s at that time (serial: `[fallback]`). Start the service again; the page shows the fallback banner and "Last" today. Put 08:00 back and reflash.
6. **OTA safety:** start a 90 s run, then begin an OTA upload. The relay releases immediately.
7. **Video:** Start video shows the stream. Stop video, then within about 5 s `rpicam-still -o /tmp/t.jpg` works, which proves the camera was released.

- [ ] **Step 6: Retire the old copy (ask the user first)**

Once every check passes, ask the user before deleting `iot-home-automation/devices/irrigation-controller/` and the superseded `docs/superpowers/specs/2026-10-10-irrigation-controller-design.md`.
