from app.security import courier_ref
import hmac
import hashlib
import json
from urllib.parse import urlencode

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.db import Base
import app.models  # noqa: F401
from app.models import TelegramLink, CourierLocation
from app.config import settings


def _init_data(token: str, user: dict) -> str:
    fields = {"auth_date": "1700000000", "query_id": "AAEgps",
              "user": json.dumps(user, separators=(",", ":"))}
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        s.add(TelegramLink(telegram_id="555", employee_code="C1", name="Курьер",
                           branch_code="B1"))
        await s.commit()

    from app import api
    from app.db import get_session

    async def override():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app),
                           base_url="http://test") as c:
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


async def test_location_upsert_and_list(client):
    c, maker = client
    settings.telegram_bot_token = "testtoken"
    init = _init_data("testtoken", {"id": 555, "first_name": "Кур"})

    r1 = await c.post("/tg/location", json={"initData": init, "lat": 51.10, "lon": 71.40})
    assert r1.status_code == 200
    r2 = await c.post("/tg/location", json={"initData": init, "lat": 51.20, "lon": 71.50})
    assert r2.status_code == 200

    # одна строка (upsert), последние координаты
    async with maker() as s:
        n = (await s.execute(select(func.count()).select_from(CourierLocation))).scalar_one()
        assert n == 1
        loc = (await s.execute(select(CourierLocation))).scalar_one()
        assert loc.courier_code == "C1"
        assert loc.lat == 51.20 and loc.lon == 71.50

    r3 = await c.get("/admin/couriers-locations", params={"branch_code": "B1"})
    assert r3.status_code == 200
    data = r3.json()
    assert len(data) == 1
    assert data[0]["courier_code"] == courier_ref("C1")
    assert data[0]["lat"] == 51.20


async def test_location_rejects_unlinked(client):
    c, _ = client
    settings.telegram_bot_token = "testtoken"
    init = _init_data("testtoken", {"id": 999, "first_name": "Чужой"})
    r = await c.post("/tg/location", json={"initData": init, "lat": 1.0, "lon": 2.0})
    assert r.status_code == 401
