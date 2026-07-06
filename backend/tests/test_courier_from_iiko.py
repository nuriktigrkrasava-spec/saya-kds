"""Курьер, назначенный в iiko, подтягивается в заказ: имя на доску сборщика,
код RMS — чтобы заказ появился у курьера в Telegram (/tg/my-orders)."""
import pytest

from app import ingest, iiko_cloud, rms
from app.schemas import NormalizedOrder


def _wrapper(courier: dict | None) -> dict:
    o = {
        "number": 89,
        "status": "OnWay",
        "items": [{"product": {"id": "p1", "name": "Ролл"}, "amount": 1}],
    }
    if courier is not None:
        o["courierInfo"] = {"courier": courier, "isCourierSelectedManually": False}
    return {"id": "ord-89", "order": o}


def test_normalize_full_order_extracts_courier():
    n = iiko_cloud.normalize_full_order(
        _wrapper({"id": "2e1a2c76", "name": "Адильхан курьер"}), "org-1")
    assert n.courier_iiko_id == "2e1a2c76"
    assert n.courier_name == "Адильхан курьер"


def test_normalize_full_order_without_courier():
    n = iiko_cloud.normalize_full_order(_wrapper(None), "org-1")
    assert n.courier_iiko_id is None
    assert n.courier_name is None


@pytest.mark.asyncio
async def test_ingest_sets_courier_from_iiko(session, monkeypatch):
    async def fake_cached():
        return ([{"id": "2e1a2c76", "name": "Адильхан курьер", "code": "2393707",
                  "role": "КЭКСПО20", "department_codes": ["10ASTKBN"],
                  "deleted": False, "is_employee": True}], [])
    monkeypatch.setattr(rms, "_employees_and_departments_cached", fake_cached)

    data = NormalizedOrder(iiko_order_id="ord-89", org_id="org-1", number="89",
                           courier_iiko_id="2e1a2c76", courier_name="Адильхан курьер")
    order = await ingest.ingest_order(session, data)
    assert order.assigned_courier_name == "Адильхан курьер"
    assert order.assigned_courier_code == "2393707"


@pytest.mark.asyncio
async def test_ingest_keeps_our_assignment_when_iiko_empty(session, monkeypatch):
    """Пустой курьер из iiko НЕ стирает назначение, сделанное сборщиком."""
    data = NormalizedOrder(iiko_order_id="ord-90", org_id="org-1", number="90")
    order = await ingest.ingest_order(session, data)
    order.assigned_courier_code = "C7"
    order.assigned_courier_name = "Наш Курьер"
    await session.commit()

    order = await ingest.ingest_order(session, data)  # повторный вебхук без курьера
    assert order.assigned_courier_name == "Наш Курьер"
    assert order.assigned_courier_code == "C7"


@pytest.mark.asyncio
async def test_ingest_courier_name_even_if_rms_down(session, monkeypatch):
    """RMS недоступен -> код не достали, но имя на доске всё равно показываем."""
    async def boom():
        raise RuntimeError("rms down")
    monkeypatch.setattr(rms, "_employees_and_departments_cached", boom)

    data = NormalizedOrder(iiko_order_id="ord-91", org_id="org-1", number="91",
                           courier_iiko_id="xxx", courier_name="Айболат")
    order = await ingest.ingest_order(session, data)
    assert order.assigned_courier_name == "Айболат"
    assert order.assigned_courier_code is None
