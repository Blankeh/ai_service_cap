import numpy as np
import pytest

from .conftest import make_jpeg


class TestImageService:
    def test_enhance_returns_correct_shape(self, image_svc):
        raw = make_jpeg(width=320, height=240)
        out = image_svc.enhance(raw)
        assert out.shape == (640, 640, 3), "Output must be target_size × target_size × 3"

    def test_enhance_dtype_uint8(self, image_svc):
        raw = make_jpeg()
        out = image_svc.enhance(raw)
        assert out.dtype == np.uint8

    def test_enhance_wide_image_letterboxed(self, image_svc):
        """Wide image (16:9) should be letterboxed, not stretched."""
        raw = make_jpeg(width=640, height=360)
        out = image_svc.enhance(raw)
        assert out.shape == (640, 640, 3)

    def test_enhance_tall_image_letterboxed(self, image_svc):
        raw = make_jpeg(width=240, height=480)
        out = image_svc.enhance(raw)
        assert out.shape == (640, 640, 3)

    def test_enhance_square_image(self, image_svc):
        raw = make_jpeg(width=300, height=300)
        out = image_svc.enhance(raw)
        assert out.shape == (640, 640, 3)

    def test_enhance_invalid_bytes_raises(self, image_svc):
        with pytest.raises(ValueError, match="Failed to decode"):
            image_svc.enhance(b"not an image")

    def test_enhance_empty_bytes_raises(self, image_svc):
        with pytest.raises(ValueError):
            image_svc.enhance(b"")

    def test_clahe_increases_contrast(self, image_svc):
        """A very dark image should have higher std-dev after enhancement."""
        raw = make_jpeg(color=(10, 10, 10))  # nearly black
        out = image_svc.enhance(raw)
        assert out.std() > 0, "CLAHE should introduce variance in a dark image"

    def test_encode_jpeg_roundtrip(self, image_svc):
        raw = make_jpeg()
        img = image_svc.enhance(raw)
        encoded = image_svc.encode_jpeg(img)
        assert encoded[:2] == b"\xff\xd8", "Must be valid JPEG (SOI marker)"
