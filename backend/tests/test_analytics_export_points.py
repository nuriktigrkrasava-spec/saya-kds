"""Выгрузка для внешней аналитики отдаёт точки доставки с зоной.

В iiko OLAP нет ни наших зон, ни привязки заказа к зоне — это данные KDS.
"""
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from app.api import app
from app.db import get_session
from app.config import settings
from app.models import Order, DeliveryZone


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://t")
    app.dependency_overrides.clear()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "analytics_api_key", "secret")
    return "secret"


@pytest.mark.asyncio
async def test_export_returns_delivery_points_with_zone(client, session, api_key):
    now = datetime.utcnow()
    session.add(DeliveryZone(org_id="demo", name="Центр",
                             polygon=[[51.0, 71.2], [51.0, 71.5],
                                      [51.3, 71.5], [51.3, 71.2]]))
    session.add(Order(iiko_order_id="p1", org_id="demo", number="101",
                      created_at=now - timedelta(hours=2),
                      address_lat=51.15, address_lon=71.35,
                      on_way_at=now - timedelta(hours=1),
                      delivered_at=now - timedelta(minutes=30)))
    await session.commit()

    async with client as c:
        r = await c.get("/admin/analytics/export?org_id=demo",
                        headers={"X-API-Key": api_key})
    assert r.status_code == 200
    points = r.json()["delivery_points"]
    assert len(points) == 1
    p = points[0]
    assert p["number"] == "101"
    assert (p["lat"], p["lon"]) == (51.15, 71.35)
    assert p["zone_name"] == "Центр"
    assert p["zone_id"] is not None
    assert p["delivery_seconds"] == 1800


@pytest.mark.asyncio
async def test_export_point_outside_any_zone_has_null_zone(client, session, api_key):
    session.add(DeliveryZone(org_id="demo", name="Центр",
                             polygon=[[51.0, 71.2], [51.0, 71.3],
                                      [51.1, 71.3], [51.1, 71.2]]))
    session.add(Order(iiko_order_id="p2", org_id="demo", number="102",
                      created_at=datetime.utcnow(),
                      address_lat=52.9, address_lon=73.9))
    await session.commit()

    async with client as c:
        r = await c.get("/admin/analytics/export?org_id=demo",
                        headers={"X-API-Key": api_key})
    p = r.json()["delivery_points"][0]
    assert p["zone_id"] is None
    assert p["zone_name"] is None
    assert p["delivery_seconds"] is None


@pytest.mark.asyncio
async def test_export_skips_orders_without_coordinates(client, session, api_key):
    session.add(Order(iiko_order_id="p3", org_id="demo", number="103",
                      created_at=datetime.utcnow()))
    await session.commit()

    async with client as c:
        r = await c.get("/admin/analytics/export?org_id=demo",
                        headers={"X-API-Key": api_key})
    assert r.json()["delivery_points"] == []
