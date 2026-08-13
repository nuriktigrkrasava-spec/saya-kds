"""Почему заказ опоздал: кухня, ожидание курьера или дорога.

Опоздание берём из iiko, а виновника — из наших отметок: только у нас есть
время по этапам. Склейка по iiko_order_id.
"""
from datetime import datetime, timedelta
from app import late_reasons
from app.models import Order, OrderItem


def _order(oid="o1", queue=2, cooking=10, assembly=3, wait=4, delivery=20):
    """Заказ с заданными длительностями этапов в минутах."""
    base = datetime(2026, 8, 9, 18, 0)
    o = Order(iiko_order_id=oid, org_id="org", number="1")
    o.created_at = base
    acc = base + timedelta(minutes=queue)
    ready = acc + timedelta(minutes=cooking)
    it = OrderItem(product_id="p", name="Ролл", station="rolls")
    it.station_accepted_at = acc
    it.station_ready_at = ready
    o.items = [it]
    o.assembled_at = ready + timedelta(minutes=assembly)
    o.on_way_at = o.assembled_at + timedelta(minutes=wait)
    o.delivered_at = o.on_way_at + timedelta(minutes=delivery)
    return o


class TestNorms:
    def test_norms_are_medians_of_each_leg(self):
        norms = late_reasons.stage_norms([
            _order(cooking=10, wait=4, delivery=20),
            _order(cooking=12, wait=4, delivery=22),
            _order(cooking=14, wait=6, delivery=24),
        ])
        assert norms["kitchen"] == (2 + 12) * 60      # очередь + готовка
        assert norms["pickup"] == (3 + 4) * 60        # сборка + ожидание курьера
        assert norms["delivery"] == 22 * 60

    def test_no_orders_no_norms(self):
        assert late_reasons.stage_norms([]) == {}


class TestReason:
    def _norms(self):
        return {"kitchen": 12 * 60, "pickup": 7 * 60, "delivery": 20 * 60}

    def test_slow_kitchen_is_blamed(self):
        reason = late_reasons.reason_for(_order(cooking=40), self._norms())
        assert reason["key"] == "kitchen"
        assert reason["label"] == "Долгая готовка"
        assert reason["over_seconds"] == (2 + 40) * 60 - 12 * 60

    def test_courier_kept_waiting_is_blamed(self):
        reason = late_reasons.reason_for(_order(wait=30), self._norms())
        assert reason["key"] == "pickup"
        assert reason["label"] == "Ждали курьера"

    def test_long_road_is_blamed(self):
        reason = late_reasons.reason_for(_order(delivery=60), self._norms())
        assert reason["key"] == "delivery"
        assert reason["label"] == "Долго везли"

    def test_worst_excess_wins_not_the_longest_stage(self):
        """Дорога всегда длиннее готовки, но виноват тот, кто СИЛЬНЕЕ вышел за норму."""
        reason = late_reasons.reason_for(_order(cooking=40, delivery=25), self._norms())
        assert reason["key"] == "kitchen"

    def test_order_without_our_marks_has_no_reason(self):
        o = Order(iiko_order_id="x", org_id="org", number="2")
        o.created_at = datetime(2026, 8, 9, 18, 0)
        o.items = []
        assert late_reasons.reason_for(o, self._norms()) is None

    def test_nothing_exceeded_means_no_single_culprit(self):
        """Все этапы в норме — опоздание набежало по мелочи, винить некого."""
        assert late_reasons.reason_for(_order(), self._norms()) is None


class TestAttach:
    def test_reasons_are_attached_and_counted(self):
        late = [{"order_id": "o1", "number": 1}, {"order_id": "o2", "number": 2},
                {"order_id": "нет-у-нас", "number": 3}]
        ours = [_order("o1", cooking=40), _order("o2", delivery=60)]
        summary = late_reasons.attach_reasons(late, ours)

        assert late[0]["reason"]["key"] == "kitchen"
        assert late[1]["reason"]["key"] == "delivery"
        assert late[2]["reason"] is None
        assert summary == {"kitchen": 1, "delivery": 1, "unknown": 1}
