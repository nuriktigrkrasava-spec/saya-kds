"""Изоляция филиалов: данные одного org_id не видны/не правятся через другой."""
from app.time_utils import utc_now
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from app.api import app
from app.db import get_session


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://t")
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_timeline_rejects_foreign_org(client, session):
    from app.models import Order
    o = Order(iiko_order_id="iso-1", org_id="org-a", number="500",
              created_at=datetime(2026, 6, 20, 12, 0))
    session.add(o)
    await session.commit()
    async with client as c:
        # чужой филиал не видит таймлайн
        r = await c.get(f"/admin/orders/{o.id}/timeline", params={"org_id": "org-b"})
        assert r.status_code == 404
        # свой — видит
        r = await c.get(f"/admin/orders/{o.id}/timeline", params={"org_id": "org-a"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_delivery_zone_update_delete_scoped_to_org(client):
    async with client as c:
        r = await c.post("/admin/delivery-zones",
                         json={"org_id": "org-a", "name": "Центр",
                               "polygon": [[51.1, 71.4], [51.1, 71.5], [51.2, 71.5]]})
        zid = r.json()["id"]

        # чужой org не может редактировать
        r = await c.put(f"/admin/delivery-zones/{zid}?org_id=org-b",
                        json={"name": "Взлом"})
        assert r.status_code == 404
        # и удалять
        r = await c.delete(f"/admin/delivery-zones/{zid}?org_id=org-b")
        assert r.status_code == 404

        # свой org — может
        r = await c.put(f"/admin/delivery-zones/{zid}?org_id=org-a",
                        json={"name": "Центр-2"})
        assert r.status_code == 200 and r.json()["name"] == "Центр-2"
        r = await c.delete(f"/admin/delivery-zones/{zid}?org_id=org-a")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_couriers_scoped_by_org_links(client, session, monkeypatch):
    """Без настройки branch_code курьеры на смене фильтруются по филиалу через
    telegram_links (branch_code -> org_id), а не считаются все скопом."""
    from app.models import CourierLocation, TelegramLink
    from app.config import settings
    monkeypatch.setattr(settings, "branch_code", "")
    now = utc_now()
    session.add(TelegramLink(telegram_id="1", employee_code="c-our",
                             branch_code="BR-A", org_id="org-a"))
    session.add(TelegramLink(telegram_id="2", employee_code="c-other",
                             branch_code="BR-B", org_id="org-b"))
    session.add(CourierLocation(courier_code="c-our", branch_code="BR-A",
                                lat=51.1, lon=71.4, updated_at=now))
    session.add(CourierLocation(courier_code="c-other", branch_code="BR-B",
                                lat=51.2, lon=71.5, updated_at=now))
    await session.commit()
    async with client as c:
        r = await c.get("/admin/dashboard", params={"org_id": "org-a"})
        assert r.json()["on_shift_couriers"] == 1
