from unittest.mock import AsyncMock, MagicMock
import pytest

from src.services.aggregator_service import AggregatorService
from src.services.bus_info_service import BusInfo, BusInfoService
from src.services.cloudflare_service import CloudflareService

CAM_A = "CAM-front"
CAM_B = "CAM-rear"
TS    = "2024-01-01T00:00:00+00:00"


@pytest.fixture()
def cf_svc() -> AsyncMock:
    svc = AsyncMock(spec=CloudflareService)
    svc.send.return_value = True
    return svc


@pytest.fixture()
def bus_info_svc() -> MagicMock:
    svc = MagicMock(spec=BusInfoService)
    svc.current.return_value = BusInfo(
        bus_id=34,
        route="34A-Taksim",
        bus_status="RUNNING",
        driver_name="Mehmet Yilmaz",
    )
    return svc


@pytest.fixture()
def agg(cf_svc, bus_info_svc) -> AggregatorService:
    return AggregatorService(
        cloudflare_svc=cf_svc,
        bus_info_svc=bus_info_svc,
        flush_interval=60,
        spike_threshold=5,
    )


class TestPush:
    async def test_push_stores_count(self, agg):
        await agg.push(CAM_A, 5, TS)
        assert agg._buffers[CAM_A].counts == [5]

    async def test_push_multiple_readings(self, agg):
        for count in [3, 5, 7]:
            await agg.push(CAM_A, count, TS)
        assert agg._buffers[CAM_A].counts == [3, 5, 7]

    async def test_push_separate_cameras(self, agg):
        await agg.push(CAM_A, 3, TS)
        await agg.push(CAM_B, 8, TS)
        assert CAM_A in agg._buffers
        assert CAM_B in agg._buffers

    async def test_push_default_status_is_active(self, agg):
        await agg.push(CAM_A, 5, TS)
        assert agg._buffers[CAM_A].latest_status == "ACTIVE"

    async def test_push_error_status_stored(self, agg):
        await agg.push(CAM_A, 0, TS, camera_status="ERROR")
        assert agg._buffers[CAM_A].latest_status == "ERROR"


class TestFlush:
    async def test_flush_sends_latest_count(self, agg, cf_svc):
        for count in [2, 4, 9]:
            await agg.push(CAM_A, count, TS)
        await agg.flush()
        cf_svc.send.assert_awaited_once()
        payload = cf_svc.send.call_args.args[0]
        assert payload["passengerCount"] == 9   # latest, not average

    async def test_flush_clears_buffer(self, agg, cf_svc):
        await agg.push(CAM_A, 5, TS)
        await agg.flush()
        assert agg._buffers[CAM_A].counts == []

    async def test_flush_sends_each_camera_once(self, agg, cf_svc):
        await agg.push(CAM_A, 3, TS)
        await agg.push(CAM_B, 7, TS)
        await agg.flush()
        assert cf_svc.send.await_count == 2

    async def test_flush_empty_buffer_does_nothing(self, agg, cf_svc):
        await agg.flush()
        cf_svc.send.assert_not_awaited()

    async def test_flush_after_second_flush_does_nothing(self, agg, cf_svc):
        await agg.push(CAM_A, 5, TS)
        await agg.flush()
        cf_svc.send.reset_mock()
        await agg.flush()
        cf_svc.send.assert_not_awaited()

    async def test_payload_has_required_fields(self, agg, cf_svc):
        await agg.push(CAM_A, 4, TS)
        await agg.flush()
        payload = cf_svc.send.call_args.args[0]
        for key in ("cameraId", "busId", "route", "cameraStatus", "busStatus",
                    "timestamp", "passengerCount"):
            assert key in payload, f"Missing field: {key}"

    async def test_driver_name_included_when_present(self, agg, cf_svc):
        await agg.push(CAM_A, 4, TS)
        await agg.flush()
        payload = cf_svc.send.call_args.args[0]
        assert payload["driverName"] == "Mehmet Yilmaz"

    async def test_driver_name_omitted_when_none(self, agg, cf_svc, bus_info_svc):
        bus_info_svc.current.return_value = BusInfo(
            bus_id=34, route="34A-Taksim", bus_status="RUNNING", driver_name=None
        )
        await agg.push(CAM_A, 4, TS)
        await agg.flush()
        payload = cf_svc.send.call_args.args[0]
        assert "driverName" not in payload

    async def test_passenger_count_clamped_to_500(self, agg, cf_svc):
        await agg.push(CAM_A, 600, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["passengerCount"] == 500

    async def test_passenger_count_clamped_to_0(self, agg, cf_svc):
        await agg.push(CAM_A, -5, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["passengerCount"] == 0

    async def test_timestamp_has_turkey_offset(self, agg, cf_svc):
        await agg.push(CAM_A, 4, TS)
        await agg.flush()
        ts = cf_svc.send.call_args.args[0]["timestamp"]
        assert "+03:00" in ts, f"Timestamp missing Turkey TZ offset: {ts}"

    async def test_camera_id_built_from_device_and_bus(self, agg, cf_svc):
        await agg.push("CAM-front", 4, TS)
        await agg.flush()
        camera_id = cf_svc.send.call_args.args[0]["cameraId"]
        # Default template "CAM-BUS{bus}-{pos}": bus=34, pos=front→001
        assert camera_id == "CAM-BUS34-001"

    async def test_bus_status_from_bus_info(self, agg, cf_svc):
        await agg.push(CAM_A, 4, TS)
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["busStatus"] == "RUNNING"

    async def test_camera_status_error_propagated(self, agg, cf_svc):
        await agg.push(CAM_A, 0, TS, camera_status="ERROR")
        await agg.flush()
        assert cf_svc.send.call_args.args[0]["cameraStatus"] == "ERROR"


class TestSpikeDetection:
    async def test_spike_triggers_immediate_flush(self, agg, cf_svc):
        # Establish baseline
        agg._last_sent[CAM_A] = 5
        # Push a count that exceeds spike_threshold (5)
        await agg.push(CAM_A, 11, TS)
        cf_svc.send.assert_awaited_once()

    async def test_small_change_does_not_spike(self, agg, cf_svc):
        agg._last_sent[CAM_A] = 5
        await agg.push(CAM_A, 8, TS)   # Δ3 < threshold 5
        cf_svc.send.assert_not_awaited()

    async def test_no_spike_on_first_reading(self, agg, cf_svc):
        await agg.push(CAM_A, 50, TS)
        cf_svc.send.assert_not_awaited()

    async def test_spike_updates_last_sent(self, agg, cf_svc):
        agg._last_sent[CAM_A] = 2
        await agg.push(CAM_A, 20, TS)
        assert agg._last_sent[CAM_A] == 20

    async def test_drop_spike_also_triggers(self, agg, cf_svc):
        agg._last_sent[CAM_A] = 20
        await agg.push(CAM_A, 5, TS)   # Δ-15 > threshold
        cf_svc.send.assert_awaited_once()
