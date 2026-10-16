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

        # Активный заказ: 2 позиции — одна готова сегодня (Алия, 120с), вторая нет.
        active = Order(iiko_order_id="a1", org_id="o1", number="201", status="New")
        active.created_at = now
        ready_item = OrderItem(product_id="p1", name="Филадельфия", station="rolls",
                               checked_by="Алия")
        ready_item.station_accepted_at = now
        ready_item.station_ready_at = now + timedelta(seconds=120)
        not_ready = OrderItem(product_id="p2", name="Пепперони", station="pizza")
        not_ready.station_accepted_at = now
        active.items = [ready_item, not_ready]

        # Доставленный сегодня заказ: on_way + delivered проставлены.
        done = Order(iiko_order_id="d1", org_id="o1", number="202", status="Closed")
        done.created_at = now
        done.on_way_at = now + timedelta(minutes=5)
        done.delivered_at = now + timedelta(minutes=25)

        s.add_all([active, done])
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
async def test_dashboard_snapshot(client):
    r = await client.get("/admin/dashboard?org_id=o1")
    assert r.status_code == 200
    data = r.json()
    assert data["active_orders"] >= 1
    assert data["delivered_today"] >= 1
    assert data["avg_prep_today_seconds"] is not None
    assert data["avg_delivery_today_seconds"] is not None
    # Не готовая позиция пиццы в активном заказе попадает в by_zone.
    assert data["by_zone"]["pizza"] >= 1


@respx.mock
async def test_leaderboard_lists_cook(client):
    r = await client.get("/admin/leaderboard?org_id=o1")
    assert r.status_code == 200
    data = r.json()
    aliya = next((row for row in data if row["name"] == "Алия"), None)
    assert aliya is not None
    assert aliya["items_done"] >= 1


@respx.mock
async def test_forecast_24_entries_sum_total(client):
    r = await client.get("/admin/forecast?org_id=o1")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 24
    assert sum(e["count"] for e in data) == 2  # два заказа в org o1


@respx.mock
async def test_report_csv(client):
    r = await client.get("/admin/report.csv?org_id=o1")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "order_number" in r.text
