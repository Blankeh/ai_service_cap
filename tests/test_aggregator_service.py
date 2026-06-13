from unittest.mock import AsyncMock
import pytest

from src.services.aggregator_service import AggregatorService
from src.services.cloudflare_service import CloudflareService

BUS    = 34
BUS_KEY = "34"
TS     = "2024-01-01T00:00:00+00:00"


@pytest.fixture()
def cf_svc() -> AsyncMock:
    svc = AsyncMock(spec=CloudflareService)
    svc.send.return_value = True
    return svc


@pytest.fixture()
def agg(cf_svc) -> AggregatorService:
    return AggregatorService(
        cloudflare_svc=cf_svc,
        flush_interval=60,
        spike_threshold=5,
    )


class TestPush:
    async def test_push_stores_count(self, agg):
        await agg.push(BUS, 5, TS)
        assert agg._buffers[BUS_KEY].counts == [5]

    async def test_push_multiple_readings(self, agg):
        for count in [3, 5, 7]:
            await agg.push(BUS, count, TS)
        assert agg._buffers[BUS_KEY].counts == [3, 5, 7]

    async def test_push_default_status_is_active(self, agg):
        await agg.push(BUS, 5, TS)
        assert agg._buffers[BUS_KEY].latest_status == "ACTIVE"

    async def test_push_error_status_stored(self, agg):
        await agg.push(BUS, 0, TS, camera_status="ERROR")
        assert agg._buffers[BUS_KEY].latest_status == "ERROR"


class TestFlush:
    async def test_flush_sends_latest_count(self, agg, cf_svc):
        for count in [2, 4, 9]:
            await agg.push(BUS, count, TS)
        await agg.flush()
        cf_svc.send.assert_awaited_once()
        payload = cf_svc.send.call_args.args[0]
        assert payload["passengerCount"] == 9   # latest, not average

    async def test_flush_clears_buffer(self, agg, cf_svc):
        await agg.push(BUS, 5, TS)
        await agg.flush()
        assert agg._buffers[BUS_KEY].counts == []

    async def test_flush_one_payload_per_bus(self, agg, cf_svc):
        await agg.push(34, 3, TS)
        await agg.push(35, 7, TS)
        await agg.flush()
        assert cf_svc.send.await_count == 2

    async def test_flush_empty_buffer_does_nothing(self, agg, cf_svc):
        await agg.flush()
        cf_svc.send.assert_not_awaited()

    async def test_flush_after_second_flush_does_nothing(self, agg, cf_svc):
        await agg.push(BUS, 5, TS)
        await agg.flush()
        cf_svc.send.reset_mock()
        await agg.flush()
        cf_svc.send.assert_not_awaited()

    async def test_payload_has_required_fields(self, agg, cf_svc):
        await agg.push(BUS, 4, TS)
        await agg.flush()
        payload = cf_svc.send.call_args.args[0]
        for key in ("cameraId", "busId", "cameraStatus", "busStatus",
                    "timestamp", "passengerCount"):
            assert key in payload, f"Missing field: {key}"

    async def test_payload_drops_route_and_driver(self, agg, cf_svc):
        await agg.push(BUS, 4, TS)
        await agg.flush()
        payload = cf_svc.send.call_args.args[0]
        assert "route" not in payload
        assert "driverName" not in payload

    async def test_bus_status_is_running(self, agg, cf_svc):
        await agg.push(BUS, 4, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["busStatus"] == "RUNNING"

    async def test_camera_status_error_propagated(self, agg, cf_svc):
        await agg.push(BUS, 0, TS, camera_status="ERROR")
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["cameraStatus"] == "ERROR"

    async def test_bus_level_camera_id(self, agg, cf_svc):
        await agg.push(BUS, 4, TS)
        await agg.flush()
        # Default template "CAM-BUS{bus}-{pos}", pos="ALL"
        assert cf_svc.send.call_args.args[0]["cameraId"] == "CAM-BUS34-ALL"

    async def test_bus_id_in_payload(self, agg, cf_svc):
        await agg.push(BUS, 4, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["busId"] == 34

    async def test_passenger_count_clamped_to_500(self, agg, cf_svc):
        await agg.push(BUS, 600, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["passengerCount"] == 500

    async def test_passenger_count_clamped_to_0(self, agg, cf_svc):
        await agg.push(BUS, -5, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["passengerCount"] == 0

    async def test_timestamp_has_turkey_offset(self, agg, cf_svc):
        await agg.push(BUS, 4, TS)
        await agg.flush()
        ts = cf_svc.send.call_args.args[0]["timestamp"]
        assert "+03:00" in ts, f"Timestamp missing Turkey TZ offset: {ts}"


class TestSpikeDetection:
    async def test_spike_triggers_immediate_flush(self, agg, cf_svc):
        agg._last_sent[BUS_KEY] = 5
        await agg.push(BUS, 11, TS)   # Δ6 ≥ threshold 5
        cf_svc.send.assert_awaited_once()

    async def test_small_change_does_not_spike(self, agg, cf_svc):
        agg._last_sent[BUS_KEY] = 5
        await agg.push(BUS, 8, TS)   # Δ3 < threshold 5
        cf_svc.send.assert_not_awaited()

    async def test_no_spike_on_first_reading(self, agg, cf_svc):
        await agg.push(BUS, 50, TS)
        cf_svc.send.assert_not_awaited()

    async def test_spike_updates_last_sent(self, agg, cf_svc):
        agg._last_sent[BUS_KEY] = 2
        await agg.push(BUS, 20, TS)
        assert agg._last_sent[BUS_KEY] == 20

    async def test_drop_spike_also_triggers(self, agg, cf_svc):
        agg._last_sent[BUS_KEY] = 20
        await agg.push(BUS, 5, TS)   # Δ-15 > threshold
        cf_svc.send.assert_awaited_once()
