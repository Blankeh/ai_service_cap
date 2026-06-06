#pragma once
#include <esp_camera.h>

// Initialise the OV2640 sensor. Returns false on failure.
bool cameraInit();

// Capture one JPEG frame. Returns nullptr on failure.
// Caller MUST call esp_camera_fb_return(fb) when done with the buffer.
camera_fb_t* captureFrame();
