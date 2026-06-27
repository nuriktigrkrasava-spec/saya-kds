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


# ── Авто-сетка зон ──────────────────────────────────────────────────────────
from app.geo import points_bbox, build_grid_zones, ASTANA_BBOX


def test_points_bbox_fallback_when_too_few():
    assert points_bbox([]) == ASTANA_BBOX
    assert points_bbox([(51.1, 71.4)]) == ASTANA_BBOX  # 1 точка — вырождено


def test_points_bbox_pads_extent():
    bbox = points_bbox([(51.1, 71.4), (51.2, 71.5)], pad_ratio=0.0)
    assert bbox == (51.1, 51.2, 71.4, 71.5)


def test_build_grid_names_and_count():
    cells = build_grid_zones((51.0, 51.3, 71.2, 71.7), 3)
    assert len(cells) == 9
    assert cells[0]["name"] == "A1"     # северо-запад
    assert cells[-1]["name"] == "C3"    # юго-восток
    assert len(cells[0]["polygon"]) == 4


def test_build_grid_cells_assign_points():
    bbox = (51.0, 51.3, 71.2, 71.7)
    cells = build_grid_zones(bbox, 3)
    zones = [{"id": i, "polygon": c["polygon"]} for i, c in enumerate(cells)]
    # центр карты попадает в среднюю клетку B2 (индекс 4)
    assert assign_zone(51.15, 71.45, zones) == 4


def test_build_grid_clamps_n():
    assert len(build_grid_zones(ASTANA_BBOX, 0)) == 1      # n>=1
    assert len(build_grid_zones(ASTANA_BBOX, 99)) == 144   # n<=12
