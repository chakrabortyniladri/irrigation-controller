// firmware/test/test_runner.h — header-only micro test harness
#pragma once
#include <cstdio>
#include <cstdlib>
#include <cstring>

inline int& g_pass()  { static int n = 0; return n; }
inline int& g_fail()  { static int n = 0; return n; }

#define ASSERT_TRUE(cond)                                              \
  do {                                                                 \
    if (cond) { ++g_pass(); }                                          \
    else {                                                             \
      ++g_fail();                                                      \
      std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); \
    }                                                                  \
  } while (0)

#define ASSERT_FALSE(cond) ASSERT_TRUE(!(cond))

#define ASSERT_EQ_INT(expected, actual)                                \
  do {                                                                 \
    long long e = (long long)(expected), a = (long long)(actual);      \
    if (e == a) { ++g_pass(); }                                        \
    else {                                                             \
      ++g_fail();                                                      \
      std::fprintf(stderr, "FAIL %s:%d  expected=%lld actual=%lld\n",  \
                   __FILE__, __LINE__, e, a);                          \
    }                                                                  \
  } while (0)

#define ASSERT_STREQ(expected, actual)                                 \
  do {                                                                 \
    const char* e = (expected); const char* a = (actual);              \
    if (e && a && std::strcmp(e, a) == 0) { ++g_pass(); }              \
    else {                                                             \
      ++g_fail();                                                      \
      std::fprintf(stderr, "FAIL %s:%d  expected=\"%s\" actual=\"%s\"\n", \
                   __FILE__, __LINE__, e ? e : "(null)", a ? a : "(null)"); \
    }                                                                  \
  } while (0)

#define ASSERT_CONTAINS(haystack, needle)                              \
  do {                                                                 \
    const char* h = (haystack); const char* n = (needle);              \
    if (h && n && std::strstr(h, n)) { ++g_pass(); }                   \
    else {                                                             \
      ++g_fail();                                                      \
      std::fprintf(stderr, "FAIL %s:%d  \"%s\" does not contain \"%s\"\n", \
                   __FILE__, __LINE__, h ? h : "(null)", n ? n : "(null)"); \
    }                                                                  \
  } while (0)

inline int test_summary() {
  int p = g_pass(), f = g_fail();
  if (f == 0) { std::printf("OK: %d tests passed\n", p); return 0; }
  std::printf("FAIL: %d passed, %d failed\n", p, f);
  return 1;
}
