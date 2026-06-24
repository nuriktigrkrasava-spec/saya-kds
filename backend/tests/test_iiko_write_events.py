from sqlalchemy import select
from app.models import Order, OrderEvent


async def test_log_iiko_event_writes_row(session):
    from app.api import _log_iiko_event
    o = Order(iiko_order_id="o1", org_id="org-1", number="1")
    session.add(o); await session.commit()
    await _log_iiko_event(session, o.id, "update_order_courier", True, {"correlationId": "c1"})
    ev = (await session.execute(select(OrderEvent))).scalars().all()
    assert len(ev) == 1
    assert ev[0].type == "iiko_write"
    assert ev[0].payload["method"] == "update_order_courier"
    assert ev[0].payload["ok"] is True
