#include "udp_listener.h"
#include <Arduino.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>

static WiFiUDP       _udp;
static CaptureCallback _cb = nullptr;

bool udpListenerInit(uint16_t port, CaptureCallback cb) {
    _cb = cb;
    if (_udp.begin(port)) {
        Serial.printf("[UDP] Listening on port %d\n", port);
        return true;
    }
    Serial.println("[UDP] begin() failed");
    return false;
}

void udpListenerLoop() {
    int len = _udp.parsePacket();
    if (len <= 0) return;

    // Packet fits in 128 bytes: {"cmd":"capture","ts":1234567890}
    char buf[128];
    int  n = _udp.read(buf, sizeof(buf) - 1);
    if (n <= 0) return;
    buf[n] = '\0';

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, buf);
    if (err) {
        Serial.printf("[UDP] Bad JSON: %s\n", err.c_str());
        return;
    }

    const char* cmd = doc["cmd"];
    if (!cmd || strcmp(cmd, "capture") != 0) return;

    uint32_t ts = doc["ts"] | 0;
    Serial.printf("[UDP] Trigger received (server_ts=%lu)\n", (unsigned long)ts);

    if (_cb) _cb(ts);
}
