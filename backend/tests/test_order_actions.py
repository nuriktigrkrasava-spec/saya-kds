import respx
import httpx
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy import select
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
        order = Order(iiko_order_id="o1", org_id="org-1", number="101")
        order.items = [OrderItem(product_id="p1", name="Ролл", station="rolls")]
        s.add(order); await s.commit()

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
async def test_assemble_sets_assembled_flag(client):
    c, maker = client
    async with maker() as s:
        order_id = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{order_id}/assemble", json={"by": "Сборщик"})
    assert r.status_code == 200
    assert r.json()["assembled"] is True

    async with maker() as s:
        o = (await s.execute(select(Order).where(Order.id == order_id))).scalar_one()
        assert o.assembled_at is not None


@respx.mock
async def test_take_sets_on_way_flag(client):
    c, maker = client
    async with maker() as s:
        order_id = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{order_id}/take", json={"by": "Курьер"})
    assert r.status_code == 200
    body = r.json()
    assert body["on_way"] is True

    async with maker() as s:
        o = (await s.execute(select(Order).where(Order.id == order_id))).scalar_one()
        assert o.on_way_at is not None


@respx.mock
async def test_delivered_sets_delivered_flag(client):
    c, maker = client
    async with maker() as s:
        order_id = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{order_id}/delivered", json={"by": "Курьер"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True


@respx.mock
async def test_action_missing_order_404(client):
    c, _ = client
    r = await c.post("/orders/9999/assemble", json={"by": "x"})
    assert r.status_code == 404
    assert r.json()["detail"] == "order not found"


@respx.mock
async def test_seed_creates_three_orders(client):
    c, _ = client
    r = await c.post("/debug/seed?org_id=demo")
    assert r.status_code == 200
    payloads = r.json()
    assert len(payloads) == 3

    # Они появляются в GET /orders.
    r2 = await c.get("/orders?org_id=demo")
    assert r2.status_code == 200
    orders = r2.json()
    numbers = {o["number"] for o in orders}
    assert {"1001", "1002", "1003"}.issubset(numbers)

    # Категории смаппились на станции.
    seed_1001 = next(o for o in orders if o["number"] == "1001")
    stations = {i["station"] for i in seed_1001["items"]}
    assert stations == {"rolls", "pizza"}
    assert seed_1001["lat"] is not None and seed_1001["lon"] is not None


@respx.mock
async def test_seed_is_idempotent(client):
    c, _ = client
    await c.post("/debug/seed?org_id=demo")
    await c.post("/debug/seed?org_id=demo")
    r = await c.get("/orders?org_id=demo")
    numbers = [o["number"] for o in r.json()]
    assert numbers.count("1001") == 1


_IIKO = "https://api-ru.iiko.services"


def _token():
    respx.post(f"{_IIKO}/api/1/access_token").mock(
        return_value=httpx.Response(200, json={"token": "T"}))


@respx.mock
async def test_assign_courier_success_syncs_to_iiko(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-1", "items": [{"id": "emp-9", "displayName": "Иван Курьер"}]}]}))
    upd = respx.post(f"{_IIKO}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": "K1", "courier_name": "Иван Курьер"})
    assert r.status_code == 200
    assert upd.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_assign_courier_unmatched_sets_sync_error(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": []}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": "K1", "courier_name": "Нет В Cloud"})
    assert r.status_code == 200
    assert r.json()["iiko_sync_error"] == "courier_not_matched"
    # локальное назначение всё равно сохранено
    async with maker() as s:
        o = (await s.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert o.assigned_courier_name == "Нет В Cloud"


@respx.mock
async def test_delivered_with_payment_closes_in_iiko(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/payment_types").mock(
        return_value=httpx.Response(200, json={"paymentTypes": [
            {"id": "pt-cash", "name": "Наличные", "paymentTypeKind": "Cash"}]}))
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    close = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_kind": "Cash"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    assert pay.called and close.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_unknown_payment_sets_sync_error(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/payment_types").mock(
        return_value=httpx.Response(200, json={"paymentTypes": []}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_kind": "Cash"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    assert r.json()["iiko_sync_error"] == "payment_type_not_found"


@respx.mock
async def test_delivered_without_payment_kind_unchanged(client):
    c, maker = client
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
    r = await c.post(f"/orders/{oid}/delivered", json={"by": "Курьер"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True  # без iiko-вызовов


@respx.mock
async def test_delivered_iiko_down_still_200(client):
    """Если токен-эндпоинт недоступен — /delivered всё равно 200, локальный статус сохранён."""
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    respx.post(f"{_IIKO}/api/1/access_token").mock(
        side_effect=httpx.ConnectError("iiko down"))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_kind": "Cash"})
    assert r.status_code == 200
    body = r.json()
    assert body["delivered"] is True
    assert body["iiko_sync_error"] is not None and isinstance(body["iiko_sync_error"], str)
