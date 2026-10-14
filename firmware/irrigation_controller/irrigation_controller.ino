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

  // Reply to every heartbeat: the Pi trusts only live traffic, not the retained "online".
  void onHeartbeat() { pi.seen(millis()); publishState(); }
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
  network::tick(valve.mayBlock(millis()));
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
