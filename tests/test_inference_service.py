from unittest.mock import MagicMock

import numpy as np

from src.services.inference_service import InferenceService


def _svc(**kw):
    svc = InferenceService(model_path="dummy.pt", **kw)
    svc._model = MagicMock()          # skip real YOLO load
    svc._model.predict.return_value = []   # no results → crowd_count 0
    return svc


def test_passes_conf_and_iou_to_predict():
    svc = _svc(confidence_threshold=0.4, iou_threshold=0.3)
    svc.count_crowd(np.zeros((640, 640, 3), np.uint8))
    _, kwargs = svc._model.predict.call_args
    assert kwargs["conf"] == 0.4
    assert kwargs["iou"] == 0.3


def test_default_iou_is_tighter_than_ultralytics():
    # Guards against silently reverting to Ultralytics' loose 0.7 default.
    assert _svc().iou_threshold == 0.45
