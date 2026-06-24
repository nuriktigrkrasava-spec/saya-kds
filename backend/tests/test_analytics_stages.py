from datetime import datetime, timedelta
from app import analytics_stages as A
from app.models import Order, OrderItem


def _order(**ts):
    base = datetime(2026, 6, 20, 12, 0, 0)
    o = Order(iiko_order_id="x", org_id="org-1", number="1")
    o.created_at = base
    it = OrderItem(product_id="p", name="Ролл", station="rolls")
    it.station_accepted_at = ts.get("acc")
    it.station_ready_at = ts.get("ready")
    o.items = [it]
    o.assembled_at = ts.get("assembled")
    o.on_way_at = ts.get("on_way")
    o.delivered_at = ts.get("delivered")
    return o, base


def test_order_stages_durations_and_bottleneck():
    o, base = _order(
        acc=datetime(2026, 6, 20, 12, 2),       # queue 120s
        ready=datetime(2026, 6, 20, 12, 12),    # cooking 600s  <-- bottleneck
        assembled=datetime(2026, 6, 20, 12, 14),# assembly 120s
        on_way=datetime(2026, 6, 20, 12, 19),   # pickup_wait 300s
        delivered=datetime(2026, 6, 20, 12, 29) # delivery 600s -> tie, first max wins cooking
    )
    stages = A.order_stages(o)
    by = {s["key"]: s for s in stages}
    assert by["queue"]["seconds"] == 120
    assert by["cooking"]["seconds"] == 600
    assert by["iiko_close"]["seconds"] is None  # close_at не передан
    bott = [s["key"] for s in stages if s["bottleneck"]]
    assert bott == ["cooking"]  # первый максимум


def test_order_stages_none_when_missing_timestamp():
    o, _ = _order(acc=None, ready=None)
    by = {s["key"]: s for s in A.order_stages(o)}
    assert by["queue"]["seconds"] is None and by["cooking"]["seconds"] is None


def test_pctl_and_aggregates():
    assert A.pctl([], 95) is None
    assert A.pctl([10], 95) == 10
    o1, _ = _order(acc=datetime(2026,6,20,12,1), ready=datetime(2026,6,20,12,3))   # cooking 120
    o2, _ = _order(acc=datetime(2026,6,20,12,1), ready=datetime(2026,6,20,12,6))   # cooking 300
    agg = A.stage_aggregates([o1, o2])
    cooking = next(s for s in agg["stages"] if s["key"] == "cooking")
    assert cooking["count"] == 2 and cooking["avg"] == 210
    assert agg["bottleneck_distribution"]["cooking"] == 2
