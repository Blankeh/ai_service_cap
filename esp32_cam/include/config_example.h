#pragma once

// ─── WiFi ─────────────────────────────────────────────────────────────────────
#define WIFI_SSID        "your_wifi_ssid"
#define WIFI_PASSWORD    "your_wifi_password"
#define WIFI_TIMEOUT_MS  15000

// ─── Raspberry Pi AI-service ──────────────────────────────────────────────────
// The Pi's IP is auto-discovered from the UDP sync trigger's source address, so
// it no longer needs hardcoding here (survives DHCP changes). Only the port and
// path are fixed.
#define SERVER_PORT  8000
#define UPLOAD_PATH  "/api/v1/upload"

// ─── Camera identity ──────────────────────────────────────────────────────────
// Flash each physical camera with its own position label.
// Must match a position the Pi's CAMERA_ID_TEMPLATE recognises:
//   front → 001  |  mid → 002  |  rear → 003
#define DEVICE_ID  "CAM-front"

// ─── UDP broadcast sync ───────────────────────────────────────────────────────
// Must match CAMERA_SYNC_PORT in the Pi's .env
#define SYNC_UDP_PORT  5005

// ─── NTP ──────────────────────────────────────────────────────────────────────
#define NTP_SERVER      "pool.ntp.org"
#define NTP_GMT_OFFSET  10800   // UTC+3 Turkey, no DST (seconds)
#define NTP_DST_OFFSET  0

// ─── Frame settings ───────────────────────────────────────────────────────────
#define FRAME_SIZE    FRAMESIZE_VGA   // 640×480  (requires PSRAM)
#define JPEG_QUALITY  12              // 0 = best quality, 63 = worst
