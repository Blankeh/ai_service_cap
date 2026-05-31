import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ImageService:
    def __init__(self, target_size: int = 640):
        self.target_size = target_size
        # CLAHE for contrast enhancement under varied bus lighting
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    def enhance(self, raw_bytes: bytes) -> np.ndarray:
        """
        Decode JPEG from ESP32-CAM, enhance, and return BGR ndarray
        ready for YOLO inference.

        Pipeline:
          1. Decode JPEG
          2. CLAHE on luminance channel (preserves color, fixes low contrast)
          3. Bilateral filter (noise reduction, keeps person-detection edges)
          4. Resize to YOLO input size with letterboxing
        """
        img = self._decode(raw_bytes)
        img = self._apply_clahe(img)
        img = self._denoise(img)
        img = self._letterbox(img)
        return img

    def encode_jpeg(self, img: np.ndarray, quality: int = 85) -> bytes:
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decode(self, raw_bytes: bytes) -> np.ndarray:
        if not raw_bytes:
            logger.error("_decode: received empty byte buffer — cannot decode JPEG")
            raise ValueError("Failed to decode image — empty bytes")
        arr = np.frombuffer(raw_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            logger.error(
                "_decode: cv2.imdecode returned None for %d byte payload — likely corrupt JPEG",
                len(raw_bytes),
            )
            raise ValueError("Failed to decode image — invalid JPEG bytes")
        logger.debug("_decode: %d bytes → %dx%d BGR", len(raw_bytes), img.shape[1], img.shape[0])
        return img

    def _apply_clahe(self, img: np.ndarray) -> np.ndarray:
        # Work in LAB so CLAHE only touches luminance, not hue/saturation
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = self.clahe.apply(l)
        lab = cv2.merge([l, a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def _denoise(self, img: np.ndarray) -> np.ndarray:
        # d=5 is ~4× faster than d=9 on ARM Cortex-A72 with similar quality
        return cv2.bilateralFilter(img, d=5, sigmaColor=75, sigmaSpace=75)

    def _letterbox(self, img: np.ndarray) -> np.ndarray:
        """Resize with padding to maintain aspect ratio."""
        h, w = img.shape[:2]
        scale = self.target_size / max(h, w)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        canvas = np.full((self.target_size, self.target_size, 3), 114, dtype=np.uint8)
        pad_top = (self.target_size - new_h) // 2
        pad_left = (self.target_size - new_w) // 2
        canvas[pad_top : pad_top + new_h, pad_left : pad_left + new_w] = resized
        return canvas
