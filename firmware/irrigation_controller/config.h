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
