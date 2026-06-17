import respx
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy import select
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
        order = Order(iiko_order_id="o1", org_id="org-1", number="101")
        order.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(order); await s.commit()

    from app import api
    from app.db import get_session

    async def override():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url="http://test") as c:
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


@respx.mock
async def test_accept_then_ready_sets_timestamps(client):
    c, maker = client
    async with maker() as s:
        item_id = (await s.execute(select(OrderItem.id))).scalar_one()

    r1 = await c.post(f"/items/{item_id}/accept", json={"by": "Алия"})
    assert r1.status_code == 200
    r2 = await c.post(f"/items/{item_id}/ready", json={"by": "Алия"})
    assert r2.status_code == 200

    async with maker() as s:
        it = (await s.execute(select(OrderItem).where(OrderItem.id == item_id))).scalar_one()
        assert it.station_accepted_at is not None
        assert it.station_ready_at is not None


@respx.mock
async def test_revert_returns_item_to_kitchen(client):
    c, maker = client
    async with maker() as s:
        item_id = (await s.execute(select(OrderItem.id))).scalar_one()
    await c.post(f"/items/{item_id}/accept", json={"by": "Алия"})
    await c.post(f"/items/{item_id}/ready", json={"by": "Алия"})
    await c.post(f"/items/{item_id}/check", json={"by": "Сборщик"})

    r = await c.post(f"/items/{item_id}/revert", json={"by": "Сборщик"})
    assert r.status_code == 200

    async with maker() as s:
        it = (await s.execute(select(OrderItem).where(OrderItem.id == item_id))).scalar_one()
        assert it.station_ready_at is None      # снова «не готово» -> вернётся на кухню
        assert it.is_checked is False
        assert it.station_accepted_at is not None  # таймер с «Принял» продолжает идти
