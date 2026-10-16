"""Люди на кухне против объёма заказов: кто стоял, где всплеск, сколько ставить.

Смены берём из явок iiko, а НЕ из входов на планшет: планшета на кухне два
(«Япония» и «Пицца и горячее»), а людей в зоне может быть больше — по планшетам
численность не посчитать.

Пицца и горячее — одна зона, потому что это один планшет и одна бригада.
"""
from __future__ import annotations
from app.time_utils import utc_now
import math
import statistics
from datetime import datetime, timedelta

from app.config import settings

# Должности RMS → зона кухни. Коды сети Saya Sushi.
ZONE_BY_ROLE = {
    "СУШ": "japan", "СЭКСПО12": "japan", "СЭКСПО13": "japan",
    "ПИЦЦ": "pizza_hot", "ПЭКСПО13": "pizza_hot",
    "CO1": "pizza_hot", "СуШеф": "pizza_hot",
}

ZONE_LABEL = {"japan": "Япония", "pizza_hot": "Пицца и горячее"}

# Явки в нашем iiko не закрывают годами (видели хвосты 2024-2025). Открытая
# явка старше этого — забытый хвост, а не смена.
STALE_SHIFT_DAYS = 2

# Час считается всплеском, если он и заметно выше среднего часа своего дня,
# и входит в тройку самых загруженных часов этого дня.
PEAK_RATIO = 1.3
PEAK_TOP_N = 3

# Норма выработки: сколько позиций в час делает один человек, когда работает.
# Часы с меньшей нагрузкой — простой, по ним норму считать нельзя.
MIN_DEMAND_FOR_NORM = 5
NORM_PERCENTILE = 0.75


def zone_of_role(role: str | None) -> str | None:
    return ZONE_BY_ROLE.get((role or "").strip())


