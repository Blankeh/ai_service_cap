"""
add_frame grouping behaviour: sync-round grouping (Fix 1) and early-fire
(Fix 3). The existing test_roi_service.py covers _process/ROI summing; this
focuses on how frames are keyed into groups and when processing fires.
"""
from unittest.mock import AsyncMock, MagicMock

import numpy as np

from src.services.grouper_service import FrameGrouper

META = {"pad_left": 0, "pad_top": 0, "new_w": 640, "new_h": 640, "scale": 1.0}


def _det(cx, cy):
    return {"bbox": [cx - 5, cy - 5, cx + 5, cy + 5], "confidence": 0.9, "class": "person"}


def _make_grouper(expected_cameras=0, group_window_ms=10_000):
    """Grouper with mocked services; huge window so only early-fire can process."""
    image_svc = MagicMock()
    image_svc.enhance_with_meta.return_value = (np.zeros((640, 640, 3), np.uint8), META)
    inference_svc = MagicMock()
    inference_svc.count_crowd.return_value = {
        "crowd_count": 2,
        "detections": [_det(100, 100), _det(100, 500)],
    }
    aggregator = MagicMock()
    aggregator.push = AsyncMock()
    grouper = FrameGrouper(
        image_svc=image_svc,
        inference_svc=inference_svc,
        aggregator_svc=aggregator,
        group_window_ms=group_window_ms,
        bucket_size=2,
        expected_cameras=expected_cameras,
    )
    return grouper, aggregator


def _cancel_pending_timers(grouper):
    """Avoid 'task destroyed but pending' warnings for groups left open."""
    for group in grouper._groups.values():
        if group.timer_task is not None and not group.timer_task.done():
            group.timer_task.cancel()


class TestSyncRoundGrouping:
    async def test_same_round_groups_across_bucket_boundary(self):
        # captured_at 1001 and 1002 would fall in buckets 1000 and 1002, but the
        # shared sync_round keeps them in ONE group. expected_cameras=2 makes the
        # 2nd frame early-fire so we can assert a single combined push.
        grouper, agg = _make_grouper(expected_cameras=2)
        await grouper.add_frame("34", "CAM-front", b"x", "ts", captured_at=1001, sync_round=777)
        agg.push.assert_not_awaited()   # only one camera so far
        await grouper.add_frame("34", "CAM-rear", b"x", "ts", captured_at=1002, sync_round=777)

        agg.push.assert_awaited_once()
        _bus, count, _ts, _status = agg.push.call_args.args
        assert count == 4               # 2 dets × 2 cameras, full frame, one group
        assert grouper._groups == {}    # group consumed

    async def test_without_sync_round_boundary_splits(self):
        # No X-Sync-Round → fall back to captured_at buckets. 1001→1000, 1002→1002
        # are different groups, so with expected_cameras=2 neither early-fires.
        grouper, agg = _make_grouper(expected_cameras=2)
        await grouper.add_frame("34", "CAM-front", b"x", "ts", captured_at=1001)
        await grouper.add_frame("34", "CAM-rear", b"x", "ts", captured_at=1002)

        agg.push.assert_not_awaited()       # split across two buckets, neither full
        assert len(grouper._groups) == 2    # the boundary-split bug, fallback path
        _cancel_pending_timers(grouper)


class TestEarlyFire:
    async def test_processes_when_expected_reached(self):
        grouper, agg = _make_grouper(expected_cameras=3)
        await grouper.add_frame("34", "CAM-front", b"x", "ts", sync_round=5)
        await grouper.add_frame("34", "CAM-mid", b"x", "ts", sync_round=5)
        agg.push.assert_not_awaited()       # 2 of 3 — still waiting
        await grouper.add_frame("34", "CAM-rear", b"x", "ts", sync_round=5)
        agg.push.assert_awaited_once()      # 3rd arrival fires before the deadline
        assert grouper._groups == {}

    async def test_disabled_when_expected_zero(self):
        # expected_cameras=0 → never early-fire; group stays open for the deadline.
        grouper, agg = _make_grouper(expected_cameras=0)
        await grouper.add_frame("34", "CAM-front", b"x", "ts", sync_round=5)
        await grouper.add_frame("34", "CAM-rear", b"x", "ts", sync_round=5)
        agg.push.assert_not_awaited()
        assert len(grouper._groups) == 1
        _cancel_pending_timers(grouper)


class TestStragglerGuard:
    async def test_late_frame_after_processing_is_dropped(self):
        # Round of 2 cameras early-fires and pushes the combined count. A 3rd
        # camera then replies late for the SAME round — it must be dropped, not
        # re-opened into a new partial group that overwrites the good count.
        grouper, agg = _make_grouper(expected_cameras=2)
        await grouper.add_frame("34", "CAM-front", b"x", "ts", sync_round=9)
        await grouper.add_frame("34", "CAM-rear", b"x", "ts", sync_round=9)
        agg.push.assert_awaited_once()          # round already processed

        res = await grouper.add_frame("34", "CAM-mid", b"x", "ts", sync_round=9)
        assert res["received"] == []            # dropped
        agg.push.assert_awaited_once()          # NO second (partial) push
        assert grouper._groups == {}            # no new group opened

    async def test_processed_history_is_bounded(self):
        from src.services.grouper_service import _PROCESSED_HISTORY
        grouper, _agg = _make_grouper(expected_cameras=1)
        # Each round of 1 camera processes immediately; push more than the cap.
        for r in range(_PROCESSED_HISTORY + 50):
            await grouper.add_frame("34", "CAM-front", b"x", "ts", sync_round=r)
        assert len(grouper._processed_keys) == _PROCESSED_HISTORY
        assert len(grouper._processed_set) == _PROCESSED_HISTORY
