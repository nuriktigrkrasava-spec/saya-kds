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
async def test_payment_item_builds_cash_item():
    w.reset_caches()
    _token_route(); _paytypes_route()
    item = await w.payment_item("org-p", "Cash", 8500)
    assert item == {"paymentTypeKind": "Cash", "sum": 8500,
                    "paymentTypeId": "pt-cash", "isProcessedExternally": True}


@respx.mock
async def test_payment_item_none_for_unknown_kind():
    w.reset_caches()
    _token_route(); _paytypes_route()
    assert await w.payment_item("org-p2", "Bonus", 100) is None
