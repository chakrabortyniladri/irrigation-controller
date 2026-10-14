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
