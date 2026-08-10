"""Шкала «Путь заказа · в среднем»: готовка → сборка → ожидание → доставка."""
from datetime import datetime
from app import analytics_stages as A
from app.models import Order, OrderItem


def _order(acc, ready, assembled, on_way, delivered, created=None):
    o = Order(iiko_order_id="x", org_id="org-1", number="1")
    o.created_at = created or datetime(2026, 6, 20, 12, 0, 0)
    it = OrderItem(product_id="p", name="Ролл", station="rolls")
    it.station_accepted_at = acc
    it.station_ready_at = ready
    o.items = [it]
    o.assembled_at = assembled
    o.on_way_at = on_way
    o.delivered_at = delivered
    return o


def test_order_path_has_four_stages_in_order():
    path = A.order_path([])
    assert [s["key"] for s in path["stages"]] == [
        "cooking", "assembly", "pickup_wait", "delivery"]
    assert [s["label"] for s in path["stages"]] == [
        "Готовка", "Сборка", "Ожидание", "Доставка"]


def test_order_path_averages_in_minutes_and_total():
    d = datetime(2026, 6, 20, 12, 0, 0)
    o = _order(acc=d.replace(minute=2), ready=d.replace(minute=26),      # готовка 24м
               assembled=d.replace(minute=39),                           # сборка 13м
               on_way=d.replace(minute=43),                              # ожидание 4м
               delivered=d.replace(minute=59))                           # доставка 16м
    path = A.order_path([o])
    by = {s["key"]: s for s in path["stages"]}
    assert by["cooking"]["minutes"] == 24
    assert by["assembly"]["minutes"] == 13
    assert by["pickup_wait"]["minutes"] == 4
    assert by["delivery"]["minutes"] == 16
    assert path["total_minutes"] == 57
    assert by["cooking"]["seconds"] == 24 * 60
    assert by["cooking"]["count"] == 1


def test_order_path_ignores_stages_without_data():
    """Заказ без сборки не должен обнулять среднее по сборке — он в него не входит."""
    d = datetime(2026, 6, 20, 12, 0, 0)
    full = _order(acc=d.replace(minute=0), ready=d.replace(minute=10),
                  assembled=d.replace(minute=20), on_way=d.replace(minute=25),
                  delivered=d.replace(minute=45))
    partial = _order(acc=d.replace(minute=0), ready=d.replace(minute=20),
                     assembled=None, on_way=None, delivered=None)
    path = A.order_path([full, partial])
    by = {s["key"]: s for s in path["stages"]}
    assert by["cooking"]["minutes"] == 15 and by["cooking"]["count"] == 2
    assert by["assembly"]["minutes"] == 10 and by["assembly"]["count"] == 1


def test_order_path_empty_when_no_orders():
    path = A.order_path([])
    assert path["total_minutes"] == 0
    assert all(s["minutes"] is None and s["count"] == 0 for s in path["stages"])
