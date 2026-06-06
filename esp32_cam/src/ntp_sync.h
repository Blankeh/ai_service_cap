#pragma once
#include <stdint.h>

// Sync to NTP. Returns true if time was obtained within 10 s.
bool ntpInit();

// Current Unix timestamp (UTC). Returns 0 if never synced.
uint32_t ntpUnixTime();

bool ntpIsSynced();
