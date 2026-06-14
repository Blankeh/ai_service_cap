#pragma once
#include <Arduino.h>
#include <esp_camera.h>
#include <stdint.h>

// POST fb as multipart/form-data to the Pi's /upload endpoint.
// capturedAt:  Unix timestamp to send as X-Captured-At header.
// syncRoundId: the trigger ts shared by all cameras in this capture round, sent
//              as X-Sync-Round so the Pi groups the round together regardless of
//              per-camera clock skew. Pass 0 to omit.
// serverHost:  host/IP to POST to (the Pi's IP, learned from the sync trigger).
// Returns true on HTTP 2xx.
bool uploaderPost(camera_fb_t* fb, uint32_t capturedAt, uint32_t syncRoundId,
                  const String& serverHost);
