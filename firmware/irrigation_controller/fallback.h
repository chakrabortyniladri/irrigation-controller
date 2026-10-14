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
