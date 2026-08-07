import time
import httpx, respx, pytest
from app import iiko_cloud_write as w

BASE = "https://api-ru.iiko.services"


def _token_route():
    return respx.post(f"{BASE}/api/1/access_token").mock(
        return_value=httpx.Response(200, json={"token": "T"}))


def _couriers_route():
    return respx.post(f"{BASE}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-c", "items": [
                {"id": "emp-42", "displayName": "Адильхан Курьер"}]}]}))


@respx.mock
async def test_assign_courier_ok_sends_employee_id():
    _token_route()
    route = respx.post(f"{BASE}/api/1/deliveries/update_order_courier").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    # Команда асинхронная: успех подтверждается только через commands/status.
    respx.post(f"{BASE}/api/1/commands/status").mock(
        return_value=httpx.Response(200, json={"state": "Success"}))
    ok, detail = await w.assign_courier("org-1", "ord-1", "emp-1")
    assert ok is True
    body = route.calls.last.request.read()
    assert b'"employeeId"' in body and b'"emp-1"' in body


@respx.mock
async def test_close_order_error_returns_false_with_detail():
    _token_route()
    respx.post(f"{BASE}/api/1/deliveries/close").mock(
        return_value=httpx.Response(500, json={"error": "DELIVERY_DOESNT_EXIST"}))
    ok, detail = await w.close_order("org-1", "ord-1")
    assert ok is False
    assert detail.get("error") == "DELIVERY_DOESNT_EXIST"


