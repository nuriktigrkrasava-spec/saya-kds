import hashlib
import respx
from httpx import Response, ASGITransport, AsyncClient
import pytest_asyncio
from app import rms
from app.config import settings

DEPARTMENTS_XML = """<?xml version="1.0"?><corporateItemDtoes>
<corporateItemDto><id>d-aaa</id><code>AAA</code><name>Альфа</name><type>DEPARTMENT</type></corporateItemDto>
</corporateItemDtoes>"""

EMPLOYEES_XML = """<?xml version="1.0"?><employees>
<employee><id>e1</id><code>1234</code><name>Иван</name><mainRoleCode>ПОВ</mainRoleCode><departmentCodes>AAA</departmentCodes><deleted>false</deleted><employee>true</employee></employee>
</employees>"""


@pytest_asyncio.fixture
async def client():
    from app import api
    transport = ASGITransport(app=api.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _setup_rms():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    settings.jwt_secret = "test-secret"
    # Сбрасываем кэш, чтобы не было утечки между тестами.
    rms._EMP_CACHE = None
    rms._EMP_CACHE_TS = 0.0
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
async def test_login_pin_ok(client):
    _setup_rms()
    r = await client.post("/auth/login", json={"pin": "1234"})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Иван"
    assert data["branch_name"] == "Альфа"
    assert data["org_id"] == "AAA"
    assert data["token"]


@respx.mock
async def test_login_pin_bad(client):
    _setup_rms()
    r = await client.post("/auth/login", json={"pin": "0000"})
    assert r.status_code == 401
