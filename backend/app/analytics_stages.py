from __future__ import annotations
import statistics
from datetime import datetime

STAGES = [
    ("queue", "Очередь на кухню"),
    ("cooking", "Готовка"),
    ("assembly", "Сборка"),
    ("pickup_wait", "Ожидание курьера"),
    ("delivery", "Доставка"),
    ("iiko_close", "Закрытие в iiko"),
]


def _dur(a: datetime | None, b: datetime | None) -> float | None:
    """Длительность b-a в секундах; None если любой конец пуст; отрицательную клампим в 0."""
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


def _accepted_min(order) -> datetime | None:
    vals = [i.station_accepted_at for i in order.items if i.station_accepted_at]
    return min(vals) if vals else None


def _ready_max(order) -> datetime | None:
    vals = [i.station_ready_at for i in order.items if i.station_ready_at]
    return max(vals) if vals else None


def order_stages(order, close_at: datetime | None = None) -> list[dict]:
    acc, ready = _accepted_min(order), _ready_max(order)
    seconds = {
        "queue": _dur(order.created_at, acc),
        "cooking": _dur(acc, ready),
        "assembly": _dur(ready, order.assembled_at),
        "pickup_wait": _dur(order.assembled_at, order.on_way_at),
        "delivery": _dur(order.on_way_at, order.delivered_at),
        "iiko_close": _dur(order.delivered_at, close_at),
    }
    # бутылочное горло — первый максимум среди не-None
    best_key = None
    best_val = -1.0
    for key, _label in STAGES:
        s = seconds[key]
        if s is not None and s > best_val:
            best_val, best_key = s, key
    return [{"key": k, "label": lbl, "seconds": seconds[k],
             "bottleneck": (k == best_key)} for k, lbl in STAGES]


def pctl(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    rank = (p / 100) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def stage_aggregates(orders, close_at_by_order: dict | None = None) -> dict:
    close_at_by_order = close_at_by_order or {}
    buckets: dict[str, list[float]] = {k: [] for k, _ in STAGES}
    bottleneck: dict[str, int] = {k: 0 for k, _ in STAGES}
    for o in orders:
        stages = order_stages(o, close_at_by_order.get(o.id))
        for s in stages:
            if s["seconds"] is not None:
                buckets[s["key"]].append(s["seconds"])
            if s["bottleneck"]:
                bottleneck[s["key"]] += 1
    out_stages = []
    for key, label in STAGES:
        vals = buckets[key]
        out_stages.append({
            "key": key, "label": label, "count": len(vals),
            "avg": (sum(vals) / len(vals)) if vals else None,
            "median": statistics.median(vals) if vals else None,
            "p95": pctl(vals, 95),
        })
    return {"stages": out_stages, "bottleneck_distribution": bottleneck}
