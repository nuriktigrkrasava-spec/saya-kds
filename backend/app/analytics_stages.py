from __future__ import annotations
from app.time_utils import utc_now
import statistics
from datetime import datetime, timedelta

from app.geo import assign_zone

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


# iiko проставляет whenSended и whenDelivered ОДНИМ моментом при закрытии заказа,
# если курьер не отмечал статусы (видно на проде: оба поля совпадают до мс).
# «Доставка» быстрее минуты — этот артефакт, а не измерение: в средние не берём.
MIN_REAL_DELIVERY_SEC = 60


def delivery_seconds(order) -> float | None:
    """Измеренное время доставки on_way_at -> delivered_at; None, если отметок
    нет или длительность — артефакт закрытия (< MIN_REAL_DELIVERY_SEC)."""
    s = _dur(order.on_way_at, order.delivered_at)
    if s is None or s < MIN_REAL_DELIVERY_SEC:
        return None
    return s


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
        "delivery": delivery_seconds(order),
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


def sla_breaches(orders, threshold_sec: float, tz_offset_hours: int = 0,
                 now: datetime | None = None) -> dict:
    """SLA created_at -> delivered_at. Зависший заказ (не доставлен, не закрыт в
    iiko, возраст уже больше порога) — тоже просрочка: иначе самые проблемные
    заказы вообще не попадали бы в процент."""
    now = now or utc_now()
    total = breached = 0
    by_day: dict[str, dict] = {}
    by_hour: dict[int, dict] = {}
    for o in orders:
        full = _dur(o.created_at, o.delivered_at)
        if full is None:
            stuck = (o.delivered_at is None and o.status != "Closed"
                     and o.created_at is not None
                     and (now - o.created_at).total_seconds() > threshold_sec)
            if not stuck:
                continue
            full = (now - o.created_at).total_seconds()
        total += 1
        is_breach = full > threshold_sec
        local = o.created_at + timedelta(hours=tz_offset_hours)
        day = local.strftime("%Y-%m-%d")
        hour = local.hour
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


def kitchen_zones(orders) -> dict:
    by_zone: dict[str, list[float]] = {}
    by_zone_emp: dict[str, dict[str, list[float]]] = {}
    for o in orders:
        for it in o.items:
            s = _dur(it.station_accepted_at, it.station_ready_at)
            if s is None:
                continue
            z = it.station or "other"
            by_zone.setdefault(z, []).append(s)
            emp = it.checked_by or "—"
            by_zone_emp.setdefault(z, {}).setdefault(emp, []).append(s)
    zones = []
    for z, vals in sorted(by_zone.items()):
        emps = [{"name": n, "count": len(v), "avg": sum(v) / len(v)}
                for n, v in sorted(by_zone_emp.get(z, {}).items())]
        emps.sort(key=lambda e: e["avg"], reverse=True)
        zones.append({"zone": z, "count": len(vals),
                      "avg": sum(vals) / len(vals),
                      "median": statistics.median(vals), "employees": emps})
    return {"zones": zones}


def anomalies(orders, revert_counts: dict) -> list[dict]:
    out = []
    for o in orders:
        for it in o.items:
            s = _dur(it.station_accepted_at, it.station_ready_at)
            if s is not None and s < 60:
                out.append({"order_id": o.id, "number": o.number, "kind": "fast",
                            "detail": f"{it.name}: {int(s)}с", "actor": it.checked_by or ""})
        rc = revert_counts.get(o.id, 0)
        if rc >= 2:
            out.append({"order_id": o.id, "number": o.number, "kind": "reverts",
                        "detail": f"возвратов: {rc}", "actor": ""})
    return out


def synth_events(order) -> list[dict]:
    ev = []
    for it in order.items:
        if it.station_accepted_at:
            ev.append({"at": it.station_accepted_at, "type": "item_accept",
                       "item_id": it.id, "item_name": it.name, "actor_name": it.checked_by or ""})
        if it.station_ready_at:
            ev.append({"at": it.station_ready_at, "type": "item_ready",
                       "item_id": it.id, "item_name": it.name, "actor_name": it.checked_by or ""})
        if it.checked_at:
            ev.append({"at": it.checked_at, "type": "item_check",
                       "item_id": it.id, "item_name": it.name, "actor_name": it.checked_by or ""})
    if order.assembled_at:
        ev.append({"at": order.assembled_at, "type": "order_assemble",
                   "item_id": None, "item_name": None, "actor_name": ""})
    if order.on_way_at:
        ev.append({"at": order.on_way_at, "type": "take",
                   "item_id": None, "item_name": None, "actor_name": order.assigned_courier_name or ""})
    if order.delivered_at:
        ev.append({"at": order.delivered_at, "type": "delivered",
                   "item_id": None, "item_name": None, "actor_name": order.assigned_courier_name or ""})
    return sorted(ev, key=lambda e: e["at"])


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


