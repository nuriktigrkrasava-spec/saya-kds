from app.time_utils import utc_now
from datetime import datetime, timedelta

import respx
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
        now = utc_now()
        order = Order(iiko_order_id="o1", org_id="o1", number="101")
        # item1: 120с — норма; item2: 20с — подозрительно (физически невозможно).
        item1 = OrderItem(product_id="p1", name="Филадельфия", station="rolls",
                          checked_by="Алия")
        item1.station_accepted_at = now
        item1.station_ready_at = now + timedelta(seconds=120)
        item2 = OrderItem(product_id="p2", name="Калифорния", station="rolls",
                          checked_by="Алия")
        item2.station_accepted_at = now
        item2.station_ready_at = now + timedelta(seconds=20)
        order.items = [item1, item2]
        s.add(order)
        await s.commit()

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
async def test_employee_history_groups_and_flags(client):
    r = await client.get("/admin/employee-history?org_id=o1")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    emp = data[0]
    assert emp["name"] == "Алия"
    assert emp["count"] == 2
    assert emp["suspicious_count"] == 1
    # Одна позиция помечена подозрительной, другая — нет.
    flags = sorted(it["suspicious"] for it in emp["items"])
    assert flags == [False, True]


@respx.mock
async def test_anomalies_lists_only_fast_item(client):
    r = await client.get("/admin/anomalies?org_id=o1")
    assert r.status_code == 200
    body = r.json()
    data = body["fast"]
    assert len(data) == 1
    a = data[0]
    assert a["name"] == "Алия"
    assert a["item_name"] == "Калифорния"
    assert a["prep_seconds"] == 20


@respx.mock
async def test_anomalies_reverts_empty_without_revert_events(client):
    r = await client.get("/admin/anomalies?org_id=o1")
    assert r.status_code == 200
    body = r.json()
    assert body["reverts"] == []
