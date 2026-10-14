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
