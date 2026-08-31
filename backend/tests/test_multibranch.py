"""Мультифилиальность: смены, кэш явок и фильтр курьеров не привязаны к одному BRANCH_CODE.

Второй филиал (Кажымукана) не должен видеть данные первого: список смены —
по своему депо, кэш — свой, курьеры на дашборде — по коду своего филиала.
"""
import time

import pytest
import respx
from httpx import Response

from app import rms
from app.config import settings

BASE = "https://rms.test/resto"

DEP_XML = """<?xml version="1.0"?>
<corporateItemDtoes>
  <corporateItemDto><id>d-aliev</id><name>Астана, ул. Г.Алиева 10 (EXPO)</name>
    <code>10ASTKBN</code><type>DEPARTMENT</type></corporateItemDto>
  <corporateItemDto><id>d-kazh</id><name>Астана, ул. Кажымукана 12</name>
    <code>KAZH</code><type>DEPARTMENT</type></corporateItemDto>
</corporateItemDtoes>"""

EMP_XML = """<?xml version="1.0"?>
<employees>
  <employee><id>e-aliev</id><name>Адильхан курьер</name><code>77</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-kazh</id><name>Ерлан курьер</name><code>99</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>KAZH</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
</employees>"""

ATT_ALIEV = """<?xml version="1.0"?>
<attendances><attendance><employeeId>e-aliev</employeeId>
  <dateFrom>2026-07-06T11:00:00+05:00</dateFrom></attendance></attendances>"""

ATT_KAZH = """<?xml version="1.0"?>
<attendances><attendance><employeeId>e-kazh</employeeId>
  <dateFrom>2026-07-06T11:00:00+05:00</dateFrom></attendance></attendances>"""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "rms_base", BASE)
    monkeypatch.setattr(settings, "rms_login", "u")
    monkeypatch.setattr(settings, "rms_password", "p")
    monkeypatch.setattr(settings, "branch_code", "10ASTKBN")
    monkeypatch.setattr(settings, "branch_codes", "10ASTKBN,KAZH", raising=False)
    monkeypatch.setattr(rms, "_attendance_today_str", lambda: "2026-07-06")
    rms.reset_attendance_cache()
    rms._EMP_CACHE = None
    rms._ORG_MAP = None
    yield
    rms.reset_attendance_cache()
    rms._EMP_CACHE = None
    rms._ORG_MAP = None


def _mock_rms() -> dict:
    """Мокает RMS и отдаёт маршруты явок по филиалам — по ним считаем вызовы."""
    respx.get(f"{BASE}/api/auth").mock(return_value=Response(200, text="tok"))
    respx.get(f"{BASE}/api/logout").mock(return_value=Response(200, text=""))
    respx.get(f"{BASE}/api/corporation/departments").mock(
        return_value=Response(200, text=DEP_XML))
    respx.get(f"{BASE}/api/employees").mock(return_value=Response(200, text=EMP_XML))
    # Cloud недоступен -> branch_to_org_id() пустой, org_id откатывается на код.
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(500))
    return {
        "10ASTKBN": respx.get(
            f"{BASE}/api/employees/attendance/byDepartment/10ASTKBN"
        ).mock(return_value=Response(200, text=ATT_ALIEV)),
        "KAZH": respx.get(
            f"{BASE}/api/employees/attendance/byDepartment/KAZH"
        ).mock(return_value=Response(200, text=ATT_KAZH)),
    }


@respx.mock
@pytest.mark.asyncio
async def test_shift_lists_every_configured_branch():
    """Без явного филиала список смены собирается по всем BRANCH_CODES."""
    _mock_rms()
    out = await rms.shift_employees_today()
    assert [(e["name"], e["branch_code"]) for e in out] == [
        ("Адильхан курьер", "10ASTKBN"),
        ("Ерлан курьер", "KAZH"),
    ]
    kazh = next(e for e in out if e["branch_code"] == "KAZH")
    assert kazh["branch_name"] == "Астана, ул. Кажымукана 12"


@respx.mock
@pytest.mark.asyncio
async def test_shift_filtered_to_one_branch():
    """Явный код филиала -> только его смена, чужое депо не опрашиваем."""
    aliev_route = _mock_rms()["10ASTKBN"]
    out = await rms.shift_employees_today("KAZH")
    assert [e["employee_id"] for e in out] == ["e-kazh"]
    assert aliev_route.call_count == 0


@respx.mock
@pytest.mark.asyncio
async def test_shift_cache_is_per_branch():
    """Кэш ключуется филиалом: смена Кажымукана не отдаётся из кэша Алиева."""
    kazh_route = _mock_rms()["KAZH"]
    await rms.shift_employees_today("10ASTKBN")
    assert kazh_route.call_count == 0, "чужое депо не должно опрашиваться"
    out = await rms.shift_employees_today("KAZH")
    assert kazh_route.call_count == 1
    assert [e["employee_id"] for e in out] == ["e-kazh"]


@pytest.mark.asyncio
async def test_org_branch_codes_resolves_by_org_mapping(session):
    """Фильтр курьеров берёт код филиала из карты org_id, а не глобальный BRANCH_CODE."""
    from app.api import _org_branch_codes
    rms._ORG_MAP = {"10ASTKBN": "org-aliev", "KAZH": "org-kazh"}
    rms._ORG_MAP_TS = time.monotonic()
    assert await _org_branch_codes(session, "org-kazh") == {"KAZH"}
    assert await _org_branch_codes(session, "org-aliev") == {"10ASTKBN"}


@pytest.mark.asyncio
async def test_org_branch_codes_keeps_single_branch_fallback(session, monkeypatch):
    """Однофилиальная установка: BRANCH_CODE по-прежнему фильтрует (регрессия)."""
    from app.api import _org_branch_codes
    monkeypatch.setattr(settings, "branch_codes", "", raising=False)
    assert await _org_branch_codes(session, "org-aliev") == {"10ASTKBN"}


@pytest.mark.asyncio
async def test_branch_code_for_org_unknown_org_is_empty():
    """Несматченный филиал не подменяется Алиевым — иначе явки чужие."""
    from app.api import _branch_code_for_org
    rms._ORG_MAP = {"10ASTKBN": "org-aliev"}
    rms._ORG_MAP_TS = time.monotonic()
    assert await _branch_code_for_org("org-kazh") == ""


@pytest.mark.asyncio
async def test_staffing_refuses_unmapped_branch(monkeypatch):
    """Планирование смен для несматченного филиала — 404, а не явки Алиева."""
    from fastapi import HTTPException
    from app import api
    rms._ORG_MAP = {"10ASTKBN": "org-aliev"}
    rms._ORG_MAP_TS = time.monotonic()

    async def fake_department_name(org_id):
        return "Астана, ул. Кажымукана 12"

    async def fake_sales(*a, **k):
        return []

    monkeypatch.setattr(api._olap, "department_name", fake_department_name)
    monkeypatch.setattr(api._olap, "fetch_sales_rows", fake_sales)
    with pytest.raises(HTTPException) as err:
        await api._staffing_grid("org-kazh", 1)
    assert err.value.status_code == 404
