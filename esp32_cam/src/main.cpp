#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "camera.h"
#include "ntp_sync.h"
#include "udp_listener.h"
#include "uploader.h"

// ── Shared trigger state (set by UDP callback, consumed in loop) ──────────────
static volatile bool     g_triggerPending = false;
static volatile uint32_t g_triggerServerTs = 0;

// ── UDP callback ──────────────────────────────────────────────────────────────
static void onCaptureTrigger(uint32_t serverTs) {
    g_triggerServerTs  = serverTs;
    g_triggerPending   = true;
}

// ── WiFi ──────────────────────────────────────────────────────────────────────
static bool wifiConnect() {
    Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    const uint32_t deadline = millis() + WIFI_TIMEOUT_MS;
    while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
        Serial.print(".");
        delay(500);
    }
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println(" FAILED");
        return false;
    }
    Serial.printf(" OK  IP=%s\n", WiFi.localIP().toString().c_str());
    return true;
}

// ── Capture one frame and POST it to the Pi ───────────────────────────────────
static void captureAndUpload() {
    // Prefer our own NTP time for accuracy; fall back to the server's timestamp
    // embedded in the trigger packet if NTP hasn't synced yet.
    uint32_t capturedAt = ntpIsSynced() ? ntpUnixTime() : g_triggerServerTs;

    camera_fb_t* fb = captureFrame();
    if (!fb) return;

    delay(200);
    uploaderPost(fb, capturedAt);
    esp_camera_fb_return(fb);
}

// ── Arduino entry points ──────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    Serial.printf("\n[Main] ESP32-CAM boot  device=%s\n", DEVICE_ID);

    if (!wifiConnect()) {
        Serial.println("[Main] WiFi failed — rebooting in 5 s");
        delay(5000);
        ESP.restart();
    }

    // NTP is best-effort: if it fails the trigger's embedded timestamp is used
    ntpInit();

    if (!cameraInit()) {
        Serial.println("[Main] Camera init failed — rebooting in 5 s");
        delay(5000);
        ESP.restart();
    }

    if (!udpListenerInit(SYNC_UDP_PORT, onCaptureTrigger)) {
        Serial.println("[Main] UDP listener failed — rebooting in 5 s");
        delay(5000);
        ESP.restart();
    }

    Serial.printf("[Main] Ready — listening for capture triggers on UDP :%d\n", SYNC_UDP_PORT);
}

void loop() {
    // Reconnect silently if WiFi drops (bus enters a tunnel, etc.)
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[WiFi] Lost connection — reconnecting");
        wifiConnect();
    }

    udpListenerLoop();

    if (g_triggerPending) {
        g_triggerPending = false;
        captureAndUpload();
    }
}
