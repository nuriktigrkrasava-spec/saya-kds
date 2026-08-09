"""Предзаказ виден на КДС заранее, но помечен и не считается текущим заказом.

Заказчик 09.08.2026: «предзаказ на 16:00 появляется в КДС в ту же секунду, как
появился заказ» — повар не отличал его от текущего и начинал готовить сразу.
Прятать предзаказ целиком (так было) тоже нельзя: кухня должна видеть, что
вечером её ждёт большой заказ. Значит: карточка висит, но с меткой «Предзаказ,
к 16:00», а в работу входит за preorder_lead_minutes до срока.
"""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.db import Base
import app.models  # noqa: F401
from app.models import Order, OrderItem

CREATED = datetime(2026, 8, 9, 7, 35, 0)   # 12:35 по Астане


def _order(num, **kw):
    o = Order(iiko_order_id=f"o{num}", org_id="org-1", number=str(num),
              status="WaitCooking", created_at=CREATED,
              service_type="DeliveryByCourier", **kw)
    o.items = [OrderItem(product_id="p", name="Ролл", station="rolls")]
    return o


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        s.add_all([
            # предзаказ на 16:00, принят в 12:35 — держим до 15:00
            _order(7, complete_before=CREATED + timedelta(hours=3, minutes=25),
                   preorder_held=True),
            # обычный заказ: срок «через час» — это не предзаказ
            _order(8, complete_before=CREATED + timedelta(hours=1)),
        ])
        await s.commit()

    from app import api
    from app.db import get_session

    async def override():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app),
                           base_url="http://test") as c:
        yield c
    api.app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_held_preorder_is_listed_and_marked(client):
    r = await client.get("/orders", params={"org_id": "org-1"})
    assert r.status_code == 200
    by_num = {o["number"]: o for o in r.json()}
    assert set(by_num) == {"7", "8"}, "предзаказ обязан быть виден на КДС"

    pre = by_num["7"]
    assert pre["is_preorder"] is True
    assert pre["preorder_held"] is True, "в работу ещё не входит"
    assert pre["complete_before"] == "2026-08-09T11:00:00Z", "на какое время — из iiko"

    assert by_num["8"]["is_preorder"] is False, "обычный заказ на час вперёд — не предзаказ"
    assert by_num["8"]["preorder_held"] is False
