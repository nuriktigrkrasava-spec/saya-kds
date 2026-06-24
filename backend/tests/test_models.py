from sqlalchemy import select
from app.models import Order, OrderItem


async def test_order_has_iiko_sync_error_default_none(session):
    from app.models import Order
    o = Order(iiko_order_id="x1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    assert o.iiko_sync_error is None


async def test_order_with_items_persists(session):
    order = Order(iiko_order_id="o1", org_id="org-1", number="101")
    order.items.append(OrderItem(product_id="p1", name="Ролл", station="rolls"))
    session.add(order)
    await session.commit()

    got = (await session.execute(select(Order).where(Order.iiko_order_id == "o1"))).scalar_one()
    assert got.org_id == "org-1"
    assert len(got.items) == 1
    assert got.items[0].station == "rolls"
