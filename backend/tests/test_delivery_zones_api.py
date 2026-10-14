from app.time_utils import utc_now
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

        r = await c.put(f"/admin/delivery-zones/{zid}?org_id=demo",
                        json={"name": "Левый берег", "polygon": poly, "color": "#0f0"})
        assert r.json()["name"] == "Левый берег"

        r = await c.delete(f"/admin/delivery-zones/{zid}?org_id=demo")
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


@pytest.mark.asyncio
async def test_summary_period_vs_previous(client, session):
    from datetime import datetime, timedelta
    from app.models import Order
    from app.config import settings
    # Якорим на ЛОКАЛЬНЫЙ полдень — детерминированно внутри суток при любом TZ.
    off = timedelta(hours=settings.iiko_tz_offset_hours)
    local_today = (utc_now() + off).date()
    noon = datetime(local_today.year, local_today.month, local_today.day, 12) - off
    # 2 заказа в текущем локальном дне, 1 в предыдущем (по created_at)
    session.add(Order(iiko_order_id="s1", org_id="demo", number="1",
                      created_at=noon, on_way_at=noon, delivered_at=noon + timedelta(minutes=20)))
    session.add(Order(iiko_order_id="s2", org_id="demo", number="2", created_at=noon))
    session.add(Order(iiko_order_id="s3", org_id="demo", number="3",
                      created_at=noon - timedelta(days=1)))
    await session.commit()
    today = local_today.strftime("%Y-%m-%d")
    async with client as c:
        r = await c.get(f"/admin/summary?org_id=demo&from={today}&to={today}")
        d = r.json()
        assert d["current"]["orders"] == 2          # сегодня
        assert d["current"]["delivered"] == 1
        assert d["previous"]["orders"] == 1          # вчера (предыдущий равный период)
        # Сходящаяся когорта: in_progress = заказы периода, не доставленные (s2).
        assert d["in_progress"] == 1
        assert d["current"]["orders"] == d["current"]["delivered"] + d["in_progress"]


@pytest.mark.asyncio
async def test_analytics_respects_period(client, session):
    """Сводка /admin/analytics обязана учитывать period (from/to) — иначе таблица
    сотрудников/зон под общим пикером дат не стыкуется с карточками за период."""
    from datetime import datetime, timedelta
    from app.models import Order, OrderItem
    from app.config import settings
    off = timedelta(hours=settings.iiko_tz_offset_hours)
    local_today = (utc_now() + off).date()
    noon = datetime(local_today.year, local_today.month, local_today.day, 12) - off
    old = noon - timedelta(days=10)
    today_o = Order(iiko_order_id="p1", org_id="demo", number="10", created_at=noon)
    today_o.items = [OrderItem(name="Ролл", station="rolls", checked_by="Аня",
                               station_accepted_at=noon,
                               station_ready_at=noon + timedelta(minutes=5))]
    old_o = Order(iiko_order_id="p2", org_id="demo", number="11", created_at=old)
    old_o.items = [OrderItem(name="Пицца", station="pizza", checked_by="Боб",
                             station_accepted_at=old,
                             station_ready_at=old + timedelta(minutes=5))]
    session.add_all([today_o, old_o])
    await session.commit()
    today = local_today.strftime("%Y-%m-%d")
    async with client as c:
        r = await c.get(f"/admin/analytics?org_id=demo&from={today}&to={today}")
        d = r.json()
        assert d["totals"]["orders"] == 1            # только сегодняшний, не 10-дневной давности
        names = {e["name"] for e in d["employees"]}
        assert names == {"Аня"}                      # Боб (10 дней назад) вне периода


@pytest.mark.asyncio
async def test_dashboard_counts_by_local_day(client, session):
    """Дашборд считает «доставлено сегодня» по ЛОКАЛЬНОМУ дню. Заказ, доставленный
    в 02:00 локального сегодня (= вчера по UTC при оффсете +5), должен попасть в
    сегодня. Со старой UTC-логикой он бы выпал."""
    from datetime import datetime, timedelta
    from app.models import Order
    from app.config import settings
    off = timedelta(hours=settings.iiko_tz_offset_hours)
    local_today = (utc_now() + off).date()
    # 02:00 локального сегодня, переведённое в UTC (при оффсете>=3 это вчерашняя UTC-дата)
    local_early = datetime(local_today.year, local_today.month, local_today.day, 2) - off
    session.add(Order(iiko_order_id="d1", org_id="demo", number="20",
                      created_at=local_early, on_way_at=local_early,
                      delivered_at=local_early + timedelta(minutes=10)))
    await session.commit()
    async with client as c:
        r = await c.get("/admin/dashboard?org_id=demo")
        assert r.json()["delivered_today"] == 1


@pytest.mark.asyncio
async def test_dashboard_couriers_scoped_to_branch(client, session, monkeypatch):
    """«На смене» считает только курьеров нашего филиала (branch_code), а не всех."""
    from datetime import datetime
    from app.models import CourierLocation
    from app.config import settings
    monkeypatch.setattr(settings, "branch_code", "ALIYEV")
    now = utc_now()
    session.add(CourierLocation(courier_code="c-our", branch_code="ALIYEV",
                                lat=51.1, lon=71.4, updated_at=now))
    session.add(CourierLocation(courier_code="c-other", branch_code="OTHER",
                                lat=51.2, lon=71.5, updated_at=now))
    await session.commit()
    async with client as c:
        r = await c.get("/admin/dashboard?org_id=demo")
        assert r.json()["on_shift_couriers"] == 1   # только курьер филиала ALIYEV
