"""Закрытие заказа ЗАЛА/ВЫНОСА в iiko: оплата + закрытие.

У заказов зала свои методы Cloud — deliveries/* для них не работают. Проверено
на проде: order/close отвечает «Table order with Id=… doesn't exist», то есть
метод принимает табличные заказы; order/pay нашему ключу запрещён, поэтому
оплату проводим через order/add_payments.
"""
import pytest
from app import iiko_cloud_write as w


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def fake_post(path, body):
        seen.append((path, body))
        return True, {}

    async def fake_await(org_id, ok, detail):
        return ok, detail

    monkeypatch.setattr(w, "_post", fake_post)
    monkeypatch.setattr(w, "_await_command", fake_await)
    return seen


@pytest.mark.asyncio
async def test_payment_goes_to_table_order_method(calls):
    await w.add_table_order_payment("org-1", "o1", [{"paymentTypeId": "p1", "sum": 100}])
    path, body = calls[0]
    assert path == "order/add_payments"
    assert body["organizationId"] == "org-1" and body["orderId"] == "o1"
    assert body["payments"] == [{"paymentTypeId": "p1", "sum": 100}]


@pytest.mark.asyncio
async def test_close_uses_table_order_method_not_delivery(calls):
    await w.close_table_order("org-1", "o1")
    path, body = calls[0]
    assert path == "order/close"
    assert "deliveries" not in path
    assert body == {"organizationId": "org-1", "orderId": "o1"}


@pytest.mark.asyncio
async def test_pay_and_close_does_payment_first(calls):
    """Закрыть неоплаченный заказ iiko не даст — сначала оплата."""
    ok, _ = await w.pay_and_close_table_order(
        "org-1", "o1", [{"paymentTypeId": "p1", "sum": 100}])
    assert ok is True
    assert [p for p, _ in calls] == ["order/add_payments", "order/close"]


@pytest.mark.asyncio
async def test_close_is_skipped_when_payment_failed(monkeypatch):
    """Не закрываем заказ, если оплата не прошла: закрытый без оплаты заказ
    потом руками не починить."""
    seen = []

    async def fake_post(path, body):
        seen.append(path)
        return (path != "order/add_payments"), {"error": "нет такого типа оплаты"}

    async def fake_await(org_id, ok, detail):
        return ok, detail

    monkeypatch.setattr(w, "_post", fake_post)
    monkeypatch.setattr(w, "_await_command", fake_await)

    ok, detail = await w.pay_and_close_table_order("org-1", "o1", [{"sum": 1}])
    assert ok is False
    assert seen == ["order/add_payments"]
