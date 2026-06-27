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


def assign_zone(lat, lon, zones: list[dict]) -> int | None:
    """Id первой зоны, чей полигон содержит точку. None если нет координат/совпадений."""
    if lat is None or lon is None:
        return None
    for z in zones:
        poly = z.get("polygon") or []
        if point_in_polygon(lat, lon, poly):
            return z.get("id")
    return None
