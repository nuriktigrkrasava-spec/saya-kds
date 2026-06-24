import hmac
import hashlib
import json
from urllib.parse import urlencode

import httpx
import respx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.db import Base
import app.models  # noqa: F401
from app.models import Order, OrderItem
from app.config import settings
from app import telegram

_IIKO = "https://api-ru.iiko.services"


def _build_init_data(token: str, user: dict, auth_date: str = "1700000000") -> str:
    """Build a correctly signed Telegram WebApp initData query string."""
    fields = {
        "auth_date": auth_date,
        "query_id": "AAEtest",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


# ── initData validation ─────────────────────────────────────────────────────

def test_parse_and_validate_accepts_valid_signature():
    settings.telegram_bot_token = "testtoken"
    init_data = _build_init_data("testtoken", {"id": 42, "first_name": "Куряга",
                                               "username": "kur"})
    pairs = telegram.parse_and_validate(init_data)
    assert pairs is not None
    user = telegram.extract_user(pairs)
    assert user["id"] == 42
    assert user["first_name"] == "Куряга"
    assert user["username"] == "kur"


def test_parse_and_validate_rejects_tampered_hash():
    settings.telegram_bot_token = "testtoken"
    init_data = _build_init_data("testtoken", {"id": 42, "first_name": "Куряга"})
    tampered = init_data.replace("hash=", "hash=deadbeef")
    assert telegram.parse_and_validate(tampered) is None


def test_parse_and_validate_rejects_missing_hash():
    settings.telegram_bot_token = "testtoken"
    assert telegram.parse_and_validate("user=%7B%7D&auth_date=1") is None


# ── assign-courier + my-orders ──────────────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        order = Order(iiko_order_id="o1", org_id="org-1", number="101")
        order.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(order)
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


@respx.mock
async def test_assign_courier_then_my_orders(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    async with maker() as s:
        order_id = (await s.execute(select(Order.id))).scalar_one()

    respx.post(f"{_IIKO}/api/1/access_token").mock(
        return_value=httpx.Response(200, json={"token": "T"}))
    respx.post(f"{_IIKO}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-1", "items": [{"id": "emp-1", "displayName": "Айдос"}]}]}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={}))

    r = await c.post(f"/orders/{order_id}/assign-courier",
                     json={"courier_code": "C7", "courier_name": "Айдос",
                           "by": "Сборщик"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["assembled"] is True

    async with maker() as s:
        o = (await s.execute(select(Order).where(Order.id == order_id))).scalar_one()
        assert o.assigned_courier_code == "C7"
        assert o.assigned_courier_name == "Айдос"
        assert o.assembled_at is not None

    # my-orders возвращает заказ для назначенного курьера.
    r2 = await c.get("/tg/my-orders", params={"courier_code": "C7"})
    assert r2.status_code == 200
    orders = r2.json()
    assert len(orders) == 1
    assert orders[0]["id"] == order_id

    # другой курьер — пусто.
    r3 = await c.get("/tg/my-orders", params={"courier_code": "OTHER"})
    assert r3.status_code == 200
    assert r3.json() == []


@respx.mock
async def test_assign_courier_404_for_missing_order(client):
    c, _ = client
    r = await c.post("/orders/999999/assign-courier",
                     json={"courier_code": "C7", "courier_name": "X", "by": ""})
    assert r.status_code == 404
