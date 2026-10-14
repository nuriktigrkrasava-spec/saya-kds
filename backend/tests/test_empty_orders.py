"""Пустые заказы не должны заводиться и не должны висеть на планшете.

На проде у поваров с 13-14 августа висели пять записей без номера, статуса и
позиций — их не закрыть и не убрать, они просто занимали экран.
"""
from app.time_utils import utc_now
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app import ingest
from app.api import app
from app.db import get_session
from app.models import Order, OrderItem
from app.schemas import NormalizedOrder


@pytest.fixture(autouse=True)
def no_secret(monkeypatch):
    """Тесты ниже про поведение уборки, а не про доступ: секрет проверяет
    отдельный тест, остальным он только мешает (в .env он задан)."""
    from app.config import settings
    monkeypatch.setattr(settings, "webhook_secret", "")


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
    old = utc_now() - timedelta(hours=5)
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
                      status="", created_at=utc_now()))
    await session.commit()

    async with client as c:
        r = await c.post("/admin/cleanup-empty-orders?org_id=org-1")
    assert r.json()["deleted"] == 0


@pytest.mark.asyncio
async def test_broadcast_is_skipped_when_nothing_was_created(session, monkeypatch):
    """Пустышку не завели — рассылать нечего, и падать тоже нельзя.

    23.08.2026: ingest вернул None, поллер сунул его в broadcast_order, тот упал
    на order.id — проход обрывался, курсор не двигался, заказы приходили с
    задержкой до 20 минут вместо минуты.
    """
    from app import poller, iiko_cloud, realtime

    poller._revisions["org-1"] = 10
    sent = []

    async def fake_rows(org_id, start_revision):
        return {"orders": [{"id": "junk"}, {"id": "good"}], "maxRevision": 42}

    def fake_normalize(wrapper, org_id):
        empty = wrapper["id"] == "junk"
        return NormalizedOrder(iiko_order_id=wrapper["id"], org_id=org_id,
                               number="" if empty else "9", status="New",
                               items=[] if empty else [
                                   __import__("app.schemas", fromlist=["WebhookItem"])
                                   .WebhookItem(product_id="p", name="Ролл")])

    async def fake_broadcast(order):
        sent.append(order.number)

    monkeypatch.setattr(iiko_cloud, "fetch_deliveries_by_revision", fake_rows)
    monkeypatch.setattr(iiko_cloud, "normalize_full_order", fake_normalize)
    monkeypatch.setattr(realtime, "broadcast_order", fake_broadcast)

    class _Maker:
        def __call__(self):
            return self
        async def __aenter__(self):
            return session
        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(poller, "SessionLocal", _Maker())

    await poller.poll_once("org-1")

    assert sent == ["9"], "рассылаем только заведённый заказ"
    assert poller._revisions["org-1"] == 42, "проход должен дойти до конца"


@pytest.mark.asyncio
async def test_cleanup_spares_orders_that_have_a_number(client, session):
    """Заказ с номером, но без позиций, приём оставляет намеренно — позиции
    доедут следующим событием. Уборка не имеет права его сносить.

    Иначе из аналитики молча исчезает реальная доставка вместе с журналом.
    """
    old = utc_now() - timedelta(hours=5)
    session.add(Order(iiko_order_id="waiting", org_id="org-1", number="88",
                      status="New", created_at=old))
    await session.commit()

    async with client as c:
        r = await c.post("/admin/cleanup-empty-orders?org_id=org-1")
    assert r.json()["deleted"] == 0

    left = (await session.execute(select(Order.iiko_order_id))).scalars().all()
    assert left == ["waiting"]


@pytest.mark.asyncio
@pytest.mark.requires_real_auth
async def test_cleanup_requires_the_secret(client, session, monkeypatch):
    """Ручка удаляет данные — знать org_id для этого мало."""
    from app.config import settings
    monkeypatch.setattr(settings, "webhook_secret", "s3cret")

    old = utc_now() - timedelta(hours=5)
    session.add(Order(iiko_order_id="junk2", org_id="org-1", number="",
                      status="", created_at=old))
    await session.commit()

    async with client as c:
        denied = await c.post("/admin/cleanup-empty-orders?org_id=org-1")
        from app.security import issue_token
        token=issue_token({"employee_id":"tester","org_id":"org-1","role":"ADM","name":"tester","branch_code":"B1"})
        allowed = await c.post("/admin/cleanup-empty-orders?org_id=org-1", headers={"Authorization":"Bearer "+token})
    assert denied.status_code == 401
    assert allowed.json()["deleted"] == 1
