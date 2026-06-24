import respx
from datetime import datetime
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa
from app.models import Order, OrderItem


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        o = Order(iiko_order_id="o1", org_id="org-1", number="101")
        o.created_at = datetime(2026, 6, 20, 12, 0)
        it = OrderItem(product_id="p1", name="Ролл", station="rolls", checked_by="Коля")
        it.station_accepted_at = datetime(2026, 6, 20, 12, 1)
        it.station_ready_at = datetime(2026, 6, 20, 12, 4)
        o.items = [it]
        o.assembled_at = datetime(2026, 6, 20, 12, 6)
        o.on_way_at = datetime(2026, 6, 20, 12, 8)
        o.delivered_at = datetime(2026, 6, 20, 12, 40)
        s.add(o); await s.commit()
    from app import api
    from app.db import get_session
    async def override():
        async with maker() as s:
            yield s
    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as c:
        yield c
    api.app.dependency_overrides.clear()
    await engine.dispose()


@respx.mock
async def test_stage_stats(client):
    r = await client.get("/admin/stage-stats",
                         params={"org_id": "org-1", "from": "2026-06-20", "to": "2026-06-20",
                                 "sla_minutes": 45})
    assert r.status_code == 200
    body = r.json()
    cooking = next(s for s in body["stage_aggregates"]["stages"] if s["key"] == "cooking")
    assert cooking["count"] == 1 and cooking["avg"] == 180
    assert body["sla"]["total"] == 1 and body["sla"]["breached"] == 0  # 40m < 45m


@respx.mock
async def test_kitchen_zones_endpoint(client):
    r = await client.get("/admin/kitchen-zones",
                         params={"org_id": "org-1", "from": "2026-06-20", "to": "2026-06-20"})
    assert r.status_code == 200
    zones = {z["zone"]: z for z in r.json()["zones"]}
    assert zones["rolls"]["avg"] == 180


@respx.mock
async def test_timeline_synthesizes_when_no_events(client):
    # seed-заказ из фикстуры событий не имеет -> синтез из таймстампов
    r = await client.get("/admin/orders/1/timeline", params={"org_id": "org-1"})
    assert r.status_code == 200
    body = r.json()
    keys = [s["key"] for s in body["stages"]]
    assert keys == ["queue", "cooking", "assembly", "pickup_wait", "delivery", "iiko_close"]
    types = [e["type"] for e in body["events"]]
    assert "item_accept" in types and "delivered" in types  # синтезированы
