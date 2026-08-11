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


def _row(number, delay, service="COURIER"):
    return {"Delivery.Number": number, "Delivery.Courier": "Иванов",
            "Delivery.Delay": delay, "Delivery.ServiceType": service,
            "Delivery.ExpectedTime": "2026-08-09T14:00:00",
            "Delivery.ActualTime": "2026-08-09T14:47:00",
            "Delivery.Address": "ул. Абая 1"}


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
