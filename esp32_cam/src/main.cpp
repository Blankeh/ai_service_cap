#include <Arduino.h>
#include <WiFi.h>

#include "config.h"
#include "camera.h"
#include "ntp_sync.h"
#include "udp_listener.h"
#include "uploader.h"

// ── Shared trigger state (set by UDP callback, consumed in loop) ──────────────
// All accessed only from loop()'s thread (the UDP callback runs synchronously
// inside udpListenerLoop()), so no ISR/concurrency concerns.
static volatile bool     g_triggerPending  = false;
static volatile uint32_t g_triggerServerTs = 0;
static IPAddress         g_serverIp;   // Pi's IP, learned from the trigger (0.0.0.0 until first)

// ── UDP callback ──────────────────────────────────────────────────────────────
static void onCaptureTrigger(uint32_t serverTs, IPAddress serverIp) {
    g_triggerServerTs = serverTs;
    // Safety: only trust a server on our own subnet, so a rogue broadcast can't
    // redirect uploads off-network. Until a valid one arrives we use SERVER_HOST.
    const uint32_t mask = (uint32_t)WiFi.subnetMask();
    if (((uint32_t)serverIp & mask) == ((uint32_t)WiFi.localIP() & mask)) {
        g_serverIp = serverIp;
    } else {
        Serial.printf("[UDP] Ignoring off-subnet trigger source %s\n",
                      serverIp.toString().c_str());
    }
    g_triggerPending = true;
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
    // Disable modem-sleep: default STA power-save adds latency and drops packets
    // mid-transfer, which shows up as intermittent HTTP POST failures (-1 / -11).
    WiFi.setSleep(false);
    Serial.printf(" OK  IP=%s\n", WiFi.localIP().toString().c_str());
    return true;
}

// ── Capture one frame and POST it to the Pi ───────────────────────────────────
static void captureAndUpload() {
    // The trigger ts is shared by every camera in this round — use it as the
    // sync-round id so the Pi groups them together. Snapshot once (volatile).
    const uint32_t syncRoundId = g_triggerServerTs;

    // Prefer our own NTP time for accuracy; fall back to the server's timestamp
    // embedded in the trigger packet if NTP hasn't synced yet.
    uint32_t capturedAt = ntpIsSynced() ? ntpUnixTime() : syncRoundId;

    // Upload to the IP we learned from the trigger; fall back to the compiled-in
    // SERVER_HOST until the first trigger arrives.
    const String serverHost =
        ((uint32_t)g_serverIp != 0) ? g_serverIp.toString() : String(SERVER_HOST);

    camera_fb_t* fb = captureFrame();
    if (!fb) return;

    delay(200);
    uploaderPost(fb, capturedAt, syncRoundId, serverHost);
    esp_camera_fb_return(fb);
}

// Arduino entry points 
void setup() {
    Serial.begin(115200);
    Serial.printf("\n[Main] ESP32-CAM boot  device=%s\n", DEVICE_ID);

    if (!wifiConnect()) {
        Serial.println("[Main] WiFi failed — rebooting in 5 s");
        delay(5000);
        ESP.restart();
    }

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