@respx.mock
async def test_set_payments_sends_payment_items_key():
    _token_route()
    route = respx.post(f"{BASE}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={}))
    ok, _ = await w.set_payments("org-1", "ord-1", [{"paymentTypeKind": "Cash"}])
    assert ok is True
    assert b'"paymentItems"' in route.calls.last.request.read()


@respx.mock
async def test_cloud_courier_id_matches_by_normalized_name():
    w.reset_caches()
    _token_route(); _couriers_route()
    got = await w.cloud_courier_id("org-c", "адильхан   курьер")
    assert got == "emp-42"


@respx.mock
async def test_cloud_courier_id_none_when_no_match():
    w.reset_caches()
    _token_route(); _couriers_route()
    assert await w.cloud_courier_id("org-c", "Кто-то Другой") is None


@respx.mock
async def test_cloud_courier_id_uses_cache_second_call():
    w.reset_caches()
    _token_route()
    route = _couriers_route()
    await w.cloud_courier_id("org-cache", "Адильхан Курьер")
    await w.cloud_courier_id("org-cache", "Адильхан Курьер")
    assert route.call_count == 1  # второй раз — из кэша


def _paytypes_route():
    return respx.post(f"{BASE}/api/1/payment_types").mock(
        return_value=httpx.Response(200, json={"paymentTypes": [
            {"id": "pt-cash", "name": "Наличные", "paymentTypeKind": "Cash"},
            {"id": "pt-card", "name": "Карта", "paymentTypeKind": "Card"}]}))



@respx.mock
async def test_post_returns_false_when_token_fails():
    """Если токен-эндпоинт недоступен — _post возвращает (False, dict), не бросает."""
    respx.post(f"{BASE}/api/1/access_token").mock(
        side_effect=httpx.ConnectError("boom"))
    ok, detail = await w.assign_courier("org-1", "ord-1", "emp-1")
    assert ok is False
    assert isinstance(detail, dict)


@respx.mock
async def test_cloud_courier_id_none_when_token_fails():
    """Если токен-эндпоинт недоступен — cloud_courier_id возвращает None и не кэширует провал."""
    w.reset_caches()
    # Первый вызов: токен падает
    respx.post(f"{BASE}/api/1/access_token").mock(
        side_effect=httpx.ConnectError("boom"))
    result = await w.cloud_courier_id("org-fail-unique", "Адильхан Курьер")
    assert result is None

    # Второй вызов: токен работает, курьер есть — должен найти (провал не закэширован)
    w.reset_caches()
    respx.post(f"{BASE}/api/1/access_token").mock(
        return_value=httpx.Response(200, json={"token": "T"}))
    respx.post(f"{BASE}/api/1/employees/couriers").mock(
        return_value=httpx.Response(200, json={"employees": [
            {"organizationId": "org-fail-unique", "items": [
                {"id": "emp-99", "displayName": "Адильхан Курьер"}]}]}))
    result2 = await w.cloud_courier_id("org-fail-unique", "Адильхан Курьер")
    assert result2 == "emp-99"


@respx.mock
async def test_set_delivery_status_ok():
    _token_route()
    route = respx.post(f"{BASE}/api/1/deliveries/update_order_delivery_status").mock(
        return_value=httpx.Response(200, json={}))
    ok, _ = await w.set_delivery_status("org-1", "ord-1", "Delivered")
    assert ok is True
    body = route.calls.last.request.read()
    assert b'"deliveryStatus"' in body and b'"Delivered"' in body


def test_payment_item_by_id_externally_false():
    item = w.payment_item_by_id("org-x", "pt-1", "Card", 6990)
    assert item == {"paymentTypeId": "pt-1", "paymentTypeKind": "Card",
                    "sum": 6990, "isProcessedExternally": False}


# ── Асинхронность команд iiko ───────────────────────────────────────────────
# deliveries/* возвращают 200 + correlationId СРАЗУ, а выполняются в фоне.
# Пока команда не подтверждена через commands/status, слать следующую нельзя:
# close, обогнавший оплату, печатает чек и оставляет заказ ОТКРЫТЫМ.

def _status_route(*states):
    """commands/status отвечает переданными состояниями по очереди."""
    responses = [httpx.Response(200, json={"state": s}) for s in states]
    return respx.post(f"{BASE}/api/1/commands/status").mock(side_effect=responses)


@respx.mock
async def test_close_waits_for_command_success():
    """200 на close — ещё не успех: ждём подтверждения выполнения."""
    _token_route()
    respx.post(f"{BASE}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    route = _status_route("InProgress", "Success")
    ok, _ = await w.close_order("org-1", "ord-1")
    assert ok is True
    assert route.call_count == 2  # дождались, а не поверили первому ответу


@respx.mock
async def test_close_reports_failure_when_command_errors():
    """Команда принята (200), но упала в iiko -> это НЕ успех."""
    _token_route()
    respx.post(f"{BASE}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    _status_route("Error")
    ok, detail = await w.close_order("org-1", "ord-1")
    assert ok is False, "200 + Error в статусе не должен считаться успехом"


@respx.mock
async def test_set_payments_waits_too():
    """Оплата тоже асинхронна: close нельзя слать, пока она не применилась."""
    _token_route()
    respx.post(f"{BASE}/api/1/deliveries/update_order_payments").mock(
        return_value=httpx.Response(200, json={"correlationId": "c2"}))
    route = _status_route("Success")
    ok, _ = await w.set_payments("org-1", "ord-1", [{"sum": 100}])
    assert ok is True
    assert route.call_count == 1


@respx.mock
async def test_command_not_found_fails_fast():
    """410 PASSED_COMMAND_NOT_FOUND — окончательный отказ, а не повод ждать таймаут.

    Так iiko отвечает про команду, которой нет в хранилище (например, она упала
    сразу). Раньше мы такой ответ не распознавали и честно висели 15 секунд,
    заставляя курьера ждать впустую."""
    _token_route()
    respx.post(f"{BASE}/api/1/deliveries/close").mock(
        return_value=httpx.Response(200, json={"correlationId": "c1"}))
    route = respx.post(f"{BASE}/api/1/commands/status").mock(
        return_value=httpx.Response(410, json={
            "correlationId": "c1", "error": "PASSED_COMMAND_NOT_FOUND",
            "errorDescription": "Command not found in commands storage"}))

    started = time.monotonic()
    ok, detail = await w.close_order("org-1", "ord-1")
    elapsed = time.monotonic() - started

    assert ok is False
    assert elapsed < 3, f"ждали {elapsed:.1f}с вместо мгновенного отказа"
    assert route.call_count == 1, "повторять опрос несуществующей команды бессмысленно"
    assert detail.get("error") == "PASSED_COMMAND_NOT_FOUND"
