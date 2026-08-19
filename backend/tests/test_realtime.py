from app import realtime
from app.models import Order, OrderItem


def test_room_name():
    assert realtime.room("org-1", "rolls") == "org:org-1:rolls"


def test_order_payload_groups_items_by_station():
    order = Order(id=5, iiko_order_id="o1", org_id="org-1", number="101", customer_name="Иван")
    order.items = [
        OrderItem(id=1, product_id="p1", name="Ролл", station="rolls", amount=2),
        OrderItem(id=2, product_id="p2", name="Пицца", station="pizza", amount=1),
    ]
    p = realtime.order_payload(order)
    assert p["number"] == "101"
    assert p["org_id"] == "org-1"
    assert {i["name"] for i in p["items"] if i["station"] == "rolls"} == {"Ролл"}
    assert p["items"][0]["amount"] == 2


def test_order_payload_exposes_courier_and_assembled_at():
    """Сборщику нужно видеть, КОМУ передан заказ и КОГДА (таймер «в руках курьера»)."""
    from datetime import datetime
    order = Order(id=6, iiko_order_id="o2", org_id="org-1", number="102",
                  assigned_courier_code="77", assigned_courier_name="Адильхан",
                  assembled_at=datetime(2026, 7, 6, 12, 0, 0))
    order.items = []
    p = realtime.order_payload(order)
    assert p["assigned_courier_name"] == "Адильхан"
    assert p["assigned_courier_code"] == "77"
    assert p["assembled_at"] == "2026-07-06T12:00:00Z"


def test_payload_carries_the_order_total():
    """Курьеру нужна сумма заказа: он везёт деньги, а не только еду.

    В выдаче её не было вовсе — мини-апп показывал сумму только в окне
    закрытия, и то из отдельного запроса в iiko.
    """
    from app.models import Order
    from app import realtime
    order = Order(iiko_order_id="x", org_id="org-1", number="7", total=12500.0)
    order.items = []
    assert realtime.order_payload(order)["total"] == 12500.0


def test_payload_total_may_be_absent():
    from app.models import Order
    from app import realtime
    order = Order(iiko_order_id="y", org_id="org-1", number="8")
    order.items = []
    assert realtime.order_payload(order)["total"] is None
