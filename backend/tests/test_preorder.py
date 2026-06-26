from datetime import datetime, timedelta
import respx
from sqlalchemy import select
from app.models import Order


@respx.mock
async def test_release_due_releases_in_window(session, monkeypatch):
    from app import preorder, realtime
    sent = []
    async def fake_broadcast(order): sent.append(order.id)
    monkeypatch.setattr(realtime, "broadcast_order", fake_broadcast)
    now = datetime(2026, 6, 26, 10, 0, 0)
    # в окне (<= now+60м) и held -> выпустить
    o1 = Order(iiko_order_id="a", org_id="org-1", number="1", status="WaitCooking",
               preorder_held=True, complete_before=now + timedelta(minutes=30))
    # ещё далеко -> не трогать
    o2 = Order(iiko_order_id="b", org_id="org-1", number="2", status="WaitCooking",
               preorder_held=True, complete_before=now + timedelta(hours=3))
    # терминальный -> пропустить
    o3 = Order(iiko_order_id="c", org_id="org-1", number="3", status="Closed",
               preorder_held=True, complete_before=now + timedelta(minutes=5))
    session.add_all([o1, o2, o3]); await session.commit()

    n = await preorder.release_due(session, now)
    assert n == 1 and sent == [o1.id]
    refreshed = {o.iiko_order_id: o for o in (await session.execute(select(Order))).scalars()}
    assert refreshed["a"].preorder_held is False
    assert refreshed["b"].preorder_held is True
