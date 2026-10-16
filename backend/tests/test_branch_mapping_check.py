"""Сверка филиалов RMS ↔ организаций iiko Cloud.

Несматченный по названию филиал — самый неприятный отказ: заказы из вебхука не
находят доску, экран пустой, в логах ни одной ошибки. Поэтому сверяем явно.
"""
import logging

import pytest
import respx
from httpx import Response

from app import rms
from app.config import settings

BASE = "https://rms.test/resto"
CLOUD = "https://api-ru.iiko.services"

DEP_XML = """<?xml version="1.0"?>
<corporateItemDtoes>
  <corporateItemDto><id>d-aliev</id><name>Астана, ул. Г.Алиева 10 (EXPO)</name>
    <code>10ASTKBN</code><type>DEPARTMENT</type></corporateItemDto>
  <corporateItemDto><id>d-kazh</id><name>Астана, Кажымукана 12</name>
    <code>KAZH</code><type>DEPARTMENT</type></corporateItemDto>
</corporateItemDtoes>"""

EMP_XML = """<?xml version="1.0"?><employees></employees>"""

# В Cloud филиал Кажымукана назван иначе -> по имени не сматчится.
ORGS = {"organizations": [
    {"id": "org-aliev", "name": "Астана, ул. Г.Алиева 10 (EXPO)"},
    {"id": "org-kazh", "name": "Кажымукана (доставка)"},
]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "rms_base", BASE)
    monkeypatch.setattr(settings, "rms_login", "u")
    monkeypatch.setattr(settings, "rms_password", "p")
    monkeypatch.setattr(settings, "cloud_api_login", "cloud-key")
    monkeypatch.setattr(settings, "branch_code", "10ASTKBN")
    monkeypatch.setattr(settings, "branch_codes", "10ASTKBN,KAZH")
    rms._EMP_CACHE = None
    rms._ORG_MAP = None
    yield
    rms._EMP_CACHE = None
    rms._ORG_MAP = None


def _mock_all():
    respx.get(f"{BASE}/api/auth").mock(return_value=Response(200, text="tok"))
    respx.get(f"{BASE}/api/logout").mock(return_value=Response(200, text=""))
    respx.get(f"{BASE}/api/corporation/departments").mock(
        return_value=Response(200, text=DEP_XML))
    respx.get(f"{BASE}/api/employees").mock(return_value=Response(200, text=EMP_XML))
    respx.post(f"{CLOUD}/api/1/access_token").mock(
        return_value=Response(200, json={"token": "t"}))
    respx.post(f"{CLOUD}/api/1/organizations").mock(
        return_value=Response(200, json=ORGS))


@respx.mock
@pytest.mark.asyncio
async def test_report_marks_matched_and_unmatched_branches():
    _mock_all()
    rows = await rms.branch_mapping_report()
    by_code = {r["branch_code"]: r for r in rows}
    assert by_code["10ASTKBN"]["matched"] is True
    assert by_code["10ASTKBN"]["org_id"] == "org-aliev"
    kazh = by_code["KAZH"]
    assert kazh["matched"] is False and kazh["org_id"] == ""
    assert kazh["rms_name"] == "Астана, Кажымукана 12"
    assert "Кажымукана (доставка)" in kazh["problem"], \
        "в проблеме должно быть видно, с чем именно не совпало имя"


@respx.mock
@pytest.mark.asyncio
async def test_report_flags_branch_missing_in_rms(monkeypatch):
    monkeypatch.setattr(settings, "branch_codes", "10ASTKBN,NOSUCH")
    _mock_all()
    rows = await rms.branch_mapping_report()
    nosuch = next(r for r in rows if r["branch_code"] == "NOSUCH")
    assert nosuch["matched"] is False
    assert "RMS" in nosuch["problem"]


@respx.mock
@pytest.mark.asyncio
async def test_startup_check_logs_error_for_unmatched_branch(caplog):
    from app.main import _check_branch_mapping
    _mock_all()
    with caplog.at_level(logging.INFO, logger="saya.branches"):
        await _check_branch_mapping()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("KAZH" in r.getMessage() for r in errors), \
        "несматченный филиал должен быть виден в логах при старте"
    assert not any("10ASTKBN" in r.getMessage() for r in errors)


@respx.mock
@pytest.mark.asyncio
async def test_startup_check_survives_rms_outage(caplog):
    respx.get(f"{BASE}/api/auth").mock(return_value=Response(500))
    from app.main import _check_branch_mapping
    with caplog.at_level(logging.INFO, logger="saya.branches"):
        await _check_branch_mapping()  # не должно бросать — старт важнее сверки


@respx.mock
@pytest.mark.asyncio
@pytest.mark.requires_real_auth
async def test_branch_mapping_endpoint_reports_and_guards_key(monkeypatch):
    """Ручка для подключения филиала: показывает, кто не сматчился. Под ключом."""
    from httpx import ASGITransport, AsyncClient
    from app import api
    monkeypatch.setattr(settings, "admin_api_key", "s3cret")
    _mock_all()
    async with AsyncClient(transport=ASGITransport(app=api.app),
                           base_url="http://test") as c:
        assert (await c.get("/admin/branch-mapping")).status_code == 403
        r = await c.get("/admin/branch-mapping", headers={"X-Admin-Key":"s3cret"})
    assert r.status_code == 200
    by_code = {x["branch_code"]: x for x in r.json()}
    assert by_code["KAZH"]["matched"] is False
