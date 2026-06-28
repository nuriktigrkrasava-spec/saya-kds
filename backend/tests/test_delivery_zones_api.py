import pytest
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
async def test_delivery_zone_crud(client):
    async with client as c:
        poly = [[51.1, 71.4], [51.1, 71.5], [51.2, 71.5]]
        r = await c.post("/admin/delivery-zones",
                         json={"org_id": "demo", "name": "Центр", "polygon": poly, "color": "#f00"})
        assert r.status_code == 200
        zid = r.json()["id"]
        assert r.json()["name"] == "Центр"

        r = await c.get("/admin/delivery-zones?org_id=demo")
        assert len(r.json()) == 1

        r = await c.put(f"/admin/delivery-zones/{zid}",
                        json={"name": "Левый берег", "polygon": poly, "color": "#0f0"})
        assert r.json()["name"] == "Левый берег"

        r = await c.delete(f"/admin/delivery-zones/{zid}")
        assert r.json() == {"ok": True}
        r = await c.get("/admin/delivery-zones?org_id=demo")
        assert r.json() == []


@pytest.mark.asyncio
async def test_auto_grid_creates_zones_and_demand_counts(client, session):
    from app.models import Order
    # два заказа с координатами в разных углах
    session.add(Order(iiko_order_id="g1", org_id="demo", number="1",
                      address_lat=51.05, address_lon=71.25))
    session.add(Order(iiko_order_id="g2", org_id="demo", number="2",
                      address_lat=51.28, address_lon=71.65))
    await session.commit()
    async with client as c:
        r = await c.post("/admin/zones/auto-grid?org_id=demo&n=4")
        assert r.status_code == 200
        assert r.json()["zones"] == 16

        # demand-zones возвращает зоны со счётчиком; сумма count == 2 заказа
        r = await c.get("/admin/demand-zones?org_id=demo")
        zones = r.json()
        assert len(zones) == 16
        assert sum(z["count"] for z in zones) == 2


@pytest.mark.asyncio
async def test_auto_grid_replaces_only_auto_zones(client, session):
    async with client as c:
        # ручная зона остаётся
        await c.post("/admin/delivery-zones",
                     json={"org_id": "demo", "name": "Ручная", "polygon": [[51.1, 71.4]]})
        await c.post("/admin/zones/auto-grid?org_id=demo&n=2")
        await c.post("/admin/zones/auto-grid?org_id=demo&n=2")  # повтор не плодит
        r = await c.get("/admin/delivery-zones?org_id=demo")
        names = [z["name"] for z in r.json()]
        assert "Ручная" in names
        assert names.count("A1") == 1  # авто-зоны заменились, не задвоились


@pytest.mark.asyncio
async def test_seed_populates_analytics_and_zones(client):
    async with client as c:
        await c.post("/debug/seed?org_id=demo")
        # этапы аналитики больше не нули: доставка и готовка посчитаны
        r = await c.get("/admin/stage-stats?org_id=demo")
        stages = {s["key"]: s for s in r.json()["stage_aggregates"]["stages"]}
        assert stages["delivery"]["count"] > 0   # «0 доставок» закрыт
        assert stages["cooking"]["count"] > 0
        assert r.json()["sla"]["total"] > 0
        # зоны спроса наполнены заказами
        await c.post("/admin/zones/auto-grid?org_id=demo&n=6")
        zr = await c.get("/admin/demand-zones?org_id=demo")
        assert sum(z["count"] for z in zr.json()) >= 6


@pytest.mark.asyncio
async def test_analytics_export_disabled_without_key(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "analytics_api_key", "")  # ключ не задан -> выключено
    async with client as c:
        r = await c.get("/admin/analytics/export?org_id=demo")
        assert r.status_code == 503


@pytest.mark.asyncio
async def test_analytics_export_rejects_bad_key(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "analytics_api_key", "secret")
    async with client as c:
        assert (await c.get("/admin/analytics/export?org_id=demo")).status_code == 401
        r = await c.get("/admin/analytics/export?org_id=demo",
                        headers={"X-API-Key": "wrong"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_analytics_export_returns_full_bundle(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "analytics_api_key", "secret")
    async with client as c:
        await c.post("/debug/seed?org_id=demo")
        r = await c.get("/admin/analytics/export?org_id=demo",
                        headers={"X-API-Key": "secret"})
        assert r.status_code == 200
        data = r.json()
        for k in ("analytics", "stage_stats", "kitchen_zones", "zone_load",
                  "demand", "anomalies", "journal"):
            assert k in data, f"missing {k}"
        stages = {s["key"]: s for s in data["stage_stats"]["stage_aggregates"]["stages"]}
        assert stages["delivery"]["count"] > 0   # доставка из seed видна


@pytest.mark.asyncio
async def test_delete_order_by_number(client, session):
    from app.models import Order, OrderItem
    o = Order(iiko_order_id="del-1", org_id="demo", number="150")
    session.add(o)
    await session.commit()
    session.add(OrderItem(order_id=o.id, product_id="p", name="X"))
    await session.commit()
    async with client as c:
        # чужой org не удалит
        r = await c.delete("/admin/orders/by-number/150?org_id=other")
        assert r.status_code == 404
        # свой org удаляет
        r = await c.delete("/admin/orders/by-number/150?org_id=demo")
        assert r.status_code == 200 and r.json()["deleted_number"] == "150"
        # больше не отдаётся
        r = await c.get("/orders?org_id=demo")
        assert all(x["number"] != "150" for x in r.json())
