#include "ntp_sync.h"
#include "config.h"
#include <Arduino.h>
#include <time.h>
#include <sys/time.h>

static bool _synced = false;

bool ntpInit() {
    configTime(NTP_GMT_OFFSET, NTP_DST_OFFSET,
               NTP_SERVER, "time.google.com", "time.cloudflare.com");

    Serial.print("[NTP] Syncing");
    struct tm timeinfo;
    const uint32_t deadline = millis() + 10000;
    while (!getLocalTime(&timeinfo) && millis() < deadline) {
        Serial.print(".");
        delay(500);
    }

    if (!getLocalTime(&timeinfo)) {
        Serial.println(" FAILED — will use trigger timestamp as fallback");
        return false;
    }

    _synced = true;
    Serial.printf(" OK  %04d-%02d-%02d %02d:%02d:%02d (UTC+3)\n",
                  timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
                  timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
    return true;
}

bool ntpIsSynced() {
    return _synced;
}

uint32_t ntpUnixTime() {
    if (!_synced) return 0;
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    return static_cast<uint32_t>(tv.tv_sec);
}
