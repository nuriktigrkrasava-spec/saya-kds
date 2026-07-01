"""Корректность показателей аналитики: отменённые заказы, Closed без отметки
курьера, SLA по зависшим недоставленным, зона other в тепловой карте."""
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from app.api import app
from app.db import get_session
from app.config import settings


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://t")
    app.dependency_overrides.clear()


def _local_noon():
    off = timedelta(hours=settings.iiko_tz_offset_hours)
    local_today = (datetime.utcnow() + off).date()
    noon = datetime(local_today.year, local_today.month, local_today.day, 12) - off
    return local_today, noon


@pytest.mark.asyncio
async def test_cancelled_and_deleted_orders_excluded(client, session):
    from app.models import Order
    local_today, noon = _local_noon()
    session.add(Order(iiko_order_id="c1", org_id="demo", number="1", created_at=noon))
    session.add(Order(iiko_order_id="c2", org_id="demo", number="2",
                      status="Cancelled", created_at=noon))
    session.add(Order(iiko_order_id="c3", org_id="demo", number="3",
                      status="Deleted", created_at=noon))
    await session.commit()
    today = local_today.strftime("%Y-%m-%d")
    async with client as c:
        r = await c.get(f"/admin/summary?org_id=demo&from={today}&to={today}")
        assert r.json()["current"]["orders"] == 1     # отменённые не в когорте
        r = await c.get(f"/admin/analytics?org_id=demo&from={today}&to={today}")
        assert r.json()["totals"]["orders"] == 1


@pytest.mark.asyncio
async def test_closed_without_delivered_counts_as_done(client, session):
    """Заказ, закрытый в iiko без отметки курьера (или самовывоз), не должен
    вечно висеть «в работе»."""
    from app.models import Order
    local_today, noon = _local_noon()
    session.add(Order(iiko_order_id="d1", org_id="demo", number="1",
                      status="Closed", created_at=noon))          # закрыт, delivered_at нет
    session.add(Order(iiko_order_id="d2", org_id="demo", number="2", created_at=noon))
    await session.commit()
    today = local_today.strftime("%Y-%m-%d")
    async with client as c:
        r = await c.get(f"/admin/summary?org_id=demo&from={today}&to={today}")
        d = r.json()
        assert d["current"]["delivered"] == 1   # закрытый = завершён
        assert d["in_progress"] == 1            # только реально открытый
        assert d["current"]["orders"] == d["current"]["delivered"] + d["in_progress"]


@pytest.mark.asyncio
async def test_sla_counts_stuck_undelivered_as_breached(client, session):
    """Недоставленный заказ старше SLA-порога — уже просрочка, а не «не считается»."""
    from app.models import Order
    now = datetime.utcnow()
    session.add(Order(iiko_order_id="s1", org_id="demo", number="1",
                      created_at=now - timedelta(hours=2)))       # висит 2 часа
    session.add(Order(iiko_order_id="s2", org_id="demo", number="2",
                      created_at=now - timedelta(minutes=50),
                      on_way_at=now - timedelta(minutes=40),
                      delivered_at=now - timedelta(minutes=20)))  # доставлен за 30м
    await session.commit()
    async with client as c:
        r = await c.get("/admin/stage-stats?org_id=demo&sla_minutes=45")
        sla = r.json()["sla"]
        assert sla["total"] == 2
        assert sla["breached"] == 1   # зависший — просрочка; доставленный за 30м — нет


@pytest.mark.asyncio
async def test_summary_reports_delivery_sample_size(client, session):
    """Среднее время доставки должно сообщать, по скольким заказам посчитано."""
    from app.models import Order
    local_today, noon = _local_noon()
    session.add(Order(iiko_order_id="m1", org_id="demo", number="1", created_at=noon,
                      on_way_at=noon, delivered_at=noon + timedelta(minutes=30)))
    # доставлен, но курьер не нажал «Забрал» — из среднего выпадает
    session.add(Order(iiko_order_id="m2", org_id="demo", number="2", created_at=noon,
                      delivered_at=noon + timedelta(minutes=40)))
    await session.commit()
    today = local_today.strftime("%Y-%m-%d")
    async with client as c:
        r = await c.get(f"/admin/summary?org_id=demo&from={today}&to={today}")
        cur = r.json()["current"]
        assert cur["delivered"] == 2
        assert cur["avg_delivery_orders"] == 1   # посчитано только по одному
        assert cur["avg_delivery_seconds"] == 1800


def test_zone_load_includes_other():
    """Позиции без зоны (other) не должны молча выпадать из тепловой карты."""
    from app.analytics_stages import zone_load

    class _It:
        def __init__(self):
            self.kitchen_zone = "other"
            self.station = "other"
            self.station_accepted_at = datetime(2026, 6, 27, 6, 50)
            self.station_ready_at = datetime(2026, 6, 27, 7, 0)

    class _Ord:
        items = [_It()]

    out = zone_load([_Ord()], tz_offset_hours=5)
    assert "other" in out["zones"]
    assert out["totals_by_zone"]["other"]["count"] == 1
