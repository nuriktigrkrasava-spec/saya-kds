"""Разрез zone_load по дню недели: зона × день × час.

Час-пик «в среднем за период» скрывает то, ради чего смотрят на график: в
субботу пик другой, чем в среду. Считаем те же ячейки, но с третьим ключом.
"""
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


def _it(acc, ready, zone="hot"):
    return _It(zone, "pizza", acc, ready)


def test_cells_by_dow_splits_same_hour_across_weekdays():
    # 2026-06-27 — суббота (dow=5), 2026-06-29 — понедельник (dow=0).
    # Оба ready в 07:00 UTC -> локальный час 12 при offset=5.
    sat = _Ord([_it(datetime(2026, 6, 27, 6, 50), datetime(2026, 6, 27, 7, 0))])
    mon1 = _Ord([_it(datetime(2026, 6, 29, 6, 50), datetime(2026, 6, 29, 7, 0))])
    mon2 = _Ord([_it(datetime(2026, 6, 29, 6, 55), datetime(2026, 6, 29, 7, 0))])
    out = zone_load([sat, mon1, mon2], tz_offset_hours=5)

    by_dow = out["cells_by_dow"]["hot"]
    assert by_dow["5"]["12"] == {"count": 1, "avg_sec": 600.0}
    assert by_dow["0"]["12"] == {"count": 2, "avg_sec": 450.0}  # 600 и 300


def test_cells_by_dow_uses_local_day_not_utc_day():
    # ready 2026-06-27 21:00 UTC + 5ч = 2026-06-28 02:00 локально.
    # Локально это уже воскресенье (dow=6), а не суббота (dow=5).
    o = _Ord([_it(datetime(2026, 6, 27, 20, 50), datetime(2026, 6, 27, 21, 0))])
    out = zone_load([o], tz_offset_hours=5)

    by_dow = out["cells_by_dow"]["hot"]
    assert by_dow["6"]["2"]["count"] == 1
    assert "5" not in by_dow


def test_cells_by_dow_agrees_with_flat_cells():
    """Сумма по дням = плоская ячейка: два разреза не должны расходиться."""
    orders = [
        _Ord([_it(datetime(2026, 6, 27, 6, 50), datetime(2026, 6, 27, 7, 0))]),
        _Ord([_it(datetime(2026, 6, 29, 6, 50), datetime(2026, 6, 29, 7, 0))]),
        _Ord([_it(datetime(2026, 6, 30, 6, 50), datetime(2026, 6, 30, 7, 0))]),
    ]
    out = zone_load(orders, tz_offset_hours=5)

    flat = out["cells"]["hot"][12]["count"]
    by_dow = sum(d.get("12", {}).get("count", 0)
                 for d in out["cells_by_dow"]["hot"].values())
    assert flat == by_dow == 3


def test_cells_by_dow_present_for_every_zone_even_when_empty():
    out = zone_load([], tz_offset_hours=5)
    assert set(out["cells_by_dow"]) == {"pizza", "rolls", "hot", "other"}
    assert out["cells_by_dow"]["rolls"] == {}


def test_flat_cells_shape_unchanged():
    """Прод-index.html читает cells[zone][hour] — формат не трогаем."""
    o = _Ord([_it(datetime(2026, 6, 27, 6, 50), datetime(2026, 6, 27, 7, 0))])
    out = zone_load([o], tz_offset_hours=5)
    assert out["cells"]["hot"][12] == {"count": 1, "avg_sec": 600.0}
    assert out["zones"] == ["pizza", "rolls", "hot", "other"]
    assert out["hours"] == list(range(24))
