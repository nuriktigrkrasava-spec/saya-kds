from __future__ import annotations
import re
from typing import Callable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ProductStationMap
from app.schemas import WebhookItem

VALID_STATIONS = {"rolls", "pizza", "hot", "other"}

# Ручная разметка по НАЗВАНИЮ хранится в product_station_map с этим префиксом
# в поле product_id (без изменения схемы БД). Имя стабильно и в меню, и в заказе,
# поэтому такая привязка надёжнее, чем по product_id.
NAME_PREFIX = "name::"


def norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def classify_group(group_name: str) -> str:
    """Группа меню iiko -> зона (rolls|pizza|hot|other) по ключевым словам."""
    n = (group_name or "").lower()
    # порядок важен: ролл/сет/суши -> rolls раньше, чем 'горяч'
    if any(k in n for k in ["ролл", "сет", "суши", "маки", "темпура"]):
        return "rolls"
    if "пицц" in n:
        return "pizza"
    if any(k in n for k in ["суп", "вок", "горяч", "лапш", "паста", "вторые", "wok"]):
        return "hot"
    return "other"


def classify_name(name: str) -> str:
    """Классификация блюда по его НАЗВАНИЮ (надёжнее, чем по группе)."""
    n = (name or "").lower()
    if "пицц" in n:
        return "pizza"
    if any(k in n for k in ["ролл", "сет", "суши", "маки", "темпура", "урамаки", "филадельфия", "калифорни"]):
        return "rolls"
    if any(k in n for k in ["суп", "том ям", "том-ям", "вок", "лапш", "горяч", "паста", "удон", "рамен", "wok"]):
        return "hot"
    return "other"


async def build_resolver(session: AsyncSession) -> Callable[[WebhookItem], str]:
    rows = (await session.execute(select(ProductStationMap))).scalars().all()
    by_product = {r.product_id: r.station for r in rows
                  if r.product_id and not r.product_id.startswith(NAME_PREFIX)}
    by_name = {r.product_id[len(NAME_PREFIX):]: r.station for r in rows
               if r.product_id and r.product_id.startswith(NAME_PREFIX)}
    by_category = {r.category_id: r.station for r in rows if r.category_id and not r.product_id}

    def resolve(item: WebhookItem) -> str:
        if item.product_id in by_product:
            return by_product[item.product_id]
        if item.category_id and item.category_id in by_category:
            return by_category[item.category_id]
        # ручная разметка по названию (надёжнее всего)
        if norm_name(item.name) in by_name:
            return by_name[norm_name(item.name)]
        guess = classify_name(item.name)
        if guess != "other":
            return guess
        return "other"

    return resolve
