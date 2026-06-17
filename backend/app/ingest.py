from __future__ import annotations
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Order, OrderItem
from app.schemas import NormalizedOrder
from app.stations import build_resolver


async def ingest_order(session: AsyncSession, data: NormalizedOrder) -> Order:
    """Идемпотентно по iiko_order_id: создаёт или обновляет заказ и пересоздаёт позиции."""
    resolve = await build_resolver(session)

    existing = (await session.execute(
        select(Order).where(Order.iiko_order_id == data.iiko_order_id))).scalar_one_or_none()

    if existing is None:
        order = Order(iiko_order_id=data.iiko_order_id)
        session.add(order)
    else:
        order = existing
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

    await session.flush()  # гарантирует order.id

    for it in data.items:
        session.add(OrderItem(
            order_id=order.id,
            product_id=it.product_id,
            name=it.name,
            amount=it.amount,
            combo_id=it.combo_id,
            station=resolve(it),
        ))
    await session.commit()
    await session.refresh(order)
    return order
