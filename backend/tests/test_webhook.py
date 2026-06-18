import respx
from httpx import Response, ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy import select, func
from app.db import Base
import app.models  # noqa
from app.models import Order
from app.config import settings


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
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


@respx.mock
async def test_webhook_rejects_bad_secret(client):
    c, _ = client
    settings.webhook_secret = "s3cret"
    r = await c.post("/webhooks/iiko/cloud?key=wrong", json=[])
    assert r.status_code == 403


@respx.mock
async def test_webhook_ingests_delivery_event(client):
    c, maker = client
    settings.webhook_secret = "s3cret"
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    respx.post("https://api-ru.iiko.services/api/1/deliveries/by_id").mock(
        return_value=Response(200, json={"orders": [{
            "id": "o1",
            "order": {"number": "101", "customer": {"name": "Иван"},
                      "items": [{"type": "Product", "amount": 1,
                                 "product": {"id": "p1", "name": "Ролл"}}]},
        }]}))

    event = [{
        "eventType": "DeliveryOrderUpdate",
        "organizationId": "org-1",
        "eventInfo": {"id": "o1"},
    }]
    r = await c.post("/webhooks/iiko/cloud?key=s3cret", json=event)
    assert r.status_code == 200

    async with maker() as s:
        n = (await s.execute(select(func.count()).select_from(Order))).scalar_one()
        assert n == 1


async def test_ingest_front_hall_order_by_category(client):
    """Заказ зала с терминала: ингестится и зона берётся по категории блюда."""
    c, maker = client
    settings.webhook_secret = ""  # без проверки ключа
    payload = {
        "id": "T1", "org_id": "org-1", "number": "55", "service_type": "DineIn",
        "customer": "Гость", "comment": "без имбиря",
        "items": [
            {"name": "Сет Фаворит", "category": "Роллы", "amount": 1, "comment": "без лука"},
            {"name": "Кола", "category": "Напитки", "amount": 2},
        ],
    }
    r = await c.post("/ingest/front", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["number"] == "55"
    assert body["comment"] == "без имбиря"
    stations = {it["name"]: it["station"] for it in body["items"]}
    assert stations["Сет Фаворит"] == "rolls"   # по категории «Роллы»
    assert stations["Кола"] == "other"            # напитки -> прочее
    notes = {it["name"]: it["comment"] for it in body["items"]}
    assert notes["Сет Фаворит"] == "без лука"
