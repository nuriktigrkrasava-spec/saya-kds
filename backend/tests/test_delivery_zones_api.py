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
