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