# Шкала «Путь заказа»: только то, что видит клиент от готовки до двери.
# Очередь на кухню и закрытие в iiko сюда не входят — это внутренняя кухня и
# бухгалтерия, а не время ожидания заказа.
PATH_STAGES = [
    ("cooking", "Готовка"),
    ("assembly", "Сборка"),
    ("pickup_wait", "Ожидание"),
    ("delivery", "Доставка"),
]


def order_path(orders) -> dict:
    """Средняя длительность этапов пути заказа + сумма («N минут до клиента»).

    Каждый этап усредняется по своей выборке: заказ без сборки не занижает
    среднее по сборке, он в него просто не входит. Поэтому total — это сумма
    средних, а не среднее полного времени; для шкалы это и нужно.
    """
    buckets: dict[str, list[float]] = {k: [] for k, _ in PATH_STAGES}
    for o in orders:
        for s in order_stages(o):
            if s["key"] in buckets and s["seconds"] is not None:
                buckets[s["key"]].append(s["seconds"])

    stages = []
    total_min = 0
    for key, label in PATH_STAGES:
        vals = buckets[key]
        avg = (sum(vals) / len(vals)) if vals else None
        minutes = round(avg / 60) if avg is not None else None
        if minutes is not None:
            total_min += minutes
        stages.append({"key": key, "label": label, "count": len(vals),
                       "seconds": avg, "minutes": minutes})
    return {"stages": stages, "total_minutes": total_min,
            "unit_label": "мин до клиента"}


# Тип заказа iiko → как мы его называем. «Обычный заказ» с кассы и заказ со
# стола — оба зал: и там, и там кухня работает одинаково.
_KIND_BY_SERVICE_TYPE = {
    "DineIn": "hall", "Common": "hall",
    "ToGo": "togo",                      # вынос с кассы: гость стоит и ждёт
    "DeliveryByClient": "pickup",        # самовывоз с сайта и агрегаторы
    "DeliveryByCourier": "delivery",
}
_KIND_LABEL = {"hall": "Зал", "togo": "Вынос", "pickup": "Самовывоз",
               "delivery": "Доставка"}


def kitchen_time_by_kind(orders) -> dict:
    """Время готовки по типам заказа: от появления до готовности ПОСЛЕДНЕЙ позиции.

    Для зала это единственная измеримая метрика: в iiko у зальных заказов нет
    ни «готово», ни «подали», есть только открытие и закрытие чека. Наши
    отметки на планшете — единственный источник.

    Считаем только КУХОННЫЕ позиции: напитки и доп. порции соусов попадают в
    станцию «other», на кухне их не готовят и готовыми не отмечают. Пока
    метрика требовала отметки у всех позиций подряд, любой заказ с колой
    выпадал — на проде из 111 доставок в счёт шли 28.

    Заказ, где готовы не все кухонные позиции, в счёт не идёт: его время ещё
    неизвестно. Заказ вообще без кухонных позиций — тоже: кухню он не занимал.
    """
    buckets: dict[str, list[float]] = {k: [] for k in _KIND_LABEL}
    for order in orders:
        kind = _KIND_BY_SERVICE_TYPE.get((order.service_type or "").strip())
        if kind is None:
            continue
        cooked = [i for i in order.items if (i.station or "other") != "other"]
        if not cooked or any(i.station_ready_at is None for i in cooked):
            continue
        ready = max(i.station_ready_at for i in cooked)
        seconds = _dur(order.created_at, ready)
        if seconds is not None:
            buckets[kind].append(seconds)

    out = {}
    for kind, label in _KIND_LABEL.items():
        vals = sorted(buckets[kind])
        out[kind] = {
            "label": label,
            "count": len(vals),
            "avg_seconds": (sum(vals) / len(vals)) if vals else None,
            "median_seconds": statistics.median(vals) if vals else None,
            "p95_seconds": pctl(vals, 95),
        }
    return out


