import hashlib
import respx
from httpx import Response, ASGITransport, AsyncClient
from app import rms
from app.config import settings

EMPLOYEES_XML = """<?xml version="1.0"?><employees>
<employee><id>e1</id><name>Повар Алия</name><code>1234</code></employee>
</employees>"""


@respx.mock
async def test_rms_auth_uses_sha1_and_logs_out():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    sha1 = hashlib.sha1(b"pw").hexdigest()
    auth = respx.get(f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}").mock(
        return_value=Response(200, text="TOKEN-1"))
    logout = respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=Response(200, text="ok"))

    async with rms.session() as token:
        assert token == "TOKEN-1"
    assert auth.called and logout.called  # слот лицензии освобождён


@respx.mock
async def test_fetch_employees_parses_xml():
    settings.rms_base = "https://rms.test/resto"
    settings.rms_login = "nignarok"
    settings.rms_password = "pw"
    sha1 = hashlib.sha1(b"pw").hexdigest()
    respx.get(f"https://rms.test/resto/api/auth?login=nignarok&pass={sha1}").mock(
        return_value=Response(200, text="TOKEN-1"))
    respx.get("https://rms.test/resto/api/logout?key=TOKEN-1").mock(
        return_value=Response(200, text="ok"))
    respx.get("https://rms.test/resto/api/employees?key=TOKEN-1").mock(
        return_value=Response(200, text=EMPLOYEES_XML))

    emps = await rms.fetch_employees()
    assert emps == [{"id": "e1", "name": "Повар Алия", "code": "1234"}]
