from datetime import datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.db import Base
import app.models  # noqa: F401
from app.models import Order, OrderItem, CourierLocation


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from app import api
    from app.db import get_session

    async def override():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app),
                           base_url="http://test") as c:
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


async def test_track_on_way_returns_courier_point(client):
    c, maker = client
    async with maker() as s:
        order = Order(iiko_order_id="trk-1", org_id="org-1", number="501",
                      address="Астана, пр. Туран 37",
                      on_way_at=datetime.utcnow(),
                      assigned_courier_code="C1")
        order.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(order)
        s.add(CourierLocation(courier_code="C1", name="Айдос", lat=51.16, lon=71.47,
                              updated_at=datetime.utcnow()))
        await s.commit()

    r = await c.get("/track/trk-1")
    assert r.status_code == 200
    body = r.json()
    assert body["number"] == "501"
    assert body["address"] == "Астана, пр. Туран 37"
    assert len(body["stages"]) == 5

    on_way = next(st for st in body["stages"] if st["key"] == "on_way")
    assert on_way["done"] is True

    assert body["courier"] is not None
    assert body["courier"]["lat"] == 51.16
    assert body["courier"]["lon"] == 71.47

    # Не утекают приватные поля клиента.
    assert "phone" not in body
    assert "customer_name" not in body


async def test_track_no_courier_when_not_on_way(client):
    c, maker = client
    async with maker() as s:
        order = Order(iiko_order_id="trk-2", org_id="org-1", number="502",
                      address="Астана, ул. Сыганак 18",
                      assigned_courier_code="C1")
        order.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(order)
        s.add(CourierLocation(courier_code="C1", name="Айдос", lat=51.16, lon=71.47,
                              updated_at=datetime.utcnow()))
        await s.commit()

    r = await c.get("/track/trk-2")
    assert r.status_code == 200
    body = r.json()
    assert body["courier"] is None
    assert len(body["stages"]) == 5


async def test_track_404_for_unknown_id(client):
    c, _ = client
    r = await c.get("/track/does-not-exist")
    assert r.status_code == 404
