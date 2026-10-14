import pytest
from app.security import courier_ref
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


def _command_ok():
    """Команды deliveries/* асинхронные: успех подтверждается commands/status."""
    return respx.post(f"{_IIKO}/api/1/commands/status").mock(
        return_value=httpx.Response(200, json={"state": "Success"}))


def _pay(sum_, name="Оплата", pid="pt-x", kind="Card", **flags):
    """Оплата в ответе iiko. По умолчанию — обычная, уже проведённая."""
    return {"sum": sum_, "paymentType": {"id": pid, "name": name, "kind": kind},
            "isPreliminary": False, "isProcessedExternally": False, **flags}


def _by_id(total=1000.0, payments=(), service="DeliveryByCourier"):
    """Состояние заказа в iiko, которое /delivered читает перед записью."""
    return respx.post(f"{_IIKO}/api/1/deliveries/by_id").mock(
        return_value=httpx.Response(200, json={"orders": [{"id": "o1", "order": {
            "sum": total, "payments": list(payments),
            "orderType": {"orderServiceType": service}}}]}))


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
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={"correlationId": "c2"}))
    _command_ok()
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": courier_ref("K1"), "courier_name": "Иван Курьер"})
    assert r.status_code == 200
    assert upd.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_assign_courier_moves_order_to_on_way_in_iiko(client):
    """Назначили курьера — заказ должен уехать во вкладку «В пути» в iiko.

    Курьер физически забирает заказ в момент передачи сборщиком: кнопки «Забрал»
    в TG-апе курьера нет. Раньше KDS ставил только курьера, статус доставки в
    iiko не двигался, и заказ висел в «Готов» — жалоба заказчика 07.08.2026."""
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-1", "items": [{"id": "emp-9", "displayName": "Иван Курьер"}]}]}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={"correlationId": "c2"}))
    _command_ok()
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": courier_ref("K1"), "courier_name": "Иван Курьер"})
    assert r.status_code == 200
    assert st.called, "статус доставки в iiko не отправлен — заказ не попадёт в «В пути»"
    assert b'"OnWay"' in st.calls.last.request.read()
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_assign_courier_on_way_failure_does_not_hide_assignment(client):
    """Курьер назначен, а перевод в «В пути» не прошёл — назначение не откатываем,
    но ошибку показываем сборщику."""
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-1", "items": [{"id": "emp-9", "displayName": "Иван Курьер"}]}]}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(500, json={"error": "BOOM"}))
    _command_ok()
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": courier_ref("K1"), "courier_name": "Иван Курьер"})
    assert r.status_code == 200
    assert r.json()["iiko_sync_error"]
    async with maker() as s:
        o = (await s.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert o.assigned_courier_name == "Иван Курьер"


@respx.mock
async def test_take_moves_order_to_on_way_in_iiko(client):
    """Кнопка «Забрал» на веб-экране курьера тоже двигает статус доставки в iiko."""
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    _command_ok()
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/take", json={"by": "Курьер"})
    assert r.status_code == 200 and r.json()["on_way"] is True
    assert st.called
    assert b'"OnWay"' in st.calls.last.request.read()


@respx.mock
async def test_assign_courier_refused_for_pickup_order(client):
    """Яндекс/Wolt/самовывоз — не наша доставка, курьера на них не вешаем.

    Наша копия типа заказа бывает устаревшей (заказ №91 на проде значился
    курьерским, а в iiko был DeliveryByClient — назначение отбилось ошибкой).
    Спрашиваем iiko и чиним service_type у себя, чтобы карточка сборщика
    переключилась на «Выдан»."""
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    _by_id(service="DeliveryByClient")
    upd = respx.post(f"{_IIKO}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/assign-courier",
                     json={"courier_code": courier_ref("K1"), "courier_name": "Иван Курьер"})
    assert r.status_code == 409
    assert not upd.called
    async with maker() as s:
        o = (await s.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert o.assigned_courier_name is None, "курьер не должен зависнуть на заказе"
        assert o.service_type == "DeliveryByClient", "тип заказа поправлен по iiko"


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
                     json={"courier_code": courier_ref("K1"), "courier_name": "Нет В Cloud"})
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
    _by_id(payments=[])
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    close = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    _command_ok()
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-cash", "payment_kind": "Cash"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True
    assert pay.called and st.called and close.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_unknown_payment_sets_sync_error(client):
    from app import iiko_cloud_write
    iiko_cloud_write.reset_caches()
    c, maker = client
    _token()
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(500, json={"error": "payment_failed"}))
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-cash", "payment_kind": "Cash"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert r.json()["iiko_sync_error"]            # error recorded
    assert not st.called and not cl.called        # payment failed -> no Delivered, no close


