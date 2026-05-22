import logging
import threading
import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)


class InferenceService:
    def __init__(self, model_path: str, confidence_threshold: float = 0.5):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self._model = None  # loaded on first use
        self._lock = threading.Lock()

    @property
    def model(self) -> YOLO:
        if self._model is None:
            logger.info(f"Loading YOLO model from {self.model_path}")
            self._model = YOLO(self.model_path)
        return self._model

    def reload(self, new_path: str) -> None:
        """Hot-swap the model. Validates the new model before replacing."""
        logger.info(f"Loading replacement model from {new_path}")
        new_model = YOLO(new_path)  # raises if the file is invalid
        with self._lock:
            self._model = new_model
            self.model_path = new_path
        logger.info(f"Model reloaded: {new_path}")

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
        results = current_model.predict(
            img,
            conf=self.confidence_threshold,
            verbose=False,
        )

        detections = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append({
                    "bbox": [round(x1), round(y1), round(x2), round(y2)],
                    "confidence": round(float(box.conf[0]), 3),
                    "class": result.names[int(box.cls[0])],
                })

        return {
            "crowd_count": len(detections),
            "detections": detections,
        }
