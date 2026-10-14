// firmware/irrigation_controller/network.cpp — Wi-Fi + MQTT + OTA.
// MQTT-only: the Pi is the only remote path (Tailscale), so there is no on-device web page.
#include "network.h"
#include "config.h"
#include "secrets.h"   // OTA_PASSWORD; copy secrets.h.example, never commit
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>
#include <ArduinoOTA.h>
#include <string.h>
#include <stdio.h>

char TOPIC_STATE[64];
char TOPIC_COMMAND[64];
char TOPIC_AVAIL[64];

namespace {
  network::CommandHandler g_onCommand   = nullptr;
  network::Notify         g_onHeartbeat = nullptr;

  char g_clientId[20];
  char g_onlinePayload[128];
  char g_offlinePayload[128];

  WiFiClient    wifiClient;
  PubSubClient  mqtt(wifiClient);
  unsigned long nextReconnect = 0;
  unsigned long backoff       = MQTT_RECONNECT_MIN;

  void mqttCallback(char* topic, byte* payload, unsigned int length) {
    if (strcmp(topic, TOPIC_HEARTBEAT) == 0) { if (g_onHeartbeat) g_onHeartbeat(); return; }
    if (strcmp(topic, TOPIC_COMMAND) != 0 || length >= 256) return;
    char buf[256];
    memcpy(buf, payload, length);
    buf[length] = '\0';
    if (g_onCommand) g_onCommand(buf);
  }

  bool connectMqtt() {
    IPAddress ip;
    // Resolve every attempt: the Pi's DHCP address can change while the ESP keeps running.
    if (!WiFi.hostByName(MQTT_BROKER_HOST, ip, NET_TIMEOUT_MS)) {
      Serial.printf("[net] DNS failed for %s\n", MQTT_BROKER_HOST);
      return false;
    }
    mqtt.setServer(ip, MQTT_BROKER_PORT);
    if (!mqtt.connect(g_clientId, nullptr, nullptr, TOPIC_AVAIL, 1, true, g_offlinePayload)) {
      Serial.printf("[net] mqtt connect failed rc=%d\n", mqtt.state());
      return false;
    }
    mqtt.publish(TOPIC_AVAIL, g_onlinePayload, true);
    mqtt.subscribe(TOPIC_COMMAND);
    mqtt.subscribe(TOPIC_HEARTBEAT);
    Serial.println("[net] mqtt connected");
    return true;
  }
}

namespace network {

void begin(const char* guid, const char* label, CommandHandler onCommand, Notify onHeartbeat) {
  g_onCommand   = onCommand;
  g_onHeartbeat = onHeartbeat;

  snprintf(TOPIC_STATE,   sizeof(TOPIC_STATE),   "irrigation/%s/state",        guid);
  snprintf(TOPIC_COMMAND, sizeof(TOPIC_COMMAND), "irrigation/%s/command",      guid);
  snprintf(TOPIC_AVAIL,   sizeof(TOPIC_AVAIL),   "irrigation/%s/availability", guid);
  snprintf(g_clientId,    sizeof(g_clientId),    "irr-%.8s", guid);
  snprintf(g_onlinePayload,  sizeof(g_onlinePayload),
           "{\"label\":\"%s\",\"guid\":\"%s\",\"status\":\"online\"}", label, guid);
  snprintf(g_offlinePayload, sizeof(g_offlinePayload),
           "{\"label\":\"%s\",\"guid\":\"%s\",\"status\":\"offline\"}", label, guid);

  WiFi.mode(WIFI_STA);
  WiFiManager wm;
  wm.setConfigPortalTimeout(180);
  if (!wm.autoConnect(WIFI_AP_NAME)) {
    Serial.println("[net] WiFi failed — restarting");
    delay(500);
    ESP.restart();
  }
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  Serial.printf("[net] wifi ok: %s\n", WiFi.localIP().toString().c_str());

  wifiClient.setTimeout(NET_TIMEOUT_MS);   // TCP connect to a dead Pi must not stall loop()
  mqtt.setSocketTimeout(1);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);                 // state JSON + topic exceeds PubSubClient's 256 default

  ArduinoOTA.setHostname(g_clientId);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  // Flashing stops loop() (and the auto-off timer) for ~30 s: close the valve first.
  ArduinoOTA.onStart([] { digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW); });
  ArduinoOTA.begin();
}

void tick(bool mayBlock) {
  ArduinoOTA.handle();
  if (mqtt.connected()) { mqtt.loop(); return; }
  if (!mayBlock) return;   // valve about to auto-close: don't stall loop() now
  unsigned long now = millis();
  if ((long)(now - nextReconnect) < 0) return;   // rollover-safe
  if (connectMqtt()) {
    backoff = MQTT_RECONNECT_MIN;
  } else {
    nextReconnect = now + backoff;
    backoff = min(backoff * 2, MQTT_RECONNECT_MAX);
  }
}

bool connected() { return mqtt.connected(); }

bool publish(const char* topic, const char* payload, bool retained) {
  if (!mqtt.connected()) return false;
  return mqtt.publish(topic, payload, retained);
}

} // namespace network
