"""Время готовки отдельно по типам заказа: зал, вынос, доставка.

Для зала это единственная доступная метрика: в iiko у зальных заказов нет ни
«готово», ни «подали», а наши отметки есть.
"""
from datetime import datetime, timedelta
from app import analytics_stages as A
from app.models import Order, OrderItem


def _order(kind, minutes, number="1", ready=True):
    base = datetime(2026, 8, 12, 13, 0)
    o = Order(iiko_order_id=f"o{number}", org_id="org", number=number,
              service_type=kind)
    o.created_at = base
    it = OrderItem(product_id="p", name="Ролл", station="rolls")
    it.station_accepted_at = base
    it.station_ready_at = base + timedelta(minutes=minutes) if ready else None
    o.items = [it]
    return o


def test_splits_by_service_type():
    res = A.kitchen_time_by_kind([
        _order("DineIn", 8, "1"), _order("Common", 12, "2"),
        _order("DeliveryByClient", 20, "3"),
        _order("DeliveryByCourier", 30, "4"),
    ])
    assert res["hall"]["count"] == 2
    assert res["pickup"]["count"] == 1
    assert res["delivery"]["count"] == 1


def test_hall_takes_common_and_dinein_together():
    """«Обычный заказ» с кассы и заказ со стола — оба зал."""
    res = A.kitchen_time_by_kind([_order("DineIn", 10, "1"), _order("Common", 20, "2")])
    assert res["hall"]["median_seconds"] == 15 * 60
    assert res["hall"]["avg_seconds"] == 15 * 60


def test_counts_until_last_item_is_ready():
    """Заказ готов, когда готова ПОСЛЕДНЯЯ позиция, а не первая."""
    base = datetime(2026, 8, 12, 13, 0)
    o = Order(iiko_order_id="x", org_id="org", number="9", service_type="DineIn")
    o.created_at = base
    fast = OrderItem(product_id="a", name="Ролл", station="rolls",
                     station_accepted_at=base,
                     station_ready_at=base + timedelta(minutes=5))
    slow = OrderItem(product_id="b", name="Пицца", station="pizza",
                     station_accepted_at=base,
                     station_ready_at=base + timedelta(minutes=25))
    o.items = [fast, slow]
    res = A.kitchen_time_by_kind([o])
    assert res["hall"]["median_seconds"] == 25 * 60


def test_drinks_and_sauces_do_not_block_the_order():
    """Напитки и доп. порции соусов на кухне не готовят и «готовыми» не отмечают.

    Пока метрика требовала отметки у ВСЕХ позиций, любой заказ с колой выпадал
    из статистики: на проде из 111 доставок в счёт попадали 28.
    """
    base = datetime(2026, 8, 12, 13, 0)
    o = Order(iiko_order_id="d", org_id="org", number="5", service_type="DineIn")
    o.created_at = base
    roll = OrderItem(product_id="a", name="Ролл", station="rolls",
                     station_accepted_at=base,
                     station_ready_at=base + timedelta(minutes=12))
    cola = OrderItem(product_id="b", name="Кола", station="other")
    o.items = [roll, cola]
    res = A.kitchen_time_by_kind([o])
    assert res["hall"]["count"] == 1
    assert res["hall"]["median_seconds"] == 12 * 60


def test_order_without_kitchen_items_is_not_counted():
    """Заказ из одних напитков кухню не занимал — времени готовки у него нет."""
    base = datetime(2026, 8, 12, 13, 0)
    o = Order(iiko_order_id="e", org_id="org", number="6", service_type="DineIn")
    o.created_at = base
    o.items = [OrderItem(product_id="b", name="Кола", station="other")]
    assert A.kitchen_time_by_kind([o])["hall"]["count"] == 0


def test_unfinished_orders_do_not_count():
    """Пока не все позиции готовы, время готовки неизвестно."""
    res = A.kitchen_time_by_kind([_order("DineIn", 10, "1", ready=False)])
    assert res["hall"]["count"] == 0
    assert res["hall"]["median_seconds"] is None


def test_labels_are_human_readable():
    res = A.kitchen_time_by_kind([])
    assert res["hall"]["label"] == "Зал"
    assert res["togo"]["label"] == "Вынос"          # вынос с кассы
    assert res["pickup"]["label"] == "Самовывоз"    # заказан заранее с сайта
    assert res["delivery"]["label"] == "Доставка"


def test_unknown_service_type_is_ignored_not_counted_as_hall():
    res = A.kitchen_time_by_kind([_order("СтранныйТип", 10, "1")])
    assert all(res[k]["count"] == 0 for k in ("hall", "togo", "pickup", "delivery"))


def test_takeaway_is_its_own_kind():
    """Вынос считаем отдельно от зала: гость стоит и ждёт, время критичнее."""
    res = A.kitchen_time_by_kind([_order("ToGo", 9, "1")])
    assert res["togo"]["label"] == "Вынос"
    assert res["togo"]["count"] == 1
    assert res["hall"]["count"] == 0
