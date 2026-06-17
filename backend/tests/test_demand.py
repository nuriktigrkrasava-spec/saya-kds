from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa: F401
from app.models import Order


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        # Два заказа в одном кластере (~51.160, 71.470 при округлении до 3 знаков).
        o1 = Order(iiko_order_id="d1", org_id="o1", number="1",
                   address="Кабанбай батыра 1",
                   address_lat=51.1601, address_lon=71.4702)
        o2 = Order(iiko_order_id="d2", org_id="o1", number="2",
                   address="Кабанбай батыра 3",
                   address_lat=51.1604, address_lon=71.4699)
        # Отдельный кластер.
        o3 = Order(iiko_order_id="d3", org_id="o1", number="3",
                   address="Туран 37",
                   address_lat=51.200, address_lon=71.500)
        # Без координат — должен быть исключён.
        o4 = Order(iiko_order_id="d4", org_id="o1", number="4",
                   address="Без координат",
                   address_lat=None, address_lon=None)
        # Чужой филиал — не должен попасть.
        o5 = Order(iiko_order_id="d5", org_id="o2", number="5",
                   address="Другой филиал",
                   address_lat=51.300, address_lon=71.600)
        s.add_all([o1, o2, o3, o4, o5])
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


async def test_admin_demand_clusters(client):
    r = await client.get("/admin/demand?org_id=o1")
    assert r.status_code == 200
    points = r.json()

    # Два кластера (заказ без координат исключён, чужой филиал не попал).
    assert len(points) == 2

    # Самый загруженный кластер первым, count = 2.
    assert points[0]["count"] == 2
    assert points[1]["count"] == 1
    assert points[0]["sample_address"]

    # Координаты кластера — округлены до 3 знаков.
    assert points[0]["lat"] == round(points[0]["lat"], 3)
    assert points[0]["lon"] == round(points[0]["lon"], 3)


async def test_admin_demand_excludes_no_coords(client):
    r = await client.get("/admin/demand?org_id=o1")
    points = r.json()
    total = sum(p["count"] for p in points)
    # 3 заказа с координатами у o1; заказ без координат не считается.
    assert total == 3