@respx.mock
async def test_delivered_without_payment_kind_unchanged(client):
    c, maker = client
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
    r = await c.post(f"/orders/{oid}/delivered", json={"by": "Курьер"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True  # без iiko-вызовов (нет payment_type_id)


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
                     json={"by": "Курьер", "payment_type_id": "pt-cash", "payment_kind": "Cash"})
    assert r.status_code == 200
    body = r.json()
    assert body["delivered"] is True
    assert body["iiko_sync_error"] is not None and isinstance(body["iiko_sync_error"], str)


@respx.mock
async def test_delivered_full_close_flow(client):
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(payments=[])
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(return_value=httpx.Response(200, json={}))
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-1", "payment_kind": "Card"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert pay.called and st.called and cl.called
    body = st.calls.last.request.read()
    assert b'"Delivered"' in body
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_paid_order_does_not_add_second_payment(client):
    """Заказ уже оплачен в iiko — вторую оплату не шлём.

    update_order_payments не заменяет оплаты, а ДОБАВЛЯЕТ: на проде 07.08.2026
    заказ №91 на 8495 ₸ получил вторую «Яндекс безнал» 8495 ₸ и стал «оплачен»
    на 16990 ₸. Оплату шлём только когда заказ в iiko реально не закрыт по деньгам."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=8495.0, payments=[{"sum": 8495.0, "isPreliminary": False}])
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-1", "payment_kind": "Card"})
    assert r.status_code == 200
    assert not pay.called, "повторная оплата уже оплаченного заказа — двойной чек"
    assert st.called and cl.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_preliminary_payment_closes_without_second_payment(client):
    """Предоплата на всю сумму — заказ просто закрываем, вторую оплату не шлём.

    Прод 09.08.2026, заказ №23: в iiko висела предварительная «Терминал Kaspi»
    на все 22990 ₸, курьер в аппе выбрал «Kaspi для QR» — iiko отбил
    «Тип „Kaspi для QR“ некомбинируемый. Заказ не может содержать других типов
    оплаты», close не вызывался вовсе, и заказ навсегда остался в «В пути»."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=1000.0, payments=[{"sum": 1000.0, "isPreliminary": True,
                                    "paymentType": {"id": "pt-kaspi", "kind": "Card"}}])
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-1", "payment_kind": "Card"})
    assert r.status_code == 200
    assert not pay.called, "по предоплаченному заказу вторая оплата = отказ iiko"
    assert st.called and cl.called
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_prepaid_close_retry_confirms_same_payment_type(client):
    """Если close по предоплате не прошёл — подтверждаем ЕЁ ЖЕ типом и закрываем.

    Своим типом (тем, что выбрал курьер) платить нельзя: некомбинируемые типы
    iiko отбивает, и заказ остаётся открытым."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=1000.0, payments=[{"sum": 1000.0, "isPreliminary": True,
                                    "paymentType": {"id": "pt-kaspi", "kind": "Card"}}])
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(side_effect=[
        httpx.Response(500, json={"error": "order is not paid"}),
        httpx.Response(200, json={}),
    ])
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-1", "payment_kind": "Card"})
    assert r.status_code == 200
    assert pay.called, "оплату всё же подтверждаем — но типом из самого заказа"
    body = pay.calls.last.request.read()
    assert b"pt-kaspi" in body and b"pt-1" not in body
    assert b'"sum":1000.0' in body, "сумму берём из iiko, а не из своей БД"
    assert cl.call_count == 2 and r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_pays_only_the_remainder_of_a_split_order(client):
    """Часть уже оплачена бонусами — доплачиваем ОСТАТОК, а не всю сумму.

    Прод 09.08.2026, заказ №36 на 17070 ₸: 8535 ₸ уже списаны «БАЛЛАМИ»
    (isProcessedExternally), а мы отправили оплату на все 17070 ₸ — заказ
    получил лишнюю оплату, iiko ответил PaymentSumTooLarge, кассир разбирал
    руками. Платить можно только разницу."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=17070.0, payments=[
        _pay(8535.0, "БАЛЛЫ", "pt-bonus", isProcessedExternally=True)])
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-kaspi", "payment_kind": "Card"})
    assert r.status_code == 200
    body = pay.calls.last.request.read()
    assert b'"sum":8535.0' in body, "платим разницу 17070 - 8535"
    assert b"pt-kaspi" in body, "тип берём тот, которым курьер реально принял деньги"
    assert cl.called and r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_never_pays_with_bonus_type_from_our_db(client):
    """Тип оплаты из нашей БД может оказаться бонусной частью сплита.

    ingest раньше брал payments[0] — у заказа №36 это были «БАЛЛЫ», и заказ
    закрылся баллами вместо онлайна. Когда курьер тип не выбирал, берём тип
    расчёта из самого iiko (предоплата/не-внешний платёж), а не бонусный."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=17070.0, payments=[
        _pay(8535.0, "БАЛЛЫ", "pt-bonus", isProcessedExternally=True),
        _pay(8535.0, "Терминал Kaspi", "pt-kaspi", isPreliminary=True)])
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        o = (await s.execute(select(Order))).scalar_one()
        o.payment_type_id, o.payment_type_kind = "pt-bonus", "Card"
        await s.commit()
        oid = o.id

    r = await c.post(f"/orders/{oid}/delivered", json={"by": "Курьер"})
    assert r.status_code == 200
    assert not pay.called, "предоплата покрывает остаток — доплачивать нечего"
    assert cl.called and r.json()["iiko_sync_error"] is None


@respx.mock
async def test_close_info_tells_the_courier_what_is_left_to_pay(client):
    """Курьер перед закрытием видит правду из iiko: сколько и чем доплатить."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=17070.0, payments=[
        _pay(8535.0, "БАЛЛЫ", "pt-bonus", isProcessedExternally=True)])
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.get(f"/orders/{oid}/close-info")
    assert r.status_code == 200
    b = r.json()
    assert b["total"] == 17070.0 and b["paid"] == 8535.0 and b["due"] == 8535.0
    assert b["needs_choice"] is True, "чем добирать остаток — знает только курьер"
    assert b["payment_type_name"] is None


