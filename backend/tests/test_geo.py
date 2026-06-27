from app.geo import point_in_polygon, assign_zone

# Квадрат вокруг центра Астаны (lat 51.1..51.2, lon 71.4..71.5).
SQUARE = [[51.1, 71.4], [51.1, 71.5], [51.2, 71.5], [51.2, 71.4]]


def test_point_inside():
    assert point_in_polygon(51.15, 71.45, SQUARE) is True


def test_point_outside():
    assert point_in_polygon(51.05, 71.45, SQUARE) is False


def test_assign_zone_first_match():
    zones = [{"id": 7, "polygon": SQUARE}]
    assert assign_zone(51.15, 71.45, zones) == 7
    assert assign_zone(0.0, 0.0, zones) is None


def test_assign_zone_handles_missing_coords():
    zones = [{"id": 7, "polygon": SQUARE}]
    assert assign_zone(None, 71.45, zones) is None
    assert assign_zone(51.15, None, zones) is None
