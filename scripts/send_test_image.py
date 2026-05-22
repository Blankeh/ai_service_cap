"""
Quick end-to-end test: handshake → assign → upload a real image.

Usage (from ai_service/):
    python scripts/send_test_image.py
    python scripts/send_test_image.py --image path/to/photo.jpg
    python scripts/send_test_image.py --url http://192.168.1.100:8000
"""

import argparse
import io
import sys
import httpx
import numpy as np
import cv2


BASE_URL   = "http://localhost:8000/api/v1"
CAMERA_ID  = "TEST-CAM-01"
BUS_ID     = "BUS-001"
PANE       = "front"


def make_test_jpeg() -> bytes:
    """Generate a 640x480 image with random shapes — no real people but tests the full pipeline."""
    img = np.ones((480, 640, 3), dtype=np.uint8) * 180
    for _ in range(8):
        x, y = np.random.randint(50, 580), np.random.randint(50, 420)
        cv2.rectangle(img, (x, y), (x + 40, y + 80),
                      tuple(int(c) for c in np.random.randint(0, 200, 3).tolist()), -1)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


def run(base_url: str, image_path: str | None):
    print(f"Target: {base_url}\n")

    # ── 1. Handshake ──────────────────────────────────────────────────────────
    print("1. Handshake...")
    r = httpx.post(f"{base_url}/handshake", json={"camera_id": CAMERA_ID})
    r.raise_for_status()
    token = r.json()["token"]
    print(f"   token    : {token[:16]}...")
    print(f"   assigned : {r.json()['assigned']}")

    # ── 2. Assign ─────────────────────────────────────────────────────────────
    print("\n2. Assigning camera to bus/pane...")
    r = httpx.post(
        f"{base_url}/cameras/{CAMERA_ID}/assign",
        json={"bus_id": BUS_ID, "pane": PANE},
    )
    r.raise_for_status()
    print(f"   {r.json()}")

    # ── 3. Load image ─────────────────────────────────────────────────────────
    if image_path:
        print(f"\n3. Loading image from {image_path}...")
        with open(image_path, "rb") as f:
            jpeg_bytes = f.read()
    else:
        print("\n3. No image provided — generating synthetic test JPEG...")
        jpeg_bytes = make_test_jpeg()

    print(f"   size: {len(jpeg_bytes):,} bytes")

    # ── 4. Upload ─────────────────────────────────────────────────────────────
    print("\n4. Uploading frame...")
    r = httpx.post(
        f"{base_url}/upload",
        headers={"X-Camera-Token": token},
        files={"file": ("frame.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()

    print("\n-- Result ---------------------------------------")
    print(f"   camera_id : {body['camera_id']}")
    print(f"   bus_id    : {body['bus_id']}")
    print(f"   pane      : {body['pane']}")
    print(f"   bucket    : {body['bucket']}")
    print(f"   received  : {body['received']}")
    print(f"   expected  : {body['expected']}")
    print(f"   grouped   : {body['grouped']}")
    print("\n   (inference runs async — check server logs for crowd_count)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",   default=BASE_URL,  help="API base URL")
    parser.add_argument("--image", default=None,      help="Path to a JPEG image")
    args = parser.parse_args()

    try:
        run(args.url, args.image)
    except httpx.HTTPStatusError as e:
        print(f"\nHTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        print(f"\nCould not connect to {args.url} — is the server running?")
        sys.exit(1)