@respx.mock
async def test_close_info_prepaid_order_needs_no_choice(client):
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=1000.0, payments=[_pay(1000.0, "КАСПИ онлайн", "pt-kaspi", isPreliminary=True)])
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    b = (await c.get(f"/orders/{oid}/close-info")).json()
    assert b["due"] == 0 and b["needs_choice"] is False
    assert b["payment_type_name"] == "КАСПИ онлайн", "показываем реальный тип из iiko"


@respx.mock
async def test_delivered_pickup_order_skips_delivery_status(client):
    """DeliveryByClient (Яндекс/Wolt/самовывоз) — этапов доставки у заказа нет.

    Любой update_order_delivery_status по такому заказу iiko отбивает ошибкой 173
    «Статус заказа был обновлен на кассе ресторана» (проверено на проде на заказе
    №91). Раньше цепочка на этом обрывалась и close не вызывался вовсе — заказ
    оставался открытым."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    _by_id(total=8495.0, payments=[{"sum": 8495.0, "isPreliminary": False}],
           service="DeliveryByClient")
    st = respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(500, json={"error": "BOOM"}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Сборщик", "payment_type_id": "pt-1", "payment_kind": "Card"})
    assert r.status_code == 200
    assert not st.called, "для самовывоза статус доставки слать нельзя"
    assert cl.called, "заказ всё равно должен закрываться"
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivery_never_falls_into_the_hall_branch(client):
    """Не прочиталось состояние доставки — это сбой, а не повод считать её залом.

    Иначе временная ошибка чтения увела бы курьерский заказ в методы зала и
    оплатила его не тем способом. Тип заказа знаем сами — на него и смотрим.
    """
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    respx.post(f"{_IIKO}/api/1/deliveries/by_id").mock(
        return_value=httpx.Response(500, json={}))
    # Сегодня order/by_id курьерский заказ не находит (проверено на проде), но
    # закладываться на это нельзя: пусть в тесте находит — мы всё равно не
    # должны туда идти.
    respx.post(f"{_IIKO}/api/1/order/by_id").mock(return_value=httpx.Response(
        200, json={"orders": [{"order": {"sum": 9000, "status": "New",
                                         "payments": None}}]}))
    tpay = respx.post(f"{_IIKO}/api/1/order/add_payments").mock(
        return_value=httpx.Response(200, json={}))
    tclose = respx.post(f"{_IIKO}/api/1/order/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        order = (await s.execute(select(Order))).scalar_one()
        order.service_type = "DeliveryByCourier"
        await s.commit()
        oid = order.id

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Сборщик", "payment_type_id": "pt-1"})
    assert r.status_code == 200
    assert not tpay.called and not tclose.called, "доставку методами зала не трогаем"
    assert r.json()["iiko_sync_error"] is not None, "сбой чтения нельзя прятать"


@respx.mock
async def test_delivered_hall_order_is_paid_and_closed_by_us(client):
    """Заказ зала закрываем мы, своими методами: у него другие эндпоинты Cloud.

    Раньше такие заказы мы не трогали вовсе («пусть закрывает касса»), и заказ
    висел на планшете, пока кассир до него не дойдёт. Теперь сборщик выбирает
    тип оплаты и заказ закрывается сразу.

    deliveries/* тут не годятся: они табличный заказ не находят.
    """
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    respx.post(f"{_IIKO}/api/1/deliveries/by_id").mock(
        return_value=httpx.Response(200, json={"orders": []}))
    dpay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    dclose = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    respx.post(f"{_IIKO}/api/1/order/by_id").mock(return_value=httpx.Response(
        200, json={"orders": [{"order": {"sum": 4500, "status": "New",
                                         "payments": None}}]}))
    tpay = respx.post(f"{_IIKO}/api/1/order/add_payments").mock(
        return_value=httpx.Response(200, json={}))
    tclose = respx.post(f"{_IIKO}/api/1/order/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Сборщик", "payment_type_id": "pt-1",
                           "payment_kind": "Cash"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert tpay.called and tclose.called, "зал закрываем табличными методами"
    assert not dpay.called and not dclose.called, "deliveries/* тут не при чём"
    assert r.json()["iiko_sync_error"] is None


@respx.mock
async def test_delivered_unreadable_order_does_not_guess_payment(client):
    """Не смогли прочитать заказ в iiko — оплату наугад не шлём (риск двойного чека)."""
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    respx.post(f"{_IIKO}/api/1/deliveries/by_id").mock(
        return_value=httpx.Response(500, json={"error": "boom"}))
    pay = respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()

    r = await c.post(f"/orders/{oid}/delivered",
                     json={"by": "Курьер", "payment_type_id": "pt-1", "payment_kind": "Card"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert not pay.called and not cl.called
    assert r.json()["iiko_sync_error"]


@respx.mock
async def test_payment_types_endpoint(client):
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, _ = client; _token()
    respx.post(f"{_IIKO}/api/1/payment_types").mock(return_value=httpx.Response(200, json={"paymentTypes":[
        {"id":"pt-1","name":"Наличные","paymentTypeKind":"Cash","isDeleted":False},
        {"id":"pt-2","name":"Glovo","paymentTypeKind":"Card","isDeleted":True}]}))
    r = await c.get("/payment-types?org_id=org-1")
    assert r.status_code == 200
    rows = r.json()
    assert {"id":"pt-1","name":"Наличные","kind":"Cash"} in rows
    assert all(x["id"] != "pt-2" for x in rows)  # удалённые отфильтрованы


@respx.mock
async def test_delivered_status_fail_sets_error_no_close(client):
    from app import iiko_cloud_write; iiko_cloud_write.reset_caches()
    c, maker = client; _token()
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_payments").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{_IIKO}/api/1/deliveries/update_order_delivery_status").mock(return_value=httpx.Response(500, json={"error":"BOOM"}))
    cl = respx.post(f"{_IIKO}/api/1/deliveries/close").mock(return_value=httpx.Response(200, json={}))
    async with maker() as s:
        oid = (await s.execute(select(Order.id))).scalar_one()
    r = await c.post(f"/orders/{oid}/delivered", json={"by":"Курьер","payment_type_id":"pt-1","payment_kind":"Card"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert not cl.called  # close не зовём, если Delivered не проставился
    assert r.json()["iiko_sync_error"]


@pytest.fixture(autouse=True)
def courier_directory(monkeypatch, request):
    """Mock RMS identity verification; iiko write mocks remain in each behavior test."""
    from app import rms, api
    async def branches():
        return [{"code": "AAA", "employees": [
            {"code": "K1", "name": "Нет В Cloud" if "unmatched" in request.node.name else "Иван Курьер", "role": "КР"},
            {"code": "C7", "name": "Айдос", "role": "КР"}]}]
    async def branch_for_org(org_id): return "AAA" if org_id == "org-1" else "OTHER"
    monkeypatch.setattr(rms, "branches_with_employees", branches)
    monkeypatch.setattr(api, "_branch_code_for_org", branch_for_org)
