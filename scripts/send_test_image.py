"""
End-to-end smoke test: send a real JPEG to the running ai_service.

Usage (from ai_service_cap/):
    python scripts/send_test_image.py
    python scripts/send_test_image.py --image path/to/photo.jpg
    python scripts/send_test_image.py --url http://192.168.1.100:8000
    python scripts/send_test_image.py --panes front rear   (simulate two cameras)
"""

import argparse
import io
import sys
import time
import httpx
import numpy as np
import cv2

BASE_URL  = "http://localhost:8000/api/v1"
CAMERA_ID = "TEST-CAM-01"
BUS_ID    = "BUS-001"
PANE      = "front"


def make_test_jpeg(width: int = 640, height: int = 480) -> bytes:
    """Synthetic JPEG — random rectangles simulating people-shaped blobs."""
    img = np.ones((height, width, 3), dtype=np.uint8) * 180
    for _ in range(8):
        x = np.random.randint(50, width  - 80)
        y = np.random.randint(50, height - 100)
        color = tuple(int(c) for c in np.random.randint(30, 200, 3).tolist())
        cv2.rectangle(img, (x, y), (x + 40, y + 80), color, -1)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


def upload(base_url: str, jpeg_bytes: bytes, camera_id: str, bus_id: str, pane: str,
           captured_at: int | None = None) -> dict:
    headers = {
        "X-Camera-Id": camera_id,
        "X-Bus-Id":    bus_id,
        "X-Pane":      pane,
    }
    if captured_at:
        headers["X-Captured-At"] = str(captured_at)

    r = httpx.post(
        f"{base_url}/upload",
        headers=headers,
        files={"file": ("frame.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def run(base_url: str, image_path: str | None, panes: list[str]):
    print(f"Target : {base_url}")
    print(f"Bus    : {BUS_ID}  panes={panes}\n")

    # ── Health check ──────────────────────────────────────────────────────────
    print("1. Health check...")
    r = httpx.get(f"{base_url}/health")
    r.raise_for_status()
    health = r.json()
    print(f"   status  : {health['status']}")
    print(f"   model   : {health['model']}")
    print(f"   queued  : {health['queued_records']}")

    # ── Load image ────────────────────────────────────────────────────────────
    if image_path:
        print(f"\n2. Loading image from {image_path}...")
        with open(image_path, "rb") as f:
            jpeg_bytes = f.read()
    else:
        print("\n2. Generating synthetic test JPEG (640x480)...")
        jpeg_bytes = make_test_jpeg()
    print(f"   size : {len(jpeg_bytes):,} bytes")

    # ── Upload (one per pane, same NTP bucket) ────────────────────────────────
    captured_at = int(time.time())
    print(f"\n3. Uploading {len(panes)} pane(s)  captured_at={captured_at}...")

    for i, pane in enumerate(panes):
        cam_id = f"TEST-CAM-{i+1:02d}"
        body   = upload(base_url, jpeg_bytes, cam_id, BUS_ID, pane, captured_at)
        print(f"\n   [{pane}]")
        print(f"     camera_id : {body['camera_id']}")
        print(f"     bucket    : {body['bucket']}")
        print(f"     received  : {body['received']}")

    print("\n   Inference runs async in the grouper — watch server logs for crowd_count.")
    print("   (Aggregator sends to Cloudflare every 60s or on spike)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ai_service smoke test")
    parser.add_argument("--url",   default=BASE_URL, help="API base URL")
    parser.add_argument("--image", default=None,     help="Path to a JPEG")
    parser.add_argument("--panes", nargs="+", default=[PANE], help="Pane names to simulate")
    args = parser.parse_args()

    try:
        run(args.url, args.image, args.panes)
    except httpx.HTTPStatusError as e:
        print(f"\nHTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        print(f"\nCould not connect to {args.url} — is the server running?")
        sys.exit(1)
