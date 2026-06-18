# Bus Crowd AI Service

Edge inference service for real-time bus passenger counting. Runs on a
**Raspberry Pi 4 (4 GB)** mounted on a bus, receives synchronized JPEG frames
from one or more **ESP32-CAM** modules over the LAN, counts people with a YOLOv8
model, and reports an aggregated per-bus passenger count to a Cloudflare backend.

This is the **deployment / inference half** of the capstone pipeline. The model
it runs is produced by its sibling project, [`train_service_cap`](../train_service_cap).

---

## How it works

```
  ESP32-CAM(s)                 Raspberry Pi 4 (this service)                Cloudflare
 ┌───────────┐   JPEG POST    ┌─────────────────────────────────┐   JSON    ┌──────────┐
 │ CAM-front │ ─────────────▶ │ /api/v1/upload                  │           │ backend  │
 │ CAM-mid   │ ─────────────▶ │   → FrameGrouper (NTP buckets)  │           │  API     │
 │ CAM-rear  │ ─────────────▶ │   → ImageService (CLAHE+resize) │ ────────▶ │ /device/ │
 └───────────┘                │   → InferenceService (YOLO)     │  bearer   │  input   │
       ▲                      │   → ROI filter + per-bus sum    │  token    └──────────┘
       │ UDP capture trigger  │   → AggregatorService (flush)   │
       └──────────────────────│ CameraSyncService (broadcast)   │
                              └─────────────────────────────────┘
```

1. **Camera sync** — `CameraSyncService` broadcasts a UDP capture trigger to the
   LAN every `CAMERA_SYNC_INTERVAL_SECONDS`. Every ESP32-CAM fires at once, so
   all frames in a round share one timestamp.
2. **Upload** — each camera POSTs its JPEG to `/api/v1/upload` with a `device_id`
   (e.g. `CAM-front`), an `X-Captured-At` NTP timestamp, and an `X-Sync-Round`
   header echoing the trigger id.
3. **Grouping** — `FrameGrouper` buckets frames from the same bus/round together.
   A round is processed once all `EXPECTED_CAMERAS` frames arrive (early-fire) or
   the `GROUP_WINDOW_MS` deadline expires, whichever comes first. Late stragglers
   are dropped so a partial count can't overwrite a good one.
4. **Enhance + infer** — `ImageService` decodes the JPEG, applies CLAHE contrast
   enhancement (for varied bus lighting) and a light bilateral denoise, then
   letterboxes to the YOLO input size. `InferenceService` runs the model.
5. **ROI counting** — each camera covers a distinct zone of the bus. `roi_service`
   counts only detections whose center falls inside that camera's normalized ROI
   box, then sums the per-camera counts into one combined value for the bus.
6. **Aggregate + report** — `AggregatorService` buffers the per-bus count and
   flushes the latest value to Cloudflare every `AGGREGATOR_FLUSH_INTERVAL`
   seconds. A change larger than `AGGREGATOR_SPIKE_THRESHOLD` flushes immediately.
   `AuthService` obtains a bearer token at startup and re-logs in automatically on
   a 401.

---

## Project layout

```
ai_service_cap/
├── src/
│   ├── main.py                  FastAPI app + lifespan wiring of all services
│   ├── api/
│   │   ├── router.py            mounts the v1 API
│   │   ├── v1/routes.py         POST /upload, GET /health
│   │   ├── roi_routes.py        ROI calibration editor (dev AND prod)
│   │   └── dev_routes.py        live MJPEG detection viewer (dev only)
│   ├── services/
│   │   ├── grouper_service.py   FrameGrouper — NTP-bucket frame grouping
│   │   ├── image_service.py     decode + CLAHE + denoise + letterbox
│   │   ├── inference_service.py YOLO model load + crowd count
│   │   ├── roi_service.py       per-pane ROI resolution + in-ROI counting
│   │   ├── roi_store.py         persists ROIs to data/camera_rois.json
│   │   ├── snapshot_store.py    latest frame per camera (for the ROI editor)
│   │   ├── aggregator_service.py buffer + flush per-bus counts
│   │   ├── cloudflare_service.py POST payloads to the backend (with retry)
│   │   ├── auth_service.py      device login + bearer-token management
│   │   ├── camera_sync_service.py UDP broadcast capture trigger
│   │   └── dev_viewer.py        in-memory frame buffer for /dev
│   ├── core/
│   │   ├── config.py            pydantic-settings (.env) configuration
│   │   ├── custom_modules.py    registers custom YOLO modules (CBAM/P2)
│   │   └── logging.py           rotating file + console logging
│   └── repos/                   SQLite (SQLModel) camera persistence
├── esp32_cam/                   PlatformIO firmware for the ESP32-CAM
├── deploy/                      systemd unit + Pi install/uninstall scripts
├── scripts/                    db CLI, test-image sender, firewall helpers
├── tests/                      pytest suite
├── requirements.txt
├── .env.example
└── LICENSE                      GNU GPL v3
```

---

## Requirements

- Python 3.10+
- Raspberry Pi 4 (4 GB) for deployment, or any Linux/Windows/macOS host for dev
- See [`requirements.txt`](requirements.txt) — FastAPI, Uvicorn, Ultralytics,
  ONNX Runtime / NCNN, OpenCV (headless), pydantic-settings, SQLModel

