#include "uploader.h"
#include "config.h"
#include <Arduino.h>
#include <HTTPClient.h>

static const char* BOUNDARY = "----ESP32CAMBound";

bool uploaderPost(camera_fb_t* fb, uint32_t capturedAt) {
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

    HTTPClient http;
    http.begin(url);
    http.addHeader("Content-Type",
                   "multipart/form-data; boundary=" + String(BOUNDARY));
    http.addHeader("X-Captured-At", String(capturedAt));

    int code = http.POST(body, totalLen);
    free(body);
    http.end();

    if (code >= 200 && code < 300) {
        Serial.printf("[Upload] OK  HTTP %d  (%zu B jpeg)\n", code, fb->len);
        return true;
    }

    Serial.printf("[Upload] FAILED  HTTP %d  url=%s\n", code, url.c_str());
    return false;
}
