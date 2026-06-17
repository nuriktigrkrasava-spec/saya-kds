import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from app.db import Base
import app.models  # noqa: F401  (регистрирует таблицы)
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
        yield c
    api.app.dependency_overrides.clear()
    await engine.dispose()


async def test_stoplist_update_then_get_then_clear(client):
    settings.webhook_secret = "s3cret"

    # 1. StopListUpdate с одной позицией -> 200
    event = [{
        "eventType": "StopListUpdate",
        "organizationId": "o1",
        "eventInfo": {"items": [
            {"productId": "p1", "name": "Филадельфия", "balance": 0},
        ]},
    }]
    r = await client.post("/webhooks/iiko/cloud?key=s3cret", json=event)
    assert r.status_code == 200
    assert r.json()["handled"] == 1

    # 2. GET /stoplist?org_id=o1 -> 1 позиция с именем "Филадельфия"
    r = await client.get("/stoplist?org_id=o1")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "Филадельфия"
    assert data[0]["product_id"] == "p1"
    assert data[0]["balance"] == 0

    # 3. Пустой StopListUpdate -> стоп-лист очищается (replace-on-update)
    empty_event = [{
        "eventType": "StopListUpdate",
        "organizationId": "o1",
        "eventInfo": {"items": []},
    }]
    r = await client.post("/webhooks/iiko/cloud?key=s3cret", json=empty_event)
    assert r.status_code == 200

    r = await client.get("/stoplist?org_id=o1")
    assert r.status_code == 200
    assert r.json() == []
