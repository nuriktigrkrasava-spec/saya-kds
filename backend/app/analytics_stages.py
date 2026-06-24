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


def sla_breaches(orders, threshold_sec: float) -> dict:
    total = breached = 0
    by_day: dict[str, dict] = {}
    by_hour: dict[int, dict] = {}
    for o in orders:
        full = _dur(o.created_at, o.delivered_at)
        if full is None:
            continue
        total += 1
        is_breach = full > threshold_sec
        day = o.created_at.strftime("%Y-%m-%d")
        hour = o.created_at.hour
        d = by_day.setdefault(day, {"date": day, "total": 0, "breached": 0})
        h = by_hour.setdefault(hour, {"hour": hour, "total": 0, "breached": 0})
        d["total"] += 1; h["total"] += 1
        if is_breach:
            breached += 1; d["breached"] += 1; h["breached"] += 1
    return {
        "total": total, "breached": breached,
        "rate": (breached / total) if total else 0.0,
        "by_day": sorted(by_day.values(), key=lambda x: x["date"]),
        "by_hour": sorted(by_hour.values(), key=lambda x: x["hour"]),
    }


def pickup_lag(orders) -> dict:
    rows = []
    for o in orders:
        s = _dur(o.assembled_at, o.on_way_at)
        if s is not None:
            rows.append({"number": o.number, "seconds": s})
    vals = [r["seconds"] for r in rows]
    return {
        "avg": (sum(vals) / len(vals)) if vals else None,
        "median": statistics.median(vals) if vals else None,
        "p95": pctl(vals, 95),
        "worst": sorted(rows, key=lambda r: r["seconds"], reverse=True)[:10],
    }


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
