from __future__ import annotations


def point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Ray-casting. polygon — замкнутый/незамкнутый список вершин [lat, lon].
    Точка на ребре считается внутри непостоянно — для аналитики это приемлемо."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]   # lat, lon
        yj, xj = polygon[j][0], polygon[j][1]
        intersect = ((yi > lat) != (yj > lat)) and \
            (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


# Дефолтный bbox Астаны — на случай, когда заказов с координатами < 2 и
# границы посчитать не из чего. [lat_min, lat_max, lon_min, lon_max].
ASTANA_BBOX = (51.00, 51.30, 71.20, 71.70)


def points_bbox(points: list[tuple[float, float]], pad_ratio: float = 0.05):
    """bbox (lat_min, lat_max, lon_min, lon_max) по точкам с паддингом.
    Если точек < 2 или они вырождены — отдаём дефолт Астаны."""
    lats = [p[0] for p in points if p[0] is not None]
    lons = [p[1] for p in points if p[1] is not None]
    if len(lats) < 2 or len(lons) < 2:
        return ASTANA_BBOX
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    if lat_max - lat_min < 1e-6 or lon_max - lon_min < 1e-6:
        return ASTANA_BBOX
    dlat = (lat_max - lat_min) * pad_ratio
    dlon = (lon_max - lon_min) * pad_ratio
    return (lat_min - dlat, lat_max + dlat, lon_min - dlon, lon_max + dlon)


def build_grid_zones(bbox, n: int) -> list[dict]:
    """Режет bbox на n×n клеток. Возвращает список зон сверху-вниз, слева-направо:
    {name 'A1', polygon [[lat,lon]×4], bounds (lat_lo,lat_hi,lon_lo,lon_hi)}.
    Строка кодируется буквой (север сверху = 'A'), столбец — числом с 1."""
    n = max(1, min(int(n), 12))  # 1..12 — разумные границы
    lat_min, lat_max, lon_min, lon_max = bbox
    lat_step = (lat_max - lat_min) / n
    lon_step = (lon_max - lon_min) / n
    cells = []
    for i in range(n):           # i=0 — самая северная строка
        lat_hi = lat_max - i * lat_step
        lat_lo = lat_max - (i + 1) * lat_step
        for j in range(n):
            lon_lo = lon_min + j * lon_step
            lon_hi = lon_min + (j + 1) * lon_step
            cells.append({
                "name": f"{chr(ord('A') + i)}{j + 1}",
                "polygon": [[lat_lo, lon_lo], [lat_lo, lon_hi],
                            [lat_hi, lon_hi], [lat_hi, lon_lo]],
                "bounds": (lat_lo, lat_hi, lon_lo, lon_hi),
            })
    return cells


def assign_zone(lat, lon, zones: list[dict]) -> int | None:
    """Id первой зоны, чей полигон содержит точку. None если нет координат/совпадений."""
    if lat is None or lon is None:
        return None
    for z in zones:
        poly = z.get("polygon") or []
        if point_in_polygon(lat, lon, poly):
            return z.get("id")
    return None
