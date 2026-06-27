from __future__ import annotations
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Order, OrderItem
from app.schemas import NormalizedOrder
from app.stations import build_resolver
from app.config import settings


def is_held(complete_before, now: datetime, lead_minutes: int) -> bool:
    return complete_before is not None and complete_before > now + timedelta(minutes=lead_minutes)


def _item_key(product_id: str | None, combo_id: str | None,
              name: str | None, comment: str | None) -> tuple:
    """Стабильный ключ позиции для переноса прогресса готовки между вебхуками."""
    return (product_id or "", combo_id or "", name or "", comment or "")


async def ingest_order(session: AsyncSession, data: NormalizedOrder) -> Order:
    """Идемпотентно по iiko_order_id: создаёт или обновляет заказ.

    iiko шлёт по одному заказу несколько вебхуков (New -> Bill -> Closed). Раньше
    позиции пересоздавались с нуля на каждый вебхук, и прогресс готовки
    (accepted/ready/checked) терялся — уже готовый заказ «воскресал» на кухне после
    закрытия. Теперь прогресс переносится со старых позиций по ключу.
    """
    resolve = await build_resolver(session)

    existing = (await session.execute(
        select(Order).where(Order.iiko_order_id == data.iiko_order_id))).scalar_one_or_none()

    # Снимаем прогресс готовки со старых позиций ДО удаления (плоскими значениями,
    # чтобы не читать атрибуты с уже удалённых ORM-объектов).
    prev: dict[tuple, dict] = {}
    if existing is None:
        order = Order(iiko_order_id=data.iiko_order_id)
        session.add(order)
    else:
        order = existing
        for oi in order.items:
            prev[_item_key(oi.product_id, oi.combo_id, oi.name, oi.comment)] = {
                "is_checked": oi.is_checked,
                "station_accepted_at": oi.station_accepted_at,
                "station_ready_at": oi.station_ready_at,
                "checked_at": oi.checked_at,
                "checked_by": oi.checked_by,
            }
        await session.execute(delete(OrderItem).where(OrderItem.order_id == order.id))

    order.org_id = data.org_id
    order.number = data.number
    order.status = data.status
    order.customer_name = data.customer
    order.phone = data.phone
    order.address = data.address
    order.address_lat = data.lat
    order.address_lon = data.lon
    order.total = data.total
    order.service_type = data.service_type
    order.comment = data.comment
    order.payment_type_id = data.payment_type_id
    order.payment_type_kind = data.payment_type_kind
    order.payment_type_name = data.payment_type_name
    order.complete_before = data.complete_before
    order.preorder_held = is_held(data.complete_before, datetime.utcnow(),
                                  settings.preorder_lead_minutes)

    # Тайм-штампы жизненного цикла из iiko. Заполняем ТОЛЬКО пустые поля, чтобы
    # ручной прогресс в KDS (нажатия «Забрал»/«Доставлен») имел приоритет, а iiko
    # лишь добивал то, что курьер не отметил — иначе этапы аналитики были бы по нулям.
    if existing is None and data.when_created is not None:
        order.created_at = data.when_created
    if order.assembled_at is None and data.when_cooking_completed is not None:
        order.assembled_at = data.when_cooking_completed
    if order.on_way_at is None and data.when_sended is not None:
        order.on_way_at = data.when_sended
    if order.delivered_at is None and data.when_delivered is not None:
        order.delivered_at = data.when_delivered

    await session.flush()  # гарантирует order.id

    for it in data.items:
        carry = prev.get(_item_key(it.product_id, it.combo_id, it.name, it.comment), {})
        session.add(OrderItem(
            order_id=order.id,
            product_id=it.product_id,
            name=it.name,
            amount=it.amount,
            combo_id=it.combo_id,
            station=resolve(it),
            kitchen_zone=resolve.raw(it),
            comment=it.comment,
            is_checked=carry.get("is_checked", False),
            station_accepted_at=carry.get("station_accepted_at"),
            station_ready_at=carry.get("station_ready_at"),
            checked_at=carry.get("checked_at"),
            checked_by=carry.get("checked_by"),
        ))
    await session.commit()
    await session.refresh(order)
    return order
