"""Подсказка сушисту: чего заготовить перед наплывом.

Считаем по продажам из iiko за месяц. Состав сетов попадает туда
модификаторами («-Филадельфия жареная 4 шт»), поэтому роллы внутри сетов
считаются наравне с обычными позициями — техкарты для этого не нужны.

Подсказка появляется только перед часом, который для этого дня недели
систематически выше обычного. Дёргать повара в спокойный час — верный способ
добиться, чтобы плашку перестали читать.
"""
from __future__ import annotations
import statistics
from datetime import datetime

from app.staffing import PEAK_RATIO, PEAK_TOP_N, _ZONE_BY_STATION

# Сколько блюд показываем: подсказка должна читаться одним взглядом.
TOP_DISHES = 4

# Приправы и упаковка идут к каждому роллу и в подсказке бесполезны: их
# количество просто повторяет число роллов. На проде они занимали три строки из
# четырёх. Классификатор относит их к роллам — в названии стоит «для роллов».
CONDIMENT_STARTS = ("васаби", "имбирь", "соевый соус", "палочк", "коробка",
                    "упаковк", "пакет")


def is_condiment(dish: str) -> bool:
    """Приправа или упаковка, а не блюдо. Смотрим на начало названия: «Ролл с
    соусом спайси» — это блюдо, а «Соевый соус 30 мл» — порция к нему."""
    low = dish.strip().lower()
    return any(low.startswith(p) for p in CONDIMENT_STARTS)


def clean_name(name: str) -> str:
    """Ведущий дефис — пометка вложенной позиции в iiko, в подсказке он лишний."""
    return (name or "").strip().lstrip("-").strip()


def dishes_by_hour(sales_rows: list[dict], zone: str = "japan") -> list[dict]:
    """Сколько какого блюда зоны уходит в каждый час: [{date, hour, dish, amount}].

    Напитки, соусы и доп. услуги отбрасываем: их не заготавливают.
    """
    from app.stations import classify_product_raw

    totals: dict[tuple[str, int, str], int] = {}
    for row in sales_rows:
        dish = clean_name(row.get("DishName"))
        if not dish or is_condiment(dish):
            continue
        station = classify_product_raw(dish, row.get("DishGroup"),
                                       row.get("DishGroup.TopParent"))
        if _ZONE_BY_STATION.get(station) != zone:
            continue
        try:
            hour = int(row.get("HourOpen"))
        except (TypeError, ValueError):
            continue
        key = (row.get("OpenDate.Typed"), hour, dish)
        totals[key] = totals.get(key, 0) + int(row.get("DishAmountInt") or 0)

    return [{"date": d, "hour": h, "dish": dish, "amount": n}
            for (d, h, dish), n in sorted(totals.items())]


def hint_for(rows: list[dict], weekday: int, hour: int) -> dict | None:
    """Что заготовить к этому часу, или None — если час обычный.

    По каждому блюду берём медиану по одинаковым дням недели: один выброс не
    должен заставлять кухню крутить лишнее.
    """
    per_dish: dict[str, list[float]] = {}
    per_hour: dict[int, list[float]] = {}
    for r in rows:
        if datetime.fromisoformat(r["date"]).weekday() != weekday:
            continue
        per_hour.setdefault(r["hour"], []).append(r["amount"])
        if r["hour"] == hour:
            per_dish.setdefault(r["dish"], []).append(r["amount"])

    if not per_dish or not per_hour:
        return None

    # Всплеск — тот же критерий, что в отчёте по сменам: заметно выше среднего
    # часа этого дня и в тройке самых загруженных.
    by_hour_total = {h: sum(v) for h, v in per_hour.items()}
    avg = sum(by_hour_total.values()) / len(by_hour_total)
    top = sorted(by_hour_total, key=lambda h: -by_hour_total[h])[:PEAK_TOP_N]
    if by_hour_total.get(hour, 0) < avg * PEAK_RATIO or hour not in top:
        return None

    dishes = [{"dish": d, "amount": int(round(statistics.median(v)))}
              for d, v in per_dish.items()]
    dishes.sort(key=lambda x: -x["amount"])
    return {"hour": hour, "dishes": dishes[:TOP_DISHES],
            "total": sum(d["amount"] for d in dishes)}
