#include "camera.h"
#include "config.h"
#include <Arduino.h>

// ── AI-Thinker ESP32-CAM GPIO map ─────────────────────────────────────────────
#define PWDN_GPIO   32
#define RESET_GPIO  -1
#define XCLK_GPIO    0
#define SIOD_GPIO   26  // SDA
#define SIOC_GPIO   27  // SCL
#define Y9_GPIO     35
#define Y8_GPIO     34
#define Y7_GPIO     39
#define Y6_GPIO     36
#define Y5_GPIO     21
#define Y4_GPIO     19
#define Y3_GPIO     18
#define Y2_GPIO      5
#define VSYNC_GPIO  25
#define HREF_GPIO   23
#define PCLK_GPIO   22

bool cameraInit() {
    camera_config_t cfg = {};

    cfg.ledc_channel = LEDC_CHANNEL_0;
    cfg.ledc_timer   = LEDC_TIMER_0;
    cfg.pin_d0       = Y2_GPIO;
    cfg.pin_d1       = Y3_GPIO;
    cfg.pin_d2       = Y4_GPIO;
    cfg.pin_d3       = Y5_GPIO;
    cfg.pin_d4       = Y6_GPIO;
    cfg.pin_d5       = Y7_GPIO;
    cfg.pin_d6       = Y8_GPIO;
    cfg.pin_d7       = Y9_GPIO;
    cfg.pin_xclk     = XCLK_GPIO;
    cfg.pin_pclk     = PCLK_GPIO;
    cfg.pin_vsync    = VSYNC_GPIO;
    cfg.pin_href     = HREF_GPIO;
    cfg.pin_sscb_sda = SIOD_GPIO;
    cfg.pin_sscb_scl = SIOC_GPIO;
    cfg.pin_pwdn     = PWDN_GPIO;
    cfg.pin_reset    = RESET_GPIO;
    cfg.xclk_freq_hz = 20000000;
    cfg.pixel_format = PIXFORMAT_JPEG;

    if (psramFound()) {
        cfg.frame_size   = FRAME_SIZE;
        cfg.jpeg_quality = JPEG_QUALITY;
        cfg.fb_count     = 2;
        cfg.fb_location  = CAMERA_FB_IN_PSRAM;
        cfg.grab_mode    = CAMERA_GRAB_LATEST;  // always return the freshest frame
    } else {
        // PSRAM absent: fall back to a smaller buffer in DRAM
        cfg.frame_size   = FRAMESIZE_SVGA;
        cfg.jpeg_quality = 20;
        cfg.fb_count     = 1;
        cfg.fb_location  = CAMERA_FB_IN_DRAM;
        cfg.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
    }

    esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) {
        Serial.printf("[Camera] Init failed: 0x%x\n", err);
        return false;
    }

    // Tune OV2640 for indoor bus lighting
    sensor_t* s = esp_camera_sensor_get();
    s->set_brightness(s,    1);   // slight boost
    s->set_saturation(s,   -1);   // reduce colour noise
    s->set_whitebal(s,      1);   // auto white balance
    s->set_awb_gain(s,      1);
    s->set_exposure_ctrl(s, 1);   // auto exposure
    s->set_aec2(s,          1);   // AEC DSP
    s->set_gain_ctrl(s,     1);   // auto gain

    Serial.println("[Camera] Initialised OK");
    return true;
}

camera_fb_t* captureFrame() {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("[Camera] Capture failed — frame buffer empty");
        return nullptr;
    }
    Serial.printf("[Camera] Captured %zu bytes  (%dx%d)\n", fb->len, fb->width, fb->height);
    return fb;
}
