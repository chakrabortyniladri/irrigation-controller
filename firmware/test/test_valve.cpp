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