def zone_load(orders, tz_offset_hours: int = 0,
              zones: tuple[str, ...] = ("pizza", "rolls", "hot", "other")) -> dict:
    """Нагрузка кухни по часам: для каждой зоны и локального часа — число готовых
    блюд (count) и среднее время готовки (avg_sec). Час берётся по station_ready_at
    (момент завершения готовки), переведённому в локальное время (UTC+offset).
    Зона блюда — kitchen_zone (fallback на station); незнакомая зона попадает в
    other, а не выбрасывается молча. Пропускаем только блюда без ready.
    """
    cells: dict[str, dict[int, dict]] = {z: {} for z in zones}
    cells_by_dow = {z: {} for z in zones}
    totals: dict[str, dict] = {z: {"count": 0, "sum": 0.0, "timed": 0} for z in zones}
    for o in orders:
        for it in o.items:
            z = it.kitchen_zone or it.station or "other"
            if z not in cells:
                z = it.station if it.station in cells else "other"
                if z not in cells:  # вызвали с кастомным списком зон без other
                    continue
            ready = it.station_ready_at
            if ready is None:
                continue
            local = ready + timedelta(hours=tz_offset_hours)
            hour = local.hour
            dow_cell = cells_by_dow[z].setdefault(str(local.weekday()), {}).setdefault(str(hour), {"count":0,"sum":0.0,"timed":0})
            dow_cell["count"] += 1
            dur = _dur(it.station_accepted_at, it.station_ready_at)
            cell = cells[z].setdefault(hour, {"count": 0, "sum": 0.0, "timed": 0})
            cell["count"] += 1
            totals[z]["count"] += 1
            if dur is not None:
                dow_cell["sum"] += dur; dow_cell["timed"] += 1
                cell["sum"] += dur; cell["timed"] += 1
                totals[z]["sum"] += dur; totals[z]["timed"] += 1

    def _fin(d: dict) -> dict:
        return {"count": d["count"],
                "avg_sec": (d["sum"] / d["timed"]) if d["timed"] else None}

    return {
        "cells_by_dow": {z:{d:{h:_fin(c) for h,c in hours.items()} for d,hours in days.items()} for z,days in cells_by_dow.items()},
        "zones": list(zones),
        "hours": list(range(24)),
        "cells": {z: {h: _fin(c) for h, c in hrs.items()} for z, hrs in cells.items()},
        "totals_by_zone": {z: _fin(t) for z, t in totals.items()},
    }


def courier_zone_load(orders, zones: list[dict], tz_offset_hours: int = 0) -> dict:
    """Нагрузка курьеров по районам города и часам. Заказ привязывается к зоне по
    координатам доставки (address_lat/lon). Час — по on_way_at (момент выезда) в
    локальном времени. avg_sec — среднее время доставки (on_way_at -> delivered_at).
    Заказы без on_way_at пропускаем. Без координат/совпадения — псевдо-зона «Без зоны».
    """
    keys = [str(z["id"]) for z in zones] + ["none"]
    cells: dict[str, dict[int, dict]] = {k: {} for k in keys}
    totals: dict[str, dict] = {k: {"count": 0, "sum": 0.0, "timed": 0} for k in keys}
    for o in orders:
        if o.on_way_at is None:
            continue
        zid = assign_zone(o.address_lat, o.address_lon, zones)
        key = str(zid) if zid is not None else "none"
        hour = (o.on_way_at + timedelta(hours=tz_offset_hours)).hour
        dur = delivery_seconds(o)
        cell = cells[key].setdefault(hour, {"count": 0, "sum": 0.0, "timed": 0})
        cell["count"] += 1
        totals[key]["count"] += 1
        if dur is not None:
            cell["sum"] += dur; cell["timed"] += 1
            totals[key]["sum"] += dur; totals[key]["timed"] += 1

    def _fin(d: dict) -> dict:
        return {"count": d["count"],
                "avg_sec": (d["sum"] / d["timed"]) if d["timed"] else None}

    zones_out = [{"id": z["id"], "name": z["name"], "color": z.get("color", "#2563eb")}
                 for z in zones]
    zones_out.append({"id": None, "name": "Без зоны", "color": "#94a3b8"})
    return {
        "zones": zones_out,
        "hours": list(range(24)),
        "cells": {k: {h: _fin(c) for h, c in hrs.items()} for k, hrs in cells.items()},
        "totals_by_zone": {k: _fin(t) for k, t in totals.items()},
    }
