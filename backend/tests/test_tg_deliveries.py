"""Вкладка «Все доставки» курьерского аппа: собранные недоставленные доставки
филиала (только просмотр) + общий телефон колл-центра."""
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.db import Base
import app.models  # noqa: F401
from app.models import Order, OrderItem
from app.config import settings

TS = datetime(2026, 7, 9, 12, 0, 0)


def _order(num, **kw):
    o = Order(iiko_order_id=f"o{num}", org_id=kw.pop("org_id", "org-1"),
              number=str(num), service_type=kw.pop("service_type", "DeliveryByCourier"),
              **kw)
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
            _order(1, assembled_at=TS),                                # ждёт курьера
            _order(2, assembled_at=TS, assigned_courier_name="Адильхан",
                   assigned_courier_code="77"),                        # уже с курьером — мимо
            _order(3),                                                 # не собран — мимо
            _order(4, assembled_at=TS, delivered_at=TS),               # доставлен — мимо
            _order(5, assembled_at=TS, service_type="DineIn"),         # зал — мимо
            _order(6, assembled_at=TS, customer_name="Wolt Астана"),   # агрегатор — мимо
            _order(7, assembled_at=TS, org_id="org-2"),                # чужой филиал — мимо
            _order(8, assembled_at=TS, status="Closed"),               # закрыт в iiko — мимо
            # Курьер из iiko есть по имени, но кода RMS нет (ingest.py ставит имя
            # всегда, а код — только если сотрудник нашёлся в RMS). По коду такой
            # заказ не попадёт ни в чьи «Мои», значит он ЖДЁТ курьера и обязан
            # быть виден, иначе становится невидимкой для всех.
            _order(9, assembled_at=TS, assigned_courier_name="Адильхан"),
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
async def test_tg_deliveries_filters_and_cc_phone(client, monkeypatch):
    monkeypatch.setattr(settings, "call_center_phone", "+77000000000")
    r = await client.get("/tg/deliveries", params={"org_id": "org-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["call_center_phone"] == "+77000000000"
    nums = [o["number"] for o in body["orders"]]
    assert nums == ["1", "9"]


@pytest.mark.asyncio
async def test_tg_deliveries_empty_org(client):
    r = await client.get("/tg/deliveries", params={"org_id": "org-x"})
    assert r.status_code == 200
    assert r.json()["orders"] == []
