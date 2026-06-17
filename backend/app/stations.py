from __future__ import annotations
from typing import Callable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ProductStationMap
from app.schemas import WebhookItem

VALID_STATIONS = {"rolls", "pizza", "hot", "other"}


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


async def build_resolver(session: AsyncSession) -> Callable[[WebhookItem], str]:
    rows = (await session.execute(select(ProductStationMap))).scalars().all()
    by_product = {r.product_id: r.station for r in rows if r.product_id}
    by_category = {r.category_id: r.station for r in rows if r.category_id and not r.product_id}

    def resolve(item: WebhookItem) -> str:
        if item.product_id in by_product:
            return by_product[item.product_id]
        if item.category_id and item.category_id in by_category:
            return by_category[item.category_id]
        return "other"

    return resolve
