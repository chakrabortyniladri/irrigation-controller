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
