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


def test_sla_breaches():
    from datetime import datetime
    def ord_(num, created, delivered):
        o = Order(iiko_order_id=num, org_id="org-1", number=num)
        o.created_at = created; o.delivered_at = delivered; o.items = []
        return o
    orders = [
        ord_("1", datetime(2026,6,20,12,0), datetime(2026,6,20,12,30)),  # 30m ok
        ord_("2", datetime(2026,6,20,13,0), datetime(2026,6,20,14,0)),   # 60m breach
        ord_("3", datetime(2026,6,21,12,0), datetime(2026,6,21,13,0)),   # 60m breach, next day
    ]
    r = A.sla_breaches(orders, threshold_sec=45*60)
    assert r["total"] == 3 and r["breached"] == 2
    assert round(r["rate"], 2) == 0.67
    days = {d["date"]: d for d in r["by_day"]}
    assert days["2026-06-20"]["breached"] == 1 and days["2026-06-21"]["breached"] == 1
    hours = {h["hour"]: h for h in r["by_hour"]}
    assert hours[13]["breached"] == 1


def test_pickup_lag():
    from datetime import datetime
    o = Order(iiko_order_id="1", org_id="org-1", number="9"); o.items = []
    o.assembled_at = datetime(2026,6,20,12,0); o.on_way_at = datetime(2026,6,20,12,5)  # 300s
    r = A.pickup_lag([o])
    assert r["avg"] == 300 and r["worst"][0]["number"] == "9"
