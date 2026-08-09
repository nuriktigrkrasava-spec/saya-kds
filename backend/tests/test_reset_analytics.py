"""Сброс аналитики: чистая база под новый отсчёт.

Заказчик 09.08.2026: «убери все данные, на которых строится нынешняя аналитика,
начнём с нуля» — в базе лежали демо-заказы (сиды 1001-1003, 9001-9006) и мусор
от ранних вебхуков без номера, и цифры на дашборде считались по ним.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.config import settings
from app.db import Base
import app.models  # noqa: F401
from app.models import Order, OrderEvent, OrderItem, StopListItem


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        o = Order(iiko_order_id="o1", org_id="org-1", number="1")
        o.items = [OrderItem(product_id="p", name="Ролл", station="rolls")]
        s.add(o)
        s.add(StopListItem(org_id="org-1", product_id="p", name="Ролл", balance=0))
        await s.flush()
        s.add(OrderEvent(order_id=o.id, type="delivered", actor_name="Курьер"))
        await s.commit()

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


async def _count(maker, model):
    async with maker() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.asyncio
async def test_reset_requires_key(client, monkeypatch):
    c, maker = client
    monkeypatch.setattr(settings, "webhook_secret", "s3cret")
    r = await c.post("/admin/reset-analytics?key=nope")
    assert r.status_code == 403
    assert await _count(maker, Order) == 1, "чужой ключ ничего не удаляет"


@pytest.mark.asyncio
async def test_reset_wipes_orders_events_items(client, monkeypatch):
    c, maker = client
    monkeypatch.setattr(settings, "webhook_secret", "s3cret")
    r = await c.post("/admin/reset-analytics?key=s3cret")
    assert r.status_code == 200
    assert r.json() == {"orders": 1, "items": 1, "events": 1}
    assert await _count(maker, Order) == 0
    assert await _count(maker, OrderItem) == 0
    assert await _count(maker, OrderEvent) == 0
    assert await _count(maker, StopListItem) == 1, "настройки филиала не трогаем"
