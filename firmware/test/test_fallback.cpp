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
  ASSERT_TRUE(r.update((uint32_t)(0xFFFFFFFFUL - 1000 + PI_DEAD_AFTER_MS)));   // wraps past 0, like millis()
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
