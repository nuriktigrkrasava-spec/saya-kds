"""Почему доставка опоздала: кухня, ожидание курьера или дорога.

Факт опоздания даёт iiko (колонка «Опоздание доставки»), но не говорит, кто
виноват. Время по этапам есть только у нас, поэтому склеиваем опоздавшие
заказы со своими по iiko_order_id и смотрим, какой участок сильнее вышел за
свою обычную длительность.

Виноват не самый ДОЛГИЙ этап, а самый превысивший норму: дорога почти всегда
длиннее готовки, но это не делает курьера виноватым.
"""
from __future__ import annotations
import statistics

from app import analytics_stages as _stages

# Три участка, за которые отвечают разные люди.
LEGS = {
    "kitchen": (("queue", "cooking"), "Долгая готовка"),
    "pickup": (("assembly", "pickup_wait"), "Ждали курьера"),
    "delivery": (("delivery",), "Долго везли"),
}


def _leg_seconds(order) -> dict[str, float]:
    """Длительности участков заказа; участок без данных не попадает в ответ."""
    stages = {s["key"]: s["seconds"] for s in _stages.order_stages(order)}
    out: dict[str, float] = {}
    for leg, (keys, _label) in LEGS.items():
        parts = [stages.get(k) for k in keys]
        if any(p is None for p in parts):
            continue
        out[leg] = sum(parts)
    return out


def stage_norms(orders) -> dict[str, float]:
    """Обычная длительность каждого участка — медиана по заказам периода."""
    buckets: dict[str, list[float]] = {}
    for order in orders:
        for leg, seconds in _leg_seconds(order).items():
            buckets.setdefault(leg, []).append(seconds)
    return {leg: statistics.median(vals) for leg, vals in buckets.items() if vals}


def reason_for(order, norms: dict[str, float]) -> dict | None:
    """Участок, сильнее всех вышедший за норму, или None.

    None означает «винить некого»: либо наших отметок по заказу нет, либо все
    участки уложились в обычное время и опоздание набежало по мелочи.
    """
    legs = _leg_seconds(order)
    worst_leg, worst_over = None, 0.0
    for leg, seconds in legs.items():
        norm = norms.get(leg)
        if norm is None:
            continue
        over = seconds - norm
        if over > worst_over:
            worst_leg, worst_over = leg, over
    if worst_leg is None:
        return None
    return {"key": worst_leg, "label": LEGS[worst_leg][1],
            "over_seconds": int(worst_over), "seconds": int(legs[worst_leg])}


def attach_reasons(late_orders: list[dict], our_orders) -> dict[str, int]:
    """Дописывает причину каждому опоздавшему и отдаёт сводку «кто сколько раз».

    Заказы, которых нет в нашей базе (старее, чем мы собираем), считаем
    «unknown» — молча прятать их нельзя, иначе сводка соврёт.
    """
    norms = stage_norms(our_orders)
    by_id = {o.iiko_order_id: o for o in our_orders}

    summary: dict[str, int] = {}
    for row in late_orders:
        order = by_id.get(row.get("order_id"))
        reason = reason_for(order, norms) if order is not None else None
        row["reason"] = reason
        key = reason["key"] if reason else "unknown"
        summary[key] = summary.get(key, 0) + 1
    return summary
