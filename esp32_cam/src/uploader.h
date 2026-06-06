#pragma once
#include <esp_camera.h>
#include <stdint.h>

// POST fb as multipart/form-data to the Pi's /upload endpoint.
// capturedAt: Unix timestamp to send as X-Captured-At header.
// Returns true on HTTP 2xx.
bool uploaderPost(camera_fb_t* fb, uint32_t capturedAt);
