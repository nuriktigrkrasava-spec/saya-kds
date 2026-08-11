"""Кэш OLAP-запросов: RMS занимает лицензионный слот, дёргать его на каждый
показ аналитики нельзя."""
import pytest
from app import olap


@pytest.fixture(autouse=True)
def clean_cache():
    olap.reset_cache()
    yield
    olap.reset_cache()


@pytest.fixture
def counting_fetch(monkeypatch):
    calls = []

    async def fake(department, date_from, date_to):
        calls.append((department, date_from, date_to))
        return [{"Delivery.ServiceType": "COURIER", "Delivery.Delay": 5,
                 "Delivery.ActualTime": "2026-08-09T14:00:00"}]

    monkeypatch.setattr(olap, "_fetch_delivery_rows_uncached", fake)
    return calls


@pytest.mark.asyncio
async def test_second_call_served_from_cache(counting_fetch):
    a = await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10")
    b = await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10")
    assert a == b
    assert len(counting_fetch) == 1


@pytest.mark.asyncio
async def test_different_period_is_a_different_entry(counting_fetch):
    await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10")
    await olap.fetch_delivery_rows("Алиева", "2026-08-02", "2026-08-10")
    assert len(counting_fetch) == 2


@pytest.mark.asyncio
async def test_different_branch_is_a_different_entry(counting_fetch):
    await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10")
    await olap.fetch_delivery_rows("Сыганак", "2026-08-01", "2026-08-10")
    assert len(counting_fetch) == 2


@pytest.mark.asyncio
async def test_expired_entry_is_refetched(counting_fetch, monkeypatch):
    monkeypatch.setattr(olap, "CACHE_TTL_SECONDS", 0)
    await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10")
    await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10")
    assert len(counting_fetch) == 2


@pytest.mark.asyncio
async def test_failure_is_not_cached(monkeypatch):
    """Упавший RMS не должен залипнуть на две минуты — следующий показ пробует снова."""
    calls = []

    async def flaky(department, date_from, date_to):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("rms down")
        return []

    monkeypatch.setattr(olap, "_fetch_delivery_rows_uncached", flaky)
    with pytest.raises(RuntimeError):
        await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10")
    assert await olap.fetch_delivery_rows("Алиева", "2026-08-01", "2026-08-10") == []
    assert len(calls) == 2
