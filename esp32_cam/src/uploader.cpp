#include "uploader.h"
#include "config.h"
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>

static const char* BOUNDARY = "----ESP32CAMBound";

bool uploaderPost(camera_fb_t* fb, uint32_t capturedAt, uint32_t syncRoundId) {
    if (!fb || !fb->buf || fb->len == 0) return false;

    // Build multipart body in three segments so we avoid a second copy:
    //   [part_device_id][part_file_header][jpeg_bytes][epilogue]
    const String CRLF = "\r\n";

    const String partDeviceId =
        "--" + String(BOUNDARY) + CRLF +
        "Content-Disposition: form-data; name=\"device_id\"" + CRLF +
        CRLF +
        DEVICE_ID + CRLF;

    const String partFileHeader =
        "--" + String(BOUNDARY) + CRLF +
        "Content-Disposition: form-data; name=\"file\"; filename=\"frame.jpg\"" + CRLF +
        "Content-Type: image/jpeg" + CRLF +
        CRLF;

    const String epilogue = CRLF + "--" + String(BOUNDARY) + "--" + CRLF;

    const size_t totalLen = partDeviceId.length()
                          + partFileHeader.length()
                          + fb->len
                          + epilogue.length();

    // Allocate full body in PSRAM when available
    uint8_t* body = static_cast<uint8_t*>(
        psramFound() ? ps_malloc(totalLen) : malloc(totalLen)
    );
    if (!body) {
        Serial.printf("[Upload] Out of memory (%zu bytes needed)\n", totalLen);
        return false;
    }

    uint8_t* ptr = body;
    memcpy(ptr, partDeviceId.c_str(),   partDeviceId.length());   ptr += partDeviceId.length();
    memcpy(ptr, partFileHeader.c_str(), partFileHeader.length()); ptr += partFileHeader.length();
    memcpy(ptr, fb->buf,                fb->len);                 ptr += fb->len;
    memcpy(ptr, epilogue.c_str(),       epilogue.length());

    const String url =
        "http://" + String(SERVER_HOST) + ":" + SERVER_PORT + UPLOAD_PATH;

    // Context printed with every result so a failure is diagnosable on its own:
    // weak RSSI → transport errors; low heap → POST allocation failures.
    const bool wifiUp = (WiFi.status() == WL_CONNECTED);
    Serial.printf("[Upload] POST %s  body=%zuB jpeg=%zuB heap=%uB rssi=%ddBm wifi=%s\n",
                  url.c_str(), totalLen, fb->len,
                  ESP.getFreeHeap(), WiFi.RSSI(), wifiUp ? "up" : "DOWN");

    WiFiClient client;
    HTTPClient http;
    http.begin(client, url);
    http.setConnectTimeout(5000);   // TCP connect (ms)
    http.setTimeout(15000);         // wait for server response (ms) — VGA frames are large
    http.addHeader("Content-Type",
                   "multipart/form-data; boundary=" + String(BOUNDARY));
    http.addHeader("X-Captured-At", String(capturedAt));
    // Shared across all cameras in this trigger — lets the Pi group the round
    // together without NTP-bucket boundary splits. Omit when unknown (0).
    if (syncRoundId != 0) {
        http.addHeader("X-Sync-Round", String(syncRoundId));
    }

    const uint32_t t0 = millis();
    int code = http.POST(body, totalLen);
    const uint32_t elapsed = millis() - t0;
    free(body);

    // Negative codes are transport-level failures (no HTTP response received),
    // e.g. -1 connection refused, -11 read timeout. errorToString() names them.
    if (code <= 0) {
        Serial.printf("[Upload] FAILED  transport error %d (%s)  after %ums  rssi=%ddBm wifi=%s\n",
                      code, http.errorToString(code).c_str(),
                      elapsed, WiFi.RSSI(),
                      WiFi.status() == WL_CONNECTED ? "up" : "DOWN");
        http.end();
        return false;
    }

    if (code >= 200 && code < 300) {
        Serial.printf("[Upload] OK  HTTP %d  (%zuB jpeg, %ums)\n", code, fb->len, elapsed);
        http.end();
        return true;
    }

    // Server responded but rejected the request — surface its body (FastAPI sends
    // a JSON "detail" message, e.g. 415 wrong content-type, 422 invalid JPEG).
    String resp = http.getString();
    Serial.printf("[Upload] FAILED  HTTP %d  after %ums  body=%s\n",
                  code, elapsed, resp.c_str());
    http.end();
    return false;
}
