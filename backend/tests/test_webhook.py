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


@respx.mock
async def test_held_preorder_is_broadcast_marked(client):
    """Предзаказ уходит на экраны сразу, но с меткой: висит, а не готовится.

    Раньше его прятали до срока — кухня узнавала о заказе на 20 ролов за час до
    выдачи. Теперь карточка приходит с preorder_held=True, и экран сам держит её
    в отложенной пачке (жалоба заказчика 09.08.2026)."""
    c, maker = client
    settings.webhook_secret = "s3cret"
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    respx.post("https://api-ru.iiko.services/api/1/deliveries/by_id").mock(
        return_value=Response(200, json={"orders": [{
            "id": "o-held",
            "order": {
                "number": "999",
                "completeBefore": "2099-01-01 12:00:00",
                "customer": {"name": "Будущий"},
                "items": [{"type": "Product", "amount": 1,
                           "product": {"id": "p2", "name": "Суши"}}],
            },
        }]}))

    broadcast_calls = []

    from app import api as _api
    original_broadcast = _api.realtime.broadcast_order

    async def _fake_broadcast(order):
        broadcast_calls.append(order)

    _api.realtime.broadcast_order = _fake_broadcast
    try:
        event = [{"eventType": "DeliveryOrderUpdate",
                  "organizationId": "org-1",
                  "eventInfo": {"id": "o-held"}}]
        r = await c.post("/webhooks/iiko/cloud?key=s3cret", json=event)
        assert r.status_code == 200
    finally:
        _api.realtime.broadcast_order = original_broadcast

    # Заказ должен быть в БД
    async with maker() as s:
        n = (await s.execute(select(func.count()).select_from(Order))).scalar_one()
        assert n == 1

    # Экран получает предзаказ сразу — и видит, что он придержан
    assert len(broadcast_calls) == 1
    from app.realtime import order_payload
    payload = order_payload(broadcast_calls[0])
    assert payload["preorder_held"] is True and payload["is_preorder"] is True
    assert payload["complete_before"] is not None, "на какое время — обязательно"


@respx.mock
async def test_normal_order_is_broadcast(client):
    """Обычный заказ (без completeBefore или с прошедшим) — broadcast вызывается."""
    c, maker = client
    settings.webhook_secret = "s3cret"
    settings.cloud_api_login = "key"
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(200, json={"token": "T"}))
    respx.post("https://api-ru.iiko.services/api/1/deliveries/by_id").mock(
        return_value=Response(200, json={"orders": [{
            "id": "o-normal",
            "order": {
                "number": "100",
                "customer": {"name": "Клиент"},
                "items": [{"type": "Product", "amount": 1,
                           "product": {"id": "p3", "name": "Ролл"}}],
            },
        }]}))

    broadcast_calls = []

    from app import api as _api
    original_broadcast = _api.realtime.broadcast_order

    async def _fake_broadcast(order):
        broadcast_calls.append(order)

    _api.realtime.broadcast_order = _fake_broadcast
    try:
        event = [{"eventType": "DeliveryOrderUpdate",
                  "organizationId": "org-1",
                  "eventInfo": {"id": "o-normal"}}]
        r = await c.post("/webhooks/iiko/cloud?key=s3cret", json=event)
        assert r.status_code == 200
    finally:
        _api.realtime.broadcast_order = original_broadcast

    # Broadcast должен был вызваться
    assert len(broadcast_calls) == 1, f"broadcast не вызван для обычного заказа"


async def test_list_orders_includes_held_marked(client):
    """GET /orders отдаёт и придержанные предзаказы — помеченными."""
    from app.models import OrderItem
    c, maker = client

    async with maker() as s:
        # Обычный заказ
        o1 = Order(iiko_order_id="normal-1", org_id="org-test",
                   number="1", status="New", preorder_held=False)
        o1.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        # Придержанный заказ
        o2 = Order(iiko_order_id="held-1", org_id="org-test",
                   number="2", status="New", preorder_held=True)
        o2.items = [OrderItem(product_id="p2", name="Суши", station="rolls")]
        s.add(o1)
        s.add(o2)
        await s.commit()

    r = await c.get("/orders?org_id=org-test")
    assert r.status_code == 200
    body = r.json()
    by_num = {o["number"]: o for o in body}
    assert set(by_num) == {"1", "2"}, "предзаказ тоже виден кухне"
    assert by_num["1"]["preorder_held"] is False
    assert by_num["2"]["preorder_held"] is True, "но помечен как отложенный"


async def test_bot_webhook_live_location_updates_courier(client):
    """Live Location из бота (edited_message) пишет позицию курьера в фоне."""
    from app.models import TelegramLink, CourierLocation
    from sqlalchemy import select as _sel
    c, maker = client
    settings.webhook_secret = ""  # без проверки секрета в тесте
    async with maker() as s:
        s.add(TelegramLink(telegram_id="555", employee_code="CR1",
                           name="Курьер", branch_code="B1", org_id="org-1"))
        await s.commit()

    update = {"edited_message": {"from": {"id": 555},
                                 "location": {"latitude": 51.18, "longitude": 71.43}}}
    r = await c.post("/tg/bot-webhook", json=update)
    assert r.status_code == 200

    async with maker() as s:
        loc = (await s.execute(
            _sel(CourierLocation).where(CourierLocation.courier_code == "CR1"))
        ).scalar_one()
        assert round(loc.lat, 2) == 51.18 and round(loc.lon, 2) == 71.43
