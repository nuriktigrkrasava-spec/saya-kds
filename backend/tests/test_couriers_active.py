import hashlib
from datetime import datetime

import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.db import Base
import app.models  # noqa: F401
from app.models import CourierLocation
from app.config import settings

# Филиал B1 с двумя курьерами (роль КР): C1 и C2.
DEPARTMENTS_XML = """<?xml version="1.0"?><corporateItemDtoes>
<corporateItemDto><id>d-b1</id><code>B1</code><name>Бранч1</name><type>DEPARTMENT</type></corporateItemDto>
</corporateItemDtoes>"""

EMPLOYEES_XML = """<?xml version="1.0"?><employees>
<employee><id>e1</id><code>C1</code><name>Даулет</name><mainRoleCode>КР</mainRoleCode><departmentCodes>B1</departmentCodes><deleted>false</deleted><employee>true</employee></employee>
<employee><id>e2</id><code>C2</code><name>Мустафа</name><mainRoleCode>КР</mainRoleCode><departmentCodes>B1</departmentCodes><deleted>false</deleted><employee>true</employee></employee>
</employees>"""


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from app import api
    from app.db import get_session

    async def override():
        async with maker() as s:
            yield s

    api.app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=api.app),
                           base_url="http://test") as c:
        yield c, maker
    api.app.dependency_overrides.clear()
    await engine.dispose()


def _mock_rms():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    sha1 = hashlib.sha1(b"pw").hexdigest()
    respx.get(
        f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}"
    ).mock(return_value=Response(200, text="TOKEN-1"))
    respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=Response(200, text="ok"))
    respx.get(
        "https://rms.test/resto/api/corporation/departments?key=TOKEN-1"
    ).mock(return_value=Response(200, text=DEPARTMENTS_XML))
    respx.get("https://rms.test/resto/api/employees?key=TOKEN-1").mock(
        return_value=Response(200, text=EMPLOYEES_XML))


@respx.mock
async def test_couriers_active_flag_and_sort(client):
    c, maker = client
    _mock_rms()

    # C1 прислал геопозицию сейчас (на смене); у C2 строки нет.
    async with maker() as s:
        s.add(CourierLocation(courier_code="C1", name="Даулет", branch_code="B1",
                              lat=51.16, lon=71.47, updated_at=datetime.utcnow()))
        await s.commit()

    r = await c.get("/branches/couriers", params={"branch_code": "B1"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2

    by_code = {d["code"]: d for d in data}

    # C1 на смене, ~0 минут назад.
    assert by_code["C1"]["active"] is True
    assert by_code["C1"]["minutes_ago"] == 0

    # C2 не на смене, нет геопозиции.
    assert by_code["C2"]["active"] is False
    assert by_code["C2"]["minutes_ago"] is None

    # Активные сортируются первыми.
    assert data[0]["code"] == "C1"


@respx.mock
async def test_courier_on_shift_active_without_geo(client, monkeypatch):
    """Открытая явка iiko = на смене, даже если курьер не открывал TG-апп."""
    c, _ = client
    _mock_rms()

    from app import rms

    async def fake_shift():
        return [{"employee_id": "e2", "name": "Мустафа", "role": "КР",
                 "code": "C2", "branch_code": "B1", "branch_name": "Бранч1",
                 "org_id": "B1"}]
    monkeypatch.setattr(rms, "shift_employees_today", fake_shift)

    r = await c.get("/branches/couriers", params={"branch_code": "B1"})
    data = r.json()
    by_code = {d["code"]: d for d in data}

    # C2: явка открыта -> активен, хотя геопозиции нет.
    assert by_code["C2"]["active"] is True
    assert by_code["C2"]["minutes_ago"] is None
    # C1: ни явки, ни гео -> не на смене.
    assert by_code["C1"]["active"] is False
