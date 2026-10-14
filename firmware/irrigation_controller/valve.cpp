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
  uint32_t want = (uint32_t)clampSeconds(seconds) * 1000UL;
  if (isOpen) {   // second open mid-run: extend, but the overflow limit is continuous flow
    uint32_t total = (nowMs - startMs) + want;
    durationMs = total < MAX_RUN_S * 1000UL ? total : MAX_RUN_S * 1000UL;
    return;
  }
  isOpen     = true;
  startMs    = nowMs;
  durationMs = want;
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
