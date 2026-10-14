// firmware/irrigation_controller/network.h
#pragma once

namespace network {
  using CommandHandler = void (*)(const char* payloadJson);
  using Notify         = void (*)();

  // Blocks until Wi-Fi is up (WiFiManager portal "Irrigation-Setup" on first boot).
  // MQTT connects and reconnects forever from tick().
  void begin(const char* guid, const char* label, CommandHandler onCommand, Notify onHeartbeat);
  void tick();
  bool connected();
  bool publish(const char* topic, const char* payload, bool retained);
}
