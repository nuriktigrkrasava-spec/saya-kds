from datetime import datetime
from app.analytics_stages import courier_zone_load

SQUARE = [[51.1, 71.4], [51.1, 71.5], [51.2, 71.5], [51.2, 71.4]]
ZONES = [{"id": 1, "name": "Центр", "color": "#f00", "polygon": SQUARE}]


class _Ord:
    def __init__(self, lat, lon, on_way, delivered):
        self.address_lat = lat
        self.address_lon = lon
        self.on_way_at = on_way
        self.delivered_at = delivered


def test_courier_load_assigns_zone_and_avg():
    on_way = datetime(2026, 6, 27, 7, 0)       # локальный час 12 при offset=5
    delivered = datetime(2026, 6, 27, 7, 30)   # доставка 1800с
    orders = [_Ord(51.15, 71.45, on_way, delivered)]
    out = courier_zone_load(orders, ZONES, tz_offset_hours=5)
    assert out["cells"]["1"][12] == {"count": 1, "avg_sec": 1800.0}
    assert out["totals_by_zone"]["1"] == {"count": 1, "avg_sec": 1800.0}
    # «Без зоны» присутствует в списке зон, но пустой
    assert out["zones"][-1]["id"] is None
    assert out["totals_by_zone"]["none"]["count"] == 0


def test_courier_load_no_coords_goes_to_none_zone():
    on_way = datetime(2026, 6, 27, 7, 0)
    delivered = datetime(2026, 6, 27, 7, 10)
    orders = [_Ord(None, None, on_way, delivered)]
    out = courier_zone_load(orders, ZONES, tz_offset_hours=5)
    assert out["cells"]["none"][12]["count"] == 1


def test_courier_load_skips_orders_without_on_way():
    orders = [_Ord(51.15, 71.45, None, None)]
    out = courier_zone_load(orders, ZONES, tz_offset_hours=5)
    assert out["totals_by_zone"]["1"]["count"] == 0
