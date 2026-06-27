from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import OrderItem
from app.stations import classify_name, classify_product_raw


def _kitchen_zone_by_name(name: str, station: str | None) -> str:
    """До-merge зона по имени блюда; если имя зону не даёт — оставляем station."""
    z = classify_name(name)
    if z != "other":
        return z
    # classify_product_raw НЕ применяет merge_zone (hot остаётся hot), чтобы
    # бэкфилл сохранял до-merge зону так же, как это делает ingest из Task 1.
    z = classify_product_raw(name, group_path=None, group_root=None)
    if z != "other":
        return z
    return station or "other"


async def backfill_kitchen_zone(session: AsyncSession) -> int:
    rows = (await session.execute(
        select(OrderItem).where(OrderItem.kitchen_zone.is_(None)))).scalars().all()
    for it in rows:
        it.kitchen_zone = _kitchen_zone_by_name(it.name, it.station)
    await session.commit()
    return len(rows)
