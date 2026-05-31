from unittest.mock import AsyncMock
import pytest

from src.services.aggregator_service import AggregatorService
from src.services.cloudflare_service import CloudflareService

BUS_A  = "BUS-001"
BUS_B  = "BUS-002"
TS     = "2024-01-01T00:00:00+00:00"
PANES  = ["front", "rear"]


@pytest.fixture()
def cf_svc() -> AsyncMock:
    svc = AsyncMock(spec=CloudflareService)
    svc.send.return_value = True
    return svc


@pytest.fixture()
def agg(cf_svc) -> AggregatorService:
    return AggregatorService(cloudflare_svc=cf_svc, flush_interval=60, spike_threshold=5)


class TestPush:
    async def test_push_stores_count(self, agg):
        await agg.push(BUS_A, PANES, 5, TS)
        assert agg._buffers[BUS_A].counts == [5]

    async def test_push_multiple_readings(self, agg):
        for count in [3, 5, 7]:
            await agg.push(BUS_A, PANES, count, TS)
        assert agg._buffers[BUS_A].counts == [3, 5, 7]

    async def test_push_separate_buses(self, agg):
        await agg.push(BUS_A, PANES, 3, TS)
        await agg.push(BUS_B, PANES, 8, TS)
        assert BUS_A in agg._buffers
        assert BUS_B in agg._buffers


class TestFlush:
    async def test_flush_sends_latest_count(self, agg, cf_svc):
        for count in [2, 4, 9]:
            await agg.push(BUS_A, PANES, count, TS)
        await agg.flush()
        cf_svc.send.assert_awaited_once()
        payload = cf_svc.send.call_args.args[0]
        assert payload["crowd_count"] == 9   # latest, not average

    async def test_flush_clears_buffer(self, agg, cf_svc):
        await agg.push(BUS_A, PANES, 5, TS)
        await agg.flush()
        assert agg._buffers[BUS_A].counts == []

    async def test_flush_sends_each_bus_once(self, agg, cf_svc):
        await agg.push(BUS_A, PANES, 3, TS)
        await agg.push(BUS_B, PANES, 7, TS)
        await agg.flush()
        assert cf_svc.send.await_count == 2

    async def test_flush_empty_buffer_does_nothing(self, agg, cf_svc):
        await agg.flush()
        cf_svc.send.assert_not_awaited()

    async def test_flush_after_second_flush_does_nothing(self, agg, cf_svc):
        await agg.push(BUS_A, PANES, 5, TS)
        await agg.flush()
        cf_svc.send.reset_mock()
        await agg.flush()
        cf_svc.send.assert_not_awaited()

    async def test_payload_has_required_fields(self, agg, cf_svc):
        await agg.push(BUS_A, PANES, 4, TS)
        await agg.flush()
        payload = cf_svc.send.call_args.args[0]
        for key in ("group_id", "bus_id", "timestamp", "panes", "crowd_count", "sample_count"):
            assert key in payload, f"Missing field: {key}"

    async def test_sample_count_reflects_readings(self, agg, cf_svc):
        for _ in range(5):
            await agg.push(BUS_A, PANES, 3, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["sample_count"] == 5


class TestSpikeDetection:
    async def test_spike_triggers_immediate_flush(self, agg, cf_svc):
        # Establish baseline
        agg._last_sent[BUS_A] = 5
        # Push a count that exceeds spike_threshold (5)
        await agg.push(BUS_A, PANES, 11, TS)
        # Should have flushed immediately without waiting for the 60s timer
        cf_svc.send.assert_awaited_once()

    async def test_small_change_does_not_spike(self, agg, cf_svc):
        agg._last_sent[BUS_A] = 5
        await agg.push(BUS_A, PANES, 8, TS)   # Δ3 < threshold 5
        cf_svc.send.assert_not_awaited()

    async def test_no_spike_on_first_reading(self, agg, cf_svc):
        # No last_sent baseline yet — should never spike on first push
        await agg.push(BUS_A, PANES, 50, TS)
        cf_svc.send.assert_not_awaited()

    async def test_spike_updates_last_sent(self, agg, cf_svc):
        agg._last_sent[BUS_A] = 2
        await agg.push(BUS_A, PANES, 20, TS)
        assert agg._last_sent[BUS_A] == 20

    async def test_drop_spike_also_triggers(self, agg, cf_svc):
        agg._last_sent[BUS_A] = 20
        await agg.push(BUS_A, PANES, 5, TS)   # Δ-15 > threshold
        cf_svc.send.assert_awaited_once()
