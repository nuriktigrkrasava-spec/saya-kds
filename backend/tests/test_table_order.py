import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa: F401  (регистрирует таблицы)
from app.config import settings
from app import iiko_cloud


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from app import api
    from app.db import get_session

    async def override_session():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=api.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    api.app.dependency_overrides.clear()
    await engine.dispose()


def test_normalize_table_order_from_raw_event():
    event = {
        "eventInfo": {
            "id": "t1",
            "order": {
                "number": "12",
                "orderType": {"orderServiceType": "DineIn"},
                "items": [{
                    "type": "Product",
                    "amount": 1,
                    "product": {"id": "p1", "name": "Ролл",
                                "category": {"id": "c1"}},
                }],
            },
        },
    }
    n = iiko_cloud.normalize_table_order(event, "o1")
    assert n.iiko_order_id == "t1"
    assert n.org_id == "o1"
    assert n.number == "12"
    assert n.service_type == "DineIn"
    assert len(n.items) == 1
    assert n.items[0].product_id == "p1"
    assert n.items[0].category_id == "c1"
    assert n.items[0].name == "Ролл"


def test_normalize_table_order_from_wrapper_and_fallback_service_type():
    # Уже распакованный wrapper {id, order} + отсутствует orderServiceType.
    wrapper = {
        "id": "t2",
        "order": {
            "number": "13",
            "items": [{
                "amount": 2,
                "product": {"id": "p2", "name": "Пицца"},
            }],
        },
    }
    n = iiko_cloud.normalize_table_order(wrapper, "o1")
    assert n.iiko_order_id == "t2"
    assert n.service_type == "DineIn"  # фолбэк
    assert len(n.items) == 1
    assert n.items[0].category_id is None
    assert n.items[0].amount == 2


async def test_table_order_webhook_ingests(client):
    settings.webhook_secret = "s3cret"

    event = [{
        "eventType": "TableOrderUpdate",
        "organizationId": "o1",
        "eventInfo": {
            "id": "t1",
            "order": {
                "number": "12",
                "orderType": {"orderServiceType": "DineIn"},
                "items": [{
                    "type": "Product",
                    "amount": 1,
                    "product": {"id": "p1", "name": "Ролл",
                                "category": {"id": "c1"}},
                }],
            },
        },
    }]
    r = await client.post("/webhooks/iiko/cloud?key=s3cret", json=event)
    assert r.status_code == 200
    assert r.json()["handled"] == 1

    r = await client.get("/orders?org_id=o1")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["service_type"] == "DineIn"


async def test_table_order_webhook_skips_when_no_items(client):
    settings.webhook_secret = "s3cret"
    event = [{
        "eventType": "TableOrderUpdate",
        "organizationId": "o1",
        "eventInfo": {"id": "t9", "order": {"number": "99", "items": []}},
    }]
    r = await client.post("/webhooks/iiko/cloud?key=s3cret", json=event)
    assert r.status_code == 200
    assert r.json()["handled"] == 0

    r = await client.get("/orders?org_id=o1")
    assert r.json() == []
