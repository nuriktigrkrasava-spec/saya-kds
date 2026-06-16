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
        base = datetime(2026, 1, 1, 12, 0, 0)
        order = Order(iiko_order_id="o1", org_id="org-1", number="101")
        # Заказ доставлен: ставим on_way и delivered (доставка 600 секунд).
        order.on_way_at = base + timedelta(minutes=10)
        order.delivered_at = base + timedelta(minutes=20)
        # Две позиции, обе приняты и готовы сотрудником «Алия».
        it1 = OrderItem(product_id="p1", name="Ролл", station="rolls")
        it1.station_accepted_at = base
        it1.station_ready_at = base + timedelta(seconds=90)
        it1.checked_by = "Алия"
        it2 = OrderItem(product_id="p2", name="Пицца", station="pizza")
        it2.station_accepted_at = base
        it2.station_ready_at = base + timedelta(seconds=150)
        it2.checked_by = "Алия"
        order.items = [it1, it2]
        s.add(order)
        await s.commit()

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
async def test_admin_analytics(client):
    c, maker = client
    r = await c.get("/admin/analytics?org_id=org-1")
    assert r.status_code == 200
    data = r.json()

    assert data["org_id"] == "org-1"
    assert data["totals"]["orders"] == 1
    assert data["totals"]["delivered"] == 1
    assert data["totals"]["in_progress"] == 0

    employees = {e["name"]: e for e in data["employees"]}
    assert "Алия" in employees
    assert employees["Алия"]["items_done"] == 2
    assert employees["Алия"]["avg_prep_seconds"] is not None
    assert employees["Алия"]["avg_prep_seconds"] > 0
    # Среднее по 90 и 150 секунд = 120.
    assert employees["Алия"]["avg_prep_seconds"] == 120.0

    stations = {st["station"]: st for st in data["stations"]}
    assert stations["rolls"]["items_total"] == 1
    assert stations["rolls"]["items_ready"] == 1
    assert stations["pizza"]["avg_prep_seconds"] == 150.0

    assert data["delivery"]["delivered_count"] == 1
    assert data["delivery"]["avg_delivery_seconds"] is not None
    assert data["delivery"]["avg_delivery_seconds"] > 0
    assert data["delivery"]["avg_delivery_seconds"] == 600.0
