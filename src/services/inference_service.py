import logging
import threading
import time

import numpy as np
from ultralytics import YOLO

from src.core.custom_modules import register_custom_modules

register_custom_modules()

logger = logging.getLogger(__name__)


class InferenceService:
    def __init__(
        self,
        model_path: str,
        confidence_threshold: float = 0.5,
        iou_threshold: float = 0.45,
    ):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        # NMS IoU threshold. Ultralytics defaults to 0.7 (loose), which lets two or
        # three boxes on the SAME head survive → over-counting. A tighter 0.45
        # merges those duplicates. Lower it further if heads are still double-counted.
        self.iou_threshold = iou_threshold
        self._model = None  # loaded on first use
        self._lock = threading.Lock()

    @property
    def model(self) -> YOLO:
        if self._model is None:
            logger.info("Loading YOLO model from %s", self.model_path)
            self._model = YOLO(self.model_path)
            logger.info("YOLO model loaded successfully: %s", self.model_path)
        return self._model

    def count_crowd(self, img: np.ndarray) -> dict:
        """
        Run YOLO on the preprocessed image and return crowd count + detections.

        Args:
            img: BGR ndarray (already enhanced and letterboxed)

        Returns:
            {
                "crowd_count": int,
                "detections": [{"bbox": [x1,y1,x2,y2], "confidence": float, "class": str}]
            }
        """
        with self._lock:
            current_model = self.model

        t0 = time.perf_counter()
        results = current_model.predict(
            img,
            conf=self.confidence_threshold,
            iou=self.iou_threshold,
            verbose=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        detections = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append({
                    "bbox": [round(x1), round(y1), round(x2), round(y2)],
                    "confidence": round(float(box.conf[0]), 3),
                    "class": result.names[int(box.cls[0])],
                })

        crowd_count = len(detections)
        logger.info(
            "Inference: crowd=%d  conf_threshold=%.2f  iou=%.2f  elapsed=%.1f ms  img=%dx%d",
            crowd_count, self.confidence_threshold, self.iou_threshold, elapsed_ms,
            img.shape[1], img.shape[0],
        )
        if elapsed_ms > 2000:
            logger.warning(
                "Inference took %.1f ms — Pi 4 may be overloaded (CPU threads, thermal throttle?)",
                elapsed_ms,
            )

        return {
            "crowd_count": crowd_count,
            "detections": detections,
        }

