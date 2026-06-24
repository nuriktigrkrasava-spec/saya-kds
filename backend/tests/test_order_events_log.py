import respx
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa
from app.models import Order, OrderItem, OrderEvent


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        o = Order(iiko_order_id="o1", org_id="org-1", number="101")
        o.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(o); await s.commit()
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


async def _ids(maker):
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
        iid = (await s.execute(select(OrderItem.id))).scalar_one()
    return oid, iid


@respx.mock
async def test_item_ready_logs_event(client):
    c, maker = client
    _, iid = await _ids(maker)
    await c.post(f"/items/{iid}/ready", json={"by": "Коля"})
    async with maker() as s:
        ev = (await s.execute(select(OrderEvent).where(OrderEvent.type == "item_ready"))).scalars().all()
    assert len(ev) == 1
    assert ev[0].actor_name == "Коля" and ev[0].item_id == iid and ev[0].org_id == "org-1"


@respx.mock
async def test_assemble_logs_event(client):
    c, maker = client
    oid, _ = await _ids(maker)
    await c.post(f"/orders/{oid}/assemble", json={"by": "Сборщик"})
    async with maker() as s:
        ev = (await s.execute(select(OrderEvent).where(OrderEvent.type == "order_assemble"))).scalars().all()
    assert len(ev) == 1 and ev[0].actor_name == "Сборщик"
