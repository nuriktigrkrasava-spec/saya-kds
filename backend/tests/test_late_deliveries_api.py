"""Эндпоинт опозданий: филиал → OLAP «доставки» → сводка + список заказов."""
import pytest
from httpx import AsyncClient, ASGITransport
from app.api import app
from app.db import get_session
from app import olap


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://t")
    app.dependency_overrides.clear()


def _row(number, delay, service="COURIER", order_id="oid-1",
         address="ул. Абая 1"):
    return {"Delivery.Id": order_id, "Delivery.Address": address,
            "Delivery.Number": number, "Delivery.Courier": "Иванов",
            "Delivery.Delay": delay, "Delivery.ServiceType": service,
            "Delivery.ExpectedTime": "2026-08-09T14:00:00",
            "Delivery.ActualTime": "2026-08-09T14:47:00"}


@pytest.fixture
def fake_olap(monkeypatch):
    """Подменяем поход в RMS: тест про эндпоинт, а не про сеть."""
    calls = {}

    async def fake_rows(department, date_from, date_to):
        calls["department"] = department
        calls["period"] = (date_from, date_to)
        return [_row(1, 47), _row(2, 0), _row(3, 90, service="PICKUP")]

    async def fake_department(org_id):
        return "Астана, ул. Г.Алиева 10 (EXPO)"

    monkeypatch.setattr(olap, "fetch_delivery_rows", fake_rows)
    monkeypatch.setattr(olap, "department_name", fake_department)
    return calls


@pytest.mark.asyncio
async def test_late_deliveries_for_branch(client, fake_olap):
    async with client as c:
        r = await c.get("/admin/late-deliveries?org_id=org-1&from=2026-08-01&to=2026-08-10")
    assert r.status_code == 200
    body = r.json()
    assert body["department"] == "Астана, ул. Г.Алиева 10 (EXPO)"
    assert body["total"] == 2          # самовывоз не в счёт
    assert body["late_count"] == 1
    assert body["orders"][0]["number"] == 1
    assert body["orders"][0]["delay_minutes"] == 47
    assert fake_olap["period"] == ("2026-08-01", "2026-08-10")


@pytest.mark.asyncio
async def test_placeholder_address_filled_from_our_base(client, session, monkeypatch):
    """OLAP не знает line1, а мы знаем: адрес берём из своей базы по Delivery.Id."""
    from app.models import Order
    session.add(Order(iiko_order_id="oid-web", org_id="org-1", number="5",
                      address="г. Астана, ул. рыскулова 5/3, квартира 6"))
    await session.commit()

    async def rows(department, date_from, date_to):
        return [_row(5, 20, order_id="oid-web", address="ул. ---------- д. 0")]

    async def dep(org_id):
        return "Алиева"

    monkeypatch.setattr(olap, "fetch_delivery_rows", rows)
    monkeypatch.setattr(olap, "department_name", dep)

    async with client as c:
        r = await c.get("/admin/late-deliveries?org_id=org-1")
    assert r.json()["orders"][0]["address"] == "г. Астана, ул. рыскулова 5/3, квартира 6"


@pytest.mark.asyncio
async def test_address_absent_everywhere_stays_empty(client, session, monkeypatch):
    """Заказа нет в нашей базе — честно отдаём None, а не полоски."""
    async def rows(department, date_from, date_to):
        return [_row(5, 20, order_id="неизвестный", address="ул. ---------- д. 0")]

    async def dep(org_id):
        return "Алиева"

    monkeypatch.setattr(olap, "fetch_delivery_rows", rows)
    monkeypatch.setattr(olap, "department_name", dep)

    async with client as c:
        r = await c.get("/admin/late-deliveries?org_id=org-1")
    assert r.json()["orders"][0]["address"] is None


@pytest.mark.asyncio
async def test_reason_is_reported_next_to_each_late_order(client, session, monkeypatch):
    """Управляющему нужен виновник этапа, а не только факт опоздания."""
    from datetime import datetime, timedelta
    from app.models import Order, OrderItem

    base = datetime.utcnow() - timedelta(hours=3)
    order = Order(iiko_order_id="oid-late", org_id="org-1", number="7",
                  created_at=base)
    item = OrderItem(product_id="p", name="Ролл", station="rolls",
                     station_accepted_at=base + timedelta(minutes=1),
                     station_ready_at=base + timedelta(minutes=50))
    order.items = [item]
    order.assembled_at = base + timedelta(minutes=52)
    order.on_way_at = base + timedelta(minutes=55)
    order.delivered_at = base + timedelta(minutes=70)
    session.add(order)
    await session.commit()

    async def rows(department, date_from, date_to):
        return [_row(7, 25, order_id="oid-late")]

    async def dep(org_id):
        return "Алиева"

    monkeypatch.setattr(olap, "fetch_delivery_rows", rows)
    monkeypatch.setattr(olap, "department_name", dep)

    async with client as c:
        r = await c.get("/admin/late-deliveries?org_id=org-1")
    body = r.json()
    assert body["orders"][0]["reason"] is None or "label" in body["orders"][0]["reason"]
    assert sum(body["reasons"].values()) == body["late_count"]


@pytest.mark.asyncio
async def test_orders_missing_from_our_base_are_counted_as_unknown(client, fake_olap):
    """Заказ старее нашей истории — причина неизвестна, но из сводки не исчезает."""
    async with client as c:
        r = await c.get("/admin/late-deliveries?org_id=org-1")
    body = r.json()
    assert body["reasons"] == {"unknown": 1}
    assert body["orders"][0]["reason"] is None


@pytest.mark.asyncio
async def test_unknown_branch_is_reported_not_silently_empty(client, monkeypatch):
    async def no_department(org_id):
        return None
    monkeypatch.setattr(olap, "department_name", no_department)

    async with client as c:
        r = await c.get("/admin/late-deliveries?org_id=nope")
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


@pytest.mark.asyncio
async def test_export_includes_late_deliveries(client, fake_olap, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "analytics_api_key", "secret")

    async with client as c:
        r = await c.get("/admin/analytics/export?org_id=org-1",
                        headers={"X-API-Key": "secret"})
    assert r.json()["late_deliveries"]["late_count"] == 1


@pytest.mark.asyncio
async def test_export_survives_olap_outage(client, monkeypatch):
    """RMS лежит — остальная выгрузка всё равно должна доехать."""
    from app.config import settings
    monkeypatch.setattr(settings, "analytics_api_key", "secret")

    async def boom(org_id):
        raise RuntimeError("rms down")
    monkeypatch.setattr(olap, "department_name", boom)

    async with client as c:
        r = await c.get("/admin/analytics/export?org_id=org-1",
                        headers={"X-API-Key": "secret"})
    assert r.status_code == 200
    assert "rms down" in r.json()["late_deliveries"]["error"]
