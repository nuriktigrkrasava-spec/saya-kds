from datetime import datetime
from app.analytics_stages import zone_load


class _It:
    def __init__(self, kitchen_zone, station, acc, ready):
        self.kitchen_zone = kitchen_zone
        self.station = station
        self.station_accepted_at = acc
        self.station_ready_at = ready


class _Ord:
    def __init__(self, items):
        self.items = items


def test_zone_load_buckets_by_local_hour_with_avg():
    # ready в 07:00 UTC -> локальный час 12 при offset=5.
    acc = datetime(2026, 6, 27, 6, 50)
    ready = datetime(2026, 6, 27, 7, 0)   # готовка 600с
    orders = [_Ord([_It("hot", "pizza", acc, ready)])]
    out = zone_load(orders, tz_offset_hours=5)

    assert out["zones"] == ["pizza", "rolls", "hot", "other"]
    assert out["hours"] == list(range(24))
    assert out["cells"]["hot"][12] == {"count": 1, "avg_sec": 600.0}
    assert out["totals_by_zone"]["hot"] == {"count": 1, "avg_sec": 600.0}
    # пустая зона — пустые ячейки, total нулевой
    assert out["cells"]["rolls"] == {}
    assert out["totals_by_zone"]["rolls"] == {"count": 0, "avg_sec": None}


def test_zone_load_falls_back_to_station_when_kitchen_zone_null():
    acc = datetime(2026, 6, 27, 6, 50)
    ready = datetime(2026, 6, 27, 7, 0)
    orders = [_Ord([_It(None, "pizza", acc, ready)])]
    out = zone_load(orders, tz_offset_hours=5)
    assert out["cells"]["pizza"][12]["count"] == 1


def test_zone_load_skips_unready_but_counts_other():
    acc = datetime(2026, 6, 27, 6, 50)
    orders = [
        _Ord([_It("hot", "pizza", acc, None)]),        # не готово — пропуск
        _Ord([_It("other", "other", acc, acc)]),       # other теперь считается
    ]
    out = zone_load(orders, tz_offset_hours=5)
    assert out["totals_by_zone"]["hot"]["count"] == 0
    assert out["totals_by_zone"]["other"]["count"] == 1
