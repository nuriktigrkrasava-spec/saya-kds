import pytest
from sqlalchemy import select
from app.models import Order, OrderItem
from app.scripts_backfill import backfill_kitchen_zone


@pytest.mark.asyncio
async def test_backfill_separates_hot_from_pizza(session):
    o = Order(iiko_order_id="o1", org_id="demo", number="1")
    session.add(o)
    await session.flush()
    # Имитируем «старые» строки: kitchen_zone не заполнен, station уже слитый.
    session.add_all([
        OrderItem(order_id=o.id, name="Том Ям", station="pizza", kitchen_zone=None),
        OrderItem(order_id=o.id, name="Пицца Пепперони", station="pizza", kitchen_zone=None),
        OrderItem(order_id=o.id, name="Сет Филадельфия", station="rolls", kitchen_zone=None),
    ])
    await session.commit()

    n = await backfill_kitchen_zone(session)
    assert n == 3

    rows = (await session.execute(select(OrderItem).order_by(OrderItem.id))).scalars().all()
    assert rows[0].kitchen_zone == "hot"     # Том Ям отделился от pizza
    assert rows[1].kitchen_zone == "pizza"
    assert rows[2].kitchen_zone == "rolls"