def _parse_local(value: str) -> datetime | None:
    """Время явки iiko («2026-08-05T11:25:00+05:00») как локальное, без tz.

    Смещение в строке — уже местное время филиала, поэтому просто отбрасываем
    его: считать часы смены проще в наивном локальном времени.
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def people_by_hour(attendances: list[dict], role_by_employee: dict[str, str],
                   now_local: str | None = None) -> list[dict]:
    """Сколько человек каждой зоны стояло в каждый час: [{date, hour, zone, people}].

    Час засчитывается, если смена его начала: с 11:25 до 14:00 — это часы 11,
    12 и 13. Смена через полночь раскладывается на две даты.
    """
    # Время явки — местное время филиала, поэтому и «сейчас» берём местное.
    # С utcnow() открытая смена получала конец РАНЬШЕ начала (разница в 5 часов)
    # и отбрасывалась целиком: зона показывала ноль людей, а отчёт требовал
    # поставить ещё троих.
    now = (_parse_local(now_local) if now_local
           else utc_now() + timedelta(hours=settings.iiko_tz_offset_hours))
    # Копим id сотрудников, а не счётчик: перерыв даёт две явки на одного
    # человека, и по записям он считался бы за двоих — нехватка рук пряталась.
    seen: dict[tuple[str, int, str], set[str]] = {}

    for att in attendances:
        zone = zone_of_role(role_by_employee.get(att.get("employee_id")))
        if zone is None:
            continue
        start = _parse_local(att.get("date_from"))
        if start is None:
            continue
        end = _parse_local(att.get("date_to"))
        if end is None:
            # Открытая явка: считаем до «сейчас», но забытые хвосты не берём.
            if (now - start) > timedelta(days=STALE_SHIFT_DAYS):
                continue
            end = now
        if end <= start:
            continue

        cursor = start.replace(minute=0, second=0, microsecond=0)
        while cursor < end:
            key = (cursor.strftime("%Y-%m-%d"), cursor.hour, zone)
            seen.setdefault(key, set()).add(att.get("employee_id"))
            cursor += timedelta(hours=1)

    return [{"date": d, "hour": h, "zone": z, "people": len(who)}
            for (d, h, z), who in sorted(seen.items())]


# Станция блюда → зона кухни. Пицца и горячее сидят за одним планшетом.
_ZONE_BY_STATION = {"rolls": "japan", "pizza": "pizza_hot", "hot": "pizza_hot"}


def demand_by_hour(sales_rows: list[dict]) -> list[dict]:
    """Позиции кухни по часам и зонам: [{date, hour, zone, items}].

    Классифицируем тем же кодом, что и заказы на планшетах, — иначе плановая
    нагрузка разойдётся с тем, что кухня реально видит. Напитки, доп. услуги и
    прочее не кухонное отбрасываем: рук они не занимают.
    """
    from app.stations import classify_product_raw

    totals: dict[tuple[str, int, str], int] = {}
    for row in sales_rows:
        station = classify_product_raw(row.get("DishName") or "",
                                       row.get("DishGroup"),
                                       row.get("DishGroup.TopParent"))
        zone = _ZONE_BY_STATION.get(station)
        if zone is None:
            continue
        try:
            hour = int(row.get("HourOpen"))
        except (TypeError, ValueError):
            continue
        key = (row.get("OpenDate.Typed"), hour, zone)
        totals[key] = totals.get(key, 0) + int(row.get("DishAmountInt") or 0)

    return [{"date": d, "hour": h, "zone": z, "items": n}
            for (d, h, z), n in sorted(totals.items())]


def _median_int(values: list[float]) -> int:
    return int(round(statistics.median(values)))


def _norm_per_person(cells: list[dict]) -> int | None:
    """Норма выработки зоны: сколько позиций в час делает один человек.

    Считаем ТОЛЬКО по часам с реальной нагрузкой и берём высокий процентиль —
    это то, что зона показывает, когда работает, а не когда простаивает.

    Медиана по всем часам не годится: ночью повар стоит без заказов, и норма
    съезжает к 1-2 позициям. На проде из-за этого отчёт требовал 10 человек на
    20 позиций в час.
    """
    loaded = sorted(c["per_person"] for c in cells
                    if c["per_person"] is not None
                    and c["demand"] >= MIN_DEMAND_FOR_NORM)
    if not loaded:
        loaded = sorted(c["per_person"] for c in cells if c["per_person"] is not None)
    if not loaded:
        return None
    index = min(len(loaded) - 1, int(len(loaded) * NORM_PERCENTILE))
    return max(1, int(round(loaded[index])))


def build_grid(demand_rows: list[dict], shift_rows: list[dict]) -> dict:
    """Сетка «день недели × час × зона»: спрос, люди, выработка, всплески, план.

    По каждой ячейке берём МЕДИАНУ по одинаковым дням недели: один аномальный
    день (акция, сбой) не должен задирать план на все следующие недели.
    """
    demand: dict[tuple[int, int, str], list[float]] = {}
    for r in demand_rows:
        weekday = datetime.fromisoformat(r["date"]).weekday()
        demand.setdefault((weekday, r["hour"], r["zone"]), []).append(r["items"])

    people: dict[tuple[int, int, str], list[float]] = {}
    for r in shift_rows:
        weekday = datetime.fromisoformat(r["date"]).weekday()
        people.setdefault((weekday, r["hour"], r["zone"]), []).append(r["people"])

    cells = []
    for key in sorted(set(demand) | set(people)):
        weekday, hour, zone = key
        d = _median_int(demand[key]) if key in demand else 0
        p = _median_int(people[key]) if key in people else 0
        cells.append({
            "weekday": weekday, "hour": hour, "zone": zone,
            "demand": d, "people": p,
            "per_person": (d / p) if p else None,
            "is_peak": False, "recommended_people": 1,
        })

    _mark_peaks(cells)

    zones: dict[str, dict] = {}
    for zone in sorted({c["zone"] for c in cells}):
        zone_cells = [c for c in cells if c["zone"] == zone]
        norm = _norm_per_person(zone_cells)
        zones[zone] = {"label": ZONE_LABEL.get(zone, zone), "norm_per_person": norm}
        for c in zone_cells:
            c["recommended_people"] = (
                max(1, math.ceil(c["demand"] / norm)) if norm else 1)

    return {"cells": cells, "zones": zones}


def shift_plan(grid: dict, weekday: int) -> list[dict]:
    """Часы этого дня недели, где по прогнозу не хватит рук.

    Отдаём только нехватку: управляющему нужно действие («поставь третьего»),
    а не сетка целиком. Часы, где людей достаточно, в сообщение не идут.
    """
    plan = []
    for c in grid["cells"]:
        if c["weekday"] != weekday:
            continue
        shortage = c["recommended_people"] - c["people"]
        if shortage <= 0:
            continue
        plan.append({
            "zone": c["zone"],
            "zone_label": ZONE_LABEL.get(c["zone"], c["zone"]),
            "hour": c["hour"],
            "demand": c["demand"],
            "people": c["people"],
            "recommended_people": c["recommended_people"],
            "shortage": shortage,
            "is_peak": c["is_peak"],
        })
    plan.sort(key=lambda p: (-p["shortage"], p["hour"]))
    return plan


def _mark_peaks(cells: list[dict]) -> None:
    """Всплеск — час, который и выше среднего часа своего дня, и в тройке пиков."""
    groups: dict[tuple[int, str], list[dict]] = {}
    for c in cells:
        groups.setdefault((c["weekday"], c["zone"]), []).append(c)

    for group in groups.values():
        demands = [c["demand"] for c in group]
        if not any(demands):
            continue
        avg = sum(demands) / len(demands)
        top = {id(c) for c in sorted(group, key=lambda x: -x["demand"])[:PEAK_TOP_N]}
        for c in group:
            c["is_peak"] = c["demand"] >= avg * PEAK_RATIO and id(c) in top
