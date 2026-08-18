"""Состояние заказа зала перед закрытием: сколько осталось получить.

Платить вслепую нельзя: order/add_payments ДОБАВЛЯЕТ оплату, а не заменяет.
Если касса уже пробила оплату, вторая наша даст двойной чек.
"""
from app.iiko_cloud_write import table_order_state


def _payload(total=5000.0, payments=None, status="New"):
    return {"orders": [{"id": "o1", "order": {
        "sum": total, "status": status, "payments": payments,
        "number": 12, "items": [{"name": "Ролл"}]}}]}


def test_unpaid_order_owes_everything():
    st = table_order_state(_payload(total=5000))
    assert st["total"] == 5000 and st["paid"] == 0 and st["due"] == 5000


def test_paid_order_owes_nothing():
    st = table_order_state(_payload(total=5000, payments=[{"sum": 5000}]))
    assert st["paid"] == 5000 and st["due"] == 0


def test_partial_payment_leaves_the_rest():
    """Часть могли закрыть бонусами — платим ровно остаток, иначе PaymentSumTooLarge."""
    st = table_order_state(_payload(total=5000, payments=[{"sum": 1500}]))
    assert st["due"] == 3500


def test_already_closed_order_is_flagged():
    st = table_order_state(_payload(status="Closed"))
    assert st["closed"] is True


def test_missing_order_gives_nothing_to_guess_from():
    assert table_order_state({"orders": []}) is None
    assert table_order_state({}) is None
