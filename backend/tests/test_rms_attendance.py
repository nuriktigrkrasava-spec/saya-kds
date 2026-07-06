"""Тесты списка смены: парсер явок RMS, фильтр «открыта+сегодня», роли, дедуп."""
import pytest
import respx
from httpx import Response

from app import rms
from app.config import settings

BASE = "https://rms.test/resto"

ATT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<attendances>
  <attendance><employeeId>e-courier</employeeId>
    <dateFrom>2026-07-06T11:53:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-courier</employeeId>
    <dateFrom>2026-07-06T12:00:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-cashier</employeeId>
    <dateFrom>2026-07-06T11:13:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-stale</employeeId>
    <dateFrom>2024-06-26T15:46:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-closed</employeeId>
    <dateFrom>2026-07-06T09:00:00+05:00</dateFrom>
    <dateTo>2026-07-06T10:00:00+05:00</dateTo></attendance>
  <attendance><employeeId>e-manager</employeeId>
    <dateFrom>2026-07-06T11:53:00+05:00</dateFrom></attendance>
  <attendance><employeeId>e-deleted</employeeId>
    <dateFrom>2026-07-06T11:00:00+05:00</dateFrom></attendance>
</attendances>"""

EMP_XML = """<?xml version="1.0"?>
<employees>
  <employee><id>e-courier</id><name>Адильхан курьер</name><code>77</code>
    <mainRoleCode>КЭКСПО20</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-cashier</id><name>Нурсая</name><code>11</code>
    <mainRoleCode>CS1</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-stale</id><name>Старый Хвост</name><code>22</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-closed</id><name>Закрытая Явка</name><code>33</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-manager</id><name>Kaнат Сарыбай</name><code>44</code>
    <mainRoleCode>УПРЭКСПО</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>false</deleted><employee>true</employee></employee>
  <employee><id>e-deleted</id><name>Уволенный</name><code>55</code>
    <mainRoleCode>КР</mainRoleCode><departmentCodes>10ASTKBN</departmentCodes>
    <deleted>true</deleted><employee>true</employee></employee>
</employees>"""

DEP_XML = """<?xml version="1.0"?>
<corporateItemDtoes>
  <corporateItemDto><id>dep-1</id><name>Астана, ул. Г.Алиева 10 (EXPO)</name>
    <code>10ASTKBN</code><type>DEPARTMENT</type></corporateItemDto>
</corporateItemDtoes>"""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "rms_base", BASE)
    monkeypatch.setattr(settings, "rms_login", "u")
    monkeypatch.setattr(settings, "rms_password", "p")
    monkeypatch.setattr(settings, "branch_code", "10ASTKBN")
    rms.reset_attendance_cache()
    rms._EMP_CACHE = None
    rms._ORG_MAP = None
    yield
    rms.reset_attendance_cache()
    rms._EMP_CACHE = None
    rms._ORG_MAP = None


def _mock_rms(att_xml=ATT_XML):
    respx.get(f"{BASE}/api/auth").mock(return_value=Response(200, text="tok"))
    respx.get(f"{BASE}/api/logout").mock(return_value=Response(200, text=""))
    respx.get(f"{BASE}/api/corporation/departments").mock(
        return_value=Response(200, text=DEP_XML))
    respx.get(f"{BASE}/api/employees").mock(return_value=Response(200, text=EMP_XML))
    respx.get(f"{BASE}/api/employees/attendance/byDepartment/10ASTKBN").mock(
        return_value=Response(200, text=att_xml))
    # Cloud-маппинг org_id в тестах недоступен -> branch_to_org_id() отдаёт {}.
    respx.post("https://api-ru.iiko.services/api/1/access_token").mock(
        return_value=Response(500))


def test_parse_attendances_extracts_fields():
    atts = rms._parse_attendances(ATT_XML)
    assert len(atts) == 7
    assert atts[0] == {"employee_id": "e-courier",
                       "date_from": "2026-07-06T11:53:00+05:00", "date_to": ""}
    assert atts[4]["date_to"] == "2026-07-06T10:00:00+05:00"


@respx.mock
@pytest.mark.asyncio
async def test_shift_today_filters_and_joins(monkeypatch):
    """Открытые+сегодняшние; хвост-2024, закрытая, кассир и deleted — вне списка; дедуп."""
    monkeypatch.setattr(rms, "_attendance_today_str", lambda: "2026-07-06")
    _mock_rms()
    out = await rms.shift_employees_today()
    names = [e["name"] for e in out]
    assert names == ["Kaнат Сарыбай", "Адильхан курьер"]  # sorted, no dups
    courier = out[1]
    assert courier == {"employee_id": "e-courier", "name": "Адильхан курьер",
                       "role": "КЭКСПО20", "code": "77",
                       "branch_code": "10ASTKBN",
                       "branch_name": "Астана, ул. Г.Алиева 10 (EXPO)",
                       "org_id": "10ASTKBN"}  # без Cloud-маппинга org_id=код


@respx.mock
@pytest.mark.asyncio
async def test_shift_today_cached(monkeypatch):
    monkeypatch.setattr(rms, "_attendance_today_str", lambda: "2026-07-06")
    _mock_rms()
    await rms.shift_employees_today()
    route = respx.get(f"{BASE}/api/employees/attendance/byDepartment/10ASTKBN")
    calls_before = route.call_count
    await rms.shift_employees_today()
    assert route.call_count == calls_before  # второй раз из кэша


@pytest.mark.asyncio
async def test_shift_today_requires_branch_code(monkeypatch):
    monkeypatch.setattr(settings, "branch_code", "")
    with pytest.raises(ValueError):
        await rms.shift_employees_today()
