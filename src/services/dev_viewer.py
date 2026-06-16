"""
dev_viewer.py — DEV-ONLY live detection viewer.

Holds the most recent annotated frame per camera and serves them as MJPEG
streams (multipart/x-mixed-replace) so you can watch YOLO detections in a
browser while testing. Never imported or instantiated when APP_ENV=prod.

Why MJPEG-over-HTTP instead of a PyQt / cv2.imshow desktop window:
  * The project ships opencv-python-headless — no GUI/imshow support.
  * A desktop GUI event loop fights uvicorn's asyncio loop.
  * MJPEG works headless: open it in any browser, including against the Pi.
No extra dependencies — only cv2.imencode + FastAPI's StreamingResponse.
"""

import asyncio
import logging
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Cap the MJPEG output rate. Pushing parts back-to-back faster than the browser
# can decode makes the <img> stall or show torn frames; ~15 fps renders smoothly
# and is plenty for a detection viewer (the pipeline produces ~0.5 fps anyway).
_MAX_STREAM_FPS = 15
_MIN_FRAME_INTERVAL = 1.0 / _MAX_STREAM_FPS


class DevViewer:
    def __init__(self) -> None:
        self._frames: dict[str, bytes] = {}   # device_id → latest annotated JPEG
        self._seqs:   dict[str, int]   = {}    # device_id → frame counter
        self._cond = asyncio.Condition()

    def devices(self) -> list[str]:
        return sorted(self._frames)

    async def update(
        self,
        device_id: str,
        img: np.ndarray,
        detections: list[dict],
        count: int,
    ) -> None:
        """Annotate `img` with detection boxes and publish it to subscribers."""
        frame = self._annotate(img, detections, device_id, count)
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            logger.warning("[DevViewer] failed to JPEG-encode frame for %s", device_id)
            return
        async with self._cond:
            self._frames[device_id] = buf.tobytes()
            self._seqs[device_id] = self._seqs.get(device_id, 0) + 1
            self._cond.notify_all()

    async def stream(self, device_id: str):
        """Yield one MJPEG part each time a new frame arrives for device_id.

        Output is paced to _MAX_STREAM_FPS so a burst of frames can't outrun the
        browser's decoder; intermediate frames are coalesced (only the latest is
        sent). Each part carries Content-Length so the client can delimit frames
        without scanning for the next boundary.
        """
        last_seq = -1
        last_emit = 0.0
        while True:
            async with self._cond:
                await self._cond.wait_for(
                    lambda: device_id in self._frames
                    and self._seqs.get(device_id) != last_seq
                )
                last_seq = self._seqs[device_id]
                frame = self._frames[device_id]

            # Pace output: if frames are arriving faster than the cap, wait — the
            # next loop then grabs the latest frame, so we never fall behind.
            gap = _MIN_FRAME_INTERVAL - (time.monotonic() - last_emit)
            if gap > 0:
                await asyncio.sleep(gap)
            last_emit = time.monotonic()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                + frame + b"\r\n"
            )

    @staticmethod
    def _annotate(
        img: np.ndarray, detections: list[dict], device_id: str, count: int
    ) -> np.ndarray:
        canvas = img.copy()
        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                canvas, f"{det['class']} {det['confidence']:.2f}",
                (x1, max(12, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1, cv2.LINE_AA,
            )
        banner = f"{device_id}   count={count}"
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 24), (0, 0, 0), -1)
        cv2.putText(
            canvas, banner, (6, 17),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA,
        )
        return canvas