The trained model is run via ONNX Runtime by default (`MODEL_PATH=*.onnx`). On
the Pi, an **NCNN** export is ~3–5× faster on ARM — point `MODEL_PATH` at the
exported `best_ncnn_model` directory instead.

## Setup

```bash
cd ai_service_cap
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env        # then edit — see Configuration below
```

## Running

```bash
# Development (live detection viewer enabled at /dev)
APP_ENV=dev python -m src.main

# Production
APP_ENV=prod python -m src.main
```

The service listens on `HOST:PORT` (default `0.0.0.0:8000`).

### Run on boot (Raspberry Pi)

A one-command systemd installer is provided. It installs prerequisites, builds a
CPU-only venv, configures a persistent capped journal, and enables the service:

```bash
sudo bash deploy/install_service.sh
```

See [`deploy/README.md`](deploy/README.md) for management commands
(`systemctl status / restart / logs`) and uninstall.

---

## Configuration

All configuration is via `.env` (loaded by `src/core/config.py`). The full,
commented reference is in [`.env.example`](.env.example). Key settings:

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `prod` | `dev` exposes the `/dev` MJPEG viewer; `prod` never imports it |
| `MODEL_PATH` | `yolov8n.pt` | YOLO weights — `.onnx` (dev) or NCNN dir (Pi) |
| `YOLO_INPUT_SIZE` | `640` | Inference resolution (640 chosen over 320 for accuracy) |
| `CONFIDENCE_THRESHOLD` | `0.35` | Detection confidence floor |
| `BUS_ID` | `0` | Numeric id of this Pi's bus |
| `EXPECTED_CAMERAS` | `0` | When >0, a round early-fires once this many frames arrive |
| `GROUP_WINDOW_MS` | `1000` | Deadline after the first frame of a round |
| `GROUP_BUCKET_SIZE` | `2` | NTP bucket size (must match camera capture cadence) |
| `CAMERA_ROIS` | `{}` | JSON map of pane → normalized `[x1,y1,x2,y2]` ROI box |
| `AGGREGATOR_FLUSH_INTERVAL` | `60` | Seconds between scheduled Cloudflare flushes |
| `AGGREGATOR_SPIKE_THRESHOLD` | `5` | Immediate flush if the count jumps by this many |
| `CLOUDFLARE_API_URL` | `""` | Backend base URL — empty = dummy mode (payloads only logged) |
| `DEVICE_USERNAME` / `DEVICE_PASSWORD` | — | Credentials for the device login endpoint |
| `CAMERA_SYNC_*` | — | UDP broadcast capture-trigger settings |

> **Dummy mode:** leave `CLOUDFLARE_API_URL` empty to run the full pipeline
> locally without a backend — payloads are validated and logged but not sent.

---

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/upload` | Receive a JPEG frame (multipart `file` + `device_id` form field; `X-Captured-At` / `X-Sync-Round` headers) |
| `GET`  | `/api/v1/health` | Liveness + active model path |
| `GET`  | `/roi/` | Browser ROI calibration editor (dev **and** prod) |
| `GET`  | `/roi/rois` | Current ROIs, known panes, and calibration advisories |
| `PUT`/`DELETE` | `/roi/rois/{pane}` | Save / clear one pane's ROI box |
| `GET`  | `/dev` | Live MJPEG detection viewer (**dev only**) |

> **Security note:** the ROI editor's write endpoints are unauthenticated by
> design — the Pi is assumed to sit on a trusted LAN. If that assumption changes,
> gate them behind the device token or bind the editor to localhost (see the
> header of `src/api/roi_routes.py`).

### ROI calibration

Each camera should cover a distinct, non-overlapping zone of the bus so people
aren't double-counted (overlap) or missed (gaps). The easiest way to set this up
is the live editor at `http://<pi>:<port>/roi` — drag a box / use sliders over
the real camera image with a live in-box head count, then **Save**. Saved boxes
go to `data/camera_rois.json`, which **overrides** `CAMERA_ROIS` at startup and
takes effect on the next processed round (no restart).

---

## ESP32-CAM firmware

[`esp32_cam/`](esp32_cam) is a PlatformIO project (`ai-thinker-esp32-cam` board)
for the camera nodes. Each camera:

- listens for the Pi's UDP capture trigger and learns the Pi's IP from it
  (survives DHCP changes; off-subnet triggers are ignored for safety),
- captures a VGA JPEG and POSTs it to `/api/v1/upload` with its `DEVICE_ID`,
- syncs time over NTP and echoes the trigger id back as `X-Sync-Round`.

Copy `include/config_example.h` to `include/config.h` and set WiFi credentials,
`DEVICE_ID` (e.g. `CAM-front` / `CAM-mid` / `CAM-rear`), and mounting flip flags,
then build/flash with PlatformIO.

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/send_test_image.py` | End-to-end smoke test — POST a real JPEG (optionally simulating multiple panes) to a running service |
| `scripts/db_cli.py` | Manage the SQLite camera table (`init`, `cameras list/add/assign`) |
| `scripts/firewall_*.{sh,ps1}` | Open/close the service port on Linux / Windows |

## Testing

```bash
pytest
```

The suite covers the aggregator, grouper, ROI, image, camera, auth, Cloudflare,
and upload-endpoint behavior.

---

## License

This project is licensed under the **GNU General Public License v3.0**. See
[`LICENSE`](LICENSE) for the full text.
