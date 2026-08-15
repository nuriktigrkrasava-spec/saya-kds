"""Пустые заказы не должны заводиться и не должны висеть на планшете.

На проде у поваров с 13-14 августа висели пять записей без номера, статуса и
позиций — их не закрыть и не убрать, они просто занимали экран.
"""
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app import ingest
from app.api import app
from app.db import get_session
from app.models import Order, OrderItem
from app.schemas import NormalizedOrder


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://t")
    app.dependency_overrides.clear()


def _empty(order_id="e1"):
    return NormalizedOrder(iiko_order_id=order_id, org_id="org-1", number="",
                           status="", items=[])


@pytest.mark.asyncio
async def test_new_order_without_items_is_not_created(session):
    """Заказ без позиций показывать нечего — и заводить его незачем."""
    result = await ingest.ingest_order(session, _empty())
    assert result is None
    rows = (await session.execute(select(Order))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_existing_order_is_still_updated_when_items_empty(session):
    """Уже заведённый заказ обновляем как обычно: он мог опустеть при отмене."""
    session.add(Order(iiko_order_id="e2", org_id="org-1", number="7",
                      status="New"))
    await session.commit()

    data = NormalizedOrder(iiko_order_id="e2", org_id="org-1", number="7",
                           status="Closed", items=[])
    order = await ingest.ingest_order(session, data)
    assert order is not None and order.status == "Closed"


@pytest.mark.asyncio
async def test_cleanup_removes_stuck_empty_orders(client, session):
    """Уборка сносит пустые записи, но не трогает живые заказы."""
    old = datetime.utcnow() - timedelta(hours=5)
    session.add(Order(iiko_order_id="junk", org_id="org-1", number="",
                      status="", created_at=old))
    live = Order(iiko_order_id="real", org_id="org-1", number="15",
                 status="New", created_at=old)
    live.items = [OrderItem(product_id="p", name="Ролл", station="rolls")]
    session.add(live)
    await session.commit()

    async with client as c:
        r = await c.post("/admin/cleanup-empty-orders?org_id=org-1")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1

    left = (await session.execute(select(Order.iiko_order_id))).scalars().all()
    assert left == ["real"]


@pytest.mark.asyncio
async def test_cleanup_spares_orders_from_the_last_minutes(client, session):
    """Свежий пустой заказ мог только что открыться — состав ещё едет."""
    session.add(Order(iiko_order_id="fresh", org_id="org-1", number="",
                      status="", created_at=datetime.utcnow()))
    await session.commit()

    async with client as c:
        r = await c.post("/admin/cleanup-empty-orders?org_id=org-1")
    assert r.json()["deleted"] == 0
