import httpx, respx, pytest
from app import iiko_cloud_write as w

BASE = "https://api-ru.iiko.services"


def _token_route():
    return respx.post(f"{BASE}/api/1/access_token").mock(
        return_value=httpx.Response(200, json={"token": "T"}))


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
