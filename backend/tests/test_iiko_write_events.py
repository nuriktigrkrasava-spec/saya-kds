from sqlalchemy import select
from app.models import Order, OrderEvent


async def test_log_iiko_event_writes_row(session):
    from app.api import _log_iiko_event
    o = Order(iiko_order_id="o1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    await _log_iiko_event(session, o, "update_order_courier", True, {"correlationId": "c1"})
    ev = (await session.execute(select(OrderEvent))).scalars().all()
    assert len(ev) == 1
    assert ev[0].type == "iiko_write"
    assert ev[0].payload["method"] == "update_order_courier"
    assert ev[0].payload["ok"] is True


async def test_log_action_writes_actor_and_org(session):
    from app.api import _log_action
    from app.models import Order, OrderEvent
    from sqlalchemy import select
    o = Order(iiko_order_id="a1", org_id="org-9", number="55")
    session.add(o); await session.commit()
    await _log_action(session, o, "item_ready", actor_name="Коля",
                      actor_code="K1", item_id=3, payload={"name": "Ролл"})
    ev = (await session.execute(select(OrderEvent))).scalar_one()
    assert ev.type == "item_ready" and ev.org_id == "org-9"
    assert ev.actor_name == "Коля" and ev.item_id == 3
    assert ev.payload["name"] == "Ролл"
