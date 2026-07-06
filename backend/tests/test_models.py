from sqlalchemy import select
from app.models import Order, OrderItem


async def test_order_has_iiko_sync_error_default_none(session):
    from app.models import Order
    o = Order(iiko_order_id="x1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    assert o.iiko_sync_error is None


async def test_order_event_has_actor_and_org_fields(session):
    from app.models import Order, OrderEvent
    o = Order(iiko_order_id="oe1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    ev = OrderEvent(order_id=o.id, org_id="org-1", type="item_accept",
                    actor_name="Коля", actor_code="K1", item_id=7, payload={})
    session.add(ev); await session.commit()
    assert ev.org_id == "org-1" and ev.actor_name == "Коля"
    assert ev.actor_code == "K1" and ev.item_id == 7


async def test_order_with_items_persists(session):
    order = Order(iiko_order_id="o1", org_id="org-1", number="101")
    order.items.append(OrderItem(product_id="p1", name="Ролл", station="rolls"))
    session.add(order)
    await session.commit()

    got = (await session.execute(select(Order).where(Order.iiko_order_id == "o1"))).scalar_one()
    assert got.org_id == "org-1"
    assert len(got.items) == 1
    assert got.items[0].station == "rolls"


async def test_telegram_link_claim_fields(session):
    from app.models import TelegramLink
    link = TelegramLink(telegram_id="42", employee_code="77",
                        employee_id="e-courier", claimed_on="2026-07-06")
    session.add(link)
    await session.commit()
    got = (await session.execute(
        select(TelegramLink).where(TelegramLink.telegram_id == "42"))).scalar_one()
    assert got.employee_id == "e-courier"
    assert got.claimed_on == "2026-07-06"
