from unittest.mock import AsyncMock
import pytest

from src.services.aggregator_service import AggregatorService
from src.services.cloudflare_service import CloudflareService

BUS_A = "BUS-001"
BUS_B = "BUS-002"
TS    = "2024-01-01T00:00:00+00:00"


@pytest.fixture()
def cf_svc() -> AsyncMock:
    svc = AsyncMock(spec=CloudflareService)
    svc.send.return_value = True
    return svc


@pytest.fixture()
def agg(cf_svc) -> AggregatorService:
    a = AggregatorService(flush_interval=60)
    a.set_cloudflare_svc(cf_svc)
    return a


class TestPush:
    def test_push_stores_count(self, agg):
        agg.push(BUS_A, "front", 5, TS)
        assert agg._buffers[BUS_A].panes["front"].counts == [5]

    def test_push_multiple_counts(self, agg):
        for count in [3, 5, 7]:
            agg.push(BUS_A, "front", count, TS)
        assert agg._buffers[BUS_A].panes["front"].counts == [3, 5, 7]

    def test_push_separate_buses(self, agg):
        agg.push(BUS_A, "front", 3, TS)
        agg.push(BUS_B, "rear",  8, TS)
        assert BUS_A in agg._buffers
        assert BUS_B in agg._buffers


class TestFlush:
    async def test_flush_sends_average(self, agg, cf_svc):
        agg.push(BUS_A, "front", 2, TS)
        agg.push(BUS_A, "front", 4, TS)
        agg.push(BUS_A, "front", 6, TS)
        await agg.flush()
        cf_svc.send.assert_awaited_once()
        payload = cf_svc.send.call_args.args[0]
        assert payload["bus_id"] == BUS_A
        assert payload["panes"]["front"] == 4   # average of 2, 4, 6

    async def test_flush_rounds_average(self, agg, cf_svc):
        agg.push(BUS_A, "front", 3, TS)
        agg.push(BUS_A, "front", 4, TS)
        await agg.flush()
        payload = cf_svc.send.call_args.args[0]
        assert isinstance(payload["panes"]["front"], int)

    async def test_flush_clears_buffer(self, agg, cf_svc):
        agg.push(BUS_A, "front", 5, TS)
        await agg.flush()
        assert not agg._buffers[BUS_A].panes

    async def test_flush_sends_each_bus_once(self, agg, cf_svc):
        agg.push(BUS_A, "front", 3, TS)
        agg.push(BUS_B, "rear",  7, TS)
        await agg.flush()
        assert cf_svc.send.await_count == 2

    async def test_flush_empty_buffer_does_nothing(self, agg, cf_svc):
        await agg.flush()
        cf_svc.send.assert_not_awaited()

    async def test_flush_skips_empty_bus_buffer(self, agg, cf_svc):
        agg.push(BUS_A, "front", 5, TS)
        await agg.flush()
        cf_svc.send.reset_mock()
        await agg.flush()
        cf_svc.send.assert_not_awaited()
